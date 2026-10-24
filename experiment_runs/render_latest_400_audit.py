"""Render current data without carrying old, resolved findings into the report."""
import json
import sqlite3
from run_real_batches import BASE,DATABASE

out=BASE/'audit_current_400'
data=json.loads((out/'audit.json').read_text(encoding='utf-8'))
extra=json.loads((out/'map_pair_integrity.json').read_text(encoding='utf-8'))
names={'width_200':'多智能体','single_width_200':'单智能体'}
labels={'actual_change_opposes_command':'实际平均宽度变化与命令方向相反',
        'actual_differs_from_projection':'实际平均宽度偏离预测超过1毫米',
        'increased_width_discontinuities':'分段接缝跳变量增大超过1毫米',
        'no_approved_tool_baseline_for_matching_dsl':'缺少匹配真实道路DSL的approved工具标准',
        'tool_baseline_expression_differs':'工具表达式与标准不同，需核对语义等价性'}
def count(group,key):return len(data['summary'][group]['observations'].get(key,[]))
lines=['# 单、多智能体各200条最新复核报告','',f"核查时间：{data['created_at']}",'',
 '本报告基于058、059重跑和460场景更换之后的最新200+200条。旧版审查已存档，本报告覆盖此前报告。未依赖前端匹配百分比，未修改数据库、人工审核记录或地图。','',
 '## 最新结论','',
 '- 两组均为200条完成执行，400个任务均具有可解析的before/after XODR与场景JSON。',
 '- 多、单058均已改为leftmost，选择-1符合最新需求。单059已选择-3；多059仍正确选择-3。',
 '- 两组460均切换至DEU_LocationAUpper.xodr和匹配场景，road_0存在-2车道，原始需求未变，实际只在目标道路的-2车道上发生本任务的直接修改。',
 '- 本轮确定性核查未发现目标道路/车道、增减方向、程度或数值提取、road_limit、公式结果范围、trace与执行命令、数据库需求关联、地图平均值记录的异常。这里的“未发现”仅适用于报告列出的检查项。',
 '- 398条已有匹配approved工具基准的判断不能由完成状态代替：实际仍有019、085、095、407、431两组各5条缺少匹配真实道路DSL的approved工具标准。因此不能报告所有400条都已通过严格的工具标准答案审核。',
 '- 底层地图修改仍存在宽度截断影响和接缝跳变问题，见下表。','',
 '## 数量','',
 '| 检查项 | 多智能体 | 单智能体 |','|---|---:|---:|',
 '| 需求/DSL/参数/命令检查发现的异常任务 | '+str(sum(bool(r['issues']) for r in data['cases'] if r['group']=='width_200'))+' | '+str(sum(bool(r['issues']) for r in data['cases'] if r['group']=='single_width_200'))+' |',
 '| 完成执行 | 200 | 200 |']
for key in ('actual_change_opposes_command','actual_differs_from_projection','increased_width_discontinuities','no_approved_tool_baseline_for_matching_dsl'):
    lines.append(f"| {labels[key]} | {count('width_200',key)} | {count('single_width_200',key)} |")
lines+=['','分类有重叠，不能相加。1毫米是此次筛查门槛，不是项目验收标准。预测偏差包含宽度截断和平滑的影响，不全部属于模型错误。','',
 '## 前后地图与场景完整差异检查','',
 f"- 共检查{extra['summary']['checked']}组前后地图及场景文件。",
 f"- 宽度a/b/c/d以外的地图结构或属性变化：{len(extra['summary']['non_width_map_changes'])}组。",
 f"- 车辆/路径点平面位置之外的场景变化：{len(extra['summary']['unexpected_scene_changes'])}组。",
 '- 车辆数量、ID、速度、旋转、天气等非位置字段未发现意外改动。',
 '- 所有before文件哈希均与执行来源记录一致；总trace与各任务trace一致；记录中的前后平均宽度与XODR积分结果一致。',
 '- 未发现新增负宽度分段；这并不证明不存在其他几何或轨迹问题。','',
 '## 已修正任务的实际结果','',
 '| 任务 | 组别 | 当前目标车道 | 实际修改后宽度 | 提醒 |','|---|---|---|---|---|',
 '| 058 | 两组 | -1 | 2.7米 | 目标1.3米，仍受下限截断 |',
 '| 059 | 两组 | -3 | 4米 | 目标4.4米，仍受上限截断 |',
 '| 460 | 两组 | -2 | 2.7米 | 原3.6998米；需求是定性缩窄 |','',
 '## 仍需解决的地图问题','',
 '1. 两组各26条的目标车道实际平均宽度变化与命令方向相反，典型情况是原始a超过4，加宽时反被截到4。',
 '2. 修改只截断每段a，不能保证前段末端a+bL+cL²+dL³与后段起点a相等；原有接缝可能被放大。',
 '3. 单靠重新调用模型不能消除上述编辑器行为；修复底层或明确截断/拒绝策略后，才能重新验收地图是否达到数值需求。','',
 '## 方向相反的任务明细','',
 '| 组别 | 任务 | 车道 | 修改前m | 修改后m | 命令增量m |','|---|---|---:|---:|---:|---:|']
for record in data['cases']:
    for obs in record['observations']:
        if obs['type']=='actual_change_opposes_command':
            lines.append(f"| {names[record['group']]} | {record['case_id']} | {obs['lane']} | {obs['before']} | {obs['after']} | {obs['command']} |")
lines+=['','## 当前数据库审核状态','']
with sqlite3.connect(DATABASE.resolve().as_uri()+'?mode=ro',uri=True) as connection:
    for group in names:
        run=group+('_real_20261019' if group.startswith('single') else '_real_20261017')
        counts=connection.execute('SELECT decision,count(*) FROM case_reviews WHERE run_id=? GROUP BY decision',(run,)).fetchall()
        lines.append('- '+names[group]+'：'+', '.join(f'{k}={v}' for k,v in counts)+'。本次未改审核状态。')
lines+=['','## 400条逐项记录','',
 '| 组别 | 任务 | 本次需求/数据异常 | 地图与基准观察 |','|---|---|---|---|']
for record in data['cases']:
    issues='；'.join(i['type'] for i in record['issues']) or '未发现所检查项异常'
    obs='；'.join(dict.fromkeys(labels.get(i['type'],i['type']) for i in record['observations'] if i['type']!='changed_roads')) or '无额外观察'
    lines.append(f"| {names[record['group']]} | {record['case_id']} | {issues} | {obs} |")
lines+=['','## 检查边界','',
 '核查来自实际文件和数值，不是400次浏览器逐图查看。已检查宽度多项式段内极值、同一laneSection内接缝和完整XML/JSON非预期变化。未验证跨laneSection/跨道路接缝的所有连续性、NPC对新车道的实际贴合、碰撞、三维渲染或仿真驾驶。']
# State only the measured baseline count; no inferred approval total.
lines=[line.replace('398条已有匹配approved工具基准的判断不能由完成状态代替：实际仍有','完成状态不能代替工具标准审核：仍有') for line in lines]
(out/'复核报告.md').write_text('\n'.join(lines),encoding='utf-8')
print(out/'复核报告.md')
