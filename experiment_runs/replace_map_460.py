"""Use a verified existing aligned scene with lane -2, then rerun both 460 tasks."""
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
from run_real_batches import BASE,DATA,DATABASE,PROJECT,rebuild,worker,write_json,sha

GROUPS={'width_200':'width_map460_20261024','single_width_200':'single_width_map460_20261024'}
CID='req_000460'
def read(p):return json.loads(p.read_text(encoding='utf-8'))

def main():
    backup=BASE/'backups'/('before_460_scene_replacement_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    backup.mkdir(parents=True)
    connection=sqlite3.connect(DATABASE);connection.row_factory=sqlite3.Row
    with sqlite3.connect(backup/'request_tests.sqlite3') as dest:connection.backup(dest)
    old=dict(connection.execute('SELECT * FROM test_case_road_alignments WHERE case_id=?',(CID,)).fetchone())
    candidate=dict(connection.execute("SELECT * FROM test_case_road_alignments WHERE case_id='req_000012'").fetchone())
    mapfile=DATA/'OpenSCENARIO'/candidate['map_file_name'];scenefile=DATA/'LGSVL-VSE'/candidate['scene_file_name']
    assert mapfile.is_file() and scenefile.is_file()
    assert read(scenefile)['map']['name']==mapfile.stem
    road=ET.parse(mapfile).getroot().find("road[@id='%s']"%candidate['road_id'])
    assert road is not None and road.find("lanes/laneSection/right/lane[@id='-2']") is not None
    assert -2 in json.loads(candidate['route_lane_ids_json'])
    assert 0<=candidate['s_start']<candidate['s_end']<=float(road.get('length'))
    write_json(backup/'alignment_before.json',old)
    manifests={};selected={};unchanged={}
    for group,stage in GROUPS.items():
        assert not (BASE/stage).exists()
        target=BASE/group;(backup/group).mkdir()
        manifests[group]=list(csv.DictReader((target/'inputs.csv').open(encoding='utf-8-sig',newline='')))
        for name in ('inputs.csv',group+'_trace.jsonl','results.json','progress.json','run_config.json'):
            shutil.copy2(target/name,backup/group/name)
        shutil.copytree(target/'cases'/CID,backup/group/'cases'/CID)
        for row in manifests[group]:
            if row['case_id']!=CID:
                unchanged[group,row['case_id']]=sha(target/'cases'/row['case_id']/'execution_result.json');continue
            assert row['input_text']=='Lane -2 on Road 21 must be narrowed.'
            row.update(actual_road_id='road_'+candidate['road_id'],map_file_name=candidate['map_file_name'],
                scene_file_name=candidate['scene_file_name'],map_file_exists='True',scene_file_exists='True',run_ready='True',alignment_status='matched')
            selected[group]=row
        stagepath=BASE/stage;stagepath.mkdir()
        folder=stagepath/'cases'/CID;folder.mkdir(parents=True);write_json(folder/'input.json',selected[group])
    revised={k:v for k,v in candidate.items() if k not in ('alignment_id','case_id')}
    revised.update(match_reason='Replacement for missing original map; verified matching scene map name and route lane -2; reused req_000012 alignment',updated_at=datetime.now().isoformat())
    with connection:
        connection.execute('UPDATE test_case_road_alignments SET '+','.join(k+'=?' for k in revised)+' WHERE case_id=?',list(revised.values())+[CID])
    def run(group,stage):
        folder=BASE/stage/'cases'/CID
        with (folder/'worker.log').open('w',encoding='utf-8') as log:
            proc=subprocess.run([sys.executable,str(BASE/'replace_map_460.py'),'--worker',stage],cwd=PROJECT,
                env={**os.environ,'DEEPSEEK_MODEL':'deepseek-v4-pro','PYTHONIOENCODING':'utf-8','MPLBACKEND':'Agg'},
                stdout=log,stderr=subprocess.STDOUT,timeout=1200)
        assert proc.returncode==0 and read(folder/'execution_result.json')['status']=='done',str(folder)
        result=read(folder/'execution_result.json')
        assert result['commands'] and all(dict(token.split('=',1) for token in row['command'].split()[1:]).get('li')=='-2' for row in result['commands'])
        return group
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for f in as_completed([pool.submit(run,g,s) for g,s in GROUPS.items()]):print('Finished',f.result(),flush=True)
    except Exception:
        fields=[k for k in old if k not in ('alignment_id','case_id')]
        with connection:connection.execute('UPDATE test_case_road_alignments SET '+','.join(k+'=?' for k in fields)+' WHERE case_id=?',[old[k] for k in fields]+[CID])
        raise
    results=[]
    for group,stage in GROUPS.items():
        target=BASE/group/'cases'/CID;target.rename(backup/group/('replaced_'+CID))
        shutil.copytree(BASE/stage/'cases'/CID,target)
        execution=read(target/'execution_result.json');execution.update(group=group,source_group=stage)
        write_json(target/'execution_result.json',execution)
        results.append({'group':group,'commands':execution['commands'],'actual_width_before':execution['actual_width_before'],'actual_width_after':execution['actual_width_after']})
        rows=manifests[group]
        with (BASE/group/'inputs.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        rebuild(group,rows)
        runid=group+('_real_20261019' if group.startswith('single') else '_real_20261017')
        with connection:connection.execute("UPDATE case_reviews SET decision='pending',note=?,metadata_json=?,updated_at=? WHERE run_id=? AND task_id=?",
            ('Replaced missing map with a compatible map/scene; rerun awaiting review.',json.dumps({'backup':str(backup),'source_group':stage}),datetime.now().isoformat(),runid,CID))
    for (group,cid),digest in unchanged.items():assert sha(BASE/group/'cases'/cid/'execution_result.json')==digest
    assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    summary={'backup':str(backup),'case_id':CID,'alignment':revised,'source_map_sha256':sha(mapfile),'source_scene_sha256':sha(scenefile),'results':results,'other_cases_unchanged':True}
    for group in GROUPS:write_json(BASE/group/'map460_replacement_summary.json',summary)
    write_json(backup/'completion.json',summary)
    connection.close();print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--worker':worker(sys.argv[2],CID)
    else:main()
