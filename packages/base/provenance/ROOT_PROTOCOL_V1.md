# 真实在线两层消费：执行协议 v1

2026-11-04。由 root 在在线实现和性能运行之前登记。用户已授权持续研究与必要仿真；本文件的 root 批准是内部任务协调，不是向用户新增审批要求。

## 问题与固定输入

检验在真实 RoCC 完成接口、有限缓冲和同一 Gemmini 两层消费下，中途准入是否改善最终响应。算子为 INT8 A1×W1 → INT32 C1 → RNE(C1/16)、饱和到 INT8 A2 → A2×W2 → INT32 C2。K=N1=896、N2=128；本模型是共享两线性层服务点。使用 `../online_inputs/diagnostic_q896_n128_v1` 原始 32 请求和独立到达，不改数据、seed 或到达。该轨迹用于诊断，后续独立轨迹用于验证。

只使用现有 ISA 和无 CPU Gemmini RTL。`../dependent/engine_v2` 的真实 response 与 actual-busy 接口已通过独立功能审计，允许隔离复用；原有源、二进制和结果保持冻结。观察器和 TL 日志只进入独立验证，不反馈运行控制。

## 策略和公共资源

- ON/OFF 共用同一个驱动，唯一准入开关 `ON || physical_panel==0`。在上一封闭组的真实 counter+busy 完成后准入；ON 可以从非零输出 N64 panel 开始。每个 query 覆盖恰好14个不同 panel。每组至多两个 panel，在首个 query 完整或 scan 尾部提前封组。相同最高 live slot+1 活动行，洞仍产生实际物理输出。
- CLOSED 的有限候选为 J4/J8 × immediate/threshold3，threshold timeout 固定52109周期。FCFS、最大16请求，整个 C1 batch 完成后共同消费；允许普通 compact batch、A 重用和 J8 双 credit。四候选全报告，不能根据结果选择弱对照。诊断均值最小者按策略名打破平局，成为之后独立验证的固定 CLOSED；ON 参数不据此改变。
- 公共 SP、ACC、3个 C1 epoch、2个 N64 credit、最多16行的单份 A2/C2，地址采用已冻结输入。J8 同时预约两个 credit。C1 片段保留到真实 A2 完成；A2 保留到 L2 读完；C2 保留到该 cohort 全部 delivery 结束且通过 busy gate。代际不可覆盖未释放引用。
- 任意 C1 组完成后，立即消费全部已知 ready query，每 cohort 至多16个，不主动等批。consumer 运行期间不发 L1。实际 INT32 reload → ACC、scale1/16 INT8 store → A2、公共 J8 L2 → C2。之后若仍有 live L1 query，实际重载其 A1；这些 DMA、命令、等待均计入。

## 真实完成和费用

完整组静态登记物理 expected Ack bytes E；所有命令接受后读取真实写 Ack counter，要求 uint32(v−baseline)==E，不允许按前缀阈值宣布某 query 完成。大于 E、denied/corrupt、地址/数据错误均失败。下一设备阶段前，按同一费用观察实际 io_busy 为 false。零写 load/restore 组也执行 counter 与 busy gate。

共同声明 CPU 费用：poll64、decision16、issue1、delivery64 周期；真实 command/response stall 另由时钟自然计入。metadata 每次 scheduler 决策、每 panel owner snapshot、每 query bind/descriptor 构造或释放、每 cohort 映射按16周期付费。逐 chunk 地址生成并入对应 issue1，不另假称实测 CPU 性能。实现若需要额外类别，须在运行前记录类别和统一成本。各 CPU 区间互不重叠；日志逐类记录。

C2 的 counter equality 经 paid decision 后产生合法软件通知，随后按固定 cohort 顺序每 query delivery64，再付 busy gate，才允许设备/缓冲复用。C2 历史输出由只读证据层从真实 TL 写重建，不能为 controller 触发完成。实际全部 query C1、A2、C2 及完整16MiB内存独立核验。

## 有限执行与裁决

1. 隔离实现、编译和纯软件自检已授权。先注册使用原输入前4 query 的 derived tiny；必要的 J8 M1/M>1 数值 smoke 最多两个固定案例。root 统一授权 tiny 后执行，失败完整保留并给出具体修正；正常修复不要求用户审批。
2. tiny 先核验 ON/OFF、CLOSED J8 和量化/下一层/A恢复。generation 复用等未在 tiny 触发的覆盖由32请求诊断承担，覆盖结果如实记录，不借此反复扩大 tiny。
3. 编译产物、输入、运行参数和独立 checker 在32请求结果前固定，所有六策略各运行一次。memory2、无额外 A/D/response stall，最长仿真预算100,000,000周期。策略失败不解释其性能。诊断执行顺序 ON、OFF、CLOSED J4 immediate、J8 immediate、J4 threshold3、J8 threshold3。
4. 主要响应量为外部 arrival→C2 delivery_end。报告全部配对值、mean/max、总完成、流量、cohort大小、轮询与恢复成本；不由32个相关请求推断 p99 或置信区间。
5. ON 均值须比 OFF 和最强 CLOSED 各降低至少5%，且全部功能/生命周期验证通过，才进入独立负载确认；若只改善部分条件，明确给出条件与实测成本。未达门槛则保留负面结果，停止把此候选当成论文主贡献，不通过取消消费或削弱 CLOSED 挽救。
6. 通过后固定代码，以2个独立seed×2种到达率×2种内存条件验证，具体seed/rate/memory在首次验证前登记。根据因果结果和先行差异决定成稿；研究进度不等同 DAC 录用概率。

对照设计依据为 `../ONLINE_RUNTIME_IMPLEMENTATION_DESIGN.md`、`../RESEARCH_ENDPOINT_DECISION.md` 和 `../CLOSED_J8_GROUP_FEASIBILITY.md`。本协议只固定实验决策，不产生新性能结果。
