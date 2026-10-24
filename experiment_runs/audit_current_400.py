"""Read-only independent audit of current merged width runs and actual map pairs."""
import ast
import collections
import csv
import json
import math
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from run_real_batches import BASE, DATABASE, PROJECT, trace_commands, write_json, sha
sys.path.insert(0, str(PROJECT.parent / 'ResultAna'))
from visualization.scenario_visualizer import _lane_average_widths
from audit_lane_selection import expected_lanes

def parsed(x):
    return json.loads(x) if isinstance(x, str) else x

def interval(expression):
    def calc(n):
        if isinstance(n, ast.Constant) and type(n.value) in (float,int): return (float(n.value),)*2
        if isinstance(n, ast.UnaryOp):
            a,b=calc(n.operand)
            if isinstance(n.op,ast.USub): return -b,-a
            if isinstance(n.op,ast.UAdd): return a,b
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='random' and len(n.args)==2:
            return calc(n.args[0])[0],calc(n.args[1])[1]
        if isinstance(n,ast.BinOp):
            a,b=calc(n.left);c,d=calc(n.right)
            if isinstance(n.op,ast.Add):return a+c,b+d
            if isinstance(n.op,ast.Sub):return a-d,b-c
            if isinstance(n.op,ast.Mult):v=[a*c,a*d,b*c,b*d];return min(v),max(v)
            if isinstance(n.op,ast.Div) and not c<=0<=d:v=[a/c,a/d,b/c,b/d];return min(v),max(v)
        raise ValueError('unsupported formula')
    return calc(ast.parse(expression.strip(),mode='eval').body)

def normalized(x,rid,key=''):
    if isinstance(x,dict):return {k:normalized(v,rid,k) for k,v in x.items()}
    if isinstance(x,list):return [normalized(v,rid,key) for v in x]
    if key in ('road_id','id') and str(x).removeprefix('road_') in ('21',rid):return '@ROAD'
    return x

def canonical_tool(payload,rid):
    if isinstance(payload,dict):payload=payload.get('tool_calls',payload.get('plans',[payload]))
    calls=[]
    for call in payload:
        calls.extend(call.get('tool_calls',[call]))
    value=normalized(calls,rid)
    def rewrite(x,key=''):
        if isinstance(x,dict):return {k:rewrite(v,k) for k,v in x.items()}
        if isinstance(x,list):return [rewrite(v,key) for v in x]
        if key=='formula':
            tree=ast.parse(x.strip(),mode='eval')
            for node in ast.walk(tree):
                if isinstance(node,ast.Constant) and type(node.value) in (float,int):node.value=float(node.value)
            return ast.dump(tree,include_attributes=False)
        return x
    return rewrite(value)

def geometry(root):
    ranges={};jumps={}
    for road in root.findall('road'):
        sections=road.findall('lanes/laneSection')
        for si,section in enumerate(sections):
            length=(float(sections[si+1].get('s')) if si+1<len(sections) else float(road.get('length')))-float(section.get('s'))
            for lane in section.findall('./left/lane')+section.findall('./right/lane'):
                widths=lane.findall('width')
                for wi,w in enumerate(widths):
                    start=float(w.get('sOffset'));end=float(widths[wi+1].get('sOffset')) if wi+1<len(widths) else length
                    L=max(0,end-start);a,b,c,d=[float(w.get(k,'0')) for k in 'abcd']
                    at=lambda t:a+b*t+c*t*t+d*t*t*t
                    points=[0,L]
                    if abs(d)>1e-15:
                        disc=4*c*c-12*d*b
                        if disc>=0:points.extend(t for t in ((-2*c+math.sqrt(disc))/(6*d),(-2*c-math.sqrt(disc))/(6*d)) if 0<t<L)
                    elif abs(c)>1e-15 and 0< -b/(2*c)<L:points.append(-b/(2*c))
                    key=f"{road.get('id')}:{si}:{lane.get('id')}:{wi}"
                    values=[at(t) for t in points];ranges[key]=(min(values),max(values))
                    if wi+1<len(widths):jumps[key]=abs(at(L)-float(widths[wi+1].get('a')))
    return ranges,jumps

connection=sqlite3.connect(DATABASE.resolve().as_uri()+'?mode=ro',uri=True)
connection.row_factory=sqlite3.Row
results=[]
for group in ('width_200','single_width_200'):
    rows=list(csv.DictReader((BASE/group/'inputs.csv').open(encoding='utf-8-sig',newline='')))
    aggregate=[json.loads(l) for l in (BASE/group/(group+'_trace.jsonl')).read_text(encoding='utf-8').splitlines() if l.strip()]
    grouped=collections.defaultdict(list)
    for e in aggregate:grouped[e['row']['task_id']].append(e)
    run=group+('_real_20261019' if group.startswith('single') else '_real_20261017')
    for item in rows:
        cid=item['case_id'];folder=BASE/group/'cases'/cid;execution=parsed((folder/'execution_result.json').read_text(encoding='utf-8'))
        rid=item['actual_road_id'].removeprefix('road_');dbid=item.get('database_case_id') or cid
        record={'group':group,'case_id':cid,'database_case_id':dbid,'input':item['input_text'],'status':execution['status'],'issues':[],'observations':[]}
        def issue(kind,**details):record['issues'].append(dict(type=kind,**details))
        def observe(kind,**details):record['observations'].append(dict(type=kind,**details))
        review=connection.execute('SELECT decision FROM case_reviews WHERE run_id=? AND task_id=?',(run,cid)).fetchone()
        record['human_review']=review[0] if review else 'missing'
        db=connection.execute('SELECT input_text FROM test_cases WHERE case_id=?',(dbid,)).fetchone()
        if not db or db[0]!=item['input_text']:issue('database_input_mismatch')
        if cid not in grouped:issue('missing_aggregate_trace')
        if execution['status']!='done':
            issue('execution_error',message=execution.get('error'));results.append(record);continue
        events=[json.loads(l) for l in (folder/'trace.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        normalized_events=[]
        for event in events:
            event=json.loads(json.dumps(event))
            event['row'].update(task_id=cid,run_id=run,case_id=dbid)
            normalized_events.append(event)
        if grouped[cid][:len(events)]!=normalized_events:issue('aggregate_case_trace_mismatch')
        if execution.get('source_xodr_sha256')!=sha(folder/'before.xodr') or execution.get('source_json_sha256')!=sha(folder/'before.json'):
            issue('before_map_source_hash_mismatch')
        final={};contexts={};steps={}
        for e in events:
            r=e['row'];sid=str(r.get('step_id'))
            if e['type']=='agent_step':
                steps.setdefault(sid,{}).update(r)
                if r.get('road_context_json'):contexts[sid]=parsed(r['road_context_json'])
                if r.get('llm_output_json'):
                    value=parsed(r['llm_output_json']);value=value.get('dsl',value)
                    if 'operations' in value:final[sid]=value
            if e['type']=='dsl_check' and r.get('dsl_output_json'):final[sid]=parsed(r['dsl_output_json'])
        operations=[op for d in final.values() for op in d.get('operations',[]) if op.get('operation')=='width']
        if not operations:issue('missing_width_dsl')
        dsl={'operations':operations}
        before=ET.parse(folder/'before.xodr').getroot();after=ET.parse(folder/'after.xodr').getroot()
        maps={stage:{r.get('id'):_lane_average_widths(r) for r in root.findall('road')} for stage,root in [('before',before),('after',after)]}
        for stage in ('before','after'):
            parsed((folder/(stage+'.json')).read_text(encoding='utf-8'))
            recorded=execution['actual_width_'+stage]
            if {str(k):v for k,v in maps[stage][rid].items()}!=recorded['lane_avg_width']:issue('recorded_map_average_mismatch',stage=stage)
        before_roads={r.get('id'):ET.tostring(r) for r in before.findall('road')};after_roads={r.get('id'):ET.tostring(r) for r in after.findall('road')}
        changed=sorted(k for k in before_roads.keys()|after_roads.keys() if before_roads.get(k)!=after_roads.get(k))
        if changed!=execution.get('changed_road_ids'):issue('changed_road_record_mismatch')
        observe('changed_roads',roads=changed)
        planned=[' '.join(c) for c in trace_commands(events)]
        executed=[r['command'] for r in execution.get('commands',[])]
        if planned!=executed:issue('trace_execution_command_mismatch',planned=planned,executed=executed)
        text=item['input_text'].lower();context=None
        for value in contexts.values():
            if 'lane_ids' in value and value.get('road_id')=='road_'+rid:context=value
            for road in value.get('roads',[]):
                if road.get('road_id')=='road_'+rid:context=road.get('width')
        if not context:issue('missing_target_road_context');context={'lane_ids':list(maps['before'][rid]),'lane_avg_width':{str(k):v for k,v in maps['before'][rid].items()}}
        av=context['lane_avg_width'];lane_ids=context['lane_ids']
        if {int(k):v for k,v in av.items()}!=maps['before'][rid]:issue('context_map_average_mismatch')
        selected=expected_lanes(text,lane_ids)
        expected=selected[1] if selected else None
        if expected is None:
            m=re.search(r'\blane\s+(-?\d+)\b',text)
            if m:expected=[int(m[1])]
            elif 'widest' in text:expected=[max(lane_ids,key=lambda k:av[str(k)])]
            elif 'narrowest' in text:expected=[min(lane_ids,key=lambda k:av[str(k)])]
            elif re.search(r'\b(entire|whole|all)\b',text):expected=list(lane_ids)
        if expected is None:observe('selector_not_automatically_classified')
        actual=[]
        for op in operations:
            if op['road_id']!='road_'+rid:issue('wrong_road',actual=op['road_id'])
            if op.get('road_limit')!='none':issue('unexpected_scope_limit',value=op.get('road_limit'))
            actual.extend(lane_ids if op['lane_ids']=='none' else [int(k.removeprefix('lane_')) for k in op['lane_ids']])
        if expected is not None and set(actual)!=set(expected):issue('wrong_target_lanes',expected=expected,actual=actual)
        # Check direct action/magnitude extraction independently of database candidates.
        expected_action='decrease' if re.search(r'\b(narrow(?:ed|ing)?|shrink\w*|shrunk|reduc\w*|decreas\w*)\b',text) else 'increase' if re.search(r'\b(widen\w*|expand\w*|increas\w*|add)\b',text) else 'set'
        amount=re.search(r'\b(?:by|to|of)\s+(\d+(?:\.\d+)?)\s*(m\b|%|percent)',text)
        if not amount:amount=re.search(r'\b(\d+(?:\.\d+)?)\s*(m\b|%|percent)',text)
        for op in operations:
            change=op['change'];mag=change['magnitude']
            if change['action']!=expected_action:issue('action_needs_review',expected=expected_action,actual=change['action'])
            if amount:
                unit='percent' if amount[2] in ('%','percent') else 'm'
                if mag.get('kind')!='numeric' or mag.get('value')!=float(amount[1]) or mag.get('unit')!=unit:issue('numeric_magnitude_mismatch',expected=[float(amount[1]),unit],actual=mag)
            else:
                level='slight' if re.search(r'slight|a little|a bit',text) else 'significant' if re.search(r'significant|greatly|noticeably|substantial',text) else 'default'
                if mag.get('kind')!='qualitative' or mag.get('level')!=level:issue('qualitative_magnitude_mismatch',expected=level,actual=mag)
        candidates=list(connection.execute('SELECT * FROM dsl_answers WHERE case_id=? AND agent_type=\'width\'',(dbid,)))
        real=[r for r in candidates if r['road_id']=='road_'+rid];candidates=real or candidates
        matching=[r for r in candidates if normalized(parsed(r['dsl_json']),rid)==normalized(dsl,rid)]
        if not matching:issue('dsl_standard_mismatch',candidate_ids=[r['dsl_answer_id'] for r in candidates])
        tools=[]
        for r in steps.values():
            if r.get('tool_call_json'):
                payload=parsed(r['tool_call_json'])
                for call in payload:
                    tools.extend(call.get('tool_calls',[call]))
        items=[i for call in tools if call.get('name')=='width_apply' for i in call['args']['items']]
        baselines=[]
        for answer in matching:
            baselines.extend(connection.execute("SELECT * FROM tool_call_answers WHERE dsl_answer_id=? AND review_status='approved'",(answer['dsl_answer_id'],)))
        if not baselines:observe('no_approved_tool_baseline_for_matching_dsl')
        elif not any(canonical_tool(tools,rid)==canonical_tool(parsed(t['tool_call_json']),rid) for t in baselines):
            observe('tool_baseline_expression_differs',candidate_ids=[t['tool_answer_id'] for t in baselines])
        if len(items)!=len(executed):issue('tool_command_count_mismatch',tools=len(items),commands=len(executed))
        for tool,command in zip(items,executed):
            params=dict(t.split('=',1) for t in command.split()[1:]);delta=float(params['v']);lids=tool['li'] or lane_ids
            if tool['ms']!=99 or tool['s']!=1 or tool['sh']!=1:issue('unexpected_tool_scope_or_smoothing',tool=tool)
            try:
                lo,hi=interval(tool['formula'])
                if not lo-0.00051<=delta<=hi+0.00051:issue('formula_result_mismatch',formula=tool['formula'],delta=delta)
            except (ValueError,SyntaxError,TypeError):issue('formula_not_audited',formula=tool['formula'])
            op=operations[int(tool['operation_index'])];ch=op['change'];mag=ch['magnitude'];total=sum(av[str(l)] for l in lids)
            if mag['kind']=='qualitative':
                a,b={'slight':(.05,.25),'default':(.25,.4),'significant':(.4,.7)}[mag['level']]
                lo,hi=total*a/len(lids),total*b/len(lids)
            else:
                value=float(mag['value']);current=total if op['lane_ids']=='none' else total/len(lids)
                if ch['action']=='set':desired=current*value/100 if mag['unit']=='percent' else value;value=desired-current
                elif mag['unit']=='percent':value=current*value/100
                lo=hi=value/(len(lids) if op['lane_ids']=='none' else 1)
            if ch['action']=='decrease':lo,hi=-hi,-lo
            if not min(lo,hi)-.0006<=delta<=max(lo,hi)+.0006:issue('tool_delta_dsl_mismatch',delta=delta,expected=[lo,hi])
            for lid in lids:
                b=maps['before'][rid][lid];a=maps['after'][rid][lid];diff=a-b
                if delta*diff < -1e-7:observe('actual_change_opposes_command',lane=lid,before=b,after=a,command=delta)
                if abs(diff-delta)>.001:observe('actual_differs_from_projection',lane=lid,before=b,after=a,command=delta,difference=round(diff-delta,4))
        ranges_b,jumps_b=geometry(before);ranges_a,jumps_a=geometry(after)
        new_negative=[k for k,v in ranges_a.items() if v[0]<-1e-6 and ranges_b.get(k,(0,0))[0]>=-1e-6]
        if new_negative:issue('new_negative_lane_width',segments=new_negative[:20],count=len(new_negative))
        new_jumps=[k for k,v in jumps_a.items() if v>.001 and v>jumps_b.get(k,0)+.001]
        if new_jumps:observe('increased_width_discontinuities',count=len(new_jumps),max_jump=max(jumps_a[k] for k in new_jumps))
        results.append(record)
    print(group,'checked',len(rows),flush=True)

summary={}
for group in ('width_200','single_width_200'):
    selected=[r for r in results if r['group']==group]
    summary[group]={'count':len(selected),'status':dict(collections.Counter(r['status'] for r in selected)),
      'issues':{kind:[r['case_id'] for r in selected if any(i['type']==kind for i in r['issues'])] for kind in sorted({i['type'] for r in selected for i in r['issues']})},
      'observations':{kind:[r['case_id'] for r in selected if any(i['type']==kind for i in r['observations'])] for kind in sorted({i['type'] for r in selected for i in r['observations']})}}
out=BASE/'audit_current_400'
out.mkdir(exist_ok=True)
write_json(out/'audit.json',{'created_at':datetime.now().isoformat(),'summary':summary,'cases':results,
 'limitations':'Deterministic requirement parsing; candidate flags need review. Maps checked from XML, polynomial extrema, averages and within-section continuity. Not a visual/vehicle-trajectory or junction-connectivity validation. Database read-only; human reviews unchanged.'})
print(json.dumps(summary,ensure_ascii=False),flush=True)
