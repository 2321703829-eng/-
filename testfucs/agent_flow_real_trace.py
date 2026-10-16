import argparse
import copy
import hashlib
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values
from openai import AuthenticationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import utils.path as path
from handler import agent_dispatcher as agent_dispatcher_module
from handler.curve_optimizer import CurveOptimizationService
from jsons.jsonParser import JParser
from llm.curve_agent import CurveAgent
from llm.agent_tools import init_GPT_XZ
from llm.dsl_format_validator import validate_dsl_format
from llm.manager_agent import ManagerAgent
from llm.single_agent import SingleAgent
from llm.slope_agent import SlopeAgent
from llm.width_agent import WidthAgent
from testfucs import test_data_store
from testfucs.td import all_types_small_cases,slope_small_cases,failture_cases,very_difficult_cases
from utils import agent_road_context, agent_trace
from xodrs.xodrParser import XParser


DEFAULT_XODR = "CHN_Sha-14_1_T-1-case-test"
SINGLE_AGENT_XODR = "ARG_Carcarana-7_1_I-1-1"
ALIGNED_REAL_CONTEXT = "aligned-real"
DEFAULT_ALIGNED_XODR_DIRECTORY = Path(path.xodrPath)
DEFAULT_ALIGNED_JSON_DIRECTORY = Path(path.jsonPath)

# DEFAULT_TASKS = all_types_small_cases
# DEFAULT_TASKS = failture_cases
DEFAULT_TASKS = very_difficult_cases


# DEFAULT_TASKS = slope_small_cases
# DEFAULT_TASKS = small_curve_cases
# DEFAULT_TASKS = single_slope_cases
REQUEST_TEST_DATABASE = test_data_store.DEFAULT_DATABASE_PATH
DSL_ANSWER_COLUMNS = {
    "width": "width answer",
    "slope": "slope answer",
    "curve": "curve answer",
}
DSL_EVAL_OUTPUT_DIRECTORY = PROJECT_ROOT / "testfucs" / "run_data"
_DSL_EVAL_LLM_CAPTURE_LOCK = threading.Lock()
FAKE_ROAD_TYPES_BY_ID = {
    "5": ("上坡路", "C型-右转弯"),
    "6": ("上坡路", "S型-先右后左"),
    "15": ("平路", "C型-右转弯"),
    "16": ("平路", "S型-先右后左"),
    "25": ("下坡路", "C型-右转弯"),
    "26": ("下坡路", "S型-先右后左"),
}


def load_tasks(tasks_file):
    if not tasks_file:
        return DEFAULT_TASKS
    text = Path(tasks_file).read_text(encoding="utf-8")
    if tasks_file.endswith(".json"):
        data = json.loads(text)
        return data["tasks"] if isinstance(data, dict) else data
    return [line.strip() for line in text.splitlines() if line.strip()]


def setup_paths(
    xodr_name,
    json_name=None,
    xodr_directory=None,
    json_directory=None,
):
    xodr_directory = xodr_directory or PROJECT_ROOT / "data" / "OpenSCENARIO"
    json_directory = json_directory or PROJECT_ROOT / "data" / "LGSVL-VSE"
    path.xodrPath = str(Path(xodr_directory)) + os.sep
    path.jsonPath = str(Path(json_directory)) + os.sep
    path.savePath = str(PROJECT_ROOT / "data" / "output") + os.sep
    path.editSaveName(f"agent_trace_{xodr_name}")

    if not XParser.openXodr(xodr_name):
        raise RuntimeError(f"Failed to open xodr: {xodr_name}")
    json_to_open = json_name or xodr_name
    if not JParser.readJson(json_to_open):
        raise RuntimeError(f"Failed to open json: {json_to_open}")


def load_request_cases(database_path=REQUEST_TEST_DATABASE):
    database_path = Path(database_path)
    if not database_path.exists():
        return {}
    return test_data_store.load_request_case_map(database_path)


def load_real_road_alignments(database_path=REQUEST_TEST_DATABASE):
    database_path = Path(database_path)
    if not database_path.exists():
        raise FileNotFoundError(f"Request database not found: {database_path}")
    return test_data_store.load_test_case_road_alignment_map(database_path)


def _extract_answer_dsl(cell_value, agent_type):
    if cell_value is None or not str(cell_value).strip():
        raise ValueError("DSL answer cell is empty.")
    parsed = json.loads(cell_value) if isinstance(cell_value, str) else cell_value
    records = parsed if isinstance(parsed, list) else [parsed]
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        payload = record.get("value", record)
        if not isinstance(payload, dict):
            continue
        operations = payload.get("operations")
        if isinstance(operations, list) and any(
            isinstance(operation, dict)
            and operation.get("operation") == agent_type
            for operation in operations
        ):
            return copy.deepcopy(payload)
    raise ValueError(f"No {agent_type} DSL operation found in answer cell.")


def _dsl_road_id(dsl, agent_type):
    road_ids = {
        str(operation.get("road_id", "")).replace("road_", "")
        for operation in dsl.get("operations", [])
        if isinstance(operation, dict) and operation.get("operation") == agent_type
    }
    road_ids.discard("")
    if len(road_ids) != 1 or not next(iter(road_ids), "").isdigit():
        raise ValueError(f"{agent_type} DSL requires exactly one concrete road_id.")
    return next(iter(road_ids))


def _dsl_requires_tool_plan(dsl, agent_type):
    for operation in dsl.get("operations", []):
        if not isinstance(operation, dict) or operation.get("operation") != agent_type:
            continue
        if agent_type in {"width", "slope"}:
            if operation.get("change", {}).get("action") != "none":
                return True
            continue
        if operation.get("shape", {}).get("target") != "keep":
            return True
        if operation.get("semantic", {}).get("semantic_change") is True:
            return True
        for modification in operation.get("modification", []):
            if modification.get("centroid_shift", {}).get("change", {}).get(
                "action"
            ) != "none":
                return True
            if modification.get("curvature_peak", {}).get("change", {}).get(
                "action"
            ) != "none":
                return True
    return False


def _dsl_case_signature(dsl, agent_type):
    signatures = []
    for operation in dsl.get("operations", []):
        if not isinstance(operation, dict) or operation.get("operation") != agent_type:
            continue
        if agent_type == "width":
            change = operation.get("change", {})
            magnitude = change.get("magnitude", {})
            signatures.append(
                (
                    "road" if operation.get("lane_ids") == "none" else "lane",
                    change.get("action"),
                    change.get("mode"),
                    magnitude.get("unit"),
                )
            )
        elif agent_type == "slope":
            change = operation.get("change", {})
            magnitude = change.get("magnitude", {})
            signatures.append(
                (
                    change.get("attribute"),
                    operation.get("position"),
                    change.get("action"),
                    change.get("mode"),
                    magnitude.get("unit"),
                )
            )
        else:
            features = []
            if operation.get("shape", {}).get("target") != "keep" or operation.get(
                "semantic", {}
            ).get("semantic_change") is True:
                features.append("topology")
            for modification in operation.get("modification", []):
                if modification.get("centroid_shift", {}).get("change", {}).get(
                    "action"
                ) != "none":
                    features.append("centroid")
                if modification.get("curvature_peak", {}).get("change", {}).get(
                    "action"
                ) != "none":
                    features.append("curvature")
            signatures.append(tuple(sorted(set(features))))
    return tuple(signatures)


def load_dsl_tool_plan_cases(database_path=REQUEST_TEST_DATABASE):
    connection = test_data_store.connect_database(database_path)
    try:
        cases = {agent_type: [] for agent_type in DSL_ANSWER_COLUMNS}
        for row_data in test_data_store.list_request_rows(connection):
            user_input = row_data.get("输入")
            if user_input is None:
                continue
            for agent_type in DSL_ANSWER_COLUMNS:
                try:
                    entries = test_data_store.list_dsl_answer_entries(
                        connection, row_data["case_id"], agent_type
                    )
                    approved = [
                        entry
                        for entry in entries
                        if entry.get("review_status") == "approved"
                    ]
                    candidates = approved or entries
                    if not candidates:
                        continue
                    dsl = copy.deepcopy(candidates[-1]["value"])
                    road_id = _dsl_road_id(dsl, agent_type)
                    input_road_ids = _extract_road_ids_from_user_input(user_input)
                    if input_road_ids != [road_id]:
                        continue
                    width_info = None
                    if agent_type == "width":
                        width_info = copy.deepcopy(agent_road_context.FAKE_WIDTH_INFO)
                        width_info["road_id"] = f"road_{road_id}"
                    validation = validate_dsl_format(agent_type, dsl, width_info)
                    if validation.get("passed") is not True:
                        continue
                    if not _dsl_requires_tool_plan(dsl, agent_type):
                        continue
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                cases[agent_type].append(
                    {
                        "source_case_id": row_data["case_id"],
                        "source_row": row_data["source_row"],
                        "agent_type": agent_type,
                        "user_input": str(user_input),
                        "road_id": road_id,
                        "dsl": dsl,
                        "row_data": row_data,
                        "signature": _dsl_case_signature(dsl, agent_type),
                    }
                )
        return cases
    finally:
        connection.close()


def select_dsl_tool_plan_cases(cases_by_agent, sample_count=15, seed=20260823):
    if sample_count <= 0 or sample_count % len(DSL_ANSWER_COLUMNS) != 0:
        raise ValueError("sample_count must be positive and divisible by 3.")
    per_agent = sample_count // len(DSL_ANSWER_COLUMNS)
    selected = []
    for agent_index, agent_type in enumerate(DSL_ANSWER_COLUMNS):
        rng = random.Random(seed + agent_index)
        pool = list(cases_by_agent.get(agent_type, []))
        rng.shuffle(pool)
        required_predicates = {
            "width": [
                lambda case: any(part[0] == "road" for part in case["signature"]),
                lambda case: any(part[-1] == "percent" for part in case["signature"]),
                lambda case: any(part[-1] == "none" for part in case["signature"]),
            ],
            "slope": [
                lambda case: any(
                    part[0] == "elevation_delta" for part in case["signature"]
                ),
                lambda case: any(part[1] == "both" for part in case["signature"]),
                lambda case: any(part[-1] == "percent" for part in case["signature"]),
            ],
            "curve": [
                lambda case: any(
                    "topology" in part for part in case["signature"]
                ),
                lambda case: any(
                    "centroid" in part for part in case["signature"]
                ),
                lambda case: any(
                    "curvature" in part for part in case["signature"]
                ),
            ],
        }
        agent_cases = []
        for predicate in required_predicates[agent_type]:
            match = next(
                (case for case in pool if case not in agent_cases and predicate(case)),
                None,
            )
            if match is not None:
                agent_cases.append(match)

        groups = {}
        for case in pool:
            if case in agent_cases:
                continue
            groups.setdefault(case["signature"], []).append(case)
        signatures = sorted(groups, key=repr)
        rng.shuffle(signatures)
        for group in groups.values():
            rng.shuffle(group)

        while signatures and len(agent_cases) < per_agent:
            remaining = []
            for signature in signatures:
                group = groups[signature]
                if group and len(agent_cases) < per_agent:
                    agent_cases.append(group.pop())
                if group:
                    remaining.append(signature)
            signatures = remaining
        if len(agent_cases) != per_agent:
            raise ValueError(
                f"Not enough eligible {agent_type} cases: "
                f"required {per_agent}, got {len(agent_cases)}."
            )
        selected.extend(agent_cases)
    return selected


def _resolve_dsl_eval_jsonl_path(path_value, must_exist=False):
    path = Path(path_value).resolve()
    try:
        path.relative_to(DSL_EVAL_OUTPUT_DIRECTORY.resolve())
    except ValueError as exc:
        raise ValueError("Evaluation JSONL paths must stay in testfucs/run_data.") from exc
    if path.suffix.lower() != ".jsonl":
        raise ValueError("Evaluation data files must use the .jsonl extension.")
    if must_exist and not path.is_file():
        raise ValueError(f"Evaluation data file does not exist: {path.name}")
    if not must_exist and path.exists():
        raise ValueError(f"Evaluation output already exists: {path.name}")
    return path


def _resolve_dsl_eval_resume_path(path_value):
    path = Path(path_value).resolve()
    try:
        path.relative_to(DSL_EVAL_OUTPUT_DIRECTORY.resolve())
    except ValueError as exc:
        raise ValueError(
            "Evaluation resume paths must stay in testfucs/run_data."
        ) from exc
    if path.suffix.lower() not in {".jsonl", ".json"}:
        raise ValueError("Evaluation resume files must use .jsonl or legacy .json.")
    if not path.is_file():
        raise ValueError(f"Evaluation data file does not exist: {path.name}")
    return path


def _load_dsl_eval_payload(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))

    metadata = None
    cases = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid evaluation JSONL at line {line_number}."
                ) from exc
            if not isinstance(event, dict):
                raise ValueError(
                    f"Evaluation JSONL line {line_number} must be an object."
                )
            if event.get("type") == "evaluation_metadata":
                metadata = event.get("row", {})
            elif event.get("type") == "tool_plan_case":
                cases.append(event.get("row", {}))
    if metadata is None:
        raise ValueError("Evaluation JSONL is missing evaluation_metadata.")
    return {"metadata": metadata, "cases": cases}


def _write_dsl_eval_jsonl(path, payload):
    events = [
        {"type": "evaluation_metadata", "row": payload["metadata"]},
        *(
            {"type": "tool_plan_case", "row": case}
            for case in payload["cases"]
        ),
    ]
    with Path(path).open("w", encoding="utf-8", newline="\n") as file:
        for event in events:
            file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")


def _project_display_path(path_value):
    path = Path(path_value).resolve()
    try:
        return str(path.relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return path.name


def _normalize_request_key(user_input):
    return str(user_input).strip()


def _split_road_types(value):
    if value is None:
        return []
    return [item.strip() for item in str(value).splitlines() if item.strip()]


def _first_road_type(values):
    for value in values:
        return value
    return None


def _has_request_case_context_data(request_case):
    if request_case is None:
        return False
    return any(
        str(request_case.get(field) or "").strip()
        for field in ("算子", "slope", "curve")
    )


def _build_fake_context_from_road_type(road_type):
    from testfucs.LLMjudge import get_fake_road_context

    return copy.deepcopy(get_fake_road_context(road_type)) if road_type else None


def _prepare_manual_task_context(user_input, request_cases):
    matched_case = request_cases.get(_normalize_request_key(user_input))
    width_info = copy.deepcopy(agent_road_context.FAKE_WIDTH_INFO)

    if _has_request_case_context_data(matched_case):
        slope_type = _first_road_type(_split_road_types(matched_case.get("slope")))
        curve_type = _first_road_type(_split_road_types(matched_case.get("curve")))
        agent_road_context.set_manual_task_context(
            width_info=width_info,
            slope_info=_build_fake_context_from_road_type(slope_type),
            curve_info=_build_fake_context_from_road_type(curve_type),
        )
        return {
            "source": "workbook",
            "slope_type": slope_type,
            "curve_type": curve_type,
        }

    road_ids = _extract_road_ids_from_user_input(user_input)
    if not road_ids:
        raise ValueError(
            "No road id found for fake road context. "
            f"Expected one of: {', '.join(FAKE_ROAD_TYPES_BY_ID)}."
        )

    unsupported_road_ids = [
        road_id for road_id in road_ids if road_id not in FAKE_ROAD_TYPES_BY_ID
    ]
    if unsupported_road_ids:
        raise ValueError(
            "Unsupported road id for fake road context: "
            f"{', '.join(unsupported_road_ids)}. "
            f"Expected one of: {', '.join(FAKE_ROAD_TYPES_BY_ID)}."
        )

    slope_info_by_road = {}
    curve_info_by_road = {}
    road_types_by_id = {}
    for road_id in road_ids:
        slope_type, curve_type = FAKE_ROAD_TYPES_BY_ID[road_id]
        slope_info_by_road[road_id] = _build_fake_context_from_road_type(slope_type)
        curve_info_by_road[road_id] = _build_fake_context_from_road_type(curve_type)
        road_types_by_id[road_id] = {
            "slope_type": slope_type,
            "curve_type": curve_type,
        }

    agent_road_context.set_manual_task_context(
        width_info=width_info,
        slope_info_by_road=slope_info_by_road,
        curve_info_by_road=curve_info_by_road,
    )
    return {
        "source": "road_id_rule",
        "road_types_by_id": road_types_by_id,
    }


def _extract_road_ids_from_user_input(user_input):
    text = str(user_input)
    road_ids = []
    road_list_pattern = (
        r"\broads?\s+((?:\d+\s*(?:,|and|&)\s*)+\d+)\b"
    )
    for match in re.finditer(road_list_pattern, text, flags=re.IGNORECASE):
        for road_id in re.findall(r"\d+", match.group(1)):
            if road_id not in road_ids:
                road_ids.append(road_id)

    patterns = [
        r"\broad(?:\s*no\.?|\s*#)?[_\s-]*(\d+)\b",
        r"(\d+)\s*号\s*(?:道路|路)",
        r"(?:道路|路)\s*(\d+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            road_id = match.group(1)
            if road_id not in road_ids:
                road_ids.append(road_id)
    return road_ids


def _build_single_curve_context(curve_context_builder, road_id, user_input):
    try:
        geometry_index = curve_context_builder.select_geometry_index(
            str(road_id), user_input
        )
        control_points, curve_info = curve_context_builder._build_curve_context(
            str(road_id), geometry_index
        )
        return curve_context_builder._build_curve_llm_context(
            control_points, curve_info
        )
    except Exception as exc:
        return {"road_id": f"road_{road_id}", "error": str(exc)}


def _build_real_single_agent_context(dispatcher, curve_context_builder):
    previous_dispatcher_mode = agent_dispatcher_module.USE_MANUAL_ROAD_CONTEXT
    previous_context_mode = agent_road_context.use_manual_road_context()
    agent_dispatcher_module.USE_MANUAL_ROAD_CONTEXT = False
    agent_road_context.set_use_manual_road_context(False)
    try:
        roads = []
        for road_id in sorted(XParser.roads, key=lambda value: int(value)):
            curve_info = _build_single_curve_context(
                curve_context_builder, road_id, ""
            )
            if "error" in curve_info:
                raise ValueError(
                    f"Failed to build curve context for road_{road_id}: "
                    f"{curve_info['error']}"
                )
            curve_info["gi"] = 0
            roads.append(
                {
                    "road_id": f"road_{road_id}",
                    "width": dispatcher._build_width_road_info(road_id),
                    "slope": dispatcher._build_slope_road_info(road_id),
                    "curve": curve_info,
                }
            )
        return {"roads": roads}
    finally:
        agent_dispatcher_module.USE_MANUAL_ROAD_CONTEXT = previous_dispatcher_mode
        agent_road_context.set_use_manual_road_context(previous_context_mode)


def _build_single_agent_context(
    base_context,
    replace_types,
    dispatcher,
    curve_context_builder,
):
    context = copy.deepcopy(base_context)
    road_21 = next(
        (road for road in context["roads"] if road.get("road_id") == "road_21"),
        None,
    )
    if road_21 is None:
        raise ValueError("The real single-agent road list does not contain road_21.")

    replace_types = set(replace_types or [])
    if "width" in replace_types:
        road_21["width"] = dispatcher._build_width_road_info("21")
    if "slope" in replace_types:
        road_21["slope"] = dispatcher._build_slope_road_info("21")
    if "curve" in replace_types:
        curve_info = _build_single_curve_context(
            curve_context_builder, "21", ""
        )
        if "error" in curve_info:
            raise ValueError(
                f"Failed to build simulated road_21 curve context: "
                f"{curve_info['error']}"
            )
        curve_info["gi"] = 0
        road_21["curve"] = curve_info
    return context


def _single_case_operator_types(user_input, request_cases):
    request_case = request_cases.get(_normalize_request_key(user_input))
    if not request_case:
        return []
    return test_data_store._parse_operator_types(request_case.get("算子"))


def _aligned_file_stem(file_name, extension):
    if (
        not isinstance(file_name, str)
        or Path(file_name).name != file_name
        or Path(file_name).suffix.lower() != extension
    ):
        raise ValueError(f"Invalid aligned scene file name: {file_name!r}")
    return file_name[: -len(extension)]


def _prepare_aligned_task(user_input, alignment):
    if alignment is None:
        raise ValueError("No test_case_road_alignments row for this input.")
    if alignment.get("match_status") != "matched":
        raise ValueError(
            f"Road alignment is not matched: {alignment.get('match_status')}"
        )
    road_id = str(alignment.get("road_id") or "")
    if not road_id.isdigit():
        raise ValueError(f"Invalid aligned road_id: {road_id!r}")
    map_name = _aligned_file_stem(alignment.get("map_file_name"), ".xodr")
    scene_name = _aligned_file_stem(alignment.get("scene_file_name"), ".json")

    pattern = re.compile(
        r"\b(?:road(?:way)?\s*(?:no\.?\s*|#\s*)?)21\b",
        flags=re.IGNORECASE,
    )
    test_input, count = pattern.subn(
        lambda match: match.group(0)[: -2] + road_id, user_input
    )
    if count == 0:
        raise ValueError("Aligned test input has no Road 21 placeholder.")
    return {
        "case_id": alignment["case_id"],
        "map_file_name": alignment["map_file_name"],
        "scene_file_name": alignment["scene_file_name"],
        "map_name": map_name,
        "scene_name": scene_name,
        "road_id": road_id,
        "original_input": user_input,
        "test_input": test_input,
        "s_start": alignment.get("s_start"),
        "s_end": alignment.get("s_end"),
        "route_lane_ids": alignment.get("route_lane_ids", []),
        "driving_lane_ids": alignment.get("driving_lane_ids", []),
    }


def run_real_trace(
    trace_path,
    model,
    tasks,
    xodr_name=None,
    json_name=None,
    execute=False,
    road_context="fake",
    enable_dsl_check=True,
    agent_flow="normal",
    request_cases=None,
    single_real_context=None,
    stop_file=None,
    road_alignments=None,
    aligned_xodr_directory=DEFAULT_ALIGNED_XODR_DIRECTORY,
    aligned_json_directory=DEFAULT_ALIGNED_JSON_DIRECTORY,
):
    aligned_real = road_context == ALIGNED_REAL_CONTEXT
    if aligned_real and execute:
        raise ValueError("Aligned real-road tests cannot execute map edits.")
    if not aligned_real:
        xodr_name = xodr_name or (
            SINGLE_AGENT_XODR if agent_flow == "single" else DEFAULT_XODR
        )
    os.environ["AGENT_TRACE_PATH"] = str(trace_path)
    use_manual_context = road_context == agent_road_context.FAKE_CONTEXT
    agent_dispatcher_module.USE_MANUAL_ROAD_CONTEXT = use_manual_context
    agent_road_context.set_use_manual_road_context(use_manual_context)
    agent_trace.set_run_id(Path(trace_path).stem)
    if not aligned_real:
        setup_paths(xodr_name, json_name=json_name)

    if request_cases is None:
        request_cases = load_request_cases() if use_manual_context else {}
    if aligned_real and road_alignments is None:
        road_alignments = load_real_road_alignments()
    manager = None
    single_agent = None
    single_context_dispatcher = None
    single_curve_context_builder = None
    if agent_flow == "single":
        single_agent = SingleAgent(model)
        single_context_dispatcher = agent_dispatcher_module.AgentDispatcher(
            curve_agent=single_agent.curve_validator,
            curve_optimizer_agent=single_agent.curve_validator,
            width_agent=single_agent.width_validator,
            slope_agent=single_agent.slope_validator,
        )
        single_curve_context_builder = single_context_dispatcher.curve_optimizer
        if single_real_context is None and not aligned_real:
            single_real_context = _build_real_single_agent_context(
                single_context_dispatcher,
                single_curve_context_builder,
            )
    else:
        manager = ManagerAgent(model, enable_dsl_check=enable_dsl_check)

    if manager is not None and not execute:
        manager.dispatcher._execute_command = (
            lambda command: f"dry-run {' '.join(command)}"
        )

    statuses = agent_trace.read_task_statuses(trace_path)
    single_context_by_map = {}
    for index, user_input in enumerate(tasks, start=1):
        if stop_file and Path(stop_file).exists():
            raise RuntimeError("Batch stopped after a worker authentication/setup failure.")
        task_id = f"real_{index:03d}"
        if statuses.get(task_id) == "done":
            continue
        print(f"\n=== {task_id} / {len(tasks)} ===")
        print(f"userInput: {user_input}")
        agent_trace.record_task(task_id, user_input=user_input, status="running")
        try:
            test_input = user_input
            if aligned_real:
                alignment = _prepare_aligned_task(
                    user_input, road_alignments.get(_normalize_request_key(user_input))
                )
                test_input = alignment["test_input"]
                setup_paths(
                    alignment["map_name"],
                    json_name=alignment["scene_name"],
                    xodr_directory=aligned_xodr_directory,
                    json_directory=aligned_json_directory,
                )
                if alignment["road_id"] not in XParser.roads:
                    raise ValueError(
                        f"road_{alignment['road_id']} is absent from "
                        f"{alignment['map_file_name']}."
                    )
                with agent_trace.trace_context(
                    task_id=task_id,
                    step_id=-1,
                    agent="road_alignment",
                    operation="real_road_context",
                ):
                    agent_trace.record_agent_step(
                        agent="road_alignment",
                        operation="real_road_context",
                        input_text=test_input,
                        road_context_json=alignment,
                    )
                if single_agent is not None:
                    map_name = alignment["map_name"]
                    if map_name not in single_context_by_map:
                        single_context_by_map[map_name] = (
                            _build_real_single_agent_context(
                                single_context_dispatcher,
                                single_curve_context_builder,
                            )
                        )
                    single_real_context = single_context_by_map[map_name]
            if use_manual_context:
                with agent_trace.trace_context(
                    task_id=task_id,
                    step_id=-1,
                    agent="road_context_judge",
                    operation="road_context",
                ):
                    context_info = _prepare_manual_task_context(user_input, request_cases)
                # print(
                #     "roadContext: "
                #     f"{context_info['source']} "
                #     f"slope={context_info['slope_type']} "
                #     f"curve={context_info['curve_type']}"
                # )
            if agent_flow == "single":
                replace_types = (
                    _single_case_operator_types(user_input, request_cases)
                    if use_manual_context
                    else []
                )
                if use_manual_context and not replace_types:
                    raise ValueError(
                        "Single-agent simulated road_21 replacement requires "
                        "a matching database case with operator types."
                    )
                road_context_json = (
                    single_real_context
                    if aligned_real
                    else _build_single_agent_context(
                        single_real_context,
                        replace_types,
                        single_context_dispatcher,
                        single_curve_context_builder,
                    )
                )
                with agent_trace.trace_context(
                    task_id=task_id,
                    step_id=0,
                    agent="single",
                    operation="single",
                ):
                    single_result = single_agent.get_commands(
                        test_input, road_context_json
                    )
                    concrete_commands = []
                    reference_commands = []
                    resolved_results = []
                    for execution_plan in single_result["execution_plans"]:
                        commands = single_context_dispatcher.build_concrete_commands(
                            execution_plan,
                            source_command={"description": test_input},
                        )
                        references = single_context_dispatcher._build_reference_commands(
                            execution_plan["dsl"],
                            source_command={"description": test_input},
                        )
                        concrete_commands.extend(commands)
                        reference_commands.extend(references)
                        resolved_results.append(
                            {
                                "agent": execution_plan["agent"],
                                "road_id": execution_plan["road_info"]["road_id"],
                                "resolved_result": execution_plan.get(
                                    "resolved_tool_calls"
                                ),
                            }
                        )
                    for command in concrete_commands:
                        agent_trace.record_projected_command_metric(command)
                    agent_trace.update_agent_step(
                        tool_call_json=single_result["plans"],
                        resolved_result_json=resolved_results,
                        internal_command=concrete_commands,
                        reference_command=reference_commands,
                        handler_result_json=[
                            {
                                "command": " ".join(command),
                                "result": f"dry-run {' '.join(command)}",
                            }
                            for command in concrete_commands
                        ],
                        status="ok",
                    )
            else:
                with agent_trace.trace_context(
                    task_id=task_id, step_id=0, agent="manager"
                ):
                    manager_output = manager.get_commands(test_input)
                with agent_trace.trace_context(task_id=task_id):
                    manager.dispatch_task(manager_output)
            agent_trace.record_task(task_id, status="done")
        except Exception as exc:
            agent_trace.record_task(task_id, status="error", error=exc)
            print(f"Task {task_id} failed: {exc}")
            if stop_file and (
                isinstance(exc, AuthenticationError)
                or re.search(r"\b401\b|invalid.api.key|authentication", str(exc), re.I)
            ):
                _write_batch_events(stop_file, [{"type": "batch_stop", "row": {
                    "task_id": task_id, "reason": "authentication_failure"
                }}])
                raise
            if stop_file and re.search(r"\b429\b|rate.limit", str(exc), re.I):
                time.sleep(30)
            continue
        finally:
            if use_manual_context:
                agent_road_context.clear_manual_task_context()


def _write_batch_events(filename, events):
    with Path(filename).open("w", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def _read_batch_snapshot(filename):
    events = [json.loads(line) for line in Path(filename).read_text(
        encoding="utf-8"
    ).splitlines() if line.strip()]
    metadata = next(event["row"] for event in events if event["type"] == "batch_metadata")
    cases = [event["row"] for event in events if event["type"] == "batch_case"]
    context = next(event["row"] for event in events if event["type"] == "road_context_cache")
    return metadata, cases, context


def _run_slope_batch_worker(snapshot_path, group_name):
    metadata, cases, context = _read_batch_snapshot(snapshot_path)
    group = next(item for item in metadata["groups"] if item["name"] == group_name)
    selected = [case for case in cases if case["case_type"] == group["case_type"]]
    request_cases = {case["input_text"].strip(): case["request_row"] for case in selected}
    api_key = dotenv_values(PROJECT_ROOT / "testfucs" / ".env").get("XUEZHANG_API_KEY")
    if not api_key:
        raise RuntimeError("Missing XUEZHANG_API_KEY in testfucs/.env.")
    os.environ["XUEZHANG_API_KEY"] = api_key
    model = init_GPT_XZ()
    # No model receives the frozen reference answers stored in the snapshot.
    run_real_trace(
        group["trace_path"], model, [case["input_text"] for case in selected],
        xodr_name=SINGLE_AGENT_XODR, execute=False, road_context="fake",
        agent_flow=group["agent_flow"], request_cases=request_cases,
        single_real_context=context if group["agent_flow"] == "single" else None,
        stop_file=metadata["stop_file"],
    )


def run_slope_comparison(database_path=REQUEST_TEST_DATABASE):
    database_path = Path(database_path).resolve()
    connection = sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM test_cases ORDER BY source_row"
        ) if json.loads(row["operator_types_json"]) == ["slope"]]
        request_rows = {row["case_id"]: row for row in test_data_store.list_request_rows(connection)}
        baselines = test_data_store.list_auto_review_baselines(
            connection, [row["case_id"] for row in rows]
        )
    finally:
        connection.close()
    if {row["case_type"] for row in rows} != {"common", "abstract"}:
        raise ValueError("Expected common and abstract slope cases only.")
    counts = {kind: sum(row["case_type"] == kind for row in rows) for kind in ("common", "abstract")}
    if counts != {"common": 100, "abstract": 100}:
        raise ValueError(f"Slope dataset changed since approval: {counts}")
    date = datetime.now().strftime("%Y-%m-%d")
    version = 1
    while True:
        suffix = f"{date}_{version:02d}"
        snapshot_path = DSL_EVAL_OUTPUT_DIRECTORY / f"slope_comparison_snapshot_{suffix}.jsonl"
        groups = [{
            "name": f"{label}_slope_{kind}", "agent_flow": flow, "case_type": kind,
            "trace_path": str(DSL_EVAL_OUTPUT_DIRECTORY / f"{label}_agent_gpt5_4_slope_{kind}_100_{suffix}.jsonl"),
        } for label, flow in (("single", "single"), ("multi", "normal"))
            for kind in ("common", "abstract")]
        if not snapshot_path.exists() and not any(Path(group["trace_path"]).exists() for group in groups):
            break
        version += 1
    protected = [
        PROJECT_ROOT / "data" / "OpenSCENARIO" / f"{SINGLE_AGENT_XODR}.xodr",
        PROJECT_ROOT / "data" / "LGSVL-VSE" / f"{SINGLE_AGENT_XODR}.json",
        PROJECT_ROOT / "llm" / "agent_tools.py",
        PROJECT_ROOT / "llm" / "single_agent.py",
        database_path,
    ]
    fingerprints = {str(file): hashlib.sha256(file.read_bytes()).hexdigest() for file in protected}
    started = time.perf_counter()
    setup_paths(SINGLE_AGENT_XODR)
    dispatcher = agent_dispatcher_module.AgentDispatcher()
    context = _build_real_single_agent_context(dispatcher, CurveOptimizationService(None))
    context_seconds = time.perf_counter() - started
    stop_file = DSL_EVAL_OUTPUT_DIRECTORY / f"slope_comparison_stop_{suffix}.jsonl"
    metadata = {
        "created_at": datetime.now().isoformat(), "model_factory": "init_GPT_XZ",
        "model_name": "gpt-5.4", "worker_count": 4, "groups": groups,
        "database_path": str(database_path), "handler_execution": False,
        "stop_file": str(stop_file), "protected_sha256": fingerprints,
        "context_build_seconds": context_seconds, "real_road_count": len(context["roads"]),
    }
    indices = {"common": 0, "abstract": 0}
    cases = []
    for row in rows:
        indices[row["case_type"]] += 1
        cases.append({**row, "task_id": f"real_{indices[row['case_type']]:03d}",
                      "request_row": request_rows[row["case_id"]],
                      "baselines": baselines[row["case_id"]]})
    _write_batch_events(snapshot_path, [
        {"type": "batch_metadata", "row": metadata},
        {"type": "road_context_cache", "row": context},
        *[{"type": "batch_case", "row": case} for case in cases],
    ])
    print(json.dumps({"snapshot": str(snapshot_path), "groups": groups,
                      "real_road_count": len(context["roads"]),
                      "context_build_seconds": context_seconds}), flush=True)

    def launch(group):
        worker_started = time.perf_counter()
        result = subprocess.run(
            [sys.executable, "-u", str(Path(__file__).resolve()),
             "--slope-batch-worker", str(snapshot_path), "--batch-group", group["name"]],
            cwd=PROJECT_ROOT, capture_output=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        if result.returncode:
            _write_batch_events(stop_file, [{"type": "batch_stop", "row": {
                "group": group["name"], "reason": "worker_failure"
            }}])
        return {"group": group["name"], "exit_code": result.returncode,
                "duration_seconds": round(time.perf_counter() - worker_started, 3),
                "stderr_tail": result.stderr[-6000:],
                "stdout_tail": result.stdout[-6000:] if result.returncode else ""}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(launch, groups))
    unchanged = {str(file): hashlib.sha256(file.read_bytes()).hexdigest() == fingerprints[str(file)]
                 for file in protected}
    output = DSL_EVAL_OUTPUT_DIRECTORY / f"slope_comparison_execution_{suffix}.jsonl"
    _write_batch_events(output, [
        {"type": "batch_execution", "row": {"snapshot": str(snapshot_path),
         "duration_seconds": round(time.perf_counter() - started, 3),
         "protected_files_unchanged": unchanged}},
        *[{"type": "worker_result", "row": result} for result in results],
    ])
    print(json.dumps({"execution": str(output), "results": results,
                      "protected_files_unchanged": unchanged}), flush=True)
    if any(result["exit_code"] for result in results):
        raise RuntimeError("One or more comparison workers failed; inspect execution JSONL.")


def _build_dsl_eval_context(case, dispatcher):
    request_case = case["row_data"]
    _prepare_manual_task_context(
        case["user_input"],
        {_normalize_request_key(case["user_input"]): request_case},
    )
    road_id = case["road_id"]
    agent_type = case["agent_type"]
    if agent_type == "width":
        road_info = dispatcher._build_width_road_info(road_id)
        original_data = agent_road_context.get_manual_width_road_info(road_id)
    elif agent_type == "slope":
        road_info = dispatcher._build_slope_road_info(road_id)
        original_data = agent_road_context.get_manual_slope_road_info(road_id)
    else:
        geometry_index = 0
        road_info = dispatcher.curve_optimizer.build_curve_tool_context(
            road_id, geometry_index
        )
        original_data = agent_road_context.get_manual_curve_road_info(
            road_id, geometry_index
        )
    return road_info, {
        "agent_context": road_info,
        "simulated_source": original_data,
    }


def _run_dsl_tool_plan_case(case, dispatcher, agents, seed):
    agent_type = case["agent_type"]
    source_dsl = copy.deepcopy(case["dsl"])
    road_info, original_road_data = _build_dsl_eval_context(case, dispatcher)
    validation = validate_dsl_format(
        agent_type,
        source_dsl,
        road_info if agent_type == "width" else None,
    )
    if validation.get("passed") is not True:
        raise ValueError(validation.get("check_result", "DSL format validation failed."))

    planned_dsl = source_dsl
    if agent_type == "curve":
        planned_dsl = dispatcher.curve_optimizer._ensure_curve_payload_metadata(
            source_dsl, case["road_id"], 0
        )

    captured = {}
    original_invoke_llm = None

    def capture_invoke_llm(*args, **kwargs):
        response = original_invoke_llm(*args, **kwargs)
        captured["tool_calls"] = [
            {
                "name": call.get("name"),
                "args": copy.deepcopy(call.get("args", {})),
            }
            for call in (getattr(response, "tool_calls", None) or [])
            if isinstance(call, dict)
        ]
        return response

    started = time.perf_counter()
    with _DSL_EVAL_LLM_CAPTURE_LOCK:
        original_invoke_llm = agent_trace.invoke_llm
        agent_trace.invoke_llm = capture_invoke_llm
        try:
            raw_calls = agents[agent_type].plan_execution(planned_dsl, road_info)
        except Exception as exc:
            exc.dsl_eval_tool_calls = captured.get("tool_calls")
            exc.dsl_eval_duration_ms = round(
                (time.perf_counter() - started) * 1000, 3
            )
            exc.dsl_eval_planned_dsl = planned_dsl
            exc.dsl_eval_road_original_data = original_road_data
            raise
        finally:
            agent_trace.invoke_llm = original_invoke_llm
    llm_duration_ms = round((time.perf_counter() - started) * 1000, 3)
    random.seed(seed + case["source_row"])
    resolved_calls = None
    if agent_type in {"width", "slope"}:
        resolved_calls = agents[agent_type].resolve_execution_plan(
            planned_dsl, road_info, raw_calls
        )

    agent_result = {
        "agent": agent_type,
        "dsl": planned_dsl,
        "road_info": road_info,
        "tool_calls": raw_calls,
    }
    if resolved_calls is not None:
        agent_result["resolved_tool_calls"] = resolved_calls
    source_command = {
        "road_id": f"road_{case['road_id']}",
        "type_name": agent_type,
        "description": case["user_input"],
    }
    commands = dispatcher.build_concrete_commands(
        agent_result, source_command=source_command
    )
    if not commands:
        raise ValueError("Tool planning produced no concrete handler command.")
    return {
        "planned_dsl": planned_dsl,
        "road_original_data": original_road_data,
        "llm_tool_calls": raw_calls,
        "resolved_tool_calls": resolved_calls,
        "internal_commands": [" ".join(command) for command in commands],
        "llm_duration_ms": llm_duration_ms,
    }


def run_dsl_tool_plan_eval(
    output_path,
    database_path=REQUEST_TEST_DATABASE,
    sample_count=15,
    seed=20260823,
    resume_from=None,
):
    output_path = _resolve_dsl_eval_jsonl_path(output_path)
    resume_path = (
        None
        if not resume_from
        else _resolve_dsl_eval_resume_path(resume_from)
    )
    api_key = dotenv_values(PROJECT_ROOT / "testfucs" / ".env").get(
        "XUEZHANG_API_KEY"
    )
    if not api_key:
        raise RuntimeError(
            "XUEZHANG_API_KEY is required for init_GPT_XZ() tool-plan evaluation."
        )

    cases_by_agent = load_dsl_tool_plan_cases(database_path)
    selected = select_dsl_tool_plan_cases(
        cases_by_agent, sample_count=sample_count, seed=seed
    )
    agent_dispatcher_module.USE_MANUAL_ROAD_CONTEXT = True
    agent_road_context.set_use_manual_road_context(True)
    previous_api_key = os.environ.get("XUEZHANG_API_KEY")
    try:
        os.environ["XUEZHANG_API_KEY"] = api_key
        model = init_GPT_XZ()
    finally:
        if previous_api_key is None:
            os.environ.pop("XUEZHANG_API_KEY", None)
        else:
            os.environ["XUEZHANG_API_KEY"] = previous_api_key
    agents = {
        "width": WidthAgent(model, enable_dsl_check=False),
        "slope": SlopeAgent(model, enable_dsl_check=False),
        "curve": CurveAgent(model, enable_dsl_check=False),
    }
    dispatcher = agent_dispatcher_module.AgentDispatcher(
        curve_optimizer_agent=agents["curve"],
        width_agent=agents["width"],
        slope_agent=agents["slope"],
    )
    previous_results = {}
    if resume_path:
        previous_payload = _load_dsl_eval_payload(resume_path)
        previous_metadata = previous_payload.get("metadata", {})
        expected_resume_metadata = {
            "model_factory": "init_GPT_XZ",
            "model_name": "gpt-5.4",
            "source_database": _project_display_path(database_path),
            "sample_seed": seed,
            "sample_count": sample_count,
            "handler_execution": False,
        }
        if any(
            previous_metadata.get(key) != value
            for key, value in expected_resume_metadata.items()
        ):
            raise ValueError("resume_from metadata does not match this evaluation.")
        previous_results = {
            (item.get("agent_type"), item.get("source_case_id")): item
            for item in previous_payload.get("cases", [])
            if item.get("status") == "ok"
        }

    results = []
    for index, case in enumerate(selected, start=1):
        previous = previous_results.get(
            (case["agent_type"], case["source_case_id"])
        )
        if previous is not None:
            if previous.get("source_dsl") != case["dsl"]:
                raise ValueError(
                    "resume_from DSL does not match the selected database case."
                )
            reused = copy.deepcopy(previous)
            required_fields = {
                "natural_language",
                "road_original_data",
                "llm_tool_calls",
                "internal_commands",
                "llm_duration_ms",
            }
            if not required_fields.issubset(reused):
                raise ValueError("resume_from success record is incomplete.")
            reused["case_id"] = f"dsl_eval_{index:03d}"
            if reused.get("natural_language") != case["user_input"]:
                raise ValueError(
                    "resume_from natural language does not match the database case."
                )
            reused["reused_from"] = _project_display_path(resume_path)
            results.append(reused)
            continue
        print(
            f"[dsl-tool-plan] {index}/{len(selected)} "
            f"{case['agent_type']} source_case_id={case['source_case_id']}"
        )
        record = {
            "case_id": f"dsl_eval_{index:03d}",
            "source_case_id": case["source_case_id"],
            "source_row": case["source_row"],
            "agent_type": case["agent_type"],
            "natural_language": case["user_input"],
            "source_dsl": case["dsl"],
            "status": "running",
            "error": "",
        }
        try:
            record.update(_run_dsl_tool_plan_case(case, dispatcher, agents, seed))
            record["status"] = "ok"
        except Exception as exc:
            if isinstance(exc, AuthenticationError):
                raise
            if getattr(exc, "dsl_eval_planned_dsl", None) is not None:
                record["planned_dsl"] = exc.dsl_eval_planned_dsl
            if getattr(exc, "dsl_eval_road_original_data", None) is not None:
                record["road_original_data"] = exc.dsl_eval_road_original_data
            if getattr(exc, "dsl_eval_tool_calls", None) is not None:
                record["llm_tool_calls"] = exc.dsl_eval_tool_calls
            if getattr(exc, "dsl_eval_duration_ms", None) is not None:
                record["llm_duration_ms"] = exc.dsl_eval_duration_ms
            record["status"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  failed: {record['error']}")
        finally:
            agent_road_context.clear_manual_task_context()
        results.append(record)

    payload = {
        "metadata": {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model_factory": "init_GPT_XZ",
            "model_name": "gpt-5.4",
            "source_database": _project_display_path(database_path),
            "sample_seed": seed,
            "sample_count": sample_count,
            "handler_execution": False,
            "resumed_from": (
                "" if not resume_path else _project_display_path(resume_path)
            ),
            "eligible_counts": {
                key: len(value) for key, value in cases_by_agent.items()
            },
            "success_count": sum(item["status"] == "ok" for item in results),
            "error_count": sum(item["status"] == "error" for item in results),
        },
        "cases": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_dsl_eval_jsonl(output_path, payload)
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--trace",
        default=str(
            PROJECT_ROOT / "testfucs" / "run_data" / "agent_flow_real_trace.jsonl"
        ),
    )
    parser.add_argument("--model", default="GPT")
    parser.add_argument("--tasks-file")
    parser.add_argument(
        "--xodr",
        help=(
            f"Defaults to {DEFAULT_XODR} for normal flow and "
            f"{SINGLE_AGENT_XODR} for single flow."
        ),
    )
    parser.add_argument("--json")
    parser.add_argument("--execute", action="store_true")
    # 测试时使用构造测试--fake
    parser.add_argument(
        "--road-context",
        choices=("fake", "real", ALIGNED_REAL_CONTEXT),
        default="fake",
    )
    parser.add_argument("--agent-flow", choices=("normal", "single"), default="normal")
    parser.add_argument(
        "--mode", choices=("trace", "dsl-tool-plan"), default="trace"
    )
    parser.add_argument(
        "--eval-output",
        default=str(
            PROJECT_ROOT
            / "testfucs"
            / "run_data"
            / "dsl_tool_call_eval_15_2026-08-23_01.jsonl"
        ),
    )
    parser.add_argument("--sample-count", type=int, default=15)
    parser.add_argument("--sample-seed", type=int, default=20260823)
    parser.add_argument("--resume-from")
    parser.add_argument("--slope-comparison", action="store_true")
    parser.add_argument("--slope-batch-worker")
    parser.add_argument("--batch-group")
    parser.add_argument("--request-database", default=str(REQUEST_TEST_DATABASE))
    parser.add_argument(
        "--aligned-xodr-directory",
        default=str(DEFAULT_ALIGNED_XODR_DIRECTORY),
    )
    parser.add_argument(
        "--aligned-json-directory",
        default=str(DEFAULT_ALIGNED_JSON_DIRECTORY),
    )
    # 是否启用 DSL 检查，默认关闭
    parser.add_argument("--enable-dsl-check", action="store_true",default=True)
    args = parser.parse_args()

    if args.slope_comparison:
        run_slope_comparison(args.request_database)
        return
    if args.slope_batch_worker:
        _run_slope_batch_worker(args.slope_batch_worker, args.batch_group)
        return

    if args.mode == "dsl-tool-plan":
        result = run_dsl_tool_plan_eval(
            args.eval_output,
            database_path=args.request_database,
            sample_count=args.sample_count,
            seed=args.sample_seed,
            resume_from=args.resume_from,
        )
        print(
            "DSL tool-plan evaluation written to: "
            f"{args.eval_output} "
            f"(ok={result['metadata']['success_count']}, "
            f"error={result['metadata']['error_count']})"
        )
        return

    tasks = load_tasks(args.tasks_file)
    if args.road_context == ALIGNED_REAL_CONTEXT and (args.xodr or args.json):
        parser.error("--xodr/--json cannot override per-case aligned scenes.")
    run_real_trace(
        args.trace,
        args.model,
        tasks,
        xodr_name=args.xodr,
        json_name=args.json,
        execute=args.execute,
        road_context=args.road_context,
        enable_dsl_check=args.enable_dsl_check,
        agent_flow=args.agent_flow,
        road_alignments=(
            load_real_road_alignments(args.request_database)
            if args.road_context == ALIGNED_REAL_CONTEXT
            else None
        ),
        aligned_xodr_directory=args.aligned_xodr_directory,
        aligned_json_directory=args.aligned_json_directory,
    )
    print(f"Trace written to: {args.trace}")


if __name__ == "__main__":
    main()
