"""Read-only root-cause triage for the human-rejected width multi-agent run."""
import collections
import html
import json
import pathlib
import re
import sqlite3

from audit_lane_selection import expected_lanes

BASE = pathlib.Path(__file__).resolve().parent
PROJECT = BASE.parent / '完全版本' / '完全版本' / 'openDriveMapEditor'
GROUP = BASE / 'width_200'
OUT = GROUP / 'failure_analysis'
OUT.mkdir(exist_ok=True)
DB = PROJECT / 'testfucs/agent_trace_viewer/request_tests.sqlite3'
connection = sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True)
connection.row_factory = sqlite3.Row
reviews = [dict(row) for row in connection.execute(
    'SELECT * FROM case_reviews WHERE run_id=? AND decision=?',
    ('width_200_real_20261017', 'fail'),
)]
connection.close()
triage = json.loads((GROUP / 'review/review_details.json').read_text(encoding='utf-8'))
triage = {case['input']['case_id']: case for case in triage['cases']}
cases = []


def decode(value):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return None


def selection(text, context):
    ids = [int(lid) for lid in context.get('lane_ids', [])]
    lateral = expected_lanes(text, ids)
    if lateral:
        return lateral
    direct = re.findall(r'\blane\s+(-?\d+)\b', text, flags=re.I)
    if direct:
        requested = [int(lid) for lid in direct]
        return '明确车道ID ' + str(requested), [lid for lid in requested if lid in ids]
    values = context.get('lane_avg_width') or {}
    if values and re.search(r'\b(widest|narrowest)\b', text, re.I):
        maximum = bool(re.search(r'\bwidest\b', text, re.I))
        target = (max if maximum else min)(float(value) for value in values.values())
        return '最宽' if maximum else '最窄', [int(lid) for lid, value in values.items() if abs(float(value) - target) < 1e-8]
    return None


for review in reviews:
    folder = GROUP / 'cases' / review['case_id']
    task = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
    execution = json.loads((folder / 'execution_result.json').read_text(encoding='utf-8'))
    trace = folder / 'trace.jsonl'
    events = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines() if line.strip()] if trace.exists() else []
    checks = [event['row'] for event in events if event['type'] == 'dsl_check' and event['row'].get('agent') == 'width']
    last = checks[-1] if checks else {}
    context = decode(last.get('road_context_json')) or {}
    dsl = decode(last.get('dsl_output_json')) or {}
    operations = dsl.get('operations', [])
    actual = [int(str(lid).removeprefix('lane_')) for op in operations for lid in op.get('lane_ids', [])
              if isinstance(op.get('lane_ids'), list) and str(lid).removeprefix('lane_').lstrip('-').isdigit()]
    target = selection(task['input_text'], context) if context else None
    wrong = bool(target and (set(actual) != set(target[1]) or (not target[1] and operations)))
    mismatch = [finding['detail'] for finding in triage[review['case_id']]['findings']
                if finding['kind'] in ('dsl_width_mismatch', 'command_width_mismatch')]
    if target and not target[1]:
        category = '需求车道在真实道路中不存在'
    elif wrong:
        category = '目标车道存在但DSL选错'
    elif execution['status'] == 'error':
        if 'Missing original scene file' in (execution.get('error') or ''):
            category = '原始地图文件缺失'
        else:
            category = '选道正确但DSL自检拒绝（需复核其余字段）' if target else '校验或运行失败（未取得可核对选道）'
    elif mismatch:
        category = '已执行但最终宽度数值不一致'
    else:
        category = '现有检查未定位原因，需人工复核'
    previous_correct_rejected = []
    for check in checks[:-1]:
        before_dsl = decode(check.get('dsl_output_json')) or {}
        before_ops = before_dsl.get('operations', [])
        before_ids = [int(str(lid).removeprefix('lane_')) for op in before_ops for lid in op.get('lane_ids', [])
                      if isinstance(op.get('lane_ids'), list) and str(lid).removeprefix('lane_').lstrip('-').isdigit()]
        correct = target and set(before_ids) == set(target[1]) and (target[1] or not before_ops)
        if correct and str(check.get('passed')).lower() != 'true':
            previous_correct_rejected.append(check.get('check_result'))
    cases.append({'case_id': review['case_id'], 'source_row': task['source_row'],
        'category': category, 'execution_status': execution['status'], 'input_text': task['input_text'],
        'actual_road_id': task['actual_road_id'], 'available_lane_ids': context.get('lane_ids'),
        'selection_rule': target[0] if target else None, 'expected_lane_ids': target[1] if target else None,
        'final_dsl_lane_ids': actual, 'selection_wrong': wrong,
        'error': execution.get('error'), 'last_check_result': last.get('check_result'),
        'previous_correct_selection_rejected': previous_correct_rejected,
        'width_mismatches': mismatch, 'human_note': review['note'],
        'changed_road_ids': execution.get('changed_road_ids', []),
        'review_hint': ('有多条道路联动，需核对传播范围；不据此直接判错' if len(execution.get('changed_road_ids', [])) > 1 else '')})

cases.sort(key=lambda case: (case['category'], int(case['source_row'])))
summary = {'run_id': 'width_200_real_20261017', 'rejected_count': len(cases),
    'category_counts_exclusive': dict(collections.Counter(case['category'] for case in cases)),
    'execution_counts': dict(collections.Counter(case['execution_status'] for case in cases)),
    'all_selection_wrong_count': sum(case['selection_wrong'] for case in cases),
    'with_numeric_mismatch_count': sum(bool(case['width_mismatches']) for case in cases),
    'previous_correct_selection_rejected_count': sum(bool(case['previous_correct_selection_rejected']) for case in cases),
    'empty_human_notes': sum(not case['human_note'].strip() for case in cases),
    'note': '只分析本次人工标记fail的任务；类别为独立筛查归因，非人工备注。类别按映射缺失、选道错误、执行错误、数值差异依次归类，其他问题可能同时存在。'}
(OUT / 'rejected_width_details.json').write_text(json.dumps({'summary': summary, 'cases': cases}, ensure_ascii=False, indent=2), encoding='utf-8')
rows = []
for case in cases:
    detail = ('规则：' + str(case['selection_rule']) + '；可用：' + str(case['available_lane_ids'])
              + '；应选：' + str(case['expected_lane_ids']) + '；最终选：' + str(case['final_dsl_lane_ids']))
    detail += '\n' + (case['error'] or '') + '\n' + '\n'.join(case['width_mismatches'])
    detail += '\n' + case['review_hint']
    if case['previous_correct_selection_rejected']:
        detail += '\n曾选对车道但自检拒绝：' + '; '.join(str(reason) for reason in case['previous_correct_selection_rejected'])
    cid = case['case_id']
    links = ''.join(f'<a href="../cases/{cid}/{file}">{label}</a><br>' for file,label in
        [('trace.jsonl','Trace'),('execution_result.json','实际执行'),('before.xodr','修改前'),('after.xodr','修改后')]
        if (GROUP / 'cases' / cid / file).exists())
    rows.append('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in
        (cid,case['category'],case['execution_status'],case['input_text']))
        + '<td style="white-space:pre-wrap">' + html.escape(detail) + '</td><td>' + links + '</td></tr>')
header = '<!doctype html><meta charset="utf-8"><title>宽度未通过任务归因</title><style>body{font:15px system-ui;margin:20px}td,th{border:1px solid #ccc;padding:9px;vertical-align:top}table{border-collapse:collapse;width:100%}input{padding:10px;width:80%}</style><h1>多智能体宽度：人工未通过任务归因</h1><p>'
header += html.escape(summary['note']) + '</p><p>' + html.escape(str(summary['category_counts_exclusive'])) + '</p>'
header += '<input placeholder="搜索编号、原因或需求" oninput="for(const r of document.querySelectorAll(\'tbody tr\'))r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())"><table><thead><tr>'
header += ''.join('<th>'+column+'</th>' for column in ['任务','主要问题','执行状态','需求','证据','文件']) + '</tr></thead><tbody>'
(OUT / 'rejected_width_review.html').write_text(header + ''.join(rows) + '</tbody></table>', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=True))
for category in summary['category_counts_exclusive']:
    print(json.dumps({'category':category,'case_ids':[case['case_id'] for case in cases if case['category']==category]},ensure_ascii=True))
