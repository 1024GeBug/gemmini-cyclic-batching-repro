# 当前两层 Gemmini 在线服务源码复现包

本包从固定 default16 Gemmini RTL 全新生成 Verilator 模型，构建当前 online 和 closed_full 两套控制器。它不读取 Chipyard、不进行 Scala elaboration、不下载工具、不链接历史 `reused_v2` 对象。构建与运行均拒绝覆盖已有目录。当前科学结果仍是单个诊断工作负载，10臂比较的5%门槛失败。

需要 Linux x86-64、Verilator **5.022**、native g++ C++17、GNU make、Perl、Python3.10+。路径不能含空格；默认 `--jobs 2`。工具应由用户预装，建议在清空 conda 变量后以 `PATH=/path/to/verilator-5.022/bin:/usr/bin:/bin` 执行。

```bash
python3 verify_source.py
python3 build.py --profile online --build-dir build/online --jobs 2
python3 run.py --profile online --build-dir build/online --out runs/online_on_tiny \
  --case tiny4 --policy on --tile-j 4
python3 build.py --profile closed_full --build-dir build/closed_full --jobs 2
python3 run.py --profile closed_full --build-dir build/closed_full --out runs/full_j4_tiny \
  --case tiny4 --policy closed --tile-j 4
```

`run.py` 同时执行独立 checker；成功要求 simulation exit0、checker exit0、audit.status=pass、源/输入/二进制/checker哈希保持不变。tiny 执行4请求meta/arrivals，复用完整16MiB输入memory；checker读取完整冻结32请求fixture并核验前4请求。不要把32请求arrival交给4请求meta。

包内共有134个 `.sv/.v` 文件，另含 `init_inputs.h`，共135个固定平台文件。`runtimes/online/source` 对应原六臂实验；`runtimes/closed_full/source`增加仅CLOSED合法的完整14-panel扫描。两套checker分别保留原始字节和独立目录；只依赖Python标准库。SRAM使用生成的同步行为模型。所有第三方许可证位于 `platform/licenses` 与 `platform/mapper/vendor`。

`--case diagnostic32` 支持完整冻结输入。建议先运行tiny；脚本默认不会自动执行十臂实验。本包已完成online ON tiny和full J4 tiny的fresh-RTL构建与独立审计；此次包验证没有新增32请求实验。原online profile可以选择原四种closed候选，full profile可以选择J4/J8 immediate或 `--batch-target 3 --timeout-cycles 52109`。ON/OFF参数固定。

`source_manifest.json`覆盖源、RTL、mapper、fixture与checker；`provenance/export_manifest.json`记录历史来源路径，仅作归档，脚本不会打开这些历史路径。`build/`、`runs/`和`validation/`不属于源码清单。新构建的二进制hash可以不同；功能/cycle/digest一致性须由新增独立审计记录建立。实际构建和tiny证据见 `validation/`。
