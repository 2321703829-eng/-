"""Isolated real-road experiment runner; does not write the source database."""
import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
PROJECT = BASE.parent / '完全版本' / '完全版本' / 'openDriveMapEditor'
DATA = PROJECT.parent / 'SCTrans-data' / 'SCTrans-data'
DATABASE = PROJECT / 'testfucs' / 'agent_trace_viewer' / 'request_tests.sqlite3'
CONFIRM = BASE.parent / 'experiment_400_confirmation_2026-10-17'


def write_json(filename, value):
    temp = filename.with_suffix(filename.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(filename)


def sha(filename):
    return hashlib.sha256(filename.read_bytes()).hexdigest()


def map_snapshot(filename):
    root = ET.parse(filename).getroot()
    return {road.get('id'): ET.tostring(road, encoding='unicode') for road in root.findall('road')}


def trace_commands(events):
    """Keep individual commands separate when replaying an agent step."""
    commands = []
    for event in events:
        value = event['row'].get('internal_command')
        if event['type'] != 'agent_step' or not value:
            continue
        if isinstance(value, str) and value.lstrip().startswith('['):
            value = json.loads(value)
        if isinstance(value, str):
            commands.extend(line.split() for line in value.splitlines() if line.strip())
        elif isinstance(value, list):
            commands.extend(value if value and isinstance(value[0], list) else [value])
        else:
            raise ValueError('Invalid internal command payload')
    for command in commands:
        if not command or command[0] not in ('width', 'slope', 'curve'):
            raise ValueError(f'Unsupported replay command: {command}')
        keys = []
        for token in command[1:]:
            if '=' not in token:
                raise ValueError(f'Merged or invalid replay command: {command}')
            keys.append(token.split('=', 1)[0])
        if len(keys) != len(set(keys)):
            raise ValueError(f'Duplicate parameters in replay command: {command}')
    return commands


def worker(group, case_id):
    sys.path.insert(0, str(PROJECT))
    from dotenv import dotenv_values
    from langchain_openai import ChatOpenAI
    from testfucs.agent_flow_real_trace import run_real_trace, load_real_road_alignments, setup_paths
    from handler.agent_dispatcher import AgentDispatcher
    from jsons.jsonParser import JParser
    from xodrs.xodrParser import XParser
    from utils import agent_road_context

    folder = BASE / group / 'cases' / case_id
    flow = 'single' if group.startswith('single_') else 'normal'
    item = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
    model_name = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat').strip() or 'deepseek-chat'
    result = {'case_id': case_id, 'group': group, 'started_at': datetime.now().isoformat(),
              'status': 'error', 'model': model_name, 'agent_flow': flow}
    try:
        xodr = DATA / 'OpenSCENARIO' / item['map_file_name']
        scene = DATA / 'LGSVL-VSE' / item['scene_file_name']
        if not xodr.is_file() or not scene.is_file():
            raise FileNotFoundError(f'Missing original scene file: {xodr if not xodr.is_file() else scene}')
        shutil.copy2(xodr, folder / 'before.xodr')
        shutil.copy2(scene, folder / 'before.json')
        result['source_xodr_sha256'] = sha(xodr)
        result['source_json_sha256'] = sha(scene)
        key = dotenv_values(PROJECT / 'testfucs' / '.env').get('DEEPSEEK_API_KEY')
        if not key:
            raise RuntimeError('DEEPSEEK_API_KEY missing')
        os.environ['HTTP_PROXY'] = 'http://127.0.0.1:7897'
        os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:7897'
        model = ChatOpenAI(base_url='https://api.deepseek.com/v1', api_key=key,
                           model=model_name, timeout=120, max_retries=2)
        trace = folder / 'trace.jsonl'
        run_real_trace(trace, model, [item['input_text']], execute=False,
                       road_context='aligned-real', agent_flow=flow, enable_dsl_check=True,
                       request_cases={item['input_text'].strip(): {'算子': item['operator_types'].replace('+', ',')}},
                       road_alignments=load_real_road_alignments(DATABASE),
                       aligned_xodr_directory=DATA / 'OpenSCENARIO',
                       aligned_json_directory=DATA / 'LGSVL-VSE')
        events = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines() if line.strip()]
        statuses = [event['row'] for event in events if event['type'] == 'task']
        if not statuses or statuses[-1].get('status') != 'done':
            raise RuntimeError(statuses[-1].get('error', 'LLM pipeline failed') if statuses else 'No task status')
        commands = trace_commands(events)
        if not commands:
            raise RuntimeError('No executable model-generated command; no after map created')
        # Reload originals before actual replay, matching the approved single-case example.
        os.environ.pop('AGENT_TRACE_PATH', None)
        agent_road_context.set_use_manual_road_context(False)
        setup_paths('before', json_name='before', xodr_directory=folder, json_directory=folder)
        # Replay starts from originals, so reproduce planning's curve fit before
        # any mixed width/slope commands use the fitted road length.
        from handler.handlerCurve import handlerC
        curve_roads = {part.split('=', 1)[1] for command in commands if command[0] == 'curve'
                       for part in command[1:] if part.startswith('id=')}
        for curve_road in sorted(curve_roads):
            handlerC.prepare_road_fit(curve_road)
        dispatcher = AgentDispatcher()
        rid = item['actual_road_id'].removeprefix('road_')
        before_width = dispatcher._build_width_road_info(rid)
        execution = []
        for command in commands:
            execution.append({'command': ' '.join(command), 'result': dispatcher._execute_command(command)})
        XParser.data[-1].write(folder / 'after.xodr', encoding='utf-8', xml_declaration=True)
        write_json(folder / 'after.json', JParser.data[-1])
        after_width = dispatcher._build_width_road_info(rid)
        before_roads = map_snapshot(folder / 'before.xodr')
        after_roads = map_snapshot(folder / 'after.xodr')
        changed = sorted(rid for rid in set(before_roads) | set(after_roads)
                         if before_roads.get(rid) != after_roads.get(rid))
        result.update(status='done', commands=execution, actual_width_before=before_width,
                      actual_width_after=after_width, changed_road_ids=changed,
                      source_files_unchanged=(sha(xodr) == result['source_xodr_sha256']
                                              and sha(scene) == result['source_json_sha256']),
                      note='Trace road_metric events are projected; actual values are recorded here. No accuracy judgement implied.')
    except Exception as exc:
        result['error'] = str(exc)
        traceback.print_exc()
    result['finished_at'] = datetime.now().isoformat()
    write_json(folder / 'execution_result.json', result)


def rebuild(group, rows):
    output = BASE / group
    run_id = group + ('_real_20261019' if group.startswith('single_') else '_real_20261017')
    states = []
    trace_path = output / f'{group}_trace.jsonl'
    temp = trace_path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        for item in rows:
            folder = output / 'cases' / item['case_id']
            result_file = folder / 'execution_result.json'
            if not result_file.exists():
                continue
            result = json.loads(result_file.read_text(encoding='utf-8'))
            states.append(result)
            trace = folder / 'trace.jsonl'
            events = []
            if trace.exists():
                for line in trace.read_text(encoding='utf-8').splitlines():
                    if line.strip():
                        events.append(json.loads(line))
            for event in events:
                event['row']['task_id'] = item['case_id']
                event['row']['run_id'] = run_id
                event['row']['case_id'] = item.get('database_case_id') or item['case_id']
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            # Overall completion requires actual execution, overriding dry-run completion.
            task = {'run_id': run_id, 'task_id': item['case_id'],
                    'case_id': item.get('database_case_id') or item['case_id'], 'user_input': item['input_text'],
                    'status': result['status'], 'error': result.get('error'),
                    'finished_at': result['finished_at'], 'updated_at': result['finished_at']}
            stream.write(json.dumps({'type': 'task', 'row': task}, ensure_ascii=False) + '\n')
    temp.replace(trace_path)
    progress = {'group': group, 'total': len(rows), 'finished': len(states),
                'counts': dict(Counter(state['status'] for state in states)),
                'updated_at': datetime.now().isoformat(),
                'completed': len(states) == len(rows)}
    write_json(output / 'progress.json', progress)
    write_json(output / 'results.json', states)
    return progress


def batch(group):
    os.chdir(PROJECT)
    output = BASE / group
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / 'inputs.csv'
    if not manifest.exists():
        source_group = group.removeprefix('single_')
        frozen = BASE / source_group / 'inputs.csv'
        shutil.copy2(frozen if frozen.exists() else CONFIRM / (source_group + '_confirm.csv'), manifest)
    with manifest.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    model_name = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat').strip() or 'deepseek-chat'
    write_json(output / 'run_config.json', {'group': group, 'model': model_name,
        'agent_flow': 'single' if group.startswith('single_') else 'normal', 'road_context': 'aligned-real', 'database': str(DATABASE),
        'curve_prefit': {'md': 0.01, 'st': 1.0, 'gi': 0},
        'manifest_sha256': sha(manifest), 'count': len(rows), 'execution': 'model plan then isolated handler replay',
        'started_at': datetime.now().isoformat()})
    rebuild(group, rows)
    for item in rows:
        folder = output / 'cases' / item['case_id']
        folder.mkdir(parents=True, exist_ok=True)
        if (folder / 'execution_result.json').exists():
            continue
        write_json(folder / 'input.json', item)
        with (folder / 'worker.log').open('w', encoding='utf-8') as logfile:
            try:
                proc = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                    '--worker', group, '--case', item['case_id']], stdout=logfile, stderr=subprocess.STDOUT,
                    timeout=1200, env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'MPLBACKEND': 'Agg'})
                if not (folder / 'execution_result.json').exists():
                    raise RuntimeError(f'Worker exited without result: {proc.returncode}')
            except Exception as exc:
                write_json(folder / 'execution_result.json', {'case_id': item['case_id'],
                    'group': group, 'status': 'error', 'error': str(exc),
                    'finished_at': datetime.now().isoformat()})
        progress = rebuild(group, rows)
        print(json.dumps(progress, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    groups = ['width_200', 'mixed_200', 'single_width_200', 'single_mixed_200',
              'width_lane_retry_v4pro', 'single_width_retry_v4pro',
              'width_revised_v4pro_20261023', 'single_width_revised_v4pro_20261023']
    parser.add_argument('--group', choices=groups)
    parser.add_argument('--worker', choices=groups)
    parser.add_argument('--case')
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.case)
    else:
        batch(args.group)
