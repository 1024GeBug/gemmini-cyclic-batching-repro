"""Independent physical SRAM/ISA retention audit; no runtime imports."""
from pathlib import Path
import argparse, hashlib, json, sys
from bisect import bisect_right
from collections import Counter
from raw_tl import events, require, AuditError

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def audit(run, fixture, mode):
    ctl=list(events(run/'driver_events.jsonl'))
    starts={e['stage']:e for e in ctl if e['event']=='stage_start'}
    idles={e['stage']:e for e in ctl if e['event']=='stage_idle_sealed'}
    seals={e['stage']:e for e in ctl if e['event']=='sealed_group_complete'}
    plans={e['tag']:e for e in ctl if e['event']=='command_plan'}
    query={e['query']:e for e in ctl if e['event']=='query_bind'}
    releases={e['query']:e for e in ctl if e['event']=='input_release'}
    ordered=sorted(starts,key=lambda s:starts[s]['cycle'])
    cycles=[starts[s]['cycle'] for s in ordered]
    memory=(fixture/'memory.bin').read_bytes()
    snapshots={}; writes={s:[] for s in starts}; commands={s:[] for s in starts}
    snapshot_tags={}
    total_writes=0
    for e in events(run/'engine_events.jsonl'):
        name=e['event']
        if name=='observer_a1_snapshot':
            require(e['used_for_control'] is False and e['begin_row']==0 and e['rows']==896,
                    'snapshot feedback or shape')
            data=bytes.fromhex(e['hex'])
            require(len(data)==14336 and e['cycle'] not in snapshots,'snapshot size/uniqueness')
            snapshots[e['cycle']]=data; snapshot_tags[e['cycle']]=e['counter_tag']
        elif name=='observer_spad_write':
            require(e['used_for_control'] is False and 0<=e['bank']<4 and 0<=e['row']<4096 and 0<e['mask']<65536,
                    'SRAM write observation shape/feedback')
            index=bisect_right(cycles,e['cycle'])-1
            require(index>=0,'SRAM write before first stage')
            sid=ordered[index]
            require(e['cycle']<=idles[sid]['cycle'],'write after actual busy seal')
            writes[sid].append((e['cycle'],e['bank']*4096+e['row'],e['mask']))
            total_writes+=1
        elif name=='rocc_accept' and plans[e['tag']]['stage']:
            commands[plans[e['tag']]['stage']].append(e)
    require(snapshots and total_writes,'missing SRAM observations')
    def live_at(cycle):
        return sorted(q for q,p in query.items() if p['cycle']<=cycle<releases[q]['cycle'])
    def value(data,q):
        slot=query[q]['slot']
        return b''.join(data[(tile*16+slot)*16:(tile*16+slot+1)*16] for tile in range(56))
    def expected(q):
        return memory[0x1000+q*896:0x1000+(q+1)*896]
    def snap_at(cycle):
        require(cycle in snapshots,'missing exact final real-response SRAM snapshot')
        tag=snapshot_tags[cycle]
        require(plans[tag]['funct']==126,'snapshot does not belong to counter response')
        return snapshots[cycle]
    def arows(ids):
        return {tile*16+query[q]['slot'] for q in ids for tile in range(56)}
    groups={e['cycle']:e for e in ctl if e['event']=='c1_group_plan'}
    l2_records=[]; l2_b_halves=set(); resume_count=0; restore_count=0
    cohorts={e['cohort']:e for e in ctl if e['event']=='cohort_start'}
    for sid in ordered:
        stage=starts[sid]; name=stage['name']; stream=[e for e in commands[sid] if e['funct']!=126]
        loops=[e for e in stream if e['funct']==8]
        if name in ('c1_group','consumer_l2'):
            require(loops,'missing actual accepted LoopWS')
            for e in loops:
                require(((e['rs1']>>18)&3)==(2 if mode=='retain' and name=='consumer_l2' else 1) and
                        ((e['rs1']>>16)&3)==0, 'accepted A/B ID differs')
        if name=='resume_c1_config':
            resume_count+=1
            require(mode=='retain' and len(stream)==5 and all(e['funct']==0 for e in stream),
                    'resume must contain exactly five configs')
            # Compare actual C1 configuration encodings with the first admission.
            first_admit=next(s for s in ordered if starts[s]['name']=='admit_inputs')
            original=[(e['rs1'],e['rs2']) for e in commands[first_admit] if e['funct']==0]
            require([(e['rs1'],e['rs2']) for e in stream]==original,'resume C1 config differs from initial')
        if name in ('admit_inputs','restore_inputs'):
            evname='input_load_start' if name=='admit_inputs' else 'restore_start'
            input_plan=next(e for e in ctl if e['event']==evname and e['cycle']==stage['cycle'])
            permitted=arows(input_plan['queries'])
            if name=='restore_inputs':restore_count+=1
        else:permitted=set()
        for c,row,mask in writes[sid]:
            if name in ('admit_inputs','restore_inputs'):
                require(row in permitted,'input load writes wrong live slot')
            elif name in ('consumer_reload','consumer_a2','resume_c1_config'):
                raise AuditError('SRAM write in ACC-only/config stage')
            elif name=='c1_group':
                j=groups[stage['cycle']]['tile_j']
                # All current C1 Loop A pointers are zero: only B loads may write SPAD.
                require(8192-56*j*16<=row<8192 or 16384-56*j*16<=row<16384,
                        'C1 SRAM write outside auto B interval')
            elif name=='consumer_l2':
                abase=8192 if mode=='retain' else 0
                require(abase<=row<abase+896 or 1024<=row<8192 or 9216<=row<16384,
                        'L2 SRAM write outside selected A / auto B')
                if 1024<=row<8192:l2_b_halves.add('low')
                if 9216<=row<16384:l2_b_halves.add('high')
        if name in ('admit_inputs','restore_inputs'):
            # expected_write=0 permits counter equality before loads drain; check
            # next stage's later snapshot, or use the last snapshot <= idle.
            c=max(c for c in snapshots if c<=idles[sid]['cycle'] and c>=stage['cycle'])
            # The independent protocol checker proves busy seal; later consumer
            # snapshots below prove A1 exact values after all loads complete.
        if name=='consumer_l2':
            ids=live_at(stage['cycle'])
            cohort=max((e for e in cohorts.values() if e['cycle']<=stage['cycle']),key=lambda e:e['cycle'])
            # A pad_I masks absent packed cohort rows; only slots below its
            # actual row count would be overwritten by the baseline A id1.
            at_risk=[q for q in ids if query[q]['slot']<cohort['rows']]
            require(ids==live_at(idles[sid]['cycle']),'live set changed inside consumer stage')
            before=max(c for c in snapshots if c<stage['cycle'])
            pre=snap_at(before); post=snap_at(seals[sid]['response_cycle'])
            for q in ids:require(value(pre,q)==expected(q),'live A1 incorrect before consumer')
            touched=sorted({row for _,row,_ in writes[sid] if row in arows(ids)})
            equal=[q for q in ids if value(post,q)==expected(q)]
            after=[g for g in groups.values() if g['cycle']>idles[sid]['cycle'] and set(ids)&set(g['queries'])]
            if mode=='retain':
                require(not any(row<896 for _,row,_ in writes[sid]),'retention consumer overwrites A1 envelope')
                require(equal==ids and not touched,'retention A1 changed across consumer')
                if ids:require(after,'no actual later C1 use of retained inputs')
            elif ids:
                require(equal==[q for q in ids if q not in at_risk] and
                        set(touched)==arows(at_risk),'baseline SRAM overwrite differs from actual packed cohort rows')
                next_restore=next(s for s in ordered if starts[s]['cycle']>idles[sid]['cycle'] and starts[s]['name']=='restore_inputs')
                require(arows(ids)<=set(row for _,row,_ in writes[next_restore]),'baseline restore missing live A1 rows')
                restored_snapshot=min(c for c in snapshots if c>=idles[next_restore]['cycle'])
                restored=snap_at(restored_snapshot)
                require(all(value(restored,q)==expected(q) for q in ids),'baseline restore SRAM value differs')
            l2_records.append({'stage':sid,'cycle':stage['cycle'],'live_queries':ids,
              'would_be_overwritten_by_packed_id1':at_risk,
              'slots':[query[q]['slot'] for q in ids], 'slot_generations':[query[q]['slot_generation'] for q in ids],
              'start_panels':[query[q]['start_panel'] for q in ids], 'rows_written_in_live_A1':len(touched),
              'exact_A1_after':equal, 'later_C1_groups':len(after),
              'cross_scan_queries':[q for q in ids if query[q]['start_panel']%14]})
    active=[e for e in l2_records if e['live_queries']]
    vulnerable=[e for e in active if e['would_be_overwritten_by_packed_id1']]
    require(active and l2_b_halves=={'low','high'},'missing active retention / B parity coverage')
    require(vulnerable,'tiny lacks any genuinely vulnerable live A1 slot')
    require(max(q['slot_generation'] for q in query.values())>1,'slot reuse not covered')
    if mode=='retain':
        require(restore_count==0 and resume_count==len(active),'retention data restore or missing config resume')
        require(not any(e['event']=='restore_start' for e in ctl),'retention unexpectedly reloads A1')
        plans_ret=[e for e in ctl if e['event']=='retention_plan']
        require(len(plans_ret)==len(l2_records) and all(e['a1_reloaded'] is False for e in plans_ret),'retention plan cardinality')
        for e in plans_ret:
            ids=sorted(e['queries'])
            require(ids==live_at(e['cycle']) and e['live_inputs']==len(ids),'retention plan differs from real lifetimes')
    else:require(resume_count==0 and restore_count==len(active),'baseline restore stage cardinality')
    return {'status':'pass','mode':mode,'active_A1_retention_triggers':len(active),
      'genuinely_vulnerable_triggers':len(vulnerable),
      'genuinely_vulnerable_query_instances':sum(len(x['would_be_overwritten_by_packed_id1']) for x in vulnerable),
      'active_query_instances':sum(len(x['live_queries']) for x in active), 'consumer_records':l2_records,
      'auto_B_halves_exercised':sorted(l2_b_halves),'SRAM_write_events':total_writes,
      'SRAM_snapshot_count':len(snapshots),'resume_config_stages':resume_count,'restore_input_stages':restore_count,
      'slot_reuse':True,'checker_sha256':sha(Path(__file__))}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run-dir',type=Path,required=True)
    ap.add_argument('--input-dir',type=Path,required=True);ap.add_argument('--mode',choices=['baseline','retain'],required=True)
    ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    try:r=audit(a.run_dir,a.input_dir,a.mode)
    except (AuditError,AssertionError,ValueError,KeyError,StopIteration,OSError) as e:r={'status':'fail','detail':f'{type(e).__name__}: {e}'}
    a.out.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2));return r['status']!='pass'

if __name__=='__main__':sys.exit(main())
