"""Local-only HTTP application for loading and reopening visualizations."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from multiprocessing import freeze_support
from pathlib import Path
import sqlite3
import tempfile
from threading import Lock, Thread
from typing import Any, Callable
from urllib.parse import unquote, urlparse
from urllib.request import urlopen
from uuid import uuid4
import webbrowser
import xml.etree.ElementTree as ET

from .scenario_visualizer import (
    build_visualization_payload,
    render_visualization_html,
)
from .ego_analyzer import build_ego_analysis, populate_run_roads
from .ego_store import DEFAULT_EXPORT_DIRECTORY, EgoAnalysisStore


_MAX_REQUEST_BYTES = 50 * 1024 * 1024
_MAX_EGO_REQUEST_BYTES = 256 * 1024 * 1024
_DEFAULT_MAP_DIRECTORY = Path(r"D:\Desktop\SCTrans-data\OpenSCENARIO")
_DEFAULT_SCENARIO_DIRECTORY = Path(r"D:\Desktop\变异存档\Random")


class HistoryStore:
    """Persist rendered visualization payloads behind a small local interface."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS visualization_history (
                    id TEXT PRIMARY KEY,
                    map_name TEXT NOT NULL,
                    xodr_name TEXT NOT NULL,
                    scenario_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    road_count INTEGER NOT NULL,
                    npc_count INTEGER NOT NULL,
                    waypoint_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def save(
        self,
        record_id: str,
        xodr_name: str,
        scenario_name: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        now = datetime.now(timezone.utc).isoformat()
        summary = payload["summary"]
        values = (
            record_id,
            str(payload.get("mapName") or xodr_name),
            xodr_name,
            scenario_name,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            int(summary["roadCount"]),
            int(summary["npcCount"]),
            int(summary["waypointCount"]),
            now,
            now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO visualization_history (
                    id, map_name, xodr_name, scenario_name, payload_json,
                    road_count, npc_count, waypoint_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    map_name = excluded.map_name,
                    xodr_name = excluded.xodr_name,
                    scenario_name = excluded.scenario_name,
                    payload_json = excluded.payload_json,
                    road_count = excluded.road_count,
                    npc_count = excluded.npc_count,
                    waypoint_count = excluded.waypoint_count,
                    updated_at = excluded.updated_at
                """,
                values,
            )
        return self.get_item(record_id)

    def list_items(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, map_name, xodr_name, scenario_name,
                       road_count, npc_count, waypoint_count,
                       created_at, updated_at, json_extract(payload_json, '$.taskLabel')
                FROM visualization_history
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def get_item(self, record_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, map_name, xodr_name, scenario_name,
                       road_count, npc_count, waypoint_count,
                       created_at, updated_at, json_extract(payload_json, '$.taskLabel')
                FROM visualization_history
                WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
        if row is None:
            raise KeyError(record_id)
        return self._row_to_item(row)

    def get_payload(self, record_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM visualization_history WHERE id = ?",
                (record_id,),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def delete(self, record_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM visualization_history WHERE id = ?",
                (record_id,),
            )
        return cursor.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=10)

    @staticmethod
    def _row_to_item(row: tuple[Any, ...]) -> dict[str, object]:
        return {
            "id": row[0],
            "mapName": row[1],
            "xodrName": row[2],
            "scenarioName": row[3],
            "summary": {
                "roadCount": row[4],
                "npcCount": row[5],
                "waypointCount": row[6],
            },
            "createdAt": row[7],
            "updatedAt": row[8],
            "taskLabel": row[9] or "",
        }


class VisualizationRequestHandler(BaseHTTPRequestHandler):
    """Serve the browser app and its local history endpoints."""

    history_store: HistoryStore
    ego_store: EgoAnalysisStore
    import_jobs: dict[str, dict[str, object]]
    import_jobs_lock: Lock
    map_directory: Path
    scenario_directory: Path

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            app_path = Path(__file__).with_name("app.html")
            self._send_html(app_path.read_text(encoding="utf-8"))
            return
        if path == "/api/health":
            self._send_json({"application": "ResultAna", "status": "ok"})
            return
        if path == "/api/history":
            self._send_json({"items": self.history_store.list_items()})
            return
        if path == "/api/ego/history":
            self._send_json({"items": self.ego_store.list_items()})
            return
        if path.startswith("/api/import-jobs/"):
            job_id = unquote(path.removeprefix("/api/import-jobs/"))
            with self.import_jobs_lock:
                job = self.import_jobs.get(job_id)
                response = dict(job) if job is not None else None
            if response is None:
                self._send_json({"error": "Import job not found."}, status=404)
                return
            self._send_json(response)
            return
        if path.startswith("/api/ego/"):
            if self._serve_ego_api_get(path):
                return
            self._send_json({"error": "EGO analysis record not found."}, status=404)
            return
        if path.startswith("/ego-view/"):
            parts = path.removeprefix("/ego-view/").split("/")
            if len(parts) != 2:
                self._send_json({"error": "Not found."}, status=404)
                return
            try:
                run_index = int(parts[1])
            except ValueError:
                self._send_json({"error": "Invalid run index."}, status=400)
                return
            payload = self.ego_store.get_run(unquote(parts[0]), run_index)
            if payload is None:
                self._send_json({"error": "EGO run not found."}, status=404)
                return
            if (
                not payload.get("roads")
                or "egoStart" not in payload
                or "egoDestination" not in payload
            ):
                populate_run_roads(payload, self.scenario_directory)
                self.ego_store.update_run_payload(
                    unquote(parts[0]),
                    run_index,
                    payload,
                )
            self._send_html(render_visualization_html(payload))
            return
        if path.startswith("/ego/"):
            batch_id = unquote(path.removeprefix("/ego/"))
            if self.ego_store.get_batch(batch_id) is None:
                self._send_json({"error": "EGO analysis record not found."}, status=404)
                return
            self._send_html(_render_ego_dashboard_html(batch_id))
            return
        if path.startswith("/view/"):
            record_id = unquote(path.removeprefix("/view/"))
            payload = self.history_store.get_payload(record_id)
            if payload is None:
                self._send_json({"error": "History record not found."}, status=404)
                return
            self._send_html(render_visualization_html(payload))
            return
        self._send_json({"error": "Not found."}, status=404)

    def _serve_ego_api_get(self, path: str) -> bool:
        relative = path.removeprefix("/api/ego/")
        parts = relative.split("/")
        batch_id = unquote(parts[0])
        if len(parts) == 3 and parts[1] == "runs":
            try:
                run_index = int(parts[2])
            except ValueError:
                self._send_json({"error": "Invalid run index."}, status=400)
                return True
            payload = self.ego_store.get_run(batch_id, run_index)
            if payload is None:
                return False
            self._send_json(payload)
            return True
        if len(parts) == 1:
            batch = self.ego_store.get_batch(batch_id)
            if batch is None:
                return False
            self._send_json(batch)
            return True
        return False

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/ego/") and path.endswith("/export"):
            batch_id = unquote(
                path.removeprefix("/api/ego/").removesuffix("/export")
            )
            file_name = self.ego_store.export_xlsx(batch_id)
            if file_name is None:
                self._send_json(
                    {"error": "EGO analysis record not found."},
                    status=404,
                )
                return
            saved_path = self.ego_store.export_directory / file_name
            self._send_json(
                {"fileName": file_name, "savedPath": str(saved_path)}
            )
            return
        if path.startswith("/api/ego/") and "/reviews/" in path:
            self._update_ego_review(path)
            return
        if path not in {
            "/api/import",
            "/api/import-jobs",
            "/api/ego/import-jobs",
        }:
            self._send_json({"error": "Not found."}, status=404)
            return
        try:
            request = self._read_json_body(
                max_bytes=(
                    _MAX_EGO_REQUEST_BYTES
                    if path == "/api/ego/import-jobs"
                    else _MAX_REQUEST_BYTES
                )
            )
            if path == "/api/ego/import-jobs":
                log_name = _display_name(_required_text(request, "logName"))
                log_text = _required_text(request, "logText")
                if not log_name.lower().endswith((".jsonl", ".json")):
                    raise ValueError(
                        "The EGO log must use the .jsonl or .json extension."
                    )
                job_id = uuid4().hex
                with self.import_jobs_lock:
                    self.import_jobs[job_id] = {
                        "jobId": job_id,
                        "kind": "ego",
                        "state": "queued",
                        "progress": 0,
                        "message": "等待分析 EGO 轨迹",
                    }
                Thread(
                    target=type(self)._run_ego_import_job,
                    args=(job_id, log_name, log_text),
                    daemon=True,
                ).start()
                self._send_json({"jobId": job_id}, status=202)
                return
            import_values = _validated_import_values(request, self.map_directory)
            if path == "/api/import-jobs":
                job_id = uuid4().hex
                with self.import_jobs_lock:
                    self.import_jobs[job_id] = {
                        "jobId": job_id,
                        "state": "queued",
                        "progress": 0,
                        "message": "等待解析",
                    }
                Thread(
                    target=type(self)._run_import_job,
                    args=(job_id, import_values),
                    daemon=True,
                ).start()
                self._send_json({"jobId": job_id}, status=202)
                return

            result = _import_visualization(self.history_store, import_values)
            self._send_json(result, status=201)
        except (json.JSONDecodeError, UnicodeError, ValueError, ET.ParseError) as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _update_ego_review(self, path: str) -> None:
        relative = path.removeprefix("/api/ego/")
        parts = relative.split("/")
        if len(parts) != 3 or parts[1] != "reviews":
            self._send_json({"error": "Not found."}, status=404)
            return
        try:
            run_index = int(parts[2])
            request = self._read_json_body()
            status = _required_text(request, "status")
            note = request.get("note", "")
            if not isinstance(note, str):
                raise ValueError("Collision review note must be text.")
            review = self.ego_store.update_review(
                unquote(parts[0]),
                run_index,
                status,
                note,
            )
            self._send_json({"review": review})
        except KeyError:
            self._send_json({"error": "EGO run not found."}, status=404)
        except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=400)

    @classmethod
    def _run_import_job(
        cls,
        job_id: str,
        import_values: tuple[str, str, str, str],
    ) -> None:
        def update(progress: int, message: str) -> None:
            cls._update_import_job(
                job_id,
                state="running",
                progress=progress,
                message=message,
            )

        try:
            cls._update_import_job(
                job_id,
                state="running",
                progress=1,
                message="正在启动解析",
            )
            result = _import_visualization(
                cls.history_store,
                import_values,
                progress_callback=update,
            )
            cls._update_import_job(
                job_id,
                state="completed",
                progress=100,
                message="解析完成",
                result=result,
            )
        except Exception as exc:
            cls._update_import_job(
                job_id,
                state="failed",
                message="解析失败",
                error=str(exc),
            )

    @classmethod
    def _run_ego_import_job(
        cls,
        job_id: str,
        log_name: str,
        log_text: str,
    ) -> None:
        def update(progress: int, message: str) -> None:
            cls._update_import_job(
                job_id,
                state="running",
                progress=progress,
                message=message,
            )

        try:
            cls._update_import_job(
                job_id,
                state="running",
                progress=1,
                message="正在启动 EGO 轨迹分析",
            )
            analysis = build_ego_analysis(
                log_text,
                cls.scenario_directory,
                cls.map_directory,
                progress_callback=update,
                use_processes=False,
            )
            batch_id = _ego_content_id(log_text)
            item = cls.ego_store.save(batch_id, log_name, analysis)
            cls._update_import_job(
                job_id,
                state="completed",
                progress=100,
                message="EGO 轨迹分析完成",
                result={
                    "id": batch_id,
                    "sourceName": item["sourceName"],
                    "summary": item["summary"],
                },
            )
        except Exception as exc:
            cls._update_import_job(
                job_id,
                state="failed",
                message="EGO 轨迹分析失败",
                error=str(exc),
            )

    @classmethod
    def _update_import_job(cls, job_id: str, **changes: object) -> None:
        with cls.import_jobs_lock:
            cls.import_jobs[job_id].update(changes)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/ego/"):
            batch_id = unquote(path.removeprefix("/api/ego/"))
            if not self.ego_store.delete(batch_id):
                self._send_json(
                    {"error": "EGO analysis record not found."},
                    status=404,
                )
                return
            self._send_json({"deleted": True})
            return
        if not path.startswith("/api/history/"):
            self._send_json({"error": "Not found."}, status=404)
            return
        record_id = unquote(path.removeprefix("/api/history/"))
        if not self.history_store.delete(record_id):
            self._send_json({"error": "History record not found."}, status=404)
            return
        self._send_json({"deleted": True})

    def _read_json_body(
        self,
        *,
        max_bytes: int = _MAX_REQUEST_BYTES,
    ) -> dict[str, object]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header.") from exc
        if content_length <= 0:
            raise ValueError("Request body is required.")
        if content_length > max_bytes:
            limit_mb = max_bytes // (1024 * 1024)
            raise ValueError(
                f"Selected files are larger than {limit_mb} MB combined."
            )
        body = self.rfile.read(content_length).decode("utf-8")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def _send_json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def create_server(
    database_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    map_directory: str | Path = _DEFAULT_MAP_DIRECTORY,
    scenario_directory: str | Path = _DEFAULT_SCENARIO_DIRECTORY,
    export_directory: str | Path = DEFAULT_EXPORT_DIRECTORY,
) -> ThreadingHTTPServer:
    """Create a configured local server without starting its event loop."""

    history_store = HistoryStore(database_path)
    ego_store = EgoAnalysisStore(database_path, export_directory)
    import_jobs: dict[str, dict[str, object]] = {}
    import_jobs_lock = Lock()
    handler = type(
        "ConfiguredVisualizationRequestHandler",
        (VisualizationRequestHandler,),
        {
            "history_store": history_store,
            "ego_store": ego_store,
            "import_jobs": import_jobs,
            "import_jobs_lock": import_jobs_lock,
            "map_directory": Path(map_directory),
            "scenario_directory": Path(scenario_directory),
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    project_directory = Path(__file__).resolve().parent.parent
    database_path = project_directory / "history" / "visualization_history.sqlite3"
    url = "http://127.0.0.1:8765/"
    try:
        server = create_server(database_path=database_path)
    except OSError as exc:
        if _is_resultana_server(url):
            webbrowser.open(url)
            print(f"ResultAna is already running at {url}")
            return
        raise RuntimeError(
            "Port 8765 is already in use by another application."
        ) from exc

    print(f"ResultAna is running at {url}")
    print("Close this window or press Ctrl+C to stop the local server.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _required_text(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Field {key!r} must be a non-empty string.")
    return value


def _validated_import_values(
    request: dict[str, object],
    map_directory: Path,
) -> tuple[str, str, str, str]:
    scenario_name = _display_name(_required_text(request, "scenarioName"))
    scenario_text = _required_text(request, "scenarioText")
    if request.get("xodrName") is None and request.get("xodrText") is None:
        xodr_name, xodr_text = _load_map_for_scenario(
            scenario_text,
            map_directory,
        )
    else:
        xodr_name = _display_name(_required_text(request, "xodrName"))
        xodr_text = _required_text(request, "xodrText")
    if not xodr_name.lower().endswith(".xodr"):
        raise ValueError("The map file must use the .xodr extension.")
    if not scenario_name.lower().endswith(".json"):
        raise ValueError("The scenario file must use the .json extension.")
    values = (xodr_name, scenario_name, xodr_text, scenario_text)
    task_label = request.get("taskLabel", "")
    if not isinstance(task_label, str) or len(task_label) > 200:
        raise ValueError("Task label must be text of at most 200 characters.")
    return (*values, task_label.strip()) if task_label.strip() else values


def _load_map_for_scenario(
    scenario_text: str,
    map_directory: Path,
) -> tuple[str, str]:
    scenario = json.loads(scenario_text)
    map_config = scenario.get("map") if isinstance(scenario, dict) else None
    map_name = map_config.get("name") if isinstance(map_config, dict) else None
    if not isinstance(map_name, str) or not map_name.strip():
        raise ValueError("The scenario JSON must contain a non-empty map.name field.")
    normalized_name = map_name.strip().casefold()
    try:
        matches = sorted(
            path
            for path in map_directory.iterdir()
            if path.is_file()
            and path.suffix.casefold() == ".xodr"
            and path.stem.casefold() == normalized_name
        )
    except OSError as exc:
        raise ValueError(f"Cannot read the map directory: {map_directory}") from exc
    if not matches:
        raise ValueError(
            f"No XODR map named {map_name.strip()!r} was found in {map_directory}."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Multiple XODR maps named {map_name.strip()!r} were found in "
            f"{map_directory}."
        )
    map_path = matches[0]
    try:
        return map_path.name, map_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Cannot read the matched map file: {map_path}") from exc


def _import_visualization(
    history_store: HistoryStore,
    import_values: tuple[str, str, str, str],
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, object]:
    xodr_name, scenario_name, xodr_text, scenario_text = import_values[:4]
    task_label = import_values[4] if len(import_values) > 4 else ""
    record_id = _content_id(xodr_text, scenario_text)
    if task_label:
        record_id = hashlib.sha256((record_id + "\0" + task_label + "\0" + xodr_name + "\0" + scenario_name).encode("utf-8")).hexdigest()

    def report_build_progress(progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(min(progress, 95), message)

    with tempfile.TemporaryDirectory() as directory:
        map_path = Path(directory) / "map.xodr"
        scenario_path = Path(directory) / "scenario.json"
        map_path.write_text(xodr_text, encoding="utf-8")
        scenario_path.write_text(scenario_text, encoding="utf-8")
        payload = build_visualization_payload(
            map_path,
            scenario_path,
            progress_callback=(
                report_build_progress if progress_callback is not None else None
            ),
        )
    payload["mapFile"] = xodr_name
    payload["scenarioFile"] = scenario_name
    payload["taskLabel"] = task_label
    if progress_callback is not None:
        progress_callback(98, "正在保存历史记录")
    item = history_store.save(
        record_id,
        xodr_name,
        scenario_name,
        payload,
    )
    return {
        "id": record_id,
        "mapName": item["mapName"],
        "summary": item["summary"],
    }


def _display_name(value: str) -> str:
    return Path(value).name


def _content_id(xodr_text: str, scenario_text: str) -> str:
    digest = hashlib.sha256()
    digest.update(xodr_text.encode("utf-8"))
    digest.update(b"\0")
    digest.update(scenario_text.encode("utf-8"))
    return digest.hexdigest()


def _ego_content_id(log_text: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"ego-compliance\0")
    digest.update(log_text.encode("utf-8"))
    return digest.hexdigest()


def _render_ego_dashboard_html(batch_id: str) -> str:
    template_path = Path(__file__).with_name("ego_dashboard.html")
    batch_id_json = json.dumps(batch_id, ensure_ascii=False).replace("</", "<\\/")
    return template_path.read_text(encoding="utf-8").replace(
        "__EGO_BATCH_ID__",
        batch_id_json,
    )


def _is_resultana_server(url: str) -> bool:
    try:
        with urlopen(f"{url}api/health", timeout=1) as response:
            value = json.loads(response.read().decode("utf-8"))
        return value.get("application") == "ResultAna"
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


if __name__ == "__main__":
    freeze_support()
    main()
