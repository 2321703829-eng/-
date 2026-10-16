import argparse
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from testfucs import test_data_store


VIEWER_PATH = Path(__file__).resolve().parent / "viewer.html"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
CASE_PATH = re.compile(r"^/api/cases/([^/]+)$")
DSL_ANSWER_PATH = re.compile(
    r"^/api/cases/([^/]+)/dsl-answers/(width|slope|curve)$"
)
DSL_SUBTASK_PATH = re.compile(
    r"^/api/cases/([^/]+)/dsl-subtasks/(width|slope|curve)$"
)
DSL_TOOL_ANSWERS_PATH = re.compile(
    r"^/api/dsl-answers/(\d+)/tool-call-answers$"
)
TOOL_ANSWER_PATH = re.compile(r"^/api/tool-call-answers/(\d+)$")
CASE_REVIEWS_PATH = "/api/case-reviews"
CASE_REVIEWS_BATCH_PATH = "/api/case-reviews/batch"
AUTO_REVIEW_BASELINES_PATH = "/api/auto-review-baselines"
SHUTDOWN_PATH = "/api/shutdown"
SHUTDOWN_CONTROL_HEADER = "X-Viewer-Control"
SHUTDOWN_CONTROL_VALUE = "viewer-service-bat"
TOOL_ANSWER_CONTENT_KEYS = {
    "tool_calls",
    "resolved_result",
    "internal_commands",
    "random_seed",
    "metadata",
}
CASE_REVIEW_CONTENT_KEYS = {
    "case_id",
    "run_id",
    "task_id",
    "decision",
    "note",
    "review_source",
    "trace_source",
    "metadata",
}


def _handler_class(database_path, viewer_path):
    database_path = Path(database_path)
    viewer_path = Path(viewer_path)

    class ViewerRequestHandler(BaseHTTPRequestHandler):
        server_version = "AgentTraceViewer/1.0"

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                return
            if path in {"/", "/viewer.html", "/agent_trace_viewer.html"}:
                self._send_viewer()
                return
            if path == "/api/health":
                connection = test_data_store.connect_database(database_path)
                try:
                    result = test_data_store.verify_database(connection)
                finally:
                    connection.close()
                self._send_json({"ok": True, "database": database_path.name, **result})
                return
            if path == "/api/request-data":
                connection = test_data_store.connect_database(database_path)
                try:
                    rows = test_data_store.build_viewer_request_rows(connection)
                    road_alignments = test_data_store.list_case_road_alignments(connection)
                finally:
                    connection.close()
                self._send_json(
                    {
                        "sheet_name": "Sheet1",
                        "rows": rows,
                        "road_alignments": road_alignments,
                    }
                )
                return
            if path == CASE_REVIEWS_PATH:
                connection = test_data_store.connect_database(database_path)
                try:
                    reviews = test_data_store.list_case_reviews(connection)
                finally:
                    connection.close()
                self._send_json({"reviews": reviews})
                return
            match = DSL_SUBTASK_PATH.fullmatch(path)
            if match is not None:
                case_id = unquote(match.group(1))
                agent_type = match.group(2)
                connection = test_data_store.connect_database(database_path)
                try:
                    subtasks = test_data_store.list_dsl_answer_subtasks(
                        connection, case_id, agent_type
                    )
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                finally:
                    connection.close()
                self._send_json(
                    {
                        "case_id": case_id,
                        "agent_type": agent_type,
                        "subtasks": subtasks,
                    }
                )
                return
            self._send_json({"error": "Not found."}, status=404)

        def do_PATCH(self):
            path = urlparse(self.path).path
            tool_match = TOOL_ANSWER_PATH.fullmatch(path)
            if tool_match is not None:
                payload = self._read_json_body()
                if payload is None:
                    return
                if set(payload) != {"review_status"}:
                    self._send_json(
                        {"error": "PATCH requires only review_status."}, status=400
                    )
                    return
                connection = test_data_store.connect_database(database_path)
                try:
                    with connection:
                        answer = test_data_store.update_tool_call_review_status(
                            connection,
                            int(tool_match.group(1)),
                            payload["review_status"],
                        )
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                finally:
                    connection.close()
                self._send_json({"ok": True, "answer": answer})
                return

            match = CASE_PATH.fullmatch(path)
            if match is None:
                self._send_json({"error": "Not found."}, status=404)
                return
            payload = self._read_json_body()
            if payload is None:
                return
            if set(payload) != {"input_text"}:
                self._send_json(
                    {"error": "PATCH requires only input_text."}, status=400
                )
                return
            case_id = unquote(match.group(1))
            connection = test_data_store.connect_database(database_path)
            try:
                with connection:
                    test_data_store.update_case_input(
                        connection, case_id, payload["input_text"]
                    )
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            finally:
                connection.close()
            self._send_json({"ok": True, "case_id": case_id})

        def do_POST(self):
            path = urlparse(self.path).path
            if path == SHUTDOWN_PATH:
                if self.headers.get(SHUTDOWN_CONTROL_HEADER) != SHUTDOWN_CONTROL_VALUE:
                    self._send_json({"error": "Invalid Viewer control request."}, status=403)
                    return
                self._send_json({"ok": True, "message": "Viewer service is stopping."})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            if path == AUTO_REVIEW_BASELINES_PATH:
                payload = self._read_json_body()
                if payload is None:
                    return
                if set(payload) != {"case_ids"}:
                    self._send_json(
                        {"error": "Auto-review baseline request requires case_ids."},
                        status=400,
                    )
                    return
                connection = test_data_store.connect_database(database_path)
                try:
                    cases = test_data_store.list_auto_review_baselines(
                        connection, payload["case_ids"]
                    )
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                finally:
                    connection.close()
                self._send_json({"cases": cases})
                return

            match = DSL_TOOL_ANSWERS_PATH.fullmatch(path)
            if match is None:
                self._send_json({"error": "Not found."}, status=404)
                return
            payload = self._read_json_body()
            if payload is None:
                return
            if set(payload) != TOOL_ANSWER_CONTENT_KEYS:
                self._send_json(
                    {"error": "POST requires a complete tool answer."}, status=400
                )
                return
            connection = test_data_store.connect_database(database_path)
            try:
                with connection:
                    answer = test_data_store.create_tool_call_answer(
                        connection,
                        int(match.group(1)),
                        review_status="approved",
                        **payload,
                    )
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            finally:
                connection.close()
            self._send_json({"ok": True, "answer": answer}, status=201)

        def do_PUT(self):
            path = urlparse(self.path).path
            if path == CASE_REVIEWS_BATCH_PATH:
                payload = self._read_json_body()
                if payload is None:
                    return
                reviews = payload.get("reviews") if set(payload) == {"reviews"} else None
                if (
                    not isinstance(reviews, list)
                    or len(reviews) > 2000
                    or any(
                        not isinstance(review, dict)
                        or set(review) != CASE_REVIEW_CONTENT_KEYS
                        for review in reviews
                    )
                ):
                    self._send_json(
                        {"error": "Batch PUT requires complete case reviews."},
                        status=400,
                    )
                    return
                connection = test_data_store.connect_database(database_path)
                try:
                    with connection:
                        stored_reviews = [
                            test_data_store.upsert_case_review(connection, **review)
                            for review in reviews
                        ]
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                finally:
                    connection.close()
                self._send_json({"ok": True, "reviews": stored_reviews})
                return

            if path == CASE_REVIEWS_PATH:
                payload = self._read_json_body()
                if payload is None:
                    return
                if set(payload) != CASE_REVIEW_CONTENT_KEYS:
                    self._send_json(
                        {"error": "PUT requires a complete case review."}, status=400
                    )
                    return
                connection = test_data_store.connect_database(database_path)
                try:
                    with connection:
                        review = test_data_store.upsert_case_review(
                            connection, **payload
                        )
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                finally:
                    connection.close()
                self._send_json({"ok": True, "review": review})
                return

            tool_match = TOOL_ANSWER_PATH.fullmatch(path)
            if tool_match is not None:
                payload = self._read_json_body()
                if payload is None:
                    return
                if set(payload) != TOOL_ANSWER_CONTENT_KEYS:
                    self._send_json(
                        {"error": "PUT requires a complete tool answer."}, status=400
                    )
                    return
                connection = test_data_store.connect_database(database_path)
                try:
                    with connection:
                        answer = test_data_store.update_tool_call_answer(
                            connection,
                            int(tool_match.group(1)),
                            review_status="approved",
                            **payload,
                        )
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                finally:
                    connection.close()
                self._send_json({"ok": True, "answer": answer})
                return

            match = DSL_ANSWER_PATH.fullmatch(path)
            if match is None:
                self._send_json({"error": "Not found."}, status=404)
                return
            payload = self._read_json_body()
            if payload is None:
                return
            if set(payload) != {"entries"}:
                self._send_json(
                    {"error": "PUT requires only entries."}, status=400
                )
                return
            case_id = unquote(match.group(1))
            agent_type = match.group(2)
            connection = test_data_store.connect_database(database_path)
            try:
                with connection:
                    entries = test_data_store.replace_dsl_answer_entries(
                        connection, case_id, agent_type, payload["entries"]
                    )
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            finally:
                connection.close()
            self._send_json(
                {
                    "ok": True,
                    "case_id": case_id,
                    "agent_type": agent_type,
                    "entries": entries,
                }
            )

        def do_DELETE(self):
            match = TOOL_ANSWER_PATH.fullmatch(urlparse(self.path).path)
            if match is None:
                self._send_json({"error": "Not found."}, status=404)
                return
            tool_answer_id = int(match.group(1))
            connection = test_data_store.connect_database(database_path)
            try:
                with connection:
                    test_data_store.delete_tool_call_answer(
                        connection, tool_answer_id
                    )
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            finally:
                connection.close()
            self._send_json({"ok": True, "tool_answer_id": tool_answer_id})

        def _read_json_body(self):
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json({"error": "Invalid Content-Length."}, status=400)
                return None
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                self._send_json({"error": "Invalid request body size."}, status=400)
                return None
            try:
                payload = json.loads(self.rfile.read(content_length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json({"error": "Request body must be JSON."}, status=400)
                return None
            if not isinstance(payload, dict):
                self._send_json({"error": "Request body must be an object."}, status=400)
                return None
            return payload

        def _send_viewer(self):
            try:
                content = viewer_path.read_bytes()
            except FileNotFoundError:
                self._send_json({"error": "Viewer HTML not found."}, status=404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, payload, status=200):
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format_string, *args):
            print(f"[{self.log_date_time_string()}] {format_string % args}")

    return ViewerRequestHandler


def create_server(database_path, viewer_path=VIEWER_PATH, port=8730):
    handler = _handler_class(database_path, viewer_path)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main():
    parser = argparse.ArgumentParser(description="Serve the Agent trace Viewer.")
    parser.add_argument("--database", default=str(test_data_store.DEFAULT_DATABASE_PATH))
    parser.add_argument("--port", type=int, default=8730)
    args = parser.parse_args()
    server = create_server(args.database, port=args.port)
    print(f"Agent trace Viewer: http://127.0.0.1:{server.server_port}/")
    print(f"Request database: {Path(args.database).resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
