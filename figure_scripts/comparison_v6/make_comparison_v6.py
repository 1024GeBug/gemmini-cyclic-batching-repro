"""Audited one-trace latency comparison; scientific-figure-suite 0.9.0.

Source data are restricted to comparison_v6/{summary.json,paired_queries.csv,
policy_summary.csv,manifest.json}. No uncertainty or tail inference is made.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import platform
import statistics
import sys
from pathlib import Path
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
DATA=ROOT/'work/consumer_gate/comparison_v6'
SIZE_CM=(18.,6.8)
POLICIES=['on','off','closed_j4_immediate','closed_full_j4_immediate']
LABELS=['ON','OFF','两块 J4 即时','整轮 J4 即时']
INTERVALS=['admission_wait','c1_span','cohort_wait','consumer_span','delivery_span']
LEGEND=['准入等待','准入→C1通知跨度','消费者等待','消费者跨度','交付跨度']
COLORS=['#2179AA','#A5B9C8','#7855A1','#E5A052','#293744']
EXPECTED_SOURCE_HASHES={
 'summary.json':'6266e571262e4be521550dec79aa2b04529ed1f33743b8921ece5428b76cf242',
 'paired_queries.csv':'c9d9565eef0d0d09d4184cc31e008591b7d7ee53d0bbe06074bb9264f76c671c',
 'policy_summary.csv':'d311ef4aad87e26c2908d75f68f0ff08dd4376acd079de80710301e3705388e7',
 'manifest.json':'23a34679452c01a190781156eec71119e4d3becfe046cd505e77970ddcc192ac'}

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def require(ok,message):
    if not ok:raise ValueError(message)

def dump(name,obj):
    (HERE/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def module(suite,filename):
    sys.path.insert(0,str(suite/'scripts'))
    spec=importlib.util.spec_from_file_location('comparison_v6_'+Path(filename).stem,suite/'scripts'/filename)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

def verify():
    files=[DATA/n for n in ['summary.json','paired_queries.csv','policy_summary.csv','manifest.json']]
    require(all(p.is_file() for p in files),'Four approved source files required')
    before={p.name:sha(p) for p in files}
    require(before==EXPECTED_SOURCE_HASHES,'Approved figure source hashes changed; revise caption/claims before rerendering')
    summary=json.loads(files[0].read_text(encoding='utf-8'))
    manifest=json.loads(files[3].read_text(encoding='utf-8'))
    require(summary['status']=='audited_diagnostic_only','Diagnostic is not accepted')
    require(summary['inference_unit']=='one_diagnostic_workload','Inference unit differs')
    require(summary['confidence_interval_claimed'] is False,'Unexpected inference scope')
    require(all(manifest['verified'].values()),'Comparison source verification gate failed')
    for path in files[:3]:
        require(manifest['outputs'][path.name]['sha256']==before[path.name],f'Hash mismatch: {path.name}')
    require(summary['best_closed']=='closed_full_j4_immediate','Best measured closed policy changed')
    with files[2].open(encoding='utf-8',newline='') as stream:
        table={row['policy']:row for row in csv.DictReader(stream)}
    selected={}
    arrivals=set()
    for name in POLICIES:
        p=summary['policies'][name]
        require(p['status']=='pass' and p['queries']==32,f'{name}: not 32 audited queries')
        rows=sorted(summary['query_records'][name],key=lambda r:r['query'])
        require([r['query'] for r in rows]==list(range(32)),f'{name}: query IDs differ')
        arrivals.add(tuple(r['arrival'] for r in rows))
        for r in rows:
            expected=[r['admitted']-r['arrival'],r['c1_notified']-r['admitted'],
                      r['consumer_start']-r['c1_notified'],r['c2_notified']-r['consumer_start'],
                      r['delivery_end']-r['c2_notified']]
            require(all(v>=0 for v in expected),f'{name}: negative interval')
            require([r[k] for k in INTERVALS]==expected,f'{name}: interval endpoint mismatch')
            require(sum(expected)==r['latency']==r['delivery_end']-r['arrival'],f'{name}: final endpoint mismatch')
        means={k:statistics.mean(r[k] for r in rows) for k in INTERVALS}
        latency=statistics.mean(r['latency'] for r in rows)
        require(sum(means.values())==latency==p['mean_latency_cycles'],f'{name}: mean sum differs')
        require(float(table[name]['mean_latency_cycles'])==latency,f'{name}: CSV latency mismatch')
        for k in INTERVALS:
            require(means[k]==p['mean_interval_cycles'][k]==float(table[name]['mean_'+k]),f'{name}: {k} mean differs')
        selected[name]={'means':means,'latency':latency,'rows':rows}
    require(len(arrivals)==1,'External arrival vector differs')
    with files[1].open(encoding='utf-8',newline='') as stream:
        pairs=sorted([r for r in csv.DictReader(stream) if r['baseline']=='closed_full_j4_immediate'],key=lambda r:int(r['query']))
    require([int(r['query']) for r in pairs]==list(range(32)),'Missing paired requests')
    deltas=[]
    for r,on,closed in zip(pairs,selected['on']['rows'],selected['closed_full_j4_immediate']['rows']):
        delta=on['latency']-closed['latency']
        require(int(r['on_latency'])==on['latency'] and int(r['baseline_latency'])==closed['latency'],'Paired latency differs')
        require(int(r['arrival'])==on['arrival']==closed['arrival'],'Paired arrival differs')
        require(int(r['on_minus_baseline'])==delta==-int(r['net_saving']),'Paired delta differs')
        require(int(r['S_saved_wait'])-int(r['H_extra_post_admission'])==-delta,'S-H mismatch')
        deltas.append(delta)
    comparison=summary['paired_comparisons']['all_closed_best']
    counts={'faster':sum(v<0 for v in deltas),'slower':sum(v>0 for v in deltas),'tied':sum(v==0 for v in deltas)}
    require(counts['faster']==comparison['on_queries_faster'] and counts['slower']==comparison['on_queries_slower'],'Pair counts differ')
    require(statistics.mean(deltas)==selected['on']['latency']-selected['closed_full_j4_immediate']['latency'],'Delta mean differs')
    require({p.name:sha(p) for p in files}==before,'Source files changed during verification')
    dump('source_verification.json',{'status':'PASS','sources':before,'checks':[
         'manifest output SHA256 equality','all source verification booleans true',
         '32 aligned external arrivals and IDs','five nonnegative lifecycle intervals sum to final latency',
         'per-query means equal JSON and CSV','32 paired differences and S-H equality'],
         'inference_unit':'one_diagnostic_workload','counts':counts,
         'mean_on_minus_closed_cycles':statistics.mean(deltas)})
    dump('plotted_values.json',{'source_hashes':before,'policies':{p:{'mean_cycles':selected[p]['means'],
         'latency_cycles':selected[p]['latency']} for p in POLICIES},
         'query_order':list(range(32)),'on_minus_closed_full_j4_cycles':deltas,'counts':counts})
    return selected,deltas,counts,files,before

def render(suite):
    data,deltas,counts,files,before=verify()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    from matplotlib.text import Text
    from matplotlib.patches import Rectangle
    from pypdf import PdfReader
    fonts=[Path('C:/Windows/Fonts/arial.ttf'),Path('C:/Windows/Fonts/simhei.ttf')]
    for path in fonts:
        require(path.is_file(),f'Font path missing: {path}')
        fm.fontManager.addfont(str(path))
    plt.rcParams.update({'font.family':['Arial','SimHei'],'font.size':8.2,
        'axes.unicode_minus':False,'axes.linewidth':.6,'xtick.major.width':.6,'ytick.major.width':.6,
        'pdf.fonttype':42,'svg.fonttype':'none','svg.hashsalt':'comparison_v6'})
    fig=plt.figure(figsize=(SIZE_CM[0]/2.54,SIZE_CM[1]/2.54),dpi=150)
    a=fig.add_axes([2.05/18,1.88/6.8,6.58/18,3.75/6.8])
    b=fig.add_axes([10.14/18,1.88/6.8,7.53/18,3.75/6.8])
    for ax in [a,b]:
        ax.spines[['top','right']].set_visible(False)
        ax.tick_params(labelsize=8.0,length=2.5,pad=3)
        ax.set_axisbelow(True)
    bottoms=[0.0]*4
    y=[3,2,1,0]
    for key,color in zip(INTERVALS,COLORS):
        values=[data[p]['means'][key]/1000 for p in POLICIES]
        a.barh(y,values,left=bottoms,height=.58,color=color,edgecolor='none',zorder=2)
        bottoms=[lo+v for lo,v in zip(bottoms,values)]
    a.set(yticks=y,yticklabels=LABELS,xlim=(0,146),ylim=(-.62,3.62),xticks=[0,40,80,120])
    a.grid(axis='x',color='#DCE1E5',lw=.5,zorder=0)
    a.set_xlabel('平均最终交付延迟（千周期）',fontsize=8.2,labelpad=5)
    for yy,total in zip(y,bottoms):
        a.text(total+1.1,yy,f'{total:.1f}',va='center',ha='left',fontsize=8.0)
    vals=[v/1000 for v in deltas]
    b.axhline(0,color='#1F2B35',lw=.8,zorder=1)
    b.vlines(range(32),0,vals,colors='#52697D',lw=.85,zorder=2)
    positive=[i for i,v in enumerate(vals) if v>0]
    negative=[i for i,v in enumerate(vals) if v<0]
    b.scatter(positive,[vals[i] for i in positive],s=11,color='#334D63',edgecolors='none',zorder=3)
    b.scatter(negative,[vals[i] for i in negative],s=13,facecolors='white',edgecolors='#334D63',linewidths=.8,zorder=3)
    b.set(xlim=(-1,32),ylim=(-25,60),xticks=[0,8,16,24,31],yticks=[-20,0,20,40,60])
    b.grid(axis='y',color='#DCE1E5',lw=.5,zorder=0)
    b.set_xlabel('请求编号（到达顺序）',fontsize=8.2,labelpad=5)
    b.set_ylabel('ON − 整轮 J4（千周期）',fontsize=8.2,labelpad=5)
    b.text(.98,.96,f'{counts["faster"]}快，{counts["slower"]}慢；正值更慢',
           transform=b.transAxes,ha='right',va='top',fontsize=8.0)
    fig.text(.015,6.38/6.8,'(a) 平均延迟的五区间',fontsize=9.1,weight='bold',va='center')
    fig.text(9.47/18,6.38/6.8,'(b) ON − 整轮 J4 即时：32请求配对',fontsize=9.1,weight='bold',va='center')
    legend_positions=[(.25,.67),(3.00,.67),(6.75,.67),(.25,.20),(3.00,.20)]
    legend_text=[]
    for (x,y0),label,color in zip(legend_positions,LEGEND,COLORS):
        fig.patches.append(Rectangle((x/18,y0/6.8),.36/18,.20/6.8,transform=fig.transFigure,
                                     facecolor=color,edgecolor='none'))
        legend_text.append(fig.text((x+.49)/18,(y0+.1)/6.8,label,va='center',fontsize=8.0))
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    roles={}
    for panel,ax in [('a',a),('b',b)]:
        roles[id(ax.xaxis.label)]=('x_label',panel)
        roles[id(ax.yaxis.label)]=('y_label',panel)
        for item in ax.get_xticklabels()+ax.get_yticklabels():roles[id(item)]=('tick_label',panel)
        for item in ax.texts:roles[id(item)]=('annotation',panel)
    for item in legend_text:roles[id(item)]=('legend_label','a')
    elements=[]
    for item in fig.findobj(Text):
        if not item.get_visible() or not item.get_text().strip():continue
        box=item.get_window_extent(renderer)
        if box.width<=0 or box.height<=0:continue
        role,panel=roles.get(id(item),('panel_title','figure'))
        elements.append({'text':item.get_text(),'role':role,'panel_id':panel,
             'bbox':[box.x0/fig.bbox.width,box.y0/fig.bbox.height,box.width/fig.bbox.width,box.height/fig.bbox.height],
             'font_size_pt':item.get_fontsize(),'rotation':float(item.get_rotation()),'anchor':item.get_ha(),
             'priority':100,'can_wrap':False,'can_abbreviate':False,'in_axes_bounds':True,'overlap_checked':True})
    layout={'figure_id':'comparison_v6','layout_engine':'manual_axes','manual_axes_fallback':True,
       'dimensions_cm':list(SIZE_CM),'text_elements':elements,
       'panels':[{'id':'a','bbox':list(a.get_position().bounds),'plot_type':'horizontal stacked means'},
                 {'id':'b','bbox':list(b.get_position().bounds),'plot_type':'paired signed lollipops'}],
       'semantic_color_map':dict(zip(INTERVALS,COLORS))}
    dump('layout.json',layout)
    text_audit=module(suite,'audit_text_layout.py').audit_layout(layout,
        SimpleNamespace(text_profile='',bounds_tolerance=.0001,min_gap=.001,min_font_size_pt=8,
                        max_rotation=90,max_title_chars=80,max_colorbar_title_chars=36,max_direct_labels=12))
    dump('text_layout_audit.json',text_audit)
    require(text_audit['text_layout_report']['result']=='PASS','Text layout gate failed')
    for ext in ['pdf','svg','png']:
        meta={'Creator':'scientific-figure-suite / audited comparison_v6'}
        if ext=='pdf':meta.update(CreationDate=None,ModDate=None)
        if ext=='svg':meta.update(Date=None)
        fig.savefig(HERE/f'comparison_v6.{ext}',dpi=300,facecolor='white',metadata=meta)
    plt.close(fig)
    pdf=PdfReader(HERE/'comparison_v6.pdf');require(len(pdf.pages)==1,'Expected one-page figure')
    embedded=[]
    for key,raw in pdf.pages[0]['/Resources']['/Font'].get_object().items():
        top=raw.get_object()
        for leaf in top.get('/DescendantFonts',[raw]):
            font=leaf.get_object();desc=font.get('/FontDescriptor');desc=desc.get_object() if desc else {}
            streams=[s for s in ['/FontFile','/FontFile2','/FontFile3'] if s in desc and len(desc[s].get_object().get_data())>0]
            require(bool(streams) and font.get('/Subtype')!='/Type3','Unembedded or Type3 font')
            embedded.append({'font':str(font.get('/BaseFont')),'embedded':True,'subtype':str(font.get('/Subtype'))})
    dump('font_audit.json',{'status':'PASS','fonts':embedded,'pdf_sha256':sha(HERE/'comparison_v6.pdf')})
    audit=module(suite,'audit_render_quality.py');checks=[];errors=[];warnings=[]
    for ext in ['pdf','svg','png']:
        check,err,warn=audit.audit_file(HERE/f'comparison_v6.{ext}',
              SimpleNamespace(min_width=2125,min_height=803,min_variance=1,require_svg_text=True,baseline_dir=''))
        checks.append(check);errors.extend(err);warnings.extend(warn)
    dump('render_audit.json',{'status':'FAIL' if errors else 'PASS','files':checks,'errors':errors,'warnings':warnings})
    require(not errors,'Render audit failed')
    dump('multipanel_audit.json',{'status':'PASS','same_row_axes_top_bottom_equal':
          bool(a.get_position().y0==b.get_position().y0 and a.get_position().y1==b.get_position().y1),
          'color_identity':'five phases in panel a; panel b uses neutral sign position/marker fill',
          'scale_policy':'panel a starts at zero; panel b is signed difference with zero reference',
          'tiny_intervals':'no minimum-width inflation; cohort/delivery retain true linear widths'})
    dump('environment.json',{'python':platform.python_version(),'matplotlib':matplotlib.__version__,
          'fonts':[{'path':str(p),'sha256':sha(p)} for p in fonts]})
    claims=[
      {'element':'a_total_labels','claim':'Mean final latencies are 115615.625,130357.84375,130051.8125,96898 cycles.',
       'evidence':'summary.json:policies selected in POLICIES; policy_summary.csv; plotted_values.json',
       'status':'observed_one_trace','display':'one decimal in thousands of cycles'},
      {'element':'a_stack','claim':'Five nonnegative, nonoverlapping lifecycle intervals sum exactly to final latency.',
       'evidence':'summary.json:query_records and policies.mean_interval_cycles',
       'status':'computed_from_recorded_endpoints','constraint':'c1_span includes interleaved consumers/control; not pure L1 work'},
      {'element':'a_on_vs_full','claim':'ON has lower admission wait and longer post-admission span than full J4 immediate.',
       'evidence':'summary.json:paired_comparisons.all_closed_best',
       'status':'descriptive_paired_difference','constraint':'Not an independent causal micro-cost decomposition'},
      {'element':'b_signed_pairs','claim':'ON minus full J4 immediate is positive for26,negative for6,tied for0 of32 queries.',
       'evidence':'paired_queries.csv filtered baseline=closed_full_j4_immediate',
       'status':'computed_paired_counts','mean_difference_cycles':statistics.mean(deltas)},
      {'element':'b_zero_line','claim':'Positive means ON later final delivery; negative means earlier.',
       'evidence':'on_minus_baseline=on_latency-baseline_latency; source_verification.json',
       'status':'definition'},
      {'element':'scope','claim':'This figure describes one32-query arrival trace with no CI,p99 or population inference.',
       'evidence':'summary.json:inference_unit,confidence_interval_claimed; manifest.json',
       'status':'scope_constraint'}]
    (HERE/'visual_claim_ledger.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in claims),encoding='utf-8')
    require({p.name:sha(p) for p in files}==before,'Source changed during rendering')
    def record(path):return {'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'bytes':path.stat().st_size}
    dump('manifest.json',{'figure_id':'comparison_v6','skill':'scientific-figure-suite@0.9.0',
         'DATA_STATUS':'PROVIDED','STYLE_STATUS':'ESTIMATED','REPRODUCIBILITY_STATUS':'CODED',
         'EXPORT_STATUS':'READY','INTEGRITY_STATUS':'PASS','inference_unit':'one_diagnostic_workload',
         'uncertainty':'no confidence interval or p99 inference','sources':[record(p) for p in files],
         'files':[record(p) for p in sorted(HERE.iterdir()) if p.is_file() and p.name!='manifest.json']})
    print('COMPARISON_V6_AUTOMATED_PASS')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--suite',type=Path,default=Path('C:/Users/JiapengGuan/.codex/skills/scientific-figure-suite'))
    render(parser.parse_args().suite)
