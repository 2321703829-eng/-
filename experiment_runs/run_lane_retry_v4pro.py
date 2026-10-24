"""Prepare and run the human-rejected lane-selection subset with DeepSeek V4 Pro."""
import csv,json,subprocess,sys,os
from pathlib import Path
BASE=Path(__file__).resolve().parent
source=BASE/'width_200/failure_analysis/rejected_width_details.json'
out=BASE/'width_lane_retry_v4pro'
details=json.loads(source.read_text(encoding='utf-8'))['cases']
selected=[c for c in details if c.get('selection_wrong')]
orig=BASE/'width_200/inputs.csv'
rows={r['case_id']:r for r in csv.DictReader(orig.open(encoding='utf-8-sig',newline=''))}
out.mkdir(exist_ok=True)
with (out/'inputs.csv').open('w',encoding='utf-8-sig',newline='') as f:
    fields=list(rows[next(iter(rows))].keys()); w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
    for c in selected:w.writerow(rows[c['case_id']])
print(json.dumps({'group':out.name,'selected':len(selected)},ensure_ascii=False))
env=os.environ.copy();env['DEEPSEEK_MODEL']='deepseek-v4-pro'
subprocess.run([sys.executable,str(BASE/'run_real_batches.py'),'--group','width_lane_retry_v4pro'],env=env,check=True)
