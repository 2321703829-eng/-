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
    SLOPE_TOOL_PLAN_PROMPT,
    escape_langchain_template_text,
    evaluate_calculation_formula,
    get_model,
    SLOPE_DSL_CHECK_PROMPT,
    SLOPE_DSL_PROMPT,
)
from llm.dsl_format_validator import validate_dsl_format
from utils import agent_trace

load_dotenv(override=True)

DSL_CHECK_MAX_ATTEMPTS = 3


class SlopeToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    v: float = Field(allow_inf_nan=False)
    m: Literal["add"]
    mv: Literal[0, 1, 2]
    ms: int = Field(ge=0)
    sh: Literal[1]

    @field_validator("id")
    @classmethod
    def validate_id(cls, value):
        if not value.isdigit():
            raise ValueError("Slope tool id must be a concrete numeric road id.")
        return value

    @field_validator("mv", "ms", "sh", mode="before")
    @classmethod
    def reject_boolean_integers(cls, value):
        if isinstance(value, bool):
            raise ValueError("Slope integer parameters cannot be boolean values.")
        return value


class SlopePlanItemArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_index: int = Field(ge=0)
    formula: str = Field(min_length=1, max_length=500)
    id: str
    m: Literal["add"]
    mv: Literal[0, 1, 2]
    ms: int = Field(ge=0)
    sh: Literal[1]

    @field_validator("id")
    @classmethod
    def validate_id(cls, value):
        if not value.isdigit():
            raise ValueError("Slope tool id must be a concrete numeric road id.")
        return value

    @field_validator("operation_index", "mv", "ms", "sh", mode="before")
    @classmethod
    def reject_boolean_integers(cls, value):
        if isinstance(value, bool):
            raise ValueError("Slope integer parameters cannot be boolean values.")
        return value


class SlopeApplyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SlopePlanItemArguments] = Field(min_length=1)


@tool("slope_apply", args_schema=SlopeApplyArguments)
def slope_apply_tool(items):
    """Submit every formula and handlerSlope parameter set in one complete plan."""
    return {"items": items}


@tool("apply_slope", args_schema=SlopeToolArguments)
def apply_slope_tool(id, v, m, mv, ms, sh):
    """Apply one complete handlerSlope command parameter set."""
    return {"id": id, "v": v, "m": m, "mv": mv, "ms": ms, "sh": sh}


def normalize_slope_tool_calls(raw_calls):
    """Validate and normalize tool calls at both planning and execution boundaries."""
    if not isinstance(raw_calls, list):
        raise ValueError("Slope tool calls must be a list.")
    calls = []
    for call in raw_calls:
        if not isinstance(call, dict) or call.get("name") != "apply_slope":
            name = call.get("name") if isinstance(call, dict) else None
            raise ValueError(f"Unsupported slope tool: {name}")
        arguments = SlopeToolArguments.model_validate(call.get("args", {}))
        calls.append({"name": "apply_slope", "args": arguments.model_dump()})
    return calls


def normalize_slope_plan_tool_calls(raw_calls):
    """Validate the single batch plan returned by the Slope Agent."""
    if not isinstance(raw_calls, list):
        raise ValueError("Slope tool calls must be a list.")
    if not raw_calls:
        return []
    if len(raw_calls) != 1:
        raise ValueError("Slope Agent must return exactly one slope_apply call.")
    call = raw_calls[0]
    name = call.get("name") if isinstance(call, dict) else None
    if name != "slope_apply":
        raise ValueError(f"Unsupported slope tool: {name}")
    arguments = SlopeApplyArguments.model_validate(call.get("args", {}))
    return [{"name": name, "args": arguments.model_dump()}]


class SlopeAgent:

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
            escape_langchain_template_text(SLOPE_DSL_PROMPT)
            + """\nPlease ensure to directly output JSON data, without including the ```json markers, and without any prefixes or suffixes."""
        )

        human_prompt = HumanMessagePromptTemplate.from_template(
            """This is the original information of the curve {originalInfo}\nPrevious failed DSL output for reference only: {previousDslOutput}\nPrevious DSL check result for reference only: {previousCheckResult}\nRetry instruction: {retryInstruction}\nExtract the required information based on the prompt and return it in JSON format. Task description: {userInput}"""
        )

        chat_prompt = ChatPromptTemplate.from_messages([system_prompt, human_prompt])

        fixing_parser = OutputFixingParser.from_llm(parser=parser, llm=self.model)

        chain = chat_prompt | self.model | fixing_parser
        # 错误重试机制
        return chain

        # return (prompt_template.partial(format_instructions=parser.get_format_instructions())
        #                     |self.model
        #                     | parser)

    def init_check_chain(self):
        parser = JsonOutputParser()

        system_prompt = SystemMessagePromptTemplate.from_template(
            escape_langchain_template_text(SLOPE_DSL_CHECK_PROMPT)
            + """\nPlease ensure to directly output JSON data, without including the ```json markers, and without any prefixes or suffixes."""
        )

        human_prompt = HumanMessagePromptTemplate.from_template(
            """user_instruction: {userInput}\nroad_data: {originalInfo}\ndsl_output: {dslOutput}"""
        )

        chat_prompt = ChatPromptTemplate.from_messages([system_prompt, human_prompt])

        fixing_parser = OutputFixingParser.from_llm(parser=parser, llm=self.model)
        return chat_prompt | self.model | fixing_parser

    def init_execution_chain(self):
        system_prompt = SystemMessagePromptTemplate.from_template(
            escape_langchain_template_text(SLOPE_TOOL_PLAN_PROMPT)
        )
        human_prompt = HumanMessagePromptTemplate.from_template(
            "validated_dsl: {dsl}\nroad_data: {roadInfo}"
        )
        chat_prompt = ChatPromptTemplate.from_messages([system_prompt, human_prompt])
        # DeepSeek thinking models reject a forced tool_choice.  The prompt
        # requests the only bound tool, and the execution-plan validators
        # reject missing, extra, or out-of-scope calls before dispatch.
        return chat_prompt | self.model.bind_tools([slope_apply_tool])

    def print_dsl_check_result(self, attempt, check_result,check_request):
        print( f"[DSL check][slope] round={attempt}")
        if check_result.get("passed") is not True:
            print(f"[DSL] "+ json.dumps(check_request, ensure_ascii=False, indent=2))

        print(f"[DSL check][slope] passed = {check_result.get('passed')} \n"
              f"                   problem = {check_result.get('problem_source')}\n"
              f"                   result = {check_result.get('check_result')}\n"
        )

    def _build_extract_request(
        self, user_input, road_info, previous_dsl_output=None, previous_check_result=None
    ):
        retry_instruction = ""
        if previous_check_result:
            retry_instruction = (
                "Run a fresh DSL extraction from the original task description and road context. Treat the previous failed DSL output and check result only as diagnostic reference to avoid repeating the same extraction error. Do not patch the previous DSL, do not expand the user requirement, do not invent new road data, and output JSON only."
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

    def get_commands(self, user_input, road_info={}):
        # print("slopeAgent:", user_input)
        result = agent_trace.invoke_llm(
            self.chain,
            self._build_extract_request(user_input, road_info),
            request_kind="dsl_extract",
            attempt=1,
            agent="slope",
            operation="slope",
        )
        agent_trace.record_agent_step(
            agent="slope",
            operation="slope",
            input_text=user_input,
            road_context_json=road_info,
            llm_output_json=result,
            status="running" if self.enable_dsl_check else "ok",
        )
        if self.enable_dsl_check:
            last_check_result = None
            for attempt in range(1, DSL_CHECK_MAX_ATTEMPTS + 1):
                check_request = {
                    "userInput": user_input,
                    "originalInfo": road_info,
                    "dslOutput": json.dumps(result, ensure_ascii=False),
                }
                format_check_result = validate_dsl_format("slope", result, road_info)
                if format_check_result.get("passed") is not True:
                    format_check_request = dict(check_request)
                    format_check_request["checker"] = "local_dsl_format"
                    self.print_dsl_check_result(
                        attempt, format_check_result, format_check_request
                    )
                    agent_trace.record_dsl_check(
                        agent="slope",
                        operation="slope",
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
                            f"Slope DSL check failed: "
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
                        agent="slope",
                        operation="slope",
                    )
                    agent_trace.update_agent_step(
                        llm_output_json=result,
                        status="running",
                    )
                    continue
                # 通过了结构检查
                check_result = agent_trace.invoke_llm(
                    self.check_chain,
                    check_request,
                    request_kind="dsl_check",
                    attempt=attempt,
                    agent="slope",
                    operation="slope",
                )
                self.print_dsl_check_result(attempt, check_result,check_request)
                agent_trace.record_dsl_check(
                    agent="slope",
                    operation="slope",
                    attempt=attempt,
                    input_text=user_input,
                    road_context_json=road_info,
                    dsl_output_json=result,
                    check_request_json=check_request,
                    check_output_json=check_result,
                    status="ok",
                )
                last_check_result = check_result
                if check_result.get("passed") is True:
                    break
                problem_source = check_result.get("problem_source")
                if (
                    problem_source != "extraction_error"
                    or attempt == DSL_CHECK_MAX_ATTEMPTS
                ):
                    message = (
                        f"Slope DSL check failed: {check_result.get('check_result')}"
                    )
                    agent_trace.update_agent_step(status="error", error=message)
                    raise ValueError(message)
                result = agent_trace.invoke_llm(
                    self.chain,
                    self._build_extract_request(
                        user_input, road_info, result, check_result
                    ),
                    request_kind="dsl_extract",
                    attempt=attempt + 1,
                    agent="slope",
                    operation="slope",
                )
                agent_trace.update_agent_step(
                    llm_output_json=result,
                    status="running",
                )
            else:
                message = f"Slope DSL check failed: {last_check_result}"
                agent_trace.update_agent_step(status="error", error=message)
                raise ValueError(message)

            agent_trace.update_agent_step(status="ok")
        print(result)
        return result

    def plan_execution(self, dsl, road_info):
        requires_tools = self._dsl_requires_tools(dsl)
        if not requires_tools:
            agent_trace.update_agent_step(tool_call_json=[])
            return []
        request = {
            "dsl": json.dumps(dsl, ensure_ascii=False),
            "roadInfo": json.dumps(road_info, ensure_ascii=False),
        }
        response = agent_trace.invoke_llm(
            self.execution_chain,
            request,
            request_kind="tool_plan",
            attempt=1,
            agent="slope",
            operation="slope",
        )
        raw_calls = agent_trace.tool_calls_for_trace(response)
        agent_trace.update_agent_step(tool_call_json=raw_calls)
        try:
            calls = normalize_slope_plan_tool_calls(raw_calls)
            if not calls:
                raise ValueError("Slope DSL operations require Slope tool calls.")
            self._validate_plan_targets(dsl, road_info, calls)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Slope tool planning failed: {exc}") from exc
        agent_trace.update_agent_step(tool_call_json=calls)
        return calls

    def resolve_execution_plan(self, dsl, road_info, raw_calls):
        calls = normalize_slope_plan_tool_calls(raw_calls)
        items = self._validate_plan_targets(dsl, road_info, calls)
        resolved = []
        for item in items:
            resolved_arguments = dict(item)
            formula = resolved_arguments.pop("formula")
            resolved_arguments.pop("operation_index")
            resolved_arguments["v"] = evaluate_calculation_formula(formula)
            resolved.append({"name": "apply_slope", "args": resolved_arguments})

        resolved = normalize_slope_tool_calls(resolved)
        self._validate_tool_targets(dsl, road_info, resolved)
        return resolved

    def _validate_plan_targets(self, dsl, road_info, calls):
        if not calls:
            if self._dsl_requires_tools(dsl):
                raise ValueError("Slope DSL operations require a batch tool plan.")
            return []
        if len(calls) != 1:
            raise ValueError("Slope plan requires exactly one batch tool call.")
        items = calls[0]["args"]["items"]
        context_road_id = str(road_info.get("road_id", "")).replace("road_", "")
        expected_targets = []
        for operation_index, operation in enumerate(dsl.get("operations", [])):
            if (
                operation.get("operation") != "slope"
                or operation.get("change", {}).get("action") == "none"
            ):
                continue
            road_id = str(operation.get("road_id", "")).replace("road_", "")
            if context_road_id and road_id != context_road_id:
                raise ValueError("Slope DSL road_id does not match road_data.")
            road_limit = operation.get("road_limit", "none")
            ms = 99 if road_limit == "none" else int(road_limit) - 1
            expected_targets.extend(
                (operation_index, road_id, mv, ms)
                for mv in self._expected_move_values(operation)
            )

        actual_targets = [
            (item["operation_index"], item["id"], item["mv"], item["ms"])
            for item in items
        ]
        if Counter(actual_targets) != Counter(expected_targets):
            raise ValueError(
                "Slope plan items must cover exactly the DSL operation/road/position/"
                "scope targets without omissions or duplicates."
            )
        return items

    def _dsl_requires_tools(self, dsl):
        return any(
            operation.get("operation") == "slope"
            and operation.get("change", {}).get("action") != "none"
            for operation in dsl.get("operations", [])
        )

    def _validate_tool_targets(self, dsl, road_info, calls):
        expected_targets = []
        context_road_id = str(road_info.get("road_id", "")).replace("road_", "")
        for operation in dsl.get("operations", []):
            if (
                operation.get("operation") != "slope"
                or operation.get("change", {}).get("action") == "none"
            ):
                continue
            road_id = str(operation.get("road_id", "")).replace("road_", "")
            if context_road_id and road_id != context_road_id:
                raise ValueError("Slope DSL road_id does not match road_data.")
            road_limit = operation.get("road_limit", "none")
            ms = 99 if road_limit == "none" else int(road_limit) - 1
            expected_targets.extend(
                (road_id, mv, ms) for mv in self._expected_move_values(operation)
            )

        actual_targets = [
            (call["args"]["id"], call["args"]["mv"], call["args"]["ms"])
            for call in calls
        ]
        if Counter(actual_targets) != Counter(expected_targets):
            raise ValueError(
                "Slope tool calls must cover exactly the DSL road/position/scope "
                "targets without duplicates."
            )

    def _expected_move_values(self, operation):
        position = operation.get("position")
        if position == "start":
            return (0,)
        if position == "end":
            return (1,)
        if position != "both":
            raise ValueError(f"Unsupported Slope DSL position: {position}")

        change = operation.get("change", {})
        if change.get("attribute") == "grade":
            raise ValueError("Slope grade changes cannot target position=both.")
        magnitude = change.get("magnitude", {})
        if (
            change.get("mode") == "absolute"
            or change.get("action") == "set"
            or magnitude.get("unit") == "percent"
        ):
            return (0, 1)
        return (2,)

    def get_road_id(self, user_input):
        result = agent_trace.invoke_llm(
            self.chain_id,
            {"userInput": user_input},
            request_kind="road_id_extract",
            attempt=1,
            agent="slope",
            operation="slope",
        )
        if isinstance(result, str):
            result = json.loads(result)
        if "result" in result and isinstance(result["result"], dict):
            result = result["result"]
        road_id = result.get("road_id", -1)
        if road_id in (-1, None, "", "none"):
            raise ValueError("Slope road id extraction failed.")
        agent_trace.update_agent_step(road_id_output_json=result)
        return str(road_id).replace("road_", "")
