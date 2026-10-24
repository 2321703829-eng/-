"""Merge approved revisions with versioned database links; prepare single-agent rerun."""
import csv
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from run_real_batches import BASE, DATABASE, rebuild, write_json, sha

source = BASE / 'width_revised_v4pro_20261023'
target = BASE / 'width_200'
single = BASE / 'single_width_revised_v4pro_20261023'
source_run = source.name + '_real_20261017'
target_run = 'width_200_real_20261017'
assert not single.exists(), 'Already prepared; refusing duplicate merge'
mapping = json.loads((source / 'revision_mapping.json').read_text(encoding='utf-8'))
rows = list(csv.DictReader((target / 'inputs.csv').open(encoding='utf-8-sig', newline='')))
new_rows = list(csv.DictReader((source / 'inputs.csv').open(encoding='utf-8-sig', newline='')))
by_new = {r['case_id']: r for r in new_rows}
by_old = {r['case_id']: r for r in mapping}
assert len(mapping) == 45 and len(rows) == 200
connection = sqlite3.connect(DATABASE)
connection.row_factory = sqlite3.Row
reviews = {r['case_id']: dict(r) for r in connection.execute('SELECT * FROM case_reviews WHERE run_id=?', (source_run,))}
assert all(reviews[r['new_case_id']]['decision'] == 'pass' for r in mapping)
backup = BASE / 'backups' / ('width_200_before_revision_merge_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir(parents=True)
with sqlite3.connect(backup / 'request_tests.sqlite3') as dest:
    connection.backup(dest)
for name in ('inputs.csv', 'width_200_trace.jsonl', 'results.json', 'progress.json', 'run_config.json'):
    shutil.copy2(target / name, backup / name)
untouched = {r['case_id']: sha(target / 'cases' / r['case_id'] / 'trace.jsonl')
             for r in rows if r['case_id'] not in by_old and (target / 'cases' / r['case_id'] / 'trace.jsonl').exists()}
for item in rows:
    item['database_case_id'] = item['case_id']
    if item['case_id'] not in by_old:
        continue
    old_id = item['case_id']
    revision = by_old[old_id]
    new_id = revision['new_case_id']
    assert item['input_text'] == revision['original']
    shutil.copytree(target / 'cases' / old_id, backup / 'cases' / old_id)
    staged = backup / 'staged' / old_id
    shutil.copytree(source / 'cases' / new_id, staged)
    item.update(input_text=revision['proposed'], database_case_id=new_id)
    write_json(staged / 'input.json', item)
    result = json.loads((staged / 'execution_result.json').read_text(encoding='utf-8'))
    result.update(case_id=old_id, group='width_200', database_case_id=new_id,
                  source_group=source.name, source_case_id=new_id, source_run_id=source_run)
    write_json(staged / 'execution_result.json', result)
    # Move only the exact, already-backed-up case directory.
    (target / 'cases' / old_id).rename(backup / ('replaced_' + old_id))
    staged.rename(target / 'cases' / old_id)
with (target / 'inputs.csv').open('w', encoding='utf-8-sig', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
progress = rebuild('width_200', rows)
with connection:
    for revision in mapping:
        old_id, new_id = revision['case_id'], revision['new_case_id']
        review = reviews[new_id]
        connection.execute('DELETE FROM case_reviews WHERE run_id=? AND task_id=?', (target_run, old_id))
        connection.execute('''INSERT INTO case_reviews
            (case_id,run_id,task_id,decision,note,review_source,trace_source,metadata_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (new_id,target_run,old_id,review['decision'],review['note'],review['review_source'],
             'width_200_trace.jsonl',review['metadata_json'],review['created_at'],review['updated_at']))
    assert not connection.execute('PRAGMA foreign_key_check').fetchall()
for case_id, digest in untouched.items():
    assert sha(target / 'cases' / case_id / 'trace.jsonl') == digest
decisions = Counter(r[0] for r in connection.execute('SELECT decision FROM case_reviews WHERE run_id=?', (target_run,)))
assert sum(decisions.values()) == 200 and progress['total'] == progress['finished'] == 200
assert all(connection.execute('SELECT input_text FROM test_cases WHERE case_id=?', (r['new_case_id'],)).fetchone()[0] == r['proposed'] for r in mapping)
config = json.loads((target / 'run_config.json').read_text(encoding='utf-8'))
config.update(revision_merge_at=datetime.now().isoformat(), revision_count=45,
              revision_source_run_id=source_run, revision_backup=str(backup))
write_json(target / 'run_config.json', config)
single.mkdir()
for row in new_rows:
    row['experiment_group'] = single.name
with (single / 'inputs.csv').open('w', encoding='utf-8-sig', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=list(new_rows[0]))
    writer.writeheader()
    writer.writerows(new_rows)
write_json(single / 'revision_mapping.json', mapping)
summary = {'backup': str(backup), 'replaced': 45, 'reviews': dict(decisions),
    'database_strategy': 'Original task IDs retained; revised tasks link to approved new database case IDs. Old DB cases remain for unmerged single-agent runs.',
    'unchanged_other_cases': 155, 'single_batch': single.name}
write_json(target / 'merge_revision_summary.json', summary)
connection.close()
print(json.dumps(summary, ensure_ascii=False))
