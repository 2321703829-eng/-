"""
Agent 返回结果分发器。

这个文件负责把 Manager 和各领域 Agent 的工具调用转换成项目里已经存在的
`width` / `slope` / `curve` 具体命令，然后继续复用现有 handler 执行修改。

期望接受的输入结构主要有下面两类：

1. Manager 拆分后的任务列表
[
    {"road_id": "road_3", "type_name": "width", "description": "把 3 号道路 -1 车道加宽 0.5 米"},
    {"road_id": "road_5", "type_name": "curve", "description": "把 5 号道路末端曲率减小一点"},
]

2. Agent 工具调用结构
{
    "agent": "width",
    "tool_calls": [
        {
            "name": "apply_width",
            "args": {"id": "3", "v": 0.5, "s": 1, "ms": 0, "sh": 1, "li": [-1]}
        }
    ]
}

RoadEdit DSL 只用于 Agent 工具规划和人工审核参考，不再作为可直接执行的载荷。

说明：
- `curve` 统一走 `CurveOptimizationService`。
- `CurveOptimizationService` 会根据道路当前 geometry 形态构造上下文，调用 curve 优化 Agent，
  再把优化后的控制点换算回现有系统支持的 `curve id=... gi=... h0/v0/h1/v1` 命令。
"""

import asyncio
import json
import math
import threading
from contextvars import copy_context

from handler.curve_optimizer import CurveOptimizationService
from handler.dsl_reference_converter import DslReferenceConverter
from handler.handlerCurve import handlerC
from handler.handlerSlope import handlerSl
from handler.handlerWidth import handlerW
from llm.curve_agent import normalize_curve_tool_calls
from llm.slope_agent import normalize_slope_plan_tool_calls, normalize_slope_tool_calls
from llm.width_agent import normalize_width_plan_tool_calls, normalize_width_tool_calls
from utils import agent_road_context, agent_trace
from utils.lambdas import getData
from xodrs.xodrParser import XParser


USE_MANUAL_ROAD_CONTEXT = False


def _sync_road_context_mode():
    return agent_road_context.set_use_manual_road_context(USE_MANUAL_ROAD_CONTEXT)


def _use_manual_road_context():
    return _sync_road_context_mode()


class AgentDispatcher:
    """负责把 Agent 工具调用转换为具体命令，并调用对应 handler 执行。"""

    def __init__(
        self,
        curve_agent=None,
        curve_optimizer_agent=None,
        width_agent=None,
        slope_agent=None,
    ):
        """
        初始化分发器。

        参数说明：
        - curve_agent: 保留兼容字段，历史上用于旧版曲线 Agent。
        - curve_optimizer_agent: 新版曲线优化 Agent，要求支持 `get_commands(...)`。
        - width_agent / slope_agent: 负责把自然语言解析成结构化 JSON。
        """
        _sync_road_context_mode()
        self.curve_agent = curve_agent
        self.curve_optimizer_agent = curve_optimizer_agent
        self.width_agent = width_agent
        self.slope_agent = slope_agent
        self.curve_optimizer = (
            CurveOptimizationService(curve_optimizer_agent)
            if curve_optimizer_agent is not None
            else None
        )
        self.reference_converter = DslReferenceConverter(
            curve_optimizer=self.curve_optimizer,
            width_road_info_getter=self._build_width_road_info,
            lane_avg_width_getter=self._get_lane_avg_width,
            slope_road_info_getter=self._build_slope_road_info,
        )

    def dispatch_split_commands(self, commands):
        """处理 Manager 拆分后的命令列表，并返回每条具体命令的执行结果。"""
        return self._run_coroutine_sync(self.dispatch_split_commands_async(commands))

    async def dispatch_split_commands_async(self, commands):
        """Run child-agent requests concurrently, then execute concrete edits in order."""
        _sync_road_context_mode()
        print(
            "Manager_split_commands",
            json.dumps(commands, ensure_ascii=False, indent=2),
        )

        if isinstance(commands, dict):
            if set(commands) != {"commands"}:
                raise ValueError("Manager payload must contain only the commands field.")
            commands = commands["commands"]
        if not isinstance(commands, list):
            raise ValueError("Manager commands must be a list.")

        prepared_items = await self._prepare_split_commands_async(commands or [])
        execution_results = []
        for item in prepared_items:
            index = item["index"]
            command = item["command"]
            agent_result = item["agent_result"]
            # 带 description 的拆分任务先调用对应 Agent 做结构化分析。
            command_type = self._trace_command_type(command)
            with agent_trace.trace_context(
                step_id=index, agent=command_type, operation=command_type
            ):
                try:
                    execution_results.extend(
                        self.dispatch_agent_result(agent_result, source_command=command)
                    )
                except Exception as exc:
                    agent_trace.update_agent_step(status="error", error=exc)
                    raise
        return execution_results

    async def _prepare_split_commands_async(self, commands):
        # Fit all real curve roads before parallel child agents read any context.
        # Width/slope must see the same fitted length and geometry as Curve.
        for command in commands:
            self._validate_manager_command(command)
        if not _use_manual_road_context():
            for road_id in dict.fromkeys(
                self._manager_road_id(command) for command in commands
                if self._trace_command_type(command) == "curve"
            ):
                handlerC.prepare_road_fit(road_id)
        tasks = []
        for index, command in enumerate(commands, start=1):
            self._validate_manager_command(command)
            tasks.append(
                asyncio.create_task(
                    self._prepare_agent_command_async(index, command)
                )
            )
        return sorted(await asyncio.gather(*tasks), key=lambda item: item["index"])

    def _validate_manager_command(self, command):
        if not isinstance(command, dict):
            raise ValueError(
                "Each Manager command must be an object."
            )
        command_type = command.get("type_name")
        expected_keys = {"type_name", "description"}
        if command_type in {"width", "slope", "curve"}:
            expected_keys.add("road_id")
        if set(command) != expected_keys:
            raise ValueError(
                "Executable Manager commands require only road_id, type_name and "
                "description."
            )
        if not isinstance(command["description"], str) or not command[
            "description"
        ].strip():
            raise ValueError("Manager command description must be a non-empty string.")
        if command_type in {"width", "slope", "curve"}:
            self._manager_road_id(command)

    async def _prepare_agent_command_async(self, index, command):
        command_type = self._trace_command_type(command)
        with agent_trace.trace_context(
            step_id=index, agent=command_type, operation=command_type
        ):
            try:
                agent_result = await asyncio.to_thread(
                    self._run_agent_for_command, command
                )
                if command_type == "curve":
                    print(
                        "curveAgentResult:",
                        json.dumps(agent_result, ensure_ascii=False, indent=2),
                    )
                return {
                    "index": index,
                    "command": command,
                    "agent_result": agent_result,
                }
            except Exception as exc:
                agent_trace.update_agent_step(status="error", error=exc)
                raise

    def _run_coroutine_sync(self, coroutine):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        result = {}
        context = copy_context()

        def runner():
            try:
                result["value"] = context.run(lambda: asyncio.run(coroutine))
            except BaseException as exc:
                result["error"] = exc

        thread = threading.Thread(target=runner)
        thread.start()
        thread.join()
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def dispatch_agent_result(self, agent_result, source_command=None):
        """把单个 Agent 返回值转成具体命令，并逐条执行。"""
        _sync_road_context_mode()
        concrete_commands = self.build_concrete_commands(
            agent_result, source_command=source_command
        )

        agent_trace.update_agent_step(
            internal_command=concrete_commands
        )
        for command in concrete_commands:
            agent_trace.record_projected_command_metric(command)
        results = []
        for command in concrete_commands:
            result = self._execute_command(command)
            results.append({"command": " ".join(command), "result": result})
        agent_trace.update_agent_step(handler_result_json=results, status="ok")
        return results

    def build_concrete_commands(self, agent_result, source_command=None):
        """
        把内部 Agent 工具计划转换成具体命令列表。

        这里是整个 dispatcher 的核心入口：
        - `tool_calls`：结合已验证 DSL 和 road context 复查后转换成项目命令
        - DSL-only `operation(s)`：明确拒绝，避免绕过 Agent 工具规划
        """
        _sync_road_context_mode()
        if not isinstance(agent_result, dict):
            raise ValueError("Agent execution result must be an internal plan object.")
        if any(
            key in agent_result
            for key in ("operations", "operation", "commands", "command")
        ):
            raise ValueError(
                "Raw commands and DSL-only payloads cannot be executed by AgentDispatcher."
            )
        required = {"agent", "dsl", "road_info", "tool_calls"}
        if not required.issubset(agent_result):
            raise ValueError(
                "Internal Agent plan requires agent, dsl, road_info and tool_calls."
            )
        agent_type = agent_result["agent"]
        tool_calls = self._normalize_and_revalidate_tool_calls(
            agent_type,
            agent_result["dsl"],
            agent_result["road_info"],
            agent_result["tool_calls"],
            agent_result.get("resolved_tool_calls"),
        )
        return self._tool_calls_to_commands(
            agent_type,
            tool_calls,
            source_command=source_command,
        )

    def _normalize_and_revalidate_tool_calls(
        self, agent_type, dsl, road_info, tool_calls, resolved_tool_calls=None
    ):
        if agent_type == "width":
            if self.width_agent is None:
                raise ValueError("Width Agent is required to validate its tool plan.")
            if resolved_tool_calls is None:
                calls = self.width_agent.resolve_execution_plan(
                    dsl, road_info, tool_calls
                )
            else:
                raw_calls = normalize_width_plan_tool_calls(tool_calls)
                self.width_agent._validate_plan_targets(dsl, road_info, raw_calls)
                calls = normalize_width_tool_calls(resolved_tool_calls)
            self.width_agent._validate_tool_targets(dsl, road_info, calls)
            return calls
        if agent_type == "slope":
            if self.slope_agent is None:
                raise ValueError("Slope Agent is required to validate its tool plan.")
            if resolved_tool_calls is None:
                calls = self.slope_agent.resolve_execution_plan(
                    dsl, road_info, tool_calls
                )
            else:
                raw_calls = normalize_slope_plan_tool_calls(tool_calls)
                self.slope_agent._validate_plan_targets(dsl, road_info, raw_calls)
                calls = normalize_slope_tool_calls(resolved_tool_calls)
            self.slope_agent._validate_tool_targets(dsl, road_info, calls)
            return calls
        if agent_type == "curve":
            calls = normalize_curve_tool_calls(tool_calls)
            if self.curve_optimizer_agent is None:
                raise ValueError("Curve Agent is required to validate its tool plan.")
            self.curve_optimizer_agent._validate_tool_targets(dsl, road_info, calls)
            return calls
        raise ValueError(f"Unsupported tool-call agent: {agent_type}")

    def _tool_calls_to_commands(self, agent_type, tool_calls, source_command=None):
        """Convert validated Agent tool calls into existing handler command tokens."""
        if not isinstance(tool_calls, list):
            raise ValueError("tool_calls must be a list.")

        if agent_type == "curve":
            if self.curve_optimizer is None:
                raise ValueError("Curve optimizer service is not configured.")
            user_request = ""
            if isinstance(source_command, dict):
                user_request = (
                    source_command.get("description")
                    or source_command.get("input")
                    or source_command.get("command")
                    or ""
                )
            command = self.curve_optimizer.build_curve_command_from_tool_calls(
                normalize_curve_tool_calls(tool_calls),
                user_request=user_request,
            )
            return [] if command is None else [command]

        if agent_type == "width":
            tool_calls = normalize_width_tool_calls(tool_calls)
        elif agent_type == "slope":
            tool_calls = normalize_slope_tool_calls(tool_calls)
        else:
            raise ValueError(f"Unsupported tool-call agent: {agent_type}")

        commands = []
        for call in tool_calls:
            name = call.get("name")
            arguments = call.get("args", {})
            if agent_type == "width" and name == "apply_width":
                command = [
                    "width",
                    f"id={arguments['id']}",
                    f"v={self._format_number(arguments['v'])}",
                    f"s={int(arguments['s'])}",
                    f"ms={int(arguments['ms'])}",
                    f"sh={int(arguments['sh'])}",
                ]
                lane_ids = arguments["li"]
                if lane_ids:
                    command.append(f"li={','.join(str(item) for item in lane_ids)}")
                commands.append(command)
                continue
            if agent_type == "slope" and name == "apply_slope":
                commands.append(
                    [
                        "slope",
                        f"id={arguments['id']}",
                        f"v={self._format_number(arguments['v'])}",
                        f"m={arguments['m']}",
                        f"mv={int(arguments['mv'])}",
                        f"ms={int(arguments['ms'])}",
                        f"sh={int(arguments['sh'])}",
                    ]
                )
                continue
            raise ValueError(f"Unsupported {agent_type} tool call: {name}")
        return commands

    def _run_agent_for_command(self, command):
        """
        根据拆分任务类型调用对应 Agent。

        width / slope / curve 都先生成并检查 DSL，再由同一领域 Agent 规划工具调用。
        """

        _sync_road_context_mode()
        command_type = command.get("type_name") or command.get("operation")
        description = (
            command.get("description") or command.get("input") or command.get("command")
        )

        if command_type == "curve":
            if self.curve_optimizer is None:
                raise ValueError("Curve optimizer agent is not configured.")
            road_id = self._manager_road_id(command)
            dsl = self.curve_optimizer.build_curve_dsl_payload(description, road_id)
            if not dsl.get("operations"):
                road_info = {"road_id": f"road_{road_id}"}
                tool_calls = self.curve_optimizer_agent.plan_execution(dsl, road_info)
                return self._agent_execution_result(
                    "curve", dsl, road_info, tool_calls, []
                )
            operation = self._first_dsl_operation(dsl, "curve")
            geometry_index = operation.get("gi")
            if not road_id or geometry_index is None:
                raise ValueError("Curve DSL requires road_id and gi before tool planning.")
            road_info = self.curve_optimizer.build_curve_tool_context(
                road_id, geometry_index
            )
            tool_calls = self.curve_optimizer_agent.plan_execution(dsl, road_info)
            reference_commands = self._build_reference_commands(
                dsl, source_command=command
            )
            return self._agent_execution_result(
                "curve", dsl, road_info, tool_calls, reference_commands
            )

        if command_type == "width":
            if self.width_agent is None:
                raise ValueError("Width agent is not configured.")
            road_id = self._manager_road_id(command)
            road_info = self._build_width_road_info(road_id)
            dsl = self._ensure_payload_road_id(
                self.width_agent.get_commands(description, road_info), road_id
            )
            tool_calls = self.width_agent.plan_execution(dsl, road_info)
            resolved_tool_calls = self.width_agent.resolve_execution_plan(
                dsl, road_info, tool_calls
            )
            reference_commands = self._build_reference_commands(
                dsl, source_command=command
            )
            return self._agent_execution_result(
                "width",
                dsl,
                road_info,
                tool_calls,
                reference_commands,
                resolved_tool_calls=resolved_tool_calls,
            )

        if command_type == "slope":
            if self.slope_agent is None:
                raise ValueError("Slope agent is not configured.")
            road_id = self._manager_road_id(command)
            road_info = self._build_slope_road_info(road_id)
            dsl_road_info = dict(road_info)
            dsl_road_info.pop("length", None)
            dsl = self._ensure_payload_road_id(
                self.slope_agent.get_commands(description, dsl_road_info), road_id
            )
            tool_calls = self.slope_agent.plan_execution(dsl, road_info)
            resolved_tool_calls = self.slope_agent.resolve_execution_plan(
                dsl, road_info, tool_calls
            )
            reference_commands = self._build_reference_commands(
                dsl, source_command=command
            )
            return self._agent_execution_result(
                "slope",
                dsl,
                road_info,
                tool_calls,
                reference_commands,
                resolved_tool_calls=resolved_tool_calls,
            )

        raise ValueError(f"Unsupported command type: {command_type}")

    def _agent_execution_result(
        self,
        agent_type,
        dsl,
        road_info,
        tool_calls,
        reference_commands,
        resolved_tool_calls=None,
    ):
        agent_trace.update_agent_step(
            tool_call_json=tool_calls,
            resolved_result_json=resolved_tool_calls,
            reference_command=reference_commands,
        )
        result = {
            "agent": agent_type,
            "dsl": dsl,
            "road_info": road_info,
            "tool_calls": tool_calls,
            "reference_command": [
                command if isinstance(command, str) else " ".join(command)
                for command in reference_commands
            ],
        }
        if resolved_tool_calls is not None:
            result["resolved_tool_calls"] = resolved_tool_calls
        return result

    def _first_dsl_operation(self, payload, operation_type):
        for operation in payload.get("operations", []):
            if operation.get("operation") == operation_type:
                return operation
        raise ValueError(f"DSL does not contain a {operation_type} operation.")

    def _build_reference_commands(self, dsl, source_command=None):
        """Build isolated legacy commands for human review only."""
        try:
            return self.reference_converter.convert(
                dsl, source_command=source_command
            )
        except Exception as exc:
            return [f"REFERENCE_ERROR: {type(exc).__name__}: {exc}"]

    def _manager_road_id(self, command):
        road_id = str(command.get("road_id", "")).replace("road_", "")
        if not self._is_concrete_road_id(road_id):
            raise ValueError("Manager command requires a concrete road_id.")
        return road_id

    def _ensure_payload_road_id(self, payload, road_id):
        """Ensure width/slope agent payload carries the LLM-extracted road id."""
        if not isinstance(payload, dict):
            return payload

        expected_road_id = str(road_id).replace("road_", "")
        if payload.get("operation") in ("width", "slope"):
            for key in ("id", "road_id"):
                value = payload.get(key)
                if value is None:
                    continue
                actual_road_id = str(value).replace("road_", "")
                if actual_road_id != expected_road_id:
                    raise ValueError(
                        f"{payload.get('operation')} DSL {key}={value} does not "
                        f"match extracted road id {road_id}."
                    )

        if (
            payload.get("operation") in ("width", "slope")
            and payload.get("id") is None
            and payload.get("road_id") is None
        ):
            payload = dict(payload)
            payload["id"] = str(road_id)
        if isinstance(payload.get("operations"), list):
            payload = dict(payload)
            payload["operations"] = [
                self._ensure_payload_road_id(operation, road_id)
                for operation in payload["operations"]
            ]
        return payload

    def _execute_command(self, command):
        """把具体命令路由到对应 handler。"""
        name = command[0].lower()
        if name == "width":
            return handlerW.handle(command)
        if name == "slope":
            return handlerSl.handle(command)
        if name == "curve":
            return handlerC.handle(command)
        raise ValueError(f"Unsupported concrete command: {command}")

    def _build_width_road_info(self, road_id):
        if _use_manual_road_context():
            return agent_road_context.get_manual_width_road_info(road_id)
        """为 width Agent 构造道路上下文信息，例如车道 id 与平均宽度。"""
        road = XParser.roads[str(road_id)]
        lane_ids = sorted(
            [
                int(lane.get("id"))
                for lane in road.find("lanes").findall(".//lane")
                if lane.get("id") != "0"
            ],
            reverse=True,
        )
        lane_avg_width = {}
        for lane_id in lane_ids:
            lane_avg_width[str(lane_id)] = round(
                self._get_lane_avg_width(road, str(lane_id)), 4
            )

        return {
            "road_id": f"road_{road_id}",
            "lane_ids": lane_ids,
            "lane_avg_width": lane_avg_width,
        }

    def _build_slope_road_info(self, road_id):

        if _use_manual_road_context():
            slope_info = agent_road_context.get_manual_slope_road_info(road_id)
            return {
                "road_id": slope_info["road_id"],
                "length": round(slope_info["length"], 4),
                "start_elevation": round(slope_info["start_elevation"], 4),
                "end_elevation": round(slope_info["end_elevation"], 4),
                "grade_degree": round(slope_info["grade_degree"], 4),
            }
        # """为 slope Agent 构造道路总长、起终点高程、当前坡度等上下文信息。"""
    
        road = XParser.roads[str(road_id)]
        length = getData(road, "length")
        # 真是道路数据
        start_elevation = self._get_elevation_at(road, 0.0)
        end_elevation = self._get_elevation_at(road, length)
        grade_fraction = (
            0.0 if length == 0 else (end_elevation - start_elevation) / length
        )
        grade_degree = math.degrees(math.atan(grade_fraction))

        return {
            "road_id": f"road_{road_id}",
            "length": round(length, 4),
            "start_elevation": round(start_elevation, 4),
            "end_elevation": round(end_elevation, 4),
            "grade_degree": round(grade_degree, 4),
        }


    def _get_lane_avg_width(self, road, lane_id):
        """计算某条 lane 在整条道路上的加权平均宽度。"""
        road_length = getData(road, "length")
        sections = road.find("lanes").findall("laneSection")
        total_length = 0.0
        weighted_width = 0.0

        for index, section in enumerate(sections):
            section_start = getData(section, "s")
            section_end = (
                road_length
                if index == len(sections) - 1
                else getData(sections[index + 1], "s")
            )
            section_length = max(0.0, section_end - section_start)
            lane = None
            for candidate in section.findall(".//lane"):
                if candidate.get("id") == lane_id:
                    lane = candidate
                    break
            if lane is None:
                continue

            widths = lane.findall("width")
            for width_index, width in enumerate(widths):
                offset_start = getData(width, "sOffset")
                offset_end = (
                    section_length
                    if width_index == len(widths) - 1
                    else getData(widths[width_index + 1], "sOffset")
                )
                segment_length = max(0.0, offset_end - offset_start)
                if segment_length == 0:
                    continue
                a = getData(width, "a")
                b = getData(width, "b")
                c = getData(width, "c")
                d = getData(width, "d")
                average_width = (
                    a
                    + b * segment_length / 2
                    + c * segment_length**2 / 3
                    + d * segment_length**3 / 4
                )
                total_length += segment_length
                weighted_width += average_width * segment_length

        if total_length == 0:
            return 0.0
        return weighted_width / total_length

    def _get_elevation_at(self, road, pos):
        """计算道路在给定 s 位置的高程。"""
        elevations = road.find("elevationProfile").findall("elevation")
        current = None
        for elevation in elevations:
            if getData(elevation, "s") <= pos:
                current = elevation
            else:
                break
        if current is None:
            return 0.0
        ds = pos - getData(current, "s")
        a = getData(current, "a")
        b = getData(current, "b")
        c = getData(current, "c")
        d = getData(current, "d")
        return a + b * ds + c * ds**2 + d * ds**3

    def _is_concrete_road_id(self, road_id):
        """判断 road id 是否为可直接查询 XODR 的真实数字 id。"""
        if road_id is None:
            return False
        return str(road_id).isdigit()

    def _trace_command_type(self, command):
        if isinstance(command, dict):
            return command.get("type_name") or command.get("agent") or ""
        return ""

    def _format_number(self, value):
        """把浮点数格式化为更紧凑的命令参数字符串。"""
        value = float(value)
        if abs(value) < 1e-8:
            value = 0.0
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text else "0"
