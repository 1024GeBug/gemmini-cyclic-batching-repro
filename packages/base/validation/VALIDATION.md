# 当前 online 与 closed_full 的 fresh-RTL 复现验证

两套当前 runtime 已在搬迁后的独立目录从固定 Gemmini RTL 完整构建，且各一项4请求 tiny 均通过独立审计。使用 Verilator 5.022、Ubuntu native g++11.4.0、make4.3，顺序构建、每次-j2。没有链接历史reused_v2对象，没有实例化CPU，没有新增32请求实验。

| profile | fresh binary SHA256 | run/audit | drain cycle |
|---|---|---|---:|
| online | `85fd129559a2cdf3fbc9693270414f34627500890b485c549b34a2e614f78add` | 0/0 PASS | 213040 |
| closed_full | `2c35cedce289e690cec4e16f8dd3ac813225f5a03bcbf37ed61de7640daa3623` | 0/0 PASS | 207343 |

两次独立生成的 `VGemmini__ALL.a` 均为3,926,412B、SHA256 `01120eb3eb8cff12f13596e266fcf74921008b63408ec69781f7d888107730f2`，与历史归档 `4fe4918a...`不同；root header保持 `4d896e4a...`，并包含当前engine所需真实WDMA/counter观察字段。

两项tiny的engine/driver日志、16MiB最终内存、历史C1/A2/C2、query JSON/CSV和result.json均与对应冻结tiny逐字节相同。full J4的runtime_config也相同；ON的batch_target为1，旧tiny默认16，这是CLOSED专用且ON不使用的字段，因而不声称所有配置文件相同。

完整实执行命令见 `validation_registration.json`；fresh Verilator构建argv、134项RTL哈希、工具/源和148项生成文件哈希见各build_manifest.json。每次执行前后的source、binary、input、checker哈希一致，checker分别为84f209d4…/45edaa40…，fixture manifest为e0e781ab…。

构建时source manifest：`153c1428c14623fe9f18798ea9137ab161ad39112b473153687e3b69dcc0dece`；发布时：`ea601904855daeca599a92fd79f3c23ace3769b81457f46502c8f45b97368b42`。唯一构建后清单内变更为README使用说明，全部代码、RTL、mapper、input、checker与已验证构建逐字节相同。原构建清单已保留为 `validated_build_source_manifest.json`。

实际目录：`/home/Gemguan/work/codex_gemmini_paper_20261103/consumer_gate/repro_v6_check_v1`。大对象和完整日志保留在该WSL目录，包内只放哈希及小型报告。包内完整fixture为输入16MiB memory，并非运行后的memory dump。

复现命令（Linux，先把独立Verilator5.022置于PATH）：

```bash
python3 verify_source.py
python3 build.py --profile online --build-dir build/online --jobs 2
python3 run.py --profile online --build-dir build/online --out runs/online_on_tiny --case tiny4 --policy on --tile-j 4
python3 build.py --profile closed_full --build-dir build/closed_full --jobs 2
python3 run.py --profile closed_full --build-dir build/closed_full --out runs/full_j4_tiny --case tiny4 --policy closed --tile-j 4
```

runner支持冻结32请求配置，默认不自动执行十臂。此次portable validation只验证两个tiny路径；J8/threshold3及完整10臂的fresh-model重跑没有新增执行。原10臂独立审计和门槛失败结论保持原样。
