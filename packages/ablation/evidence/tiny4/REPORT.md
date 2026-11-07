# A1保留 tiny 门槛结果

固定四请求、ON、J4、latency2，无CPU，复用同一RTL模型。两臂的历史C1/A2/C2逐字节、完整16MiB内存、原始TL与真实counter/busy、费用与生命周期检查全部通过；SRAM补充v2检查通过。

| 指标 | 原映射+restore | 高A2+retain |
|---|---:|---:|
| mean最终延迟/cycle | 115910 | 114774.75 |
| 总周期（含reset） | 213040 | 212726 |
| Get/B | 2090368 | 2085888 |
| Put/Ack/B | 22528 | 22528 |
| RoCC命令 | 967 | 897 |
| restore/resume阶段 | 3 | 3 |
| restore/resume阶段累计周期 | 845 | 531 |

3次消费者期间仍有live A1，共5个query实例。2次真正危险覆盖均是q3占用复用slot0、generation2：基线写其56行、896B变值，并在restore后恢复；变体消费者零A1写，全部live A1逐字节保持，之后C1继续产生正确结果。q1/q2还覆盖旋转起点2/12的跨scan依赖。两种自动B半区均实际写入。

L2只装实际cohort packed行；首个cohort1行写slot0时live请求在slot1/2，所以该次没有实际覆盖。首次补充检查错误要求每个active cohort都覆盖而失败，原结果保留；v2修正与逐cohort记录见PREREGISTRATION_CORRECTION_V2.md及独立审计。

高A2还改变bank位置。四个L2阶段周期从13643/11744/13643/11744变为11744/13643/11744/13643，总和均50774。因此tiny平均延迟变化混合了bank位置与restore删除；不能用其报告单一restore收益。双臂总周期差314，恰等于实际restore845与config resume531之差；删去5个896B重读合计4480B和70条mvin。

只读observer加入基线后result.json所有字段与旧v2tiny_on02完全相等，包括握手digest。5个离线证据故障（A1字节、受保护写、A id、resume stride、缺失快照）全部被拒绝。source/inputs/model在运行前后不变。

该结果通过功能与物理保留门槛。静态A id选择属于现有ISA映射；tiny没有建立架构新颖性或完整32请求性能结论。
