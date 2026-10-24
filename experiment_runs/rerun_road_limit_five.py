"""Back up, rerun five scope failures, and replace only their merged results."""
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from run_real_batches import BASE, DATABASE, PROJECT, rebuild, sha, worker, write_json

GROUP = 'width_scope_retry_v4pro_20261023'
IDS = [f'req_{n:06d}' for n in (424, 428, 435, 448, 461)]
RUN = 'width_200_real_20261017'

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

def scopes(folder):
    latest = {}
    for line in (folder / 'trace.jsonl').read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        row = event['row']
        key = 'dsl_output_json' if event['type'] == 'dsl_check' else 'llm_output_json' if event['type'] == 'agent_step' else ''
        if key and row.get(key):
            value = json.loads(row[key]) if isinstance(row[key], str) else row[key]
            if 'operations' in value:
                latest[str(row.get('step_id'))] = value
    return [op.get('road_limit') for d in latest.values() for op in d['operations'] if op.get('operation') == 'width']

def fix_tool(value):
    if isinstance(value, dict):
        return {k: (99 if k == 'ms' else fix_tool(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [fix_tool(v) for v in value]
    if isinstance(value, str):
        return value.replace('ms=0 ', 'ms=99 ')
    return value

def main():
    target = BASE / 'width_200'
    output = BASE / GROUP
    assert not output.exists(), 'Already started; refusing duplicate run'
    rows = list(csv.DictReader((target / 'inputs.csv').open(encoding='utf-8-sig', newline='')))
    selected = [r for r in rows if r['case_id'] in IDS]
    assert len(selected) == 5
    backup = BASE / 'backups' / ('before_scope_rerun_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    backup.mkdir(parents=True)
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    with sqlite3.connect(backup / 'request_tests.sqlite3') as dest:
        connection.backup(dest)
    for name in ('inputs.csv', 'width_200_trace.jsonl', 'results.json', 'progress.json', 'run_config.json'):
        shutil.copy2(target / name, backup / name)
    for case_id in IDS:
        shutil.copytree(target / 'cases' / case_id, backup / 'cases' / case_id)
    original_reviews = [dict(r) for r in connection.execute('SELECT * FROM case_reviews WHERE run_id=?', (RUN,))]
    write_json(backup / 'reviews.json', original_reviews)
    unaffected = {r['case_id']: sha(target / 'cases' / r['case_id'] / 'execution_result.json') for r in rows if r['case_id'] not in IDS}
    output.mkdir()
    with (output / 'inputs.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(selected)
    for item in selected:
        folder = output / 'cases' / item['case_id']
        folder.mkdir(parents=True)
        write_json(folder / 'input.json', item)
    write_json(output / 'run_config.json', {'model': 'deepseek-v4-pro', 'agent_flow': 'normal',
        'count': 5, 'backup': str(backup), 'execution': 'Unmodified requirements; full multi-agent chain and real handler replay'})
    env = {**os.environ, 'DEEPSEEK_MODEL': 'deepseek-v4-pro', 'PYTHONIOENCODING': 'utf-8', 'MPLBACKEND': 'Agg'}
    def run(item):
        folder = output / 'cases' / item['case_id']
        with (folder / 'worker.log').open('w', encoding='utf-8') as log:
            proc = subprocess.run([sys.executable, str(BASE / 'rerun_road_limit_five.py'), '--worker', item['case_id']],
                cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
        assert proc.returncode == 0 and (folder / 'execution_result.json').exists(), item['case_id']
        return item['case_id']
    rebuild(GROUP, selected)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(run, r) for r in selected]):
            print('Finished', future.result(), flush=True)
            rebuild(GROUP, selected)
    diagnostics = []
    for item in selected:
        case_id = item['case_id']
        folder = output / 'cases' / case_id
        result = read(folder / 'execution_result.json')
        diagnostics.append({'case_id': case_id, 'status': result['status'], 'road_limits': scopes(folder), 'commands': result.get('commands', [])})
    if any(item['status'] != 'done' for item in diagnostics):
        write_json(output / 'blocked_summary.json', {'backup': str(backup), 'results': diagnostics,
                   'merged': False, 'reason': 'At least one rerun failed; original results and database remain unchanged'})
        raise SystemExit('Rerun failed; no overwrite performed. Inspect execution_result.json.')
    # Standards come from the requirement rule, never from blindly accepting new model output.
    with connection:
        for item in selected:
            case_id = item['case_id']
            dbid = item.get('database_case_id') or case_id
            for answer in list(connection.execute('SELECT * FROM dsl_answers WHERE case_id=?', (dbid,))):
                dsl = json.loads(answer['dsl_json'])
                changed = False
                for op in dsl.get('operations', []):
                    if op.get('operation') == 'width' and op.get('road_limit') == 1:
                        op['road_limit'] = 'none'
                        changed = True
                if not changed:
                    continue
                metadata = json.loads(answer['metadata_json'])
                metadata['scope_correction'] = {'reason': 'Requirement does not explicitly limit propagation', 'at': datetime.now().isoformat()}
                connection.execute('UPDATE dsl_answers SET dsl_json=?,metadata_json=?,updated_at=? WHERE dsl_answer_id=?',
                    (json.dumps(dsl, ensure_ascii=False), json.dumps(metadata), datetime.now().isoformat(), answer['dsl_answer_id']))
                for tool in list(connection.execute('SELECT * FROM tool_call_answers WHERE dsl_answer_id=?', (answer['dsl_answer_id'],))):
                    values = [json.dumps(fix_tool(json.loads(tool[k])), ensure_ascii=False) if tool[k] else None
                              for k in ('tool_call_json', 'resolved_result_json', 'internal_command_json')]
                    connection.execute('UPDATE tool_call_answers SET tool_call_json=?,resolved_result_json=?,internal_command_json=?,updated_at=? WHERE tool_answer_id=?',
                        (*values, datetime.now().isoformat(), tool['tool_answer_id']))
            review = connection.execute('SELECT * FROM case_reviews WHERE run_id=? AND task_id=?', (RUN, case_id)).fetchone()
            assert review is not None
            connection.execute("UPDATE case_reviews SET decision='pending',note=?,metadata_json=?,updated_at=? WHERE review_id=?",
                ('Rerun with DeepSeek V4 Pro; previous human decision archived; awaiting review.',
                 json.dumps({'rerun_group': GROUP, 'backup': str(backup)}), datetime.now().isoformat(), review['review_id']))
    for item in selected:
        case_id = item['case_id']
        (target / 'cases' / case_id).rename(backup / ('replaced_' + case_id))
        shutil.copytree(output / 'cases' / case_id, target / 'cases' / case_id)
        result = read(target / 'cases' / case_id / 'execution_result.json')
        result.update(group='width_200', source_group=GROUP)
        write_json(target / 'cases' / case_id / 'execution_result.json', result)
    progress = rebuild('width_200', rows)
    for case_id, digest in unaffected.items():
        assert sha(target / 'cases' / case_id / 'execution_result.json') == digest
    final_reviews = {r['task_id']: dict(r) for r in connection.execute('SELECT * FROM case_reviews WHERE run_id=?', (RUN,))}
    assert len(final_reviews) == 200
    for review in original_reviews:
        if review['task_id'] not in IDS:
            assert final_reviews[review['task_id']] == review
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    connection.close()
    summary = {'backup': str(backup), 'results': diagnostics, 'merged_progress': progress,
               'other_195_unchanged': True, 'new_reviews': 'pending', 'standards_scope_corrected': True}
    write_json(output / 'merge_summary.json', summary)
    write_json(target / 'scope_rerun_summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        worker(GROUP, sys.argv[2])
    else:
        main()
