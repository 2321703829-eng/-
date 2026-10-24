import json
from typing import Literal

from dotenv import load_dotenv
from langchain_classic.output_parsers import OutputFixingParser
from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
)
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from llm.agent_tools import (
    CURVE_TOOL_PLAN_PROMPT,
    ID_PROMPT,
    CURVE_DSL_CHECK_PROMPT,
    CURVE_DSL_PROMPT,
    escape_langchain_template_text,
    get_model,
)
from llm.dsl_format_validator import validate_dsl_format
from utils import agent_trace

load_dotenv(override=True)

# 最大重试次数
DSL_CHECK_MAX_ATTEMPTS = 3


class ChangeCurveTopologyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    road_id: str
    gi: int = Field(ge=0)
    target_shape: Literal["C-shape", "S-shape"]
    target_semantic: Literal["left", "right", "left_right", "right_left"]

    @field_validator("road_id")
    @classmethod
    def validate_road_id(cls, value):
        if not value.isdigit():
            raise ValueError("Curve tool road_id must be a concrete numeric road id.")
        return value

    @field_validator("gi", mode="before")
    @classmethod
    def reject_boolean_geometry_index(cls, value):
        if isinstance(value, bool):
            raise ValueError("Curve geometry index cannot be a boolean value.")
        return value

    @model_validator(mode="after")
    def validate_topology(self):
        allowed = {
            "C-shape": {"left", "right"},
            "S-shape": {"left_right", "right_left"},
        }
        if self.target_semantic not in allowed[self.target_shape]:
            raise ValueError(
                f"{self.target_semantic} is incompatible with {self.target_shape}"
            )
        return self


class ShiftCurveCentroidArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    road_id: str
    gi: int = Field(ge=0)
    side: Literal["start_side", "end_side"]
    mode: Literal["absolute", "relative"]
    action: Literal["move_to_start", "move_to_end", "set"]
    magnitude_kind: Literal["numeric", "qualitative"]
    value: float | None = Field(allow_inf_nan=False)
    level: Literal["slight", "default", "significant", "none"]
    unit: Literal["m", "percent", "none"]

    @field_validator("road_id")
    @classmethod
    def validate_road_id(cls, value):
        if not value.isdigit():
            raise ValueError("Curve tool road_id must be a concrete numeric road id.")
        return value

    @field_validator("gi", mode="before")
    @classmethod
    def reject_boolean_geometry_index(cls, value):
        if isinstance(value, bool):
            raise ValueError("Curve geometry index cannot be a boolean value.")
        return value

    @model_validator(mode="after")
    def validate_magnitude(self):
        if self.magnitude_kind == "numeric":
            if self.value is None or self.level != "none":
                raise ValueError("Numeric centroid magnitude requires value and level=none.")
        elif self.value is not None or self.level == "none" or self.unit != "none":
            raise ValueError(
                "Qualitative centroid magnitude requires value=None, a level and unit=none."
            )
        return self


class AdjustCurveCurvatureArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    road_id: str
    gi: int = Field(ge=0)
    side: Literal["start_side", "end_side"]
    value_type: Literal["curvature", "radius"]
    mode: Literal["absolute", "relative"]
    action: Literal["increase", "decrease", "set"]
    magnitude_kind: Literal["numeric", "qualitative"]
    value: float | None = Field(allow_inf_nan=False)
    level: Literal["slight", "default", "significant", "none"]
    unit: Literal["curvature", "m", "percent", "none"]

    @field_validator("road_id")
    @classmethod
    def validate_road_id(cls, value):
        if not value.isdigit():
            raise ValueError("Curve tool road_id must be a concrete numeric road id.")
        return value

    @field_validator("gi", mode="before")
    @classmethod
    def reject_boolean_geometry_index(cls, value):
        if isinstance(value, bool):
            raise ValueError("Curve geometry index cannot be a boolean value.")
        return value

    @model_validator(mode="after")
    def validate_magnitude(self):
        if self.magnitude_kind == "numeric":
            if self.value is None or self.level != "none":
                raise ValueError("Numeric curvature magnitude requires value and level=none.")
        elif self.value is not None or self.level == "none" or self.unit != "none":
            raise ValueError(
                "Qualitative curvature magnitude requires value=None, a level and unit=none."
            )
        return self


@tool("change_curve_topology", args_schema=ChangeCurveTopologyArgs)
def change_curve_topology_tool(road_id, gi, target_shape, target_semantic):
    """Change curve shape and turning semantic as one compatible topology."""
    return {
        "road_id": road_id,
        "gi": gi,
        "target_shape": target_shape,
        "target_semantic": target_semantic,
    }


@tool("shift_curve_centroid", args_schema=ShiftCurveCentroidArgs)
def shift_curve_centroid_tool(
    road_id, gi, side, mode, action, magnitude_kind, value, level, unit
):
    """Shift the curvature-weighted centroid on the road start or end side."""
    return {
        "road_id": road_id,
        "gi": gi,
        "side": side,
        "mode": mode,
        "action": action,
        "magnitude_kind": magnitude_kind,
        "value": value,
        "level": level,
        "unit": unit,
    }


@tool("adjust_curve_curvature", args_schema=AdjustCurveCurvatureArgs)
def adjust_curve_curvature_tool(
    road_id,
    gi,
    side,
    value_type,
    mode,
    action,
    magnitude_kind,
    value,
    level,
    unit,
):
    """Adjust curvature or radius on the road start or end side."""
    return {
        "road_id": road_id,
        "gi": gi,
        "side": side,
        "value_type": value_type,
        "mode": mode,
        "action": action,
        "magnitude_kind": magnitude_kind,
        "value": value,
        "level": level,
        "unit": unit,
    }


CURVE_TOOL_SCHEMAS = {
    "change_curve_topology": ChangeCurveTopologyArgs,
    "shift_curve_centroid": ShiftCurveCentroidArgs,
    "adjust_curve_curvature": AdjustCurveCurvatureArgs,
}


def normalize_curve_tool_calls(raw_calls):
    """Validate and normalize tool calls at both planning and execution boundaries."""
    if not isinstance(raw_calls, list):
        raise ValueError("Curve tool calls must be a list.")
    calls = []
    seen = set()
    target = None
    for call in raw_calls:
        if not isinstance(call, dict):
            raise ValueError("Each Curve tool call must be an object.")
        name = call.get("name")
        schema = CURVE_TOOL_SCHEMAS.get(name)
        if schema is None:
            raise ValueError(f"Unsupported curve tool: {name}")
        arguments = schema.model_validate(call.get("args", {})).model_dump()
        current_target = (arguments["road_id"], arguments["gi"])
        if target is None:
            target = current_target
        elif target != current_target:
            raise ValueError("All curve tool calls must target the same road and gi.")
        key = (name, arguments.get("side"))
        if key in seen:
            raise ValueError(f"Duplicate curve tool call: {key}")
        seen.add(key)
        calls.append({"name": name, "args": arguments})
    return calls


class CurveAgent:
    """Convert natural-language curve edit requests into Curve DSL JSON."""

    def __init__(self, model="", enable_dsl_check=True):
        self.model = get_model(model)
        self.enable_dsl_check = enable_dsl_check
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
            escape_langchain_template_text(CURVE_DSL_PROMPT)
            + """\nPlease ensure to directly output JSON data, without including the ```json markers, and without any prefixes or suffixes."""
        )

        human_prompt = HumanMessagePromptTemplate.from_template(
            """Road curve context: {originalInfo}\nPrevious failed DSL output for reference only: {previousDslOutput}\nPrevious DSL check result for reference only: {previousCheckResult}\nRetry instruction: {retryInstruction}\nExtract the curve modification DSL JSON according to the system prompt. Task description: {userInput}"""
        )

        chat_prompt = ChatPromptTemplate.from_messages([system_prompt, human_prompt])

        fixing_parser = OutputFixingParser.from_llm(parser=parser, llm=self.model)
        return chat_prompt | self.model | fixing_parser

    def init_check_chain(self):
        parser = JsonOutputParser()
        system_prompt = SystemMessagePromptTemplate.from_template(
            escape_langchain_template_text(CURVE_DSL_CHECK_PROMPT)
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
            escape_langchain_template_text(CURVE_TOOL_PLAN_PROMPT)
        )
        human_prompt = HumanMessagePromptTemplate.from_template(
            "validated_dsl: {dsl}\nroad_data: {roadInfo}"
        )
        chat_prompt = ChatPromptTemplate.from_messages([system_prompt, human_prompt])
        tools = [
            change_curve_topology_tool,
            shift_curve_centroid_tool,
            adjust_curve_curvature_tool,
        ]
        return chat_prompt | self.model.bind_tools(tools)

    def get_json_type(self, json_value):
        if isinstance(json_value, dict):
            return json_value
        if not isinstance(json_value, str):
            raise ValueError(
                f"Expected JSON string or dict, got {type(json_value)}: {json_value}"
            )
        try:
            return json.loads(json_value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON returned by curve agent: {json_value}"
            ) from exc

    def print_dsl_check_result(self, attempt, check_result):
        print(
            f"[DSL check][curve] round = {attempt}\n"
            f"                   passed = {check_result.get('passed')}\n"
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

    def get_commands(self, user_input, road_info=None):
        road_info = road_info or {}
        result = agent_trace.invoke_llm(
            self.chain,
            self._build_extract_request(user_input, road_info),
            request_kind="dsl_extract",
            attempt=1,
            agent="curve",
            operation="curve",
        )
        parsed = self.get_json_type(result)
        agent_trace.record_agent_step(
            agent="curve",
            operation="curve",
            input_text=user_input,
            road_context_json=road_info,
            llm_output_json=parsed,
            status="running" if self.enable_dsl_check else "ok",
        )
        if self.enable_dsl_check:
            last_check_result = None
            for attempt in range(1, DSL_CHECK_MAX_ATTEMPTS + 1):
                check_request = {
                    "userInput": user_input,
                    "originalInfo": road_info,
                    "dslOutput": json.dumps(parsed, ensure_ascii=False),
                }
                # 硬编码的格式检查
                format_check_result = validate_dsl_format("curve", parsed, road_info)
                # 检查未通过就记录-重新请求
                if format_check_result.get("passed") is not True:
                    self.print_dsl_check_result(attempt, format_check_result)
                    format_check_request = dict(check_request)
                    format_check_request["checker"] = "local_dsl_format"
                    agent_trace.record_dsl_check(
                        agent="curve",
                        operation="curve",
                        attempt=attempt,
                        input_text=user_input,
                        road_context_json=road_info,
                        dsl_output_json=parsed,
                        check_request_json=format_check_request,
                        check_output_json=format_check_result,
                        status="ok",
                    )
                    last_check_result = format_check_result
                    if attempt == DSL_CHECK_MAX_ATTEMPTS:
                        message = (
                            f"Curve DSL check failed: "
                            f"{format_check_result.get('check_result')}"
                        )
                        agent_trace.update_agent_step(status="error", error=message)
                        raise ValueError(message)
                    result = agent_trace.invoke_llm(
                        self.chain,
                        self._build_extract_request(
                            user_input, road_info, parsed, format_check_result
                        ),
                        request_kind="dsl_extract",
                        attempt=attempt + 1,
                        agent="curve",
                        operation="curve",
                    )
                    parsed = self.get_json_type(result)
                    agent_trace.update_agent_step(
                        llm_output_json=parsed,
                        status="running",
                    )
                    continue
                check_result = self.get_json_type(
                    agent_trace.invoke_llm(
                        self.check_chain,
                        check_request,
                        request_kind="dsl_check",
                        attempt=attempt,
                        agent="curve",
                        operation="curve",
                    )
                )
                self.print_dsl_check_result(attempt, check_result)
                agent_trace.record_dsl_check(
                    agent="curve",
                    operation="curve",
                    attempt=attempt,
                    input_text=user_input,
                    road_context_json=road_info,
                    dsl_output_json=parsed,
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
                        f"Curve DSL check failed: {check_result.get('check_result')}"
                    )
                    agent_trace.update_agent_step(status="error", error=message)
                    raise ValueError(message)
                result = agent_trace.invoke_llm(
                    self.chain,
                    self._build_extract_request(
                        user_input, road_info, parsed, check_result
                    ),
                    request_kind="dsl_extract",
                    attempt=attempt + 1,
                    agent="curve",
                    operation="curve",
                )
                parsed = self.get_json_type(result)
                agent_trace.update_agent_step(
                    llm_output_json=parsed,
                    status="running",
                )
            else:
                message = f"Curve DSL check failed: {last_check_result}"
                agent_trace.update_agent_step(status="error", error=message)
                raise ValueError(message)

            agent_trace.update_agent_step(status="ok")
        return parsed

    def plan_execution(self, dsl, road_info):
        requires_tools = self._dsl_requires_tools(dsl)
        request = {
            "dsl": json.dumps(dsl, ensure_ascii=False),
            "roadInfo": json.dumps(road_info, ensure_ascii=False),
        }
        response = agent_trace.invoke_llm(
            self.execution_chain,
            request,
            request_kind="tool_plan",
            attempt=1,
            agent="curve",
            operation="curve",
        )
        raw_calls = agent_trace.tool_calls_for_trace(response)
        agent_trace.update_agent_step(tool_call_json=raw_calls)
        try:
            calls = normalize_curve_tool_calls(raw_calls)
            self._validate_tool_targets(dsl, road_info, calls)
            if requires_tools and not calls:
                raise ValueError("Curve DSL changes require curve tool calls.")
            if not requires_tools and calls:
                raise ValueError("No-op Curve DSL must not produce tool calls.")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Curve tool planning failed: {exc}") from exc
        agent_trace.update_agent_step(tool_call_json=calls)
        return calls

    def _validate_tool_targets(self, dsl, road_info, calls):
        expected_targets = set()
        expected_tools = set()
        topology_rules = {}
        argument_rules = {}
        side_names = ("start_side", "end_side")
        context_road_id = str(road_info.get("road_id", "")).replace("road_", "")
        for operation in dsl.get("operations", []):
            if operation.get("operation") != "curve":
                continue
            target = (
                str(operation.get("road_id", "")).replace("road_", ""),
                operation.get("gi"),
            )
            if context_road_id and target[0] != context_road_id:
                raise ValueError(
                    "Curve DSL road_id does not match road_data."
                )
            expected_targets.add(target)
            shape = operation.get("shape", {}).get("target")
            semantic = operation.get("semantic", {})
            if shape != "keep" or semantic.get("semantic_change") is True:
                expected_tools.add(("change_curve_topology", None))
                topology_rules[target] = self._expected_topology(
                    operation, road_info
                )
            for index, modification in enumerate(operation.get("modification", [])):
                side = side_names[index]
                centroid_change = modification.get("centroid_shift", {}).get(
                    "change", {}
                )
                if centroid_change.get("action") != "none":
                    key = ("shift_curve_centroid", side)
                    expected_tools.add(key)
                    argument_rules[key] = self._expected_change_arguments(
                        centroid_change
                    )
                curvature = modification.get("curvature_peak", {})
                curvature_change = curvature.get("change", {})
                if curvature_change.get("action") != "none":
                    key = ("adjust_curve_curvature", side)
                    expected_tools.add(key)
                    argument_rules[key] = {
                        "value_type": curvature.get("value_type"),
                        **self._expected_change_arguments(curvature_change),
                    }

        actual_tools = {
            (call["name"], call["args"].get("side")) for call in calls
        }
        if actual_tools != expected_tools:
            raise ValueError(
                "Curve tool calls must cover exactly the non-no-op DSL changes."
            )

        for call in calls:
            arguments = call["args"]
            target = (arguments["road_id"], arguments["gi"])
            if expected_targets and target not in expected_targets:
                raise ValueError(
                    "Curve tool road_id/gi does not match the validated DSL."
                )
            if call["name"] == "change_curve_topology":
                shape, semantic = topology_rules[target]
                if arguments["target_shape"] != shape:
                    raise ValueError(
                        "Curve topology target_shape does not match the validated DSL."
                    )
                if arguments["target_semantic"] != semantic:
                    raise ValueError(
                        "Curve topology target_semantic does not match the validated DSL."
                    )
                continue

            key = (call["name"], arguments.get("side"))
            expected_arguments = argument_rules[key]
            if any(
                arguments.get(field) != expected_value
                for field, expected_value in expected_arguments.items()
            ):
                raise ValueError(
                    f"Curve {call['name']} parameters do not match the validated DSL."
                )

    def _expected_topology(self, operation, road_info):
        shape = operation.get("shape", {}).get("target")
        target_shape = shape if shape != "keep" else road_info.get("current_shape")
        if target_shape not in ("C-shape", "S-shape"):
            raise ValueError("Curve topology change requires a known C/S current shape.")

        semantic = operation.get("semantic", {})
        semantic_target = semantic.get("target")
        if semantic.get("semantic_change") is True and semantic_target not in (
            None,
            "keep",
        ):
            return target_shape, semantic_target

        current_semantic = road_info.get("semantic")
        if current_semantic in ("left", "left_right"):
            start_direction = "left"
        elif current_semantic in ("right", "right_left"):
            start_direction = "right"
        else:
            raise ValueError(
                "Curve shape-only change cannot infer the current start-side direction."
            )
        if target_shape == "C-shape":
            return target_shape, start_direction
        return target_shape, (
            "left_right" if start_direction == "left" else "right_left"
        )

    def _expected_change_arguments(self, change):
        magnitude = change.get("magnitude", {})
        value = magnitude.get("value")
        if value in ("none", "None", ""):
            value = None
        elif value is not None:
            value = float(value)
        return {
            "mode": change.get("mode"),
            "action": change.get("action"),
            "magnitude_kind": magnitude.get("kind"),
            "value": value,
            "level": magnitude.get("level"),
            "unit": magnitude.get("unit"),
        }

    def _dsl_requires_tools(self, dsl):
        for operation in dsl.get("operations", []):
            if operation.get("shape", {}).get("target") != "keep":
                return True
            if operation.get("semantic", {}).get("semantic_change") is True:
                return True
            for modification in operation.get("modification", []):
                centroid_action = (
                    modification.get("centroid_shift", {})
                    .get("change", {})
                    .get("action")
                )
                curvature_action = (
                    modification.get("curvature_peak", {})
                    .get("change", {})
                    .get("action")
                )
                if centroid_action not in (None, "none"):
                    return True
                if curvature_action not in (None, "none"):
                    return True
        return False

    def get_road_id(self, user_input):
        result = self.get_json_type(
            agent_trace.invoke_llm(
                self.chain_id,
                {"userInput": user_input},
                request_kind="road_id_extract",
                attempt=1,
                agent="curve",
                operation="curve",
            )
        )
        if "result" in result and isinstance(result["result"], dict):
            result = result["result"]
        road_id = result.get("road_id", -1)
        if road_id in (-1, None, "", "none"):
            raise ValueError("Curve road id extraction failed.")
        agent_trace.update_agent_step(road_id_output_json=result)
        return str(road_id).replace("road_", "")
