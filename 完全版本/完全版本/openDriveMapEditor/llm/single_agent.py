import json

from dotenv import load_dotenv
from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
)
from langchain_core.output_parsers import JsonOutputParser

from llm.agent_tools import (
    CURVE_DSL_PROMPT,
    CURVE_TOOL_PLAN_PROMPT,
    escape_langchain_template_text,
    get_model,
    SINGLE_DSL_TOOL_WRAPPER_PROMPT,
    SLOPE_DSL_PROMPT,
    SLOPE_TOOL_PLAN_PROMPT,
    WIDTH_DSL_PROMPT,
    WIDTH_TOOL_PLAN_PROMPT,
)
from llm.curve_agent import CurveAgent, normalize_curve_tool_calls
from llm.dsl_format_validator import validate_dsl_format
from llm.slope_agent import SlopeAgent, normalize_slope_plan_tool_calls
from llm.width_agent import WidthAgent, normalize_width_plan_tool_calls
from utils import agent_trace

load_dotenv(override=True)


class SingleAgent:
    def __init__(self, model=""):
        self.model = get_model(model)
        self.chain = self.init_chain()
        self.width_validator = WidthAgent.__new__(WidthAgent)
        self.slope_validator = SlopeAgent.__new__(SlopeAgent)
        self.curve_validator = CurveAgent.__new__(CurveAgent)

    def init_chain(self):
        parser = JsonOutputParser()
        combined_prompt = "\n\n".join(
            [
                "# Width DSL extraction prompt",
                WIDTH_DSL_PROMPT,
                "# Slope DSL extraction prompt",
                SLOPE_DSL_PROMPT,
                "# Curve DSL extraction prompt",
                CURVE_DSL_PROMPT,
                "# Width tool planning prompt",
                WIDTH_TOOL_PLAN_PROMPT,
                "# Slope tool planning prompt",
                SLOPE_TOOL_PLAN_PROMPT,
                "# Curve tool planning prompt",
                CURVE_TOOL_PLAN_PROMPT,
                SINGLE_DSL_TOOL_WRAPPER_PROMPT,
            ]
        )

        system_prompt = SystemMessagePromptTemplate.from_template(
            escape_langchain_template_text(combined_prompt)
            + "\nPlease ensure to directly output JSON data, without including the ```json markers, and without any prefixes or suffixes."
        )
        human_prompt = HumanMessagePromptTemplate.from_template(
            "user_instruction: {userInput}\n"
            "road_data: {roadData}\n"
            "Return all DSL operations and tool plans in the unified JSON structure."
        )
        chat_prompt = ChatPromptTemplate.from_messages([system_prompt, human_prompt])
        return chat_prompt | self.model | parser

    def _build_extract_request(self, user_input, road_context):
        road_context = road_context or {}
        return {
            "userInput": user_input,
            "roadData": json.dumps(road_context, ensure_ascii=False),
        }

    def get_commands(self, user_input, road_context=None):
        road_context = road_context or {}
        try:
            raw_result = agent_trace.invoke_llm(
                self.chain,
                self._build_extract_request(user_input, road_context),
                request_kind="single_dsl_tool_plan",
                attempt=1,
                agent="single",
                operation="single",
            )
            result = self._prepare_result(raw_result, road_context)
        except Exception as exc:
            agent_trace.record_agent_step(
                agent="single",
                operation="single",
                input_text=user_input,
                road_context_json=road_context,
                status="error",
                error=exc,
            )
            raise
        agent_trace.record_agent_step(
            agent="single",
            operation="single",
            input_text=user_input,
            road_context_json=road_context,
            llm_output_json={"dsl": result["dsl"], "plans": result["plans"]},
            tool_call_json=result["plans"],
            status="ok",
        )
        return result

    def _prepare_result(self, result, road_context):
        if not isinstance(result, dict) or set(result) != {"dsl", "plans"}:
            raise ValueError("Single Agent output must contain only dsl and plans.")
        dsl = result["dsl"]
        plans = result["plans"]
        if not isinstance(dsl, dict) or set(dsl) != {"operations"}:
            raise ValueError("Single Agent dsl must contain only operations.")
        operations = dsl["operations"]
        if not isinstance(operations, list):
            raise ValueError("Single Agent dsl.operations must be a list.")
        if not isinstance(plans, list):
            raise ValueError("Single Agent plans must be a list.")

        contexts = self._road_contexts_by_id(road_context)
        operations_by_target = {}
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError("Single Agent DSL operations must be objects.")
            agent = str(operation.get("operation", "")).lower()
            road_id = self._normalize_road_id(operation.get("road_id"))
            if agent not in {"width", "slope", "curve"} or not road_id:
                raise ValueError("Single Agent DSL requires a supported operation and road_id.")
            operations_by_target.setdefault((agent, road_id), []).append(operation)

        plans_by_target = {}
        normalized_plans = []
        for plan in plans:
            if not isinstance(plan, dict) or set(plan) != {
                "agent",
                "road_id",
                "tool_calls",
            }:
                raise ValueError(
                    "Single Agent plans require only agent, road_id and tool_calls."
                )
            agent = str(plan["agent"]).lower()
            road_id = self._normalize_road_id(plan["road_id"])
            key = (agent, road_id)
            if agent not in {"width", "slope", "curve"} or not road_id:
                raise ValueError("Single Agent plan has an invalid target.")
            if key in plans_by_target:
                raise ValueError("Single Agent requires one plan per road and agent.")
            if not isinstance(plan["tool_calls"], list):
                raise ValueError("Single Agent plan tool_calls must be a list.")
            normalized = {
                "agent": agent,
                "road_id": f"road_{road_id}",
                "tool_calls": plan["tool_calls"],
            }
            plans_by_target[key] = normalized
            normalized_plans.append(normalized)

        if set(operations_by_target) != set(plans_by_target):
            raise ValueError(
                "Single Agent plans must correspond exactly to DSL road/type targets."
            )

        execution_plans = []
        for key, target_operations in operations_by_target.items():
            agent, road_id = key
            road = contexts.get(road_id)
            if road is None or not isinstance(road.get(agent), dict):
                raise ValueError(f"Single Agent road_data is missing {agent} road_{road_id}.")
            road_info = road[agent]
            target_dsl = {"operations": target_operations}
            format_result = validate_dsl_format(agent, target_dsl, road_info)
            if format_result.get("passed") is not True:
                raise ValueError(
                    f"Single Agent {agent} DSL format failed: "
                    f"{format_result.get('check_result')}"
                )
            calls = plans_by_target[key]["tool_calls"]
            execution_plans.append(
                self._prepare_execution_plan(agent, target_dsl, road_info, calls)
            )

        return {
            "dsl": {"operations": operations},
            "plans": normalized_plans,
            "execution_plans": execution_plans,
        }

    def _prepare_execution_plan(self, agent, dsl, road_info, calls):
        if agent == "width":
            normalized = normalize_width_plan_tool_calls(calls)
            self.width_validator._validate_plan_targets(dsl, road_info, normalized)
            resolved = self.width_validator.resolve_execution_plan(
                dsl, road_info, normalized
            )
            return self._execution_plan(
                agent, dsl, road_info, normalized, resolved
            )
        if agent == "slope":
            normalized = normalize_slope_plan_tool_calls(calls)
            self.slope_validator._validate_plan_targets(dsl, road_info, normalized)
            resolved = self.slope_validator.resolve_execution_plan(
                dsl, road_info, normalized
            )
            return self._execution_plan(
                agent, dsl, road_info, normalized, resolved
            )

        normalized = normalize_curve_tool_calls(calls)
        curve_dsl = {
            "operations": [
                {**operation, "gi": int(road_info["gi"])}
                for operation in dsl["operations"]
            ]
        }
        self.curve_validator._validate_tool_targets(
            curve_dsl, road_info, normalized
        )
        return self._execution_plan(agent, curve_dsl, road_info, normalized)

    def _execution_plan(
        self, agent, dsl, road_info, tool_calls, resolved_tool_calls=None
    ):
        result = {
            "agent": agent,
            "dsl": dsl,
            "road_info": road_info,
            "tool_calls": tool_calls,
        }
        if resolved_tool_calls is not None:
            result["resolved_tool_calls"] = resolved_tool_calls
        return result

    def _road_contexts_by_id(self, road_context):
        roads = road_context.get("roads") if isinstance(road_context, dict) else None
        if not isinstance(roads, list):
            raise ValueError("Single Agent road_data.roads must be a list.")
        contexts = {}
        for road in roads:
            if not isinstance(road, dict):
                raise ValueError("Single Agent road entries must be objects.")
            road_id = self._normalize_road_id(road.get("road_id"))
            if not road_id or road_id in contexts:
                raise ValueError("Single Agent road_data contains an invalid road_id.")
            contexts[road_id] = road
        return contexts

    def _normalize_road_id(self, value):
        road_id = str(value or "").replace("road_", "")
        return road_id if road_id.isdigit() else ""

    def get_commends(self, commendDescription, road_context=None):
        return self.get_commands(commendDescription, road_context)
