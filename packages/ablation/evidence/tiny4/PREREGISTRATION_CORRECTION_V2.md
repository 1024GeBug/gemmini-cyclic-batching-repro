# SRAM 检查器修正 v2（在变体执行前）

首次基线运行 `tiny4_on_j4_baseline_v1` 的原独立TL/逐元素/内存/完成检查通过，补充SRAM检查报 `baseline overwrite trigger not exercised`。该失败保留。

实际SRAM写trace显示，L2只写cohort实际packed行，`pad_I`屏蔽其余行。首个cohort只有1行，A2仅覆盖slot0；其时live请求在slot1/2，不会受损。原补充检查把“存在live A1”错误等同于“每次都有实际覆盖”。

`check_retention_v2.py` 修正该断言：逐cohort报告live请求，以及`slot < cohort.rows`的实际危险请求；基线应恰好破坏危险请求，其他live请求值保持；下一次C1前的实际快照须确认所有live请求都已restore。变体逐cohort仍必须零A1包络写、live A1逐字节不变、实际后续C1使用。整个tiny还必须至少一次真正危险覆盖，并覆盖两个B半区与slot复用。

原输入、binary、observer、主独立checker、旧审计结果均不改。v2独立重审基线生成新文件，变体新运行使用v2预冻结hash。该修正只排除错误的覆盖前提，没有削弱原功能、完成、生命周期或数据相等检查。
