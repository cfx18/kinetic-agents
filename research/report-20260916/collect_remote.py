"""Read-only collection of already completed, project-owned endpoint evidence."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
REMOTE = '/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs'
SCRIPT = r'''
import json, pathlib
root=pathlib.Path('/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs')
names=['manifest.json','evaluation_contract.json','evaluation_result.json','progress.json','closed.json','started.json','compute_finished.json']
records=[]
for directory in sorted(root.glob('cfx_astra_*_endpoint*')):
    row={'directory':str(directory),'files':{}}
    for name in names:
        p=directory/name
        if p.is_file():
            row['files'][name]=json.loads(p.read_text())
    records.append(row)
print(json.dumps(records))
'''

def main():
    p = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=6','sca2070','python3 -'], input=SCRIPT, text=True, capture_output=True, timeout=60, check=True)
    rows=json.loads(p.stdout)
    (OUT/'evidence').mkdir(exist_ok=True)
    for row in rows:
        folder=OUT/'evidence'/Path(row['directory']).name
        folder.mkdir(exist_ok=True)
        for name,data in row['files'].items():
            (folder/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    index={'collected_at_utc':datetime.now(timezone.utc).isoformat(),'remote_root':REMOTE,'directories':[{k:v for k,v in r.items() if k!='files'} for r in rows]}
    (OUT/'evidence/index.json').write_text(json.dumps(index,indent=2)+'\n')
    print(json.dumps({'collected_directories':len(rows),'index':str(OUT/'evidence/index.json')}))

if __name__=='__main__':
    main()
