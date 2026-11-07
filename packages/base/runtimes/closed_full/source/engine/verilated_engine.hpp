#pragma once
#include "common_api.hpp"
#include <memory>

namespace online_service {
// Reuses the pinned generated VGemmini model. No RTL work occurs in this API.
std::unique_ptr<Engine> make_engine(int argc, char** argv,
    const EngineConfig& config, Controller& controller, EventLedger& ledger);
}
