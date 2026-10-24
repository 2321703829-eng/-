"""Render the independent 400-case audit as a readable Chinese report."""
import json
from pathlib import Path

base=Path(__file__).resolve().parent
folder=base/'audit_current_400'
data=json.loads((folder/'audit.json').read_text(encoding='utf-8'))
names={'width_200':'多智能体','single_width_200':'单智能体'}
labels={
 'execution_error':'缺少源地图，执行失败',
 'wrong_target_lanes':'按现有方向规则选错车道',
 'dsl_standard_mismatch':'DSL与真实道路标准答案不一致',
 'actual_change_opposes_command':'目标车道平均宽度变化与命令方向相反',
 'actual_differs_from_projection':'实际平均宽度变化偏离预测超过1毫米',
 'increased_width_discontinuities':'同一laneSection内宽度分段接缝跳变量增大超过1毫米',
 'no_approved_tool_baseline_for_matching_dsl':'匹配的真实道路DSL缺少approved工具标准答案',
}
lines=['# 单、多智能体各200条独立复核报告','',
 '范围：当前已经合并的新版本；读取原始需求、最终DSL、公式与工具参数、实际执行命令、数据库候选、修改前后XODR与场景JSON。未采用前端匹配百分比作为结论。',
 '此次审查不修改数据库、人工审核结论、trace或地图。详细原始数据见同目录 audit.json。','',
 '## 结论','',
 '- 多智能体058、单智能体058：只有负ID车道，却将“左侧第一条”替换成-1。按现有规则应为空操作。数据库的真实道路候选也保存成-1，因此与标准一致仍不代表语义正确。',
 '- 单智能体059：右侧第一条应取负ID绝对值最大的-3，实际修改-1；多智能体059选对-3。',
 '- 两组460均缺少源XODR，没有前后地图，不能算执行成功。',
 '- 两组各26条存在目标车道平均宽度变化与命令方向相反。原因与底层将宽度多项式a系数截断到[2.7,4]有关；原始a大于4时，加宽操作可能反而把它压到4。模型选对车道、公式正确与地图达到需求应分别评价。',
 '- 没有再发现road_limit填1、命令拼接覆盖、公式计算超出规则范围、trace命令与实执行命令不一致、上下文平均宽度与before地图不一致、实际平均宽度与执行记录不一致。',
 '- 所有398个已完成任务的before/after XODR与JSON均可解析，before文件哈希与原执行来源记录一致，总trace与对应任务trace一致；未发现新增负宽度分段。',
 '- 宽度分段接缝存在增大的跳变（多103条、单101条）。这是地图几何质量问题，可能是原有不连续被放大，不能全算新增接缝。',
 '- 真实道路工具标准答案缺失：两组均为019、085、095、407、431。单059因DSL已经不匹配，也没有匹配DSL下的工具基准。', '',
 '## 分类数量','',
 '| 检查项 | 多智能体 | 单智能体 |','|---|---:|---:|',
 '| 已完成实际执行 | 199 | 199 |',
 '| 方向/目标车道不符合现有规则 | 1 | 2 |',
 '| 缺源地图 | 1 | 1 |',
 '| 实际变化与命令方向相反 | 26 | 26 |',
 '| 实际变化偏离预测超过1毫米 | 158 | 160 |',
 '| 分段接缝跳变量增大超过1毫米 | 103 | 101 |','',
 '上述分类相互重叠，不能相加。1毫米仅为本次筛查门槛，不是项目规定的验收容差；偏离预测包含宽度截断与平滑影响，不能一律判为模型错误。','',
 '## 需要优先处理','',
 '1. 058：若本意是最左车道，明确将需求改成leftmost后重新运行单、多两组；若保留“左侧第一条”，则应返回空操作。不能直接将错误选道候选继续作为标准。',
 '2. 单059：按当前规则应选-3，应修正或重跑；即便选对车道，4.4米目标仍会受到编辑器4米a系数上限限制，不能自动认为地图达标。',
 '3. 26条方向相反：需要先决定编辑器如何处理原始宽度超界以及不可达目标，再修复底层并重新生成地图。仅重跑模型一般无法解决。',
 '4. 地图验收应使用after.xodr积分后的实际平均宽度；审查器road_metric目前是预测值。',
 '5. 接缝跳变需要另行确定几何质量要求；当前代码只限制a，不保证分段宽度连续或全段宽度上限。',
 '6. 补齐019、085、095、407、431的真实道路工具标准；460先补源地图。', '',
 '## 26条方向相反的实际地图结果','',
 '| 组别 | 任务 | 车道 | 修改前m | 修改后m | 命令增量m |',
 '|---|---|---:|---:|---:|---:|']
for record in data['cases']:
 for obs in record['observations']:
  if obs['type']=='actual_change_opposes_command':
   lines.append(f"| {names[record['group']]} | {record['case_id']} | {obs['lane']} | {obs['before']} | {obs['after']} | {obs['command']} |")
lines+=['','## 分段接缝实例','',
 '472两组：road_55的lane_-1，第7条width记录末端约2.914629米，下一条记录起点为4米，跳变约1.085371米；修改前同一位置已有约0.538468米跳变，本次将其放大。此处是传播影响到的道路，并不一定是需求中直接指定的道路。','',
 '## 400条逐项索引','',
 '| 组别 | 任务 | 人工结论 | 本次语义/数据问题 | 实际地图观察 |','|---|---|---|---|---|']
for record in data['cases']:
 issues='；'.join(dict.fromkeys(labels.get(i['type'],i['type']) for i in record['issues'])) or '未发现所检查项异常'
 obs='；'.join(dict.fromkeys(labels.get(i['type'],i['type']) for i in record['observations'] if i['type']!='changed_roads')) or '无额外观察'
 lines.append(f"| {names[record['group']]} | {record['case_id']} | {record['human_review']} | {issues} | {obs} |")
lines+=['','## 检查边界','',
 '这是对文件和数值的独立复核，不是400次浏览器逐图人工观察。已检查宽度多项式段内极值和同一laneSection内接缝；未完成跨laneSection/跨道路接缝、三维渲染、NPC修改后贴合度、轨迹碰撞或仿真驾驶验证。没有发现所检查项异常，不等于场景所有方面都正确。']
(folder/'复核报告.md').write_text('\n'.join(lines),encoding='utf-8')
print(folder/'复核报告.md')
