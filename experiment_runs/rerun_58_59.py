"""Revise 058 to leftmost and rerun multi 058 / single 058 and 059."""
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from run_real_batches import BASE,DATABASE,PROJECT,rebuild,worker,write_json,sha

TASKS=[('width_200','req_000058'),('single_width_200','req_000058'),('single_width_200','req_000059')]
STAGES={'width_200':'width_lane_fix_20261024','single_width_200':'single_width_lane_fix_20261024'}
NEW='Set the leftmost lane of Road 21 to 1.3m in width.'

def read(path):return json.loads(path.read_text(encoding='utf-8'))

def main():
    backup=BASE/'backups'/('before_58_59_fix_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    backup.mkdir(parents=True)
    connection=sqlite3.connect(DATABASE)
    connection.row_factory=sqlite3.Row
    with sqlite3.connect(backup/'request_tests.sqlite3') as dest:connection.backup(dest)
    manifests={};selected={};unchanged={}
    for group,stage in STAGES.items():
        assert not (BASE/stage).exists(),'Batch already exists'
        target=BASE/group
        manifests[group]=list(csv.DictReader((target/'inputs.csv').open(encoding='utf-8-sig',newline='')))
        assert len(manifests[group])==200
        (backup/group).mkdir()
        for name in ('inputs.csv',group+'_trace.jsonl','results.json','progress.json','run_config.json'):
            shutil.copy2(target/name,backup/group/name)
        selected[group]=[]
        for row in manifests[group]:
            cid=row['case_id']
            if (group,cid) in TASKS:
                shutil.copytree(target/'cases'/cid,backup/group/'cases'/cid)
                if cid=='req_000058':row['input_text']=NEW
                selected[group].append(row)
            else:unchanged[group,cid]=sha(target/'cases'/cid/'execution_result.json')
        output=BASE/stage;output.mkdir()
        with (output/'inputs.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(manifests[group][0]));writer.writeheader();writer.writerows(selected[group])
        for row in selected[group]:
            folder=output/'cases'/row['case_id'];folder.mkdir(parents=True);write_json(folder/'input.json',row)
    previous=connection.execute("SELECT input_text FROM test_cases WHERE case_id='req_000058'").fetchone()[0]
    assert previous=='Set the first lane on the left of Road 21 to 1.3m in width.'
    with connection:connection.execute("UPDATE test_cases SET input_text=?,updated_at=? WHERE case_id='req_000058'",(NEW,datetime.now().isoformat()))
    def run(group,cid):
        stage=STAGES[group];folder=BASE/stage/'cases'/cid
        with (folder/'worker.log').open('w',encoding='utf-8') as log:
            proc=subprocess.run([sys.executable,str(BASE/'rerun_58_59.py'),'--worker',stage,cid],cwd=PROJECT,
                env={**os.environ,'DEEPSEEK_MODEL':'deepseek-v4-pro','PYTHONIOENCODING':'utf-8','MPLBACKEND':'Agg'},
                stdout=log,stderr=subprocess.STDOUT,timeout=1200)
        assert proc.returncode==0 and read(folder/'execution_result.json')['status']=='done',str(folder)
        return group,cid
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            for future in as_completed([pool.submit(run,*t) for t in TASKS]):print('Finished',future.result(),flush=True)
    except Exception:
        with connection:connection.execute("UPDATE test_cases SET input_text=? WHERE case_id='req_000058'",(previous,))
        raise
    diagnostic=[]
    for group,cid in TASKS:
        source=BASE/STAGES[group]/'cases'/cid;target=BASE/group/'cases'/cid
        # Verify the actual model command selects the intended lane before merging.
        result=read(source/'execution_result.json');commands=result['commands']
        expected='-1' if cid=='req_000058' else '-3'
        assert commands and all(dict(t.split('=',1) for t in c['command'].split()[1:]).get('li')==expected for c in commands),commands
        target.rename(backup/group/('replaced_'+cid))
        shutil.copytree(source,target)
        result.update(group=group,source_group=STAGES[group]);write_json(target/'execution_result.json',result)
        diagnostic.append({'group':group,'case_id':cid,'commands':commands,'actual_width_after':result['actual_width_after']})
    with connection:
        for group,cid in TASKS:
            runid=group+('_real_20261019' if group.startswith('single') else '_real_20261017')
            connection.execute("UPDATE case_reviews SET decision='pending',note=?,metadata_json=?,updated_at=? WHERE run_id=? AND task_id=?",
                ('Lane selection rerun; awaiting review.',json.dumps({'backup':str(backup),'rerun_group':STAGES[group]}),datetime.now().isoformat(),runid,cid))
    progress={}
    for group,rows in manifests.items():
        with (BASE/group/'inputs.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        progress[group]=rebuild(group,rows)
        rebuild(STAGES[group],selected[group])
    for (group,cid),digest in unchanged.items():assert sha(BASE/group/'cases'/cid/'execution_result.json')==digest
    assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    summary={'backup':str(backup),'results':diagnostic,'progress':progress,'other_cases_unchanged':True}
    write_json(backup/'completion.json',summary)
    for group in STAGES:write_json(BASE/group/'lane_58_59_fix_summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--worker':worker(sys.argv[2],sys.argv[3])
    else:main()
