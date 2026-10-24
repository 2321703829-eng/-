import argparse,csv,json,os,subprocess,sys,shutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
BASE=Path(__file__).resolve().parent; group='width_lane_retry_v4pro'; out=BASE/group
parser=argparse.ArgumentParser()
parser.add_argument('--include-all-rejected',action='store_true')
parser.add_argument('--workers',type=int,default=4)
parser.add_argument('--retry-transient',action='store_true')
args=parser.parse_args()
if not 1 <= args.workers <= 8:parser.error('--workers must be between 1 and 8')
if args.include_all_rejected:
    rejected=json.loads((BASE/'width_200/failure_analysis/rejected_width_details.json').read_text(encoding='utf-8'))['cases']
    target_ids={case['case_id'] for case in rejected}
    existing=list(csv.DictReader((out/'inputs.csv').open(encoding='utf-8-sig',newline='')))
    source=list(csv.DictReader((BASE/'width_200/inputs.csv').open(encoding='utf-8-sig',newline='')))
    existing_ids={row['case_id'] for row in existing}
    assert existing_ids <= target_ids
    expanded=existing+[row for row in source if row['case_id'] in target_ids-existing_ids]
    assert len(expanded)==len(target_ids)==92
    backup=out/'inputs_initial_56.csv'
    if not backup.exists():shutil.copy2(out/'inputs.csv',backup)
    temporary=out/'inputs.csv.tmp'
    with temporary.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(source[0]))
        writer.writeheader();writer.writerows(expanded)
    temporary.replace(out/'inputs.csv')
from run_real_batches import rebuild,write_json
rows=list(csv.DictReader((out/'inputs.csv').open(encoding='utf-8-sig',newline='')))
if args.retry_transient:
    for item in rows:
        folder=out/'cases'/item['case_id'];result=folder/'execution_result.json'
        if not result.exists():continue
        previous=json.loads(result.read_text(encoding='utf-8'))
        message=str(previous.get('error','')).lower()
        if previous.get('status')!='error' or not any(token in message for token in ('429','rate_limit','connection error','timed out')):continue
        archive=folder/'previous_attempts'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        archive.mkdir(parents=True,exist_ok=False)
        for name in ('execution_result.json','trace.jsonl','worker.log'):
            f=folder/name
            if f.exists():shutil.move(str(f),str(archive/name))
def run(item):
    folder=out/'cases'/item['case_id'];folder.mkdir(parents=True,exist_ok=True)
    (folder/'input.json').write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding='utf-8')
    env=os.environ.copy();env['DEEPSEEK_MODEL']='deepseek-v4-pro';env['PYTHONIOENCODING']='utf-8';env['MPLBACKEND']='Agg'
    try:
        with (folder/'worker.log').open('a',encoding='utf-8') as log:
            p=subprocess.run([sys.executable,str(BASE/'run_real_batches.py'),'--worker',group,'--case',item['case_id']],cwd=str(BASE.parent/'完全版本/完全版本/openDriveMapEditor'),env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
        if not (folder/'execution_result.json').exists():raise RuntimeError(f'Worker exited without result: {p.returncode}')
        return item['case_id'],p.returncode
    except Exception as exc:
        write_json(folder/'execution_result.json',{'case_id':item['case_id'],'group':group,'model':'deepseek-v4-pro','agent_flow':'normal','status':'error','error':str(exc),'finished_at':datetime.now().isoformat()})
        return item['case_id'],'error'
todo=[r for r in rows if not (out/'cases'/r['case_id']/'execution_result.json').exists()]
print('parallel',len(todo),f'workers={args.workers}',flush=True)
rebuild(group,rows)
with ThreadPoolExecutor(max_workers=args.workers) as pool:
    futures=[pool.submit(run,r) for r in todo]
    for fut in as_completed(futures):
        print(fut.result(),flush=True)
        print(json.dumps(rebuild(group,rows),ensure_ascii=False),flush=True)
env=os.environ.copy();env['DEEPSEEK_MODEL']='deepseek-v4-pro'
subprocess.run([sys.executable,str(BASE/'run_real_batches.py'),'--group',group],env=env,check=True)
