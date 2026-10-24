import json
from pathlib import Path
import tempfile
import textwrap
import unittest

from map_locator import OpenDriveMapLocator
from visualization.ego_analyzer import (
    _suppress_lane_change_crossings,
    analyze_ego_run,
    build_ego_analysis,
    parse_ego_log,
    populate_run_roads,
)


SLOPED_MAP = """
<OpenDRIVE>
  <road name="slope" length="50" id="8" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="50"><line /></geometry>
    </planView>
    <elevationProfile>
      <elevation s="0" a="0" b="0.1" c="0" d="0" />
    </elevationProfile>
    <lanes>
      <laneSection s="0">
        <left>
          <lane id="1" type="driving">
            <width sOffset="0" a="4" b="0" c="0" d="0" />
          </lane>
        </left>
        <center><lane id="0" type="none" /></center>
      </laneSection>
    </lanes>
  </road>
</OpenDRIVE>
"""


def waypoint(
    station,
    lateral=2.0,
    *,
    yaw=90.0,
    speed=1.0,
    ordinal=0,
):
    return {
        "ordinalNumber": ordinal,
        "position": {"x": station, "y": station * 0.1, "z": lateral},
        "angle": {"x": 0.0, "y": yaw, "z": 0.0},
        "speed": speed,
    }


def run_with(waypoints, **overrides):
    run = {
        "mapname": "slope",
        "jsonname": "mutated.json",
        "waypoints": waypoints,
        "cost_time": 1.5,
        "final_distance_to_destination": 0.2,
        "max_speed": max(point["speed"] for point in waypoints),
        "sim_result": "SUCCESS: reached destination",
    }
    run.update(overrides)
    return run


class EgoAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.map_path = self.directory / "slope.xodr"
        self.map_path.write_text(
            textwrap.dedent(SLOPED_MAP).strip(),
            encoding="utf-8",
        )
        self.locator = OpenDriveMapLocator(self.map_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_crossing_side_follows_actual_ego_direction(self):
        eastbound = analyze_ego_run(
            self.locator,
            run_with([waypoint(10, 3.2, yaw=90)]),
        )
        westbound = analyze_ego_run(
            self.locator,
            run_with([waypoint(10, 3.2, yaw=-90)]),
        )

        self.assertAlmostEqual(eastbound["points"][0]["leftClearance"], 0.8)
        self.assertIn(
            "left_line_crossing",
            eastbound["points"][0]["violationTypes"],
        )
        self.assertNotIn(
            "right_line_crossing",
            eastbound["points"][0]["violationTypes"],
        )
        self.assertAlmostEqual(westbound["points"][0]["rightClearance"], 0.8)
        self.assertIn(
            "right_line_crossing",
            westbound["points"][0]["violationTypes"],
        )

    def test_adjacent_lane_change_crossings_are_suppressed(self):
        points = [
            {
                "roadId": "0",
                "laneId": lane_id,
                "laneChange": False,
                "violationTypes": violations,
            }
            for lane_id, violations in (
                (-2, []),
                (-2, []),
                (-2, []),
                (-2, ["left_line_crossing"]),
                (-2, ["left_line_crossing"]),
                (-1, ["right_line_crossing"]),
                (-1, ["right_line_crossing"]),
                (-1, []),
                (-1, []),
                (-1, []),
            )
        ]

        _suppress_lane_change_crossings(points)

        self.assertTrue(all(points[index]["laneChange"] for index in range(3, 7)))
        self.assertFalse(
            any(
                {"left_line_crossing", "right_line_crossing"}
                & set(points[index]["violationTypes"])
                for index in range(3, 7)
            )
        )

    def test_edge_excursion_that_returns_to_the_same_lane_is_not_suppressed(self):
        points = [
            {
                "roadId": "0",
                "laneId": -1,
                "laneChange": False,
                "violationTypes": (
                    ["right_line_crossing"] if 3 <= index <= 5 else []
                ),
            }
            for index in range(9)
        ]

        _suppress_lane_change_crossings(points)

        self.assertEqual(
            [
                index
                for index, point in enumerate(points)
                if "right_line_crossing" in point["violationTypes"]
            ],
            [3, 4, 5],
        )
        self.assertFalse(any(point["laneChange"] for point in points))

    def test_fall_uses_one_strictly_over_threshold_point(self):
        result = analyze_ego_run(
            self.locator,
            run_with(
                [
                    waypoint(10, speed=50.0, ordinal=0),
                    waypoint(11, speed=50.1, ordinal=1),
                ]
            ),
        )

        self.assertNotIn("fall_off_road", result["points"][0]["violationTypes"])
        self.assertIn("fall_off_road", result["points"][1]["violationTypes"])
        self.assertEqual(result["stats"]["fallPointCount"], 1)
        self.assertEqual(
            [event["type"] for event in result["events"]],
            ["fall_off_road"],
        )

    def test_detects_downhill_motion_while_heading_uphill(self):
        result = analyze_ego_run(
            self.locator,
            run_with(
                [
                    waypoint(5.0, yaw=90, speed=1.0, ordinal=0),
                    waypoint(4.7, yaw=90, speed=1.0, ordinal=1),
                    waypoint(4.3, yaw=90, speed=1.0, ordinal=2),
                ]
            ),
        )

        slides = [
            event for event in result["events"]
            if event["type"] == "longitudinal_slide"
        ]
        self.assertEqual(len(slides), 1)
        self.assertEqual(slides[0]["evidence"], "heading_uphill")
        self.assertGreater(slides[0]["downhillSurfaceDistance"], 0.5)

    def test_detects_restart_downhill_after_near_stop_only(self):
        restarted = analyze_ego_run(
            self.locator,
            run_with(
                [
                    waypoint(5.0, yaw=-90, speed=0.2, ordinal=0),
                    waypoint(4.7, yaw=-90, speed=0.2, ordinal=1),
                    waypoint(4.3, yaw=-90, speed=0.2, ordinal=2),
                ]
            ),
        )
        normal = analyze_ego_run(
            self.locator,
            run_with(
                [
                    waypoint(5.0, yaw=-90, speed=1.0, ordinal=0),
                    waypoint(4.7, yaw=-90, speed=1.0, ordinal=1),
                    waypoint(4.3, yaw=-90, speed=1.0, ordinal=2),
                ]
            ),
        )

        restarted_slide = next(
            event for event in restarted["events"]
            if event["type"] == "longitudinal_slide"
        )
        self.assertEqual(
            restarted_slide["evidence"],
            "restart_after_near_stop",
        )
        self.assertFalse(
            any(event["type"] == "longitudinal_slide" for event in normal["events"])
        )

    def test_collision_is_a_manual_review_candidate_at_the_last_point(self):
        result = analyze_ego_run(
            self.locator,
            run_with(
                [waypoint(5, ordinal=0), waypoint(6, ordinal=1)],
                sim_result="FAILURE: collision",
            ),
        )

        self.assertTrue(result["collisionCandidate"])
        self.assertEqual(result["suspectedCollisionPointIndex"], 1)
        self.assertEqual(result["collisionReview"]["status"], "unreviewed")

    def test_vehicle_stuck_is_a_run_level_event_without_a_fake_point(self):
        result = analyze_ego_run(
            self.locator,
            run_with(
                [waypoint(5, ordinal=0), waypoint(6, ordinal=1)],
                sim_result="FAILURE: vehicle stuck",
            ),
        )

        self.assertTrue(result["stuck"])
        self.assertFalse(
            any(
                "vehicle_stuck" in point["violationTypes"]
                for point in result["points"]
            )
        )

    def test_terminal_record_without_waypoints_remains_a_zero_point_run(self):
        terminal_run = run_with(
            [waypoint(5)],
            sim_result="ERROR: missing Pedestrian NavMesh",
        )
        terminal_run.update(
            {
                "waypoints": [],
                "cost_time": 0.0,
                "max_speed": None,
                "final_distance_to_destination": None,
            }
        )

        result = analyze_ego_run(self.locator, terminal_run)

        self.assertEqual(result["points"], [])
        self.assertEqual(result["events"], [])
        self.assertEqual(result["stats"]["waypointCount"], 0)
        self.assertEqual(result["simResult"], "ERROR: missing Pedestrian NavMesh")

    def test_legacy_run_keeps_working_when_scenario_source_is_missing(self):
        payload = {
            "roads": [{"id": "cached"}],
            "scenarioFile": "missing.json",
            "summary": {"roadCount": 1},
        }

        result = populate_run_roads(
            payload,
            self.directory / "missing-scenario-directory",
        )

        self.assertEqual(result["roads"], [{"id": "cached"}])
        self.assertIsNone(result["egoStart"])
        self.assertIsNone(result["egoDestination"])

    def test_batch_resolves_the_exact_recursive_scenario_file(self):
        scenario_root = self.directory / "scenarios"
        nested = scenario_root / "archive" / "random-w"
        nested.mkdir(parents=True)
        map_root = self.directory / "maps"
        map_root.mkdir()
        (map_root / "slope.xodr").write_text(SLOPED_MAP, encoding="utf-8")
        (nested / "mutated.json").write_text(
            json.dumps({"map": {"name": "slope"}}),
            encoding="utf-8",
        )
        (scenario_root / "base.json").write_text(
            json.dumps({"map": {"name": "wrong-map"}}),
            encoding="utf-8",
        )
        log_text = json.dumps([run_with([waypoint(10)])])

        result = build_ego_analysis(log_text, scenario_root, map_root)

        self.assertEqual(result["summary"]["runCount"], 1)
        self.assertEqual(result["runs"][0]["scenarioFile"], "mutated.json")
        self.assertEqual(result["runs"][0]["mapFile"], "slope.xodr")
        self.assertEqual(result["runs"][0]["mapName"], "slope")
        self.assertIsNone(result["runs"][0]["egoStart"])
        self.assertIsNone(result["runs"][0]["egoDestination"])

    def test_batch_exposes_configured_ego_start_and_destination(self):
        scenario_root = self.directory / "scenarios"
        scenario_root.mkdir()
        map_root = self.directory / "maps"
        map_root.mkdir()
        (map_root / "slope.xodr").write_text(SLOPED_MAP, encoding="utf-8")
        (scenario_root / "mutated.json").write_text(
            json.dumps(
                {
                    "map": {"name": "slope"},
                    "agents": [
                        {
                            "type": 1,
                            "transform": {
                                "position": {"x": 4.0, "y": 1.5, "z": 2.0}
                            },
                            "destinationPoint": {
                                "position": {"x": 18.0, "y": 3.5, "z": 2.5}
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = build_ego_analysis(
            json.dumps([run_with([waypoint(10)])]),
            scenario_root,
            map_root,
            use_processes=False,
        )

        run = result["runs"][0]
        self.assertEqual(run["egoStart"], {"x": 4.0, "y": 2.0, "z": 1.5})
        self.assertEqual(
            run["egoDestination"],
            {"x": 18.0, "y": 2.5, "z": 3.5},
        )

    def test_batch_prefers_the_mutated_map_beside_the_exact_scenario(self):
        scenario_root = self.directory / "scenarios"
        nested = scenario_root / "archive"
        nested.mkdir(parents=True)
        map_root = self.directory / "maps"
        map_root.mkdir()
        (nested / "mutated.json").write_text(
            json.dumps({"map": {"name": "mutated-map"}}),
            encoding="utf-8",
        )
        (nested / "mutated-map.xodr").write_text(SLOPED_MAP, encoding="utf-8")
        (map_root / "mutated-map.xodr").write_text(
            "<OpenDRIVE />",
            encoding="utf-8",
        )
        log_text = json.dumps([run_with([waypoint(10)])])

        result = build_ego_analysis(log_text, scenario_root, map_root)

        self.assertEqual(result["summary"]["runCount"], 1)
        self.assertEqual(result["runs"][0]["mapFile"], "mutated-map.xodr")
        self.assertEqual(result["runs"][0]["stats"]["resolvedPointCount"], 1)

    def test_log_accepts_jsonl_and_preserves_ego_record_fields(self):
        first = run_with([waypoint(1)])
        first.update(
            {
                "category": "Random_random_w_720",
                "scenario_index": 2,
                "completed_at": "2026-07-29T07:59:38.108291+00:00",
            }
        )
        second = run_with(
            [waypoint(2)],
            jsonname="second.json",
            sim_result="FAILURE: collision",
        )

        runs = parse_ego_log(
            "\n".join((json.dumps(first), "", json.dumps(second)))
        )

        self.assertEqual([run["jsonname"] for run in runs], [
            "mutated.json",
            "second.json",
        ])
        self.assertEqual(runs[0]["waypoints"][0]["ordinalNumber"], 0)
        self.assertEqual(runs[0]["category"], "Random_random_w_720")
        self.assertEqual(runs[1]["sim_result"], "FAILURE: collision")

    def test_log_keeps_legacy_json_array_compatibility(self):
        runs = parse_ego_log(json.dumps([run_with([waypoint(1)])]))

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["jsonname"], "mutated.json")

    def test_log_accepts_terminal_records_with_empty_waypoints(self):
        terminal_run = run_with([waypoint(1)])
        terminal_run.update(
            {
                "waypoints": [],
                "cost_time": 0.0,
                "sim_result": "ERROR: TypeError",
                "max_speed": None,
                "final_distance_to_destination": None,
                "error_type": "TypeError",
                "error_message": "invalid scene",
            }
        )

        runs = parse_ego_log(json.dumps(terminal_run))

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["waypoints"], [])
        self.assertEqual(runs[0]["error_type"], "TypeError")

    def test_log_ignores_only_an_incomplete_final_jsonl_fragment(self):
        valid = json.dumps(run_with([waypoint(1)]))

        runs = parse_ego_log(f"{valid}\n{{\"jsonname\":\"partial")

        self.assertEqual(len(runs), 1)
        self.assertEqual(
            len(parse_ego_log(f'{valid}\n{{"jsonname":"partial","waypoints":tru')),
            1,
        )
        self.assertEqual(
            len(parse_ego_log(f'{valid}\n{{"jsonname":"partial","waypoints":[],"max_speed":1e')),
            1,
        )
        with self.assertRaisesRegex(ValueError, "JSONL line 2"):
            parse_ego_log(f"{valid}\n{{not valid}}\n")
        with self.assertRaisesRegex(ValueError, "JSONL line 2"):
            parse_ego_log(
                f"{valid}\n"
                '{"jsonname":"corrupt.json","waypoints":[] trailing-garbage}'
            )

    def test_log_requires_an_original_file_name_and_waypoint_array(self):
        with self.assertRaisesRegex(ValueError, "original file name"):
            parse_ego_log(
                json.dumps(
                    [run_with([waypoint(1)], jsonname="folder/mutated.json")]
                )
            )
        empty_run = run_with([waypoint(1)])
        empty_run["waypoints"] = None
        with self.assertRaisesRegex(ValueError, "waypoints array"):
            parse_ego_log(json.dumps([empty_run]))


if __name__ == "__main__":
    unittest.main()
