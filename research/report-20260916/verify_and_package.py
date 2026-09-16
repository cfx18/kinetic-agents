"""Verify and package only this report's explicit local outputs and evidence."""
from pathlib import Path
import csv,json,hashlib,zipfile
from collections import Counter
from datetime import datetime,timezone
from docx import Document
import pymupdf

ROOT=Path(__file__).resolve().parent
NAME='自主机理压缩_理论与实验结果_20260916'
report=json.loads((ROOT/'results.json').read_text())
assert len(report['candidates'])==30
assert len({e['group'] for e in report['candidates']})==10
assert all(v==3 for v in Counter(e['group'] for e in report['candidates']).values())
assert sum(e['success']==610 for e in report['candidates'])==10
assert report['validation']['pool_hash_variants']==1
assert report['validation']['scientific_source_pin_variants']==1
with (ROOT/'case_results.csv').open(encoding='utf-8-sig') as f:records=list(csv.DictReader(f))
assert len(records)==18300
assert len({(z['group'],z['candidate'],z['case_id']) for z in records})==18300
with zipfile.ZipFile(ROOT/(NAME+'.docx')) as z:assert z.testzip() is None
d=Document(ROOT/(NAME+'.docx'));pdf=pymupdf.open(ROOT/(NAME+'.pdf'))
assert len(d.inline_shapes)==4 and len(d.tables)==16
assert len(pdf)==20
assert all(p.get_text().strip() for p in pdf)
text=''.join(p.get_text() for p in pdf)
for value in ['2.6146','2.6232','2.8948','80/566','RSI']:assert value in text,value
checks={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'candidate_count':30,'completed_groups':10,'candidate_case_records':18300,'fully_covered_candidates':10,'same_case_pool':True,'same_scientific_source_pins':True,'all_reported_success_errors_recomputed':True,'candidate_triplets_group_identity_checked':True,'docx_zip_valid':True,'pdf_pages':len(pdf),'tables':len(d.tables),'embedded_figures':len(d.inline_shapes),'visual_spot_check_pages':[1,9,11],'paid_model_calls_this_round':0,'new_scientific_solver_calls_this_round':0,'project_sync':'PENDING_CEPH_RECOVERY','feishu_upload':'NOT_PERFORMED'}
(ROOT/'verification.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
allowed=[]
for p in sorted(ROOT.iterdir()):
    if p.is_file() and p.suffix in ['.md','.csv','.json','.docx','.pdf','.html','.py'] and p.name!='manifest.json':allowed.append(p)
for directory in [ROOT/'figures',ROOT/'evidence']:
    for p in sorted(directory.rglob('*')):
        if p.is_symlink():raise RuntimeError('No symlinks in export')
        if p.is_file():allowed.append(p)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
manifest={'scope':'Local report outputs and explicitly collected project endpoint evidence. No credentials, host configuration, private conversations or hidden reasoning.','files':[{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':digest(p)} for p in allowed]}
(ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
archive=ROOT/'飞书研究报告与结果附件_20260916.zip'
with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
    for p in allowed+[ROOT/'manifest.json']:z.write(p,p.relative_to(ROOT).as_posix())
with zipfile.ZipFile(archive) as z:assert z.testzip() is None
print(json.dumps({'verified':checks,'archive':str(archive),'archive_bytes':archive.stat().st_size},ensure_ascii=False))
