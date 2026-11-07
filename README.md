# Gemmini NPU 循环合批复现实验

本仓库提供论文《共享 NPU 的循环合批：准入等待与执行连续性》的硬件仿真与数据处理材料。代码基于 Gemmini，并使用 Verilator 进行无 CPU RTL 仿真，用于复现论文中的控制器、检查器、输入 fixture、冻结结果和图表生成脚本。


## 目录

```text
packages/base/                         Gemmini RTL、控制器、输入 fixture 与基础检查器
packages/ablation/                     A1 保留消融源码、patch、检查器与证据
results/comparison_v6/      固定 32 请求比较结果（CSV/JSON）
results/a_retention_v1/     A1 保留消融的诊断结果
figure_scripts/comparison_v6/            比较图生成脚本、数据和审计元数据
MANIFEST.json                          发布文件清单
SHA256SUMS                             文件哈希
```

`packages/base/` 和 `packages/ablation/` 已在仓库中直接展开，包内 README、构建说明和校验文件随代码保留。

## 环境

- Gemmini RTL 与对应的 Chisel/Verilog 生成结果
- Verilator（建议使用项目原实验环境中的版本）
- Linux/WSL 环境，GNU make、Python 3 和常用文本处理工具
- 运行仿真需要足够的临时磁盘空间；完整模型对象和可执行文件不随仓库发布

仓库中的命令以包内 README 为准。路径、工具版本和参数若与本地环境不同，请记录在复现实验日志中。

## 基础功能验证

基础包支持在无 CPU 的 Verilator RTL 模型上验证 tiny4 功能用例：

1. ON 策略的 tiny4 用例；
2. 完整 J4 策略的 tiny4 用例。

这两项用于确认固定 RTL、控制器、输入和检查器能够闭环运行。基础包没有对 32 请求性能诊断重新进行 fresh RTL 重跑；32 请求表格使用已冻结的实验结果，详见 `results/comparison_v6/summary.json` 和 `policy_summary.csv`。

## 结果摘要

在固定的 32 请求诊断中，端点定义为最终 `C2` 交付：

| 策略 | 平均最终延迟（周期） |
| --- | ---: |
| ON（允许非零输出块准入） | 115,615.625 |
| 完整 J4 immediate（64 列宏、完整扫描、即时启动） | 96,898 |

ON 相对完整 J4 的平均延迟高 19.3%。在共同双输出块分组内，ON 相对 OFF 低 11.3%，相对已测最低延迟闭合候选低 11.1%。这些数字只描述该固定请求序列、模型和配置，不能外推到所有模型或负载。

## 消融边界

A1 保留消融使用冻结 Gemmini Verilator 模型并重新编译控制器后完成。消融包包含 checker、patch、证据和报告；portable fresh-model 链接 recipe 尚未完成独立验证，因此不能将该消融结果表述为 fresh RTL 的完整复现。

## 校验

```text
MANIFEST.json       发布内容清单
SHA256SUMS          除自身外所有发布文件的 SHA-256
```

建议在解压后先核对 SHA-256，再运行包内脚本。原始 TL/SRAM 日志、运行后内存 dump、模型对象和可执行文件未随仓库发布；如检查器依赖这些文件，需要先重新仿真生成。

## 许可与第三方代码

Gemmini 及其依赖遵循各自上游仓库的许可证。本仓库新增脚本和整理文件的许可说明将与发布仓库一并提供；使用时请同时遵守 Gemmini、Verilator 及相关依赖的许可证要求。



