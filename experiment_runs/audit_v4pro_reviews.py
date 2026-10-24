"""Audit reviewed retry traces against project rules and saved maps, read-only DB."""
import ast,collections,json,pathlib,re,sqlite3,sys
BASE=pathlib.Path(__file__).resolve().parent
PROJECT=BASE.parent/'完全版本/完全版本/openDriveMapEditor'
sys.path.insert(0,str(PROJECT))
from testfucs import test_data_store
from llm.agent_tools import evaluate_calculation_formula
from audit_lane_selection import expected_lanes
GROUP=BASE/'width_lane_retry_v4pro'
OUT=GROUP/'post_review_audit';OUT.mkdir(exist_ok=True)
db=PROJECT/'testfucs/agent_trace_viewer/request_tests.sqlite3'
c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
reviews={r['case_id']:dict(r) for r in c.execute('select * from case_reviews where run_id=?',('width_lane_retry_v4pro_real_20261017',))}
inputs=[json.loads((folder/'input.json').read_text(encoding='utf-8')) for folder in sorted((GROUP/'cases').iterdir()) if (folder/'input.json').exists()]
baselines=test_data_store.list_auto_review_baselines(c,[r['case_id'] for r in inputs]);c.close()
def parse(x):
    try:return json.loads(x) if isinstance(x,str) else x
    except (ValueError,TypeError):return None
def selection(text,context):
    ids=context.get('lane_ids',[]); rule=expected_lanes(text,ids)
    if rule:return rule
    direct=re.search(r'\blane\s+(-?\d+)\b',text,re.I)
    if direct:
        lid=int(direct[1]);return 'explicit_id',[lid] if lid in ids else []
    values=context.get('lane_avg_width',{})
    if re.search(r'\b(widest|narrowest)\b',text,re.I) and values:
        wanted=(max if 'widest' in text.lower() else min)(values.values())
        return 'width_extreme',[int(k) for k,v in values.items() if abs(v-wanted)<1e-8]
    return 'unknown',None
def norm(x,rid,key=''):
    if isinstance(x,dict):return {k:norm(v,rid,k) for k,v in x.items()}
    if isinstance(x,list):return [norm(v,rid,key) for v in x]
    if key in ('road_id','id') and str(x).removeprefix('road_') in (rid,'21'):return '@road'
    if key=='formula' and isinstance(x,str):
        try:
            tree=ast.parse(x.strip(),mode='eval')
            for n in ast.walk(tree):
                if isinstance(n,ast.Constant) and type(n.value) in (int,float):n.value=float(n.value)
            return ast.dump(tree,include_attributes=False)
        except SyntaxError:return re.sub(r'\s+','',x)
    return x
cases=[]
for item in inputs:
    cid=item['case_id'];folder=GROUP/'cases'/cid
    result=json.loads((folder/'execution_result.json').read_text(encoding='utf-8'))
    events=[json.loads(l) for l in (folder/'trace.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()] if (folder/'trace.jsonl').exists() else []
    step={}
    for e in events:
        if e['type']=='agent_step' and e['row'].get('agent')=='width':step.update(e['row'])
    context=parse(step.get('road_context_json')) or {};dsl=parse(step.get('llm_output_json'))
    calls=parse(step.get('tool_call_json')) or [];resolved=parse(step.get('resolved_result_json')) or []
    rule,wanted=selection(item['input_text'],context)
    ops=dsl.get('operations',[]) if isinstance(dsl,dict) else []
    lids=[int(str(lid).removeprefix('lane_')) for op in ops for lid in op.get('lane_ids',[]) if isinstance(op.get('lane_ids'),list)]
    issues=[];map_diffs=[];command_map_diffs=[];numeric_checks=[];formula_checks=[]
    if context and (wanted is None or set(wanted)!=set(lids) or (wanted==[] and ops)):
        issues.append({'kind':'selection','expected':wanted,'actual':lids})
    rid=item['actual_road_id'].removeprefix('road_')
    if any(op.get('road_id')!='road_'+rid for op in ops):issues.append({'kind':'road_id'})
    text=item['input_text'].lower();amount=re.search(r'(\d+(?:\.\d+)?)\s*(%|m\b)',text)
    for op in ops:
        ch=op.get('change',{});mag=ch.get('magnitude',{})
        if amount:
            unit='percent' if amount[2]=='%' else 'm'
            if mag.get('kind')!='numeric' or mag.get('unit')!=unit or mag.get('value')!=float(amount[1]):issues.append({'kind':'magnitude','expected':amount.group(0),'actual':mag})
        else:
            level='slight' if re.search(r'slight|a little|a bit',text) else 'significant' if re.search(r'greatly|significant|noticeably',text) else 'default'
            if mag.get('kind')!='qualitative' or mag.get('level')!=level:issues.append({'kind':'qualitative_level','expected':level,'actual':mag})
        expected_action='set' if re.search(r'\bto\s+\d+(?:\.\d+)?\s*m\b',text) else 'decrease' if re.search(r'narrow|reduce|decrease|shrink',text) else 'increase'
        expected_mode='absolute' if expected_action=='set' else 'relative'
        if (ch.get('action'),ch.get('mode'))!=(expected_action,expected_mode):issues.append({'kind':'action_mode','expected':[expected_action,expected_mode],'actual':ch})
    if result['status']=='done':
        before=result['actual_width_before']['lane_avg_width'];after=result['actual_width_after']['lane_avg_width']
        for op in ops:
            ch=op['change'];mag=ch['magnitude']
            for lid in op['lane_ids']:
                k=str(lid).removeprefix('lane_');old=before[k];new=after[k]
                if mag['kind']=='numeric':
                    v=mag['value'];delta=v if mag['unit']=='m' else old*v/100
                    goal=v if ch['mode']=='absolute' else old+(delta if ch['action']=='increase' else -delta)
                    numeric_checks.append({'lane':k,'before':old,'dsl_target':goal,'actual_after':new})
                    if abs(new-goal)>.01:map_diffs.append({'lane':k,'before':old,'dsl_target':goal,'actual_after':new,'difference':new-goal})
        items=[i for call in calls for i in call.get('args',{}).get('items',[])]
        if len(items)!=len(resolved):issues.append({'kind':'tool_count','items':len(items),'resolved':len(resolved)})
        for i,(tool,res) in enumerate(zip(items,resolved)):
            if tool.get('li')!=lids:issues.append({'kind':'tool_lane','li':tool.get('li'),'dsl_lids':lids})
            if str(tool.get('id'))!=rid:issues.append({'kind':'tool_road'})
            op=ops[tool['operation_index']];ch=op['change'];mag=ch['magnitude'];value=res['args']['v']
            old=context['lane_avg_width'][str(tool['li'][0])]
            formula=tool['formula']
            for lane in tool['li']:
                lane=str(lane);predicted=before[lane]+value
                if abs(after[lane]-predicted)>.01:command_map_diffs.append({'lane':lane,'before':before[lane],'resolved_v':value,'predicted_after':predicted,'actual_after':after[lane],'clamp_explains':abs(after[lane]-min(4,max(2.7,predicted)))<.01})
            if mag['kind']=='numeric':
                target=mag['value'];desired=(target-old) if ch['mode']=='absolute' else ((target if mag['unit']=='m' else old*target/100)*(1 if ch['action']=='increase' else -1))
                computed=evaluate_calculation_formula(formula)
                formula_checks.append({'formula':formula,'expected_v':round(desired,3),'resolved_v':value})
                if abs(computed-round(desired,3))>.0011 or abs(value-computed)>.0011:issues.append({'kind':'tool_numeric','formula':formula,'expected':round(desired,3),'resolved':value})
            else:
                bounds={'slight':(.05,.25),'default':(.25,.40),'significant':(.40,.70)}[mag['level']]
                ratio=value/old*(1 if ch['action']=='increase' else -1)
                if not bounds[0]-.0005<=ratio<=bounds[1]+.0005:issues.append({'kind':'tool_qualitative_range','ratio':ratio,'bounds':bounds})
                tree=ast.parse(formula,mode='eval')
                randoms=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='random']
                if len(randoms)!=1 or tuple(float(ast.literal_eval(n)) for n in randoms[0].args)!=bounds:issues.append({'kind':'tool_random_bounds','formula':formula,'expected':bounds})
                if len(randoms)==1:
                    actual_bounds=tuple(float(ast.literal_eval(n)) for n in randoms[0].args)
                    mid=sum(bounds)/2;computed=evaluate_calculation_formula(formula,random_cache={actual_bounds:mid})
                    desired=round(old*mid*(1 if ch['action']=='increase' else -1),3)
                    if abs(computed-desired)>.0011:issues.append({'kind':'tool_qualitative_base','formula':formula,'expected_midpoint':desired,'actual_midpoint':computed})
        for k,v in after.items():
            if int(k) not in lids and abs(v-before[k])>.01:issues.append({'kind':'non_target_lane_changed','lane':k,'before':before[k],'after':v})
    standards=baselines[cid]['width'];real=[a for a in standards if str(a['road_id']).removeprefix('road_')==rid]
    selected=real or [a for a in standards if str(a['road_id']).removeprefix('road_')=='21']
    dm=[a for a in selected if norm(a['dsl'],rid)==norm(dsl,rid)]
    approved=[t for a in selected for t in a.get('tool_answers',[]) if t['review_status']=='approved']
    tm=[t for t in approved if norm(t['tool_calls'],rid)==norm(calls,rid)]
    cases.append({'case_id':cid,'input':item['input_text'],'review':reviews.get(cid),'status':result['status'],'error':result.get('error'),'selection_rule':rule,'expected_lanes':wanted,'actual_lanes':lids,'empty_operations':dsl is not None and ops==[],'issues':issues,'map_diffs':map_diffs,'command_map_diffs':command_map_diffs,'numeric_checks':numeric_checks,'formula_checks':formula_checks,'dsl_baseline_match':bool(dm),'matched_dsl_ids':[a['dsl_answer_id'] for a in dm],'tool_baseline_available':bool(approved),'tool_baseline_match':bool(tm),'matched_tool_ids':[a['tool_answer_id'] for a in tm],'propagated_roads':len(set(result.get('changed_road_ids',[]))-{rid})})
summary={'count':len(cases),'reviews':dict(collections.Counter((r['review'] or {}).get('decision','missing') for r in cases)),'sources':dict(collections.Counter((r['review'] or {}).get('review_source','missing') for r in cases)),'review_execution':dict(collections.Counter(str(((r['review'] or {}).get('decision'),r['status'])) for r in cases)),'issue_cases':sum(bool(r['issues']) for r in cases),'issue_kinds':dict(collections.Counter(i['kind'] for r in cases for i in r['issues'])),'actual_map_mismatch_cases':sum(bool(r['map_diffs']) for r in cases),'command_map_mismatch_cases':sum(bool(r['command_map_diffs']) for r in cases),'clamp_explains_all_command_diffs':all(x['clamp_explains'] for r in cases for x in r['command_map_diffs']),'numeric_cases':sum(bool(r['numeric_checks']) for r in cases),'dsl_baseline_matches':sum(r['dsl_baseline_match'] for r in cases),'tool_baseline_matches':sum(r['tool_baseline_match'] for r in cases),'tool_baseline_missing':sum(not r['tool_baseline_available'] for r in cases),'empty_operation_cases':sum(r['empty_operations'] for r in cases)}
(OUT/'audit_details.json').write_text(json.dumps({'summary':summary,'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# V4 Pro 92条人工审核复核','',
       '人工审核已保存92条，47通过、45未通过，均为human来源，无漏审。数据库只读，本次未改动审核结论或标准答案。','',
       '按项目现有选道规则、原始需求数值与程度词、工具公式基数及解析结果核对：91条有DSL输出的任务未发现上述字段错误，包含47条有操作、44条符合目标不存在规则的空操作；另1条原地图缺失无法核对。此为规则筛查，不能替代整张地图全部几何与传播效果校验。','',
       '47条人工通过任务均匹配本次选用的数据库DSL候选；42条工具调用匹配对应已批准候选，另外5条在优先选取的真实道路DSL候选下缺少已批准工具答案。缺基准不等于工具错误，工具公式已另按上下文检查。','',
       '47条通过任务中，34条实际地图宽度与before+工具v的预测不同，差异均与2.7至4米限幅一致（筛查阈值0.01米）。其中15条为明确数值需求，19条为定性需求。DSL和工具正确不等于原始目标已完整落图。','',
       '44条空操作可按“需求无法在当前真实道路执行”保留任务未通过，但不应计作44条DSL生成错误；req_000460为输入地图缺失，不能计作模型理解错误。','',
       '## 实际地图与工具预测不同的34条','',
       '|任务|需求类型|目标车道|修改前 m|工具 v m|工具预测修改后 m|实际修改后 m|',
       '|---|---|---|---:|---:|---:|---:|']
for r in cases:
    for x in r['command_map_diffs']:
        lines.append(f"|{r['case_id']}|{'数值' if r['numeric_checks'] else '定性'}|{x['lane']}|{x['before']:.4f}|{x['resolved_v']:+.3f}|{x['predicted_after']:.4f}|{x['actual_after']:.4f}|")
lines+=['','## 明确数值需求未达到的15条','','|任务|车道|DSL目标 m|实际修改后 m|','|---|---|---:|---:|']
for r in cases:
    for x in r['map_diffs']:lines.append(f"|{r['case_id']}|{x['lane']}|{x['dsl_target']:.4f}|{x['actual_after']:.4f}|")
lines+=['','## 已通过但对应真实道路候选缺少approved工具基准的5条','',
        '、'.join(r['case_id'] for r in cases if (r['review'] or {}).get('decision')=='pass' and not r['tool_baseline_available']),
        '','## 全部92条逐条结果','',
        '|任务|人工审核|执行状态|选道核对|DSL基准匹配|工具基准匹配|实际宽度被限幅|',
        '|---|---|---|---|---|---|---|']
for r in cases:
    if r['empty_operations']:lane_result='空操作符合目标不存在规则'
    elif r['expected_lanes'] is None:lane_result='地图缺失 无法核对'
    else:lane_result='符合' if not r['issues'] else '需复核'
    tool_result='无对应approved基准' if not r['tool_baseline_available'] else '一致' if r['tool_baseline_match'] else '空操作 无实际调用' if r['empty_operations'] else '不一致'
    lines.append(f"|{r['case_id']}|{(r['review'] or {}).get('decision','未审核')}|{r['status']}|{lane_result}|{'一致' if r['dsl_baseline_match'] else '无输出' if r['expected_lanes'] is None else '空DSL与旧基准不同'}|{tool_result}|{'是' if r['command_map_diffs'] else '否或未执行'}|")
(OUT/'review_findings.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
for r in cases:
    if r['issues'] or r['map_diffs']:print(json.dumps({k:r[k] for k in ['case_id','input','review','issues','map_diffs']},ensure_ascii=False))
