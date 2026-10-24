"""Back up originals and insert approved revisions as independent database cases."""
import csv
import json
import shutil
import sqlite3
from datetime import datetime
from run_real_batches import BASE, DATABASE, write_json

GROUP = 'width_revised_v4pro_20261023'
out = BASE / GROUP
if out.exists():
    raise SystemExit('Revision batch already exists; refusing duplicate preparation')
proposals = json.loads((BASE / 'width_200/requirement_revision_proposal.json').read_text(encoding='utf-8'))
assert len(proposals) == 45
stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
backup = BASE / 'backups' / ('before_width_revision_' + stamp)
backup.mkdir(parents=True)
with sqlite3.connect(DATABASE.resolve().as_uri() + '?mode=ro', uri=True) as source:
    with sqlite3.connect(backup / DATABASE.name) as target:
        source.backup(target)
        assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
for name in ('inputs.csv', 'width_200_trace.jsonl', 'results.json', 'run_config.json'):
    shutil.copy2(BASE / 'width_200' / name, backup / name)
for row in proposals:
    shutil.copytree(BASE / 'width_200/cases' / row['case_id'], backup / 'cases' / row['case_id'])
out.mkdir()
rows = []
mapping = []
with sqlite3.connect(DATABASE) as connection:
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    next_row = connection.execute('SELECT MAX(source_row) FROM test_cases').fetchone()[0]
    for proposal in proposals:
        original_id = proposal['case_id']
        original = dict(connection.execute('SELECT * FROM test_cases WHERE case_id=?', (original_id,)).fetchone())
        assert original['input_text'] == proposal['original']
        next_row += 1
        new_id = f'req_{next_row:06d}'
        original.update(case_id=new_id, source_row=next_row, input_text=proposal['proposed'],
                        created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat())
        keys = list(original)
        connection.execute('INSERT INTO test_cases (' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')', list(original.values()))
        alignment = dict(connection.execute('SELECT * FROM test_case_road_alignments WHERE case_id=?', (original_id,)).fetchone())
        alignment.pop('alignment_id')
        alignment.update(case_id=new_id, updated_at=datetime.now().isoformat())
        keys = list(alignment)
        connection.execute('INSERT INTO test_case_road_alignments (' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')', list(alignment.values()))
        item = json.loads((BASE / 'width_200/cases' / original_id / 'input.json').read_text(encoding='utf-8'))
        item.update(case_id=new_id, source_case_id=original_id, source_row=str(next_row),
                    input_text=proposal['proposed'], experiment_group=GROUP)
        rows.append(item)
        mapping.append(dict(proposal, new_case_id=new_id))
    assert not connection.execute('PRAGMA foreign_key_check').fetchall()
with (out / 'inputs.csv').open('w', encoding='utf-8-sig', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
write_json(out / 'revision_mapping.json', mapping)
write_json(out / 'preparation.json', {'backup': str(backup), 'count': len(rows),
    'group': GROUP, 'database': str(DATABASE), 'original_cases_unchanged': True,
    'review_state': 'pending; no old standards or reviews copied'})
print(json.dumps({'backup': str(backup), 'group': GROUP, 'count': len(rows)}, ensure_ascii=False))
