# A1保留消融：附加源码与小证据包

本包与外层实验包中的 `repro_v6_source_package.zip` 配合使用。它保存已完成的tiny和固定32请求输入保留消融，不包含新的研究、构建或仿真。原始完整TL/SRAM日志、内存dump、模型对象和binary均不在此小包内。

## 已完成证据

固定32请求，ON、J4、latency2。相同高A2映射下，保留A1将mean最终延迟从116342.28125降至115914.4375周期（0.368%），Get减少30464B；完整J4为96898周期，保留方案仍慢19.625%，按预登记停止本候选held-out。两臂逐字节历史C1/A2/C2、完整16MiB内存、原始TL、真实counter/busy、费用、生命周期及SRAM检查全部PASS。

32请求SRAM覆盖22次active消费者、34个live query实例，其中10次具有实际覆盖危险；两个B自动半区与slot复用均覆盖。`evidence/diagnostic32/`含五区间、逐query配对CSV、summary、报告与审计。`evidence/tiny4/`含tiny证据、5个故障拒绝结果及首次补充检查失败和v2修正；首次失败未删除。

实际实验采用冻结Gemmini Verilator模型，完整重编driver.cpp、online_main.cpp、engine/verilated_engine.cpp后链接模型对象；没有重新生成RTL。历史编译器、模型、source、binary hash及实际命令记录在各build_manifest、预登记与postregistration中。它们属于controller-recompile实验记录。

## 精确源码

| 目录 | 含义与实际运行 |
|---|---|
| runtimes/baseline/source | 原ON的完整6源，逐字节等于companion repro_v6的online/source；作为原32请求比较源 |
| runtimes/baseline_observed/source | baseline只加只读SRAM observer；已运行4请求，result.json全部字段与原tiny一致 |
| runtimes/high_restore/source | L2 A id2、B auto，保留实际A1 restore；已运行32请求 |
| runtimes/high_retain/source | 相同映射，删除A1 mvin，保留真实配置恢复；已运行4及32请求 |

`patches/`为上述source树之间的相对补丁，完整6源是主要复现输入。`provenance/source_origins.json`记录每个文件来源和hash。`checkers/check_v2.py`为原独立协议检查器，只增加零读零写resume阶段；`check_retention_v2.py`用于tiny修正审计，v3增加高A2且保留restore的检查模式。

## 在companion fresh模型上编译

Linux x86-64、Python3.10+、native g++ C++17、同一Verilator5.022 include。先按companion README从其固定RTL生成online fresh模型。若外层工作目录已有这套成功fresh build，可直接复用其 `build_manifest.json`和`obj/`。本包不携带模型对象，便携recipe没有机器专用默认路径。

以下仅为未执行的复现命令示例。设两包分别解压到同一目录下的 `repro_v6/`与`ablation/`，且companion fresh online模型已构建为 `repro_v6/build/online/`：

```bash
python3 ablation/verify_package.py
python3 ablation/recipes/build_against_fresh.py \
  --arm high_retain --repro-root repro_v6 \
  --fresh-build repro_v6/build/online \
  --verilator-include /path/to/verilator-5.022/share/verilator/include \
  --out ablation_build/high_retain
```

将`--arm`改为`high_restore`、`baseline`或`baseline_observed`并使用不同新out目录即可编译其他源。recipe核验companion当前源清单、fresh manifest记录的134个RTL与原controller字节、模型输入hash及新增SRAM observer符号，然后只编译3个controller翻译单元并链接该fresh目录的模型archive和Verilator runtime对象。README等归档文本可能在fresh build后修订，recipe分别记录新旧source manifest并逐项核验模型相关源。

**此附加包的fresh-model便携链接recipe尚未执行验证。** companion自身完成的fresh-RTL/tiny验证不等于本消融变体已经通过fresh链接验证；本消融的实测证据仍以上述冻结模型controller重编为准。新machine/toolchain可产生不同binary hash，只有后续实际执行与独立审计才能建立一致性。

运行时使用companion冻结fixture，tiny使用本包4请求meta/arrivals：

```bash
ablation_build/high_retain/online_service \
  --meta ablation/inputs/tiny4/meta.json \
  --arrivals ablation/inputs/tiny4/arrivals.csv \
  --memory-image repro_v6/inputs/diagnostic_q896_n128_v1/memory.bin \
  --output-dir ablation_runs/high_retain_tiny \
  --policy on --tile-j 4 --batch-target 1 --timeout-cycles 0 \
  --memory-latency 2 --max-cycles 10000000
python3 ablation/checkers/check_v2.py \
  --input-dir repro_v6/inputs/diagnostic_q896_n128_v1 \
  --run-dir ablation_runs/high_retain_tiny --queries 4 \
  --out ablation_runs/high_retain_tiny/protocol_audit.json
python3 ablation/checkers/check_retention_v3.py \
  --input-dir repro_v6/inputs/diagnostic_q896_n128_v1 \
  --run-dir ablation_runs/high_retain_tiny --mode retain \
  --out ablation_runs/high_retain_tiny/SRAM_audit.json
```

这些命令未在打包阶段执行。若复现冻结32请求，将meta改为`ablation/inputs/diagnostic_meta.json`、arrivals改为companion fixture的arrivals.csv、queries改32、max-cycles改100000000；其它策略和费用不变。high_restore的SRAM mode为`high_restore`，baseline_observed为`baseline`。原baseline没有新增SRAM日志，只运行主protocol checker。

## 清单与归档范围

`MANIFEST.json`与`SHA256SUMS`覆盖本包实际文件。`verify_package.py`只读核验，不构建、不运行。`provenance/original_full_diagnostic_evidence_manifest.json`以及tiny历史manifest记录原完整证据集合，部分指向主动排除的大日志与dump；它们是历史索引，不代表这些大文件已随本小包发布。小证据无法替代原始日志重跑checker；审计报告及原hash已保存，需要独立重验时按源重新运行。

历史build/launch脚本保存在`provenance/historical_recipes/`，其中原绝对路径只用于说明实际运行来源，不是便携入口。执行入口为`recipes/build_against_fresh.py`与`verify_package.py`，不依赖历史模型路径。第三方RTL/mapper及许可证由companion包提供。
