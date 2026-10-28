"""Offline regressions from the observed width failures; no answer DB or API."""
import copy
import json
import random
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llm.manager_agent import ManagerAgent
from llm.single_agent import SingleAgent
from llm.width_agent import WidthAgent
from llm.dsl_format_validator import validate_dsl_format
from llm.width_guardrails import (width_context, road_ids, checker_errors,
                                  unrelated_repair_changes, validate_formula)
from handler.agent_dispatcher import AgentDispatcher


INFO = {'road_id': 'road_3', 'lane_ids': [1, -1, -2],
        'lane_avg_width': {'1': 3.7198, '-1': 4.2142, '-2': 3.6}}


def dsl(action='decrease', value=10, unit='percent', lanes=None):
    return {'operations': [{'operation': 'width', 'road_id': 'road_3',
        'lane_ids': lanes or ['lane_-1'], 'road_limit': 'none',
        'change': {'mode': 'relative', 'action': action,
            'magnitude': {'kind': 'numeric', 'level': 'none', 'unit': unit, 'value': value}}}]}


def call(formula):
    return {'name': 'width_apply', 'args': {'items': [{'operation_index': 0,
        'formula': formula, 'id': '3', 'li': [-1], 's': 1, 'ms': 99, 'sh': 1}]}}


def check(passed=True, field='operations[0].lane_ids'):
    return {'passed': passed, 'problem_source': 'none' if passed else 'extraction_error',
        'request_status': 'valid', 'dsl_status': 'correct' if passed else 'incorrect',
        'issues': [] if passed else [{'source': 'dsl', 'code': 'target_mismatch',
            'field': field, 'expected': 'lane_-2', 'actual': 'lane_-1',
            'reason': 'The requested target is the rightmost entry of the supplied position table.'}],
        'check_result': 'OK' if passed else 'Wrong lane target'}


class WidthGuardrailTest(unittest.TestCase):
    def test_explicit_road_forms_and_chinese_lane_false_positive(self):
        self.assertEqual(road_ids('Widen lane -3 on roadway 0'), ['0'])
        self.assertEqual(road_ids('把7号道路的高速公路1号车道大幅度收窄。'), ['7'])
        self.assertEqual(road_ids('把7号道路的高速公路14号车道收窄。'), ['7'])
        self.assertEqual(road_ids('Road 3 and road_4'), ['3', '4'])

    def test_manager_structured_road_is_preserved_and_conflicts_rejected(self):
        manager = ManagerAgent.__new__(ManagerAgent)
        result = manager._attach_road_ids([{'road_id': 'road_3', 'type_name': 'width',
                                           'description': 'Widen the lane'}])
        self.assertEqual(result[0]['road_id'], 'road_3')
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            manager._attach_road_ids([{'road_id': 'road_3', 'type_name': 'width',
                                      'description': 'Widen roadway 4'}])
        self.assertEqual(manager._extract_unique_road_id('roadway 0 lane -3'), 'road_0')

    @patch('handler.agent_dispatcher._use_manual_road_context', return_value=False)
    @patch.dict('handler.agent_dispatcher.XParser.roads', {}, clear=True)
    def test_absent_map_road_is_rejected(self, _):
        with self.assertRaisesRegex(ValueError, 'absent'):
            AgentDispatcher()._manager_road_id({'road_id': 'road_999'})

    def test_position_and_width_facts_preserve_context_order(self):
        ctx = width_context(INFO)
        self.assertEqual(ctx['lane_position_table'], [
            {'lane_id': 1, 'from_left': 1, 'from_right': 3},
            {'lane_id': -1, 'from_left': 2, 'from_right': 2},
            {'lane_id': -2, 'from_left': 3, 'from_right': 1}])
        self.assertEqual(ctx['lane_width_rank']['widest'], [-1])
        one = width_context({'lane_ids': [-1], 'lane_avg_width': {'-1': 3.0}})
        self.assertEqual(one['lane_width_rank'], {'narrowest': [-1], 'widest': [-1]})
        self.assertNotIn('lane_position_table', INFO)

    def test_width_mode_combinations(self):
        payload = dsl(unit='m', value=1.4)
        self.assertTrue(validate_dsl_format('width', payload, INFO)['passed'])
        payload['operations'][0]['change']['mode'] = 'absolute'
        self.assertFalse(validate_dsl_format('width', payload, INFO)['passed'])
        payload['operations'][0]['change']['action'] = 'set'
        self.assertTrue(validate_dsl_format('width', payload, INFO)['passed'])

    def test_projected_negative_width_is_not_rejected(self):
        payload = dsl(unit='m', value=9)
        self.assertTrue(validate_dsl_format('width', payload, INFO)['passed'])
        validate_formula(call('-9')['args']['items'][0], payload['operations'][0], INFO)
        over_percent = dsl(unit='percent', value=150)
        self.assertTrue(validate_dsl_format('width', over_percent, INFO)['passed'])
        validate_formula(call('-(4.2142 * 150 / 100)')['args']['items'][0],
                         over_percent['operations'][0], INFO)

    def test_shared_prompts_no_longer_require_negative_width_refusal(self):
        from llm.agent_tools import WIDTH_DSL_DEFINE, WIDTH_DSL_PROMPT, WIDTH_DSL_CHECK_PROMPT
        self.assertNotIn('任何投影结果不得小于 0', WIDTH_DSL_DEFINE)
        self.assertNotIn('请求导致负宽度，不能截断或强行生成操作', WIDTH_DSL_CHECK_PROMPT)
        self.assertIn('预计结果为负，仍表达用户要求', WIDTH_DSL_PROMPT)

    def test_contradictory_checker_and_fake_issue_are_invalid(self):
        self.assertEqual(checker_errors(check()), [])
        self.assertEqual(checker_errors(check(False)), [])
        contradictory = check()
        contradictory['passed'] = False
        self.assertTrue(checker_errors(contradictory))
        identical = check(False)
        identical['issues'][0]['expected'] = identical['issues'][0]['actual']
        self.assertIn('DSL issue expected and actual are identical', checker_errors(identical))

    def test_collateral_dsl_changes_rejected_without_local_rewriting(self):
        before = dsl()
        after = copy.deepcopy(before)
        after['operations'][0]['lane_ids'] = ['lane_-2']
        self.assertEqual(unrelated_repair_changes(before, after, check(False)['issues']), [])
        after['operations'][0]['change']['mode'] = 'absolute'
        self.assertEqual(unrelated_repair_changes(before, after, check(False)['issues']),
                         ['operations[0].change.mode'])

    def test_formula_negative_sign_and_value(self):
        item = call('-(4.2142 * 10 / 100)')['args']['items'][0]
        validate_formula(item, dsl()['operations'][0], INFO)
        for formula in ['4.2142 * 10 / 100', '-0.2', '-random(0.1,0.5)']:
            with self.subTest(formula=formula), self.assertRaises(ValueError):
                validate_formula(dict(item, formula=formula), dsl()['operations'][0], INFO)

    def test_set_target_can_require_negative_delta(self):
        operation = dsl('set', 3.2, 'm')['operations'][0]
        operation['change']['mode'] = 'absolute'
        validate_formula(call('3.2 - 4.2142')['args']['items'][0], operation, INFO)

    def test_whole_road_divisor_and_exact_coverage(self):
        operation = dsl('increase', 2, 'm')['operations'][0]
        operation['lane_ids'] = 'none'
        item = dict(call('2/3')['args']['items'][0], li=[])
        validate_formula(item, operation, INFO)
        with self.assertRaises(ValueError):
            validate_formula(dict(item, formula='2'), operation, INFO)
        agent = WidthAgent.__new__(WidthAgent)
        duplicates = call('-0.42142')
        duplicates['args']['items'] *= 2
        with self.assertRaisesRegex(ValueError, 'cover exactly'):
            agent._validate_plan_targets(dsl(), INFO, [duplicates])

    def test_qualitative_validation_does_not_consume_random_state(self):
        operation = dsl()['operations'][0]
        operation['change']['magnitude'] = {'kind': 'qualitative', 'level': 'significant', 'unit': 'none', 'value': 'none'}
        before = random.getstate()
        validate_formula(call('-(4.2142 * random(0.40,0.70))')['args']['items'][0], operation, INFO)
        self.assertEqual(random.getstate(), before)
        with self.assertRaises(ValueError):
            validate_formula(call('4.2142 * random(0.40,0.70)')['args']['items'][0], operation, INFO)

    def test_invalid_checker_rechecks_same_dsl_without_reextracting(self):
        agent = WidthAgent.__new__(WidthAgent)
        agent.chain, agent.check_chain, agent.enable_dsl_check = object(), object(), True
        payload = dsl()
        invalid = check()
        invalid['passed'] = False
        seen = []

        def invoke(chain, request, **kwargs):
            seen.append((kwargs['request_kind'], copy.deepcopy(request)))
            if kwargs['request_kind'] == 'dsl_extract':
                return payload
            return invalid if len(seen) == 2 else check()

        with patch('llm.width_agent.agent_trace.invoke_llm', side_effect=invoke):
            self.assertEqual(agent.get_commands('Narrow lane -1 by 10%', INFO), payload)
        self.assertEqual([x[0] for x in seen], ['dsl_extract', 'dsl_check', 'dsl_check'])
        self.assertEqual(seen[1][1]['dslOutput'], seen[2][1]['dslOutput'])
        self.assertIn('Invalid checker response', seen[2][1]['checkerFeedback'])

    def test_tool_repair_keeps_dsl_and_only_replans_tools(self):
        agent = WidthAgent.__new__(WidthAgent)
        agent.execution_chain = object()
        seen = []

        def invoke(chain, request, **kwargs):
            seen.append((kwargs, copy.deepcopy(request)))
            formula = '4.2142*10/100' if len(seen) == 1 else '-(4.2142*10/100)'
            return SimpleNamespace(tool_calls=[call(formula)])

        with patch('llm.width_agent.agent_trace.invoke_llm', side_effect=invoke):
            calls = agent.plan_execution(dsl(), INFO)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0][1]['dsl'], seen[1][1]['dsl'])
        self.assertTrue(all(x[0]['request_kind'] == 'tool_plan' for x in seen))
        self.assertLess(agent.resolve_execution_plan(dsl(), INFO, calls)[0]['args']['v'], 0)

    def test_single_agent_tool_only_repair_updates_returned_plan(self):
        agent = SingleAgent.__new__(SingleAgent)
        agent.model = object()
        agent.width_validator = WidthAgent.__new__(WidthAgent)
        bad = {'dsl': dsl(), 'plans': [{'agent': 'width', 'road_id': 'road_3',
                                      'tool_calls': [call('4.2142*10/100')]}]}
        with patch('llm.single_agent.WidthAgent') as planner:
            planner.return_value.plan_execution.return_value = [call('-(4.2142*10/100)')]
            result = agent._prepare_result(bad, {'roads': [{'road_id': 'road_3', 'width': INFO}]})
        self.assertEqual(planner.call_count, 1)
        self.assertEqual(result['dsl'], dsl())
        self.assertEqual(result['plans'][0]['tool_calls'], [call('-(4.2142*10/100)')])
        self.assertLess(result['execution_plans'][0]['resolved_tool_calls'][0]['args']['v'], 0)


if __name__ == '__main__':
    unittest.main()
