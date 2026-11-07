#include "verilated_engine.hpp"
#include "VGemmini.h"
#include "VGemmini___024root.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <deque>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <sstream>
#include <string>
#include <vector>

struct Response {
    uint32_t opcode, size, source;
    std::array<uint32_t, 4> data{};
    uint64_t eligible;
    bool last;
};
struct Transaction {
    uint32_t opcode, address, size, a_beats, expected_a_beats, d_beats, expected_d_beats;
    uint64_t first_cycle;
    uint64_t uid = 0;
    uint32_t source_generation = 0;
};
struct APayload {
    uint32_t opcode, param, size, source, address, mask, corrupt;
    std::array<uint32_t, 4> data;
    bool operator==(const APayload& other) const {
        return opcode == other.opcode && param == other.param && size == other.size &&
            source == other.source && address == other.address && mask == other.mask &&
            corrupt == other.corrupt && data == other.data;
    }
};
struct Options {
    std::string mode = "smoke", report, commands_out, a_events_out, trace, memory_image, dump_memory;
    uint64_t memory_latency = 2, a_stall_period = 0, d_stall_period = 0, max_cycles = 1000000;
};

using namespace online_service;

class Harness {
public:
    std::unique_ptr<VerilatedContext> context = std::make_unique<VerilatedContext>();
    VGemmini top{context.get()};
    static constexpr uint32_t base = 0x80000000u;
    std::vector<uint8_t> memory = std::vector<uint8_t>(16 * 1024 * 1024, 0);
    std::deque<Response> responses;
    std::map<uint32_t, Transaction> transactions;
    bool a_held = false;
    APayload held_a{};
    uint64_t cycles = 0, gets = 0, puts = 0, dbeats = 0, commands = 0;
    uint64_t abeats = 0, get_payload_bytes = 0, put_transfer_bytes = 0, put_masked_bytes = 0;
    uint64_t get_data_dbeats = 0, put_data_abeats = 0, a_stall_cycles = 0, d_stall_cycles = 0;
    uint64_t a_valid_cycles = 0, d_valid_cycles = 0, busy_cycles = 0, command_stall_cycles = 0;
    uint64_t source_lifetime_sum = 0, source_lifetime_max = 0, live_sources_max = 0;
    uint64_t handshake_digest = 14695981039346656037ull;
    std::array<uint64_t, 7> get_size_hist{};
    static constexpr uint64_t unset_cycle = ~uint64_t(0);
    uint64_t first_command_cycle = unset_cycle, last_a_cycle = unset_cycle;
    uint64_t last_get_d_cycle = unset_cycle, last_put_ack_cycle = unset_cycle, drain_complete_cycle = unset_cycle;
    bool cmd_fire = false;
    bool response_active = false;
    Options options;
    std::ofstream command_log;
    std::ofstream a_events;
    std::vector<std::string> checks;
    Controller& controller;
    EventLedger& event_ledger;
    EngineConfig engine_config;
    std::ofstream events;
    uint64_t event_seq = 0, next_transaction_uid = 1;
    std::map<uint32_t,uint32_t> source_generations;
    std::vector<Arrival> arrivals;
    size_t next_arrival = 0;
    std::optional<Command> pending;
    uint64_t pending_id = 0, next_command_id = 1;
    bool pending_tried = false, stop_requested = false, finished = false;
    unsigned quiet_cycles = 0;
    std::deque<Command> response_commands;
    bool rocc_held = false;
    uint32_t held_rd = 0;
    uint64_t held_response = 0;
    uint32_t observer_wdma_previous = 0;
    uint64_t rocc_responses = 0;
    void notify_rocc_response(uint32_t rd, uint64_t data) {
        if(response_commands.empty()) throw std::runtime_error("unexpected RoCC response");
        const auto command=response_commands.front();
        if(rd!=command.rd) throw std::runtime_error("RoCC response rd mismatch");
        RoCCResponse e; e.cycle=cycles; e.rd=rd; e.data=data; e.command_tag=command.tag;
        log("rocc_response", ",\"rd\":"+std::to_string(rd)+",\"data\":"+std::to_string(data)
          +",\"tag\":"+std::to_string(command.tag));
        response_commands.pop_front(); ++rocc_responses;
        controller.on_rocc_response(e);
    }

    void log(const std::string& event, const std::string& fields="") {
        if (events) events << "{\"cycle\":" << cycles << ",\"event\":\"" << event
            << "\",\"seq\":" << event_seq++ << fields << "}\n";
    }
    static std::string hex_bytes(const std::array<uint32_t,4>& data) {
        std::ostringstream out; out << std::hex << std::setfill('0');
        for(unsigned j=0;j<16;j++) out << std::setw(2) << unsigned(uint8_t(data[j/4] >> (8*(j%4))));
        return out.str();
    }
    void dispatch_arrival(const Arrival& a) {
        if(a.cycle != cycles) throw std::runtime_error("arrival injected at wrong independent cycle");
        log("arrival", ",\"query\":"+std::to_string(a.query)+",\"generation\":"+std::to_string(a.generation)
            +",\"dram_input\":"+std::to_string(a.dram_input)+",\"n\":"+std::to_string(a.n)+",\"k\":"+std::to_string(a.k));
        event_ledger.on_arrival(a); controller.on_arrival(a);
    }
    void due_arrivals() {
        while(next_arrival < arrivals.size() && arrivals[next_arrival].cycle == cycles)
            dispatch_arrival(arrivals[next_arrival++]);
        if(next_arrival < arrivals.size() && arrivals[next_arrival].cycle < cycles)
            throw std::runtime_error("independent arrival clock skipped an event");
    }
    void assign_transaction(Transaction& t, uint32_t source) {
        t.uid=next_transaction_uid++; t.source_generation=++source_generations[source];
    }
    void notify_a(const Transaction& t, const APayload& a, uint32_t beat) {
        AcceptedA e; e.cycle=cycles; e.transaction=t.uid; e.opcode=AOpcode(a.opcode); e.source=a.source;
        e.source_generation=t.source_generation; e.address=a.address; e.size=a.size; e.mask=a.mask;
        e.data=a.data; e.beat=beat; e.expected_beats=t.expected_a_beats; e.last_a=beat+1==t.expected_a_beats;
        log("tl_a", ",\"opcode\":"+std::to_string(a.opcode)+",\"size\":"+std::to_string(a.size)
          +",\"source\":"+std::to_string(a.source)+",\"source_gen\":"+std::to_string(t.source_generation)
          +",\"txid\":"+std::to_string(t.uid)+",\"address\":"+std::to_string(a.address)
          +",\"mask\":"+std::to_string(a.mask)+",\"beat_index\":"+std::to_string(beat)
          +",\"expected_beats\":"+std::to_string(t.expected_a_beats)+",\"first\":"+(beat==0?"true":"false")
          +",\"last\":"+(e.last_a?"true":"false")+",\"data_hex\":\""+hex_bytes(a.data)+"\"");
        event_ledger.on_a_accepted(e); controller.on_a_accepted(e);
    }
    void notify_d(const Transaction& t, const Response& r, uint32_t beat) {
        AcceptedD e; e.cycle=cycles; e.transaction=t.uid; e.opcode=AOpcode(t.opcode); e.d_opcode=r.opcode;
        e.source=r.source; e.source_generation=t.source_generation; e.size=r.size; e.beat=beat;
        e.expected_beats=t.expected_d_beats; e.last_d=r.last; e.first_a_cycle=t.first_cycle;
        log("tl_d", ",\"opcode\":"+std::to_string(r.opcode)+",\"size\":"+std::to_string(r.size)
          +",\"source\":"+std::to_string(r.source)+",\"source_gen\":"+std::to_string(t.source_generation)
          +",\"txid\":"+std::to_string(t.uid)+",\"beat_index\":"+std::to_string(beat)
          +",\"expected_beats\":"+std::to_string(t.expected_d_beats)+",\"first\":"+(beat==0?"true":"false")
          +",\"last\":"+(r.last?"true":"false")+",\"denied\":false,\"corrupt\":false,\"data_hex\":\""+hex_bytes(r.data)+"\"");
        event_ledger.on_d_accepted(e); controller.on_d_accepted(e);
    }
    void drive_controller() {
        CommandResult r=controller.tick(cycles,event_ledger);
        stop_requested |= r.request_stop;
        if(r.command) {
            if(pending) {
                const auto& a=*pending; const auto& b=*r.command;
                if(a.funct!=b.funct || a.rs1!=b.rs1 || a.rs2!=b.rs2 || a.tag!=b.tag || a.rd!=b.rd || a.xd!=b.xd)
                    throw std::runtime_error("controller replaced a stalled command");
            } else {pending=*r.command; pending_id=next_command_id++; pending_tried=false;}
        }
        top.io_cmd_valid = pending.has_value();
        if(pending) {
            top.io_cmd_bits_inst_funct=pending->funct; top.io_cmd_bits_rs1=pending->rs1; top.io_cmd_bits_rs2=pending->rs2;
            top.io_cmd_bits_inst_rd=pending->rd; top.io_cmd_bits_inst_xd=pending->xd;
        }
    }
    void notify_command(bool accepted, bool sampled_ready) {
        if(!pending) throw std::runtime_error("command event without engine-held payload");
        CommandAttempt e; e.cycle=cycles; e.command=*pending; e.valid=true; e.ready=sampled_ready; e.accepted=accepted;
        log("rocc_pending_sample", ",\"tag\":"+std::to_string(pending->tag)+",\"funct\":"+std::to_string(pending->funct)
          +",\"rs1\":"+std::to_string(pending->rs1)+",\"rs2\":"+std::to_string(pending->rs2)
          +",\"rd\":"+std::to_string(pending->rd)+",\"xd\":"+(pending->xd?"true":"false")
          +",\"ready\":"+(sampled_ready?"true":"false"));
        if(!pending_tried) {
            log("rocc_try", ",\"cmd_id\":"+std::to_string(pending_id)+",\"tag\":"+std::to_string(pending->tag)
              +",\"funct\":"+std::to_string(pending->funct)+",\"rs1\":"+std::to_string(pending->rs1)+",\"rs2\":"+std::to_string(pending->rs2)+",\"rd\":"+std::to_string(pending->rd)+",\"xd\":"+(pending->xd?"true":"false"));
            pending_tried=true;
        }
        event_ledger.on_command_attempt(e); controller.on_command_attempt(e);
        if(accepted) {
            log("rocc_accept", ",\"cmd_id\":"+std::to_string(pending_id)+",\"tag\":"+std::to_string(pending->tag)
              +",\"funct\":"+std::to_string(pending->funct)+",\"rs1\":"+std::to_string(pending->rs1)+",\"rs2\":"+std::to_string(pending->rs2)+",\"rd\":"+std::to_string(pending->rd)+",\"xd\":"+(pending->xd?"true":"false"));
            if(pending->funct==126) {
                if(!pending->xd || !pending->rd) throw std::runtime_error("counter command needs real rd/xd");
                response_commands.push_back(*pending);
            } else if(pending->xd) throw std::runtime_error("unsupported response-producing command");
            event_ledger.on_command_accepted(e); controller.on_command_accepted(e); pending.reset();
        }
    }

    Harness(int argc, char** argv, Options opts, const EngineConfig& cfg, Controller& ctrl, EventLedger& ledger)
      : options(std::move(opts)), controller(ctrl), event_ledger(ledger), engine_config(cfg) {
        context->commandArgs(argc, argv);
        memory.resize(cfg.memory_bytes);
        if(!cfg.event_log.empty()) {events.open(cfg.event_log); if(!events) throw std::runtime_error("cannot open raw event log");}
        if(!cfg.memory_image.empty()) {
            std::ifstream input(cfg.memory_image,std::ios::binary); if(!input) throw std::runtime_error("cannot open memory image");
            input.read(reinterpret_cast<char*>(memory.data()),memory.size());
            if(!input.eof() && input.fail()) throw std::runtime_error("cannot read memory image");
        }
        if(!cfg.arrivals_csv.empty()) {
            std::ifstream input(cfg.arrivals_csv); if(!input) throw std::runtime_error("cannot open arrivals CSV");
            std::string line; std::getline(input,line);
            if(line!="cycle,query,generation,dram_input,n,k") throw std::runtime_error("arrival CSV header mismatch");
            while(std::getline(input,line)) {
                if(line.empty()) continue; std::stringstream stream(line); std::vector<uint64_t> f; std::string token;
                while(std::getline(stream,token,',')) f.push_back(std::stoull(token,nullptr,0));
                if(f.size()!=6) throw std::runtime_error("arrival CSV row width mismatch");
                Arrival a; a.cycle=f[0];a.query=f[1];a.generation=f[2];a.dram_input=f[3];a.n=f[4];a.k=f[5];
                if(!arrivals.empty() && a.cycle<arrivals.back().cycle) throw std::runtime_error("arrival CSV not sorted");
                arrivals.push_back(a);
            }
        }
        if (!options.commands_out.empty()) {
            command_log.open(options.commands_out);
            if (!command_log) throw std::runtime_error("Cannot open command log");
            command_log << "cycle,funct,rs1,rs2\n";
        }
        if (!options.a_events_out.empty()) {
            a_events.open(options.a_events_out);
            if (!a_events) throw std::runtime_error("Cannot open TileLink A event log");
            a_events << "cycle,opcode,source,address,size,mask\n";
        }
        #include "init_inputs.h"
        top.io_cmd_bits_status_prv = 3;
        top.io_cmd_bits_status_dprv = 3;
        top.io_cmd_bits_status_sxl = 2;
        top.io_cmd_bits_status_uxl = 2;
        top.io_cmd_bits_inst_opcode = 0x7b;
        top.io_cmd_bits_inst_xs1 = 1;
        top.io_cmd_bits_inst_xs2 = 1;
        top.io_resp_ready = 1;
        top.io_ptw_0_req_ready = 1;
        // Bare-address satp mode 0, with explicit NAPOT PMP permission.
        // This generated Gemmini does not propagate status.prv to TLBReq.prv.
        top.io_ptw_0_ptbr_mode = 0;
        top.io_ptw_0_pmp_0_cfg_a = 3;
        top.io_ptw_0_pmp_0_cfg_r = 1;
        top.io_ptw_0_pmp_0_cfg_w = 1;
        top.io_ptw_0_pmp_0_cfg_x = 1;
        top.io_ptw_0_pmp_0_addr = 0x3fffffffu;
        top.io_ptw_0_pmp_0_mask = 0xffffffffu;
        top.reset = 1;
        for (int i = 0; i < 20; ++i) step();
        top.reset = 0;
        for (int i = 0; i < 20; ++i) step();
    }

    uint8_t& byte(uint32_t address) {
        if (address < base || uint64_t(address - base) >= memory.size())
            throw std::runtime_error("Out-of-range TileLink address " + std::to_string(address));
        return memory[address - base];
    }

    std::string state() const {
        std::ostringstream out;
        out << "cycle=" << cycles << " commands=" << commands << " busy=" << unsigned(top.io_busy)
            << " pending_D_beats=" << responses.size() << " live_sources=[";
        for (const auto& entry : transactions) {
            const auto& t = entry.second;
            out << entry.first << ":opcode" << t.opcode << ":A" << t.a_beats << '/' << t.expected_a_beats
                << ":D" << t.d_beats << '/' << t.expected_d_beats << ';';
        }
        out << ']';
        return out.str();
    }

    void digest(uint64_t word) {
        // FNV-1a over an explicitly ordered, little-endian stream of 64-bit words.
        for (unsigned i = 0; i < 8; ++i) {
            handshake_digest ^= uint8_t(word >> (8 * i));
            handshake_digest *= 1099511628211ull;
        }
    }

    void step() {
        top.clock = 0;
        top.io_resp_ready = !top.reset && (!engine_config.response_stall_period ||
            cycles % engine_config.response_stall_period >= engine_config.response_stall_length);
        top.auto_spad_id_out_a_ready = !top.reset && responses.size() < 512 &&
            (!options.a_stall_period || cycles % options.a_stall_period != 0);
        if (!response_active && !top.reset && !responses.empty() && responses.front().eligible <= cycles &&
            (!options.d_stall_period || cycles % options.d_stall_period != 0)) response_active = true;
        bool responding = response_active;
        top.auto_spad_id_out_d_valid = responding;
        if (responding) {
            const auto& r = responses.front();
            top.auto_spad_id_out_d_bits_opcode = r.opcode;
            #ifdef HAS_auto_spad_id_out_d_bits_param
            top.auto_spad_id_out_d_bits_param = 0;
            #endif
            top.auto_spad_id_out_d_bits_size = r.size;
            top.auto_spad_id_out_d_bits_source = r.source;
            #ifdef HAS_auto_spad_id_out_d_bits_sink
            top.auto_spad_id_out_d_bits_sink = 0;
            #endif
            #ifdef HAS_auto_spad_id_out_d_bits_denied
            top.auto_spad_id_out_d_bits_denied = 0;
            #endif
            #ifdef HAS_auto_spad_id_out_d_bits_corrupt
            top.auto_spad_id_out_d_bits_corrupt = 0;
            #endif
            for (int i = 0; i < 4; ++i) top.auto_spad_id_out_d_bits_data[i] = r.data[i];
        }
        top.eval();
        const bool rocc_valid = !top.reset && top.io_resp_valid;
        const uint32_t rocc_rd = top.io_resp_bits_rd;
        const uint64_t rocc_data = top.io_resp_bits_data;
        const bool rocc_fire = rocc_valid && top.io_resp_ready;
        if(!top.reset && rocc_held && (!rocc_valid || rocc_rd!=held_rd || rocc_data!=held_response))
            throw std::runtime_error("RoCC response changed while valid&&!ready");
        rocc_held = rocc_valid && !top.io_resp_ready;
        if(rocc_held) {held_rd=rocc_rd; held_response=rocc_data;}
        const uint32_t observer_wdma = top.rootp->Gemmini__DOT__spad__DOT__writer__DOT__total_bytes_sent;
        const uint64_t observer_counter_data = top.rootp->Gemmini__DOT__counters__DOT___counterfile_io_data;
        if(!top.reset && observer_wdma!=observer_wdma_previous) {
            log("observer_wdma", ",\"bytes\":"+std::to_string(observer_wdma));
            observer_wdma_previous=observer_wdma;
        }
        if(rocc_valid) log("rocc_response_attempt", ",\"ready\":"+(std::string)(top.io_resp_ready?"true":"false")
          +",\"rd\":"+std::to_string(rocc_rd)+",\"data\":"+std::to_string(rocc_data));
        const bool a_fire = !top.reset && top.auto_spad_id_out_a_valid && top.auto_spad_id_out_a_ready;
        const bool d_fire = responding && top.auto_spad_id_out_d_ready;
        const bool sampled_command_ready=top.io_cmd_ready;
        const bool sampled_io_busy=top.io_busy;
        cmd_fire = !top.reset && top.io_cmd_valid && sampled_command_ready;
        const uint32_t opcode = top.auto_spad_id_out_a_bits_opcode;
        const uint32_t size = top.auto_spad_id_out_a_bits_size;
        const uint32_t source = top.auto_spad_id_out_a_bits_source;
        const uint32_t address = top.auto_spad_id_out_a_bits_address;
        const uint32_t mask = top.auto_spad_id_out_a_bits_mask;
        std::array<uint32_t, 4> payload{};
        for (int i = 0; i < 4; ++i) payload[i] = top.auto_spad_id_out_a_bits_data[i];
        const APayload current_a{opcode, uint32_t(top.auto_spad_id_out_a_bits_param), size, source,
            address, mask, uint32_t(top.auto_spad_id_out_a_bits_corrupt), payload};
        if (!top.reset && a_held && (!top.auto_spad_id_out_a_valid || !(current_a == held_a)))
            throw std::runtime_error("TileLink A payload changed while valid&&!ready; " + state());
        a_held = !top.reset && top.auto_spad_id_out_a_valid && !top.auto_spad_id_out_a_ready;
        if (a_held) held_a = current_a;
        if (!top.reset) {
            a_valid_cycles += bool(top.auto_spad_id_out_a_valid);
            d_valid_cycles += responding;
            a_stall_cycles += bool(top.auto_spad_id_out_a_valid && !top.auto_spad_id_out_a_ready);
            d_stall_cycles += bool(responding && !top.auto_spad_id_out_d_ready);
            command_stall_cycles += bool(top.io_cmd_valid && !top.io_cmd_ready);
            busy_cycles += bool(top.io_busy);
            digest(cycles);
            digest(uint64_t(top.auto_spad_id_out_a_valid) | (uint64_t(top.auto_spad_id_out_a_ready) << 1) |
                (uint64_t(responding) << 2) | (uint64_t(top.auto_spad_id_out_d_ready) << 3) |
                (uint64_t(top.io_cmd_valid) << 4) | (uint64_t(top.io_cmd_ready) << 5) |
                (uint64_t(top.io_busy) << 6));
            if (a_fire) {
                digest(0xa); digest(opcode); digest(current_a.param); digest(size); digest(source);
                digest(address); digest(mask); digest(current_a.corrupt);
                for (uint32_t word : payload) digest(word);
            }
            if (d_fire) {
                const auto& r = responses.front();
                digest(0xd); digest(r.opcode); digest(r.size); digest(r.source);
                for (uint32_t word : r.data) digest(word);
            }
            if (cmd_fire) {
                digest(0xc); digest(top.io_cmd_bits_inst_funct); digest(top.io_cmd_bits_rs1); digest(top.io_cmd_bits_rs2);
            }
        }
        if (!top.reset && top.io_ptw_0_req_valid)
            throw std::runtime_error("Unexpected page-table walk in bare M-mode");
        if (!top.reset && top.io_interrupt)
            throw std::runtime_error("Gemmini translation/permission interrupt cycle=" + std::to_string(cycles) + " gets=" + std::to_string(gets) + " puts=" + std::to_string(puts) + " commands=" + std::to_string(commands));
        top.clock = 1;
        context->timeInc(1);
        top.eval();
        context->timeInc(1);
        if(!top.reset && !rocc_valid && top.io_resp_valid) {
            // Observer only: captures pre-edge source value for the newly valid response.
            // It is never delivered to the controller or used in a scheduling decision.
            log("observer_counter_capture", ",\"source_data\":"+std::to_string(observer_counter_data)
              +",\"response_data\":"+std::to_string(uint64_t(top.io_resp_bits_data))
              +",\"rd\":"+std::to_string(unsigned(top.io_resp_bits_rd)));
        }
        if(!top.reset) {
            DeviceStatus status; status.cycle=cycles; status.busy=sampled_io_busy;
            log("device_status_sample", ",\"busy\":"+std::string(status.busy?"true":"false"));
            controller.on_device_status(status);
        }
        if(rocc_fire) notify_rocc_response(rocc_rd,rocc_data);
        if (d_fire) {
            const Response r = responses.front();
            auto it = transactions.find(r.source);
            if (it == transactions.end()) throw std::runtime_error("Response to non-live source");
            auto& t = it->second;
            if (t.a_beats != t.expected_a_beats || ++t.d_beats > t.expected_d_beats ||
                r.last != (t.d_beats == t.expected_d_beats))
                throw std::runtime_error("TileLink D transaction accounting mismatch; " + state());
            if(r.size!=t.size || r.opcode!=(t.opcode==4?1u:0u)) throw std::runtime_error("D opcode/size differs from live A transaction");
            notify_d(t,r,t.d_beats-1);
            if (r.opcode == 1) { ++get_data_dbeats; last_get_d_cycle = cycles; }
            if (r.opcode == 0) last_put_ack_cycle = cycles;
            if (r.last) {
                const uint64_t lifetime = cycles - t.first_cycle + 1;
                source_lifetime_sum += lifetime;
                source_lifetime_max = std::max(source_lifetime_max, lifetime);
                transactions.erase(it);
            }
            responses.pop_front(); response_active = false; ++dbeats;
        }
        if(!top.reset && top.io_cmd_valid) notify_command(cmd_fire,sampled_command_ready);
        if (cmd_fire) {
            if (!commands) first_command_cycle = cycles;
            ++commands;
            if (command_log) command_log << cycles << ',' << uint32_t(top.io_cmd_bits_inst_funct) << ",0x" << std::hex << uint64_t(top.io_cmd_bits_rs1) << ",0x" << uint64_t(top.io_cmd_bits_rs2) << std::dec << '\n';
        }
        if (a_fire) {
            last_a_cycle = cycles;
            if (a_events) a_events << cycles << ',' << opcode << ',' << source << ",0x" << std::hex << address
                                   << std::dec << ',' << size << ",0x" << std::hex << mask << std::dec << '\n';
            ++abeats;
            if (size > 6 || current_a.param != 0 || current_a.corrupt)
                throw std::runtime_error("Illegal TileLink A attributes; " + state());
            const uint32_t bytes = 1u << size;
            if (bytes > 64) throw std::runtime_error("Unexpected DMA size >64B");
            const uint32_t beats = std::max(1u, (bytes + 15) / 16);
            const uint32_t expected_mask = bytes >= 16 ? 0xffffu : ((1u << bytes) - 1) << (address & 15u);
            if ((address & (bytes - 1)) || (mask & ~expected_mask) ||
                ((opcode == 4 || opcode == 0) && mask != expected_mask))
                throw std::runtime_error("Illegal TileLink A alignment/mask; " + state());
            if (opcode == 4) {
                if (transactions.count(source)) throw std::runtime_error("Premature TileLink source reuse; " + state());
                auto inserted=transactions.emplace(source, Transaction{opcode, address, size, 1, 1, 0, beats, cycles});
                assign_transaction(inserted.first->second,source);
                notify_a(inserted.first->second,current_a,0);
                ++gets;
                get_payload_bytes += bytes;
                ++get_size_hist[size];
                for (uint32_t b = 0; b < beats; ++b) {
                    Response r{1, size, source, {}, cycles + options.memory_latency, b + 1 == beats};
                    for (uint32_t j = 0; j < 16; ++j) {
                        const uint32_t addr = (address & ~15u) + b * 16 + j;
                        r.data[j / 4] |= uint32_t(byte(addr)) << ((j % 4) * 8);
                    }
                    responses.push_back(r);
                }
            } else if (opcode == 0 || opcode == 1) {
                auto it = transactions.find(source);
                if (it == transactions.end()) {
                    it = transactions.emplace(source, Transaction{opcode, address, size, 0, beats, 0, 1, cycles}).first;
                    assign_transaction(it->second,source);
                }
                auto& w = it->second;
                if (w.opcode != opcode || w.address != address || w.size != size || w.a_beats == w.expected_a_beats)
                    throw std::runtime_error("Inconsistent Put or premature TileLink source reuse; " + state());
                notify_a(w,current_a,w.a_beats);
                for (uint32_t j = 0; j < 16; ++j)
                    if (mask & (1u << j)) byte((w.address & ~15u) + w.a_beats * 16 + j) = uint8_t(payload[j / 4] >> ((j % 4) * 8));
                if (++w.a_beats == beats) {
                    responses.push_back(Response{0, size, source, {}, cycles + options.memory_latency, true});
                    ++puts;
                    put_transfer_bytes += bytes;
                }
                ++put_data_abeats;
                put_masked_bytes += __builtin_popcount(mask);
            } else throw std::runtime_error("Unsupported TileLink A opcode " + std::to_string(opcode));
            live_sources_max = std::max(live_sources_max, uint64_t(transactions.size()));
        }
        due_arrivals();
        ++cycles;
        if (cycles > options.max_cycles) throw std::runtime_error("Global cycle limit exceeded; " + state());
        if (context->gotFinish()) throw std::runtime_error("RTL stopped simulation");
    }

    void finish() {
        if (!options.dump_memory.empty()) {
            std::ofstream image(options.dump_memory, std::ios::binary);
            image.write(reinterpret_cast<const char*>(memory.data()), memory.size());
            if (!image) throw std::runtime_error("Cannot write memory dump");
        }
        if (!options.report.empty()) {
            std::ofstream report(options.report);
            if (!report) throw std::runtime_error("Cannot write JSON report");
            report << "{\n  \"status\": \"" << (options.mode == "smoke" ? "correctness_pass" : "replay_completed") << "\",\n  \"matrix_correctness_verified\": " << (options.mode == "smoke" ? "true" : "false") << ",\n  \"evidence_class\": \"requires_provenance_and_external_validation\",\n  \"cpu_instantiated\": false,\n  \"mode\": \"" << options.mode << "\",\n  \"cycles_including_reset\": " << cycles << ",\n  \"reset_and_initial_idle_cycles\": 40,\n  \"rocc_commands\": " << commands << ",\n  \"tl_get_transactions\": " << gets << ",\n  \"tl_put_transactions\": " << puts << ",\n  \"tl_d_beats\": " << dbeats << ",\n  \"memory_latency_cycles\": " << options.memory_latency << ",\n  \"a_stall_period\": " << options.a_stall_period << ",\n  \"d_stall_period\": " << options.d_stall_period << ",\n  \"translation\": \"bare_address_with_explicit_pmp_allow\",\n  \"checks\": [";
            for (size_t i = 0; i < checks.size(); ++i) report << (i ? ", " : "") << '"' << checks[i] << '"';
            report << "],\n  \"tl_a_beats\": " << abeats
                   << ",\n  \"tl_get_payload_bytes\": " << get_payload_bytes
                   << ",\n  \"tl_put_transfer_bytes\": " << put_transfer_bytes
                   << ",\n  \"tl_put_masked_bytes\": " << put_masked_bytes
                   << ",\n  \"tl_put_data_a_beats\": " << put_data_abeats
                   << ",\n  \"tl_get_data_d_beats\": " << get_data_dbeats
                   << ",\n  \"tl_data_lane_bytes\": " << 16 * (put_data_abeats + get_data_dbeats)
                   << ",\n  \"tl_a_stall_cycles\": " << a_stall_cycles
                   << ",\n  \"tl_d_stall_cycles\": " << d_stall_cycles
                   << ",\n  \"tl_a_valid_cycles\": " << a_valid_cycles
                   << ",\n  \"tl_d_valid_cycles\": " << d_valid_cycles
                   << ",\n  \"rocc_command_stall_cycles\": " << command_stall_cycles
                   << ",\n  \"gemmini_busy_cycles\": " << busy_cycles
                   << ",\n  \"tl_source_lifetime_cycles_sum\": " << source_lifetime_sum
                   << ",\n  \"tl_source_lifetime_cycles_max\": " << source_lifetime_max
                   << ",\n  \"tl_live_sources_max\": " << live_sources_max
                   << ",\n  \"tl_source_lifetime_definition\": \"last_D_cycle_minus_first_A_cycle_plus_one\""
                   << ",\n  \"first_command_accept_cycle\": " << first_command_cycle
                   << ",\n  \"last_TL_A_cycle\": " << last_a_cycle
                   << ",\n  \"last_Get_D_cycle\": " << last_get_d_cycle
                   << ",\n  \"last_PutAck_cycle\": " << last_put_ack_cycle
                   << ",\n  \"drain_complete_cycle\": " << drain_complete_cycle
                   << ",\n  \"kernel_service_cycles_first_command_to_last_put_ack_inclusive\": " <<
                        (first_command_cycle != unset_cycle && last_put_ack_cycle != unset_cycle ? last_put_ack_cycle - first_command_cycle + 1 : 0)
                   << ",\n  \"handshake_digest_fnv1a64\": \"" << std::hex << std::setw(16) << std::setfill('0') << handshake_digest << std::dec << "\""
                   << ",\n  \"tl_get_size_histogram_bytes\": {";
            for (size_t i = 0; i < get_size_hist.size(); ++i)
                report << (i ? ", " : "") << '"' << (1u << i) << "\": " << get_size_hist[i];
            report << "}\n}\n";
        }
        std::cout << "CPU_FREE cpu_instantiated=false translation=bare_address_pmp_allow memory_latency=" << options.memory_latency << "\n";
        top.final();
    }
};

namespace online_service {
class VerilatedEngine final : public Engine, private Harness {
    static Options options_for(const EngineConfig& c) {
        Options o; o.mode="online";o.memory_image=c.memory_image;o.dump_memory=c.dump_memory;
        o.report=c.report;o.memory_latency=c.memory_latency_cycles;o.a_stall_period=c.a_stall_period;
        o.d_stall_period=c.d_stall_period;o.max_cycles=c.max_cycles; return o;
    }
 public:
    VerilatedEngine(int argc,char**argv,const EngineConfig& cfg,Controller& ctrl,EventLedger& ledger)
      : Harness(argc,argv,options_for(cfg),cfg,ctrl,ledger) {
        if(cfg.base_address!=base) throw std::runtime_error("this frozen engine requires base0x80000000");
    }
    Cycle cycle() const override {return cycles;}
    void inject_arrival(const Arrival& a) override {dispatch_arrival(a);}
    void register_panel(const PanelPlan& p) override {event_ledger.register_panel(p);}
    void register_query(const QueryPlan& p) override {event_ledger.register_query(p);}
    const EventLedger& ledger() const override {return event_ledger;}
    std::string finish_error() const override {return event_ledger.finish_error();}
    bool step() override {
        if(finished) return false;
        drive_controller(); Harness::step();
        if(stop_requested && !pending && !top.io_busy && responses.empty() && transactions.empty() && response_commands.empty() && !top.io_resp_valid) {
            if(next_arrival!=arrivals.size()) throw std::runtime_error("policy requested stop with future arrivals unconsumed");
            if(++quiet_cycles==32) {
                const auto error=event_ledger.finish_error(); if(!error.empty()) throw std::runtime_error(error);
                drain_complete_cycle=cycles-1; checks.push_back("nonblocking_online_drained");
                log("engine_drained");finish();finished=true;return false;
            }
        } else quiet_cycles=0;
        return true;
    }
};
std::unique_ptr<Engine> make_engine(int argc,char**argv,const EngineConfig& config,Controller& controller,EventLedger& ledger) {
    return std::make_unique<VerilatedEngine>(argc,argv,config,controller,ledger);
}
}
