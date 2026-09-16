"""Recompute report tables from archived endpoint evidence; no simulation/model calls."""
from pathlib import Path
import json, math, csv, statistics, hashlib, subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parent
FIG=ROOT/'figures'; FIG.mkdir(exist_ok=True)
plt.rcParams.update({'font.family':'Noto Sans CJK JP','axes.unicode_minus':False,'font.size':10,'figure.dpi':160})

def read(p):return json.loads(p.read_text())
def save(name,data): (ROOT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])
def fmt(x):return '—' if x is None else f'{x:.4f}'

entries=[]; unused=[]; parent=None; all_rows=[]; pergroup=[]; findings=[]; jobs=[]; sources=[]
for d in sorted((ROOT/'evidence').iterdir()):
    if not d.is_dir():continue
    m=read(d/'manifest.json');c=read(d/'evaluation_contract.json');r=read(d/'evaluation_result.json')
    # The repaired bulk evaluator reuses a shared code release. It is not the
    # controller identity; obtain that from the corresponding original endpoint.
    identity_contract=c
    if d.name.endswith('_bulk_v1'):
        identity_contract=read(d.parent/d.name.removesuffix('_bulk_v1')/'evaluation_contract.json')
    run=identity_contract['local_release'].split('/runs/')[-1].split('/')[0]
    if (d.parent/(d.name+'_bulk_v1')).is_dir():
        unused.append({'directory':d.name,'reason':'原始格式接入失败；使用对应 bulk_v1 修复评测，保留失败记录'})
        if parent is None:parent=r['scores']['parent'];parent_rows=[z for z in r['rows'] if z['label']=='parent']
        continue
    if parent is None and 'parent' in r['scores']:
        parent=r['scores']['parent'];parent_rows=[z for z in r['rows'] if z['label']=='parent']
    model='Kimi' if 'kimi' in run else 'DeepSeek' if 'deepseek' in run else 'GPT'
    prompt='专家' if 'expert' in run else '普通'
    mode='Team' if 'team' in run else 'Solo'
    tag=f'{model} {prompt} {mode}'
    cases=m['development']+m['recheck'];bycase={z['case_id']:z for z in cases}
    assert len(bycase)==610 and len(m['development'])==491 and len(m['recheck'])==119
    canon=hashlib.sha256(json.dumps(cases,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    sources.append({'group':tag,'run':run,'endpoint':d.name,'job':r['job_id'],'pool_canonical_sha256':canon,'parent_sha256':m['selected'].get('parent',{}).get('sha256'),'source_pins':m['source_pins'],'solver_version':m['solver_version'],'remote':c['remote_root'],'evaluator_source_sha256':c['evaluator_source_sha256'],'release_sha256':c['release_sha256'],'status':r['status'],'finished_utc':datetime.fromtimestamp(r['finished'],timezone.utc).isoformat()})
    jobs.append(str(r['job_id']))
    rows_by_label=defaultdict(list)
    for z in r['rows']:rows_by_label[z['label']].append(z)
    for label,s in r['scores'].items():
        if label=='parent':continue
        a=s['all610'];sel=m['selected'][label];rr=rows_by_label[label]
        assert len(rr)==610 and len({z['case_id'] for z in rr})==610
        ok=[z for z in rr if z['status']=='success'];vals=[abs(z['signed_sigma']) for z in ok]
        assert len(ok)==a['success']
        assert math.isclose(statistics.mean(vals),a['successful_subset_mean_abs_sigma'],abs_tol=1e-10)
        mismatch=[]
        for z in ok:
            obs=bycase[z['case_id']]['observation'];v=z['prediction']
            err=(math.log(v/obs['value']) if obs['uncertainty_kind']=='one_sigma_log_multiplicative' else v-obs['value'])/obs['uncertainty']
            if not math.isclose(err,z['signed_sigma'],abs_tol=1e-8):mismatch.append(z['case_id'])
        assert not mismatch,(tag,label,mismatch)
        e={'group':tag,'model':model,'prompt':prompt,'mode':mode,'label':label,'species':sel['species_count'],'reactions':sel['reaction_count'],'species_reduction':1-sel['species_count']/111,'reaction_reduction':1-sel['reaction_count']/784,'success':a['success'],'coverage':a['coverage'],'full_E':a['full_pool_mean_abs_sigma'],'subset_E':a['successful_subset_mean_abs_sigma'],'statuses':a['statuses'],'sha256':sel['sha256'],'endpoint':d.name,'run':run,'source':f'evidence/{d.name}/evaluation_result.json','groups':s['fuel_observable_groups'],'development':s['development'],'recheck':s['recheck'],'median':float(np.median(vals)),'p95':float(np.percentile(vals,95)),'max':max(vals)}
        entries.append(e)
        for z in rr:
            all_rows.append({'group':tag,'candidate':label,'species':e['species'],'reactions':e['reactions'],'case_id':z['case_id'],'fuel':bycase[z['case_id']]['fuel_label'],'observable':bycase[z['case_id']]['operator']['family'],'split':z['split'],'status':z['status'],'prediction':z.get('prediction'),'signed_sigma':z.get('signed_sigma'),'source':e['source']})
        for g,v in e['groups'].items():pergroup.append({'group':tag,'candidate':label,'species':e['species'],'reactions':e['reactions'],'fuel_observable':g,'count':v['case_count'],'success':v['success'],'E_full_group':v['full_pool_mean_abs_sigma'],'E_success_subset':v['successful_subset_mean_abs_sigma']})
        invalid=Counter(bycase[z['case_id']]['fuel_label'] for z in rr if z['status']=='input_incompatible')
        bad=Counter(bycase[z['case_id']]['fuel_label'] for z in rr if z['status'] not in ['input_incompatible','success'])
        findings.append({'group':tag,'species':e['species'],'reactions':e['reactions'],'input_incompatible_by_fuel':dict(invalid),'other_failure_by_fuel':dict(bad)})

order={x:i for i,x in enumerate(['GPT 普通 Solo','GPT 普通 Team','GPT 专家 Solo','GPT 专家 Team','Kimi 普通 Solo','Kimi 普通 Team','Kimi 专家 Solo','Kimi 专家 Team','DeepSeek 普通 Solo','DeepSeek 普通 Team','DeepSeek 专家 Solo','DeepSeek 专家 Team'])}
entries.sort(key=lambda e:(order[e['group']],e['label']))
base=parent['all610']['full_pool_mean_abs_sigma']
full=[e for e in entries if e['full_E'] is not None]
for e in full:
    e['E_change_percent']=100*(e['full_E']/base-1)
    for attr in ['species','reactions']:
        e[attr+'_pareto']=not any(x[attr]<=e[attr] and x['full_E']<=e['full_E'] and (x[attr]<e[attr] or x['full_E']<e['full_E']) for x in full)
save('results.json',{'collected_at_utc':read(ROOT/'evidence/index.json')['collected_at_utc'],'parent':parent,'candidates':entries,'sources':sources,'excluded_superseded_endpoints':unused,'failure_groups':findings,'validation':{'candidates':len(entries),'case_records':len(all_rows),'full_coverage_candidates':len(full),'pool_hash_variants':len({x['pool_canonical_sha256'] for x in sources}),'scientific_source_pin_variants':len({json.dumps(x['source_pins'],sort_keys=True) for x in sources}),'all_candidate_counts_and_sigma_values_recomputed':True}})
for filename,data in [('candidate_results.csv',[{k:v for k,v in e.items() if not isinstance(v,(dict,list))} for e in entries]),('case_results.csv',all_rows),('fuel_observable_results.csv',pergroup)]:
    keys=list(dict.fromkeys(k for row in data for k in row))
    with (ROOT/filename).open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,keys);w.writeheader();w.writerows(data)

blocks={}
blocks['ALL_RESULTS']=table(['实验组','物种/反应','物种/反应压缩率','成功/610','全池 E','未成功：输入/未解析/数值'],[[e['group'],f"{e['species']}/{e['reactions']}",f"{100*e['species_reduction']:.1f}% / {100*e['reaction_reduction']:.1f}%",e['success'],fmt(e['full_E']),'/'.join(str(e['statuses'].get(k,0)) for k in ['input_incompatible','unresolved_observable','numerical_failure'])] for e in entries])
blocks['FULL_RESULTS']=table(['实验组','物种/反应','全池 E','相对父机理 E 变化','物种压缩率','反应压缩率'],[[e['group'],f"{e['species']}/{e['reactions']}",fmt(e['full_E']),f"{e['E_change_percent']:+.2f}%",f"{100*e['species_reduction']:.2f}%",f"{100*e['reaction_reduction']:.2f}%"] for e in sorted(full,key=lambda e:e['full_E'])])
blocks['COVERAGE_SUMMARY']=table(['模型 + Harness','任务','模式','完整候选 / 已评分候选','状态'],[[('GPT + Codex' if tag.startswith('GPT') else 'Kimi + Kimi Code' if tag.startswith('Kimi') else 'DeepSeek + Codex'),tag.split()[1],tag.split()[2],f"{sum(e['success']==610 for e in entries if e['group']==tag)} / {sum(e['group']==tag for e in entries)}",'终点评分完成' if any(e['group']==tag for e in entries) else '本轮无终点结果；主进程等待 Ceph'] for tag in order])
blocks['SPLIT_RESULTS']=table(['实验组','物种/反应','原开发 491：E','历史回查 119：E'],[[e['group'],f"{e['species']}/{e['reactions']}",fmt(e['development']['full_pool_mean_abs_sigma']),fmt(e['recheck']['full_pool_mean_abs_sigma'])] for e in full])
blocks['POOL_FUELS']=table(['燃料标签','工况数'],sorted(Counter(c['fuel_label'] for c in cases).items(),key=lambda x:-x[1]))
blocks['PROVENANCE']=table(['实验组','运行目录','远端评测目录 / Job'],[[s['group'],s['run'],s['endpoint']+' / '+str(s['job'])] for s in sorted(sources,key=lambda x:order[x['group']])])
gains=[]
for e in full:
    diffs=[]
    for g,v in e['groups'].items():
        d=v['full_pool_mean_abs_sigma']-parent['fuel_observable_groups'][g]['full_pool_mean_abs_sigma']
        diffs.append((d*v['case_count']/610,d,g,v['case_count']))
    gains.append({'group':e['group'],'species':e['species'],'best_contributions':sorted(diffs)[:3],'worst_contributions':sorted(diffs,reverse=True)[:3]})
save('error_contributions.json',gains)

colors={'GPT':'#3366cc','Kimi':'#ef8d22','DeepSeek':'#0b927c'}
fig,axes=plt.subplots(1,2,figsize=(12,5),layout='constrained')
for ax,attr,title in zip(axes,['species','reaction'],['物种压缩率—全池误差','反应压缩率—全池误差']):
    for e in full:
        x=100*e[attr+'_reduction'];y=e['full_E']
        ax.scatter(x,y,c=colors[e['model']],marker='o' if e['mode']=='Solo' else '^',s=55,edgecolor='white',linewidth=.5)
        ax.annotate(f"{e['species']}/{e['reactions']}",(x,y),xytext=(3,4),textcoords='offset points',fontsize=7)
    ax.scatter(0,base,c='black',marker='s',s=45,label='USC-II 父机理')
    ax.axhline(base,color='gray',lw=1,ls='--')
    ax.set(title=title,xlabel='压缩率（%），越大越好',ylabel='平均绝对标准化误差 E，越小越好');ax.grid(alpha=.15)
from matplotlib.lines import Line2D
fig.legend(handles=[Line2D([],[],color=col,marker='o',ls='',label=key) for key,col in colors.items()]+[Line2D([],[],color='gray',marker='^',ls='',label='Team')],loc='outside lower center',ncol=4)
fig.savefig(FIG/'01_full_pool_frontier.png',dpi=190,bbox_inches='tight');plt.close(fig)

fig,ax=plt.subplots(figsize=(11,12),layout='constrained')
labels=[f"{e['group']} · {e['species']}/{e['reactions']}" for e in entries]
y=np.arange(len(entries));left=np.zeros(len(entries))
for key,label,col in [('success','成功求解','#31a78a'),('input_incompatible','输入物种缺失','#e7a43b'),('unresolved_observable','观测量未解析','#9b78be'),('numerical_failure','数值失败','#d26464')]:
    v=np.array([e['statuses'].get(key,0) for e in entries]);ax.barh(y,v,left=left,color=col,label=label);left+=v
ax.set_yticks(y,labels,fontsize=9);ax.invert_yaxis();ax.set(xlim=(0,635),xlabel='固定 610 个工况，成功求解不等于误差合格',title='全部候选的覆盖率与失败构成')
for i,e in enumerate(entries):ax.text(612,i,str(e['success']),va='center',fontsize=8)
ax.legend(loc='lower center',bbox_to_anchor=(.45,-.075),ncol=4,fontsize=9)
fig.savefig(FIG/'02_coverage_and_failures.png',dpi=170,bbox_inches='tight');plt.close(fig)

chosen=[e for e in full if (e['group'],e['species']) in [('GPT 普通 Solo',87),('GPT 普通 Solo',81),('Kimi 专家 Team',70),('DeepSeek 专家 Solo',89),('DeepSeek 普通 Team',93)]]
groups=list(parent['fuel_observable_groups'])
data=np.array([[e['groups'][g]['full_pool_mean_abs_sigma']-parent['fuel_observable_groups'][g]['full_pool_mean_abs_sigma'] for e in chosen] for g in groups])
fig,ax=plt.subplots(figsize=(10,12),layout='constrained')
im=ax.imshow(data,aspect='auto',cmap='RdBu_r',vmin=-2,vmax=2)
ax.set_yticks(range(len(groups)),[g.replace('ignition_delay','IDT').replace('laminar_flame_speed','LFS') for g in groups],fontsize=8)
ax.set_xticks(range(len(chosen)),[f"{e['group']}\n{e['species']}/{e['reactions']}" for e in chosen],fontsize=9)
for i in range(len(groups)):
    for j in range(len(chosen)):ax.text(j,i,f'{data[i,j]:+.2f}',ha='center',va='center',fontsize=7,color='white' if abs(data[i,j])>1.3 else 'black')
ax.set_title('分组误差变化 ΔE：红色退化，蓝色改善\n完整覆盖候选；色阶截断于 ±2，数字为实际值')
fig.colorbar(im,ax=ax,shrink=.65,label='候选 E − 父机理 E')
fig.savefig(FIG/'03_group_error_deltas.png',dpi=180,bbox_inches='tight');plt.close(fig)

fuel=Counter(c['fuel_label'] for c in cases);names=sorted(fuel,key=fuel.get,reverse=True)
fig,ax=plt.subplots(figsize=(11,4),layout='constrained');bottom=np.zeros(len(names))
for fam,label,col in [('ignition_delay','0D IDT','#376aab'),('laminar_flame_speed','1D PDE / LFS','#32a58c')]:
    cnt=Counter(c['fuel_label'] for c in cases if c['operator']['family']==fam);v=[cnt[k] for k in names];ax.bar(names,v,bottom=bottom,label=label,color=col);bottom+=np.array(v)
ax.tick_params(axis='x',labelrotation=45);ax.set(ylabel='工况点数',title='固定评测池：610 个工况 / 18 个燃料标签');ax.legend()
fig.savefig(FIG/'04_pool_composition.png',dpi=180,bbox_inches='tight');plt.close(fig)

# Independent Slurm allocation accounting, read-only. Never charge search ledger.
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=6','sca2070',f"sacct -n -P -X -j {','.join(jobs)} --format=JobID,JobName,State,ElapsedRaw,AllocCPUS,Start,End"],capture_output=True,text=True,timeout=30)
cost=[]
if p.returncode==0:
    (ROOT/'evidence/sacct_endpoint.txt').write_text(p.stdout)
    for line in p.stdout.splitlines():
        a=line.split('|')
        if len(a)<7:continue
        src=next((s for s in sources if str(s['job'])==a[0]),None)
        cost.append({'group':src['group'] if src else a[1],'job':a[0],'state':a[2],'elapsed_seconds':int(a[3]),'allocated_cores':int(a[4]),'allocated_core_hours':int(a[3])*int(a[4])/3600,'start_slurm_local_UTC_plus_8':a[5],'end_slurm_local_UTC_plus_8':a[6]})
save('endpoint_allocation_costs.json',cost)
blocks['COSTS']=table(['实验组','Job','节点墙钟分钟','分配核时（独立评测）'],[[x['group'],x['job'],f"{x['elapsed_seconds']/60:.2f}",f"{x['allocated_core_hours']:.2f}"] for x in sorted(cost,key=lambda e:order[e['group']])])
save('report_blocks.json',blocks)
print(json.dumps({'candidates':len(entries),'full_candidates':len(full),'pool_hash_variants':len({x['pool_canonical_sha256'] for x in sources}),'pin_variants':len({json.dumps(x['source_pins'],sort_keys=True) for x in sources}),'best_E':min(e['full_E'] for e in full),'cost_rows':len(cost)},ensure_ascii=False))
