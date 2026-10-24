"""Retry only the two balance failures in the revised single-agent batch."""
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from run_real_batches import BASE, DATABASE, PROJECT, rebuild, sha, write_json

group = 'single_width_revised_v4pro_20261023'
output = BASE / group
ids = ('req_000857', 'req_000858')
rows = list(csv.DictReader((output / 'inputs.csv').open(encoding='utf-8-sig', newline='')))
backup = BASE / 'backups' / ('single_two_balance_retry_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir(parents=True)
with sqlite3.connect(DATABASE.resolve().as_uri() + '?mode=ro', uri=True) as connection:
    with sqlite3.connect(backup / 'request_tests.sqlite3') as destination:
        connection.backup(destination)
for name in ('inputs.csv', group + '_trace.jsonl', 'results.json', 'progress.json', 'run_config.json'):
    shutil.copy2(output / name, backup / name)
unchanged = {r['case_id']: sha(output / 'cases' / r['case_id'] / 'execution_result.json') for r in rows if r['case_id'] not in ids}
for cid in ids:
    folder = output / 'cases' / cid
    shutil.copytree(folder, backup / 'cases' / cid)
    assert folder.resolve().is_relative_to((output / 'cases').resolve())
    folder.rename(backup / ('old_' + cid))
    folder.mkdir()
    shutil.copy2(backup / 'cases' / cid / 'input.json', folder / 'input.json')
    env = {**os.environ, 'DEEPSEEK_MODEL': 'deepseek-v4-pro', 'PYTHONIOENCODING': 'utf-8', 'MPLBACKEND': 'Agg'}
    with (folder / 'worker.log').open('w', encoding='utf-8') as log:
        result = subprocess.run([sys.executable, str(BASE / 'run_real_batches.py'), '--worker', group, '--case', cid],
                                cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    assert result.returncode == 0 and (folder / 'execution_result.json').exists()
    execution = json.loads((folder / 'execution_result.json').read_text(encoding='utf-8'))
    if execution['status'] != 'done':
        folder.rename(backup / ('new_failed_' + cid))
        shutil.copytree(backup / 'cases' / cid, folder)
        raise RuntimeError(execution.get('error'))
    assert all((folder / name).exists() for name in ('before.xodr', 'before.json', 'after.xodr', 'after.json'))
    print(cid, 'done', flush=True)
progress = rebuild(group, rows)
with sqlite3.connect(DATABASE) as connection:
    for cid in ids:
        connection.execute("UPDATE case_reviews SET decision='pending',note='',updated_at=? WHERE run_id=? AND task_id=?",
                           (datetime.now().isoformat(), group + '_real_20261019', cid))
for cid, digest in unchanged.items():
    assert sha(output / 'cases' / cid / 'execution_result.json') == digest
write_json(output / 'balance_retry_summary.json', {'backup': str(backup), 'retried': list(ids), 'progress': progress,
          'other_43_unchanged': True, 'original_single_200_unchanged': True})
print(json.dumps(progress), flush=True)
