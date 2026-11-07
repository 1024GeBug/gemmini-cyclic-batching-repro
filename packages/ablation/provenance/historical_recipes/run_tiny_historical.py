"""Run one frozen four-request arm, then independent protocol/SRAM audits."""
from pathlib import Path
import argparse, hashlib, json, os, subprocess, time

ROOT=Path(__file__).resolve().parent
WSL=Path('/home/Gemguan/work/codex_gemmini_paper_20261103/consumer_gate/a_retention_v1')
FIXTURE=ROOT.parent/'online_inputs/diagnostic_q896_n128_v1'

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['baseline','retain'],required=True)
    ap.add_argument('--name',required=True);a=ap.parse_args()
    build=WSL/'builds'/('baseline_observer_v1' if a.mode=='baseline' else 'retention_observer_v1')
    source=ROOT/('baseline' if a.mode=='baseline' else 'variant')/'source'
    binary=build/'online_service';manifest=build/'manifest.json'
    expected_binary={'baseline':'53411fc937151bb9b94394714b394b94cd961c8bc8e0c0a1161a9fd107032419',
                     'retain':'2f866d5ba8955d044041634dcf1a6c9b3b3394e8b5f4944b0ae4b45d7c0a90c3'}[a.mode]
    require=lambda c,m:None if c else (_ for _ in ()).throw(RuntimeError(m))
    require(sha(binary)==expected_binary,'binary hash differs')
    require(sha(FIXTURE/'manifest.json')=='e0e781aba066e8daa17bb43703d9f51786747e057d9bd2a7d9bc7356668ff109','input manifest differs')
    for name,record in json.loads((FIXTURE/'manifest.json').read_text())['files'].items():
        require(sha(FIXTURE/name)==record['sha256'],'input differs '+name)
    require(sha(ROOT/'tiny_inputs/meta.json')=='5362a70caceef335a09e6a3849b8b48323b2a50b7407896f9a5cc34f25e373db','tiny meta differs')
    require(sha(ROOT/'tiny_inputs/arrivals.csv')=='88ddac7a700f393f1f45bbbf900dad5bb5e02ee49c86e9c4bd9ec6715845b499','tiny arrivals differs')
    m=json.loads(manifest.read_text());require(m['status']=='built','build not successful')
    for rel,digest in m['snapshots']['source']['sha256'].items():
        require(sha(source/rel)==sha(build/'source'/rel)==digest,'source differs '+rel)
    WSL.mkdir(exist_ok=True,parents=True)
    out=WSL/a.name;side=WSL/(a.name+'.preregistration.json')
    require(not out.exists() and not side.exists(),'immutable run already exists')
    cmd=[str(binary),'--meta',str(ROOT/'tiny_inputs/meta.json'),'--memory-image',str(FIXTURE/'memory.bin'),
         '--arrivals',str(ROOT/'tiny_inputs/arrivals.csv'),'--output-dir',str(out),'--policy','on','--tile-j','4',
         '--batch-target','1','--timeout-cycles','0','--memory-latency','2','--max-cycles','10000000']
    checker_paths=[ROOT/'audit/check_v2.py',ROOT/'audit/raw_tl.py',ROOT/'audit/check_retention.py']
    frozen=[binary,manifest,ROOT/'source_freeze.json',ROOT/'STATIC_AUDIT_AND_PREREGISTRATION.md',
            Path(__file__),ROOT/'tiny_inputs/meta.json',ROOT/'tiny_inputs/arrivals.csv']+checker_paths
    frozen += [p for p in FIXTURE.iterdir() if p.is_file()]
    frozen += list(source.rglob('*.cpp'))+list(source.rglob('*.hpp'))
    before={str(p):sha(p) for p in frozen}
    reg={'status':'frozen_before_run','time_unix':time.time(),'mode':a.mode,'queries':4,'tile_j':4,
         'policy':'on','memory_latency':2,'cpu_instantiated':False,'rtl_modified':False,
         'scope':'tiny functional and retention coverage; no 32-request performance conclusion',
         'argv':cmd,'frozen_sha256':before,'model_inputs':m['reused_inputs']}
    side.write_text(json.dumps(reg,indent=2)+'\n');pre=side.read_bytes()
    env=dict(os.environ);env['LD_LIBRARY_PATH']='/home/Gemguan/work/chipyard/.conda-env/lib'
    env['PYTHONDONTWRITEBYTECODE']='1'
    stdout=WSL/(a.name+'.stdout.log');stderr=WSL/(a.name+'.stderr.log')
    with stdout.open('w') as so,stderr.open('w') as se:p=subprocess.run(cmd,env=env,stdout=so,stderr=se)
    require(out.is_dir(),'runtime did not create output; see sidecar streams')
    (out/'process.stdout.log').write_bytes(stdout.read_bytes());(out/'process.stderr.log').write_bytes(stderr.read_bytes())
    reg.update(exit_code=p.returncode,finished_unix=time.time())
    audits=[]
    if p.returncode==0:
        audits=[['python3',str(checker_paths[0]),'--input-dir',str(FIXTURE),'--run-dir',str(out),'--queries','4',
                 '--out',str(out/'independent_audit_v2.json')],
                ['python3',str(checker_paths[2]),'--input-dir',str(FIXTURE),'--run-dir',str(out),'--mode',a.mode,
                 '--out',str(out/'independent_retention_audit.json')]]
    reg['audit_results']=[]
    for i,ac in enumerate(audits):
        with (out/f'audit_{i}.stdout.log').open('w') as so,(out/f'audit_{i}.stderr.log').open('w') as se:
            audit=subprocess.run(ac,env=env,stdout=so,stderr=se)
        reg['audit_results'].append({'argv':ac,'exit_code':audit.returncode})
    reg['all_frozen_inputs_unchanged']=all(sha(Path(p))==digest for p,digest in before.items())
    reg['model_unchanged']=all(sha(Path(p))==digest for p,digest in m['reused_inputs'].items())
    reg['outputs']={p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in out.iterdir() if p.is_file()}
    reg['status']='pass' if p.returncode==0 and len(reg['audit_results'])==2 and all(x['exit_code']==0 for x in reg['audit_results']) and reg['all_frozen_inputs_unchanged'] and reg['model_unchanged'] else 'fail'
    (out/'preregistration.json').write_bytes(pre);(out/'postregistration.json').write_text(json.dumps(reg,indent=2)+'\n')
    print(json.dumps({'status':reg['status'],'out':str(out),'exit_code':p.returncode,'audits':reg['audit_results'],
                      'inputs_unchanged':reg['all_frozen_inputs_unchanged'],'model_unchanged':reg['model_unchanged']},indent=2))
    raise SystemExit(reg['status']!='pass')

if __name__=='__main__':main()
