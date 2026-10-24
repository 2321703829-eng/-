const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const output=path.join(__dirname,'width_200/failure_analysis');
const snapshot=JSON.parse(fs.readFileSync(path.join(output,'report_snapshot.json'),'utf8'));
const trace=fs.readFileSync(path.join(__dirname,'width_200/width_200_trace.jsonl'),'utf8');
const source=fs.readFileSync(path.join(root,'完全版本/完全版本/openDriveMapEditor/testfucs/agent_trace_viewer/viewer.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const elements=new Map();
function element(key){if(!elements.has(key)) elements.set(key,{addEventListener(){},querySelectorAll(){return[]},replaceChildren(){},classList:{toggle(){}},textContent:'',innerHTML:'',value:'',style:{}});return elements.get(key);}
const context=vm.createContext({console,setTimeout,clearTimeout,snapshot,trace,save:(x)=>fs.writeFileSync(path.join(output,'stage_accuracy.json'),JSON.stringify(x,null,2)),requestAnimationFrame:f=>f(),document:{querySelector:element,addEventListener(){}},window:{addEventListener(){}}});
vm.runInContext(source+`
(async()=>{
setRequestOperatorSource(snapshot.source);
const workbook=readJsonlTrace(trace,'width_200_trace.jsonl').workbook;
const rows=name=>sheetToObjects(workbook.sheets[name]||[]);
const tasks=buildTaskModels(rows('tasks'),rows('agent_steps'),rows('road_metrics'),rows('dsl_checks'),rows('llm_requests'));
state.taskModels=tasks;state.sourceName='width_200_trace.jsonl';state.sourceFormat='jsonl';
const baseline={cases:snapshot.cases,caseIdByTask:new Map(tasks.map(t=>[t.task_id,t.task_id])),errorsByTask:new Map()};
const results=new Map();
for(const task of tasks) results.set(task.task_id,await judgeTaskAccuracy(task,baseline));
const data={dsl:summarizeAccuracy(tasks,results,'dsl'),tool:summarizeAccuracy(tasks,results,'tool'),results:Object.fromEntries(results),roadContexts:Object.fromEntries(tasks.map(t=>[t.task_id,buildRoadComparisonContext(t,baseline)]))};
save(data); console.log(JSON.stringify({dsl:data.dsl,tool:data.tool}));
})().catch(console.error);
`,context);
