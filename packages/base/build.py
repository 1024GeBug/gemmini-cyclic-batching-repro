"""Fresh CPU-free Verilator build from bundled RTL; no old objects are accepted."""
from pathlib import Path
import argparse,datetime,json,os,shutil,subprocess,sys
from verify_source import ROOT,sha,verify

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--profile',required=True,choices=['online','closed_full'])
    ap.add_argument('--build-dir',type=Path,required=True)
    ap.add_argument('--jobs',type=int,default=2,choices=[1,2,4])
    a=ap.parse_args(); frozen=verify(); out=a.build_dir.resolve()
    if out.exists():raise RuntimeError('refuse existing build directory: '+str(out))
    if ' ' in str(ROOT) or ' ' in str(out):raise RuntimeError('use paths without spaces for Verilator make response files')
    tools={n:shutil.which(n) for n in ['verilator','g++','gcc','make','ar','ranlib','ld','perl']}
    if not all(tools.values()):raise RuntimeError('missing Linux tool: '+str(tools))
    env=dict(os.environ)
    for name in ['VERILATOR_ROOT','VERILATOR_BIN','LD_LIBRARY_PATH']:env.pop(name,None)
    env.update(CXX=tools['g++'],CC=tools['gcc'],AR=tools['ar'],RANLIB=tools['ranlib'],LD=tools['ld'])
    versions={n:next(line for line in subprocess.check_output([tools[n],'--version'],env=env,text=True).splitlines() if line.strip())
              for n in ['verilator','g++','make','perl']}
    if not versions['verilator'].startswith('Verilator 5.022 '):raise RuntimeError('requires Verilator 5.022')
    out.mkdir(parents=True); obj=out/'obj'; rtl=ROOT/'platform/rtl/default16'
    files=sorted(p for p in rtl.iterdir() if p.suffix in ['.v','.sv'])
    if len(files)!=134:raise RuntimeError('expected 134 RTL files plus init_inputs.h')
    (out/'rtl.f').write_text(''.join(str(p)+'\n' for p in files))
    src=ROOT/'runtimes'/a.profile/'source'
    includes=[rtl,src,src/'engine',ROOT/'platform/mapper',ROOT/'platform/mapper/vendor']
    cflags='-O1 -std=gnu++17 -pthread '+' '.join('-I'+str(p) for p in includes)
    # No executable/archive/object outside this fresh obj directory appears in argv.
    cmd=[tools['verilator'],'--cc','--exe','--build','-j',str(a.jobs),'--assert','-Wno-fatal',
        '--top-module','Gemmini','--Mdir',str(obj),'--output-split','10000','--output-split-cfuncs','100',
        '-CFLAGS',cflags,'-LDFLAGS','-pthread','+define+PRINTF_COND=0','-f',str(out/'rtl.f'),
        str(src/'engine/verilated_engine.cpp'),str(src/'online_main.cpp'),str(src/'driver.cpp'),'-o','online_service']
    assert 'reused_v2' not in ' '.join(cmd)
    record={'schema':1,'status':'registered_before_build','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'profile':a.profile,'argv':cmd,'cwd':str(out),'tool_versions':versions,
        'tools':{n:{'path':p,'resolved':str(Path(p).resolve()),'bytes':Path(p).stat().st_size,'sha256':sha(Path(p))} for n,p in tools.items()},
        'package':frozen,'rtl_file_count':len(files),'rtl_filelist_sha256':sha(out/'rtl.f'),
        'rtl':{str(p.relative_to(ROOT)):sha(p) for p in files},
        'controller_source':{str(p.relative_to(src)):sha(p) for p in src.rglob('*') if p.is_file()},
        'fresh_rtl_model':True,'historical_objects_used':False,'cpu_instantiated':False,
        'build_environment':{n:env[n] for n in ['CXX','CC','AR','RANLIB','LD']}}
    manifest=out/'build_manifest.json'
    manifest.write_text(json.dumps(record,indent=2)+'\n')
    with (out/'build.stdout.log').open('w') as stdout,(out/'build.stderr.log').open('w') as stderr:
        result=subprocess.run(cmd,cwd=out,env=env,stdout=stdout,stderr=stderr)
    record['exit_code']=result.returncode
    if result.returncode==0:
        header=obj/'VGemmini___024root.h'; text=header.read_text()
        members=['Gemmini__DOT__spad__DOT__writer__DOT__total_bytes_sent','Gemmini__DOT__counters__DOT___counterfile_io_data']
        if not all(n in text for n in members):raise RuntimeError('fresh model lacks required audit observer fields')
        record['required_observer_members']=members
        record['generated_model']={p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(obj.iterdir()) if p.is_file() and p.suffix in ['.h','.cpp','.a','.o','.mk','.dat']}
        binary=obj/'online_service';record['binary']={'path':str(binary),'bytes':binary.stat().st_size,'sha256':sha(binary)}
    post=verify()
    if post!=frozen:raise RuntimeError('source package changed during fresh build')
    record['postbuild_source_verification']=post
    record['status']='built' if result.returncode==0 else 'failed'
    record['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    manifest.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'status':record['status'],'exit_code':result.returncode,'manifest':str(manifest),'binary':record.get('binary')},indent=2))
    return result.returncode

if __name__=='__main__':sys.exit(main())
