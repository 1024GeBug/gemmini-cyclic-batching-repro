#ifndef CODEX_GEMMINI_HOST_ROCC_H
#define CODEX_GEMMINI_HOST_ROCC_H
#include <stdint.h>
void host_emit(uint64_t rs1, uint64_t rs2, unsigned funct);
void host_fence(void);
#define ROCC_INSTRUCTION_0_R_R(x, rs1, rs2, funct) do { host_emit((uint64_t)(rs1), (uint64_t)(rs2), (funct)); } while (0);
#define ROCC_INSTRUCTION(x, rd, rs1, rs2, funct) do { host_emit((uint64_t)(rs1), (uint64_t)(rs2), (funct)); (rd) = 0; } while (0);
#endif
