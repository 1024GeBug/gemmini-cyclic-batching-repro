"""Execute exactly the two preregistered high-A2 fixed-trace arms."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,time
ROOT=Path(__file__).resolve().parent
LOCAL=ROOT/'diagnostic32_v1'
WSL=Path('/home/Gemguan/work/codex_gemmini_paper_20261103/consumer_gate/a_retention_v1')
OUT=WSL/'diagnostic32_v1'
INPUT=ROOT.parent/'online_inputs/diagnostic_q896_n128_v1'
ARMS=['high_a2_restore','high_a2_retain']

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def require(c,m):
    if not c:raise RuntimeError(m)

def main():
    require(not OUT.exists(),'immutable output exists')
    freeze=json.loads((LOCAL/'source_freeze.json').read_text())
    frozen={}
    def add(p):frozen[str(p)]=sha(p)
    for p in [LOCAL/'source_freeze.json',LOCAL/'PREREGISTRATION.md',LOCAL/'meta.json',Path(__file__),
              ROOT/'tiny_evidence_v1/manifest.json',ROOT/'fault_checks_v1.json']:
        add(p)
    for rel,h in freeze['checkers'].items():require(sha(ROOT/rel)==h,'checker differs');add(ROOT/rel)
    require(sha(INPUT/'manifest.json')=='e0e781aba066e8daa17bb43703d9f51786747e057d9bd2a7d9bc7356668ff109','fixture manifest differs')
    for n,r in json.loads((INPUT/'manifest.json').read_text())['files'].items():require(sha(INPUT/n)==r['sha256'],'fixture differs '+n)
    for p in INPUT.iterdir():
        if p.is_file():add(p)
    builds={};commands={};model={}
    for arm in ARMS:
        for rel,h in freeze['arms'][arm].items():require(sha(LOCAL/arm/rel)==h,'arm source differs');add(LOCAL/arm/rel)
        b=WSL/'builds'/('diagnostic32_'+arm+'_v1');m=json.loads((b/'manifest.json').read_text());builds[arm]=b
        require(m['status']=='built' and m['cpu_instantiated'] is False and m['rtl_modified'] is False,'build state differs')
        require(sha(b/'online_service')==m['binary']['sha256'],'binary differs')
        for rel,h in m['snapshots']['source']['sha256'].items():require(sha(b/'source'/rel)==h==sha(LOCAL/arm/'source'/rel),'compiled source differs')
        add(b/'online_service');add(b/'manifest.json')
        if model:require(model==m['reused_inputs'],'model arms differ')
        model=m['reused_inputs']
        commands[arm]=[str(b/'online_service'),'--meta',str(LOCAL/'meta.json'),'--memory-image',str(INPUT/'memory.bin'),
           '--arrivals',str(INPUT/'arrivals.csv'),'--output-dir',str(OUT/arm),'--policy','on','--tile-j','4',
           '--batch-target','1','--timeout-cycles','0','--memory-latency','2','--max-cycles','100000000']
    OUT.mkdir()
    shutil.copytree(ROOT/'audit',OUT/'audit_source',ignore=shutil.ignore_patterns('__pycache__','*.json'))
    add(OUT/'audit_source/check_v2.py');add(OUT/'audit_source/raw_tl.py');add(OUT/'audit_source/check_retention_v3.py')
    registration={'schema':1,'status':'frozen_before_results','registered_unix':time.time(),'arms':ARMS,'commands':commands,
                  'queries':32,'cpu_instantiated':False,'rtl_modified':False,'frozen':frozen,'model':model,
                  'scope':'fixed diagnostic only; if retain slower than full J4 stop candidate held-out'}
    pre=OUT/'preregistration.json';pre.write_text(json.dumps(registration,indent=2)+'\n');prehash=sha(pre)
    def unchanged():
        return sha(pre)==prehash and all(sha(Path(p))==h for p,h in frozen.items()) and all(sha(Path(p))==h for p,h in model.items())
    env=dict(os.environ);env['LD_LIBRARY_PATH']='/home/Gemguan/work/chipyard/.conda-env/lib';env['PYTHONDONTWRITEBYTECODE']='1'
    summary=[]
    for arm in ARMS:
        require(unchanged(),'pre-run frozen hash changed')
        print('RUN '+arm,flush=True)
        with (OUT/(arm+'.stdout.log')).open('w') as so,(OUT/(arm+'.stderr.log')).open('w') as se:
            p=subprocess.run(commands[arm],env=env,stdout=so,stderr=se)
        run=OUT/arm
        rec={'arm':arm,'run_exit':p.returncode,'audit_exit':None,'SRAM_audit_exit':None}
        if p.returncode==0:
            auditcmd=['python3',str(OUT/'audit_source/check_v2.py'),'--input-dir',str(INPUT),'--run-dir',str(run),'--queries','32','--out',str(run/'independent_audit_v2.json')]
            with (run/'protocol_audit.stdout.log').open('w') as so,(run/'protocol_audit.stderr.log').open('w') as se:
                ac=subprocess.run(auditcmd,env=env,stdout=so,stderr=se)
            rec['audit_exit']=ac.returncode
            auditcmd=['python3',str(OUT/'audit_source/check_retention_v3.py'),'--input-dir',str(INPUT),'--run-dir',str(run),'--mode',('high_restore' if arm=='high_a2_restore' else 'retain'),'--out',str(run/'independent_retention_audit_v3.json')]
            with (run/'SRAM_audit.stdout.log').open('w') as so,(run/'SRAM_audit.stderr.log').open('w') as se:
                ac=subprocess.run(auditcmd,env=env,stdout=so,stderr=se)
            rec['SRAM_audit_exit']=ac.returncode
        rec['all_frozen_unchanged']=unchanged()
        rec['outputs']={p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in run.iterdir() if p.is_file()} if run.exists() else {}
        summary.append(rec);(OUT/'execution_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        print('FINISH '+json.dumps({k:v for k,v in rec.items() if k!='outputs'}),flush=True)
    require(unchanged(),'post-run frozen hash changed')
    post={'status':'pass' if all(x['run_exit']==x['audit_exit']==x['SRAM_audit_exit']==0 and x['all_frozen_unchanged'] for x in summary) else 'fail',
          'preregistration_sha256':prehash,'finished_unix':time.time(),'all_frozen_unchanged':True,'summary':summary}
    (OUT/'postregistration.json').write_text(json.dumps(post,indent=2)+'\n')
    print('ALL_TWO '+post['status'],flush=True)
    raise SystemExit(post['status']!='pass')

if __name__=='__main__':main()
