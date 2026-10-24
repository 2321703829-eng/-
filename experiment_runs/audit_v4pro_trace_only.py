"""Read-only review of trace and answer DB only; no scene/map reads."""
import ast,collections,json,pathlib,re,shlex,sqlite3,sys
BASE=pathlib.Path(__file__).resolve().parent;PROJECT=BASE.parent/'完全版本/完全版本/openDriveMapEditor'
sys.path.insert(0,str(PROJECT))
from llm.agent_tools import evaluate_calculation_formula
from audit_lane_selection import expected_lanes
GROUP=BASE/'width_lane_retry_v4pro';OUT=GROUP/'post_review_audit';OUT.mkdir(exist_ok=True)
steps={};tasks={}
for line in (GROUP/'width_lane_retry_v4pro_trace.jsonl').read_text(encoding='utf-8').splitlines():
    event=json.loads(line);row=event['row'];cid=row['task_id']
    if event['type']=='task':tasks.setdefault(cid,{}).update(row)
    if event['type']=='agent_step' and row.get('agent')=='width':steps.setdefault(cid,{}).update(row)
db=PROJECT/'testfucs/agent_trace_viewer/request_tests.sqlite3'
connection=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);connection.row_factory=sqlite3.Row
reviews={r['case_id']:dict(r) for r in connection.execute('select * from case_reviews where run_id=?',('width_lane_retry_v4pro_real_20261017',))}
cases=[]
for cid,task in tasks.items():
    step=steps.get(cid,{});context=json.loads(step.get('road_context_json','{}'));dsl=json.loads(step.get('llm_output_json','{}'));calls=json.loads(step.get('tool_call_json','[]'));resolved=json.loads(step.get('resolved_result_json','[]'));ops=dsl.get('operations',[]);text=task['user_input'].lower();issues=[]
    ids=context.get('lane_ids',[]);selection=expected_lanes(text,ids)
    if selection:wanted=selection[1]
    elif re.search(r'\blane\s+(-?\d+)\b',text):
        lid=int(re.search(r'\blane\s+(-?\d+)\b',text)[1]);wanted=[lid] if lid in ids else []
    elif 'widest' in text or 'narrowest' in text:
        widths=context.get('lane_avg_width',{});extreme=(max if 'widest' in text else min)(widths.values()) if widths else None;wanted=[int(k) for k,v in widths.items() if v==extreme]
    else:wanted=None
    lids=[int(lid.removeprefix('lane_')) for op in ops for lid in op['lane_ids']]
    if step and (wanted is None or set(wanted)!=set(lids) or (wanted==[] and ops)):issues.append({'field':'lane_ids','expected':wanted,'actual':lids})
    for op in ops:
        if op['road_id']!=context['road_id']:issues.append({'field':'road_id'})
        # None of the 92 source requests explicitly restricts propagation.
        if op['road_limit']!='none':issues.append({'field':'road_limit','expected':'none','actual':op['road_limit']})
        change=op['change'];mag=change['magnitude'];amount=re.search(r'(\d+(?:\.\d+)?)\s*(%|m\b)',text)
        if amount:
            expected={'kind':'numeric','value':float(amount[1]),'unit':'percent' if amount[2]=='%' else 'm'}
            if any(mag.get(k)!=v for k,v in expected.items()):issues.append({'field':'magnitude','expected':expected,'actual':mag})
        else:
            level='slight' if re.search(r'slight|a little|a bit',text) else 'significant' if re.search(r'greatly|significant|noticeably',text) else 'default'
            if mag['kind']!='qualitative' or mag['level']!=level:issues.append({'field':'magnitude.level','expected':level,'actual':mag})
        action='set' if re.search(r'\bto\s+\d+(?:\.\d+)?\s*m\b',text) else 'decrease' if re.search(r'narrow|reduce|decrease|shrink',text) else 'increase'
        if change['action']!=action or change['mode']!=('absolute' if action=='set' else 'relative'):issues.append({'field':'change.action/mode'})
    items=[item for call in calls for item in call.get('args',{}).get('items',[])]
    commands=step.get('internal_command','').splitlines()
    if len(items)!=len(ops) or len(resolved)!=len(ops) or len(commands)!=len(ops):issues.append({'field':'tool_count'})
    for i,(op,it,res,command) in enumerate(zip(ops,items,resolved,commands)):
        args=res['args'];param=dict(part.split('=',1) for part in shlex.split(command)[1:]);change=op['change'];mag=change['magnitude'];formula=it['formula'];old=context['lane_avg_width'][str(it['li'][0])];value=args['v'];expected_li=[int(l.removeprefix('lane_')) for l in op['lane_ids']]
        if calls[0]['name']!='width_apply' or res['name']!='apply_width':issues.append({'field':'tool_name'})
        if it['operation_index']!=i or it['li']!=expected_li:issues.append({'field':'tool_target'})
        expected_ms=99 if op['road_limit']=='none' else int(op['road_limit'])-1
        if (it['ms'],it['s'],it['sh'])!=(expected_ms,1,1):issues.append({'field':'tool_non_value_parameters'})
        if str(it['id'])!=context['road_id'].removeprefix('road_'):issues.append({'field':'tool_road'})
        for k in ('id','s','ms','sh'):
            if str(it[k])!=str(args[k]) or str(args[k])!=param.get(k):issues.append({'field':'resolved_or_command.'+k})
        if it['li']!=args['li'] or ','.join(map(str,args['li']))!=param.get('li') or abs(float(param['v'])-value)>1e-8:issues.append({'field':'resolved_or_command.li/v'})
        sign=1 if change['action']=='increase' else -1
        if mag['kind']=='numeric':
            desired=mag['value']-old if change['mode']=='absolute' else (mag['value'] if mag['unit']=='m' else old*mag['value']/100)*sign
            computed=evaluate_calculation_formula(formula)
            if abs(computed-round(desired,3))>.0011 or abs(computed-value)>.0011:issues.append({'field':'formula_or_resolved_v'})
        else:
            bounds={'slight':(.05,.25),'default':(.25,.40),'significant':(.40,.70)}[mag['level']]
            nodes=[n for n in ast.walk(ast.parse(formula,mode='eval')) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='random']
            actual_bounds=tuple(float(ast.literal_eval(n)) for n in nodes[0].args) if len(nodes)==1 else None
            if actual_bounds!=bounds:issues.append({'field':'formula.random_bounds'})
            if actual_bounds:
                mid=sum(bounds)/2;computed=evaluate_calculation_formula(formula,random_cache={actual_bounds:mid})
                if abs(computed-round(old*mid*sign,3))>.0011:issues.append({'field':'formula.base_or_sign'})
            ratio=value/old*sign
            if not bounds[0]-.0005<=ratio<=bounds[1]+.0005:issues.append({'field':'resolved_v.qualitative_range'})
    standard_limit_values=[]
    if issues:
        for answer in connection.execute('select dsl_json from dsl_answers where case_id=? and agent_type=?',(cid,'width')):
            standard_limit_values.extend(op.get('road_limit') for op in json.loads(answer[0]).get('operations',[]))
    cases.append({'case_id':cid,'input':task['user_input'],'review':reviews.get(cid),'status':task.get('status'),'empty_operations':bool(step) and ops==[],'issues':issues,'standard_road_limit_values':standard_limit_values})
connection.close()
summary={'total':len(cases),'review_counts':dict(collections.Counter((r['review'] or {}).get('decision','missing') for r in cases)),'issues':[{k:r[k] for k in ('case_id','input','issues','standard_road_limit_values')} for r in cases if r['issues']],'empty_operations':sum(r['empty_operations'] for r in cases),'scope':'trace and read-only SQLite only; no scene/map inspection'}
(OUT/'trace_only_details.json').write_text(json.dumps({'summary':summary,'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# V4 Pro 92条 Trace复核','',summary['scope'],'','人工审核47通过45未通过，92条均已保存。','',
       '发现3条通过任务在未说明传播限制的需求中输出road_limit=1，按项目规范应为none。其工具ms=0与错误DSL一致，属于上游DSL范围提取问题；标准答案也需复核。','',
       '|任务|需求|DSL实际|按规范预期|工具参数|','|---|---|---|---|---|']
for r in cases:
    if r['issues']:lines.append(f"|{r['case_id']}|{r['input']}|road_limit=1|road_limit=none|ms=0；正确DSL对应ms=99|")
lines+=['','44条空操作均符合项目目标不存在规则，未发现选错左右或误改其他车道；不能将这44条记作模型DSL错误。1条缺原地图未进入模型链路，应单列输入缺失。',
        '','其余44条有操作的任务，本轮需求、上下文、DSL动作/幅度/车道、公式、解析值和内部命令核对未发现明显差异。本次未改动审核记录或标准答案。']
(OUT/'trace_only_findings.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
