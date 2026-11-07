#include "driver_main.hpp"
#include <algorithm>
#include <deque>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <utility>
#include "gemmini_host.h"

static std::vector<online_service::Command>* capture=nullptr;
void host_emit(uint64_t a,uint64_t b,unsigned f) {
  if(!capture)throw std::runtime_error("emit outside command construction");
  capture->push_back({f,a,b,0});
}
void host_fence(){throw std::runtime_error("unexpected host fence");}

namespace online_service {
using Fields=std::vector<std::pair<std::string,std::string>>;
static std::string num(uint64_t x){return std::to_string(x);}
static std::string str(const std::string& x){return "\""+x+"\"";}
template<class T> static std::string nums(const T& xs){std::string s="[";bool first=true;for(auto x:xs){if(!first)s+=",";first=false;s+=num(x);}return s+"]";}
static void require(bool x,const char* s){if(!x)throw std::runtime_error(s);}
struct Log {
  EmitJSON emit;uint64_t seq=0;
  void event(Cycle c,const std::string& name,Fields fields={}) {
    std::string s="{\"cycle\":"+num(c)+",\"seq\":"+num(seq++)+",\"event\":"+str(name);
    for(auto& f:fields)s+=",\""+f.first+"\":"+f.second;
    emit(s+"}");
  }
};

// The ledger records evidence only. Its completion accessors deliberately fail.
// The controller has no TL callbacks and never reads these containers.
struct QState {QueryPlan plan;std::vector<uint8_t> data,seen,acked;uint64_t ack_count=0;Cycle ready=0;bool complete=false;};
struct StageState {uint64_t base=0,bytes=0,acked=0;bool complete=false;Cycle complete_cycle=0;std::vector<uint8_t> data,seen;};
struct PState {PanelPlan plan;std::map<uint64_t,uint8_t> bytes;uint64_t ack_count=0;bool complete=false;};
struct PutState {bool consumer=false;uint64_t owner=0;std::string kind;std::vector<std::pair<uint64_t,uint8_t>> bytes;};
class ServiceLedger final:public EventLedger {
public:
  DriverConfig cfg;std::shared_ptr<Log> log;Cycle now=0;
  std::map<QueryId,QState> qs;std::map<PanelId,PState> ps;
  std::map<TransactionId,PutState> puts;
  std::map<std::pair<QueryId,std::string>,StageState> stages;
  std::map<SlotId,Generation> slot_generations;
  ServiceLedger(DriverConfig c,std::shared_ptr<Log> l):cfg(std::move(c)),log(std::move(l)){}
  void register_panel(const PanelPlan& p) override {
    require(!ps.count(p.panel),"duplicate panel plan");PState s;s.plan=p;
    std::string ranges="[";bool first=true;
    for(auto r:p.expected_puts){require(r.begin<r.end,"empty panel range");if(!first)ranges+=",";first=false;ranges+="["+num(r.begin)+","+num(r.end)+"]";
      for(auto a=r.begin;a<r.end;a++){require(a>=cfg.output_base&&a<cfg.output_base+cfg.output_budget_bytes,"C1 budget");require(s.bytes.emplace(a,0).second,"panel duplicate byte");}}
    ranges+="]";ps.emplace(p.panel,std::move(s));
    log->event(now,"panel_reserve",{{"panel",num(p.panel)},{"physical_panel",num(p.physical_panel)},{"buffer",num(p.epoch)},
      {"generation",num(p.generation)},{"expected_puts",ranges},{"effective_queries",nums(p.effective_queries)},{"reserved_credit",num(p.reserved_credit)}});
  }
  void register_query(const QueryPlan& p) override {
    require(!qs.count(p.query),"duplicate query plan");QState q;q.plan=p;q.data.resize(cfg.n*4);q.seen.resize(cfg.n*4);q.acked.resize(cfg.n*4);
    uint64_t off=0;std::string fragments="[";bool first=true;
    for(size_t i=0;i<p.descriptor_fragments.size();i++){
      auto r=p.descriptor_fragments[i];if(!first)fragments+=",";first=false;
      uint64_t e=p.start_panel/(cfg.n/64);if(p.start_panel%(cfg.n/64)&&i==0)e++;
      fragments+="{\"address\":"+num(r.begin)+",\"length\":"+num(r.end-r.begin)+",\"logical_offset\":"+num(off)+",\"buffer\":"+num(e%3)+",\"buffer_generation\":"+num(e/3+1)+"}";
      off+=r.end-r.begin;
    }
    require(off==cfg.n*4,"query descriptor length");fragments+="]";qs.emplace(p.query,std::move(q));
    log->event(now,"query_plan",{{"query",num(p.query)},{"generation",num(p.generation)},{"slot",num(p.slot)},
      {"slot_generation",num(slot_generations.at(p.slot))},{"start_panel",num(p.start_panel)},{"panel_count",num(p.panel_count)},{"fragments",fragments}});
  }
  void register_stage(QueryId q,uint64_t base,uint64_t bytes,const std::string& kind,uint64_t cohort) {
    require(!stages.count({q,kind}),"duplicate consumer registration");
    StageState s;s.base=base;s.bytes=bytes;s.data.resize(bytes);s.seen.resize(bytes);stages[{q,kind}]=std::move(s);
    log->event(now,"consumer_register",{{"query",num(q)},{"base",num(base)},{"bytes",num(bytes)},{"kind",str(kind)},{"cohort",num(cohort)},{"generation",num(cohort+1)}});
  }
  void on_arrival(const Arrival&) override {}
  void on_command_attempt(const CommandAttempt&) override {}
  void on_command_accepted(const CommandAttempt&) override {}
  void on_a_accepted(const AcceptedA& a) override {
    if(a.opcode!=AOpcode::PutFull&&a.opcode!=AOpcode::PutPartial)return;
    if(a.beat==0){
      PutState t;unsigned owners=0;
      for(auto& [id,p]:ps)if(!p.complete&&p.bytes.count(a.address)){t.owner=id;t.consumer=false;owners++;}
      for(auto& [key,s]:stages)if(!s.complete&&a.address>=s.base&&a.address<s.base+s.bytes){t.owner=key.first;t.kind=key.second;t.consumer=true;owners++;}
      require(owners==1,"Put requires one live registered owner");require(puts.emplace(a.transaction,std::move(t)).second,"duplicate transaction");
    }
    auto& tx=puts.at(a.transaction);
    for(unsigned lane=0;lane<16;lane++)if(a.mask&(1u<<lane)){
      uint64_t address=a.address+a.beat*16+lane;uint8_t value=(a.data[lane/4]>>(8*(lane%4)))&255;tx.bytes.emplace_back(address,value);
      if(tx.consumer){auto& s=stages.at({tx.owner,tx.kind});require(address>=s.base&&address<s.base+s.bytes,"consumer Put range");auto i=address-s.base;require(!s.seen[i],"duplicate consumer byte");s.seen[i]=1;s.data[i]=value;}
      else {auto& p=ps.at(tx.owner);require(p.bytes.count(address)&&p.bytes.at(address)==0,"panel Put range/duplicate");p.bytes[address]=1;
        for(auto& [id,q]:qs)if(!q.complete){uint64_t off=0;for(auto r:q.plan.effective_output){if(address>=r.begin&&address<r.end){auto i=off+address-r.begin;require(!q.seen[i],"duplicate query byte");q.seen[i]=1;q.data[i]=value;}off+=r.end-r.begin;}}}
    }
  }
  void on_d_accepted(const AcceptedD& d) override {
    if(d.opcode!=AOpcode::PutFull&&d.opcode!=AOpcode::PutPartial)return;
    require(d.last_d&&!d.denied&&!d.corrupt,"invalid Ack");auto tx=puts.at(d.transaction);
    if(tx.consumer){auto& s=stages.at({tx.owner,tx.kind});s.acked+=tx.bytes.size();require(s.acked<=s.bytes,"consumer Ack overflow");
      if(s.acked==s.bytes){s.complete=true;s.complete_cycle=d.cycle;log->event(d.cycle,"observer_consumer_complete",{{"query",num(tx.owner)},{"kind",str(tx.kind)}});}}
    else {auto& p=ps.at(tx.owner);for(auto [address,value]:tx.bytes){require(p.bytes.at(address)==1,"Ack without Put");p.bytes[address]=3;p.ack_count++;
        for(auto& [id,q]:qs)if(!q.complete){uint64_t off=0;for(auto r:q.plan.effective_output){if(address>=r.begin&&address<r.end){auto i=off+address-r.begin;require(q.seen[i]&&!q.acked[i],"query Ack coverage");q.acked[i]=1;q.ack_count++;}off+=r.end-r.begin;}}}
      for(auto& [id,q]:qs)if(!q.complete&&q.ack_count==cfg.n*4){q.complete=true;q.ready=d.cycle;log->event(d.cycle,"observer_query_complete",{{"query",num(id)}});}
      if(p.ack_count==p.bytes.size()){p.complete=true;log->event(d.cycle,"observer_panel_complete",{{"panel",num(tx.owner)}});}}
    puts.erase(d.transaction);
  }
  bool panel_released(PanelId) const override {throw std::runtime_error("observer readiness cannot control execution");}
  bool query_device_ready(QueryId,Generation) const override {throw std::runtime_error("observer readiness cannot control execution");}
  std::string finish_error() const override {
    if(!puts.empty())return "unacked puts";for(auto& [id,p]:ps)if(!p.complete)return "incomplete panel";
    if(qs.size()!=cfg.query_count)return "missing query plan";for(auto& [id,q]:qs)if(!q.complete)return "incomplete C1";
    for(auto& [key,s]:stages)if(!s.complete)return "incomplete consumer";return {};
  }
};

struct Query {
  Arrival arrival;bool observed=false,bound=false,input_released=false,consumed=false;
  unsigned slot=0,slot_generation=0,sealed=0;uint64_t start=0;
  std::set<uint64_t> epochs;std::vector<ByteRange> fragments;uint64_t a2_addr=0,c2_addr=0;
  Cycle admitted=0,c1_notified=0,consumer_start=0,a2_sealed=0,c2_notified=0,delivery_end=0;
};
struct Buffer {uint64_t identity=0,generation=0,logical=0,address=0;bool closed=false,released=false;std::set<QueryId> refs;};
struct Stage {
  uint64_t id=0,expected=0;uint32_t baseline=0;Cycle last=0;unsigned count=0;
  std::string name;std::function<void()> sealed,idle;
};
struct Task {
  enum Kind {Fn,Delay,Word,Response} kind=Fn;
  std::function<void()> fn;Cycle duration=0,start=0,end=0;bool started=false;
  std::string label;Fields fields;Command cmd;std::shared_ptr<Stage> stage;
  std::function<void(uint32_t,Cycle)> response;
};

class ServiceController final:public Controller {
public:
  DriverConfig cfg;ServiceLedger& registrar;std::shared_ptr<Log> log;Cycle now=0;
  std::map<QueryId,Query> queries;std::deque<QueryId> backlog;std::set<QueryId> ready;
  std::map<uint64_t,Buffer> buffers;std::map<unsigned,uint64_t> physical_epoch;
  std::vector<int> slots=std::vector<int>(16,-1);std::vector<unsigned> slot_gen=std::vector<unsigned>(16,0);
  std::deque<Task> tasks;std::map<uint64_t,CommandAttempt> accept;std::map<uint64_t,RoCCResponse> responses;
  DeviceStatus status;bool have_status=false,stopped=false;uint32_t baseline=0;
  uint64_t tag=1,stage_id=0,next_panel=0,scan=0,cohort_id=0,batch_no=0;
  bool batch=false;unsigned batch_p=0,batch_rows=0;uint64_t batch_epoch=0;QueryId batch_first=0;
  std::map<QueryId,Cycle> delivered;

  ServiceController(DriverConfig c,ServiceLedger& r,std::shared_ptr<Log> l):cfg(std::move(c)),registrar(r),log(std::move(l)) {
    require(cfg.n==896&&cfg.k==896&&cfg.query_count<=48,"registered gate shape");
    require(cfg.policy=="on"||cfg.policy=="off"||cfg.policy=="closed","registered policy");
    require(cfg.tile_j==4||(cfg.policy=="closed"&&cfg.tile_j==8),"registered tile J4/J8");
    require(cfg.output_budget_bytes>=3*epoch_bytes(),"three C1 epochs required");
    require(cfg.poll_cycles==64&&cfg.decision_cycles==16&&cfg.issue_cycles==1&&cfg.sink_cycles==64,"registered common costs");
    log->event(0,"driver_config",{{"policy",str(cfg.policy)},{"n",num(cfg.n)},{"k",num(cfg.k)},
      {"query_count",num(cfg.query_count)},{"output_base",num(cfg.output_base)},{"output_budget_bytes",num(cfg.output_budget_bytes)},
      {"poll",num(64)},{"decision",num(16)},{"issue",num(1)},{"sink",num(64)},{"observer_controls_execution","false"}});
    counter(2,"counter_init",[this](uint32_t,Cycle){counter(0x80005008,"counter_init",[this](uint32_t,Cycle){
      counter(0,"counter_baseline",[this](uint32_t v,Cycle){require(v==0,"nonzero initial counter");baseline=v;log->event(now,"counter_baseline",{{"value",num(v)}});});});});
  }
  uint64_t epoch_bytes() const{return uint64_t(16)*cfg.n*4;}
  void on_arrival(const Arrival& a) override {require(a.query<cfg.query_count&&!queries.count(a.query)&&a.n==cfg.n&&a.k==cfg.k,"arrival identity");queries[a.query].arrival=a;}
  void on_command_accepted(const CommandAttempt& a) override {require(accept.emplace(a.command.tag,a).second,"duplicate acceptance");}
  void on_rocc_response(const RoCCResponse& r) override {require(responses.emplace(r.command_tag,r).second,"duplicate response");}
  void on_device_status(const DeviceStatus& s) override {status=s;have_status=true;}
  void fn(std::function<void()> f){Task t;t.fn=std::move(f);tasks.push_back(std::move(t));}
  void delay(Cycle n,const std::string& kind,Fields fields={}) {Task t;t.kind=Task::Delay;t.duration=n;t.label=kind;t.fields=std::move(fields);tasks.push_back(std::move(t));}
  void metadata(const std::string& category,std::function<void()> f) {
    delay(16,"metadata",{{"category",str(category)},{"cost",num(16)}});
    fn([this,category,f=std::move(f)]{log->event(now,"metadata_op",{{"category",str(category)},{"cost",num(16)}});f();});
  }
  std::vector<Command> make(std::function<void()> f){std::vector<Command> xs;require(!capture,"nested capture");capture=&xs;f();capture=nullptr;return xs;}
  std::vector<uint64_t> words(std::vector<Command> xs,const std::string& label,std::shared_ptr<Stage> s={}) {
    std::vector<uint64_t> tags;
    for(auto c:xs){c.tag=tag++;tags.push_back(c.tag);log->event(now,"command_plan",{{"stage",num(s?s->id:0)},{"tag",num(c.tag)},
      {"funct",num(c.funct)},{"rs1",num(c.rs1)},{"rs2",num(c.rs2)},{"rd",num(c.rd)},{"xd",c.xd?"true":"false"},{"label",str(label)}});
      delay(cfg.issue_cycles,"issue",{{"tag",num(c.tag)}});Task t;t.kind=Task::Word;t.cmd=c;t.label=label;t.stage=s;tasks.push_back(std::move(t));}
    return tags;
  }
  void counter(uint64_t rs1,const std::string& label,std::function<void(uint32_t,Cycle)> done,std::shared_ptr<Stage> s={}) {
    Command c;c.funct=126;c.rs1=rs1;c.rd=1;c.xd=true;auto tags=words({c},label,s);
    Task t;t.kind=Task::Response;t.cmd.tag=tags[0];
    t.response=[this,done=std::move(done)](uint32_t v,Cycle response_cycle){delay(cfg.decision_cycles,"response_decision");fn([done,v,response_cycle]{done(v,response_cycle);});};
    tasks.push_back(std::move(t));
  }
  void seal(const std::shared_ptr<Stage>& s) {
    delay(cfg.poll_cycles,"counter_poll",{{"stage",num(s->id)}});
    fn([this,s]{counter(0,"stage_counter",[this,s](uint32_t v,Cycle response_cycle){uint32_t d=v-s->baseline;
      require(d<=s->expected,"counter overshot sealed stage target");
      if(d<s->expected){seal(s);return;}
      baseline=v;log->event(now,"sealed_group_complete",{{"stage",num(s->id)},{"name",str(s->name)},
        {"baseline",num(s->baseline)},{"target",num(uint32_t(s->baseline+s->expected))},{"delta",num(d)},{"response_cycle",num(response_cycle)}});
      if(s->sealed)s->sealed();fn([this,s]{busy(s);});
    },s);});
  }
  void busy(const std::shared_ptr<Stage>& s) {
    auto begin=std::make_shared<Cycle>(0);fn([this,begin]{*begin=now;});
    delay(cfg.poll_cycles,"status_poll",{{"stage",num(s->id)}});
    fn([this,s,begin]{require(have_status&&status.cycle<now&&status.cycle>=*begin&&status.cycle>s->last,"fresh causal busy sample required");
      auto sample=status;log->event(now,"status_poll_observe",{{"stage",num(s->id)},{"sample_cycle",num(sample.cycle)},{"busy",sample.busy?"true":"false"}});
      delay(cfg.decision_cycles,"status_decision",{{"stage",num(s->id)}});
      fn([this,s,sample]{if(sample.busy){busy(s);return;}log->event(now,"stage_idle_sealed",{{"stage",num(s->id)},
        {"name",str(s->name)},{"sample_cycle",num(sample.cycle)},{"last_accepted_cycle",num(s->last)},{"counter_value",num(baseline)}});
        if(s->idle)s->idle();});
    });
  }
  void stage(const std::string& name,std::vector<Command> xs,uint64_t bytes,std::function<void()> sealed={},std::function<void()> idle={}) {
    require(!xs.empty(),"empty device stage");auto s=std::make_shared<Stage>();s->id=++stage_id;s->name=name;s->expected=bytes;s->baseline=baseline;s->sealed=std::move(sealed);s->idle=std::move(idle);
    log->event(now,"stage_start",{{"stage",num(s->id)},{"name",str(name)},{"baseline",num(s->baseline)},
      {"expected_write_bytes",num(bytes)},{"target",num(uint32_t(s->baseline+bytes))}});
    words(std::move(xs),name,s);fn([this,s]{log->event(now,"stage_all_accepted",{{"stage",num(s->id)},
      {"last_accepted_cycle",num(s->last)},{"command_count",num(s->count)}});seal(s);});
  }
  void config_c1() {
    gemmini_config_ex(WS,NO_ACTIVATION,0);gemmini_config_st(cfg.n*4);
    gemmini_extended3_config_ld(cfg.k,MVIN_SCALE_IDENTITY,false,0);
    gemmini_extended3_config_ld(cfg.n,MVIN_SCALE_IDENTITY,false,1);
    gemmini_extended3_config_ld(0,MVIN_SCALE_IDENTITY,false,2);
  }
  bool can_epoch(uint64_t e) const {
    if(buffers.count(e))return !buffers.at(e).closed&&!buffers.at(e).released;
    auto i=physical_epoch.find(e%3);return i==physical_epoch.end()||buffers.at(i->second).released;
  }
  void allocate(uint64_t e) {
    if(buffers.count(e))return;require(can_epoch(e),"epoch reuse while live");
    Buffer b;b.logical=e;b.identity=e%3;b.generation=e/3+1;b.address=cfg.output_base+b.identity*epoch_bytes();buffers[e]=b;physical_epoch[e%3]=e;
    log->event(now,"buffer_alloc",{{"buffer",num(b.identity)},{"generation",num(b.generation)},{"scan_epoch",num(e)},
      {"address",num(b.address)},{"length",num(epoch_bytes())}});
  }
  void close(uint64_t e) {auto& b=buffers.at(e);if(b.closed)return;b.closed=true;log->event(now,"buffer_close",{{"buffer",num(b.identity)},{"generation",num(b.generation)}});}
  void release_buffers() {
    for(auto& [e,b]:buffers)if(b.closed&&!b.released&&b.refs.empty())metadata("epoch_update",[this,e]{auto& x=buffers.at(e);require(x.closed&&x.refs.empty()&&!x.released,"epoch release state");x.released=true;
      log->event(now,"buffer_release",{{"buffer",num(x.identity)},{"generation",num(x.generation)}});});
  }
  void bind(QueryId id,unsigned slot,uint64_t start) {
    auto& q=queries.at(id);require(!q.bound&&slots[slot]<0,"slot bind state");q.bound=true;q.slot=slot;q.slot_generation=++slot_gen[slot];q.start=start;q.admitted=now;slots[slot]=id;
    registrar.slot_generations[slot]=q.slot_generation;
    log->event(now,"query_bind",{{"query",num(id)},{"slot",num(slot)},{"generation",num(q.arrival.generation)},
      {"slot_generation",num(q.slot_generation)},{"start_panel",num(start)},{"admitted",num(now)}});
    log->event(now,"slot_bind",{{"query",num(id)},{"generation",num(q.arrival.generation)},{"slot",num(slot)},
      {"slot_generation",num(q.slot_generation)},{"start_panel",num(start)}});
    uint64_t e=start/14,p=start%14,split=p*256;
    if(p){auto& b=buffers.at(e+1);q.fragments.push_back({b.address+slot*3584,b.address+slot*3584+split});q.epochs.insert(e+1);}
    auto& b=buffers.at(e);q.fragments.push_back({b.address+slot*3584+split,b.address+(slot+1)*3584});q.epochs.insert(e);
    for(auto x:q.epochs)buffers.at(x).refs.insert(id);
    QueryPlan plan;plan.query=id;plan.generation=q.arrival.generation;plan.slot=slot;plan.start_panel=start;plan.panel_count=14;plan.effective_output=q.fragments;plan.descriptor_fragments=q.fragments;
    registrar.register_query(plan);
  }
  std::vector<QueryId> live() const {std::vector<QueryId> ids;for(auto x:slots)if(x>=0)ids.push_back(x);return ids;}
  void load_inputs(const std::vector<QueryId>& ids,bool restore) {
    if(ids.empty())return;
    auto xs=make([&]{config_c1();for(auto id:ids){auto& q=queries.at(id);for(unsigned k=0;k<896;k+=64)gemmini_extended_mvin(q.arrival.dram_input+k,k+q.slot,64,1);}});
    log->event(now,restore?"restore_start":"input_load_start",{{"queries",nums(ids)},{"live_inputs",num(ids.size())}});
    stage(restore?"restore_inputs":"admit_inputs",std::move(xs),0,{},restore?std::function<void()>{}:std::function<void()>{[this]{c1_group();}});
  }
  void observe() {
    for(auto& [id,q]:queries)if(!q.observed&&q.arrival.cycle<now){q.observed=true;backlog.push_back(id);
      log->event(now,"query_observe",{{"query",num(id)},{"generation",num(q.arrival.generation)},{"poll_end",num(now)}});}
  }
  void decide() {
    if(!ready.empty()){std::vector<QueryId> ids(ready.begin(),ready.end());require(ids.size()<=16,"ready cohort capacity");consume(ids);return;}
    if(cfg.policy=="closed") {
      if(batch){c1_group();return;}if(backlog.empty())return;
      bool threshold=backlog.size()>=cfg.batch_target;bool timeout=now>=queries.at(backlog.front()).arrival.cycle+cfg.timeout_cycles;
      log->event(now,"batch_decision",{{"backlog",num(backlog.size())},{"oldest_arrival",num(queries.at(backlog.front()).arrival.cycle)},
        {"batch_target",num(cfg.batch_target)},{"timeout_cycles",num(cfg.timeout_cycles)},{"launch",threshold||timeout?"true":"false"}});
      if(!threshold&&!timeout)return;
      batch_epoch=batch_no++;require(can_epoch(batch_epoch),"closed epoch capacity");batch=true;batch_p=0;batch_rows=std::min<size_t>(16,backlog.size());batch_first=backlog.front();
      metadata("epoch_update",[this]{allocate(batch_epoch);});
      std::vector<QueryId> ids;for(unsigned row=0;row<batch_rows;row++){auto id=backlog.front();backlog.pop_front();require(id==batch_first+row,"contiguous FCFS closed input");ids.push_back(id);metadata("query_bind",[this,id,row]{bind(id,row,batch_epoch*14);});}
      fn([this,ids]{log->event(now,"batch_start",{{"first_query",num(batch_first)},{"rows",num(batch_rows)},{"queries",nums(ids)},
        {"ordinal",num(batch_no-1)},{"a_spad_id",num(1)}});c1_group();});return;
    }
    std::vector<QueryId> incoming;
    if((cfg.policy=="on"||scan%14==0)&&!backlog.empty()){
      uint64_t e=scan/14,p=scan%14;
      if(can_epoch(e)&&(!p||can_epoch(e+1))){
        if(!buffers.count(e))metadata("epoch_update",[this,e]{allocate(e);});
        if(p&&!buffers.count(e+1))metadata("epoch_update",[this,e]{allocate(e+1);});
        for(unsigned slot=0;slot<16&&!backlog.empty();slot++)if(slots[slot]<0){auto id=backlog.front();backlog.pop_front();incoming.push_back(id);metadata("query_bind",[this,id,slot]{bind(id,slot,scan);});}
      }
    }
    if(!incoming.empty()){fn([this,incoming]{load_inputs(incoming,false);});return;}
    if(!live().empty())c1_group();
  }
  void c1_group() {
    auto owners=live();if(owners.empty())return;
    unsigned p=cfg.policy=="closed"?batch_p:scan%14;uint64_t e=cfg.policy=="closed"?batch_epoch:scan/14;
    if(!buffers.count(e)){require(can_epoch(e),"next C1 epoch capacity");metadata("epoch_update",[this,e]{allocate(e);});fn([this]{c1_group();});return;}
    unsigned rows=0,count=std::min(2u,14-p);for(auto id:owners){auto& q=queries.at(id);rows=std::max(rows,q.slot+1);count=std::min(count,14-q.sealed);}
    require(count>0,"C1 group empty");require(cfg.tile_j!=8||count==2,"J8 two credits");
    auto panels=std::make_shared<std::vector<PanelId>>();auto& b=buffers.at(e);
    for(unsigned offset=0;offset<count;offset++)metadata("panel_snapshot",[this,e,p,offset,rows,owners,panels]{auto& b=buffers.at(e);PanelPlan plan;
      plan.panel=next_panel++;plan.physical_panel=p+offset;plan.epoch=b.identity;plan.generation=b.generation;plan.reserved_credit=1;plan.effective_queries=owners;
      for(unsigned row=0;row<rows;row++){auto a=b.address+row*3584+(p+offset)*256;plan.expected_puts.push_back({a,a+256});}
      plan.expected_output={plan.expected_puts.front().begin,plan.expected_puts.back().end};registrar.register_panel(plan);panels->push_back(plan.panel);});
    fn([this,e,p,count,rows,owners,panels]{auto& b=buffers.at(e);auto xs=make([&]{config_c1();
      for(unsigned offset=0;offset<count;offset+=cfg.tile_j/4){uint64_t a=0;if(cfg.policy=="closed"&&p==0&&offset==0)a=queries.at(batch_first).arrival.dram_input;
        gemmini_loop_ws(1,cfg.tile_j,56,16-rows,0,0,a,cfg.weight_base+(p+offset)*64,0,b.address+(p+offset)*256,
          896,896,0,896,false,false,true,false,false,NO_ACTIVATION,1,0,false);}
    });
      log->event(now,"c1_group_plan",{{"panels",nums(*panels)},{"queries",nums(owners)},{"rows",num(rows)},
        {"physical_panel",num(p)},{"panel_count",num(count)},{"tile_j",num(cfg.tile_j)}});
      stage("c1_group",std::move(xs),uint64_t(rows)*256*count,{},[this,e,p,count,owners,panels]{
        for(auto id:owners){auto& q=queries.at(id);q.sealed+=count;require(q.sealed<=14,"duplicate C1 logical panels");
          if(q.sealed==14)metadata("query_c1_ready",[this,id]{auto& q=queries.at(id);q.c1_notified=now;ready.insert(id);log->event(now,"c1_notified",{{"query",num(id)}});});}
        metadata("group_retire",[this,panels]{for(auto id:*panels)log->event(now,"panel_release",{{"panel",num(id)}});});
        for(auto id:owners)if(queries.at(id).sealed==14)metadata("input_release",[this,id]{auto& q=queries.at(id);require(slots[q.slot]==int(id),"input identity");slots[q.slot]=-1;q.input_released=true;
          log->event(now,"input_release",{{"query",num(id)},{"generation",num(q.arrival.generation)},{"slot",num(q.slot)},
            {"slot_generation",num(q.slot_generation)},{"reason",str("sealed_counter_and_idle")}});});
        metadata("epoch_update",[this,e,p,count]{if(cfg.policy=="closed"){batch_p+=count;if(batch_p==14){batch=false;close(e);}}else{scan+=count;if(scan%14==0)close(e);}});
      });
    });
  }
  void consume(const std::vector<QueryId>& ids) {
    auto cid=cohort_id++;
    metadata("cohort_map",[this,ids,cid]{
      log->event(now,"cohort_start",{{"cohort",num(cid)},{"generation",num(cid+1)},{"queries",nums(ids)},{"rows",num(ids.size())}});
      for(unsigned row=0;row<ids.size();row++){auto& q=queries.at(ids[row]);q.consumer_start=now;q.a2_addr=cfg.a2_base+row*896;q.c2_addr=cfg.c2_base+row*512;
        registrar.register_stage(ids[row],q.a2_addr,896,"a2",cid);registrar.register_stage(ids[row],q.c2_addr,512,"c2",cid);
        log->event(now,"consumer_start",{{"query",num(ids[row])},{"cohort",num(cid)},{"row",num(row)},
          {"a2_address",num(q.a2_addr)},{"c2_address",num(q.c2_addr)}});}
      auto xs=make([&]{gemmini_extended3_config_ld(64,MVIN_SCALE_IDENTITY,false,0);
        for(unsigned row=0;row<ids.size();row++)for(unsigned t=0;t<56;t++){auto& q=queries.at(ids[row]);uint64_t remain=t*64,source=0;
          for(auto r:q.fragments){auto len=r.end-r.begin;if(remain<len){source=r.begin+remain;break;}remain-=len;}require(source,"C1 descriptor lookup");
          gemmini_extended_mvin(source,(1u<<(ADDR_LEN-1))+row*56+t,16,1);}});
      stage("consumer_reload",std::move(xs),0,{},[this,ids,cid]{quantize(ids,cid);});
    });
  }
  void quantize(const std::vector<QueryId>& ids,uint64_t cid) {
    auto xs=make([&]{gemmini_extended_config_st(16,NO_ACTIVATION,1.0/16.0);
      for(unsigned row=0;row<ids.size();row++)for(unsigned t=0;t<56;t++)gemmini_extended_mvout(queries.at(ids[row]).a2_addr+t*16,(1u<<(ADDR_LEN-1))+row*56+t,16,1);});
    stage("consumer_a2",std::move(xs),ids.size()*896,{},[this,ids,cid]{
      for(auto id:ids){queries.at(id).a2_sealed=now;log->event(now,"a2_sealed",{{"query",num(id)}});}
      for(auto id:ids)metadata("query_fragment_release",[this,id]{for(auto e:queries.at(id).epochs)require(buffers.at(e).refs.erase(id)==1,"C1 reference release");
        log->event(now,"query_fragment_release",{{"query",num(id)}});});
      fn([this,ids,cid]{release_buffers();fn([this,ids,cid]{l2(ids,cid);});});
    });
  }
  void l2(const std::vector<QueryId>& ids,uint64_t cid) {
    auto xs=make([&]{gemmini_config_ex(WS,NO_ACTIVATION,0);gemmini_config_st(512);
      gemmini_extended3_config_ld(896,MVIN_SCALE_IDENTITY,false,0);gemmini_extended3_config_ld(128,MVIN_SCALE_IDENTITY,false,1);gemmini_extended3_config_ld(0,MVIN_SCALE_IDENTITY,false,2);
      gemmini_loop_ws(1,8,56,16-ids.size(),0,0,cfg.a2_base,cfg.weight2_base,0,cfg.c2_base,896,128,0,128,false,false,true,false,false,NO_ACTIVATION,2,0,false);});
    log->event(now,"l2_start",{{"cohort",num(cid)},{"queries",nums(ids)},{"rows",num(ids.size())}});
    stage("consumer_l2",std::move(xs),ids.size()*512,[this,ids]{
      for(auto id:ids){queries.at(id).c2_notified=now;log->event(now,"c2_notified",{{"query",num(id)}});}
      for(auto id:ids){delay(cfg.sink_cycles,"delivery",{{"queries","["+num(id)+"]"}});fn([this,id]{auto& q=queries.at(id);q.delivery_end=now;q.consumed=true;delivered[id]=now;ready.erase(id);
        log->event(now,"delivery_end",{{"query",num(id)},{"generation",num(q.arrival.generation)},{"sink_end",num(now)}});});}
    },[this,ids,cid]{metadata("cohort_release",[this,ids,cid]{log->event(now,"cohort_release",{{"cohort",num(cid)},{"generation",num(cid+1)},{"queries",nums(ids)}});
        for(auto id:ids)log->event(now,"query_release",{{"query",num(id)}});
      });
      metadata("restore_plan",[this]{auto ids=live();log->event(now,"restore_plan",{{"queries",nums(ids)},{"live_inputs",num(ids.size())}});load_inputs(ids,true);});
    });
  }
  CommandResult tick(Cycle c,const EventLedger&) override {
    now=c;registrar.now=c;
    for(unsigned steps=0;steps<100;steps++){
      if(tasks.empty()){
        if(stopped)return {{},true};
        if(delivered.size()==cfg.query_count){
          for(auto& [e,b]:buffers)if(!b.closed)metadata("epoch_update",[this,e]{close(e);});
          fn([this]{release_buffers();fn([this]{stopped=true;log->event(now,"engine_drained");});});continue;
        }
        delay(cfg.poll_cycles,"poll");fn([this]{observe();});delay(cfg.decision_cycles,"decision");metadata("schedule_decision",[this]{decide();});continue;
      }
      auto& t=tasks.front();
      if(t.kind==Task::Fn){auto f=std::move(t.fn);tasks.pop_front();f();continue;}
      if(t.kind==Task::Delay){if(!t.started){t.started=true;t.start=now;t.end=now+t.duration;}if(now<t.end)return {};
        auto fields=t.fields;fields.push_back({"kind",str(t.label)});fields.push_back({"start",num(t.start)});fields.push_back({"end",num(t.end)});log->event(now,"cpu_op",fields);tasks.pop_front();continue;}
      if(t.kind==Task::Word){if(!t.started){t.started=true;return {t.cmd,false};}auto i=accept.find(t.cmd.tag);if(i==accept.end())return {};
        require(i->second.cycle<now,"accept next-cycle visibility");log->event(now,"command_accept_observed",{{"stage",num(t.stage?t.stage->id:0)},{"tag",num(t.cmd.tag)},{"accepted_cycle",num(i->second.cycle)}});
        if(t.stage&&t.cmd.funct!=126){t.stage->last=i->second.cycle;t.stage->count++;}accept.erase(i);tasks.pop_front();continue;}
      auto i=responses.find(t.cmd.tag);if(i==responses.end())return {};auto r=i->second;require(r.cycle<now&&r.rd==1&&r.data==uint32_t(r.data),"true counter response required");
      log->event(now,"response_observed",{{"tag",num(r.command_tag)},{"response_cycle",num(r.cycle)},{"rd",num(r.rd)},{"data",num(r.data)}});
      auto done=std::move(t.response);responses.erase(i);tasks.pop_front();done(uint32_t(r.data),r.cycle);
    }
    throw std::runtime_error("zero-time task loop");
  }
};

DriverBundle create_driver(const DriverConfig& cfg,EmitJSON emit){auto log=std::make_shared<Log>();log->emit=std::move(emit);auto ledger=std::make_unique<ServiceLedger>(cfg,log);auto controller=std::make_unique<ServiceController>(cfg,*ledger,log);return {std::move(ledger),std::move(controller)};}
void save_driver_outputs(const DriverBundle& bundle,const std::string& directory){
  auto& l=dynamic_cast<const ServiceLedger&>(*bundle.ledger);auto& c=dynamic_cast<const ServiceController&>(*bundle.controller);
  std::ofstream c1(directory+"/query_outputs.bin",std::ios::binary),a2(directory+"/query_a2_outputs.bin",std::ios::binary),c2(directory+"/query_c2_outputs.bin",std::ios::binary),times(directory+"/query_times.csv"),json(directory+"/query_results.json");
  times<<"query,arrival,device_ready,consumed\n";json<<"[\n";
  for(unsigned id=0;id<l.cfg.query_count;id++){auto& q=l.qs.at(id);c1.write(reinterpret_cast<const char*>(q.data.data()),q.data.size());
    for(auto kind:{"a2","c2"}){auto& s=l.stages.at({id,kind});auto& out=std::string(kind)=="a2"?a2:c2;out.write(reinterpret_cast<const char*>(s.data.data()),s.data.size());}
    auto& x=c.queries.at(id);times<<id<<","<<x.arrival.cycle<<","<<q.ready<<","<<x.delivery_end<<"\n";
    json<<"{\"query\":"<<id<<",\"arrival\":"<<x.arrival.cycle<<",\"admitted\":"<<x.admitted<<",\"c1_notified\":"<<x.c1_notified<<",\"consumer_start\":"<<x.consumer_start<<",\"a2_sealed\":"<<x.a2_sealed<<",\"c2_notified\":"<<x.c2_notified<<",\"delivery_end\":"<<x.delivery_end<<",\"panel_count\":"<<x.sealed<<"}"<<(id+1==l.cfg.query_count?"\n":",\n");}
  json<<"]\n";require(bool(c1)&&bool(a2)&&bool(c2)&&bool(times)&&bool(json),"output evidence write failed");
}
} // namespace online_service
