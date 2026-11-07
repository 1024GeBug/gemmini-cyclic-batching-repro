"""Inject observer/ISA evidence faults in memory; never edit simulation logs."""
from pathlib import Path
import hashlib, json, time
import check_retention_v2 as checker
from raw_tl import AuditError, events

ROOT=Path(__file__).resolve().parent.parent
RUN=Path('/home/Gemguan/work/codex_gemmini_paper_20261103/consumer_gate/a_retention_v1/tiny4_on_j4_retention_v1')
OUT=ROOT/'fault_checks_v1.json'
FIXTURE=ROOT.parent/'online_inputs/diagnostic_q896_n128_v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    assert not OUT.exists(),'exclusive fault report'
    frozen={str(p):sha(p) for p in [RUN/'engine_events.jsonl',RUN/'driver_events.jsonl',Path(checker.__file__)]}
    registration={'time_unix':time.time(),'scope':'offline evidence mutation only; no simulation or source edit',
      'frozen':frozen,'faults':['A1_snapshot_corruption','protected_A1_write','L2_A_ID_wrong','resume_stride_wrong','final_snapshot_missing']}
    pre=ROOT/'fault_checks_preregistration_v1.json';assert not pre.exists();pre.write_text(json.dumps(registration,indent=2)+'\n')
    ctl=list(events(RUN/'driver_events.jsonl'))
    report=json.loads((RUN/'independent_retention_audit.json').read_text())
    target=next(x for x in report['consumer_records'] if x['would_be_overwritten_by_packed_id1'])
    sid=target['stage'];start=target['cycle']
    end=next(e['response_cycle'] for e in ctl if e['event']=='sealed_group_complete' and e['stage']==sid)
    loop_tag=next(e['tag'] for e in ctl if e['event']=='command_plan' and e['stage']==sid and e['funct']==8)
    resume=next(e['stage'] for e in ctl if e['event']=='stage_start' and e['name']=='resume_c1_config')
    ld_tag=next(e['tag'] for e in ctl if e['event']=='command_plan' and e['stage']==resume and e['funct']==0 and e['rs1']&3==1)
    tests=[]
    for fault in registration['faults']:
        edits=[]
        def changed(path):
            for e in events(path):
                if path.name=='engine_events.jsonl':
                    if fault=='A1_snapshot_corruption' and e['event']=='observer_a1_snapshot' and e['cycle']==end:
                        e=dict(e);data=bytearray.fromhex(e['hex']);data[0]^=1;e['hex']=data.hex();edits.append('flip first byte of q3 slot0 after vulnerable consumer')
                    if fault=='protected_A1_write' and not edits and e['cycle']>=start+1:
                        edits.append('inject physical bank0 row0 write during vulnerable consumer')
                        yield {'event':'observer_spad_write','cycle':start+1,'bank':0,'row':0,'mask':65535,'used_for_control':False}
                    if fault=='L2_A_ID_wrong' and e['event']=='rocc_accept' and e['tag']==loop_tag:
                        e=dict(e);e['rs1']=(e['rs1']&~(3<<18))|(1<<18);edits.append('set accepted L2 A id1')
                    if fault=='resume_stride_wrong' and e['event']=='rocc_accept' and e['tag']==ld_tag:
                        e=dict(e);e['rs2']+=16;edits.append('change accepted resume LD stride')
                    if fault=='final_snapshot_missing' and e['event']=='observer_a1_snapshot' and e['cycle']==end:
                        edits.append('remove exact final real-response SRAM snapshot');continue
                yield e
        checker.events=changed
        try:
            checker.audit(RUN,FIXTURE,'retain')
            rejected=False;reason='unexpected pass'
        except (AuditError,ValueError,KeyError,StopIteration) as e:
            rejected=True;reason=f'{type(e).__name__}: {e}'
        finally:checker.events=events
        tests.append({'fault':fault,'edits':edits,'rejected':rejected,'reason':reason})
    out={'status':'pass' if all(t['rejected'] and len(t['edits'])==1 for t in tests) else 'fail',
         'tests':tests,'inputs_unchanged':all(sha(Path(p))==digest for p,digest in frozen.items()),
         'script_sha256':sha(Path(__file__)),'preregistration_sha256':sha(pre)}
    OUT.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
    assert out['status']=='pass' and out['inputs_unchanged']

if __name__=='__main__':main()
