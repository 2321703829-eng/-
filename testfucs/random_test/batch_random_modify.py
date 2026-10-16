"""Batch-apply random width, slope, and curve edits to map scenarios."""

from __future__ import annotations

import argparse
import ast
import json
import os
import random
import stat
import sys
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import utils.path as project_path
import editor.editorFit as editor_fit_module
from handler.handlerCurve import handlerC
from handler.handlerRandom import handlerRandom
from handler.handlerSlope import handlerSl
from handler.handlerWidth import handlerW
from jsons.jsonParser import JParser
from utils.lambdas import is_curve
from xodrs.xodrParser import XParser


SUPPORTED_OPERATORS = ("width", "slope", "curve")
OPERATOR_ABBREVIATIONS = {"width": "w", "slope": "s", "curve": "c"}
CONFIGURED_XODR_DIR = Path(project_path.xodrPath).expanduser().absolute()
CONFIGURED_JSON_DIR = Path(project_path.jsonPath).expanduser().absolute()
JSON_ALLOWLIST_SOURCE = PROJECT_ROOT / "testfucs" / "td.py"
WEB_PAGE = Path(__file__).with_name("batch_random_modify.html")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "output" / "random_test" 
MAX_REQUEST_BYTES = 1024 * 1024
HANDLERS = {
    "width": handlerW,
    "slope": handlerSl,
    "curve": handlerC,
}
JOBS: dict[str, dict[str, object]] = {}
JOB_STATE_LOCK = threading.Lock()
BATCH_RUN_LOCK = threading.Lock()


def _skip_curve_preview(_params: object) -> None:
    return None


def normalize_operators(values: list[str]) -> list[str]:
    if any(not isinstance(value, str) for value in values):
        raise ValueError("Operator values must be strings")
    requested = {
        item.strip().lower()
        for value in values
        for item in value.split(",")
        if item.strip()
    }
    invalid = requested.difference(SUPPORTED_OPERATORS)
    if invalid:
        choices = ", ".join(SUPPORTED_OPERATORS)
        invalid_values = ", ".join(sorted(invalid))
        raise ValueError(
            f"Unsupported operators: {invalid_values}. Choose from: {choices}"
        )
    if not requested:
        raise ValueError("At least one operator is required")
    return [operator for operator in SUPPORTED_OPERATORS if operator in requested]


def safe_search_root(directory: Path) -> Path | None:
    absolute_directory = directory.absolute()
    if str(absolute_directory).startswith("\\\\"):
        return None
    try:
        file_stat = absolute_directory.lstat()
    except FileNotFoundError:
        return None
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(file_stat, "st_file_attributes", 0)
    if stat.S_ISLNK(file_stat.st_mode) or file_attributes & reparse_flag:
        return None

    resolved_directory = absolute_directory.resolve()
    if str(resolved_directory).startswith("\\\\"):
        return None
    absolute_text = os.path.normcase(os.path.normpath(str(absolute_directory)))
    resolved_text = os.path.normcase(os.path.normpath(str(resolved_directory)))
    return resolved_directory if absolute_text == resolved_text else None


def find_xodr(json_file: Path) -> Path:
    with json_file.open("r", encoding="utf-8") as file:
        scenario = json.load(file)

    map_name = scenario.get("map", {}).get("name")
    if not isinstance(map_name, str) or not map_name.strip():
        raise ValueError(f"JSON has no valid map.name: {json_file}")

    map_filename = map_name.strip()
    raw_map_path = Path(map_filename)
    if raw_map_path.is_absolute() or raw_map_path.name != map_filename:
        raise ValueError(f"map.name must be a file name, not a path: {map_name}")

    names = []
    for name in (raw_map_path.name, json_file.stem):
        filename = name if Path(name).suffix.lower() == ".xodr" else f"{name}.xodr"
        if filename not in names:
            names.append(filename)

    search_dirs = [
        json_file.parent,
        json_file.parent.parent / "OpenSCENARIO",
        PROJECT_ROOT / "data" / "OpenSCENARIO",
        CONFIGURED_XODR_DIR,
    ]
    candidates = []
    for directory in search_dirs:
        search_root = safe_search_root(directory)
        if search_root is None:
            continue
        candidates.extend((search_root / name, search_root) for name in names)

    for candidate, search_root in candidates:
        if candidate.is_symlink() or not candidate.is_file():
            continue
        resolved_candidate = candidate.resolve()
        if resolved_candidate.parent == search_root:
            return resolved_candidate

    checked = "\n  ".join(str(candidate) for candidate, _ in candidates)
    raise FileNotFoundError(
        f"Cannot locate XODR for {json_file}. Checked:\n  {checked}"
    )


def load_allowed_scenarios() -> dict[str, str]:
    source = JSON_ALLOWLIST_SOURCE
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    values = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {
                "jsons_all",
                "xodrs_all",
            }:
                values[target.id] = ast.literal_eval(node.value)

    raw_json_names = values.get("jsons_all")
    raw_xodr_names = values.get("xodrs_all")
    if not isinstance(raw_json_names, list) or not raw_json_names:
        raise RuntimeError(f"jsons_all is missing or empty in {source}")
    if not isinstance(raw_xodr_names, list) or len(raw_xodr_names) != len(
        raw_json_names
    ):
        raise RuntimeError(f"xodrs_all does not align with jsons_all in {source}")

    scenarios = {}
    for json_value, xodr_value in zip(raw_json_names, raw_xodr_names):
        if not isinstance(json_value, str):
            raise RuntimeError(f"jsons_all contains a non-string value in {source}")
        name = json_value.strip()
        path = Path(name)
        if not name or path.name != name or path.suffix.lower() != ".json":
            raise RuntimeError(
                f"jsons_all contains an invalid JSON file name: {json_value!r}"
            )
        if not isinstance(xodr_value, str) or not xodr_value.strip():
            raise RuntimeError(f"xodrs_all contains an invalid value in {source}")
        if name in scenarios:
            raise RuntimeError(f"jsons_all contains a duplicate file name: {name}")
        scenarios[name] = xodr_value.strip()
    return scenarios


def choose_arg_carcarana_replacement(json_file: Path) -> Path:
    search_root = safe_search_root(json_file.parent)
    if search_root is None:
        raise FileNotFoundError(
            f"Cannot search for an ARG_Carcarana replacement beside {json_file}"
        )

    candidates = []
    for name in load_allowed_scenarios():
        if not name.startswith("ARG_Carcarana-"):
            continue
        candidate = search_root / name
        if candidate.is_symlink() or not candidate.is_file():
            continue
        resolved_candidate = candidate.resolve()
        if (
            resolved_candidate.parent == search_root
            and resolved_candidate != json_file
        ):
            candidates.append(resolved_candidate)

    if not candidates:
        raise FileNotFoundError(
            f"No ARG_Carcarana replacement JSON is available beside {json_file}"
        )
    return random.choice(candidates)


def list_json_files(directory: str | Path) -> tuple[Path, list[dict[str, object]]]:
    requested_directory = Path(directory).expanduser().absolute()
    search_root = safe_search_root(requested_directory)
    if search_root is None:
        raise ValueError(f"JSON directory is unavailable or unsafe: {directory}")

    files = []
    for name, xodr_name in load_allowed_scenarios().items():
        candidate = search_root / name
        if candidate.is_symlink() or not candidate.is_file():
            continue
        resolved_candidate = candidate.resolve()
        if resolved_candidate.parent != search_root:
            continue
        files.append(
            {
                "name": resolved_candidate.name,
                "path": str(resolved_candidate),
                "size": resolved_candidate.stat().st_size,
                "xodr_name": xodr_name,
            }
        )
    return search_root, files


def validate_web_json_files(values: object) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError("Select at least one JSON file")
    if len(values) > 5000:
        raise ValueError("A single job can process at most 5000 JSON files")

    allowed_names = set(load_allowed_scenarios())
    validated = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("JSON file paths must be non-empty strings")
        candidate = Path(value).expanduser().absolute()
        if str(candidate).startswith("\\\\") or candidate.suffix.lower() != ".json":
            raise ValueError(f"Invalid JSON file path: {value}")
        search_root = safe_search_root(candidate.parent)
        if search_root is None or candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"JSON file is unavailable or unsafe: {value}")
        resolved_candidate = candidate.resolve()
        if resolved_candidate.parent != search_root:
            raise ValueError(f"JSON file leaves its source directory: {value}")
        if resolved_candidate.name not in allowed_names:
            raise ValueError(
                f"JSON file is not included in testfucs/td.py jsons_all: {value}"
            )
        validated.append(str(resolved_candidate))
    return validated


def validate_output_directory(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Output directory is required")
    output_dir = Path(value).expanduser().absolute()
    if str(output_dir).startswith("\\\\"):
        raise ValueError("UNC and device output paths are not allowed")

    existing_ancestor = output_dir
    while not existing_ancestor.exists():
        parent = existing_ancestor.parent
        if parent == existing_ancestor:
            raise ValueError(f"Cannot resolve output directory: {value}")
        existing_ancestor = parent
    if not existing_ancestor.is_dir():
        raise ValueError(f"Output path has a non-directory ancestor: {value}")
    if safe_search_root(existing_ancestor) is None:
        raise ValueError(f"Output directory is unavailable or unsafe: {value}")
    return output_dir


def generate_commands(operators: list[str]) -> list[str]:
    random_handler = handlerRandom()
    random_handler.init_cross_roads()
    random_handler.init_cross_road_connection()
    roads = list(XParser.roads.values())
    commands = []

    if "width" in operators:
        for road in roads:
            commands.extend(random_handler.get_random_width(road))

    if "slope" in operators:
        for road in roads:
            if random_handler.is_in_cross(road):
                continue
            move_types = random_handler.get_type_for_slope_command(road.get("id"))
            if not move_types:
                continue
            slope_commands = random_handler.get_random_slope(road, move_types)
            random_handler.note_command(slope_commands)
            commands.extend(slope_commands)

    if "curve" in operators:
        for road in roads:
            if is_curve(road):
                commands.extend(random_handler.get_random_curve(road))

    return commands


def load_scenario(json_file: Path, xodr_file: Path) -> None:
    project_path.xodrPath = str(xodr_file.parent) + os.sep
    project_path.jsonPath = str(json_file.parent) + os.sep
    if not XParser.openXodr(xodr_file.stem):
        raise RuntimeError(f"Failed to read XODR: {xodr_file}")
    if not JParser.readJson(json_file.stem):
        raise RuntimeError(f"Failed to read JSON: {json_file}")


def apply_commands(commands: list[str], progress_callback=None) -> None:
    original_curve_preview = editor_fit_module.showCurve
    # Interactive curve editing writes a preview to data/outputPics. Batch mode
    # keeps generated artifacts inside --output-dir and does not need a preview.
    editor_fit_module.showCurve = _skip_curve_preview
    try:
        total = len(commands)
        for index, command in enumerate(commands, start=1):
            tokens = command.split()
            HANDLERS[tokens[0]].handle(tokens)
            if progress_callback:
                progress_callback(index, total)
    finally:
        editor_fit_module.showCurve = original_curve_preview


def next_output_name(output_dir: Path, base_name: str) -> str:
    index = 1
    while True:
        output_name = base_name if index == 1 else f"{base_name}_{index}"
        xodr_output = output_dir / f"{output_name}.xodr"
        json_output = output_dir / f"{output_name}.json"
        if not xodr_output.exists() and not json_output.exists():
            return output_name
        index += 1


def write_output_pair(output_dir: Path, output_name: str) -> None:
    temporary_name = f".{output_name}.{uuid.uuid4().hex}.tmp"
    temporary_xodr = output_dir / f"{temporary_name}.xodr"
    temporary_json = output_dir / f"{temporary_name}.json"
    final_xodr = output_dir / f"{output_name}.xodr"
    final_json = output_dir / f"{output_name}.json"
    xodr_moved = False

    project_path.saveName = temporary_name
    project_path.jsonSaveName = temporary_name
    try:
        XParser.writeXodr()
        JParser.writeJson()
        if final_xodr.exists() or final_json.exists():
            raise FileExistsError(f"Output already exists: {output_name}")
        temporary_xodr.rename(final_xodr)
        xodr_moved = True
        temporary_json.rename(final_json)
    except Exception:
        if xodr_moved:
            final_xodr.unlink(missing_ok=True)
        raise
    finally:
        temporary_xodr.unlink(missing_ok=True)
        temporary_json.unlink(missing_ok=True)
        project_path.saveName = output_name
        project_path.jsonSaveName = output_name


def process_scenario(
    json_file: Path,
    operators: list[str],
    output_dir: Path,
    output_name: str,
    progress_callback=None,
) -> dict[str, object]:
    def report(stage: str, **details: object) -> None:
        if progress_callback:
            progress_callback({"stage": stage, **details})

    report("locating_xodr")
    xodr_file = find_xodr(json_file)
    report("loading_scenario")
    load_scenario(json_file, xodr_file)

    output_dir.mkdir(parents=True, exist_ok=True)
    project_path.savePath = str(output_dir) + os.sep
    project_path.editSaveName(output_name)
    project_path.editJsonSaveName(output_name)
    JParser.editSaveName(output_name)

    report("generating_commands")
    commands = generate_commands(operators)
    generated_operators = {command.split()[0] for command in commands}
    missing_operators = [
        operator for operator in operators if operator not in generated_operators
    ]
    if missing_operators:
        raise RuntimeError(
            "No applicable roads for operators: " + ", ".join(missing_operators)
        )
    command_total = len(commands)
    report("applying_commands", command_completed=0, command_total=command_total)
    apply_commands(
        commands,
        progress_callback=lambda completed, total: report(
            "applying_commands",
            command_completed=completed,
            command_total=total,
        ),
    )
    report(
        "saving_output",
        command_completed=command_total,
        command_total=command_total,
    )
    write_output_pair(output_dir, output_name)
    return {
        "json": str(json_file),
        "xodr": str(xodr_file),
        "output_name": output_name,
        "command_count": len(commands),
    }


def batch_modify(
    json_files: list[str | Path],
    operators: list[str],
    output_dir: str | Path,
    progress_callback=None,
) -> tuple[list[dict[str, object]], list[tuple[Path, Exception]]]:
    normalized_operators = normalize_operators(operators)
    destination = Path(output_dir).expanduser().resolve()
    results = []
    failures = []
    suffix = "".join(
        OPERATOR_ABBREVIATIONS[operator] for operator in normalized_operators
    )
    date_stamp = time.strftime("%m-%d")

    total = len(json_files)
    for index, value in enumerate(json_files, start=1):
        json_file = Path(value).expanduser().resolve()

        def run_scenario(candidate: Path) -> dict[str, object]:
            if not candidate.is_file():
                raise FileNotFoundError(f"JSON file does not exist: {candidate}")
            base_name = f"{candidate.stem}_random_{date_stamp}_{suffix}"
            output_name = next_output_name(destination, base_name)

            def report_scenario_progress(details: dict[str, object]) -> None:
                if progress_callback:
                    event = {
                        "index": index,
                        "total": total,
                        "status": "processing",
                        "json": str(candidate),
                        **details,
                    }
                    if candidate != json_file:
                        event["replacement_for"] = str(json_file)
                    progress_callback(event)

            return process_scenario(
                candidate,
                normalized_operators,
                destination,
                output_name,
                progress_callback=report_scenario_progress,
            )

        active_json = json_file
        replacement_for = None
        try:
            try:
                result = run_scenario(json_file)
            except Exception as original_error:
                try:
                    active_json = choose_arg_carcarana_replacement(json_file)
                    replacement_for = str(json_file)
                    if progress_callback:
                        progress_callback(
                            {
                                "index": index,
                                "total": total,
                                "status": "processing",
                                "stage": "selecting_replacement",
                                "json": str(active_json),
                                "replacement_for": replacement_for,
                            }
                        )
                    result = run_scenario(active_json)
                    result["replacement_for"] = replacement_for
                except Exception as replacement_error:
                    raise RuntimeError(
                        f"Original scenario failed: {original_error}; "
                        f"ARG_Carcarana replacement failed: {replacement_error}"
                    ) from replacement_error

            results.append(result)
            if progress_callback:
                event = {
                    "index": index,
                    "total": total,
                    "status": "ok",
                    "json": str(active_json),
                    "result": result,
                }
                if replacement_for:
                    event["replacement_for"] = replacement_for
                progress_callback(event)
            print(
                f"OK {active_json} -> {destination / result['output_name']} "
                f"({result['command_count']} commands)"
            )
        except Exception as error:
            failures.append((json_file, error))
            if progress_callback:
                progress_callback(
                    {
                        "index": index,
                        "total": total,
                        "status": "error",
                        "json": str(json_file),
                        "error": str(error),
                    }
                )
            print(f"ERROR {json_file}: {error}", file=sys.stderr)

    return results, failures


def default_json_directory() -> Path:
    for candidate in (
        CONFIGURED_JSON_DIR,
        PROJECT_ROOT / "data" / "LGSVL-VSE",
    ):
        search_root = safe_search_root(candidate)
        if search_root is not None:
            return search_root
    return PROJECT_ROOT / "data" / "LGSVL-VSE"


def job_snapshot(job_id: str) -> dict[str, object] | None:
    with JOB_STATE_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return None
        snapshot = dict(job)
        snapshot["items"] = list(job.get("items", []))
        snapshot["results"] = list(job.get("results", []))
        snapshot["failures"] = list(job.get("failures", []))
        return snapshot


def _run_web_job(
    job_id: str,
    json_files: list[str],
    operators: list[str],
    output_dir: Path,
) -> None:
    with BATCH_RUN_LOCK:
        with JOB_STATE_LOCK:
            JOBS[job_id]["status"] = "running"
            JOBS[job_id]["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        def update_progress(event: dict[str, object]) -> None:
            with JOB_STATE_LOCK:
                job = JOBS[job_id]
                if event["status"] == "processing":
                    job["current"] = event
                    command_total = int(event.get("command_total", 0))
                    command_completed = int(event.get("command_completed", 0))
                    scenario_progress = 0.0
                    if event.get("stage") == "saving_output":
                        scenario_progress = 0.99
                    elif command_total:
                        scenario_progress = min(
                            command_completed / command_total,
                            0.99,
                        )
                    job["progress_percent"] = (
                        (int(event["index"]) - 1 + scenario_progress)
                        / int(event["total"])
                        * 100
                    )
                else:
                    job["completed"] = event["index"]
                    job["progress_percent"] = (
                        int(event["index"]) / int(event["total"]) * 100
                    )
                    job["current"] = event
                    job["items"].append(event)

        try:
            results, failures = batch_modify(
                json_files,
                operators,
                output_dir,
                progress_callback=update_progress,
            )
            serialized_failures = [
                {"json": str(json_file), "error": str(error)}
                for json_file, error in failures
            ]
            with JOB_STATE_LOCK:
                job = JOBS[job_id]
                if not failures:
                    job["status"] = "completed"
                elif results:
                    job["status"] = "partial"
                else:
                    job["status"] = "failed"
                job["results"] = results
                job["failures"] = serialized_failures
                job["progress_percent"] = 100.0
                job["current"] = None
                job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as error:
            with JOB_STATE_LOCK:
                job = JOBS[job_id]
                job["status"] = "failed"
                job["error"] = str(error)
                job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def start_web_job(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    json_files = validate_web_json_files(payload.get("json_files"))
    operator_values = payload.get("operators")
    if not isinstance(operator_values, list):
        raise ValueError("operators must be a list")
    operators = normalize_operators(operator_values)
    output_dir = validate_output_directory(payload.get("output_dir"))

    with JOB_STATE_LOCK:
        if any(
            job.get("status") in {"queued", "running"}
            for job in JOBS.values()
        ):
            raise RuntimeError("Another random modification job is still running")
        completed_ids = [
            key
            for key, job in JOBS.items()
            if job.get("status") not in {"queued", "running"}
        ]
        for old_id in completed_ids[:-19]:
            JOBS.pop(old_id, None)

        job_id = uuid.uuid4().hex
        JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "total": len(json_files),
            "completed": 0,
            "progress_percent": 0.0,
            "current": None,
            "operators": operators,
            "output_dir": str(output_dir),
            "items": [],
            "results": [],
            "failures": [],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    worker = threading.Thread(
        target=_run_web_job,
        args=(job_id, json_files, operators, output_dir),
        # Let an accepted write job finish cleanly if the web server is stopped.
        daemon=False,
        name=f"random-modify-{job_id[:8]}",
    )
    worker.start()
    return job_snapshot(job_id) or {}


class RandomModifyWebHandler(BaseHTTPRequestHandler):
    server_version = "OpenDriveRandomTest/1.0"

    def _send_json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> object:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise ValueError("Content-Type must be application/json")
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            raise ValueError("Request body is empty or too large")
        return json.loads(self.rfile.read(content_length).decode("utf-8"))

    def _host_is_local(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        return host in {"127.0.0.1", "localhost"}

    def _serve_page(self) -> None:
        if not WEB_PAGE.is_file():
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"Frontend file not found: {WEB_PAGE}"},
            )
            return
        body = WEB_PAGE.read_bytes()
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._host_is_local():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Local access only"})
            return
        request_path = urlparse(self.path).path
        if request_path in {"/", "/batch_random_modify.html"}:
            self._serve_page()
            return
        if request_path == "/api/config":
            self._send_json(
                HTTPStatus.OK,
                {
                    "json_dir": str(default_json_directory()),
                    "output_dir": str(DEFAULT_OUTPUT_DIR),
                    "operators": list(SUPPORTED_OPERATORS),
                },
            )
            return
        if request_path.startswith("/api/jobs/"):
            job_id = request_path.rsplit("/", 1)[-1]
            job = job_snapshot(job_id)
            if job is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Job not found"})
            else:
                self._send_json(HTTPStatus.OK, job)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._host_is_local():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Local access only"})
            return
        request_path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if request_path == "/api/scan":
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object")
                json_dir, files = list_json_files(payload.get("json_dir", ""))
                self._send_json(
                    HTTPStatus.OK,
                    {"json_dir": str(json_dir), "files": files},
                )
                return
            if request_path == "/api/jobs":
                self._send_json(HTTPStatus.ACCEPTED, start_web_job(payload))
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON body"})
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except RuntimeError as error:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
        except Exception as error:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[web] {self.address_string()} {format_string % args}")


class LocalRandomModifyServer(ThreadingHTTPServer):
    daemon_threads = True


def create_web_server(port: int = 0) -> LocalRandomModifyServer:
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    return LocalRandomModifyServer(("127.0.0.1", port), RandomModifyWebHandler)


def serve_web(port: int = 0, open_browser: bool = True) -> int:
    server = create_web_server(port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Random test web UI: {url}")
    print("Press Ctrl+C to stop the server.")
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("Stopping random test web UI.")
    finally:
        server.server_close()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-apply random map edits from the CLI or local web UI."
    )
    parser.add_argument("--web", action="store_true", help="Start the local web UI")
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local web UI port (default: choose an available port)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser when starting the web UI",
    )
    parser.add_argument("--json-files", nargs="+", help="Input JSON files")
    parser.add_argument(
        "--operators",
        nargs="+",
        help="Any combination of width, slope, and curve (spaces or commas)",
    )
    parser.add_argument("--output-dir", help="Directory for generated files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.web:
        try:
            return serve_web(args.port, open_browser=not args.no_browser)
        except (OSError, ValueError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
    if not args.json_files or not args.operators or not args.output_dir:
        print(
            "ERROR: --json-files, --operators, and --output-dir are required "
            "unless --web is used",
            file=sys.stderr,
        )
        return 2
    try:
        _, failures = batch_modify(args.json_files, args.operators, args.output_dir)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
