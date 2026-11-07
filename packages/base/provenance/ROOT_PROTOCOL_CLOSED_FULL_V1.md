# 原生完整闭合扫描：新增强对照登记 v1

2026-11-04，root；新增运行前登记。本对照依据独立贡献审查提出，原六策略诊断和源码保持冻结。

原六策略已通过全部独立审计，ON 对 OFF/已登记最优闭合候选平均最终延迟改善11.31%/11.10%。这只说明在共同的两N64封闭组条件下存在收益。官方 Gemmini WS outer 允许第一层完整列扫描连续发出普通宏、最后才 fence；故补测原生完整闭合执行，区分必要的物理容量与软件封闭组限制。

## 固定实现和资源

隔离目录 closed_full_runtime/source，构建 closed_full_v1，二进制SHA256 `22d929567390ddd35def1cb713e83c811cfed5917c9d4e115c35e631893762c9`。CLI c1_group_panels=14 仅允许policy=closed；ON/OFF原有行为保持为2。engine、RTL及原冻结模型不变。独立checker SHA256 `45edaa400d724520fb7d1ed51c3fbad2792daa7404444ef3adcf61ea11eaaab7`。

每batch第一层使用一次公共EX/ST/LD配置，随后14个J4或7个J8宏连续提交。第一宏A=首个query输入DRAM地址，后续宏A=0，A固定id1只读复用，B自动id0；各宏B/C非零且对应当前列。硬件原生双Loop槽的ready背压控制宏进入，完整scan写回量E=3584M，全部普通字接受后真实counter+busy封闭。静态14个输出panel计划用于审计，其数量不表示新增硬件槽。共享3×57344B C1、16输入行、14336B A2与8192B C2；不增加SP/ACC、DMA在途容量或输出buffer。

消费者、RNE量化、最终交付、FCFS、每操作费用、threshold3 timeout52109、内存delay2、输入与32请求原到达全部沿用原协议。单独14panel组使闭合策略减少中间counter/busy栅栏，这是它合法利用完整批次的优势。

## 有限执行

1. 先同一原始前4请求执行 J4 immediate 与 J8 immediate 两个tiny，各一次，max10M cycles。逐历史C1/A2/C2、精确ISA、全部物理计划、真实response/busy、全内存及生命周期独立核验。失败保留并修复，不解释性能。
2. 两tiny通过后冻结源码/输入/checker，再按 J4 immediate、J8 immediate、J4 threshold3、J8 threshold3 顺序各执行一次32请求，max100M cycles。全部通过才解释。
3. 这四项与原四种闭合策略共同选平均最终延迟最低者，平局按固定策略名；不改ON参数。为排除隔离代码回归，同一新二进制默认group2须核对源差异及控制语义与原版本相同；当前diff仅新参数/记录和closed14合法性，不依赖未来输入。
4. ON须仍比OFF和新最强已测closed各改善至少5%，才能进入独立确认。无论结果如何，原六策略保留并报告其有限作用；不得以“两N64计划数”排除原生完整scan。

后续独立确认还需覆盖下游消费强度或输出块数，以及前端费用尺度；具体新增值须在运行前固定。
