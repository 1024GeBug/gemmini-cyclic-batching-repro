#pragma once
#ifndef ONLINE_SERVICE_COMMON_API_HPP
#define ONLINE_SERVICE_COMMON_API_HPP

// CPU-free online-service protocol shared by the engine and controller.
// This header deliberately contains no Verilator or Gemmini implementation.
// The engine owns cycle advancement and protocol truth; a Controller only sees
// events that have already occurred and returns at most one stable command.

#include <cstdint>
#include <array>
#include <optional>
#include <string>
#include <vector>

namespace online_service {

using Cycle = std::uint64_t;
using QueryId = std::uint32_t;
using Generation = std::uint32_t;
using PanelId = std::uint64_t;
using EpochId = std::uint32_t;
using SlotId = std::uint16_t;
using SourceId = std::uint32_t;
using TransactionId = std::uint64_t;

// An arrival is injected by an independent clock.  The engine passes it to the
// controller only at `arrival.cycle`; the controller has no future-arrival API.
struct Arrival {
  Cycle cycle = 0;
  QueryId query = 0;
  Generation generation = 0;
  std::uint64_t dram_input = 0;
  std::uint32_t n = 0;
  std::uint32_t k = 0;
  std::int32_t desired_slot = -1;  // -1 means policy chooses.
};

// A command payload is immutable while valid && !ready.  `tag` is software
// provenance only and must never change the Gemmini funct/rs1/rs2 payload.
struct Command {
  std::uint32_t funct = 0;
  std::uint64_t rs1 = 0;
  std::uint64_t rs2 = 0;
  std::uint64_t tag = 0;
  std::uint8_t rd = 0;
  bool xd = false;
};

struct RoCCResponse {
  Cycle cycle = 0;
  std::uint8_t rd = 0;
  std::uint64_t data = 0;
  std::uint64_t command_tag = 0;
};

// A real top-level io_busy sample at edge c. Delivered after that edge;
// the controller may use it only at tick c+1 or later, after its paid poll.
struct DeviceStatus {
  Cycle cycle = 0;
  bool busy = true;
};

struct CommandAttempt {
  Cycle cycle = 0;
  Command command{};
  bool valid = false;
  bool ready = false;
  bool accepted = false;
};

enum class AOpcode : std::uint8_t { Get = 4, PutFull = 0, PutPartial = 1, Unknown = 255 };

// One accepted TileLink A beat.  A transaction has one Get A beat or several
// Put data A beats. `beat` is zero based within that accepted transaction.
struct AcceptedA {
  Cycle cycle = 0;
  TransactionId transaction = 0;
  AOpcode opcode = AOpcode::Unknown;
  SourceId source = 0;
  std::uint64_t address = 0;
  std::uint8_t size = 0;       // log2(bytes)
  std::uint16_t mask = 0;
  std::uint32_t beat = 0;
  std::uint32_t expected_beats = 0;
  std::uint32_t source_generation = 0;
  std::array<std::uint32_t, 4> data{}; // low-address byte order, 16B lanes
  bool last_a = false;
  PanelId panel = 0;
  EpochId epoch = 0;
  Generation generation = 0;
};

// D has no address.  The engine must resolve the source to its live
// TransactionId before calling the controller.  `last_d` is true only for the
// final expected D beat/AccessAck of this transaction.
struct AcceptedD {
  Cycle cycle = 0;
  TransactionId transaction = 0;
  AOpcode opcode = AOpcode::Unknown;
  std::uint32_t d_opcode = 0; // raw TL D: AccessAck=0, AccessAckData=1
  SourceId source = 0;
  std::uint8_t size = 0;
  std::uint32_t beat = 0;
  std::uint32_t expected_beats = 0;
  std::uint32_t source_generation = 0;
  bool last_d = false;
  bool denied = false;
  bool corrupt = false;
  std::uint64_t first_a_cycle = 0;
  PanelId panel = 0;
  EpochId epoch = 0;
  Generation generation = 0;
};

struct ByteRange {
  std::uint64_t begin = 0;  // inclusive
  std::uint64_t end = 0;    // exclusive
};

// A panel plan is registered before its first config command. `expected_puts`
// must be static from the dimensions/layout; a panel cannot release merely
// because all currently observed writes have Acked.
struct PanelPlan {
  PanelId panel = 0;
  std::uint32_t physical_panel = 0;
  EpochId epoch = 0;
  Generation generation = 0;
  ByteRange expected_output{};
  std::vector<ByteRange> expected_puts;
  std::vector<QueryId> effective_queries;
  std::uint32_t reserved_credit = 0;
};

struct QueryPlan {
  QueryId query = 0;
  Generation generation = 0;
  SlotId slot = 0;
  PanelId start_panel = 0;
  std::uint32_t panel_count = 0;
  std::vector<ByteRange> effective_output;
  std::vector<ByteRange> descriptor_fragments;
};

struct CommandResult {
  std::optional<Command> command;
  bool request_stop = false;
  // A controller may return a command only after the engine has reported every
  // event from this cycle. The engine calls tick again next cycle. request_stop
  // is advisory: the engine stops only after no future arrival, no pending
  // command, no live source/response, and the RTL has drained.
};

class EventLedger {
 public:
  virtual ~EventLedger() = default;
  virtual void register_panel(const PanelPlan& plan) = 0;
  virtual void register_query(const QueryPlan& plan) = 0;
  virtual void on_arrival(const Arrival& arrival) = 0;
  virtual void on_command_attempt(const CommandAttempt& attempt) = 0;
  virtual void on_command_accepted(const CommandAttempt& accepted) = 0;
  virtual void on_a_accepted(const AcceptedA& event) = 0;
  virtual void on_d_accepted(const AcceptedD& event) = 0;
  virtual bool panel_released(PanelId panel) const = 0;
  virtual bool query_device_ready(QueryId query, Generation generation) const = 0;
  virtual std::string finish_error() const = 0;
};

// Ordering contract per cycle c:
//   1. Engine performs TL and command handshakes at c and records accepted A/D
//      and command events in the ledger.
//   2. Engine injects arrivals stamped c (independent of ready/busy state).
//   3. Controller observes those events only through callbacks and its next
//      tick runs at c+1. It may not use a D/Ack at c to create a command at c.
//   4. A returned command is held stable until accepted; no later tick may
//      replace or reorder it while valid && !ready.
class Controller {
 public:
  virtual ~Controller() = default;
  virtual void on_arrival(const Arrival&) {}
  virtual void on_command_attempt(const CommandAttempt&) {}
  virtual void on_command_accepted(const CommandAttempt&) {}
  virtual void on_a_accepted(const AcceptedA&) {}
  virtual void on_d_accepted(const AcceptedD&) {}
  virtual void on_rocc_response(const RoCCResponse&) {}
  virtual void on_device_status(const DeviceStatus&) {}
  virtual CommandResult tick(Cycle now, const EventLedger& ledger) = 0;
};

struct EngineConfig {
  std::uint64_t base_address = 0x80000000ULL;
  std::uint32_t memory_bytes = 16u * 1024u * 1024u;
  std::uint32_t memory_latency_cycles = 2;
  std::uint32_t logical_panel_bytes = 0;
  std::uint32_t max_panel_credits = 2;
  std::uint32_t max_input_slots = 16;
  bool cpu_free_completion_adapter = true;
  std::uint64_t max_cycles = 1000000;
  std::uint32_t a_stall_period = 0;
  std::uint32_t d_stall_period = 0;
  std::uint32_t response_stall_period = 0;
  std::uint32_t response_stall_length = 0;
  std::string memory_image;
  std::string arrivals_csv;
  std::string event_log;
  std::string dump_memory;
  std::string report;
};

class Engine {
 public:
  virtual ~Engine() = default;
  virtual Cycle cycle() const = 0;
  virtual void inject_arrival(const Arrival& arrival) = 0;
  virtual void register_panel(const PanelPlan& plan) = 0;
  virtual void register_query(const QueryPlan& plan) = 0;
  virtual bool step() = 0;  // false after a clean, fully drained finish
  virtual const EventLedger& ledger() const = 0;
  virtual std::string finish_error() const = 0;
};

}  // namespace online_service

#endif  // ONLINE_SERVICE_COMMON_API_HPP
