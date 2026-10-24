"""Read-only per-case comparison, independent of all viewer matching code."""
import ast, collections, json, re, pathlib, copy
ROOT=pathlib.Path(__file__).resolve().parent
OUT=ROOT/'width_200/failure_analysis'
snap=json.loads((OUT/'report_snapshot.json').read_text(encoding='utf-8'))
triage={c['case_id']:c for c in json.loads((OUT/'rejected_width_details.json').read_text(encoding='utf-8'))['cases']}
steps={}
for line in (ROOT/'width_200/width_200_trace.jsonl').read_text(encoding='utf-8').splitlines():
    event=json.loads(line); row=event.get('row',{})
    if event['type']=='agent_step' and row.get('agent')=='width':
        steps.setdefault(row['task_id'],{}).setdefault(str(row['step_id']),{}).update(row)
def parse(x):
    try:return json.loads(x) if isinstance(x,str) else x
    except (ValueError,TypeError):return None
def road(x): return str(x).removeprefix('road_')
def normalize(x,actual,key=''):
    if isinstance(x,dict):return {k:normalize(v,actual,k) for k,v in x.items()}
    if isinstance(x,list):return [normalize(v,actual,key) for v in x]
    if key in ('road_id','id') and road(x) in ('21',actual):return '@ROAD'
    if key=='formula' and isinstance(x,str):
        try:
            tree=ast.parse(x.strip(),mode='eval')
            for node in ast.walk(tree):
                if isinstance(node,ast.Constant) and type(node.value) in (int,float):node.value=float(node.value)
            return ast.dump(tree,include_attributes=False)
        except (ValueError,SyntaxError):return re.sub(r'\s+','',x)
    return x
def diff(a,b,p='$'):
    if isinstance(a,dict) and isinstance(b,dict):
        return [z for k in sorted(a.keys()|b.keys()) for z in (diff(a.get(k,'<缺失>'),b.get(k,'<缺失>'),p+'.'+k))]
    if isinstance(a,list) and isinstance(b,list):
        return ([{'field':p+'.length','actual':len(a),'standard':len(b)}] if len(a)!=len(b) else [])+[z for i,(aa,bb) in enumerate(zip(a,b)) for z in diff(aa,bb,f'{p}[{i}]')]
    return [] if a==b else [{'field':p,'actual':a,'standard':b}]
def compare(value,candidates,actual,label):
    if value is None or value==[] or value=={}:return {'decision':'no_output','reason':f'没有{label}输出','diffs':[]}
    if not candidates:return {'decision':'no_baseline','reason':f'没有可用{label}标准答案','diffs':[]}
    tests=[(diff(normalize(value,actual),normalize(v,actual)),i) for i,v in candidates]
    ds,answer_id=min(tests,key=lambda t:len(t[0]))
    return {'decision':'pass' if not ds else 'fail','reason':'与标准答案一致' if not ds else '字段不一致','answer_id':answer_id,'diffs':ds}
results=[]
for review in sorted(snap['reviews'],key=lambda r:r['case_id']):
    cid=review['case_id']; data=snap['cases'][cid]
    task=json.loads((ROOT/'width_200/cases'/cid/'input.json').read_text(encoding='utf-8'))
    actual=road(task['actual_road_id'])
    standards=data['width']; real=[a for a in standards if road(a['road_id'])==actual]
    selected=real or [a for a in standards if road(a['road_id'])=='21']
    final_steps=list(steps.get(cid,{}).values())
    final=final_steps[-1] if final_steps else {}
    # This width-only run has one logical width step for every case with output.
    assert len(final_steps)<=1,(cid,len(final_steps))
    dslc=[(a['dsl_answer_id'],a['dsl']) for a in selected]
    toolc=[(t['tool_answer_id'],t['tool_calls']) for a in selected for t in a.get('tool_answers',[]) if t['review_status']=='approved']
    dsl=compare(parse(final.get('llm_output_json')),dslc,actual,'DSL')
    tool=compare(parse(final.get('tool_call_json')),toolc,actual,'工具调用')
    results.append({'case_id':cid,'human':review['decision'],'input':task['input_text'],'road':actual,'baseline_scope':'真实道路' if real else '需求道路映射','dsl':dsl,'tool':tool,'triage':triage.get(cid),'standard_dsl_ids':[a['dsl_answer_id'] for a in selected]})
summary={s:dict(collections.Counter(r[s]['decision'] for r in results)) for s in ['dsl','tool']}
out={'method':'独立字段比较；优先真实道路候选；无真实候选时比较需求道路候选并仅归一化road21到实际road；忽略JSON对象键顺序；formula比较表达式树，忽略空白、多余括号及2和2.0之类数字写法差异，不做代数变换；其余字段保持不变。工具阶段独立比较所有相关approved候选，不依赖DSL先通过。','summary':summary,'cases':results}
(OUT/'independent_comparison.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
for s in ['dsl','tool']:
    print(s,collections.Counter(d['field'] for r in results if r[s]['decision']=='fail' for d in r[s]['diffs']))
