"""Index the actual before/after artifacts of the merged width experiments."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
for group in ('width_200', 'single_width_200'):
    root = BASE / group
    results = json.loads((root / 'results.json').read_text(encoding='utf-8'))
    lines = ['# 修改前后地图文件索引', '',
             '每条任务使用同一目录中的 XODR 与 JSON 配对加载。空操作没有生成修改后文件。', '',
             '| 任务 | 修改前 XODR | 修改前 JSON | 修改后 XODR | 修改后 JSON | 说明 |',
             '| --- | --- | --- | --- | --- | --- |']
    for result in results:
        case_id = result['case_id']
        folder = root / 'cases' / case_id
        links = []
        for filename in ('before.xodr', 'before.json', 'after.xodr', 'after.json'):
            path = folder / filename
            links.append(f'[{filename}]({path.as_posix()})' if path.is_file() else '无')
        note = '已生成修改后文件' if result['status'] == 'done' else (
            '空操作，未生成修改后文件' if 'No executable' in result.get('error', '') else '原始地图缺失')
        lines.append('| ' + ' | '.join([case_id, *links, note]) + ' |')
    (root / 'map_files_index.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(group, len(results))
