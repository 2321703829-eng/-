"""Audit historical replay commands and regenerate six maps without any LLM call."""
import argparse
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from run_real_batches import BASE, PROJECT, DATABASE, map_snapshot, sha, trace_commands, write_json

CASES = [f'req_{n:06d}' for n in (405, 425, 447, 448, 459, 497)]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def events(folder):
    path = folder / 'trace.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []


def approved_cases():
    connection = sqlite3.connect(DATABASE.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return {row[0] for row in connection.execute(
            "SELECT case_id FROM case_reviews WHERE run_id=? AND decision='pass'",
            ('width_200_real_20261017',))}
    finally:
        connection.close()


def audit():
    findings = []
    selected = approved_cases()
    for group in ('width_200',):
        for folder in sorted((BASE / group / 'cases').iterdir()):
            if folder.name not in selected:
                continue
            result = read(folder / 'execution_result.json')
            planned = trace_commands(events(folder))
            executed = result.get('commands', [])
            issues = []
            if result['status'] == 'done':
                if len(planned) != len(executed):
                    issues.append('command_count_mismatch')
                for row in executed:
                    if any('=' not in token for token in row['command'].split()[1:]):
                        issues.append('merged_commands')
                if any(not (folder / name).is_file() for name in ('before.xodr', 'before.json', 'after.xodr', 'after.json')):
                    issues.append('missing_map_artifact')
            if issues:
                findings.append({'group': group, 'case_id': folder.name, 'issues': sorted(set(issues)),
                                 'planned_count': len(planned), 'executed_count': len(executed)})
    return findings


def replay(case_id):
    sys.path.insert(0, str(PROJECT))
    from testfucs.agent_flow_real_trace import setup_paths
    from handler.agent_dispatcher import AgentDispatcher
    from xodrs.xodrParser import XParser
    from jsons.jsonParser import JParser
    from utils import agent_road_context

    folder = BASE / 'width_200' / 'cases' / case_id
    result = read(folder / 'execution_result.json')
    item = read(folder / 'input.json')
    commands = trace_commands(events(folder))
    assert len(commands) == 2 and all(command[0] == 'width' for command in commands)
    before_hashes = {name: sha(folder / name) for name in ('before.xodr', 'before.json')}
    os.environ.pop('AGENT_TRACE_PATH', None)
    agent_road_context.set_use_manual_road_context(False)
    setup_paths('before', json_name='before', xodr_directory=folder, json_directory=folder)
    dispatcher = AgentDispatcher()
    rid = item['actual_road_id'].removeprefix('road_')
    before = dispatcher._build_width_road_info(rid)
    executed = []
    for command in commands:
        reply = dispatcher._execute_command(command)
        assert reply and str(reply).startswith('width id='), reply
        # The same loaded map is used for both executions; do not reload here.
        executed.append({'command': ' '.join(command), 'result': reply})
    after = dispatcher._build_width_road_info(rid)
    assert len(executed) == len(commands)
    for lane_id in ('1', '-1'):
        assert abs(after['lane_avg_width'][lane_id] - before['lane_avg_width'][lane_id]) > 1e-6
    XParser.data[-1].write(folder / 'after.xodr', encoding='utf-8', xml_declaration=True)
    write_json(folder / 'after.json', JParser.data[-1])
    assert before_hashes == {name: sha(folder / name) for name in before_hashes}
    old_roads = map_snapshot(folder / 'before.xodr')
    new_roads = map_snapshot(folder / 'after.xodr')
    result.update(commands=executed, actual_width_before=before, actual_width_after=after,
                  changed_road_ids=sorted(r for r in old_roads.keys() | new_roads.keys() if old_roads.get(r) != new_roads.get(r)),
                  replay_repaired_at=datetime.now().isoformat(),
                  replay_repair='Execute each original trace command separately on the same map; no new model call')
    write_json(folder / 'execution_result.json', result)
    print(case_id, before['lane_avg_width'], after['lane_avg_width'], flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case')
    parser.add_argument('--audit-only', action='store_true')
    args = parser.parse_args()
    selected = approved_cases()
    assert set(CASES) <= selected, 'Repair targets must be approved multi-agent cases'
    if args.audit_only:
        path = BASE / 'width_200' / 'replay_repair_report.json'
        report = read(path)
        report.update(scope='Approved multi-agent width tasks only; command replay and artifact integrity',
                      checked_count=len(selected), checked_case_ids=sorted(selected),
                      audit_after=audit(), scope_corrected_at=datetime.now().isoformat())
        report['audit_before'] = [row for row in report['audit_before']
                                  if row['group'] == 'width_200' and row['case_id'] in selected]
        write_json(path, report)
        print(json.dumps({'checked_count': len(selected), 'audit_after': report['audit_after'],
                          'repaired': report['repaired']}, ensure_ascii=False))
        return
    if args.case:
        replay(args.case)
        return
    backup = BASE / 'backups' / ('width_replay_repair_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    backup.mkdir(parents=True)
    findings = audit()
    write_json(backup / 'audit_before.json', findings)
    print('AUDIT', json.dumps(findings, ensure_ascii=False), flush=True)
    for case_id in CASES:
        folder = BASE / 'width_200' / 'cases' / case_id
        shutil.copytree(folder, backup / 'cases' / case_id)
    shutil.copy2(BASE / 'width_200' / 'results.json', backup / 'results.json')
    for case_id in CASES:
        subprocess.run([sys.executable, str(Path(__file__).resolve()), '--case', case_id],
                       cwd=str(PROJECT), env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'MPLBACKEND': 'Agg'}, check=True)
    with (BASE / 'width_200' / 'inputs.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    write_json(BASE / 'width_200' / 'results.json',
               [read(BASE / 'width_200' / 'cases' / row['case_id'] / 'execution_result.json') for row in rows])
    report = {'backup': str(backup), 'audit_before': findings, 'repaired': CASES,
              'audit_after': audit(), 'checked_count': len(selected), 'checked_case_ids': sorted(selected),
              'scope': 'Approved multi-agent width tasks only; command replay and artifact integrity'}
    write_json(BASE / 'width_200' / 'replay_repair_report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
