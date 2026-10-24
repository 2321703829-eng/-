from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Thread
import time
import unittest

from test_scenario_visualizer import SIMPLE_MAP, SIMPLE_SCENARIO
from visualization.server import create_server


class VisualizationServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.database_path = self.directory / "history.sqlite3"
        self.map_directory = self.directory / "maps"
        self.map_directory.mkdir()
        self.scenario_directory = self.directory / "scenarios"
        self.scenario_directory.mkdir()
        self.export_directory = self.directory / "exports"
        self.server = create_server(
            database_path=self.database_path,
            port=0,
            map_directory=self.map_directory,
            scenario_directory=self.scenario_directory,
            export_directory=self.export_directory,
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary_directory.cleanup()

    def restart_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.server = create_server(
            database_path=self.database_path,
            port=0,
            map_directory=self.map_directory,
            scenario_directory=self.scenario_directory,
            export_directory=self.export_directory,
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def request(self, method, path, body=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {}
        encoded_body = None
        if body is not None:
            encoded_body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded_body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        content_type = response.getheader("Content-Type", "")
        connection.close()
        if content_type.startswith("application/json"):
            return response.status, json.loads(response_body.decode("utf-8"))
        return response.status, response_body.decode("utf-8")

    def wait_for_job(self, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, job = self.request("GET", f"/api/import-jobs/{job_id}")
            self.assertEqual(status, 200)
            if job["state"] in {"completed", "failed"}:
                return job
            time.sleep(0.01)
        self.fail("Import job did not finish.")

    def import_ego_batch(self, *, jsonl=False, sim_result="FAILURE: collision"):
        (self.map_directory / "simple.xodr").write_text(
            SIMPLE_MAP,
            encoding="utf-8",
        )
        nested = self.scenario_directory / "nested"
        nested.mkdir(exist_ok=True)
        (nested / "mutated.json").write_text(
            json.dumps(
                {
                    "map": {"name": "simple"},
                    "agents": [
                        {
                            "type": 1,
                            "transform": {
                                "position": {"x": 0, "y": 0, "z": 1}
                            },
                            "destinationPoint": {
                                "position": {"x": 15, "y": 0, "z": 1}
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        log = [
            {
                "mapname": "ignored-log-map-name",
                "jsonname": "mutated.json",
                "waypoints": [
                    {
                        "ordinalNumber": 3,
                        "position": {"x": 1, "y": 0, "z": 1},
                        "angle": {"x": 0, "y": 90, "z": 0},
                        "speed": 51,
                    }
                ],
                "cost_time": 1,
                "final_distance_to_destination": 2,
                "max_speed": 51,
                "sim_result": sim_result,
            }
        ]
        log_text = json.dumps(log[0]) if jsonl else json.dumps(log)
        status, created = self.request(
            "POST",
            "/api/ego/import-jobs",
            {
                "logName": "ego-log.jsonl" if jsonl else "ego-log.json",
                "logText": log_text,
            },
        )
        self.assertEqual(status, 202)
        job = self.wait_for_job(created["jobId"])
        self.assertEqual(job["state"], "completed", job.get("error"))
        return job["result"]["id"]

    def test_root_serves_the_reusable_file_loader(self):
        status, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["application"], "ResultAna")

        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('id="xodr-file"', html)
        self.assertIn('id="scenario-file"', html)
        self.assertIn('id="xodr-drop-zone"', html)
        self.assertIn('id="scenario-drop-zone"', html)
        self.assertIn('addEventListener("drop"', html)
        self.assertNotIn('id="xodr-file" name="xodr" type="file" accept=".xodr" required', html)
        self.assertIn('id="parse-progress"', html)
        self.assertIn('id="history-list"', html)
        self.assertIn('id="ego-log-file"', html)
        self.assertIn('id="ego-log-drop-zone"', html)
        self.assertIn('id="ego-history-list"', html)
        self.assertIn('request("/api/ego/import-jobs"', html)
        self.assertIn('accept=".jsonl,.json,', html)
        self.assertNotIn("JSON.parse(logText)", html)
        self.assertIn('id="viewer-frame"', html)
        self.assertIn('request("/api/import-jobs"', html)

    def test_jsonl_ego_log_imports_through_the_existing_job_api(self):
        batch_id = self.import_ego_batch(jsonl=True)

        status, batch = self.request("GET", f"/api/ego/{batch_id}")

        self.assertEqual(status, 200)
        self.assertEqual(batch["sourceName"], "ego-log.jsonl")
        self.assertEqual(batch["summary"]["runCount"], 1)
        self.assertEqual(batch["runs"][0]["stats"]["waypointCount"], 1)

    def test_invalid_jsonl_is_reported_by_the_background_job(self):
        status, created = self.request(
            "POST",
            "/api/ego/import-jobs",
            {
                "logName": "broken.results.jsonl",
                "logText": '{"jsonname":"first.json"}\n{not valid}\n',
            },
        )

        self.assertEqual(status, 202)
        job = self.wait_for_job(created["jobId"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("JSONL line 2", job["error"])

    def test_imported_files_appear_in_history_and_open_in_the_viewer(self):
        status, imported = self.request(
            "POST",
            "/api/import",
            {
                "xodrName": "simple.xodr",
                "scenarioName": "simple.json",
                "xodrText": SIMPLE_MAP,
                "scenarioText": json.dumps(SIMPLE_SCENARIO),
            },
        )

        self.assertEqual(status, 201)
        self.assertEqual(imported["summary"]["roadCount"], 1)
        status, history = self.request("GET", "/api/history")
        self.assertEqual(status, 200)
        self.assertEqual(len(history["items"]), 1)
        self.assertEqual(history["items"][0]["id"], imported["id"])

        status, html = self.request("GET", f"/view/{imported['id']}")
        self.assertEqual(status, 200)
        self.assertIn("<canvas", html)
        self.assertIn("simple.xodr", html)

    def test_json_only_import_finds_xodr_from_the_scenario_map_name(self):
        (self.map_directory / "simple.xodr").write_text(SIMPLE_MAP, encoding="utf-8")

        status, imported = self.request(
            "POST",
            "/api/import",
            {
                "scenarioName": "simple.json",
                "scenarioText": json.dumps(SIMPLE_SCENARIO),
            },
        )

        self.assertEqual(status, 201)
        self.assertEqual(imported["summary"]["roadCount"], 1)
        _, history = self.request("GET", "/api/history")
        self.assertEqual(history["items"][0]["xodrName"], "simple.xodr")

    def test_json_only_import_reports_a_missing_named_map(self):
        status, response = self.request(
            "POST",
            "/api/import",
            {
                "scenarioName": "simple.json",
                "scenarioText": json.dumps(SIMPLE_SCENARIO),
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("simple", response["error"])
        self.assertIn(str(self.map_directory), response["error"])

    def test_import_job_reports_progress_and_returns_the_saved_record(self):
        status, created = self.request(
            "POST",
            "/api/import-jobs",
            {
                "xodrName": "simple.xodr",
                "scenarioName": "simple.json",
                "xodrText": SIMPLE_MAP,
                "scenarioText": json.dumps(SIMPLE_SCENARIO),
            },
        )

        self.assertEqual(status, 202)
        self.assertIn("jobId", created)
        deadline = time.monotonic() + 5
        observed_progress = []
        while time.monotonic() < deadline:
            poll_status, job = self.request(
                "GET",
                f"/api/import-jobs/{created['jobId']}",
            )
            self.assertEqual(poll_status, 200)
            observed_progress.append(job["progress"])
            if job["state"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        else:
            self.fail("Import job did not finish.")

        self.assertEqual(job["state"], "completed")
        self.assertEqual(job["progress"], 100)
        self.assertEqual(job["result"]["summary"]["roadCount"], 1)
        self.assertEqual(observed_progress, sorted(observed_progress))
        _, history = self.request("GET", "/api/history")
        self.assertEqual(history["items"][0]["id"], job["result"]["id"])

    def test_ego_job_uses_exact_sources_and_serves_dashboard_and_map(self):
        batch_id = self.import_ego_batch()

        status, history = self.request("GET", "/api/ego/history")
        self.assertEqual(status, 200)
        self.assertEqual(history["items"][0]["id"], batch_id)
        self.assertEqual(history["items"][0]["summary"]["fallRunCount"], 1)

        status, batch = self.request("GET", f"/api/ego/{batch_id}")
        self.assertEqual(status, 200)
        self.assertEqual(batch["runs"][0]["jsonName"], "mutated.json")
        self.assertEqual(batch["runs"][0]["mapFile"], "simple.xodr")

        status, dashboard = self.request("GET", f"/ego/{batch_id}")
        self.assertEqual(status, 200)
        self.assertIn("EGO 轨迹合规分析", dashboard)
        self.assertIn(batch_id, dashboard)
        self.assertIn('id="violation-percent"', dashboard)
        self.assertIn('class="metric-label">异常运行', dashboard)
        self.assertIn("percentage.toFixed(1)", dashboard)
        self.assertIn(r"导出到 D:\download", dashboard)

        status, viewer = self.request("GET", f"/ego-view/{batch_id}/0")
        self.assertEqual(status, 200)
        self.assertIn('"analysisMode":"ego"', viewer)
        self.assertIn("fall_off_road", viewer)
        self.assertIn("collision_candidate", viewer)
        self.assertIn('"laneChange":false', viewer)
        self.assertIn('<details class="map-legend" id="map-legend">', viewer)
        self.assertIn("resultana-map-legend-open", viewer)
        self.assertIn("point-row ${routeKind(point)}", viewer)
        self.assertIn("pointDisplayColor(point, npc.color)", viewer)
        self.assertIn("classificationColor(run.kind, npc.color)", viewer)

    def test_stuck_ego_view_shows_route_landmarks_and_remaining_distance(self):
        batch_id = self.import_ego_batch(
            sim_result="FAILURE: vehicle stuck",
        )
        store = self.server.RequestHandlerClass.ego_store
        legacy_payload = store.get_run(batch_id, 0)
        legacy_payload.pop("egoStart")
        legacy_payload.pop("egoDestination")
        store.update_run_payload(batch_id, 0, legacy_payload)

        status, viewer = self.request("GET", f"/ego-view/{batch_id}/0")

        self.assertEqual(status, 200)
        self.assertIn('"egoStart":{"x":0.0,"y":1.0,"z":0.0}', viewer)
        self.assertIn(
            '"egoDestination":{"x":15.0,"y":1.0,"z":0.0}',
            viewer,
        )
        self.assertIn('"stuck":true', viewer)
        self.assertIn("function drawEgoRouteLandmarks(npc)", viewer)
        self.assertIn("--ego-start: #2563a5", viewer)
        self.assertIn("--ego-destination: #1f8a5b", viewer)
        self.assertIn("蓝色圆环：场景配置起点", viewer)
        self.assertIn("绿色靶心：目标终点", viewer)
        self.assertIn("function formatEgoLandmarkCoordinate(label, point)", viewer)
        self.assertIn("预览坐标 X ${point.x.toFixed(2)} / Y ${point.y.toFixed(2)}", viewer)
        self.assertIn("停滞时最后记录点", viewer)
        self.assertIn("距目标终点", viewer)

    def test_ego_collision_review_is_persisted(self):
        batch_id = self.import_ego_batch()

        status, review = self.request(
            "POST",
            f"/api/ego/{batch_id}/reviews/0",
            {"status": "confirmed", "note": "人工查看录像后确认"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(review["review"]["status"], "confirmed")

        self.restart_server()
        _, batch = self.request("GET", f"/api/ego/{batch_id}")
        self.assertEqual(batch["summary"]["confirmedCollisionCount"], 1)
        self.assertEqual(batch["runs"][0]["collisionReview"]["note"], "人工查看录像后确认")

    def test_ego_history_can_be_deleted(self):
        batch_id = self.import_ego_batch()

        status, response = self.request("DELETE", f"/api/ego/{batch_id}")

        self.assertEqual(status, 200)
        self.assertEqual(response, {"deleted": True})
        _, history = self.request("GET", "/api/ego/history")
        self.assertEqual(history["items"], [])
        status, _ = self.request("GET", f"/ego/{batch_id}")
        self.assertEqual(status, 404)

    def test_history_record_can_be_deleted(self):
        _, imported = self.request(
            "POST",
            "/api/import",
            {
                "xodrName": "simple.xodr",
                "scenarioName": "simple.json",
                "xodrText": SIMPLE_MAP,
                "scenarioText": json.dumps(SIMPLE_SCENARIO),
            },
        )

        status, response = self.request(
            "DELETE",
            f"/api/history/{imported['id']}",
        )

        self.assertEqual(status, 200)
        self.assertEqual(response, {"deleted": True})
        _, history = self.request("GET", "/api/history")
        self.assertEqual(history["items"], [])
        status, _ = self.request("GET", f"/view/{imported['id']}")
        self.assertEqual(status, 404)

    def test_reimporting_the_same_files_updates_one_history_record(self):
        request_body = {
            "xodrName": "simple.xodr",
            "scenarioName": "simple.json",
            "xodrText": SIMPLE_MAP,
            "scenarioText": json.dumps(SIMPLE_SCENARIO),
        }

        first_status, first = self.request("POST", "/api/import", request_body)
        second_status, second = self.request("POST", "/api/import", request_body)

        self.assertEqual((first_status, second_status), (201, 201))
        self.assertEqual(first["id"], second["id"])
        _, history = self.request("GET", "/api/history")
        self.assertEqual(len(history["items"]), 1)

    def test_invalid_scenario_is_rejected_without_creating_history(self):
        status, response = self.request(
            "POST",
            "/api/import",
            {
                "xodrName": "simple.xodr",
                "scenarioName": "broken.json",
                "xodrText": SIMPLE_MAP,
                "scenarioText": "{not valid json}",
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("error", response)
        _, history = self.request("GET", "/api/history")
        self.assertEqual(history["items"], [])

    def test_history_remains_available_after_server_restart(self):
        _, imported = self.request(
            "POST",
            "/api/import",
            {
                "xodrName": "simple.xodr",
                "scenarioName": "simple.json",
                "xodrText": SIMPLE_MAP,
                "scenarioText": json.dumps(SIMPLE_SCENARIO),
            },
        )

        self.restart_server()
        status, history = self.request("GET", "/api/history")

        self.assertEqual(status, 200)
        self.assertEqual(history["items"][0]["id"], imported["id"])


if __name__ == "__main__":
    unittest.main()
