#pragma once
#include "engine/common_api.hpp"
#include <functional>
#include <memory>
#include <string>

namespace online_service {
struct DriverConfig {
  unsigned n=128, k=64, query_count=8;
  std::uint64_t input_base=0, weight_base=0, output_base=0;
  std::uint64_t weight2_base=0, a2_base=0, c2_base=0;
  std::uint64_t output_budget_bytes=0;
  std::string policy="cyclic"; // cyclic or closed
  unsigned tile_j=1, batch_target=16;
  Cycle timeout_cycles=0;
  Cycle poll_cycles=64, decision_cycles=16, issue_cycles=1, sink_cycles=64;
};
using EmitJSON=std::function<void(const std::string&)>;
struct DriverBundle {
  std::unique_ptr<EventLedger> ledger;
  std::unique_ptr<Controller> controller;
};
DriverBundle create_driver(const DriverConfig&,EmitJSON);
// Saves captured valid output bytes (query-id order), readiness and delivery
// times, and controller statistics; never reads the private arrival file.
void save_driver_outputs(const DriverBundle&,const std::string& directory);
}
