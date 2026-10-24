"""Audit lateral lane selection and DSL self-check consistency, without model calls."""
import html
import json
import re
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent


def parsed(value):
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return None


def truth(value):
    return value is True or str(value).lower() == 'true'


def expected_lanes(text, lane_ids):
    text = text.lower()
    if re.search(r'\bleftmost\b|\bfar\s+left\s+lane', text):
        return '最左（完整列表首元素）', lane_ids[:1]
    if re.search(r'\brightmost\b|\bfar\s+right\s+lane', text):
        return '最右（完整列表尾元素）', lane_ids[-1:]
    match = re.search(r'\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+lane\s+(?:on\s+the\s+|on\s+|to\s+the\s+)?(left|right)\b', text)
    if match:
        ordinal, side = match.groups()
        rank = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4,
                '1st': 1, '2nd': 2, '3rd': 3, '4th': 4}[ordinal]
        available = sorted([lid for lid in lane_ids if lid > 0], reverse=True) if side == 'left' else sorted([lid for lid in lane_ids if lid < 0], key=abs, reverse=True)
        return f'{side}侧第{rank}条（按项目半幅计数规则）', available[rank-1:rank]
    return None


def inspect(folder):
    task = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
    execution = json.loads((folder / 'execution_result.json').read_text(encoding='utf-8'))
    trace = folder / 'trace.jsonl'
    if not trace.exists():
        return None
    events = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines() if line.strip()]
    histories = {}
    for event in events:
        row = event['row']
        if event['type'] != 'dsl_check' or row.get('agent') != 'width':
            continue
        context = parsed(row.get('road_context_json'))
        dsl = parsed(row.get('dsl_output_json'))
        if not isinstance(context, dict) or not isinstance(dsl, dict):
            continue
        ids = context.get('lane_ids')
        if not isinstance(ids, list):
            continue
        ids = [int(value) for value in ids]
        selection = expected_lanes(row.get('input_text') or task['input_text'], ids)
        if selection is None:
            continue
        rule, expected = selection
        ops = [op for op in dsl.get('operations', []) if op.get('operation') == 'width']
        actual = []
        malformed = False
        for op in ops:
            values = op.get('lane_ids')
            if not isinstance(values, list):
                malformed = True
                continue
            for value in values:
                try:
                    actual.append(int(str(value).removeprefix('lane_')))
                except ValueError:
                    malformed = True
        # A missing target is represented by no operations, not by an operation with an empty lane list.
        correct = (not malformed and set(actual) == set(expected)
                   and (bool(expected) or not ops))
        entry = {'attempt': row.get('attempt'), 'step_id': row.get('step_id'),
                 'rule': rule, 'lane_ids': ids, 'road_id': context.get('road_id'),
                 'expected': expected, 'actual': actual, 'operations_count': len(ops),
                 'selector_correct': correct, 'self_check_passed': truth(row.get('passed')),
                 'check_result': row.get('check_result'), 'dsl': dsl}
        histories.setdefault(str(row.get('step_id')), []).append(entry)
    if not histories:
        return None
    issues = set()
    for entries in histories.values():
        final = entries[-1]
        if not final['selector_correct']:
            issues.add('final_selection_wrong')
        if not final['expected']:
            issues.add('requested_side_lane_missing')
        if any(e['selector_correct'] and not e['self_check_passed'] for e in entries):
            # Self-check may fail for other DSL fields; this is a candidate, not proof of a false rejection.
            issues.add('correct_selection_rejected_review')
        if any(not e['selector_correct'] and e['self_check_passed'] for e in entries):
            issues.add('wrong_selection_accepted')
        if (not final['selector_correct'] and any(e['selector_correct'] and not e['self_check_passed'] for e in entries[:-1])):
            issues.add('correct_then_wrong_after_rejection')
    return {'case_id': task['case_id'], 'source_row': task['source_row'],
            'input_text': task['input_text'], 'execution_status': execution['status'],
            'issues': sorted(issues), 'step_histories': histories,
            'executed_with_wrong_selection': execution['status'] == 'done' and 'final_selection_wrong' in issues}


LABELS = {
    'final_selection_wrong': '最终DSL车道选择不符规则',
    'requested_side_lane_missing': '原始地图缺少需求所指半幅/序号车道',
    'correct_selection_rejected_review': '车道选对但自检拒绝（需核对是否另有字段错误）',
    'wrong_selection_accepted': '错误车道被自检通过',
    'correct_then_wrong_after_rejection': '先选对、自检拒绝后最终选错',
}


def run(group):
    root = BASE / group
    output = root / 'lane_review'
    output.mkdir(exist_ok=True)
    checked = []
    for folder in sorted((root / 'cases').iterdir()):
        if (folder / 'execution_result.json').exists():
            item = inspect(folder)
            if item:
                checked.append(item)
    counts = {issue: sum(issue in item['issues'] for item in checked) for issue in LABELS}
    summary = {'group': group, 'all_cases': 200, 'cases_with_lateral_selector_and_dsl': len(checked),
               'counts': counts, 'executed_with_wrong_selection': sum(item['executed_with_wrong_selection'] for item in checked),
               'final_selection_wrong_case_ids': [item['case_id'] for item in checked if 'final_selection_wrong' in item['issues']],
               'correct_then_wrong_case_ids': [item['case_id'] for item in checked if 'correct_then_wrong_after_rejection' in item['issues']],
               'note': '按当前项目提示词确定性筛查左右车道；没有模型输出的失败任务不计作车道正确。未检查宽度幅度等其他字段。类别可重叠。'}
    checked.sort(key=lambda item: (not item['executed_with_wrong_selection'], 'final_selection_wrong' not in item['issues'], not bool(item['issues']), int(item['source_row'])))
    (output / 'lane_selection_details.json').write_text(json.dumps({'summary': summary, 'cases': checked}, ensure_ascii=False, indent=2), encoding='utf-8')
    markdown = ['# ' + group + ' 左右车道审核', '', summary['note'], '',
                '规则：最左/最右取完整lane_ids首/尾；左侧第N条取正ID降序；右侧第N条取负ID绝对值降序；无目标则空operations。', '',
                '| 任务 | 执行状态 | 需求 | 当前车道 | 应选 | 最终选 | 问题 |', '|---|---|---|---|---|---|---|']
    rows = []
    for item in checked:
        final = [history[-1] for history in item['step_histories'].values()]
        ids = '; '.join(str(entry['lane_ids']) for entry in final)
        wanted = '; '.join(str(entry['expected']) if entry['expected'] else '无目标，应空操作' for entry in final)
        actual = '; '.join(str(entry['actual']) for entry in final)
        description = '；'.join(LABELS[issue] for issue in item['issues']) or '当前车道规则检查一致'
        markdown.append('| ' + ' | '.join(str(value).replace('|', '/').replace('\n', ' ') for value in
            (item['case_id'], item['execution_status'], item['input_text'], ids, wanted, actual, description)) + ' |')
        attempts = []
        for history in item['step_histories'].values():
            for attempt in history:
                attempts.append(f"第{attempt['attempt']}次：选{attempt['actual']}，自检{'通过' if attempt['self_check_passed'] else '拒绝'}；{attempt['check_result']}")
        cid = item['case_id']
        links = ' '.join(f'<a href="../cases/{cid}/{file}">{label}</a>' for file, label in
            [('trace.jsonl', 'Trace'), ('before.xodr', '修改前'), ('after.xodr', '修改后')]
            if (root / 'cases' / cid / file).exists())
        color = '#fff0ea' if 'final_selection_wrong' in item['issues'] else '#fff9dc' if item['issues'] else 'white'
        cells = [cid, item['execution_status'], item['input_text'], ids, wanted, actual, description]
        rows.append(f'<tr style="background:{color}">' + ''.join('<td>' + html.escape(str(cell)) + '</td>' for cell in cells)
            + '<td><details><summary>生成和自检过程</summary>' + '<br>'.join(html.escape(attempt) for attempt in attempts) + '</details>' + links + '</td></tr>')
    (output / 'lane_selection_review.md').write_text('\n'.join(markdown), encoding='utf-8')
    page = '<!doctype html><meta charset="utf-8"><title>左右车道审核</title><style>body{font:15px system-ui;margin:24px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;vertical-align:top}input{padding:10px;width:80%}a{display:block}</style><h1>' + group + ' 左右车道审核</h1><p>' + html.escape(summary['note']) + '</p><p>应选为空表示原始地图不存在所指车道。自检拒绝可能涉及其他字段，需结合过程判断。</p><input placeholder="搜索任务编号或问题" oninput="for(const r of document.querySelectorAll(\'tbody tr\'))r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())"><table><thead><tr>'
    page += ''.join('<th>' + title + '</th>' for title in ['任务','执行状态','需求','当前车道','应选','最终选','问题','过程和文件']) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
    (output / 'lane_selection_review.html').write_text(page, encoding='utf-8')
    for category, selected in [('wrong_selection', {item['case_id'] for item in checked if 'final_selection_wrong' in item['issues']}),
                               ('selfcheck_issues', {item['case_id'] for item in checked if any(issue in item['issues'] for issue in ('wrong_selection_accepted','correct_selection_rejected_review'))})]:
        with (output / f'{group}_{category}_trace.jsonl').open('w', encoding='utf-8') as target:
            for line in (root / f'{group}_trace.jsonl').read_text(encoding='utf-8').splitlines():
                event = json.loads(line)
                if event['row'].get('task_id') in selected:
                    target.write(line + '\n')
    print(json.dumps(summary, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    for group in ('width_200', 'mixed_200'):
        run(group)
