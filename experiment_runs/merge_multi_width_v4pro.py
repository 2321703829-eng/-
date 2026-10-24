"""Replace the 92 multi-agent reruns and carry their human reviews forward."""
import csv
import argparse
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from run_real_batches import DATABASE, rebuild, write_json

BASE = Path(__file__).resolve().parent
TARGET = BASE / 'width_200'
SOURCE = BASE / 'width_lane_retry_v4pro'
TARGET_RUN = 'width_200_real_20261017'
SOURCE_RUN = 'width_lane_retry_v4pro_real_20261017'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    global TARGET, SOURCE, TARGET_RUN, SOURCE_RUN
    parser = argparse.ArgumentParser()
    parser.add_argument('--single', action='store_true')
    args = parser.parse_args()
    group = 'single_width_200' if args.single else 'width_200'
    source_group = 'single_width_retry_v4pro' if args.single else 'width_lane_retry_v4pro'
    count = 56 if args.single else 92
    flow = 'single' if args.single else 'normal'
    TARGET = BASE / group
    SOURCE = BASE / source_group
    TARGET_RUN = group + ('_real_20261019' if args.single else '_real_20261017')
    SOURCE_RUN = source_group + ('_real_20261019' if args.single else '_real_20261017')
    trace_name = group + '_trace.jsonl'
    with (TARGET / 'inputs.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    with (SOURCE / 'inputs.csv').open(encoding='utf-8-sig', newline='') as stream:
        replacements = list(csv.DictReader(stream))
    ids = {row['case_id'] for row in replacements}
    assert len(rows) == len({row['case_id'] for row in rows}) == 200
    assert len(replacements) == len(ids) == count
    assert ids <= {row['case_id'] for row in rows}
    original_events = [json.loads(line) for line in (TARGET / trace_name).read_text(encoding='utf-8').splitlines() if line.strip()]
    source_events = [json.loads(line) for line in (SOURCE / (source_group + '_trace.jsonl')).read_text(encoding='utf-8').splitlines() if line.strip()]
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    source_reviews = {r['case_id']: dict(r) for r in connection.execute('SELECT * FROM case_reviews WHERE run_id=?', (SOURCE_RUN,))}
    old_reviews = {r['case_id']: dict(r) for r in connection.execute('SELECT * FROM case_reviews WHERE run_id=?', (TARGET_RUN,))}
    assert set(source_reviews) == ids
    assert len(old_reviews) == 200
    assert all(r['decision'] != 'pending' for r in source_reviews.values())
    for item in replacements:
        case = SOURCE / 'cases' / item['case_id']
        result = read_json(case / 'execution_result.json')
        assert result['model'] == 'deepseek-v4-pro' and result['agent_flow'] == flow
        assert read_json(case / 'input.json') == item
    backup = BASE / 'backups' / (group + '_before_v4pro_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    backup.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(backup / 'request_tests.sqlite3') as destination:
        connection.backup(destination)
    for name in (trace_name, 'results.json', 'progress.json', 'run_config.json', 'inputs.csv'):
        shutil.copy2(TARGET / name, backup / name)
    write_json(backup / 'reviews_before.json', list(old_reviews.values()))
    stage = backup / 'staged'
    for case_id in sorted(ids):
        shutil.copytree(SOURCE / 'cases' / case_id, stage / case_id)
    (backup / 'cases').mkdir()
    for case_id in sorted(ids):
        (TARGET / 'cases' / case_id).rename(backup / 'cases' / case_id)
        (stage / case_id).rename(TARGET / 'cases' / case_id)
        result_path = TARGET / 'cases' / case_id / 'execution_result.json'
        result = read_json(result_path)
        result.update(group=group, source_group=source_group, source_run_id=SOURCE_RUN)
        write_json(result_path, result)
    progress = rebuild(group, rows)
    config = read_json(TARGET / 'run_config.json')
    config.update(model=f'mixed: deepseek-chat ({200-count}), deepseek-v4-pro ({count})',
                  merged_at=datetime.now().isoformat(),
                  replacement_count=count, replacement_source_run_id=SOURCE_RUN,
                  backup_directory=str(backup))
    write_json(TARGET / 'run_config.json', config)
    with connection:
        for case_id in sorted(ids):
            review = source_reviews[case_id]
            cursor = connection.execute(
                '''UPDATE case_reviews SET decision=?, note=?, review_source=?,
                   trace_source=?, metadata_json=?, created_at=?, updated_at=?
                   WHERE case_id=? AND run_id=? AND task_id=?''',
                (review['decision'], review['note'], review['review_source'],
                 trace_name, review['metadata_json'], review['created_at'],
                 review['updated_at'], case_id, TARGET_RUN, case_id))
            assert cursor.rowcount == 1
    merged = [json.loads(line) for line in (TARGET / trace_name).read_text(encoding='utf-8').splitlines() if line.strip()]
    def grouped(events, selected):
        result = {}
        for event in events:
            case_id = event['row'].get('case_id') or event['row'].get('task_id')
            if case_id not in selected:
                continue
            event = json.loads(json.dumps(event))
            event['row']['run_id'] = TARGET_RUN
            result.setdefault(case_id, []).append(event)
        return result
    all_ids = {row['case_id'] for row in rows}
    assert grouped(merged, all_ids - ids) == grouped(original_events, all_ids - ids)
    assert grouped(merged, ids) == grouped(source_events, ids)
    final_reviews = {r['case_id']: dict(r) for r in connection.execute('SELECT * FROM case_reviews WHERE run_id=?', (TARGET_RUN,))}
    for case_id in all_ids - ids:
        assert final_reviews[case_id] == old_reviews[case_id]
    for case_id in ids:
        for key in ('decision', 'note', 'review_source', 'metadata_json', 'created_at', 'updated_at'):
            assert final_reviews[case_id][key] == source_reviews[case_id][key]
    assert len(grouped(merged, all_ids)) == 200
    assert progress['finished'] == progress['total'] == 200
    summary = {'progress': progress, 'replaced': len(ids), 'backup': str(backup),
               'reviews': dict(Counter(r['decision'] for r in final_reviews.values())),
               'verified': f'{200-count} original event streams unchanged; {count} rerun event streams exact after run_id normalization; all 200 reviews verified'}
    write_json(TARGET / 'merge_v4pro_summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    connection.close()


if __name__ == '__main__':
    main()
