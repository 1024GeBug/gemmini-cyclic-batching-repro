// Exercise the official metadata path without changing its auto-tiling algorithm.
#define PRINT_TILE 1
#include "gemmini_host.h"
#include <stdexcept>
#include <string>
void host_emit(uint64_t, uint64_t, unsigned) {}
void host_fence(void) {}
int main(int argc, char** argv) {
    if (argc != 5) return 2;
    tiled_matmul_auto(std::stoull(argv[1]), std::stoull(argv[2]), std::stoull(argv[3]),
        nullptr, nullptr, nullptr, nullptr, 0, 0, 0, 0,
        MVIN_SCALE_IDENTITY, MVIN_SCALE_IDENTITY, 1, NO_ACTIVATION, ACC_SCALE_IDENTITY, 0,
        false, false, false, true, false, 0, std::string(argv[4]) == "OS" ? OS : WS);
    return 3; // PRINT_TILE=1 must exit before instruction emission.
}
