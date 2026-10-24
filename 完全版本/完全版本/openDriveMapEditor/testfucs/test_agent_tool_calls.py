import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from handler.agent_dispatcher import AgentDispatcher
from handler.curve_optimizer import CurveOptimizationService
from handler.dsl_reference_converter import DslReferenceConverter
from llm.curve_agent import (
    AdjustCurveCurvatureArgs,
    ChangeCurveTopologyArgs,
    CurveAgent,
    ShiftCurveCentroidArgs,
)
from llm.manager_agent import ManagerAgent
from llm.agent_tools import evaluate_calculation_formula
from llm.single_agent import SingleAgent
from llm.slope_agent import SlopeAgent, SlopeToolArguments
from llm.width_agent import WidthAgent, WidthToolArguments
from utils import agent_trace


class SingleAgentPlanTest(unittest.TestCase):
    def setUp(self):
        self.agent = SingleAgent.__new__(SingleAgent)
        self.agent.width_validator = WidthAgent.__new__(WidthAgent)
        self.agent.slope_validator = SlopeAgent.__new__(SlopeAgent)
        self.agent.curve_validator = CurveAgent.__new__(CurveAgent)
        self.road_context = {
            "roads": [
                {
                    "road_id": "road_21",
                    "width": {
                        "road_id": "road_21",
                        "lane_ids": [1, -1],
                        "lane_avg_width": {"1": 3.4, "-1": 3.5},
                    },
                    "slope": {
                        "road_id": "road_21",
                        "length": 50.0,
                        "start_elevation": 0.0,
                        "end_elevation": 0.0,
                        "grade_degree": 0.0,
                    },
                    "curve": {
                        "road_id": "road_21",
                        "gi": 0,
                        "current_shape": "C-shape",
                        "semantic": "left",
                        "p_s": 0.3,
                        "p_e": 0.4,
                    },
                }
            ]
        }

    def _width_result(self, plan_road_id="road_21"):
        return {
            "dsl": {
                "operations": [
                    {
                        "operation": "width",
                        "road_id": "road_21",
                        "lane_ids": ["lane_-1"],
                        "road_limit": "none",
                        "change": {
                            "mode": "relative",
                            "action": "increase",
                            "magnitude": {
                                "kind": "numeric",
                                "value": 0.5,
                                "level": "none",
                                "unit": "m",
                            },
                        },
                    }
                ]
            },
            "plans": [
                {
                    "agent": "width",
                    "road_id": plan_road_id,
                    "tool_calls": [
                        {
                            "name": "width_apply",
                            "args": {
                                "items": [
                                    {
                                        "operation_index": 0,
                                        "formula": "0.5",
                                        "id": "21",
                                        "s": 1,
                                        "ms": 99,
                                        "sh": 1,
                                        "li": [-1],
                                    }
                                ]
                            },
                        }
                    ],
                }
            ],
        }

    def test_unified_result_is_resolved_with_existing_width_rules(self):
        result = self.agent._prepare_result(
            self._width_result(), self.road_context
        )

        self.assertEqual(result["plans"][0]["road_id"], "road_21")
        execution_plan = result["execution_plans"][0]
        self.assertEqual(execution_plan["agent"], "width")
        self.assertEqual(
            execution_plan["resolved_tool_calls"][0]["args"]["v"], 0.5
        )

    def test_unified_result_rejects_plan_for_different_road(self):
        with self.assertRaisesRegex(ValueError, "correspond exactly"):
            self.agent._prepare_result(
                self._width_result(plan_road_id="road_20"),
                self.road_context,
            )

    def test_get_commands_uses_one_unified_llm_request(self):
        self.agent.chain = object()
        with patch.object(
            agent_trace, "invoke_llm", return_value=self._width_result()
        ) as invoke_llm, patch.object(agent_trace, "record_agent_step"):
            result = self.agent.get_commands("Widen lane -1 on road 21.", self.road_context)

        self.assertEqual(result["execution_plans"][0]["agent"], "width")
        invoke_llm.assert_called_once()
        self.assertEqual(
            invoke_llm.call_args.kwargs["request_kind"],
            "single_dsl_tool_plan",
        )


class ToolArgumentSchemaTest(unittest.TestCase):
    def test_width_requires_every_handler_parameter(self):
        with self.assertRaises(ValidationError):
            WidthToolArguments.model_validate(
                {"id": "3", "v": 0.5, "s": 1, "ms": 0, "sh": 1}
            )

        arguments = WidthToolArguments.model_validate(
            {"id": "3", "v": 0.5, "s": 1, "ms": 0, "sh": 1, "li": [-1]}
        )
        self.assertEqual(arguments.li, [-1])

    def test_slope_requires_every_handler_parameter(self):
        arguments = SlopeToolArguments.model_validate(
            {"id": "5", "v": 2.4, "m": "add", "mv": 1, "ms": 0, "sh": 1}
        )
        self.assertEqual(arguments.model_dump()["m"], "add")

        with self.assertRaises(ValidationError):
            SlopeToolArguments.model_validate(
                {"id": "5", "v": 2.4, "m": "angle", "mv": 1, "ms": 0, "sh": 1}
            )

    def test_curve_topology_rejects_shape_semantic_conflicts(self):
        with self.assertRaises(ValidationError):
            ChangeCurveTopologyArgs.model_validate(
                {
                    "road_id": "5",
                    "gi": 0,
                    "target_shape": "C-shape",
                    "target_semantic": "left_right",
                }
            )

    def test_tool_argument_schemas_reject_boolean_numbers(self):
        with self.assertRaises(ValidationError):
            WidthToolArguments.model_validate(
                {"id": "3", "v": True, "s": 1, "ms": 0, "sh": 1, "li": []}
            )
        with self.assertRaises(ValidationError):
            SlopeToolArguments.model_validate(
                {"id": "5", "v": 1.0, "m": "add", "mv": True, "ms": 0, "sh": 1}
            )
        with self.assertRaises(ValidationError):
            ChangeCurveTopologyArgs.model_validate(
                {
                    "road_id": "5",
                    "gi": True,
                    "target_shape": "C-shape",
                    "target_semantic": "left",
                }
            )


class CalculationFormulaTest(unittest.TestCase):
    def test_formula_supports_whitelisted_arithmetic_and_functions(self):
        value = evaluate_calculation_formula(
            "100.0 * tan(radians(abs(5.0))) - 2.0"
        )
        self.assertEqual(value, 6.749)

        with patch("llm.agent_tools.random.uniform", return_value=0.2):
            self.assertAlmostEqual(
                evaluate_calculation_formula("3.5 * random(0.05, 0.25)"), 0.7
            )

    def test_formula_rejects_unsafe_or_invalid_expressions(self):
        bad_formulas = (
            "road.__class__",
            "unknown_value + 1",
            "1.0 / 0",
            "random(0.70, 0.40)",
        )
        for formula in bad_formulas:
            with self.subTest(formula=formula):
                with self.assertRaises(ValueError):
                    evaluate_calculation_formula(formula)

    def test_formula_random_cache_reuses_one_sample(self):
        random_cache = {}
        with patch("llm.agent_tools.random.uniform", return_value=0.3) as uniform:
            first = evaluate_calculation_formula(
                "3.0 * random(0.25, 0.40)", random_cache=random_cache
            )
            second = evaluate_calculation_formula(
                "3.5 * random(0.25, 0.40)", random_cache=random_cache
            )

        self.assertEqual(first, 0.9)
        self.assertEqual(second, 1.05)
        uniform.assert_called_once_with(0.25, 0.4)


class ManagerRoadIdTest(unittest.TestCase):
    def setUp(self):
        self.manager = ManagerAgent.__new__(ManagerAgent)

    def test_executable_tasks_receive_road_id_from_chinese_and_english(self):
        commands = self.manager._attach_road_ids(
            [
                {"type_name": "width", "description": "将3号道路加宽"},
                {"type_name": "slope", "description": "increase slope on road_12"},
                {"type_name": "recommend", "description": "recommend a road"},
            ]
        )

        self.assertEqual(commands[0]["road_id"], "road_3")
        self.assertEqual(commands[1]["road_id"], "road_12")
        self.assertNotIn("road_id", commands[2])

    def test_executable_task_requires_exactly_one_road_id(self):
        for description in ("increase the width", "change road 3 and road 4"):
            with self.subTest(description=description):
                with self.assertRaisesRegex(ValueError, "exactly one road id"):
                    self.manager._attach_road_ids(
                        [{"type_name": "width", "description": description}]
                    )


class CurveToolMappingTest(unittest.TestCase):
    def setUp(self):
        self.service = CurveOptimizationService(None)

    def test_c_shape_maps_sides_to_p1_and_p2_without_forcing_division(self):
        calls = [
            {
                "name": "adjust_curve_curvature",
                "args": AdjustCurveCurvatureArgs(
                    road_id="5",
                    gi=0,
                    side="start_side",
                    value_type="curvature",
                    mode="relative",
                    action="increase",
                    magnitude_kind="qualitative",
                    value=None,
                    level="slight",
                    unit="none",
                ).model_dump(),
            },
            {
                "name": "shift_curve_centroid",
                "args": ShiftCurveCentroidArgs(
                    road_id="5",
                    gi=0,
                    side="end_side",
                    mode="relative",
                    action="move_to_end",
                    magnitude_kind="numeric",
                    value=10,
                    level="none",
                    unit="percent",
                ).model_dump(),
            },
        ]

        intent = self.service.build_curve_intent_from_tool_calls(
            calls,
            {"current_shape": "C-shape", "semantic": "right"},
        )

        self.assertEqual(
            intent["modification"][0]["curvature_peak"]["change"]["action"],
            "increase",
        )
        self.assertEqual(
            intent["modification"][1]["centroid_shift"]["change"]["action"],
            "move_to_end",
        )
        self.assertFalse(self.service.need_division(intent))

    def test_s_shape_topology_forces_division_after_side_mapping(self):
        calls = [
            {
                "name": "change_curve_topology",
                "args": ChangeCurveTopologyArgs(
                    road_id="5",
                    gi=0,
                    target_shape="S-shape",
                    target_semantic="right_left",
                ).model_dump(),
            },
            {
                "name": "adjust_curve_curvature",
                "args": AdjustCurveCurvatureArgs(
                    road_id="5",
                    gi=0,
                    side="start_side",
                    value_type="curvature",
                    mode="relative",
                    action="increase",
                    magnitude_kind="qualitative",
                    value=None,
                    level="default",
                    unit="none",
                ).model_dump(),
            },
        ]

        intent = self.service.build_curve_intent_from_tool_calls(
            calls,
            {"current_shape": "C-shape", "semantic": "right"},
        )

        self.assertEqual(intent["target_shape"], "S-shape")
        self.assertEqual(intent["target_semantic"], "right_left")
        self.assertEqual(intent["semantic_change"], "right_left")
        self.assertTrue(self.service.need_division(intent))

    def test_c_shape_set_centroids_divide_only_for_opposite_directions(self):
        control_points = [
            [70.0, 120.0],
            [210.0, 360.0],
            [490.0, 360.0],
            [630.0, 120.0],
        ]

        def build_intent(end_value):
            calls = []
            for side, value in (("start_side", 20.0), ("end_side", end_value)):
                calls.append(
                    {
                        "name": "shift_curve_centroid",
                        "args": ShiftCurveCentroidArgs(
                            road_id="5",
                            gi=0,
                            side=side,
                            mode="absolute",
                            action="set",
                            magnitude_kind="numeric",
                            value=value,
                            level="none",
                            unit="percent",
                        ).model_dump(),
                    }
                )
            return self.service.build_curve_intent_from_tool_calls(
                calls,
                {"current_shape": "C-shape", "semantic": "right"},
            )

        self.assertTrue(self.service.need_division(build_intent(80.0), control_points))
        self.assertFalse(self.service.need_division(build_intent(20.0), control_points))

    def test_curve_metadata_rejects_a_different_extracted_road(self):
        with self.assertRaisesRegex(ValueError, "does not match extracted road id"):
            self.service._ensure_curve_operation_metadata(
                {
                    "operation": "curve",
                    "road_id": "road_6",
                    "gi": 0,
                },
                road_id="5",
                geometry_index=0,
            )


class PlannedCommandDispatchTest(unittest.TestCase):
    def test_width_tool_call_builds_complete_handler_command(self):
        dispatcher = AgentDispatcher(width_agent=WidthAgent.__new__(WidthAgent))
        dsl = {
            "operations": [
                {
                    "operation": "width",
                    "road_id": "road_3",
                    "lane_ids": ["lane_-1"],
                    "road_limit": 1,
                    "change": {
                        "mode": "relative",
                        "action": "increase",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 0.5,
                            "level": "none",
                            "unit": "m",
                        },
                    },
                }
            ]
        }
        road_info = {
            "road_id": "road_3",
            "lane_ids": [-1],
            "lane_avg_width": {"-1": 3.5},
        }
        commands = dispatcher.build_concrete_commands(
            {
                "agent": "width",
                "dsl": dsl,
                "road_info": road_info,
                "tool_calls": [
                    {
                        "name": "width_apply",
                        "args": {
                            "items": [
                                {
                                    "operation_index": 0,
                                    "formula": "abs(0.5)",
                                    "id": "3",
                                    "s": 1,
                                    "ms": 0,
                                    "sh": 1,
                                    "li": [-1],
                                }
                            ]
                        },
                    }
                ],
            }
        )

        self.assertEqual(
            commands,
            [["width", "id=3", "v=0.5", "s=1", "ms=0", "sh=1", "li=-1"]],
        )

    def test_direct_tool_payload_cannot_bypass_argument_validation(self):
        dispatcher = AgentDispatcher(width_agent=WidthAgent.__new__(WidthAgent))
        with self.assertRaises(ValueError):
            dispatcher.build_concrete_commands(
                {
                    "agent": "width",
                    "dsl": {
                        "operations": [
                            {
                                "operation": "width",
                                "road_id": "road_3",
                                "lane_ids": ["lane_-1"],
                                "road_limit": 1,
                                "change": {"action": "increase"},
                            }
                        ]
                    },
                    "road_info": {"road_id": "road_3", "lane_ids": [-1]},
                    "tool_calls": [
                        {
                            "name": "apply_width",
                            "args": {
                                "id": "3",
                                "v": 0.5,
                                "s": 1,
                                "ms": 0,
                                "sh": 1,
                            },
                        }
                    ],
                }
            )

    def test_executor_rejects_direct_ai_provided_width_value(self):
        dispatcher = AgentDispatcher(width_agent=WidthAgent.__new__(WidthAgent))
        with self.assertRaisesRegex(ValueError, "Unsupported width tool"):
            dispatcher.build_concrete_commands(
                {
                    "agent": "width",
                    "dsl": {
                        "operations": [
                            {
                                "operation": "width",
                                "road_id": "road_3",
                                "lane_ids": "none",
                                "road_limit": 1,
                                "change": {
                                    "mode": "absolute",
                                    "action": "set",
                                    "magnitude": {
                                        "kind": "numeric",
                                        "value": 9.0,
                                        "level": "none",
                                        "unit": "m",
                                    },
                                },
                            }
                        ]
                    },
                    "road_info": {
                        "road_id": "road_3",
                        "lane_ids": [-1, -2],
                        "lane_avg_width": {"-1": 3.0, "-2": 4.0},
                    },
                    "tool_calls": [
                        {
                            "name": "apply_width",
                            "args": {
                                "id": "3",
                                "v": 2.0,
                                "s": 1,
                                "ms": 0,
                                "sh": 1,
                                "li": [],
                            },
                        }
                    ],
                }
            )

    def test_dsl_only_payload_cannot_bypass_tool_planning(self):
        dispatcher = AgentDispatcher()
        with self.assertRaisesRegex(ValueError, "cannot be executed"):
            dispatcher.build_concrete_commands(
                {
                    "operations": [
                        {
                            "operation": "width",
                            "road_id": "road_3",
                            "lane_ids": ["lane_-1"],
                        }
                    ]
                }
            )

    def test_manager_payload_cannot_inject_a_raw_handler_command(self):
        dispatcher = AgentDispatcher()
        with self.assertRaisesRegex(ValueError, "type_name and description"):
            asyncio.run(
                dispatcher.dispatch_split_commands_async(
                    [
                        {
                            "type_name": "width",
                            "description": "change road 3",
                            "command": "width id=3 v=100 s=1 ms=99 sh=1",
                        }
                    ]
                )
            )

    def test_reference_output_is_not_passed_back_to_agent_planning(self):
        class FakeWidthAgent:
            def __init__(self):
                self.plan_inputs = None

            def get_road_id(self, description):
                raise AssertionError("Dispatcher must use Manager road_id.")

            def get_commands(self, description, road_info):
                return {"operations": [{"operation": "width", "road_id": "road_3"}]}

            def plan_execution(self, dsl, road_info):
                self.plan_inputs = (dsl, road_info)
                return [
                    {
                        "name": "width_apply",
                        "args": {
                            "items": [
                                {
                                    "operation_index": 0,
                                    "formula": "abs(0.5)",
                                    "id": "3",
                                    "s": 1,
                                    "ms": 0,
                                    "sh": 1,
                                    "li": [-1],
                                }
                            ]
                        },
                    }
                ]

            def resolve_execution_plan(self, dsl, road_info, calls):
                return [
                    {
                        "name": "apply_width",
                        "args": {
                            "id": "3",
                            "v": 0.5,
                            "s": 1,
                            "ms": 0,
                            "sh": 1,
                            "li": [-1],
                        },
                    }
                ]

        agent = FakeWidthAgent()
        dispatcher = AgentDispatcher(width_agent=agent)
        road_info = {
            "road_id": "road_3",
            "lane_ids": [-1],
            "lane_avg_width": {"-1": 3.5},
        }
        dispatcher._build_width_road_info = lambda road_id: road_info
        dispatcher._build_reference_commands = lambda dsl, source_command=None: [
            ["width", "id=3", "v=0.5", "s=1", "ms=0", "sh=1", "li=-1"]
        ]

        result = dispatcher._run_agent_for_command(
            {
                "road_id": "road_3",
                "type_name": "width",
                "description": "set road 3 lane width",
            }
        )

        self.assertEqual(agent.plan_inputs, (result["dsl"], road_info))
        self.assertNotIn("reference_command", agent.plan_inputs[0])
        self.assertEqual(result["reference_command"][0], "width id=3 v=0.5 s=1 ms=0 sh=1 li=-1")

    def test_dsl_road_id_must_match_the_separate_id_extraction(self):
        dispatcher = AgentDispatcher()
        with self.assertRaisesRegex(ValueError, "does not match extracted road id"):
            dispatcher._ensure_payload_road_id(
                {
                    "operations": [
                        {
                            "operation": "width",
                            "road_id": "road_4",
                        }
                    ]
                },
                "3",
            )

    def test_reference_failure_is_visible_but_does_not_block_tool_calls(self):
        dispatcher = AgentDispatcher()

        def fail_reference(*args, **kwargs):
            raise RuntimeError("legacy converter failed")

        dispatcher.reference_converter.convert = fail_reference
        reference_commands = dispatcher._build_reference_commands(
            {"operations": []}
        )
        result = dispatcher._agent_execution_result(
            "width", {"operations": []}, {}, [], reference_commands
        )

        self.assertEqual(
            result["reference_command"],
            ["REFERENCE_ERROR: RuntimeError: legacy converter failed"],
        )

    @patch("handler.agent_dispatcher.agent_trace.update_agent_step")
    def test_reference_commands_are_traced_as_separate_commands(self, update_step):
        dispatcher = AgentDispatcher()
        reference_commands = [
            ["width", "id=3", "v=0.5", "li=-1"],
            ["width", "id=3", "v=0.5", "li=-2"],
        ]

        dispatcher._agent_execution_result(
            "width", {"operations": []}, {}, [], reference_commands
        )

        self.assertEqual(
            update_step.call_args.kwargs["reference_command"], reference_commands
        )
        self.assertEqual(
            agent_trace._command_text(reference_commands),
            "width id=3 v=0.5 li=-1\nwidth id=3 v=0.5 li=-2",
        )

    @patch("handler.agent_dispatcher.agent_trace.update_agent_step")
    def test_resolved_tool_calls_are_traced_separately(self, update_step):
        dispatcher = AgentDispatcher()
        resolved = [
            {
                "name": "apply_width",
                "args": {"id": "3", "v": 0.5, "s": 1, "ms": 0, "sh": 1, "li": [-1]},
            }
        ]

        result = dispatcher._agent_execution_result(
            "width",
            {"operations": []},
            {},
            [{"name": "width_apply", "args": {"items": []}}],
            [],
            resolved_tool_calls=resolved,
        )

        self.assertEqual(
            update_step.call_args.kwargs["resolved_result_json"], resolved
        )
        self.assertEqual(result["resolved_tool_calls"], resolved)

    def test_all_agent_planning_finishes_before_sequential_execution(self):
        dispatcher = AgentDispatcher()
        barrier = threading.Barrier(2)
        planned = []
        executed = []
        lock = threading.Lock()

        def plan(command):
            barrier.wait(timeout=2)
            with lock:
                planned.append(command["type_name"])
            return {"agent": command["type_name"], "tool_calls": []}

        def execute(agent_result, source_command=None):
            self.assertCountEqual(planned, ["width", "slope"])
            executed.append(source_command["type_name"])
            return []

        dispatcher._run_agent_for_command = plan
        dispatcher.dispatch_agent_result = execute

        asyncio.run(
            dispatcher.dispatch_split_commands_async(
                [
                    {
                        "road_id": "road_3",
                        "type_name": "width",
                        "description": "road 3 width task",
                    },
                    {
                        "road_id": "road_4",
                        "type_name": "slope",
                        "description": "road 4 slope task",
                    },
                ]
            )
        )

        self.assertEqual(executed, ["width", "slope"])


class AgentToolPlanningTest(unittest.TestCase):
    def test_width_invalid_plan_fails_after_one_llm_request(self):
        agent = WidthAgent.__new__(WidthAgent)
        agent.execution_chain = object()
        response = SimpleNamespace(
            tool_calls=[
                {
                    "name": "apply_width",
                    "args": {
                        "id": "3",
                        "v": 0.5,
                        "s": 1,
                        "ms": 0,
                        "sh": 1,
                        "li": [-1],
                    },
                }
            ]
        )
        requests = []

        def invoke_llm(chain, request, **kwargs):
            requests.append(dict(request))
            return response

        dsl = {
            "operations": [
                {
                    "operation": "width",
                    "road_id": "road_3",
                    "lane_ids": ["lane_-1"],
                    "road_limit": 1,
                    "change": {"action": "increase"},
                }
            ]
        }
        road_info = {"road_id": "road_3", "lane_ids": [-1, -2]}

        with patch("llm.width_agent.agent_trace.invoke_llm", side_effect=invoke_llm):
            with self.assertRaisesRegex(ValueError, "Unsupported width tool"):
                agent.plan_execution(dsl, road_info)

        self.assertEqual(len(requests), 1)
        self.assertEqual(set(requests[0]), {"dsl", "roadInfo"})

    def test_width_total_target_divides_delta_across_all_lanes(self):
        agent = WidthAgent.__new__(WidthAgent)
        agent.execution_chain = object()
        response = SimpleNamespace(
            tool_calls=[
                {
                    "name": "width_apply",
                    "args": {
                        "items": [
                            {
                                "operation_index": 0,
                                "formula": "(9.0 - 7.0) / 2",
                                "id": "3",
                                "s": 1,
                                "ms": 0,
                                "sh": 1,
                                "li": [],
                            }
                        ]
                    },
                },
            ]
        )
        requests = []

        def invoke_llm(chain, request, **kwargs):
            requests.append(dict(request))
            return response

        dsl = {
            "operations": [
                {
                    "operation": "width",
                    "road_id": "road_3",
                    "lane_ids": "none",
                    "road_limit": 1,
                    "change": {
                        "mode": "absolute",
                        "action": "set",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 9.0,
                            "level": "none",
                            "unit": "m",
                        },
                    },
                }
            ]
        }
        road_info = {
            "road_id": "road_3",
            "lane_ids": [-1, -2],
            "lane_avg_width": {"-1": 3.0, "-2": 4.0},
        }

        with patch("llm.width_agent.agent_trace.invoke_llm", side_effect=invoke_llm):
            calls = agent.plan_execution(dsl, road_info)

        resolved = agent.resolve_execution_plan(dsl, road_info, calls)
        self.assertEqual(resolved[0]["args"]["v"], 1.0)
        self.assertEqual(len(requests), 1)

    def test_width_whole_road_qualitative_change_expands_with_shared_random(self):
        agent = WidthAgent.__new__(WidthAgent)
        dsl = {
            "operations": [
                {
                    "operation": "width",
                    "road_id": "road_3",
                    "lane_ids": "none",
                    "road_limit": "none",
                    "change": {
                        "mode": "relative",
                        "action": "decrease",
                        "magnitude": {
                            "kind": "qualitative",
                            "value": "none",
                            "level": "default",
                            "unit": "none",
                        },
                    },
                }
            ]
        }
        road_info = {
            "road_id": "road_3",
            "lane_ids": [1, -1],
            "lane_avg_width": {"1": 3.0, "-1": 3.5},
        }
        items = [
            {
                "operation_index": 0,
                "formula": "-(3.5 * random(0.25, 0.40))",
                "id": "3",
                "s": 1,
                "ms": 99,
                "sh": 1,
                "li": [-1],
            },
            {
                "operation_index": 0,
                "formula": "-(3.0 * random(0.25, 0.40))",
                "id": "3",
                "s": 1,
                "ms": 99,
                "sh": 1,
                "li": [1],
            },
        ]
        calls = [{"name": "width_apply", "args": {"items": items}}]

        with patch("llm.agent_tools.random.uniform", return_value=0.3) as uniform:
            resolved = agent.resolve_execution_plan(dsl, road_info, calls)

        self.assertEqual([call["args"]["li"] for call in resolved], [[1], [-1]])
        self.assertEqual([call["args"]["v"] for call in resolved], [-0.9, -1.05])
        uniform.assert_called_once_with(0.25, 0.4)
        with self.assertRaisesRegex(ValueError, "without omissions"):
            agent.resolve_execution_plan(
                dsl,
                road_info,
                [{"name": "width_apply", "args": {"items": items[:1]}}],
            )

    def test_width_whole_road_percent_change_expands_by_lane(self):
        agent = WidthAgent.__new__(WidthAgent)
        dsl = {
            "operations": [
                {
                    "operation": "width",
                    "road_id": "road_3",
                    "lane_ids": "none",
                    "road_limit": 1,
                    "change": {
                        "mode": "relative",
                        "action": "increase",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 20.0,
                            "level": "none",
                            "unit": "percent",
                        },
                    },
                }
            ]
        }
        road_info = {
            "road_id": "road_3",
            "lane_ids": [1, -1],
            "lane_avg_width": {"1": 3.0, "-1": 3.5},
        }
        calls = [
            {
                "name": "width_apply",
                "args": {
                    "items": [
                        {
                            "operation_index": 0,
                            "formula": "3.0 * 20.0 / 100",
                            "id": "3",
                            "s": 1,
                            "ms": 0,
                            "sh": 1,
                            "li": [1],
                        },
                        {
                            "operation_index": 0,
                            "formula": "3.5 * 20.0 / 100",
                            "id": "3",
                            "s": 1,
                            "ms": 0,
                            "sh": 1,
                            "li": [-1],
                        },
                    ]
                },
            }
        ]

        resolved = agent.resolve_execution_plan(dsl, road_info, calls)

        self.assertEqual([call["args"]["v"] for call in resolved], [0.6, 0.7])

    def test_width_whole_road_meter_change_remains_evenly_distributed(self):
        agent = WidthAgent.__new__(WidthAgent)
        dsl = {
            "operations": [
                {
                    "operation": "width",
                    "road_id": "road_3",
                    "lane_ids": "none",
                    "road_limit": 1,
                    "change": {
                        "mode": "relative",
                        "action": "increase",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 5.0,
                            "level": "none",
                            "unit": "m",
                        },
                    },
                }
            ]
        }
        road_info = {"road_id": "road_3", "lane_ids": [1, -1]}
        calls = [
            {
                "name": "width_apply",
                "args": {
                    "items": [
                        {
                            "operation_index": 0,
                            "formula": "5.0 / 2",
                            "id": "3",
                            "s": 1,
                            "ms": 0,
                            "sh": 1,
                            "li": [],
                        }
                    ]
                },
            }
        ]

        resolved = agent.resolve_execution_plan(dsl, road_info, calls)

        self.assertEqual(resolved[0]["args"]["li"], [])
        self.assertEqual(resolved[0]["args"]["v"], 2.5)

    def test_width_batch_plan_covers_multiple_operations_once(self):
        agent = WidthAgent.__new__(WidthAgent)
        dsl = {
            "operations": [
                {
                    "operation": "width",
                    "road_id": "road_8",
                    "lane_ids": ["lane_1"],
                    "road_limit": 1,
                    "change": {
                        "mode": "relative",
                        "action": "increase",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 0.5,
                            "level": "none",
                            "unit": "m",
                        },
                    },
                },
                {
                    "operation": "width",
                    "road_id": "road_8",
                    "lane_ids": ["lane_-1"],
                    "road_limit": 2,
                    "change": {
                        "mode": "relative",
                        "action": "decrease",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 10.0,
                            "level": "none",
                            "unit": "percent",
                        },
                    },
                },
            ]
        }
        road_info = {
            "road_id": "road_8",
            "lane_ids": [1, -1],
            "lane_avg_width": {"1": 3.0, "-1": 3.5},
        }
        items = [
            {
                "operation_index": 0,
                "formula": "0.5",
                "id": "8",
                "s": 1,
                "ms": 0,
                "sh": 1,
                "li": [1],
            },
            {
                "operation_index": 1,
                "formula": "-(3.5 * 10.0 / 100)",
                "id": "8",
                "s": 1,
                "ms": 1,
                "sh": 1,
                "li": [-1],
            },
        ]
        calls = [{"name": "width_apply", "args": {"items": items}}]

        resolved = agent.resolve_execution_plan(dsl, road_info, calls)

        self.assertEqual([call["args"]["li"] for call in resolved], [[1], [-1]])
        self.assertAlmostEqual(resolved[0]["args"]["v"], 0.5)
        self.assertAlmostEqual(resolved[1]["args"]["v"], -0.35)
        with self.assertRaisesRegex(ValueError, "without omissions"):
            agent.resolve_execution_plan(
                dsl,
                road_info,
                [{"name": "width_apply", "args": {"items": items[:1]}}],
            )

    def test_width_no_op_dsl_skips_tool_planning(self):
        agent = WidthAgent.__new__(WidthAgent)
        agent.execution_chain = object()
        dsl = {
            "operations": [
                {
                    "operation": "width",
                    "road_id": "road_8",
                    "change": {"action": "none"},
                }
            ]
        }

        with patch("llm.width_agent.agent_trace.invoke_llm") as invoke_llm:
            calls = agent.plan_execution(dsl, {"road_id": "road_8"})

        self.assertEqual(calls, [])
        self.assertEqual(agent.resolve_execution_plan(dsl, {"road_id": "road_8"}, []), [])
        self.assertEqual(invoke_llm.call_count, 0)

    def test_width_rejects_malformed_batch_plans_and_targets(self):
        agent = WidthAgent.__new__(WidthAgent)
        dsl = {
            "operations": [
                {
                    "operation": "width",
                    "road_id": "road_3",
                    "lane_ids": ["lane_-1"],
                    "road_limit": 1,
                    "change": {
                        "mode": "relative",
                        "action": "increase",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 0.5,
                            "level": "none",
                            "unit": "m",
                        },
                    },
                }
            ]
        }
        road_info = {
            "road_id": "road_3",
            "lane_ids": [-1],
            "lane_avg_width": {"-1": 3.5},
        }
        item = {
            "operation_index": 0,
            "formula": "abs(0.5)",
            "id": "3",
            "s": 1,
            "ms": 0,
            "sh": 1,
            "li": [-1],
        }
        batch = {
            "name": "width_apply",
            "args": {
                "items": [item],
            },
        }

        bad_cases = [
            (
                "exactly one",
                [batch, batch],
            ),
            (
                "variables",
                [
                    dict(
                        batch,
                        args={"items": [dict(item, formula="magnitude_value")]},
                    )
                ],
            ),
            (
                "cover exactly",
                [
                    dict(
                        batch,
                        args={"items": [dict(item, li=[-2])]},
                    )
                ],
            ),
        ]
        for message, calls in bad_cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    agent.resolve_execution_plan(dsl, road_info, calls)

    def test_slope_grade_percent_is_calculated_locally_in_metres(self):
        agent = SlopeAgent.__new__(SlopeAgent)
        agent.execution_chain = object()
        dsl = {
            "operations": [
                {
                    "operation": "slope",
                    "road_id": "road_5",
                    "position": "end",
                    "road_limit": 1,
                    "change": {
                        "attribute": "grade",
                        "mode": "relative",
                        "action": "increase",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 20.0,
                            "level": "none",
                            "unit": "percent",
                        },
                    },
                }
            ]
        }
        road_info = {
            "road_id": "road_5",
            "length": 100.0,
            "start_elevation": 10.0,
            "end_elevation": 5.0,
        }
        response = SimpleNamespace(
            tool_calls=[
                {
                    "name": "slope_apply",
                    "args": {
                        "items": [
                            {
                                "operation_index": 0,
                                "formula": (
                                    "100.0 * tan(radians(-2.8624052261 * "
                                    "(1 + 20.0 / 100))) - (5.0 - 10.0)"
                                ),
                                "id": "5",
                                "m": "add",
                                "mv": 1,
                                "ms": 0,
                                "sh": 1,
                            }
                        ]
                    },
                },
            ]
        )

        with patch(
            "llm.slope_agent.agent_trace.invoke_llm", return_value=response
        ) as invoke_llm:
            raw_calls = agent.plan_execution(dsl, road_info)
        resolved = agent.resolve_execution_plan(dsl, road_info, raw_calls)

        self.assertEqual(invoke_llm.call_count, 1)
        self.assertEqual(resolved[0]["args"]["v"], -1.002)

    def test_slope_absolute_both_resolves_each_endpoint_separately(self):
        agent = SlopeAgent.__new__(SlopeAgent)
        dsl = {
            "operations": [
                {
                    "operation": "slope",
                    "road_id": "road_5",
                    "position": "both",
                    "road_limit": 1,
                    "change": {
                        "attribute": "elevation_delta",
                        "mode": "absolute",
                        "action": "set",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 8.0,
                            "level": "none",
                            "unit": "m",
                        },
                    },
                }
            ]
        }
        road_info = {
            "road_id": "road_5",
            "length": 50.0,
            "start_elevation": 5.0,
            "end_elevation": 2.0,
        }
        calls = [
            {
                "name": "slope_apply",
                "args": {
                    "items": [
                        {
                            "operation_index": 0,
                            "formula": "8.0 - 5.0",
                            "id": "5",
                            "m": "add",
                            "mv": 0,
                            "ms": 0,
                            "sh": 1,
                        },
                        {
                            "operation_index": 0,
                            "formula": "8.0 - 2.0",
                            "id": "5",
                            "m": "add",
                            "mv": 1,
                            "ms": 0,
                            "sh": 1,
                        },
                    ]
                },
            }
        ]

        resolved = agent.resolve_execution_plan(dsl, road_info, calls)

        self.assertEqual(
            [(call["args"]["mv"], call["args"]["v"]) for call in resolved],
            [(0, 3.0), (1, 6.0)],
        )

    def test_slope_batch_plan_covers_multiple_operations_once(self):
        agent = SlopeAgent.__new__(SlopeAgent)
        dsl = {
            "operations": [
                {
                    "operation": "slope",
                    "road_id": "road_8",
                    "position": "start",
                    "road_limit": "none",
                    "change": {
                        "attribute": "elevation_delta",
                        "mode": "relative",
                        "action": "decrease",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 20.0,
                            "level": "none",
                            "unit": "percent",
                        },
                    },
                },
                {
                    "operation": "slope",
                    "road_id": "road_8",
                    "position": "end",
                    "road_limit": 2,
                    "change": {
                        "attribute": "elevation_delta",
                        "mode": "relative",
                        "action": "increase",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 1.5,
                            "level": "none",
                            "unit": "m",
                        },
                    },
                },
            ]
        }
        road_info = {
            "road_id": "road_8",
            "length": 80.0,
            "start_elevation": 10.0,
            "end_elevation": 5.0,
        }
        items = [
            {
                "operation_index": 0,
                "formula": "-(abs(5.0 - 10.0) * 20.0 / 100)",
                "id": "8",
                "m": "add",
                "mv": 0,
                "ms": 99,
                "sh": 1,
            },
            {
                "operation_index": 1,
                "formula": "1.5",
                "id": "8",
                "m": "add",
                "mv": 1,
                "ms": 1,
                "sh": 1,
            },
        ]
        calls = [{"name": "slope_apply", "args": {"items": items}}]

        resolved = agent.resolve_execution_plan(dsl, road_info, calls)

        self.assertEqual(
            resolved,
            [
                {
                    "name": "apply_slope",
                    "args": {
                        "id": "8",
                        "v": -1.0,
                        "m": "add",
                        "mv": 0,
                        "ms": 99,
                        "sh": 1,
                    },
                },
                {
                    "name": "apply_slope",
                    "args": {
                        "id": "8",
                        "v": 1.5,
                        "m": "add",
                        "mv": 1,
                        "ms": 1,
                        "sh": 1,
                    },
                },
            ],
        )
        with self.assertRaisesRegex(ValueError, "without omissions"):
            agent.resolve_execution_plan(
                dsl,
                road_info,
                [{"name": "slope_apply", "args": {"items": items[:1]}}],
            )

    def test_slope_rejects_wrong_apply_endpoint(self):
        agent = SlopeAgent.__new__(SlopeAgent)
        dsl = {
            "operations": [
                {
                    "operation": "slope",
                    "road_id": "road_5",
                    "position": "end",
                    "road_limit": 1,
                    "change": {
                        "attribute": "elevation_delta",
                        "mode": "relative",
                        "action": "increase",
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
        calls = [
            {
                "name": "slope_apply",
                "args": {
                    "items": [
                        {
                            "operation_index": 0,
                            "formula": "abs(1.0)",
                            "id": "5",
                            "m": "add",
                            "mv": 0,
                            "ms": 0,
                            "sh": 1,
                        }
                    ]
                },
            },
        ]
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            agent.resolve_execution_plan(
                dsl,
                {
                    "road_id": "road_5",
                    "length": 50.0,
                    "start_elevation": 5.0,
                    "end_elevation": 2.0,
                },
                calls,
            )

    def test_slope_no_op_dsl_accepts_an_empty_plan(self):
        agent = SlopeAgent.__new__(SlopeAgent)
        agent.execution_chain = object()
        dsl = {
            "operations": [
                {
                    "operation": "slope",
                    "road_id": "road_5",
                    "change": {"action": "none"},
                }
            ]
        }
        with patch(
            "llm.slope_agent.agent_trace.invoke_llm"
        ) as invoke_llm:
            calls = agent.plan_execution(dsl, {"road_id": "road_5"})

        self.assertEqual(calls, [])
        self.assertEqual(agent.resolve_execution_plan(dsl, {"road_id": "road_5"}, []), [])
        self.assertEqual(invoke_llm.call_count, 0)

    def test_slope_rejects_duplicate_position_calls(self):
        agent = SlopeAgent.__new__(SlopeAgent)
        dsl = {
            "operations": [
                {
                    "operation": "slope",
                    "road_id": "road_5",
                    "position": "end",
                    "road_limit": 1,
                    "change": {
                        "attribute": "elevation_delta",
                        "mode": "relative",
                        "action": "increase",
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
        call = {
            "name": "apply_slope",
            "args": {"id": "5", "v": 1.0, "m": "add", "mv": 1, "ms": 0, "sh": 1},
        }

        with self.assertRaisesRegex(ValueError, "without duplicates"):
            agent._validate_tool_targets(dsl, {"road_id": "road_5"}, [call, call])

    def test_slope_both_absolute_elevation_requires_two_endpoint_calls(self):
        agent = SlopeAgent.__new__(SlopeAgent)
        dsl = {
            "operations": [
                {
                    "operation": "slope",
                    "road_id": "road_5",
                    "position": "both",
                    "road_limit": 1,
                    "change": {
                        "attribute": "elevation_delta",
                        "mode": "absolute",
                        "action": "set",
                        "magnitude": {
                            "kind": "numeric",
                            "value": 8.0,
                            "level": "none",
                            "unit": "m",
                        },
                    },
                }
            ]
        }
        calls = [
            {
                "name": "apply_slope",
                "args": {"id": "5", "v": 3.0, "m": "add", "mv": mv, "ms": 0, "sh": 1},
            }
            for mv in (0, 1)
        ]

        agent._validate_tool_targets(dsl, {"road_id": "road_5"}, calls)
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            agent._validate_tool_targets(
                dsl,
                {"road_id": "road_5"},
                [dict(calls[0], args=dict(calls[0]["args"], mv=2))],
            )

    def test_curve_invalid_plan_fails_after_one_llm_request(self):
        agent = CurveAgent.__new__(CurveAgent)
        agent.execution_chain = object()
        invalid_args = {
            "road_id": "6",
            "gi": 0,
            "side": "start_side",
            "value_type": "curvature",
            "mode": "relative",
            "action": "increase",
            "magnitude_kind": "qualitative",
            "value": None,
            "level": "slight",
            "unit": "none",
        }
        response = SimpleNamespace(
            tool_calls=[
                {"name": "adjust_curve_curvature", "args": invalid_args}
            ]
        )
        requests = []

        def invoke_llm(chain, request, **kwargs):
            requests.append(dict(request))
            return response

        dsl = {
            "operations": [
                {
                    "operation": "curve",
                    "road_id": "road_5",
                    "gi": 0,
                    "shape": {"target": "keep"},
                    "semantic": {"semantic_change": False},
                    "modification": [
                        {
                            "centroid_shift": {"change": {"action": "none"}},
                            "curvature_peak": {
                                "value_type": "curvature",
                                "change": {
                                    "mode": "relative",
                                    "action": "increase",
                                    "magnitude": {
                                        "kind": "qualitative",
                                        "value": "none",
                                        "level": "slight",
                                        "unit": "none",
                                    },
                                },
                            },
                        },
                        {
                            "centroid_shift": {"change": {"action": "none"}},
                            "curvature_peak": {"change": {"action": "none"}},
                        },
                    ],
                }
            ]
        }

        with patch("llm.curve_agent.agent_trace.invoke_llm", side_effect=invoke_llm):
            with self.assertRaisesRegex(ValueError, "does not match"):
                agent.plan_execution(dsl, {"road_id": "road_5"})

        self.assertEqual(len(requests), 1)
        self.assertEqual(set(requests[0]), {"dsl", "roadInfo"})

    def test_curve_rejects_parameters_that_do_not_match_dsl(self):
        agent = CurveAgent.__new__(CurveAgent)
        dsl = {
            "operations": [
                {
                    "operation": "curve",
                    "road_id": "road_5",
                    "gi": 0,
                    "shape": {"target": "keep"},
                    "semantic": {"semantic_change": False, "target": "keep"},
                    "modification": [
                        {
                            "centroid_shift": {"change": {"action": "none"}},
                            "curvature_peak": {
                                "value_type": "curvature",
                                "change": {
                                    "mode": "relative",
                                    "action": "increase",
                                    "magnitude": {
                                        "kind": "qualitative",
                                        "value": "none",
                                        "level": "slight",
                                        "unit": "none",
                                    },
                                },
                            },
                        },
                        {
                            "centroid_shift": {"change": {"action": "none"}},
                            "curvature_peak": {"change": {"action": "none"}},
                        },
                    ],
                }
            ]
        }
        calls = [
            {
                "name": "adjust_curve_curvature",
                "args": {
                    "road_id": "5",
                    "gi": 0,
                    "side": "start_side",
                    "value_type": "radius",
                    "mode": "absolute",
                    "action": "decrease",
                    "magnitude_kind": "numeric",
                    "value": 100.0,
                    "level": "none",
                    "unit": "m",
                },
            }
        ]

        with self.assertRaisesRegex(ValueError, "parameters do not match"):
            agent._validate_tool_targets(dsl, {"road_id": "road_5"}, calls)

    def test_curve_shape_only_change_preserves_start_direction(self):
        agent = CurveAgent.__new__(CurveAgent)
        dsl = {
            "operations": [
                {
                    "operation": "curve",
                    "road_id": "road_5",
                    "gi": 0,
                    "shape": {"target": "S-shape"},
                    "semantic": {"semantic_change": False, "target": "keep"},
                    "modification": [],
                }
            ]
        }
        wrong_call = {
            "name": "change_curve_topology",
            "args": {
                "road_id": "5",
                "gi": 0,
                "target_shape": "S-shape",
                "target_semantic": "left_right",
            },
        }
        road_info = {
            "road_id": "road_5",
            "current_shape": "C-shape",
            "semantic": "right",
        }

        with self.assertRaisesRegex(ValueError, "target_semantic"):
            agent._validate_tool_targets(dsl, road_info, [wrong_call])
        correct_call = dict(
            wrong_call,
            args=dict(wrong_call["args"], target_semantic="right_left"),
        )
        agent._validate_tool_targets(dsl, road_info, [correct_call])


class DslReferenceConverterTest(unittest.TestCase):
    def setUp(self):
        class FakeCurveOptimizer:
            def __init__(self):
                self.calls = []

            def select_geometry_index(self, road_id, user_request):
                del road_id, user_request
                return 0

            def build_curve_tool_context(self, road_id, geometry_index):
                return {
                    "road_id": f"road_{road_id}",
                    "gi": geometry_index,
                    "current_shape": "C-shape",
                    "semantic": "left",
                }

            def build_curve_command_from_intent(
                self, road_id, intent, user_request=None, record_metric=True
            ):
                self.calls.append(
                    (road_id, intent, user_request, record_metric)
                )
                return ["curve", f"id={road_id}", "gi=0", "h0=1"]

        self.curve_optimizer = FakeCurveOptimizer()
        self.converter = DslReferenceConverter(
            curve_optimizer=self.curve_optimizer,
            width_road_info_getter=lambda road_id: {
                "road_id": f"road_{road_id}",
                "lane_ids": [-1, -2],
            },
            lane_avg_width_getter=lambda road, lane_id: {
                "-1": 3.5,
                "-2": 3.0,
            }[lane_id],
            slope_road_info_getter=lambda road_id: {
                "road_id": f"road_{road_id}",
                "length": 100.0,
                "start_elevation": 2.0,
                "end_elevation": 5.0,
                "grade_degree": 0.0,
            },
        )

    def test_width_reference_conversion_remains_in_reference_module(self):
        with patch(
            "handler.dsl_reference_converter.agent_road_context.use_manual_road_context",
            return_value=True,
        ), patch(
            "handler.dsl_reference_converter.agent_road_context.get_manual_width_reference_value",
            return_value=3.5,
        ):
            commands = self.converter.convert(
                {
                    "operations": [
                        {
                            "operation": "width",
                            "road_id": "road_3",
                            "lane_ids": ["lane_-1"],
                            "road_limit": 1,
                            "change": {
                                "action": "increase",
                                "magnitude": {
                                    "kind": "numeric",
                                    "value": 0.5,
                                    "unit": "m",
                                },
                            },
                        }
                    ]
                }
            )

        self.assertEqual(
            commands,
            [["width", "id=3", "v=0.5", "s=1", "ms=0", "sh=1", "li=-1"]],
        )

    def test_width_reference_total_target_divides_delta_by_lane_count(self):
        with patch(
            "handler.dsl_reference_converter.agent_road_context.use_manual_road_context",
            return_value=True,
        ), patch(
            "handler.dsl_reference_converter.agent_road_context.get_manual_width_reference_value",
            return_value=6.5,
        ):
            commands = self.converter.convert(
                {
                    "operations": [
                        {
                            "operation": "width",
                            "road_id": "road_3",
                            "lane_ids": "none",
                            "road_limit": 1,
                            "change": {
                                "mode": "absolute",
                                "action": "set",
                                "magnitude": {
                                    "kind": "numeric",
                                    "value": 8.0,
                                    "level": "none",
                                    "unit": "m",
                                },
                            },
                        }
                    ]
                }
            )

        self.assertEqual(
            commands,
            [["width", "id=3", "v=0.75", "s=1", "ms=0", "sh=1"]],
        )

    def test_width_reference_whole_road_qualitative_expands_by_lane(self):
        lane_widths = {-1: 3.5, -2: 3.0}

        def reference_value(road_id, lane_ids):
            del road_id
            return sum(lane_widths[int(lane_id)] for lane_id in lane_ids)

        with patch(
            "handler.dsl_reference_converter.agent_road_context.use_manual_road_context",
            return_value=True,
        ), patch(
            "handler.dsl_reference_converter.agent_road_context.get_manual_width_reference_value",
            side_effect=reference_value,
        ):
            commands = self.converter.convert(
                {
                    "operations": [
                        {
                            "operation": "width",
                            "road_id": "road_3",
                            "lane_ids": "none",
                            "road_limit": 1,
                            "change": {
                                "mode": "relative",
                                "action": "decrease",
                                "magnitude": {
                                    "kind": "qualitative",
                                    "value": "none",
                                    "level": "default",
                                    "unit": "none",
                                },
                            },
                        }
                    ]
                }
            )

        self.assertEqual(
            commands,
            [
                ["width", "id=3", "v=-1.1375", "s=1", "ms=0", "sh=1", "li=-1"],
                ["width", "id=3", "v=-0.975", "s=1", "ms=0", "sh=1", "li=-2"],
            ],
        )

    def test_slope_reference_conversion_remains_in_reference_module(self):
        commands = self.converter.convert(
            {
                "operations": [
                    {
                        "operation": "slope",
                        "road_id": "road_5",
                        "position": "end",
                        "road_limit": 1,
                        "change": {
                            "attribute": "elevation_delta",
                            "mode": "absolute",
                            "action": "set",
                            "magnitude": {
                                "kind": "numeric",
                                "value": 8.0,
                                "unit": "m",
                            },
                        },
                    }
                ]
            }
        )

        self.assertEqual(
            commands,
            [["slope", "id=5", "v=3", "m=add", "mv=1", "ms=0", "sh=1"]],
        )

    def test_slope_reference_elevation_percent_uses_height_delta_with_flat_fallback(self):
        operation = {
            "operation": "slope",
            "road_id": "road_21",
            "position": "end",
            "road_limit": "none",
            "change": {
                "attribute": "elevation_delta",
                "mode": "relative",
                "action": "increase",
                "magnitude": {
                    "kind": "numeric",
                    "value": 12,
                    "unit": "percent",
                },
            },
        }

        cases = [
            ((-5.0, 5.0), "1.2"),
            ((5.0, 5.0), "0.6"),
            ((0.0, 0.0), None),
        ]
        for (start_elevation, end_elevation), expected_value in cases:
            with self.subTest(
                start_elevation=start_elevation,
                end_elevation=end_elevation,
            ):
                self.converter.slope_road_info_getter = lambda road_id, start=start_elevation, end=end_elevation: {
                    "road_id": f"road_{road_id}",
                    "length": 50.0,
                    "start_elevation": start,
                    "end_elevation": end,
                    "grade_degree": 0.0,
                }
                commands = self.converter.convert({"operations": [operation]})
                if expected_value is None:
                    self.assertEqual(commands, [])
                else:
                    self.assertEqual(commands[0][2], f"v={expected_value}")

    def test_slope_reference_start_elevation_percent_uses_height_delta(self):
        self.converter.slope_road_info_getter = lambda road_id: {
            "road_id": f"road_{road_id}",
            "length": 50.0,
            "start_elevation": 5.0,
            "end_elevation": -5.0,
            "grade_degree": -11.3099,
        }
        commands = self.converter.convert(
            {
                "operations": [
                    {
                        "operation": "slope",
                        "road_id": "road_21",
                        "position": "start",
                        "road_limit": "none",
                        "change": {
                            "attribute": "elevation_delta",
                            "mode": "relative",
                            "action": "increase",
                            "magnitude": {
                                "kind": "numeric",
                                "value": 9,
                                "unit": "percent",
                            },
                        },
                    }
                ]
            }
        )

        self.assertEqual(
            commands,
            [["slope", "id=21", "v=0.9", "m=add", "mv=0", "ms=99", "sh=1"]],
        )

    def test_slope_reference_relative_set_percent_keeps_endpoint_base(self):
        self.converter.slope_road_info_getter = lambda road_id: {
            "road_id": f"road_{road_id}",
            "length": 50.0,
            "start_elevation": -5.0,
            "end_elevation": 5.0,
            "grade_degree": 0.0,
        }
        commands = self.converter.convert(
            {
                "operations": [
                    {
                        "operation": "slope",
                        "road_id": "road_21",
                        "position": "end",
                        "road_limit": "none",
                        "change": {
                            "attribute": "elevation_delta",
                            "mode": "relative",
                            "action": "set",
                            "magnitude": {
                                "kind": "numeric",
                                "value": 12,
                                "unit": "percent",
                            },
                        },
                    }
                ]
            }
        )

        self.assertEqual(commands[0][2], "v=-4.4")

    def test_curve_reference_disables_metric_recording(self):
        operation = {
            "operation": "curve",
            "road_id": "road_7",
            "gi": 0,
        }
        commands = self.converter.convert(
            {"operations": [operation]},
            source_command={"description": "adjust road 7"},
        )

        self.assertEqual(commands, [["curve", "id=7", "gi=0", "h0=1"]])
        road_id, intent, user_request, record_metric = self.curve_optimizer.calls[0]
        self.assertEqual(road_id, "7")
        self.assertEqual(intent["target_shape"], "C-shape")
        self.assertEqual(intent["semantic"], "left")
        self.assertEqual(user_request, "adjust road 7")
        self.assertFalse(record_metric)

    def test_curve_dsl_normalization_stays_in_reference_converter(self):
        operation = {
            "operation": "curve",
            "road_id": "road_7",
            "gi": 0,
            "shape": {"target": "S-shape"},
            "semantic": {
                "semantic_change": True,
                "target": "left_right",
            },
            "modification": [
                {
                    "centroid_shift": {
                        "change": {
                            "action": "move_to_start",
                            "mode": "relative",
                            "magnitude": {
                                "kind": "qualitative",
                                "level": "slight",
                            },
                        }
                    },
                    "curvature_peak": {
                        "value_type": "radius",
                        "change": {
                            "action": "set",
                            "mode": "absolute",
                            "magnitude": {
                                "kind": "numeric",
                                "value": 25,
                                "unit": "radius",
                            },
                        },
                    },
                },
                {},
            ],
        }

        self.converter.convert({"operations": [operation]})

        intent = self.curve_optimizer.calls[0][1]
        self.assertEqual(intent["target_shape"], "S-shape")
        self.assertEqual(intent["target_semantic"], "left_right")
        self.assertEqual(intent["semantic_change"], "left_right")
        self.assertEqual(
            intent["modification"][0]["centroid_shift"]["change"]["action"],
            "move_to_start",
        )
        curvature = intent["modification"][0]["curvature_peak"]
        self.assertEqual(curvature["value_type"], "radius")
        self.assertEqual(curvature["change"]["magnitude"]["value"], 25.0)
        self.assertEqual(curvature["change"]["magnitude"]["unit"], "m")


class AgentTraceToolCallTest(unittest.TestCase):
    def test_projected_width_metric_treats_v_as_per_lane_delta(self):
        width_info = {
            "road_id": "road_21",
            "lane_ids": [1, -1],
            "lane_avg_width": {"1": 3.4, "-1": 3.5},
        }
        parsed = {
            "raw": "width id=21 v=-0.5 s=1 ms=99 sh=1",
            "params": {"id": "21", "v": "-0.5"},
        }

        with patch("utils.agent_trace._get_width_road_info", return_value=width_info):
            before, after, delta = agent_trace._project_width_metric("21", parsed)

        self.assertEqual(before["target_avg_width_total"], 6.9)
        self.assertEqual(after["lane_avg_width"], {"1": 2.9, "-1": 3.0})
        self.assertEqual(after["target_avg_width_total"], 5.9)
        self.assertEqual(delta["delta_total_width"], -1.0)
        self.assertEqual(delta["delta_per_target_lane"], {"1": -0.5, "-1": -0.5})

    def test_tool_call_trace_removes_provider_call_ids(self):
        class Response:
            tool_calls = [
                {
                    "id": "call_provider_specific",
                    "name": "slope_apply",
                    "args": {"items": []},
                    "type": "tool_call",
                }
            ]

        self.assertEqual(
            agent_trace.tool_calls_for_trace(Response()),
            [{"name": "slope_apply", "args": {"items": []}}],
        )

    def test_tool_calls_and_reference_commands_are_written_to_jsonl_only(self):
        previous_run_id = agent_trace._RUN_ID
        try:
            with tempfile.TemporaryDirectory() as directory:
                trace_path = Path(directory) / "tool_trace.jsonl"
                with patch.dict(
                    os.environ, {"AGENT_TRACE_PATH": str(trace_path)}, clear=False
                ):
                    agent_trace.set_run_id("tool-call-test")
                    with agent_trace.trace_context(
                        task_id="1", step_id="1", agent="width", operation="width"
                    ):
                        agent_trace.record_agent_step(
                            agent="width",
                            operation="width",
                            tool_call_json=[
                                {
                                    "name": "width_apply",
                                    "args": {
                                        "items": [
                                            {
                                                "operation_index": 0,
                                                "formula": "abs(0.5)",
                                                "id": "3",
                                                "s": 1,
                                                "ms": 0,
                                                "sh": 1,
                                                "li": [-1],
                                            }
                                        ]
                                    },
                                }
                            ],
                            resolved_result_json=[
                                {
                                    "name": "apply_width",
                                    "args": {
                                        "id": "3",
                                        "v": 0.5,
                                        "s": 1,
                                        "ms": 0,
                                        "sh": 1,
                                        "li": [-1],
                                    },
                                }
                            ],
                            internal_command=[
                                [
                                    "width",
                                    "id=3",
                                    "v=0.5",
                                    "s=1",
                                    "ms=0",
                                    "sh=1",
                                    "li=-1",
                                ]
                            ],
                            reference_command=[
                                ["width", "id=3", "v=0.5", "li=-1"]
                            ],
                        )

                events = [
                    json.loads(line)
                    for line in trace_path.read_text(encoding="utf-8").splitlines()
                ]
                row = events[-1]["row"]
                traced_tool_calls = json.loads(row["tool_call_json"])
                tool_names = [call["name"] for call in traced_tool_calls]
                self.assertEqual(tool_names, ["width_apply"])
                self.assertEqual(
                    traced_tool_calls[0]["args"]["items"][0]["formula"],
                    "abs(0.5)",
                )
                self.assertEqual(
                    json.loads(row["resolved_result_json"])[0]["name"],
                    "apply_width",
                )
                self.assertIn("v=0.5", row["internal_command"])
                self.assertEqual(
                    row["reference_command"], "width id=3 v=0.5 li=-1"
                )

                self.assertFalse(trace_path.with_suffix(".xlsx").exists())
        finally:
            agent_trace._RUN_ID = previous_run_id


if __name__ == "__main__":
    unittest.main()
