#include "verilated_engine.hpp"
#include "driver_main.hpp"
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <regex>
#include <sstream>
#include <stdexcept>
using namespace online_service;
static std::string jq(const std::string& s){std::string o="\"";for(char c:s){if(c=='\"'||c=='\\')o+='\\';o+=c;}return o+"\"";}
static uint64_t field(const std::string& j,const std::string& k){std::smatch m;std::regex r("\\\""+k+"\\\"\\s*:\\s*([0-9]+)");if(!std::regex_search(j,m,r))throw std::runtime_error("missing metadata "+k);return std::stoull(m[1]);}
int main(int argc,char**argv){std::filesystem::path out;bool made=false;try{
 std::map<std::string,std::string> o;for(int i=1;i<argc;i++){std::string k=argv[i];if(k[0]=='+')continue;if(k.rfind("--",0)||i+1==argc)throw std::runtime_error("bad option");o[k]=argv[++i];}
 auto req=[&](const std::string& k){auto it=o.find(k);if(it==o.end())throw std::runtime_error("required "+k);return it->second;};auto val=[&](const std::string&k,const std::string&d){auto it=o.find(k);return it==o.end()?d:it->second;};
 std::ifstream mf(req("--meta"));std::string s((std::istreambuf_iterator<char>(mf)),{});DriverConfig dc;
 dc.n=field(s,"n");dc.k=field(s,"k");dc.query_count=field(s,"query_count");dc.input_base=field(s,"a_address");dc.weight_base=field(s,"b_address");dc.output_base=field(s,"c_address");dc.output_budget_bytes=field(s,"output_budget_bytes");dc.weight2_base=field(s,"w2_address");dc.a2_base=field(s,"a2_address");dc.c2_base=field(s,"c2_address");
 dc.policy=val("--policy","cyclic");dc.tile_j=std::stoul(val("--tile-j","1"));dc.batch_target=std::stoul(val("--batch-target","16"));dc.timeout_cycles=std::stoull(val("--timeout-cycles","0"));dc.poll_cycles=std::stoull(val("--poll-cycles","64"));dc.decision_cycles=std::stoull(val("--decision-cycles","16"));dc.issue_cycles=std::stoull(val("--issue-cycles","1"));dc.sink_cycles=std::stoull(val("--sink-cycles","64"));
 dc.c1_group_panels=std::stoul(val("--c1-group-panels","2"));
 out=req("--output-dir");if(std::filesystem::exists(out))throw std::runtime_error("output exists");std::filesystem::create_directories(out);made=true;std::ofstream dl(out/"driver_events.jsonl");
 auto bundle=create_driver(dc,[&](const std::string& x){dl<<x<<'\n';});EngineConfig ec;ec.cpu_free_completion_adapter=false;ec.memory_image=req("--memory-image");ec.arrivals_csv=req("--arrivals");ec.event_log=(out/"engine_events.jsonl").string();ec.dump_memory=(out/"actual_memory.bin").string();ec.report=(out/"result.json").string();ec.memory_latency_cycles=std::stoul(val("--memory-latency","2"));ec.a_stall_period=std::stoul(val("--a-stall-period","0"));ec.d_stall_period=std::stoul(val("--d-stall-period","0"));ec.max_cycles=std::stoull(val("--max-cycles","5000000"));
 std::ofstream rc(out/"runtime_config.json");rc<<"{\"n\":"<<dc.n<<",\"k\":"<<dc.k<<",\"query_count\":"<<dc.query_count<<",\"input_base\":"<<dc.input_base<<",\"weight_base\":"<<dc.weight_base<<",\"output_base\":"<<dc.output_base<<",\"output_budget_bytes\":"<<dc.output_budget_bytes<<",\"w2_address\":"<<dc.weight2_base<<",\"a2_address\":"<<dc.a2_base<<",\"c2_address\":"<<dc.c2_base<<",\"policy\":"<<jq(dc.policy)<<",\"tile_j\":"<<dc.tile_j<<",\"batch_target\":"<<dc.batch_target<<",\"timeout_cycles\":"<<dc.timeout_cycles<<",\"c1_group_panels\":"<<dc.c1_group_panels<<",\"poll_cycles\":"<<dc.poll_cycles<<",\"decision_cycles\":"<<dc.decision_cycles<<",\"issue_cycles\":"<<dc.issue_cycles<<",\"sink_cycles\":"<<dc.sink_cycles<<",\"memory_latency_cycles\":"<<ec.memory_latency_cycles<<",\"a_stall_period\":"<<ec.a_stall_period<<",\"d_stall_period\":"<<ec.d_stall_period<<",\"max_cycles\":"<<ec.max_cycles<<"}\n";
 auto engine=make_engine(argc,argv,ec,*bundle.controller,*bundle.ledger);while(engine->step()){}save_driver_outputs(bundle,out.string());std::cout<<"ONLINE_DRAINED policy="<<dc.policy<<" cycles="<<engine->cycle()<<"\n";return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";if(made){std::ofstream f(out/"failure.json");f<<"{\"status\":\"failed\",\"detail\":"<<jq(e.what())<<"}\n";}return 1;}}
