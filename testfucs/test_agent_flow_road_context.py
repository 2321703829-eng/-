import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from unittest.mock import Mock, patch

from openpyxl import Workbook

from handler import agent_dispatcher as agent_dispatcher_module
from handler.curve_optimizer import CurveOptimizationService
from testfucs import LLMjudge, agent_flow_real_trace, test_data_store
from testfucs.agent_trace_viewer import server as agent_trace_viewer_server
from utils import agent_road_context


class ViewerAccuracyTest(unittest.TestCase):
    def test_stage_accuracy_and_refresh(self):
        node = os.environ.get("NODE_BINARY") or shutil.which("node")
        if not node:
            bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
            if bundled.exists():
                node = str(bundled)
        if not node:
            self.skipTest("Node.js is needed to test the Viewer JavaScript")
        viewer_path = Path(agent_trace_viewer_server.__file__).with_name("viewer.html")
        runner = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = new Map();
const element = (selector) => {
  if (!elements.has(selector)) elements.set(selector, {
    addEventListener() {}, querySelectorAll() { return []; }, replaceChildren() {},
    classList: { toggle() {} }, textContent: '', innerHTML: '', value: '', style: {}
  });
  return elements.get(selector);
};
const context = vm.createContext({ assert, console, setTimeout, clearTimeout,
  requestAnimationFrame: (callback) => callback(),
  document: { querySelector: element, addEventListener() {} },
  window: { addEventListener() {} }
});
vm.runInContext(source + `
(async () => {
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const dsl = (agent, road = 'road_21') => ({operations: [{operation: agent, road_id: road, value: 4}]});
  const calls = (agent, road = '21') => [{name: agent === 'curve' ? 'adjust_curve_curvature' : agent + '_apply',
    args: agent === 'curve' ? {road_id: String(road).replace(/^road_/, ''), side: 'start_side', value: 4} :
      {items: [{operation_index: 0, id: String(road).replace(/^road_/, ''), formula: '4', m: 'add', mv: 1, ms: 99, sh: 1}]}}];
  const step = (agent, id = agent, road = 'road_21') => ({agent, step_id: id, llm_output_json: dsl(agent, road), tool_call_json: calls(agent, road)});
  const task = (steps) => ({task_id: 'test', user_input: 'test request', status: 'done', steps,
    agents: steps.map(s => ({name: s.agent})), metrics: [], llmRequests: []});
  const baseline = (steps) => ({cases: {case1: Object.fromEntries(ANSWER_AGENTS.map(agent => [agent,
    steps.filter(s => s.agent === agent).map((s, i) => ({dsl: s.llm_output_json, subtask_key: agent + '_' + i,
      tool_answers: [{review_status: 'approved', tool_calls: s.tool_call_json}]}))]))},
    caseIdByTask: new Map([['test', 'case1']]), errorsByTask: new Map()});
  const expect = async (item, data, dslDecision, toolDecision) => {
    const result = await judgeTaskAccuracy(item, data);
    assert.equal(result.dsl.decision, dslDecision, JSON.stringify(result));
    assert.equal(result.tool.decision, toolDecision, JSON.stringify(result));
  };
  const slope = step('slope');
  const one = task([slope]);
  const data = baseline([slope]);
  await expect(one, data, 'pass', 'pass');
  const alignedActual = step('slope', 'aligned', 'road_5');
  const alignedStandard = step('slope', 'standard', 'road_21');
  const alignedData = baseline([alignedStandard]);
  alignedData.cases.case1.road_alignment = {match_status: 'matched', road_id: '5'};
  const alignedTask = task([alignedActual]);
  await expect(alignedTask, alignedData, 'pass', 'pass');
  const wrongAligned = clone(alignedTask);
  wrongAligned.steps[0].llm_output_json.operations[0].road_id = 'road_6';
  wrongAligned.steps[0].tool_call_json[0].args.items[0].id = '6';
  await expect(wrongAligned, alignedData, 'fail', 'fail');
  const singleAligned = task([{agent: 'single', step_id: 'single',
    llm_output_json: {dsl: {operations: [{operation: 'slope', road_id: 'road_0', value: 4}]},
      plans: [{agent: 'slope', road_id: 'road_0', tool_calls: calls('slope', '0')}]},
    tool_call_json: JSON.stringify([{agent: 'slope', road_id: 'road_0', tool_calls: calls('slope', '0')}]),
    internal_command: 'slope id=0 v=4 m=add mv=1 ms=99 sh=1'}]);
  const singleAlignedData = baseline([alignedStandard]);
  singleAlignedData.cases.case1.road_alignment = {match_status: 'matched', road_id: '0'};
  await expect(singleAligned, singleAlignedData, 'pass', 'pass');
  one.dslChecks = [{status: 'error'}];
  state.reviewByTask.set('test', {decision: 'fail', review_source: 'human'});
  await expect(one, data, 'pass', 'pass');
  const errorTask = clone(one);
  errorTask.status = 'error';
  errorTask.steps[0].tool_call_json = [];
  await expect(errorTask, data, 'pass', 'fail');
  const wrong = clone(one);
  wrong.steps[0].tool_call_json[0].args.items[0].formula = '5';
  await expect(wrong, data, 'pass', 'fail');
  const emptyStep = task([slope, {...step('slope', 'empty'), llm_output_json: '', tool_call_json: []}]);
  await expect(emptyStep, data, 'fail', 'fail');
  const invalidTools = clone(one);
  invalidTools.steps[0].tool_call_json = [{}];
  await expect(invalidTools, baseline([]), 'question', 'fail');
  const composite = task(ANSWER_AGENTS.map(a => step(a)));
  const compositeData = baseline(composite.steps);
  await expect(composite, compositeData, 'pass', 'pass');
  composite.steps.pop();
  await expect(composite, compositeData, 'fail', 'fail');
  const roads = task([step('slope', '1'), step('slope', '2', 'road_22')]);
  const roadsData = baseline(roads.steps);
  await expect(roads, roadsData, 'pass', 'pass');
  roads.steps.pop();
  await expect(roads, roadsData, 'fail', 'fail');
  await expect(one, baseline([]), 'question', 'fail');
  const noApproved = baseline([slope]);
  noApproved.cases.case1.slope[0].tool_answers[0].review_status = 'draft';
  await expect(one, noApproved, 'pass', 'fail');
  const ambiguous = baseline([slope, slope]);
  await expect(one, ambiguous, 'pass', 'fail');
  const invalid = clone(one);
  invalid.steps[0].llm_output_json = 'not json';
  await expect(invalid, data, 'fail', 'fail');
  const random = clone(one);
  random.steps[0].tool_call_json[0].args.items[0].formula = '10 * random(0.25, 0.4)';
  const randomData = baseline(random.steps);
  randomData.cases.case1.slope[0].tool_answers[0].resolved_result = [{v: 3}];
  random.steps[0].resolved_result_json = [{v: 4}];
  await expect(random, randomData, 'pass', 'pass');
  const numeric = clone(one);
  numeric.steps[0].tool_call_json[0].args.items[0].formula = '4.0';
  await expect(numeric, data, 'pass', 'pass');
  const formattedFormula = clone(one);
  formattedFormula.steps[0].tool_call_json[0].args.items[0].formula =
    '50*tan(radians(11.3099*(1+random(0.4,0.7))))-(5--5)';
  const formattedFormulaData = baseline([slope]);
  formattedFormulaData.cases.case1.slope[0].tool_answers[0].tool_calls[0]
    .args.items[0].formula =
      '50.0 * tan(radians(11.3099 * (1 + random(0.40, 0.70)))) - (5 - -5)';
  await expect(formattedFormula, formattedFormulaData, 'pass', 'pass');
  const widthSumStep = {...step('width'), tool_call_json: [{name: 'width_apply', args: {items: [{
    operation_index: 0, id: '21', li: [], ms: 99, s: 1, sh: 1,
    formula: '(8.7 - (2.8 + 3.3 + 3.4 + 3.5 + 3.0 + 2.7)) / 6'
  }]}}], resolved_result_json: [{name: 'apply_width', args: {
    id: '21', li: [], ms: 99, s: 1, sh: 1, v: -1.667
  }}], internal_command: 'width id=21 v=-1.667 s=1 ms=99 sh=1'};
  const widthSumTask = task([widthSumStep]);
  const standardPane = {innerHTML: ''};
  const reviewPanel = {querySelector: selector => selector === '.tool-review-standard' ? standardPane : null};
  renderToolReviewPanel(reviewPanel, widthSumTask, widthSumStep, 0, 'width', 'width:road_21', [], [], null, null);
  assert.match(standardPane.innerHTML, /class="tool-dsl-select"[^>]*disabled/);
  assert.match(standardPane.innerHTML, /data-tool-action="create"[^>]*>写入并批准</);
  const actionEnabled = action => !standardPane.innerHTML.match(
    new RegExp('<button[^>]*data-tool-action="' + action + '"[^>]*>')
  )[0].includes('disabled');
  assert.equal(actionEnabled('create'), true);
  for (const action of ['overwrite', 'approve', 'reject', 'delete']) assert.equal(actionEnabled(action), false);
  const unselectedDsl = {dsl_answer_id: 1, subtask_key: 'width_1', road_id: 'road_21', tool_answers: []};
  renderToolReviewPanel(reviewPanel, widthSumTask, widthSumStep, 0, 'width', 'width:road_21', [unselectedDsl], [], null, null);
  assert.equal(actionEnabled('create'), false);
  renderToolReviewPanel(reviewPanel, widthSumTask, widthSumStep, 0, 'width', 'width:road_21', [unselectedDsl], [], unselectedDsl, null);
  assert.equal(actionEnabled('create'), true);
  const widthSumAnswer = {review_status: 'approved', variant_index: 2,
    tool_calls: clone(widthSumStep.tool_call_json), resolved_result: clone(widthSumStep.resolved_result_json),
    internal_commands: [widthSumStep.internal_command]};
  widthSumAnswer.tool_calls[0].args.items[0].formula = '(8.7-(3.4+3.3+2.8+3.5+3+2.7))/6';
  assert.equal(compareToolAnswer(widthSumStep, widthSumTask, 'width', widthSumAnswer).matches, true);
  assert.equal(toolAnswerDifferenceScore(widthSumStep, widthSumTask, 'width', widthSumAnswer), 0);
  assert.equal(jsonDifferenceScore('1+2', '1+3'), 1);
  assert.equal(jsonDifferenceScore('width_apply', 'slope_apply'), 1);
  assert.equal(jsonDifferenceScore('21', 21), 1);
  const wrongWidthFormula = clone(widthSumAnswer);
  wrongWidthFormula.tool_calls[0].args.items[0].formula = '(8.7-(3.4+3.3+2.8+3.5+3+2.7))/7';
  assert.equal(compareToolAnswer(widthSumStep, widthSumTask, 'width', wrongWidthFormula).matches, false);
  assert.equal(findClosestToolAnswer(widthSumStep, widthSumTask, 'width', [wrongWidthFormula, widthSumAnswer]), widthSumAnswer);
  const widthSumData = baseline([widthSumStep]);
  widthSumData.cases.case1.width[0].tool_answers = [widthSumAnswer];
  await expect(widthSumTask, widthSumData, 'pass', 'pass');
  const widthSingleTask = task([{agent: 'single', step_id: '0', llm_output_json: {
    dsl: widthSumStep.llm_output_json}, tool_call_json: [{agent: 'width', road_id: 'road_21',
      tool_calls: widthSumStep.tool_call_json}], resolved_result_json: [{agent: 'width', road_id: 'road_21',
        resolved_result: widthSumStep.resolved_result_json}], internal_command: widthSumStep.internal_command}]);
  await expect(widthSingleTask, widthSumData, 'pass', 'pass');
  const evidenceText = answer => formatToolEvidence({
    tool_calls: normalizeToolCallsForCompare(answer.tool_calls, 'width'),
    resolved_result: answer.resolved_result,
    internal_commands: normalizeToolInternalCommandsForCompare(answer.internal_commands)
  });
  const widthComparison = compareToolAnswer(widthSumStep, widthSumTask, 'width', widthSumAnswer);
  assert.doesNotMatch(renderToolAnswerDifference(evidenceText(widthSumAnswer), widthSumStep,
    widthSumTask, 'width', widthSumAnswer, widthComparison), /class="answer-diff"/);
  const wrongWidthParameter = clone(widthSumAnswer);
  wrongWidthParameter.tool_calls[0].args.items[0].s = 2;
  const wrongWidthComparison = compareToolAnswer(widthSumStep, widthSumTask, 'width', wrongWidthParameter);
  assert.equal(wrongWidthComparison.matches, false);
  const highlightedWidth = renderToolAnswerDifference(evidenceText(wrongWidthParameter), widthSumStep,
    widthSumTask, 'width', wrongWidthParameter, wrongWidthComparison);
  assert.match(highlightedWidth, /class="answer-diff"/);
  assert.doesNotMatch(highlightedWidth, /class="answer-diff">.*formula/);
  for (const [left, right] of [
    ['1+(2+3)', '(3+1)+2'], ['1e+2+.50', '0.5+100.0'],
    ['1+-2', '-2+1'], ['2**3+1', '1+2**3'],
    ['max(2+1,4)', 'max(1+2,4)'], ['2+random(0.4,0.7)', 'random(0.40,0.70)+2.0']
  ]) assert.equal(normalizeToolFormulaForCompare(left), normalizeToolFormulaForCompare(right), left);
  for (const [left, right] of [
    ['1-2', '2-1'], ['1/(2+3)', '1/2+3'], ['1-(2+3)', '1-2+3'],
    ['1+1+2', '1+2'], ['1+2', '3'], ['-2**2+1', '(-2)**2+1'],
    ['2**3**2', '(2**3)**2'], ['2**-2', '-2**2'],
    ['random(0.4,0.7)+1', 'random(0.7,0.4)+1'],
    ['random(0.4,0.7)+1', 'random(0.4,0.8)+1']
  ]) assert.notEqual(normalizeToolFormulaForCompare(left), normalizeToolFormulaForCompare(right), left);
  for (const invalidFormula of ['(1+2', '1+2+', 'unknown(2+1)', '2//3+4']) {
    assert.equal(normalizeToolFormulaForCompare(invalidFormula), invalidFormula);
  }
  const fartherAnswer = {variant_index: 1, tool_calls: calls('slope')};
  fartherAnswer.tool_calls[0].args.items[0].formula = '5';
  fartherAnswer.tool_calls[0].args.items[0].mv = 0;
  const closerAnswer = {variant_index: 2, tool_calls: calls('slope')};
  closerAnswer.tool_calls[0].args.items[0].formula = '5';
  assert.equal(findClosestToolAnswer(one.steps[0], one, 'slope', [fartherAnswer, closerAnswer]), closerAnswer);
  const all = ANSWER_AGENTS.map(a => step(a));
  const single = task([{agent: 'single', step_id: '1', llm_output_json: {
    dsl: {operations: all.flatMap(s => s.llm_output_json.operations)}},
    tool_call_json: all.map(s => ({agent: s.agent, road_id: 'road_21', tool_calls: s.tool_call_json}))}]);
  await expect(single, baseline(all), 'pass', 'pass');
  single.steps[0].tool_call_json.pop();
  await expect(single, baseline(all), 'pass', 'fail');
  const supplied = {...one, evaluation_mode: 'tool_plan_only'};
  await expect(supplied, data, 'unavailable', 'pass');
  assert.equal((await judgeTaskByRequestAnswers(supplied, data)).decision, 'pass');
  const legacy = clone(one);
  delete legacy.steps[0].tool_call_json;
  await expect(legacy, data, 'pass', 'unavailable');
  assert.equal((await judgeTaskByRequestAnswers(legacy, data)).decision, 'fail');
  state.workbook = {sheets: {agent_steps: [['tool_call_json']]}};
  await expect(legacy, data, 'pass', 'fail');
  await expect({...one, status: 'running'}, data, 'question', 'question');
  const retry = clone(one);
  retry.steps.unshift({...clone(slope), llm_output_json: dsl('width')});
  await expect(retry, data, 'pass', 'pass');
  const counts = new Map([['a', {dsl: {decision: 'pass'}}], ['b', {dsl: {decision: 'fail'}}],
    ['c', {dsl: {decision: 'question'}}], ['d', {dsl: {decision: 'unavailable'}}]]);
  const summary = summarizeAccuracy(['a','b','c','d'].map(task_id => ({task_id})), counts, 'dsl');
  assert.equal(summary.rate, '50.0%');
  assert.equal(summary.judged, 2);
  assert.equal(summary.question, 1);
  assert.equal(summarizeAccuracy([], counts, 'dsl').rate, '--');
  state.taskModels = [one, {...one, task_id: 'second', user_input: 'width request', status: 'error', agents: [{name: 'width'}]}];
  state.requestOperatorByInput = new Map([['test request', ['slope']], ['width request', ['width']]]);
  state.reviewFilter = 'pass';
  assert.equal(filteredTasks({includeReviewFilter: false}).length, 2);
  state.statusFilter = 'done';
  assert.equal(filteredTasks({includeReviewFilter: false}).length, 1);
  state.statusFilter = 'all';
  state.instructionFilter = 'W';
  assert.equal(filteredTasks({includeReviewFilter: false})[0].task_id, 'second');
  state.instructionFilter = 'all';
  state.search = 'second';
  assert.equal(filteredTasks({includeReviewFilter: false}).length, 1);
  state.search = '';
  state.reviewFilter = 'all';
  const combinations = {
    W: ['width'], S: ['slope'], C: ['curve'], WS: ['width', 'slope'],
    WC: ['width', 'curve'], SC: ['slope', 'curve'], WSC: ['width', 'slope', 'curve']
  };
  setRequestOperatorSource({workbook: {sheets: {Sheet1: [
    [REQUEST_INPUT_COLUMN, REQUEST_OPERATOR_COLUMN],
    ...Object.entries(combinations).map(([type, agents]) => [
      type + ' request', type === 'WSC' ? 'all' : agents.slice().reverse().join('+')
    ])
  ]}}});
  state.taskModels = Object.keys(combinations).map(type => ({...one, task_id: type,
    user_input: '  ' + type.toLowerCase() + '   request  ', agents: [{name: 'single'}]}));
  state.taskModels.push({...one, task_id: 'unknown', user_input: 'unknown width request'});
  for (const type of Object.keys(combinations)) {
    toggleInstructionFilter(type);
    assert.deepEqual(filteredTasks().map(t => t.task_id), [type]);
    assert.match($('#instructionFilters').innerHTML, new RegExp('data-instruction="' + type + '" aria-pressed="true"'));
  }
  toggleInstructionFilter('WSC');
  assert.equal(state.instructionFilter, 'all');
  assert.equal(filteredTasks().length, 8);
  toggleInstructionFilter('W');
  toggleInstructionFilter('all');
  assert.equal(filteredTasks().length, 8);
  state.accuracyResults = new Map([
    ['W', {dsl: {decision: 'fail'}, tool: {decision: 'pass'}}],
    ['S', {dsl: {decision: 'pass'}, tool: {decision: 'fail'}}],
    ['WS', {dsl: {decision: 'fail'}, tool: {decision: 'fail'}}],
    ['C', {dsl: {decision: 'question'}, tool: {decision: 'unavailable'}}]
  ]);
  renderAccuracySummary();
  const scopeBeforeFilter = $('#accuracyScope').textContent;
  state.viewMode = 'detail';
  state.detailTaskIds = ['C'];
  toggleAccuracyFilter('dsl');
  assert.equal(state.viewMode, 'overview');
  assert.deepEqual(state.detailTaskIds, []);
  assert.deepEqual(filteredTasks().map(t => t.task_id), ['W', 'WS']);
  assert.equal($('#accuracyScope').textContent, scopeBeforeFilter);
  assert.match($('#accuracyStats').innerHTML, /33.3%/);
  assert.match($('#accuracyStats').innerHTML, /data-stage="dsl" aria-pressed="true"/);
  captureDetailTaskSnapshot('W');
  state.viewMode = 'detail';
  assert.deepEqual(getTaskListItems().map(t => t.task_id), ['W', 'WS']);
  assert.equal(getTaskNavigation('W').next.task_id, 'WS');
  toggleAccuracyFilter('tool');
  assert.deepEqual(filteredTasks().map(t => t.task_id), ['S', 'WS']);
  toggleInstructionFilter('WS');
  assert.deepEqual(filteredTasks().map(t => t.task_id), ['WS']);
  state.statusFilter = 'error';
  assert.equal(filteredTasks().length, 0);
  state.statusFilter = 'all';
  state.search = 'no matching text';
  assert.equal(filteredTasks().length, 0);
  state.search = '';
  state.reviewByTask.set('WS', {decision: 'pass', review_source: 'human'});
  state.reviewFilter = 'fail';
  assert.equal(filteredTasks().length, 0);
  assert.equal(filteredTasks({includeReviewFilter: false}).length, 1);
  state.reviewFilter = 'pass';
  assert.equal(filteredTasks().length, 1);
  state.reviewFilter = 'all';
  toggleInstructionFilter('all');
  state.accuracyResults.set('S', {dsl: {decision: 'pass'}, tool: {decision: 'pass'}});
  assert.deepEqual(filteredTasks().map(t => t.task_id), ['WS']);
  toggleAccuracyFilter('tool');
  assert.equal(state.accuracyFailureFilter, 'all');
  assert.equal(filteredTasks().length, 8);
  state.taskModels = [one];
  const requests = [];
  getRequestInputCellInfo = async () => ({caseId: 'case1'});
  requestDatabaseApi = async (path, options) => {
    requests.push({path, options});
    assert.equal(path, '/api/auto-review-baselines');
    return {cases: data.cases};
  };
  await refreshAccuracy();
  assert.equal(requests.length, 1);
  renderAccuracySummary();
  assert.equal(requests.length, 1);
  assert.equal(state.reviewByTask.get('test').decision, 'fail');
  const other = {...one, task_id: 'other'};
  state.taskModels = [one, other];
  state.requestOperatorWorkbook = {sheets: {Sheet1: []}};
  renderTaskList = () => {};
  renderOverview = () => {};
  requestDatabaseApi = async (path, options) => {
    requests.push({path, options});
    if (path === '/api/auto-review-baselines') return {cases: data.cases};
    assert.equal(path, '/api/case-reviews/batch');
    const reviews = JSON.parse(options.body).reviews;
    assert.equal(reviews.length, 1);
    assert.equal(reviews[0].task_id, 'other');
    return {reviews};
  };
  requests.length = 0;
  await autoReviewPendingTasks();
  assert.equal(requests.length, 2);
  assert.equal(state.reviewByTask.get('test').review_source, 'human');
  assert.equal(state.reviewByTask.get('other').decision, 'pass');
  assert.equal(state.accuracyResults.size, 2);
  state.taskModels = [one];
  const late = [];
  loadAutoReviewBaselines = () => new Promise(resolve => late.push(resolve));
  const oldRefresh = refreshAccuracy();
  const newRefresh = refreshAccuracy();
  late[1](data);
  await newRefresh;
  late[0](baseline([]));
  await oldRefresh;
  assert.equal(state.accuracyResults.get('test').dsl.decision, 'pass');
  state.accuracyFailureFilter = 'tool';
  state.accuracyResults.set('test', {dsl: {decision: 'pass'}, tool: {decision: 'fail'}});
  let listRefreshes = 0;
  let overviewRefreshes = 0;
  renderTaskList = () => { listRefreshes += 1; };
  renderOverview = () => { overviewRefreshes += 1; };
  loadAutoReviewBaselines = async () => data;
  await refreshAccuracy();
  assert.equal(filteredTasks().length, 0);
  assert.equal(listRefreshes, 1);
  assert.equal(overviewRefreshes, 1);
  state.viewMode = 'detail';
  state.detailTaskIds = ['test'];
  await refreshAccuracy();
  assert.equal(state.viewMode, 'detail');
  assert.deepEqual(state.detailTaskIds, ['test']);
  assert.equal(overviewRefreshes, 1);
  loadAutoReviewBaselines = async () => { throw new Error('offline'); };
  await refreshAccuracy();
  assert.equal(state.accuracyResults.size, 0);
  assert.match(state.accuracyError, /offline/);
  assert.equal(state.accuracyLoading, false);
  state.instructionFilter = 'WSC';
  state.accuracyFailureFilter = 'tool';
  state.detailTaskIds = ['test'];
  releaseLoadedTraceState();
  assert.equal(state.instructionFilter, 'all');
  assert.equal(state.accuracyFailureFilter, 'all');
  assert.deepEqual(state.detailTaskIds, []);
  console.log('Viewer stage accuracy scenarios passed');
})()
`, context).catch(error => {console.error(error); process.exitCode = 1;});
"""
        result = subprocess.run(
            [node, "-", str(viewer_path)], input=runner, text=True,
            encoding="utf-8", capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ManualRoadContextResolutionTest(unittest.TestCase):
    def setUp(self):
        self.previous_dispatcher_mode = (
            agent_dispatcher_module.USE_MANUAL_ROAD_CONTEXT
        )
        self.previous_context_mode = agent_road_context.use_manual_road_context()
        agent_dispatcher_module.USE_MANUAL_ROAD_CONTEXT = True
        agent_road_context.set_use_manual_road_context(True)

    def tearDown(self):
        agent_road_context.clear_manual_task_context()
        agent_dispatcher_module.USE_MANUAL_ROAD_CONTEXT = (
            self.previous_dispatcher_mode
        )
        agent_road_context.set_use_manual_road_context(self.previous_context_mode)

    def test_single_agent_replaces_only_requested_road_21_context(self):
        user_input = "Raise the end point of Road 21."
        request_cases = {
            user_input: {
                "输入": user_input,
                "算子": "slope",
                "width": None,
                "slope": "上坡路",
                "curve": None,
            }
        }

        result = agent_flow_real_trace._prepare_manual_task_context(
            user_input, request_cases
        )
        base_context = {
            "roads": [
                {
                    "road_id": "road_5",
                    "width": {"road_id": "road_5", "source": "real-width-5"},
                    "slope": {"road_id": "road_5", "source": "real-slope-5"},
                    "curve": {"road_id": "road_5", "source": "real-curve-5"},
                },
                {
                    "road_id": "road_21",
                    "width": {"road_id": "road_21", "source": "real-width-21"},
                    "slope": {"road_id": "road_21", "source": "real-slope-21"},
                    "curve": {"road_id": "road_21", "source": "real-curve-21"},
                },
            ]
        }
        single_context = agent_flow_real_trace._build_single_agent_context(
            base_context,
            ["slope"],
            agent_dispatcher_module.AgentDispatcher(),
            CurveOptimizationService(None),
        )

        self.assertEqual(result["source"], "workbook")
        road_5, road_21 = single_context["roads"]
        self.assertEqual(road_5, base_context["roads"][0])
        self.assertEqual(road_21["width"]["source"], "real-width-21")
        self.assertEqual(road_21["curve"]["source"], "real-curve-21")
        self.assertGreater(road_21["slope"]["grade_degree"], 0)
        self.assertEqual(road_21["slope"]["road_id"], "road_21")

    def test_classified_workbook_row_keeps_workbook_priority(self):
        user_input = "Adjust Road 5 using classified workbook data."
        request_cases = {
            user_input: {
                "输入": user_input,
                "算子": "slope",
                "slope": "下坡路",
                "curve": None,
            }
        }

        result = agent_flow_real_trace._prepare_manual_task_context(
            user_input, request_cases
        )
        slope_info = agent_road_context.get_manual_slope_road_info("5")

        self.assertEqual(result["source"], "workbook")
        self.assertLess(slope_info["height_delta"], 0)


class SlopeComparisonWorkerTest(unittest.TestCase):
    def test_workers_receive_only_their_group_and_no_reference_answers(self):
        from unittest.mock import Mock

        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot.jsonl"
            groups = [{
                "name": f"{flow}_{kind}", "agent_flow": flow, "case_type": kind,
                "trace_path": str(Path(directory) / f"{flow}_{kind}.jsonl"),
            } for flow in ("single", "normal") for kind in ("common", "abstract")]
            cases = [{"case_type": kind, "input_text": kind,
                      "request_row": {"case_id": kind, "slope": "uphill"},
                      "baselines": {"secret_reference": True}}
                     for kind in ("common", "abstract")]
            context = {"roads": [{"road_id": "road_21"}]}
            agent_flow_real_trace._write_batch_events(snapshot, [
                {"type": "batch_metadata", "row": {"groups": groups, "stop_file": "stop"}},
                {"type": "road_context_cache", "row": context},
                *[{"type": "batch_case", "row": case} for case in cases],
            ])
            model = Mock()
            with patch.object(agent_flow_real_trace, "dotenv_values", return_value={"XUEZHANG_API_KEY": "test"}), \
                 patch.object(agent_flow_real_trace, "init_GPT_XZ", return_value=model), \
                 patch.object(agent_flow_real_trace, "run_real_trace") as run, \
                 patch.dict(agent_flow_real_trace.os.environ):
                for group in groups:
                    agent_flow_real_trace._run_slope_batch_worker(snapshot, group["name"])
            self.assertEqual(run.call_count, 4)
            for call, group in zip(run.call_args_list, groups):
                self.assertEqual(call.args, (group["trace_path"], model, [group["case_type"]]))
                self.assertFalse(call.kwargs["execute"])
                self.assertNotIn("secret_reference", json.dumps(call.kwargs))
                self.assertEqual(call.kwargs["single_real_context"],
                                 context if group["agent_flow"] == "single" else None)
            self.assertEqual(len({call.args[0] for call in run.call_args_list}), 4)


class AlignedRealRoadTraceTest(unittest.TestCase):
    def test_alignment_loader_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cases.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE test_cases (
                    case_id TEXT PRIMARY KEY,
                    source_row INTEGER,
                    input_text TEXT
                );
                CREATE TABLE test_case_road_alignments (
                    case_id TEXT PRIMARY KEY,
                    match_status TEXT,
                    scene_file_name TEXT,
                    map_file_name TEXT,
                    road_id TEXT,
                    s_start REAL,
                    s_end REAL,
                    route_lane_ids_json TEXT,
                    driving_lane_ids_json TEXT
                );
                INSERT INTO test_cases VALUES (
                    'req_1', 1, 'Widen Road 21.'
                );
                INSERT INTO test_case_road_alignments VALUES (
                    'req_1', 'matched', 'scene.json', 'map.xodr', '5',
                    1.0, 10.0, '[-1]', '[-2,-1]'
                );
            """)
            connection.commit()
            connection.close()

            alignments = test_data_store.load_test_case_road_alignment_map(database)

            self.assertEqual(alignments["Widen Road 21."]["road_id"], "5")
            self.assertEqual(alignments["Widen Road 21."]["route_lane_ids"], [-1])
            connection = sqlite3.connect(database)
            connection.row_factory = sqlite3.Row
            batch_alignments = test_data_store.list_case_road_alignments(
                connection, ["req_1"]
            )
            self.assertEqual(batch_alignments["req_1"]["road_id"], "5")
            self.assertEqual(batch_alignments["req_1"]["match_status"], "matched")
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            connection.close()

    def test_road_21_is_replaced_in_memory_only(self):
        alignment = {
            "case_id": "req_1", "match_status": "matched",
            "scene_file_name": "scene.json", "map_file_name": "map.xodr",
            "road_id": "5", "s_start": 0.0, "s_end": 50.0,
        }
        original = "Widen Road 21, then inspect Road 21."

        result = agent_flow_real_trace._prepare_aligned_task(original, alignment)

        self.assertEqual(result["original_input"], original)
        self.assertEqual(result["test_input"], "Widen Road 5, then inspect Road 5.")
        self.assertEqual(result["map_name"], "map")
        self.assertEqual(result["scene_name"], "scene")
        with self.assertRaisesRegex(ValueError, "Road 21 placeholder"):
            agent_flow_real_trace._prepare_aligned_task("Widen Road 22.", alignment)
        with self.assertRaisesRegex(ValueError, "Invalid aligned scene file name"):
            agent_flow_real_trace._prepare_aligned_task(
                original, {**alignment, "map_file_name": "../map.xodr"}
            )

    def test_multi_agent_switches_scene_for_each_task(self):
        tasks = ["Widen Road 21.", "Steepen Road 21."]
        alignments = {
            tasks[0]: {
                "case_id": "req_1", "match_status": "matched",
                "scene_file_name": "scene_a.json", "map_file_name": "map_a.xodr",
                "road_id": "5",
            },
            tasks[1]: {
                "case_id": "req_2", "match_status": "matched",
                "scene_file_name": "scene_b.json", "map_file_name": "map_b.xodr",
                "road_id": "3",
            },
        }
        manager = Mock()
        manager.get_commands.return_value = []

        def load_scene(map_name, **kwargs):
            agent_flow_real_trace.XParser.roads.clear()
            agent_flow_real_trace.XParser.roads[
                "5" if map_name == "map_a" else "3"
            ] = object()

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(agent_flow_real_trace, "ManagerAgent", return_value=manager), \
             patch.object(agent_flow_real_trace, "setup_paths", side_effect=load_scene) as setup, \
             patch.object(agent_flow_real_trace.XParser, "roads", {}):
            trace = Path(directory) / "trace.jsonl"
            agent_flow_real_trace.run_real_trace(
                trace, object(), tasks,
                road_context=agent_flow_real_trace.ALIGNED_REAL_CONTEXT,
                road_alignments=alignments,
                aligned_xodr_directory="maps",
                aligned_json_directory="scenes",
            )
            events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(
            [(call.args, call.kwargs["json_name"]) for call in setup.call_args_list],
            [(('map_a',), 'scene_a'), (('map_b',), 'scene_b')],
        )
        self.assertEqual(
            [call.args[0] for call in manager.get_commands.call_args_list],
            ["Widen Road 5.", "Steepen Road 3."],
        )
        task_inputs = [event["row"]["user_input"] for event in events
                       if event["type"] == "task" and event["row"].get("user_input")]
        self.assertEqual(task_inputs, tasks)
        aligned_inputs = [event["row"]["input_text"] for event in events
                          if event["type"] == "agent_step"
                          and event["row"].get("agent") == "road_alignment"]
        self.assertEqual(aligned_inputs, ["Widen Road 5.", "Steepen Road 3."])

    def test_single_agent_uses_current_map_and_reuses_its_context(self):
        tasks = ["Widen Road 21.", "Steepen Road 21.", "Narrow Road 21."]
        alignments = {
            task: {
                "case_id": f"req_{index}", "match_status": "matched",
                "scene_file_name": f"scene_{index}.json",
                "map_file_name": "map_a.xodr" if index != 2 else "map_b.xodr",
                "road_id": "5" if index != 2 else "3",
            }
            for index, task in enumerate(tasks, start=1)
        }
        single = Mock()
        single.get_commands.return_value = {"execution_plans": [], "plans": []}

        def load_scene(map_name, **kwargs):
            agent_flow_real_trace.XParser.roads.clear()
            agent_flow_real_trace.XParser.roads[
                "5" if map_name == "map_a" else "3"
            ] = object()

        def build_context(*args):
            road_id = next(iter(agent_flow_real_trace.XParser.roads))
            return {"roads": [{"road_id": f"road_{road_id}"}]}

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(agent_flow_real_trace, "SingleAgent", return_value=single), \
             patch.object(agent_flow_real_trace, "setup_paths", side_effect=load_scene), \
             patch.object(agent_flow_real_trace, "_build_real_single_agent_context",
                          side_effect=build_context) as build, \
             patch.object(agent_flow_real_trace.XParser, "roads", {}):
            agent_flow_real_trace.run_real_trace(
                Path(directory) / "trace.jsonl", object(), tasks,
                road_context=agent_flow_real_trace.ALIGNED_REAL_CONTEXT,
                agent_flow="single", road_alignments=alignments,
            )

        self.assertEqual(build.call_count, 2)
        self.assertEqual(
            [(call.args[0], call.args[1]["roads"][0]["road_id"])
             for call in single.get_commands.call_args_list],
            [("Widen Road 5.", "road_5"),
             ("Steepen Road 3.", "road_3"),
             ("Narrow Road 5.", "road_5")],
        )


class DslToolPlanEvaluationDataTest(unittest.TestCase):
    def test_answer_wrapper_uses_latest_matching_dsl_record(self):
        older = {
            "operations": [
                {
                    "operation": "width",
                    "road_id": "road_21",
                    "lane_ids": ["lane_-1"],
                    "road_limit": "none",
                    "change": {
                        "action": "increase",
                        "mode": "relative",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 1.0,
                            "level": "none",
                            "unit": "m",
                        },
                    },
                }
            ]
        }
        newer = json.loads(json.dumps(older))
        newer["operations"][0]["change"]["magnitude"]["value"] = 2.0
        cell = json.dumps(
            [
                {"value": older, "mode": "write"},
                {"value": newer, "mode": "append"},
            ]
        )

        result = agent_flow_real_trace._extract_answer_dsl(cell, "width")

        self.assertEqual(
            result["operations"][0]["change"]["magnitude"]["value"], 2.0
        )

    def test_request_workbook_produces_reproducible_balanced_sample(self):
        cases = agent_flow_real_trace.load_dsl_tool_plan_cases()

        first = agent_flow_real_trace.select_dsl_tool_plan_cases(cases)
        second = agent_flow_real_trace.select_dsl_tool_plan_cases(cases)

        self.assertEqual(len(first), 15)
        self.assertEqual(Counter(item["agent_type"] for item in first), {
            "width": 5,
            "slope": 5,
            "curve": 5,
        })
        self.assertEqual(
            [(item["agent_type"], item["source_row"]) for item in first],
            [(item["agent_type"], item["source_row"]) for item in second],
        )
        self.assertTrue(all(item["road_id"].isdigit() for item in first))
        self.assertTrue(
            all(
                agent_flow_real_trace._extract_road_ids_from_user_input(
                    item["user_input"]
                )
                == [item["road_id"]]
                for item in first
            )
        )

    def test_evaluation_jsonl_paths_stay_in_run_data_and_do_not_overwrite(self):
        valid = agent_flow_real_trace._resolve_dsl_eval_jsonl_path(
            agent_flow_real_trace.DSL_EVAL_OUTPUT_DIRECTORY / "new-eval-result.jsonl"
        )

        self.assertEqual(valid.name, "new-eval-result.jsonl")
        with self.assertRaisesRegex(ValueError, "testfucs/run_data"):
            agent_flow_real_trace._resolve_dsl_eval_jsonl_path(
                agent_flow_real_trace.PROJECT_ROOT / "testfucs" / ".env"
            )
        with self.assertRaisesRegex(ValueError, "already exists"):
            with patch.object(Path, "exists", return_value=True):
                agent_flow_real_trace._resolve_dsl_eval_jsonl_path(
                    agent_flow_real_trace.DSL_EVAL_OUTPUT_DIRECTORY
                    / "existing-result.jsonl"
                )

    def test_evaluation_jsonl_round_trip_preserves_metadata_and_cases(self):
        payload = {
            "metadata": {"model_name": "gpt-5.4", "sample_count": 1},
            "cases": [
                {
                    "case_id": "dsl_eval_001",
                    "agent_type": "width",
                    "status": "ok",
                    "llm_tool_calls": [
                        {"name": "width_apply", "args": {"items": []}}
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluation.jsonl"
            agent_flow_real_trace._write_dsl_eval_jsonl(path, payload)

            lines = path.read_text(encoding="utf-8").splitlines()
            loaded = agent_flow_real_trace._load_dsl_eval_payload(path)

        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["type"], "evaluation_metadata")
        self.assertEqual(json.loads(lines[1])["type"], "tool_plan_case")
        self.assertEqual(loaded, payload)


class RequestTestDatabaseMigrationTest(unittest.TestCase):
    def test_migration_uses_only_sheet1_and_keeps_tool_answers_empty(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            workbook_path = temp_path / "request-tests.xlsx"
            database_path = temp_path / "request-tests.sqlite3"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Sheet1"
            worksheet.append(list(test_data_store.REQUIRED_COLUMNS))

            def dsl(operation):
                return {"operations": [operation]}

            width_dsl = dsl(
                {
                    "operation": "width",
                    "road_id": "road_21",
                    "lane_ids": ["lane_1"],
                    "road_limit": "none",
                    "change": {
                        "action": "increase",
                        "mode": "relative",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 1,
                            "level": "none",
                            "unit": "m",
                        },
                    },
                }
            )
            slope_dsl = dsl(
                {
                    "operation": "slope",
                    "road_id": "road_21",
                    "position": "end",
                    "road_limit": "none",
                    "change": {
                        "action": "increase",
                        "attribute": "elevation_delta",
                        "mode": "relative",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 2,
                            "level": "none",
                            "unit": "m",
                        },
                    },
                }
            )
            curve_dsl = dsl(
                {
                    "operation": "curve",
                    "road_id": "road_21",
                    "road_limit": "none",
                    "shape": {"target": "keep"},
                    "semantic": {"semantic_change": True, "target": "left"},
                    "modification": [],
                }
            )
            worksheet.append(
                [
                    "Widen, raise, and turn Road 21.",
                    "all",
                    "abstract",
                    None,
                    "上坡路",
                    "C型-右转弯",
                    json.dumps(
                        [{"value": width_dsl, "created_at": "source-time"}]
                    ),
                    json.dumps(slope_dsl),
                    json.dumps([{"value": curve_dsl, "mode": "write"}]),
                ]
            )
            country_sheet = workbook.create_sheet("中国")
            country_sheet.append(["must-not-migrate"])
            workbook.save(workbook_path)

            result = test_data_store.migrate_sheet1(
                workbook_path, database_path
            )

            self.assertEqual(result["test_cases"], 1)
            self.assertEqual(result["dsl_answers"], 3)
            self.assertEqual(result["tool_call_answers"], 0)
            connection = test_data_store.connect_database(database_path)
            try:
                verification = test_data_store.verify_database(connection)
                case = connection.execute("SELECT * FROM test_cases").fetchone()
                answers = connection.execute(
                    """
                    SELECT answer_set_id, subtask_key, agent_type, road_id,
                           metadata_json
                    FROM dsl_answers
                    ORDER BY agent_type
                    """
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(verification["schema_version"], 2)
            self.assertEqual(json.loads(case["operator_types_json"]), [
                "width",
                "slope",
                "curve",
            ])
            self.assertEqual(json.loads(case["road_context_json"]), {
                "width": None,
                "slope": "上坡路",
                "curve": "C型-右转弯",
            })
            self.assertEqual(
                {answer["subtask_key"] for answer in answers},
                {"width_1", "slope_1", "curve_1"},
            )
            self.assertEqual(
                {answer["answer_set_id"] for answer in answers},
                {"req_000001_set_1"},
            )
            self.assertTrue(all(answer["road_id"] == "road_21" for answer in answers))
            self.assertNotIn("must-not-migrate", database_path.read_bytes().decode(
                "utf-8", errors="ignore"
            ))

            connection = test_data_store.connect_database(database_path)
            try:
                curve_answer_id = connection.execute(
                    """
                    SELECT dsl_answer_id
                    FROM dsl_answers
                    WHERE agent_type = 'curve'
                    """
                ).fetchone()[0]
                test_data_store.insert_tool_call_answer(
                    connection,
                    curve_answer_id,
                    1,
                    tool_calls=[{"name": "change_curve_topology", "args": {}}],
                    resolved_result={
                        "optimized_control_points": [
                            [0.0, 1.0],
                            [2.0, 3.0],
                            [4.0, 5.0],
                            [6.0, 7.0],
                        ]
                    },
                    internal_commands=["curve id=21 gi=0"],
                )
                stored = connection.execute(
                    "SELECT resolved_result_json FROM tool_call_answers"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(
                json.loads(stored)["optimized_control_points"],
                [[0.0, 1.0], [2.0, 3.0], [4.0, 5.0], [6.0, 7.0]],
            )

    def test_curve_resolved_result_requires_four_control_points(self):
        test_data_store.validate_resolved_result(
            "curve",
            {
                "optimized_control_points": [
                    [0.0, 1.0],
                    [2.0, 3.0],
                    [4.0, 5.0],
                    [6.0, 7.0],
                ]
            },
        )

        with self.assertRaisesRegex(ValueError, "four control points"):
            test_data_store.validate_resolved_result(
                "curve", {"optimized_control_points": [[0.0, 1.0]]}
            )


class RequestTestDatabaseRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "request-tests.sqlite3"
        shutil.copy2(test_data_store.DEFAULT_DATABASE_PATH, self.database_path)
        connection = sqlite3.connect(self.database_path)
        try:
            with connection:
                connection.execute("DELETE FROM case_reviews")
                connection.execute("DELETE FROM tool_call_answers")
        finally:
            connection.close()

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_dsl_answers_follow_alignment_road_id(self):
        connection = test_data_store.connect_database(self.database_path)
        try:
            answer_row = connection.execute(
                """
                SELECT dsl_answers.dsl_answer_id, dsl_answers.case_id,
                       dsl_answers.agent_type, dsl_answers.road_id
                FROM dsl_answers
                JOIN test_case_road_alignments
                  ON test_case_road_alignments.case_id = dsl_answers.case_id
                WHERE test_case_road_alignments.match_status = 'matched'
                ORDER BY dsl_answers.dsl_answer_id
                LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(answer_row)
            alignment = test_data_store.list_case_road_alignments(
                connection, [answer_row["case_id"]]
            )[answer_row["case_id"]]
            expected_road_id = f"road_{alignment['road_id']}"
            expected_tool_road_id = str(alignment["road_id"])
            with connection:
                test_data_store.insert_tool_call_answer(
                    connection,
                    answer_row["dsl_answer_id"],
                    1,
                    tool_calls=[
                        {
                            "name": "aligned_test",
                            "args": {"id": "21", "road_id": "road_21"},
                        }
                    ],
                    internal_commands=["width id=21 v=1"],
                    review_status="approved",
                )
            entries = test_data_store.list_dsl_answer_entries(
                connection, answer_row["case_id"], answer_row["agent_type"]
            )
            subtasks = test_data_store.list_dsl_answer_subtasks(
                connection, answer_row["case_id"], answer_row["agent_type"]
            )
            baselines = test_data_store.list_auto_review_baselines(
                connection, [answer_row["case_id"]]
            )
        finally:
            connection.close()

        self.assertTrue(entries)
        self.assertTrue(subtasks)
        self.assertTrue(baselines[answer_row["case_id"]][answer_row["agent_type"]])
        self.assertTrue(
            all(
                operation["road_id"] == expected_road_id
                for entry in entries
                for operation in entry["value"]["operations"]
            )
        )
        self.assertTrue(
            all(
                operation["road_id"] == expected_road_id
                for subtask in subtasks
                for operation in subtask["dsl"]["operations"]
            )
        )
        self.assertTrue(
            all(
                operation["road_id"] == expected_road_id
                for answer in baselines[answer_row["case_id"]][answer_row["agent_type"]]
                for operation in answer["dsl"]["operations"]
            )
        )
        tool = subtasks[0]["tool_answers"][0]
        self.assertEqual(
            tool["tool_calls"][0]["args"]["id"], expected_tool_road_id
        )
        self.assertEqual(
            tool["tool_calls"][0]["args"]["road_id"], expected_road_id
        )
        self.assertEqual(
            tool["internal_commands"][0],
            f"width id={expected_tool_road_id} v=1",
        )

    def test_schema_v1_database_is_upgraded_without_changing_test_data(self):
        raw_connection = sqlite3.connect(self.database_path)
        try:
            expected_cases = raw_connection.execute(
                "SELECT COUNT(*) FROM test_cases"
            ).fetchone()[0]
            raw_connection.execute("DROP TABLE case_reviews")
            raw_connection.execute("PRAGMA user_version = 1")
            raw_connection.commit()
        finally:
            raw_connection.close()

        connection = test_data_store.connect_database(self.database_path)
        try:
            verification = test_data_store.verify_database(connection)
        finally:
            connection.close()

        self.assertEqual(verification["schema_version"], 2)
        self.assertEqual(verification["test_cases"], expected_cases)
        self.assertEqual(verification["case_reviews"], 0)

    def test_case_review_upsert_preserves_human_review_from_auto_review(self):
        connection = test_data_store.connect_database(self.database_path)
        try:
            case_id = connection.execute(
                "SELECT case_id FROM test_cases ORDER BY source_row LIMIT 1"
            ).fetchone()[0]
            with connection:
                automatic = test_data_store.upsert_case_review(
                    connection,
                    case_id,
                    "unit-run",
                    "task_1",
                    "pass",
                    note="auto",
                    review_source="auto",
                    trace_source="trace.jsonl",
                )
                human = test_data_store.upsert_case_review(
                    connection,
                    case_id,
                    "unit-run",
                    "task_1",
                    "question",
                    note="check manually",
                    review_source="human",
                    trace_source="trace.jsonl",
                )
                ignored_auto = test_data_store.upsert_case_review(
                    connection,
                    case_id,
                    "unit-run",
                    "task_1",
                    "fail",
                    note="later auto",
                    review_source="auto",
                    trace_source="trace.jsonl",
                )
            reviews = test_data_store.list_case_reviews(connection, "unit-run")
        finally:
            connection.close()

        self.assertEqual(automatic["review_source"], "auto")
        self.assertEqual(human["decision"], "question")
        self.assertEqual(ignored_auto["decision"], "question")
        self.assertEqual(reviews, [ignored_auto])

    def test_road_context_updates_preserve_append_and_replace_behavior(self):
        connection = test_data_store.connect_database(self.database_path)
        try:
            case = connection.execute(
                "SELECT case_id, input_text FROM test_cases ORDER BY source_row LIMIT 1"
            ).fetchone()
            with connection:
                test_data_store.save_road_context_values(
                    connection,
                    case["input_text"],
                    {"slope": ["上坡路"]},
                )
                test_data_store.save_road_context_values(
                    connection,
                    case["input_text"],
                    {"slope": ["下坡路"], "curve": ["C型-左转弯"]},
                    append=True,
                )
            stored = connection.execute(
                "SELECT road_context_json FROM test_cases WHERE case_id = ?",
                (case["case_id"],),
            ).fetchone()[0]
        finally:
            connection.close()

        context = json.loads(stored)
        self.assertEqual(context["slope"], "上坡路\n下坡路")
        self.assertEqual(context["curve"], "C型-左转弯")

    def test_llm_judge_writes_results_to_database(self):
        connection = test_data_store.connect_database(self.database_path)
        try:
            user_input = connection.execute(
                "SELECT input_text FROM test_cases ORDER BY source_row LIMIT 1"
            ).fetchone()[0]
        finally:
            connection.close()

        LLMjudge.save_judge_results(
            user_input,
            [
                {"type_name": "slope", "road_type": "平路"},
                {"type_name": "curve", "road_type": "S型-先左后右"},
            ],
            database_path=self.database_path,
        )
        LLMjudge.append_judge_result(
            user_input,
            {"type_name": "curve", "road_type": "C型-右转弯"},
            database_path=self.database_path,
        )

        connection = test_data_store.connect_database(self.database_path)
        try:
            context = json.loads(
                connection.execute(
                    "SELECT road_context_json FROM test_cases WHERE input_text = ?",
                    (user_input,),
                ).fetchone()[0]
            )
        finally:
            connection.close()
        self.assertEqual(context["slope"], "平路")
        self.assertEqual(context["curve"], "S型-先左后右\nC型-右转弯")

    def test_dsl_edit_marks_tool_answer_stale_and_keeps_viewer_shape(self):
        connection = test_data_store.connect_database(self.database_path)
        try:
            dsl_row = connection.execute(
                """
                SELECT dsl_answer_id, case_id
                FROM dsl_answers
                WHERE agent_type = 'width'
                ORDER BY dsl_answer_id
                LIMIT 1
                """
            ).fetchone()
            entries = test_data_store.list_dsl_answer_entries(
                connection, dsl_row["case_id"], "width"
            )
            with connection:
                tool_answer_id = test_data_store.insert_tool_call_answer(
                    connection,
                    dsl_row["dsl_answer_id"],
                    1,
                    tool_calls=[{"name": "width_apply", "args": {"items": []}}],
                    review_status="approved",
                )
                entries[0]["value"]["operations"][0]["road_limit"] = "road_9"
                stored_entries = test_data_store.replace_dsl_answer_entries(
                    connection, dsl_row["case_id"], "width", entries
                )
            tool_status = connection.execute(
                "SELECT review_status FROM tool_call_answers WHERE tool_answer_id = ?",
                (tool_answer_id,),
            ).fetchone()[0]
            viewer_rows = test_data_store.build_viewer_request_rows(connection)
        finally:
            connection.close()

        self.assertEqual(tool_status, "stale")
        self.assertEqual(
            stored_entries[0]["value"]["operations"][0]["road_limit"], "road_9"
        )
        self.assertEqual(viewer_rows[0][0], "case_id")
        self.assertEqual(len(viewer_rows), 813)

    def test_viewer_server_reads_database_and_updates_input(self):
        server = agent_trace_viewer_server.create_server(
            self.database_path, port=0
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(f"{base_url}/api/request-data") as response:
                payload = json.load(response)
            case_id = payload["rows"][1][0]
            next_input = f"{payload['rows'][1][1]} database-edit"
            request = Request(
                f"{base_url}/api/cases/{case_id}",
                data=json.dumps({"input_text": next_input}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PATCH",
            )
            with opener.open(request) as response:
                result = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertTrue(result["ok"])
        connection = test_data_store.connect_database(self.database_path)
        try:
            stored = connection.execute(
                "SELECT input_text FROM test_cases WHERE case_id = ?", (case_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(stored, next_input)

    def test_viewer_shutdown_api_stops_server(self):
        server = agent_trace_viewer_server.create_server(self.database_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        opener = build_opener(ProxyHandler({}))
        try:
            unauthorized = Request(
                f"{base_url}/api/shutdown", data=b"", method="POST"
            )
            with self.assertRaises(HTTPError) as context:
                opener.open(unauthorized)
            self.assertEqual(context.exception.code, 403)
            self.assertTrue(thread.is_alive())

            request = Request(
                f"{base_url}/api/shutdown",
                data=b"",
                headers={"X-Viewer-Control": "viewer-service-bat"},
                method="POST",
            )
            with opener.open(request) as response:
                result = json.load(response)
            thread.join(timeout=5)
        finally:
            if thread.is_alive():
                server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertTrue(result["ok"])
        self.assertFalse(thread.is_alive())

    def test_viewer_service_script_uses_graceful_shutdown_only(self):
        script_path = Path(agent_trace_viewer_server.__file__).with_name(
            "viewer_service.bat"
        )
        script = script_path.read_text(encoding="ascii").lower()

        self.assertIn("http://127.0.0.1:", script)
        self.assertIn("x-viewer-control: viewer-service-bat", script)
        for forbidden in (
            "powershell",
            "executionpolicy",
            "stop-process",
            "taskkill",
            "wmic",
            "get-process",
        ):
            self.assertNotIn(forbidden, script)

    def test_viewer_tool_review_control_styles(self):
        viewer_path = Path(agent_trace_viewer_server.__file__).with_name("viewer.html")
        source = viewer_path.read_text(encoding="utf-8")
        disabled_rule = source.split(".field-action:disabled", 1)[1].split("}", 1)[0]
        action_rule = source.split(".tool-review-actions .field-action", 1)[1].split("}", 1)[0]

        self.assertIn("cursor: default", disabled_rule)
        self.assertIn("pointer-events: none", disabled_rule)
        self.assertIn("flex: 0 0 auto", action_rule)
        self.assertIn("white-space: nowrap", action_rule)

    def test_viewer_refreshes_tool_review_after_dsl_answer_changes(self):
        viewer_path = Path(agent_trace_viewer_server.__file__).with_name("viewer.html")
        source = viewer_path.read_text(encoding="utf-8")
        helper_body = source.split(
            "async function refreshAnswerAndToolReviewPanels(task)", 1
        )[1].split("function toolReviewSelectionKey", 1)[0]

        self.assertIn("await refreshAnswerComparePanels(task)", helper_body)
        self.assertIn("await refreshToolReviewPanels(task)", helper_body)

    def test_viewer_curve_comparison_draws_legacy_and_agent_results(self):
        viewer_path = Path(agent_trace_viewer_server.__file__).with_name("viewer.html")
        source = viewer_path.read_text(encoding="utf-8")

        self.assertIn("function getLegacyCurvePoints", source)
        self.assertIn("function applyCurveCommandToPoints", source)
        self.assertIn("renderCurveSvg(beforePoints, legacyPoints, afterPoints)", source)
        self.assertIn("旧 DSL 优化", source)
        self.assertIn("Agent 自主调用", source)
        self.assertIn('stroke="oklch(55% 0.2 28)"', source)

    def test_viewer_supports_dragging_a_trace_file(self):
        viewer_path = Path(agent_trace_viewer_server.__file__).with_name("viewer.html")
        source = viewer_path.read_text(encoding="utf-8")

        self.assertIn('id="fileDropOverlay"', source)
        self.assertIn("body.is-file-drag-over .file-panel", source)
        self.assertIn(
            'document.addEventListener("dragenter", handlePageFileDragEnter)', source
        )
        self.assertIn(
            'document.addEventListener("drop", handlePageFileDrop)', source
        )
        self.assertIn("function clearPageFileDragState", source)
        self.assertIn("async function loadDroppedTraceFile", source)
        dropped_file_start = source.index("async function loadDroppedTraceFile")
        dropped_file_end = source.index(
            "async function chooseTraceDirectory", dropped_file_start
        )
        self.assertIn(
            "await loadTraceFile(files[0])",
            source[dropped_file_start:dropped_file_end],
        )
        self.assertIn("function releaseLoadedTraceState", source)
        self.assertIn("async function flushPendingReviewSaves", source)
        self.assertIn("traceLoadToken", source)
        self.assertIn("await flushPendingReviewSaves()", source)
        release_start = source.index("function releaseLoadedTraceState")
        release_end = source.index("async function loadTraceFile", release_start)
        release_source = source[release_start:release_end]
        self.assertIn('state.statusFilter = "all"', release_source)
        self.assertIn('state.instructionFilter = "all"', release_source)
        self.assertIn('state.accuracyFailureFilter = "all"', release_source)
        self.assertIn('state.reviewFilter = "all"', release_source)
        self.assertIn('state.search = ""', release_source)
        self.assertIn('$("#taskList").replaceChildren()', release_source)
        self.assertIn('$("#overviewView").replaceChildren()', release_source)
        self.assertIn('$("#detailView").replaceChildren()', release_source)

    def test_viewer_case_review_api_round_trip(self):
        connection = test_data_store.connect_database(self.database_path)
        try:
            case_id = connection.execute(
                "SELECT case_id FROM test_cases ORDER BY source_row LIMIT 1"
            ).fetchone()[0]
        finally:
            connection.close()

        server = agent_trace_viewer_server.create_server(self.database_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        opener = build_opener(ProxyHandler({}))
        review_payload = {
            "case_id": case_id,
            "run_id": "viewer-run",
            "task_id": "real_001",
            "decision": "pass",
            "note": "reviewed in viewer",
            "review_source": "human",
            "trace_source": "viewer-trace.jsonl",
            "metadata": {"agent_types": ["width"]},
        }
        try:
            request = Request(
                f"{base_url}/api/case-reviews",
                data=json.dumps(review_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            with opener.open(request) as response:
                stored = json.load(response)["review"]
            with opener.open(f"{base_url}/api/case-reviews") as response:
                reviews = json.load(response)["reviews"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(stored["decision"], "pass")
        self.assertEqual(stored["review_source"], "human")
        self.assertEqual(reviews, [stored])

    def test_viewer_auto_review_batch_api_round_trip(self):
        connection = test_data_store.connect_database(self.database_path)
        try:
            cases = connection.execute(
                "SELECT case_id FROM test_cases ORDER BY source_row LIMIT 2"
            ).fetchall()
        finally:
            connection.close()
        case_ids = [row["case_id"] for row in cases]

        server = agent_trace_viewer_server.create_server(self.database_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        opener = build_opener(ProxyHandler({}))

        def api(path, method, payload):
            request = Request(
                f"{base_url}{path}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method=method,
            )
            with opener.open(request) as response:
                return json.load(response)

        reviews = [
            {
                "case_id": case_id,
                "run_id": "batch-run",
                "task_id": f"real_{index:03d}",
                "decision": "question",
                "note": "batch auto review",
                "review_source": "auto",
                "trace_source": "batch.jsonl",
                "metadata": {"agent_types": ["width"]},
            }
            for index, case_id in enumerate(case_ids, start=1)
        ]
        try:
            baselines = api(
                "/api/auto-review-baselines", "POST", {"case_ids": case_ids}
            )["cases"]
            stored = api(
                "/api/case-reviews/batch", "PUT", {"reviews": reviews}
            )["reviews"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(set(baselines), set(case_ids))
        self.assertEqual(
            set(baselines[case_ids[0]]),
            {"width", "slope", "curve", "road_alignment"},
        )
        self.assertEqual(
            baselines[case_ids[0]]["road_alignment"]["match_status"],
            "matched",
        )
        self.assertEqual(len(stored), 2)
        self.assertEqual({review["task_id"] for review in stored}, {"real_001", "real_002"})

    def test_viewer_tool_answer_api_round_trip_and_multiple_approved_variants(self):
        connection = test_data_store.connect_database(self.database_path)
        try:
            dsl_row = connection.execute(
                """
                SELECT dsl_answer_id, case_id
                FROM dsl_answers
                WHERE agent_type = 'width'
                ORDER BY dsl_answer_id
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()

        server = agent_trace_viewer_server.create_server(self.database_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        opener = build_opener(ProxyHandler({}))

        def api(path, method="GET", payload=None):
            data = None if payload is None else json.dumps(payload).encode("utf-8")
            request = Request(
                f"{base_url}{path}",
                data=data,
                headers={"Content-Type": "application/json"},
                method=method,
            )
            with opener.open(request) as response:
                return json.load(response)

        base_answer = {
            "tool_calls": [{"name": "width_apply", "args": {"items": []}}],
            "resolved_result": [],
            "internal_commands": [],
            "random_seed": None,
            "metadata": {"trace_source": "unit-test.xlsx"},
        }
        try:
            subtasks = api(
                f"/api/cases/{dsl_row['case_id']}/dsl-subtasks/width"
            )["subtasks"]
            self.assertIn(
                dsl_row["dsl_answer_id"],
                {item["dsl_answer_id"] for item in subtasks},
            )

            first = api(
                f"/api/dsl-answers/{dsl_row['dsl_answer_id']}/tool-call-answers",
                "POST",
                base_answer,
            )["answer"]
            self.assertEqual(first["variant_index"], 1)
            self.assertEqual(first["review_status"], "approved")

            updated_payload = {
                **base_answer,
                "internal_commands": ["width id=3 v=0.5 s=1 ms=0 sh=1 li=-1"],
            }
            updated = api(
                f"/api/tool-call-answers/{first['tool_answer_id']}",
                "PUT",
                updated_payload,
            )["answer"]
            self.assertEqual(updated["review_status"], "approved")
            self.assertEqual(updated["internal_commands"], updated_payload["internal_commands"])

            approved_first = api(
                f"/api/tool-call-answers/{first['tool_answer_id']}",
                "PATCH",
                {"review_status": "approved"},
            )["answer"]
            second = api(
                f"/api/dsl-answers/{dsl_row['dsl_answer_id']}/tool-call-answers",
                "POST",
                base_answer,
            )["answer"]
            approved_second = api(
                f"/api/tool-call-answers/{second['tool_answer_id']}",
                "PATCH",
                {"review_status": "approved"},
            )["answer"]
            self.assertEqual(approved_first["review_status"], "approved")
            self.assertEqual(approved_second["review_status"], "approved")
            self.assertEqual(approved_second["variant_index"], 2)

            refreshed = api(
                f"/api/cases/{dsl_row['case_id']}/dsl-subtasks/width"
            )["subtasks"]
            selected = next(
                item for item in refreshed
                if item["dsl_answer_id"] == dsl_row["dsl_answer_id"]
            )
            self.assertEqual(
                [answer["review_status"] for answer in selected["tool_answers"]],
                ["approved", "approved"],
            )

            api(
                f"/api/tool-call-answers/{first['tool_answer_id']}",
                "DELETE",
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        connection = test_data_store.connect_database(self.database_path)
        try:
            remaining = test_data_store.list_tool_call_answers(
                connection, dsl_row["dsl_answer_id"]
            )
        finally:
            connection.close()
        self.assertEqual(
            [answer["tool_answer_id"] for answer in remaining],
            [second["tool_answer_id"]],
        )


if __name__ == "__main__":
    unittest.main()
