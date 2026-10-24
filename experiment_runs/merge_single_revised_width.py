"""Merge 45 human-approved revised single-agent tasks into the original 200."""
import csv
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from run_real_batches import BASE, DATABASE, rebuild, sha, write_json

source = BASE / 'single_width_revised_v4pro_20261023'
target = BASE / 'single_width_200'
source_run = source.name + '_real_20261019'
target_run = target.name + '_real_20261019'
mapping = json.loads((source / 'revision_mapping.json').read_text(encoding='utf-8'))
rows = list(csv.DictReader((target / 'inputs.csv').open(encoding='utf-8-sig', newline='')))
by_old = {r['case_id']: r for r in mapping}
assert len(rows) == 200 and len(by_old) == 45
assert not (target / 'merge_revision_summary.json').exists(), 'Already merged'
connection = sqlite3.connect(DATABASE)
connection.row_factory = sqlite3.Row
reviews = {r['case_id']: dict(r) for r in connection.execute('SELECT * FROM case_reviews WHERE run_id=?', (source_run,))}
old_reviews = {r['task_id']: dict(r) for r in connection.execute('SELECT * FROM case_reviews WHERE run_id=?', (target_run,))}
assert len(old_reviews) == 200
for revision in mapping:
    new_id = revision['new_case_id']
    assert reviews[new_id]['decision'] == 'pass'
    folder = source / 'cases' / new_id
    result = json.loads((folder / 'execution_result.json').read_text(encoding='utf-8'))
    assert result['status'] == 'done' and result['model'] == 'deepseek-v4-pro' and result['agent_flow'] == 'single'
    assert all((folder / name).is_file() for name in ('trace.jsonl','before.xodr','before.json','after.xodr','after.json'))
    assert connection.execute('SELECT input_text FROM test_cases WHERE case_id=?',(new_id,)).fetchone()[0] == revision['proposed']
backup = BASE / 'backups' / ('single_width_200_before_revision_merge_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir(parents=True)
with sqlite3.connect(backup / 'request_tests.sqlite3') as dest:
    connection.backup(dest)
for name in ('inputs.csv','single_width_200_trace.jsonl','results.json','progress.json','run_config.json'):
    shutil.copy2(target / name, backup / name)
write_json(backup / 'reviews.json', list(old_reviews.values()))
unchanged = {}
for item in rows:
    cid = item['case_id']
    folder = target / 'cases' / cid
    if cid not in by_old:
        unchanged[cid] = {p.name: sha(p) for p in folder.iterdir() if p.is_file()}
        continue
    revision = by_old[cid]
    assert item['input_text'] == revision['original']
    staged = backup / 'staged' / cid
    shutil.copytree(source / 'cases' / revision['new_case_id'], staged)
    item.update(input_text=revision['proposed'], database_case_id=revision['new_case_id'])
    write_json(staged / 'input.json', item)
    result = json.loads((staged / 'execution_result.json').read_text(encoding='utf-8'))
    result.update(case_id=cid, group=target.name, database_case_id=revision['new_case_id'],
                  source_group=source.name,source_case_id=revision['new_case_id'],source_run_id=source_run)
    write_json(staged / 'execution_result.json',result)
    folder.rename(backup / 'cases' / cid) if (backup / 'cases').exists() else None
    if folder.exists():
        (backup / 'cases').mkdir(exist_ok=True)
        folder.rename(backup / 'cases' / cid)
    staged.rename(folder)
fields = list(dict.fromkeys(key for row in rows for key in row))
with (target / 'inputs.csv').open('w',encoding='utf-8-sig',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
progress = rebuild(target.name,rows)
with connection:
    for revision in mapping:
        old_id,new_id = revision['case_id'],revision['new_case_id']
        review=reviews[new_id]
        connection.execute('DELETE FROM case_reviews WHERE run_id=? AND task_id=?',(target_run,old_id))
        connection.execute('''INSERT INTO case_reviews
          (case_id,run_id,task_id,decision,note,review_source,trace_source,metadata_json,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?)''',
          (new_id,target_run,old_id,review['decision'],review['note'],review['review_source'],
           'single_width_200_trace.jsonl',review['metadata_json'],review['created_at'],review['updated_at']))
    assert not connection.execute('PRAGMA foreign_key_check').fetchall()
final={r['task_id']:dict(r) for r in connection.execute('SELECT * FROM case_reviews WHERE run_id=?',(target_run,))}
assert len(final)==200
for cid, files in unchanged.items():
    folder=target/'cases'/cid
    assert {p.name:sha(p) for p in folder.iterdir() if p.is_file()}==files
    assert final[cid]==old_reviews[cid]
for revision in mapping:
    review=final[revision['case_id']]
    assert review['case_id']==revision['new_case_id'] and review['decision']=='pass'
config=json.loads((target/'run_config.json').read_text(encoding='utf-8'))
config.update(revision_merge_at=datetime.now().isoformat(),revision_count=45,
              revision_source_run_id=source_run,revision_backup=str(backup))
write_json(target/'run_config.json',config)
summary={'backup':str(backup),'replaced':45,'unchanged':155,'progress':progress,
         'reviews':dict(Counter(r['decision'] for r in final.values())),
         'database_strategy':'Original task IDs preserved; revised tasks link to the revised database cases and standards; old DB versions retained for history.'}
write_json(target/'merge_revision_summary.json',summary)
assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
connection.close()
print(json.dumps(summary,ensure_ascii=False))
