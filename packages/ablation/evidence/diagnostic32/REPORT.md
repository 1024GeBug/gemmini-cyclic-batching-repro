# 高A2的restore/retain固定32请求诊断

两臂原始TL、历史C1/A2/C2逐字节、完整16MiB内存、真实counter/busy和生命周期检查及SRAM补充检查均PASS；运行前后源码、输入、checker、模型和binary均不变。

| 策略 | mean最终延迟/cycle | Get/B | Put/Ack/B | cohorts | restore/resume |
|---|---:|---:|---:|---:|---:|
| original_on | 115615.625 | 16602880 | 178176 | 29 | 22/0 |
| high_a2_restore | 116342.28125 | 16602880 | 178176 | 29 | 22/0 |
| high_a2_retain | 115914.4375 | 16572416 | 178176 | 29 | 0/22 |
| closed_full_j4 | 96898 | 22192128 | 159744 | 24 | 0/0 |

五段均值：

| 策略 | arrival→admit | admit→C1 | C1→consumer | consumer→A2 | A2→delivery |
|---|---:|---:|---:|---:|---:|
| original_on | 8311.1875 | 93449.75 | 164.5 | 1037.5 | 12652.6875 |
| high_a2_restore | 8797.46875 | 93687.125 | 164.5 | 1037.5 | 12655.6875 |
| high_a2_retain | 8504.6875 | 93552.0625 | 164.5 | 1037.5 | 12655.6875 |
| closed_full_j4 | 28251.0625 | 54536 | 173.5 | 1310.5 | 12626.9375 |

`query_five_intervals.csv`保存逐query绝对值，`query_paired_deltas.csv`保存retain相对其他三策略的配对差。配对差为同一到达query的描述性时间差，不代表独立实验样本。

比较：

- retain 相对 original_on：均值差298.8125周期（0.258%）；逐query更快/相同/更慢=23/0/9。
- retain 相对 high_a2_restore：均值差-427.84375周期（-0.368%）；逐query更快/相同/更慢=29/1/2。
- retain 相对 closed_full_j4：均值差19016.4375周期（19.625%）；逐query更快/相同/更慢=6/0/26。

决策：retain仍慢于完整J4，按预登记停止此候选held-out。

相同高A2映射的对照分离了bank位置选择与删除restore，但调度时间改变可使owner/cohort和后续成本改变，应按完整轨迹结果解释。静态映射仍是现有ISA能力。源码patch、预登记、完成记录和完整压缩原始证据随包保存。
