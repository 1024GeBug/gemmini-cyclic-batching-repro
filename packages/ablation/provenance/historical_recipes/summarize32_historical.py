"""Summarize completed immutable logs, with query-paired five-interval accounting."""
from pathlib import Path
from collections import Counter
import csv,gzip,hashlib,json,shutil,statistics
ROOT=Path(__file__).resolve().parent
BASE=Path('/home/Gemguan/work/codex_gemmini_paper_20261103/consumer_gate')
RUNS=BASE/'a_retention_v1/diagnostic32_v1'
OUT=ROOT/'diagnostic32_evidence_v1'
STAMPS=['arrival','admitted','c1_notified','consumer_start','a2_sealed','delivery_end']
INTERVALS=['arrival_to_admit','admit_to_c1','c1_to_consumer','consumer_to_a2','a2_to_delivery']

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def load(p):return json.loads(p.read_text())
def artifact(run,name):
    paths=[p for p in [run/name,run/(name+'.gz')] if p.exists()]
    assert len(paths)==1,(run,name)
    return paths[0]
def events(run):
    p=artifact(run,'driver_events.jsonl')
    with (gzip.open(p,'rt') if p.suffix=='.gz' else p.open()) as f:
        return [json.loads(l) for l in f]

def summarize(name,run,auditfile):
    audit=load(run/auditfile);assert audit['status']=='pass'
    ctl=events(run);result=load(run/'result.json');queries=load(run/'query_results.json')
    queries=queries['queries'] if isinstance(queries,dict) else queries
    queries=sorted(queries,key=lambda q:q['query']);assert [q['query'] for q in queries]==list(range(32))
    for fld,filename in [('driver_events','driver_events.jsonl'),('engine_events','engine_events.jsonl'),('actual_memory','actual_memory.bin')]:
        assert sha(artifact(run,filename))==audit['hashes'][fld],('audited evidence changed',name,filename)
    rows=[]
    for q in queries:
        r={'arm':name,'query':q['query'],**{k:q[k] for k in STAMPS}}
        for i,k in enumerate(INTERVALS):r[k]=q[STAMPS[i+1]]-q[STAMPS[i]];assert r[k]>=0
        r['final_latency']=q['delivery_end']-q['arrival'];assert sum(r[k] for k in INTERVALS)==r['final_latency']
        rows.append(r)
    starts={e['stage']:e for e in ctl if e['event']=='stage_start'}
    idles={e['stage']:e for e in ctl if e['event']=='stage_idle_sealed'}
    duration=Counter();counts=Counter();fees=Counter()
    for sid,s in starts.items():counts[s['name']]+=1;duration[s['name']]+=idles[sid]['cycle']-s['cycle']
    for e in ctl:
        if e['event']=='cpu_op':fees[e['kind']]+=e['end']-e['start']
    summary={'arm':name,'mean_final_latency':statistics.mean(r['final_latency'] for r in rows),
      'mean_intervals':{k:statistics.mean(r[k] for r in rows) for k in INTERVALS},
      'last_delivery':max(q['delivery_end'] for q in queries),'cycles_including_reset':result['cycles_including_reset'],
      'get_bytes':result['tl_get_payload_bytes'],'put_ack_bytes':result['tl_put_transfer_bytes'],'rocc_commands':result['rocc_commands'],
      'cohort_sizes':[e['rows'] for e in ctl if e['event']=='cohort_start'],
      'cohorts':sum(e['event']=='cohort_start' for e in ctl),
      'restore_stages':counts['restore_inputs'],'resume_stages':counts['resume_c1_config'],
      'restore_query_instances':sum(e['live_inputs'] for e in ctl if e['event']=='restore_start'),
      'retention_query_instances':sum(e['live_inputs'] for e in ctl if e['event']=='resume_c1_config_start'),
      'stage_counts':dict(counts),'stage_elapsed_cycles':dict(duration),'paid_CPU_intervals':dict(fees),
      'run':str(run),'audit_sha256':sha(run/auditfile)}
    sram=run/'independent_retention_audit_v3.json'
    if sram.exists():
        a=load(sram);assert a['status']=='pass'
        summary['SRAM']={k:a[k] for k in ['active_A1_retention_triggers','active_query_instances','genuinely_vulnerable_triggers',
          'genuinely_vulnerable_query_instances','auto_B_halves_exercised','SRAM_write_events','SRAM_snapshot_count']}
    return summary,rows

def table(summary):
    return '\n'.join('| '+r['arm']+' | '+str(r['mean_final_latency'])+' | '+str(r['get_bytes'])+' | '+str(r['put_ack_bytes'])+' | '+str(r['cohorts'])+' | '+str(r['restore_stages'])+'/'+str(r['resume_stages'])+' |' for r in summary)

def main():
    post=load(RUNS/'postregistration.json');assert post['status']=='pass' and post['all_frozen_unchanged']
    OUT.mkdir(exist_ok=False)
    specs=[('original_on',BASE/'online_runtime/diagnostic_v2_01/on','audit.json'),
      ('high_a2_restore',RUNS/'high_a2_restore','independent_audit_v2.json'),
      ('high_a2_retain',RUNS/'high_a2_retain','independent_audit_v2.json'),
      ('closed_full_j4',BASE/'closed_full_runtime/diagnostic_full_v1/closed_full_j4_immediate','audit.json')]
    records=[];allrows=[]
    for name,run,auditfile in specs:
        if not (run/auditfile).exists():
            candidates=[p for p in run.glob('*audit*.json') if 'retention' not in p.name and load(p).get('status')=='pass']
            assert len(candidates)==1,(run,candidates);auditfile=candidates[0].name
        s,rows=summarize(name,run,auditfile);records.append(s);allrows+=rows
        if name.startswith('high_a2'):
            dest=OUT/name;dest.mkdir()
            for p in run.iterdir():
                if p.suffix in ('.json','.csv','.log'):shutil.copyfile(p,dest/p.name)
            # Preserve complete wire, SRAM trace, and 16MiB memory in compressed files.
            for filename in ['engine_events.jsonl','driver_events.jsonl','actual_memory.bin']:
                src=run/filename;dst=dest/(filename+'.gz')
                with src.open('rb') as f,gzip.open(dst,'wb',compresslevel=5) as g:shutil.copyfileobj(f,g,1024*1024)
                s.setdefault('raw_evidence',{})[filename]={'sha256':sha(src),'compressed_sha256':sha(dst),'bytes':src.stat().st_size}
            build=BASE/'a_retention_v1/builds'/('diagnostic32_'+name+'_v1')
            shutil.copyfile(build/'manifest.json',dest/'build_manifest.json')
    indexed={(r['arm'],r['query']):r for r in allrows};paired=[]
    for q in range(32):
        for comparator in ['original_on','high_a2_restore','closed_full_j4']:
            a=indexed['high_a2_retain',q];b=indexed[comparator,q]
            assert a['arrival']==b['arrival']
            paired.append({'query':q,'comparison':'retain_minus_'+comparator,
              **{k:a[k]-b[k] for k in INTERVALS+['final_latency']}})
    with (OUT/'query_five_intervals.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(allrows[0]));w.writeheader();w.writerows(allrows)
    with (OUT/'query_paired_deltas.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(paired[0]));w.writeheader();w.writerows(paired)
    byname={r['arm']:r for r in records}
    comparisons={n:{'mean_delta':byname['high_a2_retain']['mean_final_latency']-byname[n]['mean_final_latency'],
      'relative_delta':byname['high_a2_retain']['mean_final_latency']/byname[n]['mean_final_latency']-1,
      'queries_better':sum(r['final_latency']<0 for r in paired if r['comparison']=='retain_minus_'+n),
      'queries_equal':sum(r['final_latency']==0 for r in paired if r['comparison']=='retain_minus_'+n),
      'queries_worse':sum(r['final_latency']>0 for r in paired if r['comparison']=='retain_minus_'+n)} for n in ['original_on','high_a2_restore','closed_full_j4']}
    stop=byname['high_a2_retain']['mean_final_latency']>byname['closed_full_j4']['mean_final_latency']
    summary={'status':'audited_fixed_diagnostic','records':records,'comparisons':comparisons,
      'heldout_decision':'stop_this_candidate' if stop else 'review_contribution_before_any_heldout',
      'preregistration_sha256':sha(RUNS/'preregistration.json'),'all_frozen_unchanged':True,
      'interpretation':'same-high-A2 comparison controls mapping; schedule/cohort changes are part of policy effect, not fixed',
      'inference_unit':'one fixed 32-request diagnostic; no independent repeats or statistical CI'}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    for n in ['preregistration.json','postregistration.json','execution_summary.json']:shutil.copyfile(RUNS/n,OUT/n)
    shutil.copytree(ROOT/'diagnostic32_v1',OUT/'frozen_local_sources')
    shutil.copytree(RUNS/'audit_source',OUT/'audit_source')
    (OUT/'REPORT.md').write_text('# 高A2的restore/retain固定32请求诊断\n\n两臂原始TL、历史C1/A2/C2逐字节、完整16MiB内存、真实counter/busy和生命周期检查及SRAM补充检查均PASS；运行前后源码、输入、checker、模型和binary均不变。\n\n| 策略 | mean最终延迟/cycle | Get/B | Put/Ack/B | cohorts | restore/resume |\n|---|---:|---:|---:|---:|---:|\n'+table(records)+'\n\n五段均值：\n\n| 策略 | arrival→admit | admit→C1 | C1→consumer | consumer→A2 | A2→delivery |\n|---|---:|---:|---:|---:|---:|\n'+'\n'.join('| '+r['arm']+' | '+' | '.join(str(r['mean_intervals'][k]) for k in INTERVALS)+' |' for r in records)+'\n\n`query_five_intervals.csv`保存逐query绝对值，`query_paired_deltas.csv`保存retain相对其他三策略的配对差。配对差为同一到达query的描述性时间差，不代表独立实验样本。\n\n比较：\n\n'+ '\n'.join(f'- retain 相对 {n}：均值差{v["mean_delta"]}周期（{v["relative_delta"]*100:.3f}%）；逐query更快/相同/更慢={v["queries_better"]}/{v["queries_equal"]}/{v["queries_worse"]}。' for n,v in comparisons.items())+'\n\n决策：'+('retain仍慢于完整J4，按预登记停止此候选held-out。' if stop else '继续评审研究贡献，尚未授权held-out。')+'\n\n相同高A2映射的对照分离了bank位置选择与删除restore，但调度时间改变可使owner/cohort和后续成本改变，应按完整轨迹结果解释。静态映射仍是现有ISA能力。源码patch、预登记、完成记录和完整压缩原始证据随包保存。\n',encoding='utf-8')
    manifest={str(p.relative_to(OUT)):{'bytes':p.stat().st_size,'sha256':sha(p)} for p in OUT.rglob('*') if p.is_file()}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'out':str(OUT),'manifest_sha256':sha(OUT/'manifest.json'),'summary':summary},indent=2))

if __name__=='__main__':main()
