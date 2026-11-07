# A1 保留最小变体：静态审计与 tiny 预登记

日期：2026-11-04。仅运行固定四请求、ON、J4 的基线与保留变体，不运行 32 请求或参数扫描。二者复用同一冻结 Verilated Gemmini 模型；重编三个 C++ 翻译单元，不修改 RTL。

## 改动和待检验命题

基线是 `controller_v2_tiny02` 的完整冻结 source。两臂加入相同的只读 observer：在时钟边沿前记录四个 SRAM bank 的写地址及 mask，并在真实 RoCC counter response 时读出低端 896 行。observer 不向 controller 反馈。

变体只将消费者 L2 的 `a_spad_id=1` 改为 2，`b_spad_id=0` 保持原生自动双缓冲。L2 后枚举 live C1 请求并恢复 EX、ST、三个 LD 配置，不再 mvin A1。该真实保留计划沿用已有 `restore_plan` 元数据费用类别，并记为 `retention_plan` 事件；`resume_c1_config` 是真实五条 CONFIG 的零数据阶段，按原 counter 及 busy 条件封闭。删除的 A1 mvin 没有命令费用、Get 字节或设备执行周期。

检验命题：消费者执行期间仍存活且后续继续 C1 的 A1，可保持原值并得到逐元素正确的 C1/A2/C2。静态地址映射是 ISA 现有能力，不作为新架构贡献。tiny 周期仅诊断，不推断 32 请求性能。

## SP 地址与容量

配置 DIM=16、4 bank、4096 行/bank；每行16B，总256KiB。手工 mvin 地址为 `k+slot+16b`，`k=0,64,...,832`、`b=0..3`。因此16槽 A1 的完整包络为 `[0,896)`，不是851行。

| 对象 | 半开行区间 | 生命周期 |
|---|---|---|
| A1，id1 | [0,896) | 从该slot装入到该请求全部14个输出面板counter/Ack及busy封闭 |
| A2，id2 | [8192,9088) | 消费者L2期间；不覆盖A1 |
| J8 B auto低 | [1024,8192) | 每个Loop重新从非零权重DRAM指针加载 |
| J8 B auto高 | [9216,16384) | 同上 |
| J4 B auto低/高 | [4608,8192) / [12800,16384) | 同上 |

两A与两J8 B共16128行=252KiB，余4KiB。A2与高B共享bank2，地址不相交不能消除端口争用。对应源为 `LoopMatmul.scala:1125–1149,1168–1171`、`gemmini.h:366–374`。

## ACC、配置和完成依赖

ACC 1024行。C1、L2宏的ACC输出半区仍原生交替；consumer reload使用 `row*56+t`，最大896行。本变体保持所有阶段顺序，reload/量化/L2/C1不重叠，所以不增加ACC空间或所有权假设。3份C1 DRAM epoch合计172032B、单份16行A2/C2缓冲、16 input slot、2个软件N64 credit均不变。

消费者先等C1外部写Ack、counter等式及真实busy=false，再重载C1；A2完成后再L2。保留A1只删除重复读入，不更改任何C1/A2/C2完成、输入释放、fragment引用或slot generation条件。L2之后恢复C1全部EX/ST/LD配置，避免量化scale、stride与第二层配置泄漏。

SRAM端口观察依据 `Scratchpad.scala:113,119–126,163–184`：单端口读`ren`与写`io.write.fire`互斥，读写复用SRAM使能；在边沿前`RW0_en && !ren`观察写地址/mask。独立逐字节SRAM快照另行验证实际输入值，端口日志不作为调度完成条件。

## 预先规定的检查

1. 每臂均运行既有独立原始TL/完整16MiB内存/逐字节历史C1、A2、C2/counter真实response/busy/费用/生命周期审计。仅增加零读零写`resume_c1_config`阶段；原检查不放宽。
2. 使用接受的LoopWS字段核验所有C1为A id1/B auto，L2基线id1、变体id2/B auto；核验零数据resume只包含5条原C1配置。
3. 至少一次消费者期间存在live A1，并在后续C1被使用；逐次报告活跃query、slot、generation、是否跨scan轮、写端口覆盖、前后896B输入逐字节比较。空覆盖不算通过。
4. 变体消费者期间不得写入任何低端A1行；基线应观察A1覆盖并在实际restore后恢复。变体不得出现restore_inputs或任何consumer后的A1重读。
5. 四请求必须覆盖slot复用、旋转准入，并检查L2使用两个自动B物理半区。若未覆盖，报告缺口，另建定向tiny，不把缺失覆盖写成PASS。
6. 原输入manifest、meta、arrivals、源码、checker、模型、binary与运行命令在执行前冻结；完成后记录不变性与输出hash。保留所有失败与旧目录。
