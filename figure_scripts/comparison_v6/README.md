此图包使用scientific-figure-suite 0.9.0的statistical-plots、multipanel-composer、text-layout与export流程。源数据只读，未重跑RTL。

复现：

```powershell
$env:PYTHONPATH=(Resolve-Path 'work/pythondeps').Path
& 'C:\Users\JiapengGuan\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'work/figures/comparison_v6/make_comparison_v6.py'
```

依赖：Python、matplotlib、Pillow、numpy、pypdf；套件目录可用`--suite`参数指定。当前字体使用Windows的Arial与SimHei，路径和哈希记录于`environment.json`。其他平台需提供对应字体或修改字体路径。

脚本先核验四个comparison_v6源文件，再导出PDF/SVG/300dpi PNG。`plotted_values.json`保存精确绘图数值；`source_verification.json`保存数据验证结果；`layout.json`和审计文件保存文字布局、字体嵌入与导出检查。PDF人工视觉核验单独记录于`visual_audit.json`。
