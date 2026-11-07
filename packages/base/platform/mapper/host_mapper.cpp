#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <array>
#include "gemmini_host.h"

static std::ofstream trace;
static uint64_t emitted = 0, fences = 0;
static uint64_t loop_ws_commands = 0;
void host_emit(uint64_t rs1, uint64_t rs2, unsigned funct) {
    trace << funct << ",0x" << std::hex << rs1 << ",0x" << rs2 << std::dec << '\n';
    ++emitted;
    loop_ws_commands += funct == k_LOOP_WS;
}
void host_fence(void) { trace << "fence\n"; ++fences; }

static std::array<size_t, 3> official_auto_tiles(const std::filesystem::path& executable,
        size_t M, size_t N, size_t K, const std::string& dataflow) {
    // The official PRINT_TILE path computes and prints the same auto factors,
    // then exits before issuing commands. Run it in a separate native process.
    int descriptors[2];
    if (pipe(descriptors)) throw std::runtime_error("Cannot open tile-probe pipe");
    const std::string m = std::to_string(M), n = std::to_string(N), k = std::to_string(K);
    const pid_t pid = fork();
    if (pid < 0) throw std::runtime_error("Cannot fork tile probe");
    if (pid == 0) {
        close(descriptors[0]);
        dup2(descriptors[1], STDOUT_FILENO);
        close(descriptors[1]);
        execl(executable.c_str(), executable.c_str(), m.c_str(), n.c_str(), k.c_str(), dataflow.c_str(), nullptr);
        _exit(127);
    }
    close(descriptors[1]);
    std::string output;
    char buffer[1024];
    ssize_t count;
    while ((count = read(descriptors[0], buffer, sizeof(buffer))) > 0) output.append(buffer, count);
    close(descriptors[0]);
    int status;
    waitpid(pid, &status, 0);
    std::array<size_t, 3> tiles{};
    if (!WIFEXITED(status) || WEXITSTATUS(status) ||
        sscanf(output.c_str(), "tile_I: %zu\ntile_J: %zu\ntile_K: %zu", &tiles[0], &tiles[1], &tiles[2]) != 3)
        throw std::runtime_error("Official auto tile probe failed: " + output);
    return tiles;
}

int main(int argc, char** argv) {
    try {
        if (argc < 5) throw std::runtime_error("Usage: host_mapper M N K OUTPUT_DIR [--dataflow WS|OS] [--tiles I J K]");
        const size_t M = std::stoull(argv[1]), N = std::stoull(argv[2]), K = std::stoull(argv[3]);
        std::string dataflow_name = "WS";
        std::array<size_t, 3> tiles{};
        bool explicit_tiles = false;
        for (int index = 5; index < argc; ++index) {
            const std::string key = argv[index];
            if (key == "--dataflow" && index + 1 < argc) dataflow_name = argv[++index];
            else if (key == "--tiles" && index + 3 < argc) {
                for (size_t& tile : tiles) tile = std::stoull(argv[++index]);
                explicit_tiles = true;
            } else throw std::runtime_error("Invalid mapper option: " + key);
        }
        if (dataflow_name != "WS" && dataflow_name != "OS") throw std::runtime_error("dataflow must be WS or OS");
        const auto dataflow = dataflow_name == "WS" ? WS : OS;
        if (!explicit_tiles) tiles = official_auto_tiles(std::filesystem::absolute(argv[0]).parent_path() / "tile_probe", M, N, K, dataflow_name);
        for (size_t tile : tiles) if (!tile) throw std::runtime_error("Tile factors must be positive");
        if (!M || !N || !K || M > 1024 || N > 16384 || K > 16384) throw std::runtime_error("Invalid dimensions");
        const std::filesystem::path out = argv[4];
        std::filesystem::create_directories(out);
        constexpr uintptr_t base = 0x80000000u;
        constexpr size_t capacity = 16 * 1024 * 1024;
        const size_t a_offset = 0x1000;
        const size_t b_offset = (a_offset + M * K + 63) & ~size_t(63);
        const size_t c_offset = (b_offset + K * N + 63) & ~size_t(63);
        const size_t end_offset = c_offset + M * N * sizeof(acc_t);
        if (end_offset > capacity) throw std::runtime_error("Matrices exceed harness memory capacity");
        void* mapping = mmap(reinterpret_cast<void*>(base), capacity, PROT_READ | PROT_WRITE,
                             MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
        if (mapping == MAP_FAILED || reinterpret_cast<uintptr_t>(mapping) != base)
            throw std::runtime_error("Could not map free host range at 0x80000000");
        auto* bytes = static_cast<uint8_t*>(mapping);
        auto* A = reinterpret_cast<elem_t*>(bytes + a_offset);
        auto* B = reinterpret_cast<elem_t*>(bytes + b_offset);
        auto* C = reinterpret_cast<acc_t*>(bytes + c_offset);
        uint32_t state = 20261103;
        auto value = [&state]() -> int8_t { state = state * 1664525u + 1013904223u; return int8_t(int((state >> 16) % 5) - 2); };
        for (size_t i = 0; i < M * K; ++i) A[i] = value();
        for (size_t i = 0; i < K * N; ++i) B[i] = value();
        std::vector<int32_t> expected(M * N, 0);
        const auto ref_start = std::chrono::steady_clock::now();
        // Independent i-k-j integer reference; full-width accumulation, no quantization.
        for (size_t i = 0; i < M; ++i) for (size_t k = 0; k < K; ++k)
            for (size_t j = 0; j < N; ++j) expected[i * N + j] += int32_t(A[i * K + k]) * int32_t(B[k * N + j]);
        const auto ref_end = std::chrono::steady_clock::now();
        trace.open(out / "commands.csv");
        if (!trace) throw std::runtime_error("Cannot open trace");
        trace << "funct,rs1,rs2\n";
        const auto map_start = std::chrono::steady_clock::now();
        if (explicit_tiles)
            tiled_matmul(M, N, K, A, B, nullptr, C,
                K, N, N, N, MVIN_SCALE_IDENTITY, MVIN_SCALE_IDENTITY, 1,
                NO_ACTIVATION, ACC_SCALE_IDENTITY, 0, false,
                tiles[0], tiles[1], tiles[2], false, false, true, false, 0, dataflow);
        else
            tiled_matmul_auto(M, N, K, A, B, nullptr, C,
                K, N, N, N, MVIN_SCALE_IDENTITY, MVIN_SCALE_IDENTITY, 1,
                NO_ACTIVATION, ACC_SCALE_IDENTITY, 0,
                false, false, false, true, false, 0, dataflow);
        host_fence();
        trace.close();
        const auto map_end = std::chrono::steady_clock::now();
        std::ofstream memory(out / "memory.bin", std::ios::binary);
        memory.write(reinterpret_cast<const char*>(bytes), capacity);
        std::ofstream gold(out / "expected.bin", std::ios::binary);
        gold.write(reinterpret_cast<const char*>(expected.data()), expected.size() * sizeof(int32_t));
        if (!memory || !gold) throw std::runtime_error("Cannot save memory/reference");
        const double reference_seconds = std::chrono::duration<double>(ref_end - ref_start).count();
        const double mapping_seconds = std::chrono::duration<double>(map_end - map_start).count();
        std::ofstream meta(out / "case.json");
        meta << "{\n  \"M\": " << M << ", \"N\": " << N << ", \"K\": " << K
             << ",\n  \"input_dtype\": \"int8\", \"output_dtype\": \"int32\",\n  \"synthetic_values_seed\": 20261103,\n  \"mapper\": \"official_"
             << (explicit_tiles ? "tiled_matmul" : "tiled_matmul_auto") << "\",\n  \"dataflow\": \"" << dataflow_name
             << "\",\n  \"tile_I\": " << tiles[0] << ", \"tile_J\": " << tiles[1] << ", \"tile_K\": " << tiles[2]
             << ",\n  \"double_buffered\": " << (dataflow == WS ? "true" : "false")
             << ",\n  \"spad_tile_budget_rows\": " << (BANK_NUM * BANK_ROWS / (dataflow == WS ? 2 : 1))
             << ",\n  \"acc_tile_budget_rows\": " << (ACC_ROWS / (dataflow == WS ? 2 : 1))
             << ",\n  \"logical_input_bytes\": " << M * K + K * N
             << ",\n  \"logical_output_bytes\": " << M * N * sizeof(acc_t)
             << ",\n  \"logical_total_bytes\": " << M * K + K * N + M * N * sizeof(acc_t)
             << ",\n  \"full_C\": true,\n  \"memory_base\": " << base
             << ",\n  \"a_offset\": " << a_offset << ", \"b_offset\": " << b_offset << ", \"c_offset\": " << c_offset
             << ",\n  \"output_bytes\": " << expected.size() * 4
             << ",\n  \"emitted_commands\": " << emitted << ", \"loop_ws_commands\": " << loop_ws_commands << ", \"fences\": " << fences
             << ",\n  \"reference_seconds\": " << reference_seconds << ", \"mapping_seconds\": " << mapping_seconds << "\n}\n";
        std::ofstream run(out / "run.json");
        run << "{\n  \"schema_version\": 1,\n  \"mode\": \"trace\",\n  \"trace\": \"commands.csv\",\n  \"memory_image\": \"memory.bin\",\n  \"dump_memory\": \"actual_memory.bin\",\n  \"memory_latency_cycles\": 2,\n  \"max_cycles\": 50000000\n}\n";
        std::cout << "MAPPED M=" << M << " N=" << N << " K=" << K << " commands=" << emitted
                  << " mapping_seconds=" << mapping_seconds << " reference_seconds=" << reference_seconds << "\n";
        munmap(mapping, capacity);
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL " << e.what() << '\n'; return 1; }
}
