import json
import os
import threading
import time
import uuid
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

from utils import agent_road_context
from utils.lambdas import getData
from xodrs.xodrParser import XParser


TASK_HEADERS = [
    "run_id",
    "task_id",
    "user_input",
    "status",
    "started_at",
    "finished_at",
    "duration_ms",
    "error",
]

AGENT_STEP_HEADERS = [
    "run_id",
    "task_id",
    "step_id",
    "agent",
    "operation",
    "input_text",
    "road_context_json",
    "road_id_output_json",
    "llm_output_json",
    "tool_call_json",
    "resolved_result_json",
    "reference_command",
    "internal_command",
    "handler_result_json",
    "status",
    "error",
    "created_at",
    "updated_at",
]

ROAD_METRIC_HEADERS = [
    "run_id",
    "task_id",
    "step_id",
    "metric_id",
    "operation",
    "road_id",
    "internal_command",
    "before_json",
    "after_json",
    "delta_json",
    "status",
    "error",
    "created_at",
    "updated_at",
]

DSL_CHECK_HEADERS = [
    "run_id",
    "task_id",
    "step_id",
    "check_id",
    "agent",
    "operation",
    "attempt",
    "input_text",
    "road_context_json",
    "dsl_output_json",
    "check_request_json",
    "check_output_json",
    "passed",
    "problem_source",
    "check_result",
    "status",
    "error",
    "created_at",
    "updated_at",
]

LLM_REQUEST_HEADERS = [
    "run_id",
    "task_id",
    "step_id",
    "logical_request_id",
    "request_id",
    "request_index",
    "agent",
    "operation",
    "request_kind",
    "attempt",
    "model_name",
    "status",
    "duration_ms",
    "started_at",
    "finished_at",
    "error",
]

_RUN_ID = None
_DEFAULT_CONTEXT = {"task_id": "", "step_id": "", "agent": "", "operation": ""}
_CONTEXT = ContextVar("agent_trace_context", default=_DEFAULT_CONTEXT)
_TRACE_LOCK = threading.RLock()
_STANDALONE_STEP = 0
_TASK_START_TIMES = {}


def enabled():
    return bool(os.getenv("AGENT_TRACE_PATH"))


def set_run_id(run_id):
    global _RUN_ID
    _RUN_ID = str(run_id)


def get_run_id():
    global _RUN_ID
    if _RUN_ID is None:
        _RUN_ID = os.getenv("AGENT_TRACE_RUN_ID") or datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
    return _RUN_ID


def _current_context():
    return dict(_CONTEXT.get())


def set_context(task_id=None, step_id=None, agent=None, operation=None):
    context = _current_context()
    if task_id is not None:
        context["task_id"] = str(task_id)
    if step_id is not None:
        context["step_id"] = str(step_id)
    if agent is not None:
        context["agent"] = str(agent)
    if operation is not None:
        context["operation"] = str(operation)
    _CONTEXT.set(context)


@contextmanager
def trace_context(task_id=None, step_id=None, agent=None, operation=None):
    previous = _current_context()
    set_context(task_id=task_id, step_id=step_id, agent=agent, operation=operation)
    try:
        yield
    finally:
        _CONTEXT.set(previous)


def record_task(task_id, user_input=None, status=None, error=None):
    if not enabled():
        return
    now = _now()
    run_id = get_run_id()
    task_id = str(task_id)
    row = {
        "run_id": run_id,
        "task_id": task_id,
        "updated_at": now,
    }
    if user_input is not None:
        row["user_input"] = user_input
    if status is not None:
        row["status"] = status
        if status == "running":
            row["started_at"] = now
            with _TRACE_LOCK:
                _TASK_START_TIMES[(run_id, task_id)] = time.perf_counter_ns()
        if status in ("done", "error"):
            row["finished_at"] = now
            with _TRACE_LOCK:
                started = _TASK_START_TIMES.pop((run_id, task_id), None)
            if started is not None:
                row["duration_ms"] = round(
                    (time.perf_counter_ns() - started) / 1_000_000, 3
                )
    if error is not None:
        row["error"] = str(error)
    _append_event({"type": "task", "row": row})


def invoke_llm(chain, request, request_kind, attempt=1, agent=None, operation=None):
    if not enabled():
        return chain.invoke(request)
    callback = _LLMRequestTraceCallback(
        request_kind=request_kind,
        attempt=attempt,
        agent=agent,
        operation=operation,
    )
    return chain.invoke(request, config={"callbacks": [callback]})


def tool_calls_for_trace(response):
    calls = getattr(response, "tool_calls", None) or []
    traced = []
    for call in calls:
        if not isinstance(call, dict):
            traced.append(_make_jsonable(call))
            continue
        traced.append(
            {
                "name": call.get("name"),
                "args": _make_jsonable(call.get("args", {})),
            }
        )
    return traced


class _LLMRequestTraceCallback(BaseCallbackHandler):
    def __init__(self, request_kind, attempt, agent=None, operation=None):
        context = _current_context()
        self.trace_run_id = get_run_id()
        self.task_id = context.get("task_id", "")
        self.step_id = context.get("step_id", "")
        self.agent = agent or context.get("agent", "")
        self.operation = operation or context.get("operation", "")
        self.request_kind = str(request_kind)
        self.attempt = attempt
        self.logical_request_id = str(uuid.uuid4())
        self._starts = {}
        self._request_index = 0
        self._lock = threading.Lock()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._start_request(serialized, run_id, kwargs)

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self._start_request(serialized, run_id, kwargs)

    def on_llm_end(self, response, *, run_id, **kwargs):
        self._finish_request(run_id, "ok")

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._finish_request(run_id, "error", error)

    def _start_request(self, serialized, run_id, kwargs):
        request_id = str(run_id)
        with self._lock:
            if request_id in self._starts:
                return
            self._request_index += 1
            invocation = kwargs.get("invocation_params") or {}
            serialized_kwargs = (serialized or {}).get("kwargs") or {}
            model_name = (
                invocation.get("model_name")
                or invocation.get("model")
                or serialized_kwargs.get("model_name")
                or serialized_kwargs.get("model")
                or ""
            )
            self._starts[request_id] = {
                "started_ns": time.perf_counter_ns(),
                "started_at": _now_ms(),
                "request_index": self._request_index,
                "model_name": str(model_name),
            }

    def _finish_request(self, run_id, status, error=None):
        request_id = str(run_id)
        with self._lock:
            started = self._starts.pop(request_id, None)
        if started is None:
            return
        row = {
            "run_id": self.trace_run_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "logical_request_id": self.logical_request_id,
            "request_id": request_id,
            "request_index": started["request_index"],
            "agent": self.agent,
            "operation": self.operation,
            "request_kind": self.request_kind,
            "attempt": self.attempt,
            "model_name": started["model_name"],
            "status": status,
            "duration_ms": round(
                (time.perf_counter_ns() - started["started_ns"]) / 1_000_000, 3
            ),
            "started_at": started["started_at"],
            "finished_at": _now_ms(),
            "error": "" if error is None else str(error),
        }
        _append_event({"type": "llm_request", "row": row})


def record_agent_step(
    agent,
    operation=None,
    input_text=None,
    road_context_json=None,
    road_id_output_json=None,
    llm_output_json=None,
    tool_call_json=None,
    resolved_result_json=None,
    reference_command=None,
    internal_command=None,
    handler_result_json=None,
    status="ok",
    error=None,
):
    if not enabled():
        return
    row = _base_agent_row(agent, operation)
    if input_text is not None:
        row["input_text"] = input_text
    if road_context_json is not None:
        row["road_context_json"] = _json_text(road_context_json)
    if road_id_output_json is not None:
        row["road_id_output_json"] = _json_text(road_id_output_json)
    if llm_output_json is not None:
        row["llm_output_json"] = _json_text(llm_output_json)
    if tool_call_json is not None:
        row["tool_call_json"] = _json_text(tool_call_json)
    if resolved_result_json is not None:
        row["resolved_result_json"] = _json_text(resolved_result_json)
    if reference_command is not None:
        row["reference_command"] = _command_text(reference_command)
    if internal_command is not None:
        row["internal_command"] = _command_text(internal_command)
    if handler_result_json is not None:
        row["handler_result_json"] = _json_text(handler_result_json)
    if status is not None:
        row["status"] = status
    if error is not None:
        row["error"] = str(error)
    _append_event({"type": "agent_step", "row": row})


def update_agent_step(
    input_text=None,
    road_context_json=None,
    internal_command=None,
    handler_result_json=None,
    road_id_output_json=None,
    llm_output_json=None,
    tool_call_json=None,
    resolved_result_json=None,
    reference_command=None,
    status=None,
    error=None,
):
    if not enabled():
        return
    context = _current_context()
    row = _base_agent_row(context.get("agent"), context.get("operation"))
    if input_text is not None:
        row["input_text"] = input_text
    if road_context_json is not None:
        row["road_context_json"] = _json_text(road_context_json)
    if internal_command is not None:
        row["internal_command"] = _command_text(internal_command)
    if handler_result_json is not None:
        row["handler_result_json"] = _json_text(handler_result_json)
    if road_id_output_json is not None:
        row["road_id_output_json"] = _json_text(road_id_output_json)
    if llm_output_json is not None:
        row["llm_output_json"] = _json_text(llm_output_json)
    if tool_call_json is not None:
        row["tool_call_json"] = _json_text(tool_call_json)
    if resolved_result_json is not None:
        row["resolved_result_json"] = _json_text(resolved_result_json)
    if reference_command is not None:
        row["reference_command"] = _command_text(reference_command)
    if status is not None:
        row["status"] = status
    if error is not None:
        row["error"] = str(error)
    _append_event({"type": "agent_step", "row": row})


def record_road_metric(
    operation,
    road_id,
    internal_command=None,
    before_json=None,
    after_json=None,
    delta_json=None,
    status="ok",
    error=None,
    metric_id=None,
):
    if not enabled():
        return
    now = _now()
    context = _current_context()
    row = {
        "run_id": get_run_id(),
        "task_id": context.get("task_id", ""),
        "step_id": context.get("step_id", ""),
        "metric_id": metric_id
        or f"{context.get('task_id', '')}:{context.get('step_id', '')}:{operation}:{road_id}",
        "operation": operation,
        "road_id": str(road_id),
        "internal_command": _command_text(internal_command),
        "before_json": _json_text(before_json),
        "after_json": _json_text(after_json),
        "delta_json": _json_text(delta_json),
        "status": status,
        "error": "" if error is None else str(error),
        "created_at": now,
        "updated_at": now,
    }
    _append_event({"type": "road_metric", "row": row})


def record_dsl_check(
    agent,
    operation=None,
    attempt=None,
    input_text=None,
    road_context_json=None,
    dsl_output_json=None,
    check_request_json=None,
    check_output_json=None,
    status="ok",
    error=None,
):
    if not enabled():
        return
    now = _now()
    row = _base_agent_row(agent, operation)
    row["check_id"] = (
        f"{row.get('task_id', '')}:{row.get('step_id', '')}:"
        f"{agent}:{attempt or ''}"
    )
    row["attempt"] = "" if attempt is None else str(attempt)
    if input_text is not None:
        row["input_text"] = input_text
    if road_context_json is not None:
        row["road_context_json"] = _json_text(road_context_json)
    if dsl_output_json is not None:
        row["dsl_output_json"] = _json_text(dsl_output_json)
    if check_request_json is not None:
        row["check_request_json"] = _json_text(check_request_json)
    if check_output_json is not None:
        row["check_output_json"] = _json_text(check_output_json)
        if isinstance(check_output_json, dict):
            row["passed"] = str(check_output_json.get("passed", ""))
            row["problem_source"] = str(check_output_json.get("problem_source", ""))
            row["check_result"] = str(check_output_json.get("check_result", ""))
    if status is not None:
        row["status"] = status
    if error is not None:
        row["error"] = str(error)
    row["created_at"] = now
    row["updated_at"] = now
    _append_event({"type": "dsl_check", "row": row})


def capture_command_metric(command):
    parsed = _parse_command(command)
    operation = parsed["operation"]
    if operation not in ("width", "slope"):
        return None
    road_id = parsed["params"].get("id")
    if not road_id or not str(road_id).isdigit():
        return None
    if str(road_id) not in XParser.roads:
        return None
    if operation == "width":
        lane_ids = _parse_lane_ids(parsed["params"].get("li"))
        return {
            "operation": operation,
            "road_id": str(road_id),
            "metric": {
                "lane_ids": lane_ids,
                "avg_width": _width_reference_value(str(road_id), lane_ids),
            },
        }
    return {
        "operation": operation,
        "road_id": str(road_id),
        "metric": _slope_metric(str(road_id)),
    }


def record_command_metric(command, before, after, status="ok", error=None):
    if not enabled() or before is None:
        return
    parsed = _parse_command(command)
    operation = before["operation"]
    road_id = before["road_id"]
    after_metric = after["metric"] if after else None
    delta = _metric_delta(operation, before["metric"], after_metric)
    context = _current_context()
    record_road_metric(
        operation=operation,
        road_id=road_id,
        internal_command=" ".join(command),
        before_json=before["metric"],
        after_json=after_metric,
        delta_json=delta,
        status=status,
        error=error,
        metric_id=f"{context.get('task_id', '')}:{context.get('step_id', '')}:{operation}:{road_id}:{parsed['raw']}",
    )


def record_projected_command_metric(command):
    if not enabled():
        return
    parsed = _parse_command(command)
    operation = parsed["operation"]
    if operation not in ("width", "slope"):
        return
    road_id = parsed["params"].get("id")
    if not road_id or not str(road_id).isdigit():
        return
    if str(road_id) not in XParser.roads:
        return

    try:
        if operation == "width":
            before_metric, after_metric, delta = _project_width_metric(
                str(road_id), parsed
            )
        else:
            before_metric, after_metric, delta = _project_slope_metric(
                str(road_id), parsed
            )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        context = _current_context()
        record_road_metric(
            operation=operation,
            road_id=str(road_id),
            internal_command=parsed["raw"],
            status="error",
            error=error,
            metric_id=f"{context.get('task_id', '')}:{context.get('step_id', '')}:{operation}:{road_id}:projected:{parsed['raw']}",
        )
        raise RuntimeError(
            f"Projected road metric failed for command '{parsed['raw']}': {error}"
        ) from exc

    context = _current_context()
    record_road_metric(
        operation=operation,
        road_id=str(road_id),
        internal_command=parsed["raw"],
        before_json=before_metric,
        after_json=after_metric,
        delta_json=delta,
        status="ok",
        metric_id=f"{context.get('task_id', '')}:{context.get('step_id', '')}:{operation}:{road_id}:projected:{parsed['raw']}",
    )


def read_task_statuses(trace_path=None):
    events = _read_events(trace_path)
    tasks = {}
    for event in events:
        if event.get("type") != "task":
            continue
        row = event.get("row", {})
        tasks[str(row.get("task_id", ""))] = row.get("status", "")
    return tasks


def _base_agent_row(agent, operation=None):
    now = _now()
    context = _current_context()
    return {
        "run_id": get_run_id(),
        "task_id": context.get("task_id", ""),
        "step_id": _current_step_id(),
        "agent": agent or context.get("agent", ""),
        "operation": operation or context.get("operation", ""),
        "created_at": now,
        "updated_at": now,
    }


def _current_step_id():
    global _STANDALONE_STEP
    context = _current_context()
    step_id = context.get("step_id")
    if step_id != "":
        return step_id
    with _TRACE_LOCK:
        _STANDALONE_STEP += 1
        context["step_id"] = str(_STANDALONE_STEP)
    _CONTEXT.set(context)
    return context["step_id"]


def _append_event(event):
    with _TRACE_LOCK:
        path = _jsonl_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(_make_jsonable(event), ensure_ascii=False) + "\n")


def _read_events(trace_path=None):
    path = _jsonl_path(trace_path)
    if not path.exists():
        return []
    events = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            stripped = line.strip()
            if not stripped:
                continue
            events.append(json.loads(stripped))
    return events


def _jsonl_path(trace_path=None):
    path_value = trace_path or os.getenv("AGENT_TRACE_PATH")
    if not path_value:
        return Path("agent_trace.jsonl")
    return Path(path_value).with_suffix(".jsonl")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _now_ms():
    return datetime.now().isoformat(timespec="milliseconds")


def _json_text(value):
    if value in (None, ""):
        return ""
    return json.dumps(_make_jsonable(value), ensure_ascii=False, sort_keys=True)


def _command_text(command):
    if command in (None, ""):
        return ""
    if isinstance(command, str):
        return command
    if isinstance(command, list):
        if command and all(isinstance(item, str) for item in command):
            return " ".join(command)
        return "\n".join(_command_text(item) for item in command)
    return _json_text(command)


def _make_jsonable(value):
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(key): _make_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _parse_command(command):
    if isinstance(command, str):
        tokens = command.split()
    else:
        tokens = list(command)
    operation = tokens[0].lower() if tokens else ""
    params = {}
    for token in tokens[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            params[key] = value
    return {"operation": operation, "params": params, "raw": " ".join(tokens)}


def _safe_float(value):
    if value in (None, "", "none", "None"):
        return 0.0
    return float(value)


def _parse_lane_ids(value):
    if not value:
        return []
    return [int(item) for item in str(value).split(",") if item]


def _get_width_road_info(road_id):
    if agent_road_context.use_manual_road_context():
        return agent_road_context.get_manual_width_road_info(road_id)
    lane_ids = _road_lane_ids(road_id)
    return {
        "road_id": f"road_{road_id}",
        "lane_ids": lane_ids,
        "lane_avg_width": _lane_avg_width_map(road_id, lane_ids),
    }


def _get_slope_road_info(road_id):
    if agent_road_context.use_manual_road_context():
        return agent_road_context.get_manual_slope_road_info(road_id)
    return _slope_metric(road_id)


def _project_width_metric(road_id, parsed):
    width_info = _get_width_road_info(road_id)
    all_lane_ids = width_info["lane_ids"]
    target_lane_ids = _parse_lane_ids(parsed["params"].get("li")) or all_lane_ids
    lane_avg_width = width_info["lane_avg_width"]
    before_total = sum(lane_avg_width[str(lane_id)] for lane_id in target_lane_ids)
    per_lane_delta = _safe_float(parsed["params"].get("v"))
    delta_total = per_lane_delta * len(target_lane_ids)
    after_lane_avg_width = dict(lane_avg_width)
    delta_per_lane = {}
    for lane_id in target_lane_ids:
        key = str(lane_id)
        after_lane_avg_width[key] = lane_avg_width.get(key, 0.0) + per_lane_delta
        delta_per_lane[key] = per_lane_delta
    before_metric = {
        "metric_mode": "current",
        "road_id": str(road_id),
        "lane_ids_all": all_lane_ids,
        "target_lane_ids": target_lane_ids,
        "lane_avg_width": lane_avg_width,
        "target_avg_width_total": before_total,
    }
    after_metric = {
        "metric_mode": "projected",
        "road_id": str(road_id),
        "lane_ids_all": all_lane_ids,
        "target_lane_ids": target_lane_ids,
        "lane_avg_width": after_lane_avg_width,
        "target_avg_width_total": before_total + delta_total,
    }
    delta = {
        "source": "internal_command",
        "command": parsed["raw"],
        "delta_total_width": delta_total,
        "delta_per_target_lane": delta_per_lane,
    }
    return before_metric, after_metric, delta


def _project_slope_metric(road_id, parsed):
    import math

    before_metric = _get_slope_road_info(road_id)
    before_metric["metric_mode"] = "current"
    mode = parsed["params"].get("m", "angle")
    mv = int(parsed["params"].get("mv", 1))
    value = _safe_float(parsed["params"].get("v"))
    after_metric = dict(before_metric)
    after_metric["metric_mode"] = "projected"
    delta_start = 0.0
    delta_end = 0.0

    if mode == "add":
        if mv == 0:
            delta_start = value
        elif mv == 1:
            delta_end = value
        elif mv == 2:
            delta_start = value
            delta_end = value
        after_metric["start_elevation"] = before_metric["start_elevation"] + delta_start
        after_metric["end_elevation"] = before_metric["end_elevation"] + delta_end
    else:
        after_metric["projection_note"] = (
            "m=angle changes slope angle through handler geometry; projected "
            "endpoint elevations are not inferred without executing handler."
        )
        after_metric["command_angle_delta"] = value

    after_metric["height_delta"] = (
        after_metric["end_elevation"] - after_metric["start_elevation"]
    )
    after_metric["grade_fraction"] = (
        0.0
        if after_metric["length"] == 0
        else after_metric["height_delta"] / after_metric["length"]
    )
    after_metric["grade_degree"] = math.degrees(
        math.atan(after_metric["grade_fraction"])
    )
    delta = {
        "source": "internal_command",
        "command": parsed["raw"],
        "mode": mode,
        "mv": mv,
        "value": value,
        "delta_start_elevation": after_metric["start_elevation"]
        - before_metric["start_elevation"],
        "delta_end_elevation": after_metric["end_elevation"]
        - before_metric["end_elevation"],
        "delta_height_delta": after_metric["height_delta"]
        - before_metric["height_delta"],
        "delta_grade_degree": after_metric["grade_degree"]
        - before_metric["grade_degree"],
    }
    return before_metric, after_metric, delta


def _lane_avg_width_map(road_id, lane_ids):
    return {
        str(lane_id): _lane_avg_width(str(road_id), str(lane_id))
        for lane_id in lane_ids
    }


def _width_reference_value(road_id, lane_ids):
    target_lane_ids = lane_ids or _road_lane_ids(road_id)
    return sum(_lane_avg_width(road_id, str(lane_id)) for lane_id in target_lane_ids)


def _road_lane_ids(road_id):
    road = XParser.roads[str(road_id)]
    return [
        int(lane.get("id"))
        for lane in road.find("lanes").findall(".//lane")
        if lane.get("id") != "0"
    ]


def _lane_avg_width(road_id, lane_id):
    road = XParser.roads[str(road_id)]
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
    return 0.0 if total_length == 0 else weighted_width / total_length


def _slope_metric(road_id):
    road = XParser.roads[str(road_id)]
    length = getData(road, "length")
    start = _elevation_at(road, 0.0)
    end = _elevation_at(road, length)
    grade_fraction = 0.0 if length == 0 else (end - start) / length
    import math

    return {
        "road_id": str(road_id),
        "length": length,
        "start_elevation": start,
        "end_elevation": end,
        "height_delta": end - start,
        "grade_fraction": grade_fraction,
        "grade_degree": math.degrees(math.atan(grade_fraction)),
    }


def _elevation_at(road, pos):
    profile = road.find("elevationProfile")
    if profile is None:
        return 0.0
    elevations = profile.findall("elevation")
    current = None
    for elevation in elevations:
        if getData(elevation, "s") <= pos:
            current = elevation
        else:
            break
    if current is None:
        return 0.0
    ds = pos - getData(current, "s")
    return (
        getData(current, "a")
        + getData(current, "b") * ds
        + getData(current, "c") * ds**2
        + getData(current, "d") * ds**3
    )


def _metric_delta(operation, before, after):
    if before is None or after is None:
        return None
    if operation == "width":
        return {
            "delta_avg_width": after.get("avg_width", 0.0)
            - before.get("avg_width", 0.0)
        }
    if operation == "slope":
        return {
            "delta_start_elevation": after.get("start_elevation", 0.0)
            - before.get("start_elevation", 0.0),
            "delta_end_elevation": after.get("end_elevation", 0.0)
            - before.get("end_elevation", 0.0),
            "delta_grade_degree": after.get("grade_degree", 0.0)
            - before.get("grade_degree", 0.0),
        }
    return None
