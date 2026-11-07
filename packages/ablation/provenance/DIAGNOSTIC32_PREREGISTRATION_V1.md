# 高A2固定映射的 restore / retain 32请求诊断预登记

授权范围：只运行固定原32请求fixture的两臂，ON、J4、immediate、latency2，无A/D外部backpressure；不跑OFF、高A闭合、其他轨迹或held-out。

1. `high_a2_restore`：原冻结driver只把L2 A id1改2，B保留auto；原restore计划、实际live A1 mvin及费用全部保留。
2. `high_a2_retain`：与tiny验证的retain源完全相同；L2 A id2/B auto，消费者后只恢复5条C1 CONFIG，删除A1 mvin；真实live枚举与保留计划沿用restore_plan元数据类别。

两臂使用相同只读SRAM observer、相同主独立checker及SRAM v3。v3仅新增`high_restore`模式：高A2也必须零A1覆盖、所有live A1保持，但允许并验证原restore写回；原数据/TL/counter/busy/生命周期检查不变。其余静态资源与tiny一致，不新增存储或credit。

输入manifest固定为e0e781aba066e8daa17bb43703d9f51786747e057d9bd2a7d9bc7356668ff109。使用原diagnostic_meta.json和arrivals.csv，源、模型、工具链、输入、checker、命令与tiny gate在运行前冻结。两臂每次新目录，保留失败。

首要比较同高A2映射下restore与retain，分离bank位置与删除restore。只读附列原ON及完整J4，按query配对给出五段：arrival→admit、admit→C1、C1→consumer、consumer→A2、A2→delivery，总和须等于最终延迟。报告均值、逐query差、字节、cohort、恢复/配置阶段、active A1物理保留覆盖和实际费用。不同策略时间戳造成的cohort/owner变化如实报告，不固定为预期结果。

若retain仍慢于完整J4，停止此候选held-out。该实验不把静态地址选择改称架构创新，不使用32请求诊断调节参数再报告正向测试。
