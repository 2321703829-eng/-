"""Read-only triage of completed experiments; emits separate review artifacts."""
import html
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
PROJECT = BASE.parent / '完全版本' / '完全版本' / 'openDriveMapEditor'
sys.path.insert(0, str(PROJECT))
from handler.agent_dispatcher import AgentDispatcher

DISPATCHER = AgentDispatcher()
TOLERANCE_M = 0.01  # screening tolerance only, not an approved experiment scoring rule


def decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def widths(road):
    ids = {lane.get('id') for lane in road.findall('./lanes/laneSection/*/lane') if lane.get('id') != '0'}
    return {lid: DISPATCHER._get_lane_avg_width(road, lid) for lid in ids}


def elevation(road):
    length = float(road.get('length'))
    return [DISPATCHER._get_elevation_at(road, position) for position in (0, length)]


def target_width(change, previous):
    magnitude = change.get('magnitude', {})
    if magnitude.get('kind') != 'numeric':
        return None
    value = float(magnitude['value'])
    action = change.get('action')
    unit = magnitude.get('unit')
    if change.get('mode') == 'absolute' and action == 'set' and unit == 'm':
        return value
    if change.get('mode') == 'relative' and unit in ('m', 'percent'):
        delta = value if unit == 'm' else previous * value / 100
        if action == 'increase':
            return previous + delta
        if action == 'decrease':
            return previous - delta
    return None


def inspect(folder):
    item = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
    result = json.loads((folder / 'execution_result.json').read_text(encoding='utf-8'))
    trace = folder / 'trace.jsonl'
    events = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines() if line.strip()] if trace.exists() else []
    findings = []
    checks = []
    limitations = []

    def add(kind, text):
        findings.append({'kind': kind, 'detail': text})

    if result['status'] != 'done':
        add('execution_failed', result.get('error', 'No successful execution'))
        return {'input': item, 'execution_status': result['status'], 'priority': 1,
                'findings': findings, 'checks': checks, 'limitations': ['No successful after map; do not count as a measured scene mismatch.']}

    before = {r.get('id'): r for r in ET.parse(folder / 'before.xodr').getroot().findall('road')}
    after = {r.get('id'): r for r in ET.parse(folder / 'after.xodr').getroot().findall('road')}
    steps = {}
    for event in events:
        row = event['row']
        if event['type'] == 'dsl_check':
            dsl = decode(row.get('dsl_output_json'))
            if isinstance(dsl, dict) and isinstance(dsl.get('operations'), list):
                steps[str(row.get('step_id'))] = dsl

    operations = [op for dsl in steps.values() for op in dsl['operations']]
    declared_roads = {str(op.get('road_id', '')).removeprefix('road_') for op in operations}
    extra_roads = sorted(set(result.get('changed_road_ids', [])) - declared_roads)
    if extra_roads:
        add('connected_roads_review', '除 DSL 指定道路外还有变化：' + ', '.join('road_' + rid for rid in extra_roads) + '；需确认传播规则是否符合需求')
    if not operations:
        add('dsl_missing', 'Trace 未找到可解析的 DSL 检查输出')

    expected_by_road = {}
    checked_lanes = set()
    for op in operations:
        kind = op.get('operation')
        rid = str(op.get('road_id', '')).removeprefix('road_')
        if rid not in before or rid not in after:
            add('road_missing', f'DSL 道路 road_{rid} 不在前后地图中')
            continue
        if kind == 'width':
            if rid not in expected_by_road:
                expected_by_road[rid] = widths(before[rid])
            expected = expected_by_road[rid]
            lane_ids = op.get('lane_ids')
            if isinstance(lane_ids, list) and lane_ids and lane_ids != ['none']:
                targets = [str(lid).removeprefix('lane_') for lid in lane_ids]
                for lid in targets:
                    if lid not in expected:
                        add('lane_missing', f'DSL 指定 road_{rid}/lane_{lid}，原始地图无此车道')
                        continue
                    target = target_width(op.get('change', {}), expected[lid])
                    if target is None:
                        limitations.append(f'road_{rid}/lane_{lid} 为定性宽度；只核对命令数值执行，不判定 DSL 幅度是否合意')
                    else:
                        expected[lid] = target
                        checked_lanes.add((rid, lid))
            elif lane_ids in (None, 'none', []) or lane_ids == ['none']:
                old_total = sum(expected.values())
                target = target_width(op.get('change', {}), old_total)
                if target is not None and expected:
                    delta = (target - old_total) / len(expected)
                    for lid in expected:
                        expected[lid] += delta
                        checked_lanes.add((rid, lid))
                else:
                    limitations.append(f'road_{rid} 整路定性宽度需要人工核对')
        elif kind == 'curve':
            limitations.append(f'road_{rid} 弯度的形状/半径/顶点语义需在地图中人工审核')
        elif kind == 'slope':
            limitations.append(f'road_{rid} 坡度仅核对命令预测端点与实际端点；DSL 的正负方向及语义另需人工核对')

    for rid, expected in expected_by_road.items():
        actual = widths(after[rid])
        for lid, wanted in expected.items():
            if (rid, lid) not in checked_lanes or lid not in actual:
                continue
            error = actual[lid] - wanted
            checks.append({'kind': 'dsl_width_to_map', 'road_id': rid, 'lane_id': lid,
                           'expected_m': wanted, 'actual_m': actual[lid], 'error_m': error})
            if abs(error) > TOLERANCE_M:
                add('dsl_width_mismatch', f'road_{rid}/lane_{lid}：DSL目标 {wanted:.4f} m，实际 {actual[lid]:.4f} m，差 {error:+.4f} m')

    # Compare command projections with actual saved maps, keeping this distinct from DSL semantics.
    projected_width = {}
    projected_slope = {}
    for event in events:
        if event['type'] != 'road_metric':
            continue
        row = event['row']
        projected = decode(row.get('after_json')) or {}
        rid = str(row.get('road_id'))
        if row.get('operation') == 'width':
            projected_width[rid] = projected
        elif row.get('operation') == 'slope':
            projected_slope[rid] = projected
    for rid, projected in projected_width.items():
        if rid not in after:
            continue
        actual = widths(after[rid])
        for lid, wanted in projected.get('lane_avg_width', {}).items():
            if (rid, lid) in checked_lanes and rid in expected_by_road:
                dsl_wanted = expected_by_road[rid].get(lid)
                if dsl_wanted is not None and abs(wanted - dsl_wanted) > TOLERANCE_M:
                    add('dsl_command_mismatch', f'road_{rid}/lane_{lid}：DSL目标 {dsl_wanted:.4f} m，命令预测 {wanted:.4f} m；工具换算疑点')
            if lid in actual and abs(actual[lid] - wanted) > TOLERANCE_M:
                add('command_width_mismatch', f'road_{rid}/lane_{lid}：命令预测 {wanted:.4f} m，实际 {actual[lid]:.4f} m')
    for rid, projected in projected_slope.items():
        if rid not in after:
            continue
        actual = elevation(after[rid])
        for i, key in enumerate(('start_elevation', 'end_elevation')):
            if key in projected:
                wanted = projected[key]
                checks.append({'kind': 'command_slope_to_map', 'road_id': rid, 'endpoint': key,
                               'expected_m': wanted, 'actual_m': actual[i], 'error_m': actual[i] - wanted})
                if abs(actual[i] - wanted) > TOLERANCE_M:
                    add('command_slope_mismatch', f'road_{rid}/{key}：命令预测 {wanted:.4f} m，实际 {actual[i]:.4f} m')
    if not findings and limitations:
        priority = 3
    elif any(f['kind'] in ('dsl_width_mismatch', 'dsl_command_mismatch', 'command_width_mismatch', 'command_slope_mismatch', 'lane_missing', 'road_missing', 'dsl_missing') for f in findings):
        priority = 1
    elif findings:
        priority = 2
    else:
        priority = 4
    return {'input': item, 'execution_status': result['status'], 'priority': priority,
            'findings': findings, 'checks': checks, 'limitations': list(dict.fromkeys(limitations))}


def run(group):
    group_dir = BASE / group
    out = group_dir / 'review'
    out.mkdir(exist_ok=True)
    cases = []
    for folder in sorted((group_dir / 'cases').iterdir()):
        if (folder / 'execution_result.json').exists():
            try:
                cases.append(inspect(folder))
            except Exception as exc:
                cases.append({'input': json.loads((folder / 'input.json').read_text(encoding='utf-8')),
                    'execution_status': 'audit_error', 'priority': 1,
                    'findings': [{'kind': 'audit_error', 'detail': str(exc)}], 'checks': [], 'limitations': []})
    cases.sort(key=lambda item: (item['priority'], int(item['input']['source_row'])))
    counts = Counter(f['kind'] for case in cases for f in {f['kind']: f for f in case['findings']}.values())
    summary = {'group': group, 'total': len(cases), 'priority_counts': dict(Counter(c['priority'] for c in cases)),
               'finding_case_counts': dict(counts), 'screening_tolerance_m': TOLERANCE_M,
               'note': '自动筛查疑点，不写入数据库、不自动判通过；没有疑点不代表正确'}
    (out / 'review_details.json').write_text(json.dumps({'summary': summary, 'cases': cases}, ensure_ascii=False, indent=2), encoding='utf-8')
    md = ['# ' + group + ' 优先审核清单', '',
          'P1：数值不一致或执行失败；P2：额外道路联动；P3：定性/坡度语义/弯度待人工判断；P4：当前检查未发现疑点。',
          '数值筛查阈值为 0.01 m，仅用于排序，尚不是正式评分标准。失败记录与实际地图不一致分开计数。',
          'Trace 内 road_metric 是预测值；请对照实际 after.xodr。', '',
          '| 优先级 | case_id | 需求 | 疑点 |', '|---|---|---|---|']
    html_rows = []
    for case in cases:
        item = case['input']
        cid = item['case_id']
        details = [f['detail'] for f in case['findings']] + case['limitations']
        text = '；'.join(details) or '当前检查未发现疑点，仍需核对需求语义'
        md.append(f"| P{case['priority']} | {cid} | {item['input_text'].replace('|', '/')} | {text.replace('|', '/').replace(chr(10), ' ')} |")
        links = ' '.join(f'<a href="../cases/{cid}/{name}">{label}</a>' for name, label in
                         [('trace.jsonl', 'Trace'), ('before.xodr', '修改前地图'), ('after.xodr', '修改后地图'), ('execution_result.json', '实际执行结果')]
                         if (group_dir / 'cases' / cid / name).exists())
        html_rows.append(f'<tr class="p{case["priority"]}"><td>P{case["priority"]}</td><td>{cid}<br>{html.escape(item["actual_road_id"])}</td><td>{html.escape(item["input_text"])}</td><td>{html.escape(text)}</td><td>{links}</td></tr>')
    (out / 'review_priority.md').write_text('\n'.join(md), encoding='utf-8')
    page = '<!doctype html><meta charset="utf-8"><title>' + group + ' 审核清单</title><style>body{font:15px system-ui;margin:24px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:10px;vertical-align:top}.p1{background:#fff1ed}.p2{background:#fff9df}a{display:block}input{width:70%;padding:10px}</style>'
    page += '<h1>' + group + ' 优先审核清单</h1><p>400条均已处理。P1 数值不一致或执行失败；P2 额外道路联动；P3 语义待审核；P4 暂未发现疑点。筛查阈值0.01m，不是最终评分标准。</p><p>搜索 case_id、道路、需求或疑点。Trace指标是预测值，实际值见执行结果及地图。失败记录不算已测得的地图差异。</p><input placeholder="搜索" oninput="for(const r of document.querySelectorAll(\'tbody tr\'))r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())"><table><thead><tr><th>优先级</th><th>任务</th><th>需求</th><th>疑点</th><th>文件</th></tr></thead><tbody>'
    page += ''.join(html_rows) + '</tbody></table>'
    (out / 'review_priority.html').write_text(page, encoding='utf-8')
    for bucket, wanted in [('mismatches', {c['input']['case_id'] for c in cases if any(f['kind'] in ('dsl_width_mismatch','dsl_command_mismatch','command_width_mismatch','command_slope_mismatch','lane_missing','road_missing','dsl_missing') for f in c['findings'])}),
                            ('failed', {c['input']['case_id'] for c in cases if c['execution_status'] == 'error'})]:
        source = group_dir / f'{group}_trace.jsonl'
        with (out / f'{group}_{bucket}_trace.jsonl').open('w', encoding='utf-8') as output:
            for line in source.read_text(encoding='utf-8').splitlines():
                event = json.loads(line)
                if event['row'].get('task_id') in wanted:
                    output.write(line + '\n')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    for group in ('width_200', 'mixed_200'):
        run(group)
