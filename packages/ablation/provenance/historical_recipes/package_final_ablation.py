"""Package only completed ablation sources and compact evidence; never build/run."""
from pathlib import Path
import ast,datetime,difflib,hashlib,json,shutil,zipfile

ROOT=Path(__file__).resolve().parent
WORK=ROOT.parents[1]
STAGE=WORK/'staging/final_ablation'
PKG=STAGE/'ablation'
REPRO=ROOT.parent/'repro_v6'
WSL=Path('/home/Gemguan/work/codex_gemmini_paper_20261103/consumer_gate')

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def tree(p):return {str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()}
def cp(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    assert sha(src)==sha(dst)
def compact(src,dst):
    for p in sorted(src.rglob('*')):
        if p.is_file() and p.suffix.lower() in {'.json','.md','.csv','.log','.patch'}:
            assert p.stat().st_size<2*1024*1024
            cp(p,dst/p.relative_to(src))
def patch(before,after,path):
    out=[]
    for rel in sorted(set(tree(before))|set(tree(after))):
        a=before/rel;b=after/rel
        out+=difflib.unified_diff(a.read_text().splitlines(True),b.read_text().splitlines(True),
          fromfile='a/source/'+rel,tofile='b/source/'+rel)
    path.parent.mkdir(exist_ok=True,parents=True);path.write_text(''.join(out))

def main():
    assert not STAGE.exists(),'exclusive staging directory'
    STAGE.mkdir(parents=True);PKG.mkdir()
    assert tree(ROOT/'base_source')==tree(REPRO/'runtimes/online/source'),'original six-source origin mismatch'
    origins={}
    for arm,src in [('baseline',ROOT/'base_source'),('baseline_observed',ROOT/'baseline/source'),
                    ('high_restore',ROOT/'diagnostic32_v1/high_a2_restore/source'),
                    ('high_retain',ROOT/'diagnostic32_v1/high_a2_retain/source')]:
        files=tree(src);assert len(files)==6
        shutil.copytree(src,PKG/'runtimes'/arm/'source')
        assert tree(PKG/'runtimes'/arm/'source')==files
        origins[arm]={'historical_origin':str(src),'files':files}
    origins['baseline']['same_bytes_as_companion']='repro_v6/runtimes/online/source (six files)'
    origins['baseline']['execution_scope']='original 32-query ON comparator; original engine without added SRAM observer'
    origins['baseline_observed']['execution_scope']='new 4-query baseline; SRAM observer added; result.json exactly equals original tiny baseline'
    origins['high_restore']['execution_scope']='32-query high A2, real restores retained, same added SRAM observer as retain'
    origins['high_retain']['execution_scope']='4- and 32-query high A2, real restores removed; configuration-only resume'
    (PKG/'provenance').mkdir()
    (PKG/'provenance/source_origins.json').write_text(json.dumps(origins,indent=2)+'\n')
    patch(PKG/'runtimes/baseline/source',PKG/'runtimes/baseline_observed/source',PKG/'patches/observer_only.patch')
    patch(PKG/'runtimes/baseline_observed/source',PKG/'runtimes/high_restore/source',PKG/'patches/high_restore_vs_observed_baseline.patch')
    patch(PKG/'runtimes/high_restore/source',PKG/'runtimes/high_retain/source',PKG/'patches/retain_vs_high_restore.patch')
    for name in ['check_v2.py','raw_tl.py','check_retention.py','check_retention_v2.py','check_retention_v3.py','test_retention_faults.py']:
        cp(ROOT/'audit'/name,PKG/'checkers'/name)
    for name in ['STATIC_AUDIT_AND_PREREGISTRATION.md','PREREGISTRATION_CORRECTION_V2.md','DIAGNOSTIC32_PREREGISTRATION_V1.md',
                 'source_freeze.json','fault_checks_v1.json','fault_checks_preregistration_v1.json']:
        cp(ROOT/name,PKG/'provenance'/name)
    compact(ROOT/'tiny_evidence_v1',PKG/'evidence/tiny4')
    diagnostic=ROOT/'diagnostic32_evidence_v1'
    # Select the compact evidence only, preserving original full evidence manifest separately.
    for name in ['REPORT.md','summary.json','query_five_intervals.csv','query_paired_deltas.csv',
                 'preregistration.json','postregistration.json','execution_summary.json']:
        cp(diagnostic/name,PKG/'evidence/diagnostic32'/name)
    cp(diagnostic/'manifest.json',PKG/'provenance/original_full_diagnostic_evidence_manifest.json')
    for name in ['high_a2_restore','high_a2_retain']:compact(diagnostic/name,PKG/'evidence/diagnostic32'/name)
    for name in ['source_freeze.json','meta.json','PREREGISTRATION.md','high_a2_restore.patch','high_a2_retain.patch']:
        cp(ROOT/'diagnostic32_v1'/name,PKG/'provenance/diagnostic32'/name)
    for arm,src in [('original_on',WSL/'online_runtime/diagnostic_v2_01/on'),
                    ('closed_full_j4',WSL/'closed_full_runtime/diagnostic_full_v1/closed_full_j4_immediate')]:
        for name in ['result.json','query_results.json','query_times.csv','runtime_config.json','audit.json']:
            cp(src/name,PKG/'evidence/comparators'/arm/name)
    cp(ROOT/'tiny_inputs/meta.json',PKG/'inputs/tiny4/meta.json')
    cp(ROOT/'tiny_inputs/arrivals.csv',PKG/'inputs/tiny4/arrivals.csv')
    cp(ROOT/'diagnostic32_v1/meta.json',PKG/'inputs/diagnostic_meta.json')
    # Preserve historical build/launch recipes as provenance, not portable commands.
    for name,src in [('controller_build_historical.py',ROOT/'baseline/build.py'),('run_tiny_historical.py',ROOT/'run_tiny.py'),
      ('run_tiny_v2_historical.py',ROOT/'run_tiny_v2.py'),('run_diagnostic32_historical.py',ROOT/'run_diagnostic32.py'),
      ('summarize32_historical.py',ROOT/'summarize32.py'),('package_final_ablation.py',Path(__file__))]:
        cp(src,PKG/'provenance/historical_recipes'/name)
    cp(ROOT/'build_against_fresh.py',PKG/'recipes/build_against_fresh.py')
    companion=WORK/'staging/repro_v6/ZIP_VERIFICATION.json'
    cp(companion,PKG/'provenance/companion_repro_v6_zip_verification.json')
    (PKG/'README.md').write_text('''# A1保留消融：附加源码与小证据包

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
python3 ablation/recipes/build_against_fresh.py \\
  --arm high_retain --repro-root repro_v6 \\
  --fresh-build repro_v6/build/online \\
  --verilator-include /path/to/verilator-5.022/share/verilator/include \\
  --out ablation_build/high_retain
```

将`--arm`改为`high_restore`、`baseline`或`baseline_observed`并使用不同新out目录即可编译其他源。recipe核验companion当前源清单、fresh manifest记录的134个RTL与原controller字节、模型输入hash及新增SRAM observer符号，然后只编译3个controller翻译单元并链接该fresh目录的模型archive和Verilator runtime对象。README等归档文本可能在fresh build后修订，recipe分别记录新旧source manifest并逐项核验模型相关源。

**此附加包的fresh-model便携链接recipe尚未执行验证。** companion自身完成的fresh-RTL/tiny验证不等于本消融变体已经通过fresh链接验证；本消融的实测证据仍以上述冻结模型controller重编为准。新machine/toolchain可产生不同binary hash，只有后续实际执行与独立审计才能建立一致性。

运行时使用companion冻结fixture，tiny使用本包4请求meta/arrivals：

```bash
ablation_build/high_retain/online_service \\
  --meta ablation/inputs/tiny4/meta.json \\
  --arrivals ablation/inputs/tiny4/arrivals.csv \\
  --memory-image repro_v6/inputs/diagnostic_q896_n128_v1/memory.bin \\
  --output-dir ablation_runs/high_retain_tiny \\
  --policy on --tile-j 4 --batch-target 1 --timeout-cycles 0 \\
  --memory-latency 2 --max-cycles 10000000
python3 ablation/checkers/check_v2.py \\
  --input-dir repro_v6/inputs/diagnostic_q896_n128_v1 \\
  --run-dir ablation_runs/high_retain_tiny --queries 4 \\
  --out ablation_runs/high_retain_tiny/protocol_audit.json
python3 ablation/checkers/check_retention_v3.py \\
  --input-dir repro_v6/inputs/diagnostic_q896_n128_v1 \\
  --run-dir ablation_runs/high_retain_tiny --mode retain \\
  --out ablation_runs/high_retain_tiny/SRAM_audit.json
```

这些命令未在打包阶段执行。若复现冻结32请求，将meta改为`ablation/inputs/diagnostic_meta.json`、arrivals改为companion fixture的arrivals.csv、queries改32、max-cycles改100000000；其它策略和费用不变。high_restore的SRAM mode为`high_restore`，baseline_observed为`baseline`。原baseline没有新增SRAM日志，只运行主protocol checker。

## 清单与归档范围

`MANIFEST.json`与`SHA256SUMS`覆盖本包实际文件。`verify_package.py`只读核验，不构建、不运行。`provenance/original_full_diagnostic_evidence_manifest.json`以及tiny历史manifest记录原完整证据集合，部分指向主动排除的大日志与dump；它们是历史索引，不代表这些大文件已随本小包发布。小证据无法替代原始日志重跑checker；审计报告及原hash已保存，需要独立重验时按源重新运行。

历史build/launch脚本保存在`provenance/historical_recipes/`，其中原绝对路径只用于说明实际运行来源，不是便携入口。执行入口为`recipes/build_against_fresh.py`与`verify_package.py`，不依赖历史模型路径。第三方RTL/mapper及许可证由companion包提供。
''',encoding='utf-8')
    verify='''"""Read-only byte/hash verification; no build or simulation."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    m=json.loads((ROOT/'MANIFEST.json').read_text())
    for r in m['files']:
        p=ROOT/r['path']
        if not p.is_file() or p.stat().st_size!=r['bytes'] or sha(p)!=r['sha256']:
            raise RuntimeError('package file mismatch: '+r['path'])
    print(json.dumps({'status':'pass','files':len(m['files']),'bytes':sum(r['bytes'] for r in m['files']),
      'manifest_sha256':sha(ROOT/'MANIFEST.json'),'build_or_simulation_executed':False},indent=2))
if __name__=='__main__':main()
'''
    (PKG/'verify_package.py').write_text(verify)
    # Syntax-only parsing writes no bytecode and executes none of these recipes.
    for p in PKG.rglob('*.py'):ast.parse(p.read_text(),filename=str(p))
    forbidden={'.o','.a','.so','.exe','.bin','.pyc','.jsonl','.gz','.xz','.zst','.obj','.dll'}
    for p in PKG.rglob('*'):
        if p.is_file():assert p.suffix.lower() not in forbidden and p.stat().st_size<2*1024*1024,p
    files=[{'path':str(p.relative_to(PKG)).replace('\\','/'),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(PKG.rglob('*')) if p.is_file()]
    manifest={'schema':1,'scope':'completed ablation source and compact evidence only','packaged_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
      'portable_link_recipe_executed':False,'new_build_or_simulation_executed_during_packaging':False,'files':files}
    (PKG/'MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    sums=''.join(r['sha256']+'  '+r['path']+'\n' for r in files)+sha(PKG/'MANIFEST.json')+'  MANIFEST.json\n'
    (PKG/'SHA256SUMS').write_text(sums)
    archive=STAGE/'ablation_source_and_evidence.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(PKG.rglob('*')):
            if p.is_file():z.write(p,'ablation/'+str(p.relative_to(PKG)).replace('\\','/'))
    check=STAGE/'zipcheck';check.mkdir()
    with zipfile.ZipFile(archive) as z:
        for n in z.namelist():assert not Path(n).is_absolute() and '..' not in Path(n).parts
        z.extractall(check)
    assert tree(PKG)==tree(check/'ablation')
    for r in files:
        p=check/'ablation'/r['path'];assert p.stat().st_size==r['bytes'] and sha(p)==r['sha256']
    report={'status':'pass','zip':str(archive),'zip_bytes':archive.stat().st_size,'zip_sha256':sha(archive),
      'manifest_sha256':sha(PKG/'MANIFEST.json'),'payload_files':len(files),'archive_files':len(tree(PKG)),
      'uncompressed_bytes':sum(p.stat().st_size for p in PKG.rglob('*') if p.is_file()),
      'all_unzipped_file_hashes_equal':True,'forbidden_objects_binaries_fulltraces_or_memorydumps':False,
      'portable_link_recipe_executed':False,'build_or_simulation_executed':False}
    (STAGE/'ZIP_VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n')
    (STAGE/'SHA256SUMS').write_text(sha(archive)+'  '+archive.name+'\n'+sha(STAGE/'ZIP_VERIFICATION.json')+'  ZIP_VERIFICATION.json\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
