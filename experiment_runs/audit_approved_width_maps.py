"""Read-only geometry checks for the currently approved multi-agent width cases."""
import json
import sys
import xml.etree.ElementTree as ET

from repair_width_replay import approved_cases
from run_real_batches import BASE, PROJECT, write_json

sys.path.insert(0, str(PROJECT.parent / 'ResultAna'))
from visualization.scenario_visualizer import _lane_average_widths

findings = []
for case_id in sorted(approved_cases()):
    folder = BASE / 'width_200' / 'cases' / case_id
    result = json.loads((folder / 'execution_result.json').read_text(encoding='utf-8'))
    maps = {}
    for stage in ('before', 'after'):
        maps[stage] = {r.get('id'): _lane_average_widths(r)
                       for r in ET.parse(folder / (stage + '.xodr')).getroot().findall('road')
                       if r.find('lanes') is not None}
        json.loads((folder / (stage + '.json')).read_text(encoding='utf-8'))
    issues = []
    rid = result['actual_width_before']['road_id'].removeprefix('road_')
    for stage in ('before', 'after'):
        recorded = result['actual_width_' + stage]['lane_avg_width']
        actual = {str(k): v for k, v in maps[stage][rid].items()}
        if recorded != actual:
            issues.append({'type': 'map_record_mismatch', 'stage': stage})
    for row in result.get('commands', []):
        tokens = row['command'].split()
        params = dict(token.split('=', 1) for token in tokens[1:])
        road_id = params['id']
        delta = float(params['v'])
        lanes = [int(x) for x in params.get('li', '').split(',') if x] or list(maps['before'][road_id])
        for lane_id in lanes:
            before = maps['before'][road_id][lane_id]
            after = maps['after'][road_id][lane_id]
            change = after - before
            entry = {'road_id': road_id, 'lane_id': lane_id, 'before': before,
                     'after': after, 'command_delta': delta, 'actual_delta': round(change, 4),
                     'difference_from_unclamped_projection': round(change - delta, 4)}
            if delta * change < -1e-7:
                issues.append(dict(entry, type='opposite_direction'))
            elif abs(delta) > 0.001 and abs(change) < 1e-6:
                issues.append(dict(entry, type='no_average_change'))
            if abs(change - delta) > 0.001:
                issues.append(dict(entry, type='execution_differs_from_projection'))
    if issues:
        findings.append({'case_id': case_id, 'issues': issues})

report = {'checked_count': len(approved_cases()),
          'scope': 'Approved multi-agent width maps only; XML/JSON parsing, average widths vs execution records, target-lane direction and projected delta. Not a full scene/trajectory/geometry-continuity validation.',
          'findings': findings}
write_json(BASE / 'width_200' / 'approved_map_checks.json', report)
for kind in ('map_record_mismatch', 'opposite_direction', 'no_average_change', 'execution_differs_from_projection'):
    cases = [r['case_id'] for r in findings if any(x['type'] == kind for x in r['issues'])]
    print(kind, len(cases), ','.join(cases))
