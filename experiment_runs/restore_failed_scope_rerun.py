"""Undo only the five failed rerun replacements, preserving concurrent unrelated reviews."""
import shutil
import sqlite3
from run_real_batches import BASE, DATABASE, rebuild, write_json
from rerun_road_limit_five import IDS, RUN, read
import csv

target = BASE / 'width_200'
output = BASE / 'width_scope_retry_v4pro_20261023'
summary = read(output / 'merge_summary.json')
backup = BASE / 'backups' / 'before_scope_rerun_20261023_183211'
assert str(backup) == summary['backup']
for case_id in IDS:
    current = target / 'cases' / case_id
    assert current.resolve().is_relative_to((target / 'cases').resolve())
    assert read(current / 'execution_result.json')['status'] == 'error'
    assert '402' in read(current / 'execution_result.json')['error']
    archive = output / 'failed_merged_cases' / case_id
    archive.parent.mkdir(exist_ok=True)
    current.rename(archive)
    shutil.copytree(backup / 'cases' / case_id, current)
with sqlite3.connect(DATABASE) as connection:
    connection.row_factory = sqlite3.Row
    connection.execute('ATTACH DATABASE ? AS previous', (str(backup / 'request_tests.sqlite3'),))
    dbids = [read(target / 'cases' / cid / 'input.json').get('database_case_id') or cid for cid in IDS]
    placeholders = ','.join('?' for _ in dbids)
    answer_ids = [r[0] for r in connection.execute('SELECT dsl_answer_id FROM previous.dsl_answers WHERE case_id IN (' + placeholders + ')', dbids)]
    def restore(table, key, where, params):
        for old in connection.execute('SELECT * FROM previous.' + table + ' WHERE ' + where, params).fetchall():
            columns = [k for k in old.keys() if k != key]
            connection.execute('UPDATE main.' + table + ' SET ' + ','.join(k + '=?' for k in columns) + ' WHERE ' + key + '=?',
                               [old[k] for k in columns] + [old[key]])
    restore('dsl_answers', 'dsl_answer_id', 'case_id IN (' + placeholders + ')', dbids)
    restore('tool_call_answers', 'tool_answer_id', 'dsl_answer_id IN (' + ','.join('?' for _ in answer_ids) + ')', answer_ids)
    restore('case_reviews', 'review_id', 'run_id=? AND task_id IN (' + ','.join('?' for _ in IDS) + ')', [RUN] + IDS)
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
rows = list(csv.DictReader((target / 'inputs.csv').open(encoding='utf-8-sig', newline='')))
progress = rebuild('width_200', rows)
summary.update(merged=False, restored=True, reason='DeepSeek HTTP 402 Insufficient Balance; all five original case files, answers and reviews restored', merged_progress=progress)
write_json(output / 'merge_summary.json', summary)
write_json(target / 'scope_rerun_summary.json', summary)
print(progress)
