"""Run one selected registered policy on a fresh-model binary; never overwrite."""
from pathlib import Path
import argparse,csv,datetime,json,os,subprocess,sys
from verify_source import ROOT,sha,verify

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--profile',required=True,choices=['online','closed_full'])
    ap.add_argument('--build-dir',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--case',choices=['tiny4','diagnostic32'],default='tiny4')
    ap.add_argument('--policy',required=True,choices=['on','off','closed'])
    ap.add_argument('--tile-j',type=int,choices=[4,8],default=4)
    ap.add_argument('--batch-target',type=int,choices=[1,3],default=1)
    ap.add_argument('--timeout-cycles',type=int,choices=[0,52109],default=0)
    a=ap.parse_args(); frozen=verify(); build=a.build_dir.resolve();out=a.out.resolve()
    if out.exists():raise RuntimeError('refuse existing run directory: '+str(out))
    if a.profile=='closed_full' and a.policy!='closed':raise RuntimeError('full profile restricted to registered CLOSED')
    if a.policy=='closed' and (a.batch_target,a.timeout_cycles) not in [(1,0),(3,52109)]:raise RuntimeError('unregistered CLOSED batch target/timeout pair')
    if a.policy!='closed' and (a.batch_target,a.timeout_cycles)!=(1,0):raise RuntimeError('ON/OFF require immediate parameters')
    if a.profile=='online' and a.policy in ['on','off'] and (a.tile_j!=4 or a.batch_target!=1 or a.timeout_cycles!=0):
        raise RuntimeError('ON/OFF parameters must remain frozen')
    if a.case=='tiny4' and (a.batch_target!=1 or a.timeout_cycles!=0):raise RuntimeError('tiny gate uses immediate policy')
    bm=json.loads((build/'build_manifest.json').read_text());binary=build/'obj/online_service'
    if bm['status']!='built' or bm['profile']!=a.profile or bm['historical_objects_used'] or not bm['fresh_rtl_model'] or bm['exit_code']!=0:
        raise RuntimeError('requires matching successful fresh-RTL build manifest')
    if sha(binary)!=bm['binary']['sha256'] or bm['package']['source_manifest_sha256']!=frozen['source_manifest_sha256']:
        raise RuntimeError('binary/package differs from fresh build')
    fixture=ROOT/'inputs/diagnostic_q896_n128_v1'
    if sha(fixture/'manifest.json')!='e0e781aba066e8daa17bb43703d9f51786747e057d9bd2a7d9bc7356668ff109':raise RuntimeError('fixture changed')
    tiny=ROOT/'inputs/tiny4'
    meta=tiny/'meta.json' if a.case=='tiny4' else ROOT/'inputs/diagnostic_meta.json'
    arrivals=tiny/'arrivals.csv' if a.case=='tiny4' else fixture/'arrivals.csv'
    count=4 if a.case=='tiny4' else 32
    with arrivals.open() as f: rows=list(csv.DictReader(f))
    with (fixture/'arrivals.csv').open() as f: full=list(csv.DictReader(f))
    if len(rows)!=count or rows!=full[:count]:raise RuntimeError('arrival subset differs')
    m=json.loads(meta.read_text())['driver_meta'];m32=json.loads((ROOT/'inputs/diagnostic_meta.json').read_text())['driver_meta']
    if m['query_count']!=count or {**m,'query_count':32}!=m32:raise RuntimeError('metadata shape/budget/address differs')
    checker=ROOT/'checkers'/a.profile/'check_v2.py'
    cmd=[str(binary),'--meta',str(meta),'--arrivals',str(arrivals),'--memory-image',str(fixture/'memory.bin'),
        '--output-dir',str(out),'--policy',a.policy,'--tile-j',str(a.tile_j),'--batch-target',str(a.batch_target),
        '--timeout-cycles',str(a.timeout_cycles),'--memory-latency','2','--max-cycles','10000000' if count==4 else '100000000',
        '--poll-cycles','64','--decision-cycles','16','--issue-cycles','1','--sink-cycles','64']
    if a.profile=='closed_full':cmd+=['--c1-group-panels','14']
    out.parent.mkdir(parents=True,exist_ok=True)
    side=out.parent/(out.name+'.registration.json')
    sidecars=[side,out.parent/(out.name+'.stdout.log'),out.parent/(out.name+'.stderr.log'),out.parent/(out.name+'.execution_manifest.json')]
    if any(p.exists() for p in [out,*sidecars]):raise RuntimeError('refuse existing run/output sidecar')
    record={'schema':1,'status':'registered_before_run','registered_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'profile':a.profile,'case':a.case,'queries':count,'argv':cmd,'package':frozen,
        'build_manifest_sha256':sha(build/'build_manifest.json'),'binary_sha256':sha(binary),
        'checker_sha256':sha(checker),'raw_tl_sha256':sha(checker.parent/'raw_tl.py'),
        'input_files':{str(p.relative_to(ROOT)):sha(p) for p in [meta,arrivals,fixture/'memory.bin',fixture/'manifest.json']}}
    side.write_text(json.dumps(record,indent=2)+'\n'); prereg=side.read_bytes()
    env=dict(os.environ);env.pop('LD_LIBRARY_PATH',None)
    with (out.parent/(out.name+'.stdout.log')).open('w') as stdout,(out.parent/(out.name+'.stderr.log')).open('w') as stderr:
        r=subprocess.run(cmd,env=env,stdout=stdout,stderr=stderr)
    record['run_exit']=r.returncode
    if out.exists():(out/'preregistration.json').write_bytes(prereg)
    if r.returncode==0:
        cfg=json.loads((out/'runtime_config.json').read_text())
        expected={'query_count':count,'policy':a.policy,'tile_j':a.tile_j,'batch_target':a.batch_target,
            'timeout_cycles':a.timeout_cycles,'memory_latency_cycles':2,'poll_cycles':64,'decision_cycles':16,'issue_cycles':1,'sink_cycles':64}
        if a.profile=='closed_full':expected['c1_group_panels']=14
        if any(cfg[k]!=v for k,v in expected.items()):raise RuntimeError('runtime configuration differs')
        ac=[sys.executable,str(checker),'--input-dir',str(fixture),'--run-dir',str(out),'--queries',str(count),'--out',str(out/'audit.json')]
        record['audit_argv']=ac
        with (out/'audit.stdout.log').open('w') as stdout,(out/'audit.stderr.log').open('w') as stderr:
            checked=subprocess.run(ac,stdout=stdout,stderr=stderr)
        audit=json.loads((out/'audit.json').read_text())
        record['audit_exit']=checked.returncode;record['audit_status']=audit['status']
        if audit.get('queries')!=count or audit.get('hashes',{}).get('checker')!=record['checker_sha256'] or audit.get('hashes',{}).get('fixture_manifest')!='e0e781aba066e8daa17bb43703d9f51786747e057d9bd2a7d9bc7356668ff109':
            raise RuntimeError('audit report provenance differs')
    post=verify()
    if post!=frozen:raise RuntimeError('source package changed during run')
    record['postrun_source_verification']=post
    record['binary_unchanged']=sha(binary)==record['binary_sha256']
    record['checker_unchanged']=sha(checker)==record['checker_sha256'] and sha(checker.parent/'raw_tl.py')==record['raw_tl_sha256']
    record['inputs_unchanged']=all(sha(ROOT/p)==d for p,d in record['input_files'].items())
    if out.exists():record['outputs']={p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in out.iterdir() if p.is_file()}
    record['status']='pass' if r.returncode==record.get('audit_exit')==0 and record.get('audit_status')=='pass' and all(record[k] for k in ['binary_unchanged','checker_unchanged','inputs_unchanged']) else 'failed'
    (out.parent/(out.name+'.execution_manifest.json')).write_text(json.dumps(record,indent=2)+'\n')
    if out.exists():(out/'execution_manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'status':record['status'],'run_exit':r.returncode,'audit_exit':record.get('audit_exit'),'out':str(out)},indent=2))
    return 0 if record['status']=='pass' else 1

if __name__=='__main__':sys.exit(main())
