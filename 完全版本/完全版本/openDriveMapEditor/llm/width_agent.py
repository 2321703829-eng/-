import json
import os
from collections import Counter
from typing import Literal

from dotenv import load_dotenv
from langchain_classic.output_parsers import (
    ResponseSchema,
    StructuredOutputParser,
    OutputFixingParser,
)
from langchain_core.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from llm.agent_tools import (
    ID_PROMPT,
    WIDTH_TOOL_PLAN_PROMPT,
    escape_langchain_template_text,
    evaluate_calculation_formula,
    get_model,
    WIDTH_DSL_CHECK_PROMPT,
    WIDTH_DSL_PROMPT,
)
from llm.dsl_format_validator import validate_dsl_format
from llm.width_guardrails import WIDTH_RULES, CHECK_RULES, width_context, checker_errors, validate_formula, unrelated_repair_changes
from utils import agent_trace

load_dotenv(override=True)

DSL_CHECK_MAX_ATTEMPTS = 3


class WidthToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    v: float = Field(allow_inf_nan=False)
    s: Literal[1]
    ms: int = Field(ge=0)
    sh: Literal[1]
    li: list[int]

    @field_validator("id")
    @classmethod
    def validate_id(cls, value):
        if not value.isdigit():
            raise ValueError("Width tool id must be a concrete numeric road id.")
        return value

    @field_validator("s", "ms", "sh", mode="before")
    @classmethod
    def reject_boolean_integers(cls, value):
        if isinstance(value, bool):
            raise ValueError("Width integer parameters cannot be boolean values.")
        return value

    @field_validator("li", mode="before")
    @classmethod
    def reject_boolean_lane_ids(cls, value):
        if isinstance(value, list) and any(isinstance(item, bool) for item in value):
            raise ValueError("Width lane ids cannot be boolean values.")
        return value


class WidthPlanItemArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_index: int = Field(ge=0)
    formula: str = Field(min_length=1, max_length=500)
    id: str
    s: Literal[1]
    ms: int = Field(ge=0)
    sh: Literal[1]
    li: list[int]

    @field_validator("id")
    @classmethod
    def validate_id(cls, value):
        if not value.isdigit():
            raise ValueError("Width tool id must be a concrete numeric road id.")
        return value

    @field_validator("operation_index", "s", "ms", "sh", mode="before")
    @classmethod
    def reject_boolean_integers(cls, value):
        if isinstance(value, bool):
            raise ValueError("Width integer parameters cannot be boolean values.")
        return value

    @field_validator("li", mode="before")
    @classmethod
    def reject_boolean_lane_ids(cls, value):
        if isinstance(value, list) and any(isinstance(item, bool) for item in value):
            raise ValueError("Width lane ids cannot be boolean values.")
        return value


class WidthApplyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WidthPlanItemArguments] = Field(min_length=1)


@tool("width_apply", args_schema=WidthApplyArguments)
def width_apply_tool(items):
    """Submit every formula and handlerWidth parameter set in one complete plan."""
    return {"items": items}


@tool("apply_width", args_schema=WidthToolArguments)
def apply_width_tool(id, v, s, ms, sh, li):
    """Apply one complete handlerWidth command parameter set."""
    return {"id": id, "v": v, "s": s, "ms": ms, "sh": sh, "li": li}


def normalize_width_tool_calls(raw_calls):
    """Validate and normalize tool calls at both planning and execution boundaries."""
    if not isinstance(raw_calls, list):
        raise ValueError("Width tool calls must be a list.")
    calls = []
    for call in raw_calls:
        if not isinstance(call, dict) or call.get("name") != "apply_width":
            name = call.get("name") if isinstance(call, dict) else None
            raise ValueError(f"Unsupported width tool: {name}")
        arguments = WidthToolArguments.model_validate(call.get("args", {}))
        calls.append({"name": "apply_width", "args": arguments.model_dump()})
    return calls


def normalize_width_plan_tool_calls(raw_calls):
    """Validate the single batch plan returned by the Width Agent."""
    if not isinstance(raw_calls, list):
        raise ValueError("Width tool calls must be a list.")
    if not raw_calls:
        return []
    if len(raw_calls) != 1:
        raise ValueError("Width Agent must return exactly one width_apply call.")
    call = raw_calls[0]
    name = call.get("name") if isinstance(call, dict) else None
    if name != "width_apply":
        raise ValueError(f"Unsupported width tool: {name}")
    arguments = WidthApplyArguments.model_validate(call.get("args", {}))
    return [{"name": name, "args": arguments.model_dump()}]


class WidthAgent:

    def __init__(self, model="", enable_dsl_check=True):
        # self.apikey = None
        self.model = get_model(model)
        self.enable_dsl_check = enable_dsl_check
        # self.init(model)
        self.chain_id = self.init_chain_id()
        self.chain = self.init_chain()
        self.check_chain = self.init_check_chain() if self.enable_dsl_check else None
        self.execution_chain = self.init_execution_chain()

    def init_chain_id(self):
        parser = JsonOutputParser()

        system_prompt = SystemMessagePromptTemplate.from_template(
            escape_langchain_template_text(ID_PROMPT)
            + """\nPlease ensure to directly output JSON data, without including the ```json markers, and without any prefixes or suffixes."""
        )

        human_prompt = HumanMessagePromptTemplate.from_template(
            """Extract the road id according to the system prompt. Task description: {userInput}"""
        )

        chat_prompt = ChatPromptTemplate.from_messages([system_prompt, human_prompt])

        fixing_parser = OutputFixingParser.from_llm(parser=parser, llm=self.model)
        return chat_prompt | self.model | fixing_parser

    def init_chain(self):

        parser = JsonOutputParser()

        system_prompt = SystemMessagePromptTemplate.from_template(
            escape_langchain_template_text(WIDTH_DSL_PROMPT + WIDTH_RULES)
            + """\nPlease ensure to directly output JSON data, without including the ```json markers, and without any prefixes or suffixes."""
        )

        human_prompt = HumanMessagePromptTemplate.from_template(
            """Road width context: {originalInfo}\nPrevious failed DSL output for reference only: {previousDslOutput}\nPrevious DSL check result for reference only: {previousCheckResult}\nRetry instruction: {retryInstruction}\nExtract the width modification DSL JSON according to the system prompt. Task description: {userInput}"""
        )

        chat_prompt = ChatPromptTemplate.from_messages([system_prompt, human_prompt])

        fixing_parser = OutputFixingParser.from_llm(parser=parser, llm=self.model)

        chain = chat_prompt | self.model | fixing_parser
        # 错误重试机制
        return chain

    def init_check_chain(self):
        parser = JsonOutputParser()

        system_prompt = SystemMessagePromptTemplate.from_template(
            escape_langchain_template_text(WIDTH_DSL_CHECK_PROMPT + WIDTH_RULES + CHECK_RULES)
            + """\nPlease ensure to directly output JSON data, without including the ```json markers, and without any prefixes or suffixes."""
        )

        human_prompt = HumanMessagePromptTemplate.from_template(
            """user_instruction: {userInput}\nroad_data: {originalInfo}\ndsl_output: {dslOutput}\nreview_history: {reviewHistory}\nchecker_feedback: {checkerFeedback}"""
        )

        chat_prompt = ChatPromptTemplate.from_messages([system_prompt, human_prompt])

        fixing_parser = OutputFixingParser.from_llm(parser=parser, llm=self.model)
        return chat_prompt | self.model | fixing_parser

    def init_execution_chain(self):
        system_prompt = SystemMessagePromptTemplate.from_template(
            escape_langchain_template_text(WIDTH_TOOL_PLAN_PROMPT + WIDTH_RULES)
        )
        human_prompt = HumanMessagePromptTemplate.from_template(
            "validated_dsl: {dsl}\nroad_data: {roadInfo}"
        )
        chat_prompt = ChatPromptTemplate.from_messages([system_prompt, human_prompt])
        # DeepSeek thinking models support tool calls, but reject a forced
        # tool_choice.  There is only one tool here; the prompt requests it and
        # plan_execution validates that the model returned exactly one valid
        # batch call before anything is changed.
        return chat_prompt | self.model.bind_tools([width_apply_tool])

    def print_dsl_check_result(self, attempt, check_result):
        print(
            f"[DSL check][width] round = {attempt}\n"
            f"                   passed = {check_result.get('passed')}\n"
            f"                   problem = {check_result.get('problem_source')}\n"
            f"                   result = {check_result.get('check_result')}\n"
        )

    def _build_extract_request(
        self, user_input, road_info, previous_dsl_output=None, previous_check_result=None
    ):
        road_info = width_context(road_info)
        retry_instruction = ""
        if previous_check_result:
            retry_instruction = (
                "Run a fresh DSL extraction from the original task description and road context. Treat the previous failed DSL output and check result only as diagnostic reference to avoid repeating the same extraction error. Preserve fields not implicated by concrete issues unless the original request proves another error; do not expand the user requirement, do not invent new road data, and output JSON only."
            )
        return {
            "userInput": user_input,
            "originalInfo": road_info,
            "previousDslOutput": (
                "" if previous_dsl_output is None else json.dumps(previous_dsl_output, ensure_ascii=False)
            ),
            "previousCheckResult": (
                "" if previous_check_result is None else json.dumps(previous_check_result, ensure_ascii=False)
            ),
            "retryInstruction": retry_instruction,
        }

    def get_commands(self, user_input, road_info):
        road_info = width_context(road_info)
        # print("widthAgent:", user_input)
        result = agent_trace.invoke_llm(
            self.chain,
            self._build_extract_request(user_input, road_info),
            request_kind="dsl_extract",
            attempt=1,
            agent="width",
            operation="width",
        )
        agent_trace.record_agent_step(
            agent="width",
            operation="width",
            input_text=user_input,
            road_context_json=road_info,
            llm_output_json=result,
            status="running" if self.enable_dsl_check else "ok",
        )
        if self.enable_dsl_check:
            last_check_result = None
            review_history = []
            checker_feedback = ""
            for attempt in range(1, DSL_CHECK_MAX_ATTEMPTS + 1):
                check_request = {
                    "userInput": user_input,
                    "originalInfo": road_info,
                    "dslOutput": json.dumps(result, ensure_ascii=False),
                    "reviewHistory": json.dumps(review_history, ensure_ascii=False),
                    "checkerFeedback": checker_feedback,
                }
                format_check_result = validate_dsl_format("width", result, road_info)
                if format_check_result.get("passed") is not True:
                    self.print_dsl_check_result(attempt, format_check_result)
                    format_check_request = dict(check_request)
                    format_check_request["checker"] = "local_dsl_format"
                    agent_trace.record_dsl_check(
                        agent="width",
                        operation="width",
                        attempt=attempt,
                        input_text=user_input,
                        road_context_json=road_info,
                        dsl_output_json=result,
                        check_request_json=format_check_request,
                        check_output_json=format_check_result,
                        status="ok",
                    )
                    last_check_result = format_check_result
                    if attempt == DSL_CHECK_MAX_ATTEMPTS:
                        message = (
                            f"Width DSL check failed: "
                            f"{format_check_result.get('check_result')}"
                        )
                        agent_trace.update_agent_step(status="error", error=message)
                        raise ValueError(message)
                    result = agent_trace.invoke_llm(
                        self.chain,
                        self._build_extract_request(
                            user_input, road_info, result, format_check_result
                        ),
                        request_kind="dsl_extract",
                        attempt=attempt + 1,
                        agent="width",
                        operation="width",
                    )
                    agent_trace.update_agent_step(
                        llm_output_json=result,
                        status="running",
                    )
                    continue

                check_result = agent_trace.invoke_llm(
                    self.check_chain,
                    check_request,
                    request_kind="dsl_check",
                    attempt=attempt,
                    agent="width",
                    operation="width",
                )
                contract_errors = checker_errors(check_result)
                self.print_dsl_check_result(attempt, check_result)
                agent_trace.record_dsl_check(
                    agent="width",
                    operation="width",
                    attempt=attempt,
                    input_text=user_input,
                    road_context_json=road_info,
                    dsl_output_json=result,
                    check_request_json=check_request,
                    check_output_json=check_result,
                    status="error" if contract_errors else "ok",
                )
                review_history.append({"attempt": attempt, "dsl": result, "check": check_result})
                if contract_errors:
                    checker_feedback = "Invalid checker response: " + "; ".join(contract_errors) + ". Recheck the SAME DSL; do not treat these contract errors as extraction errors."
                    if attempt == DSL_CHECK_MAX_ATTEMPTS:
                        message = "Width checker response invalid: " + "; ".join(contract_errors)
                        agent_trace.update_agent_step(status="error", error=message)
                        raise ValueError(message)
                    continue
                checker_feedback = ""
                last_check_result = check_result
                if check_result.get("passed") is True:
                    break
                problem_source = check_result.get("problem_source")
                if (
                    problem_source != "extraction_error"
                    or attempt == DSL_CHECK_MAX_ATTEMPTS
                ):
                    message = (
                        f"Width DSL check failed: {check_result.get('check_result')}"
                    )
                    agent_trace.update_agent_step(status="error", error=message)
                    raise ValueError(message)
                previous_result = result
                result = agent_trace.invoke_llm(
                    self.chain,
                    self._build_extract_request(
                        user_input, road_info, result, check_result
                    ),
                    request_kind="dsl_extract",
                    attempt=attempt + 1,
                    agent="width",
                    operation="width",
                )
                collateral = unrelated_repair_changes(previous_result, result, check_result["issues"])
                if collateral:
                    message = "Width DSL repair changed fields outside reported issues: " + ", ".join(collateral)
                    agent_trace.update_agent_step(llm_output_json=result, status="error", error=message)
                    raise ValueError(message)
                agent_trace.update_agent_step(
                    llm_output_json=result,
                    status="running",
                )
            else:
                message = f"Width DSL check failed: {last_check_result}"
                agent_trace.update_agent_step(status="error", error=message)
                raise ValueError(message)

            agent_trace.update_agent_step(status="ok")
        print(result)
        return result

    def plan_execution(self, dsl, road_info):
        if not self._dsl_requires_tools(dsl):
            agent_trace.update_agent_step(tool_call_json=[])
            return []
        road_info = width_context(road_info)
        request = {"dsl": json.dumps(dsl, ensure_ascii=False),
                   "roadInfo": json.dumps(road_info, ensure_ascii=False)}
        for attempt in range(1, 4):
            response = agent_trace.invoke_llm(self.execution_chain, request,
                request_kind="tool_plan", attempt=attempt, agent="width", operation="width")
            raw_calls = agent_trace.tool_calls_for_trace(response)
            agent_trace.update_agent_step(tool_call_json=raw_calls)
            try:
                calls = normalize_width_plan_tool_calls(raw_calls)
                self._validate_plan_targets(dsl, road_info, calls)
            except (TypeError, ValueError, SyntaxError, KeyError, OverflowError) as exc:
                agent_trace.update_agent_step(error=f"Width tool plan attempt {attempt}: {exc}", status="running")
                if attempt == 3:
                    raise ValueError(f"Width tool planning failed: {exc}") from exc
                request = {"dsl": json.dumps(dsl, ensure_ascii=False), "roadInfo": json.dumps({
                    **road_info, "tool_plan_feedback": str(exc), "previous_tool_calls": raw_calls,
                    "retry_scope": "Repair only the tool plan. The validated DSL is fixed; cover every operation/road/lane and correct the formula sign and value."}, ensure_ascii=False)}
                continue
            agent_trace.update_agent_step(tool_call_json=calls, error="", status="ok")
            return calls

    def resolve_execution_plan(self, dsl, road_info, raw_calls):
        calls = normalize_width_plan_tool_calls(raw_calls)
        items = self._validate_plan_targets(dsl, road_info, calls)
        resolved = []
        random_cache_by_operation = {}
        for item in items:
            resolved_arguments = dict(item)
            formula = resolved_arguments.pop("formula")
            operation_index = resolved_arguments.pop("operation_index")
            random_cache = random_cache_by_operation.setdefault(operation_index, {})
            resolved_arguments["v"] = evaluate_calculation_formula(
                formula, random_cache=random_cache
            )
            resolved.append({"name": "apply_width", "args": resolved_arguments})

        resolved = normalize_width_tool_calls(resolved)
        self._validate_tool_targets(dsl, road_info, resolved)
        return resolved

    def _validate_plan_targets(self, dsl, road_info, calls):
        if not calls:
            if self._dsl_requires_tools(dsl):
                raise ValueError("Width DSL operations require a batch tool plan.")
            return []
        if len(calls) != 1:
            raise ValueError("Width plan requires exactly one batch tool call.")
        items = calls[0]["args"]["items"]
        context_road_id = str(road_info.get("road_id", "")).replace("road_", "")
        expected_targets = []
        for operation_index, operation in enumerate(dsl.get("operations", [])):
            if (
                operation.get("operation") != "width"
                or operation.get("change", {}).get("action") == "none"
            ):
                continue
            road_id = str(operation.get("road_id", "")).replace("road_", "")
            if context_road_id and road_id != context_road_id:
                raise ValueError("Width DSL road_id does not match road_data.")
            lane_targets = self._plan_lane_targets(operation, road_info)
            road_limit = operation.get("road_limit", "none")
            ms = 99 if road_limit == "none" else int(road_limit) - 1
            expected_targets.extend(
                (operation_index, road_id, lane_target, ms)
                for lane_target in lane_targets
            )

        actual_targets = [
            (
                item["operation_index"],
                item["id"],
                tuple(item["li"]),
                item["ms"],
            )
            for item in items
        ]
        if Counter(actual_targets) != Counter(expected_targets):
            raise ValueError(
                "Width plan items must cover exactly the DSL operation/road/lane/"
                "scope targets without omissions or duplicates."
            )

        target_order = {
            target: index for index, target in enumerate(expected_targets)
        }
        items.sort(
            key=lambda item: target_order[
                (
                    item["operation_index"],
                    item["id"],
                    tuple(item["li"]),
                    item["ms"],
                )
            ]
        )

        valid_lane_ids = {int(item) for item in road_info.get("lane_ids", [])}
        for item in items:
            if valid_lane_ids and not set(item["li"]).issubset(valid_lane_ids):
                raise ValueError("Width tool li contains a lane outside road_data.")
        for item in items:
            validate_formula(item, dsl["operations"][item["operation_index"]], road_info)
        return items

    def _dsl_requires_tools(self, dsl):
        return any(
            operation.get("operation") == "width"
            and operation.get("change", {}).get("action") != "none"
            for operation in dsl.get("operations", [])
        )

    def _plan_lane_targets(self, operation, road_info):
        lane_ids = operation.get("lane_ids")
        if lane_ids not in ("none", []):
            return [
                (int(str(lane_id).replace("lane_", "")),)
                for lane_id in lane_ids
            ]
        if self._is_proportional_whole_road_change(operation):
            return [(int(lane_id),) for lane_id in road_info.get("lane_ids", [])]
        return [()]

    @staticmethod
    def _is_proportional_whole_road_change(operation):
        if operation.get("lane_ids") not in ("none", []):
            return False
        change = operation.get("change", {})
        magnitude = change.get("magnitude", {})
        action = change.get("action")
        return (
            magnitude.get("kind") == "qualitative"
            or (
                magnitude.get("kind") == "numeric"
                and magnitude.get("unit") == "percent"
                and action in ("increase", "decrease")
            )
        )

    def _validate_tool_targets(self, dsl, road_info, calls):
        expected_targets = []
        context_road_id = str(road_info.get("road_id", "")).replace("road_", "")
        for operation in dsl.get("operations", []):
            if (
                operation.get("operation") != "width"
                or operation.get("change", {}).get("action") == "none"
            ):
                continue
            road_id = str(operation.get("road_id", "")).replace("road_", "")
            if context_road_id and road_id != context_road_id:
                raise ValueError("Width DSL road_id does not match road_data.")
            lane_targets = self._plan_lane_targets(operation, road_info)
            road_limit = operation.get("road_limit", "none")
            ms = 99 if road_limit == "none" else int(road_limit) - 1
            expected_targets.extend(
                (road_id, lane_target, ms) for lane_target in lane_targets
            )

        actual_targets = [
            (call["args"]["id"], tuple(call["args"]["li"]), call["args"]["ms"])
            for call in calls
        ]
        if Counter(actual_targets) != Counter(expected_targets):
            raise ValueError(
                "Width tool calls must cover exactly the DSL road/lane/scope targets."
            )

        valid_lane_ids = {int(item) for item in road_info.get("lane_ids", [])}
        for call in calls:
            arguments = call["args"]
            if valid_lane_ids and not set(arguments["li"]).issubset(valid_lane_ids):
                raise ValueError("Width tool li contains a lane outside road_data.")

    def get_road_id(self, user_input):
        result = agent_trace.invoke_llm(
            self.chain_id,
            {"userInput": user_input},
            request_kind="road_id_extract",
            attempt=1,
            agent="width",
            operation="width",
        )
        if isinstance(result, str):
            result = json.loads(result)
        if "result" in result and isinstance(result["result"], dict):
            result = result["result"]
        road_id = result.get("road_id", -1)
        if road_id in (-1, None, "", "none"):
            raise ValueError("Width road id extraction failed.")
        agent_trace.update_agent_step(road_id_output_json=result)
        return str(road_id).replace("road_", "")
