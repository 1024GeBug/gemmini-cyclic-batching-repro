"""Controller-only build recipe for a companion repro_v6 fresh model.

This portable recipe was packaged without executing a build or simulation.
It has no default machine-specific toolchain/model path.
"""
from pathlib import Path
import argparse, datetime, hashlib, json, os, shutil, subprocess, sys
ROOT=Path(__file__).resolve().parent.parent

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def tree(p):return {str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()}
def require(c,m):
    if not c:raise RuntimeError(m)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--arm',choices=['baseline','baseline_observed','high_restore','high_retain'],required=True)
    ap.add_argument('--repro-root',type=Path,required=True,help='extracted companion repro_v6 source root')
    ap.add_argument('--fresh-build',type=Path,required=True,help='successful repro_v6 online build with build_manifest.json and obj/')
    ap.add_argument('--verilator-include',type=Path,required=True,help='include directory of the same Verilator 5.022 installation')
    ap.add_argument('--cxx',default='g++',help='native compiler compatible with the fresh model objects')
    ap.add_argument('--out',type=Path,required=True,help='new immutable controller build directory')
    a=ap.parse_args()
    repro=a.repro_root.resolve();fresh=a.fresh_build.resolve();inc=a.verilator_include.resolve();out=a.out.resolve()
    require(os.name=='posix','run under Linux/WSL')
    require(not out.exists(),'refuse existing output directory')
    require((inc/'verilated.h').is_file(),'Verilator include directory missing verilated.h')
    manifest=json.loads((fresh/'build_manifest.json').read_text())
    require(manifest['status']=='built' and manifest['exit_code']==0 and manifest['profile']=='online' and
            manifest['fresh_rtl_model'] is True and manifest['historical_objects_used'] is False,
            'requires a successful companion fresh online model build')
    source_manifest=repro/'source_manifest.json'
    for r in json.loads(source_manifest.read_text())['files']:
        p=repro/r['path'];require(p.is_file() and p.stat().st_size==r['bytes'] and sha(p)==r['sha256'],'companion source mismatch: '+r['path'])
    # A published README may differ from the archived build package. Require
    # every recorded RTL and original controller byte to agree independently.
    require(len(manifest['rtl'])==134,'fresh model does not record all 134 RTL sources')
    for rel,h in manifest['rtl'].items():require(sha(repro/rel)==h,'fresh-model RTL differs from companion: '+rel)
    for rel,h in manifest['controller_source'].items():
        require(sha(repro/'runtimes/online/source'/rel)==h,'fresh original controller differs from companion: '+rel)
    model=fresh/'obj';models={}
    for name in ['VGemmini__ALL.a','verilated.o','verilated_threads.o','VGemmini___024root.h']:
        p=model/name;r=manifest['generated_model'][name]
        require(p.is_file() and sha(p)==r['sha256'],'fresh generated input changed: '+name)
        models[str(p)]=sha(p)
    members=['Gemmini__DOT__spad__DOT__spad_mems_0__DOT__mem_ext__DOT__Memory',
             'Gemmini__DOT__spad__DOT__spad_mems_0__DOT____Vcellinp__mem_ext__RW0_en',
             'Gemmini__DOT__spad__DOT__spad_mems_3__DOT____Vcellinp__mem_ext__RW0_wmask']
    header=(model/'VGemmini___024root.h').read_text()
    require(all(m in header for m in members),'fresh header lacks extra SRAM observer members; do not silently remove the observer')
    compiler=shutil.which(a.cxx);require(compiler is not None,'native compiler unavailable')
    src=ROOT/'runtimes'/a.arm/'source';before=tree(src)
    out.mkdir(parents=True);shutil.copytree(src,out/'source')
    mapper=repro/'platform/mapper';shutil.copytree(mapper,out/'mapper')
    record={'schema':1,'status':'registered_before_controller_build','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
       'arm':a.arm,'source':before,'source_origin':str(src),'fresh_build_manifest_sha256':sha(fresh/'build_manifest.json'),
       'companion_source_manifest_sha256':sha(source_manifest),
       'fresh_package_source_manifest_sha256':manifest['package']['source_manifest_sha256'],
       'recorded_RTL_and_original_controller_match_companion':True,'model_inputs':models,
       'compiler':{'path':compiler,'sha256':sha(Path(compiler))},'verilator_include':str(inc),
       'rtl_recompiled':False,'cpu_instantiated':False,'recipe_sha256':sha(Path(__file__)),'commands':[]}
    dest=out/'controller_build_manifest.json'
    def save():dest.write_text(json.dumps(record,indent=2)+'\n')
    env=dict(os.environ);env.pop('LD_LIBRARY_PATH',None)
    def run(label,argv):
        with (out/(label+'.stdout.log')).open('w') as so,(out/(label+'.stderr.log')).open('w') as se:
            p=subprocess.run(argv,cwd=out,env=env,stdout=so,stderr=se)
        record['commands'].append({'label':label,'argv':argv,'exit_code':p.returncode});save()
        require(p.returncode==0,'controller build step failed: '+label)
    save()
    try:
        common=[compiler,'-O1','-std=gnu++17','-pthread']
        for p in [out/'source',out/'source/engine',out/'mapper',out/'mapper/vendor',model,inc,inc/'vltstd']:
            common+=['-I',str(p)]
        objects=[]
        for label,rel in [('driver','driver.cpp'),('online_main','online_main.cpp'),('verilated_engine','engine/verilated_engine.cpp')]:
            obj=out/(label+'.o');run(label,common+['-c',str(out/'source'/rel),'-o',str(obj)]);objects.append(obj)
        binary=out/'online_service'
        run('link',[compiler,'-pthread',*[str(p) for p in objects],*[str(model/n) for n in ['verilated.o','verilated_threads.o','VGemmini__ALL.a']],'-o',str(binary)])
        require(tree(src)==before and all(sha(Path(p))==h for p,h in models.items()),'source/model changed during controller build')
        record.update(status='built',binary={'path':str(binary),'sha256':sha(binary),'bytes':binary.stat().st_size})
    except Exception as e:
        record.update(status='failed',error=f'{type(e).__name__}: {e}')
    record['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();save()
    print(json.dumps({'status':record['status'],'manifest':str(dest),'binary':record.get('binary'),'error':record.get('error')},indent=2))
    return record['status']!='built'

if __name__=='__main__':sys.exit(main())
