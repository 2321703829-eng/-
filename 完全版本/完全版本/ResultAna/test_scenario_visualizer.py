import copy
import json
from pathlib import Path
import re
import tempfile
import textwrap
import unittest

from visualization import (
    build_scenario_visualization,
    build_visualization_payload,
    render_visualization_html,
)


SIMPLE_MAP = """
<OpenDRIVE>
  <road name="main" length="20" id="1" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry>
    </planView>
    <lanes><laneSection s="0">
      <left>
        <lane id="1" type="driving">
          <width sOffset="0" a="3" b="0" c="0" d="0" />
        </lane>
      </left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
</OpenDRIVE>
"""


OVERLAPPING_MAP = """
<OpenDRIVE>
  <road name="lower" length="20" id="1" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry>
    </planView>
    <lanes><laneSection s="0"><left>
      <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
    </left><center><lane id="0" type="none" /></center></laneSection></lanes>
  </road>
  <road name="upper" length="20" id="2" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry>
    </planView>
    <lanes><laneSection s="0"><left>
      <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
    </left><center><lane id="0" type="none" /></center></laneSection></lanes>
  </road>
</OpenDRIVE>
"""


ELEVATED_OVERLAPPING_MAP = """
<OpenDRIVE>
  <road name="lower" length="20" id="1" junction="-1">
    <planView><geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry></planView>
    <elevationProfile><elevation s="0" a="0" b="0" c="0" d="0" /></elevationProfile>
    <lanes><laneSection s="0"><left>
      <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
    </left><center><lane id="0" type="none" /></center></laneSection></lanes>
  </road>
  <road name="upper" length="20" id="2" junction="-1">
    <planView><geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry></planView>
    <elevationProfile><elevation s="0" a="5" b="0" c="0" d="0" /></elevationProfile>
    <lanes><laneSection s="0"><left>
      <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
    </left><center><lane id="0" type="none" /></center></laneSection></lanes>
  </road>
</OpenDRIVE>
"""


SHORT_TERMINAL_SECTION_MAP = """
<OpenDRIVE>
  <road name="short-terminal-section" length="20" id="1" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry>
    </planView>
    <lanes>
      <laneSection s="0"><left>
        <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
      </left><center><lane id="0" type="none" /></center></laneSection>
      <laneSection s="19.5"><left>
        <lane id="1" type="driving"><width sOffset="0" a="5" b="0" c="0" d="0" /></lane>
      </left><center><lane id="0" type="none" /></center></laneSection>
    </lanes>
  </road>
</OpenDRIVE>
"""


SIMPLE_SCENARIO = {
    "map": {"name": "simple"},
    "agents": [
        {
            "type": 2,
            "variant": "Sedan",
            "transform": {
                "position": {"x": 1, "y": 0, "z": 1},
                "rotation": {"x": 0, "y": 90, "z": 0},
            },
            "waypoints": [
                {
                    "ordinalNumber": 0,
                    "position": {"x": 5, "y": 0, "z": 1},
                    "angle": {"x": 0, "y": 90, "z": 0},
                    "speed": 3,
                    "waitTime": 0,
                },
                {
                    "ordinalNumber": 1,
                    "position": {"x": 10, "y": 0, "z": 1},
                    "angle": {"x": 0, "y": 90, "z": 0},
                    "speed": 4,
                    "waitTime": 0,
                },
            ],
        }
    ],
}


class ScenarioVisualizerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_builds_self_contained_map_and_npc_route_visualization(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        output_path = self.directory / "visualization.html"
        map_path.write_text(textwrap.dedent(SIMPLE_MAP).strip(), encoding="utf-8")
        scenario = copy.deepcopy(SIMPLE_SCENARIO)
        second_npc = copy.deepcopy(SIMPLE_SCENARIO["agents"][0])
        second_npc["variant"] = "SUV"
        scenario["agents"].append(second_npc)
        scenario_path.write_text(
            json.dumps(scenario),
            encoding="utf-8",
        )

        summary = build_scenario_visualization(
            map_path,
            scenario_path,
            output_path,
        )

        self.assertEqual(summary.road_count, 1)
        self.assertEqual(summary.npc_count, 2)
        self.assertEqual(summary.waypoint_count, 4)
        self.assertEqual(summary.output_path, output_path)
        html = output_path.read_text(encoding="utf-8")
        self.assertIn("<canvas", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        payload_match = re.search(
            r'<script id="visualization-data" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(payload_match)
        payload = json.loads(payload_match.group(1))
        self.assertEqual(payload["roads"][0]["id"], "1")
        self.assertEqual(payload["roads"][0]["points"][0], {"x": 0.0, "y": 0.0})
        self.assertEqual(payload["npcs"][0]["roadIds"], ["1"])
        self.assertEqual(len(payload["npcs"][0]["points"]), 3)
        self.assertNotEqual(payload["npcs"][0]["color"], payload["npcs"][1]["color"])

    def test_builds_and_renders_visualization_without_writing_an_output_file(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(textwrap.dedent(SIMPLE_MAP).strip(), encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        payload = build_visualization_payload(map_path, scenario_path)
        html = render_visualization_html(payload)

        self.assertEqual(payload["summary"]["roadCount"], 1)
        self.assertEqual(payload["summary"]["npcCount"], 1)
        self.assertIn('<script id="visualization-data" type="application/json">', html)
        self.assertNotIn("__VISUALIZATION_DATA__", html)

    def test_payload_reports_real_parsing_progress(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(textwrap.dedent(SIMPLE_MAP).strip(), encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")
        updates = []

        build_visualization_payload(
            map_path,
            scenario_path,
            progress_callback=lambda progress, message: updates.append(
                (progress, message)
            ),
        )

        self.assertEqual(updates[-1], (100, "解析完成"))
        self.assertEqual(
            [progress for progress, _ in updates],
            sorted(progress for progress, _ in updates),
        )
        self.assertTrue(any("道路" in message for _, message in updates))
        self.assertTrue(any("NPC" in message for _, message in updates))

    def test_rendered_viewer_exposes_direction_and_minimap_context(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(textwrap.dedent(SIMPLE_MAP).strip(), encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        html = render_visualization_html(
            build_visualization_payload(map_path, scenario_path)
        )

        self.assertIn(
            '<canvas id="minimap-canvas" aria-label="道路鸟瞰图"></canvas>',
            html,
        )
        self.assertIn("总览仅显示 NPC 路线；选择 NPC 后显示路径点与行驶方向", html)

    def test_rendered_viewer_uses_lightweight_overview_and_selected_point_details(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(textwrap.dedent(SIMPLE_MAP).strip(), encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        html = render_visualization_html(
            build_visualization_payload(map_path, scenario_path)
        )

        self.assertIn("function buildNpcRouteRuns(npc)", html)
        self.assertIn("function drawSelectedNpcPoints(npc)", html)
        self.assertIn("if (isSelected) drawSelectedNpcPoints(npc);", html)
        self.assertIn("if (state.selectedNpc === null) return null;", html)
        self.assertIn("function scheduleRender()", html)
        self.assertIn("requestAnimationFrame(() =>", html)
        self.assertNotIn("function drawNpcSegment(", html)

    def test_rendered_viewer_explains_transparency_and_scaled_directions(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(textwrap.dedent(SIMPLE_MAP).strip(), encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        html = render_visualization_html(
            build_visualization_payload(map_path, scenario_path)
        )

        self.assertIn("半透明道路面与清晰边界", html)
        self.assertIn("路径方向箭头随缩放增强", html)

    def test_rendered_viewer_explains_road_endpoint_caps(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(textwrap.dedent(SIMPLE_MAP).strip(), encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        html = render_visualization_html(
            build_visualization_payload(map_path, scenario_path)
        )

        self.assertIn("道路首尾分界线", html)

    def test_rendered_viewer_distinguishes_route_status_and_point_ownership(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(textwrap.dedent(SIMPLE_MAP).strip(), encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        html = render_visualization_html(
            build_visualization_payload(map_path, scenario_path)
        )

        self.assertIn("实线 / 实心：确定归属", html)
        self.assertIn("虚线 / 空心：候选归属", html)
        self.assertIn("路径点道路归属", html)

    def test_rendered_viewer_treats_ranked_candidates_as_resolved_points(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(textwrap.dedent(SIMPLE_MAP).strip(), encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        html = render_visualization_html(
            build_visualization_payload(map_path, scenario_path)
        )

        self.assertIn('point.resolution === "ranked"', html)

    def test_rendered_viewer_auto_fits_a_selected_npc_path(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(textwrap.dedent(SIMPLE_MAP).strip(), encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        html = render_visualization_html(
            build_visualization_payload(map_path, scenario_path)
        )

        self.assertIn("function fitNpcView(npc)", html)
        self.assertIn("fitNpcView(npc);", html)

    def test_payload_includes_lane_boundaries_and_sampled_widths(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(textwrap.dedent(SIMPLE_MAP).strip(), encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        payload = build_visualization_payload(map_path, scenario_path)
        lane = payload["roads"][0]["lanes"][0]

        self.assertEqual((lane["id"], lane["type"]), (1, "driving"))
        self.assertEqual(lane["widths"], [3.0] * len(lane["centerPoints"]))
        self.assertEqual(lane["innerPoints"][0], {"x": 0.0, "y": 0.0})
        self.assertEqual(lane["centerPoints"][0], {"x": 0.0, "y": 1.5})
        self.assertEqual(lane["outerPoints"][0], {"x": 0.0, "y": 3.0})

    def test_payload_includes_full_width_road_endpoint_caps(self):
        map_path = self.directory / "simple.xodr"
        scenario_path = self.directory / "simple.json"
        two_sided_map = textwrap.dedent(SIMPLE_MAP).strip().replace(
            "</left>",
            """</left><right>
        <lane id="-1" type="driving">
          <width sOffset="0" a="2" b="0" c="0" d="0" />
        </lane>
      </right>""",
        )
        map_path.write_text(two_sided_map, encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        road = build_visualization_payload(map_path, scenario_path)["roads"][0]

        self.assertEqual(
            road["startCap"],
            [{"x": 0.0, "y": 3.0}, {"x": 0.0, "y": -2.0}],
        )
        self.assertEqual(
            road["endCap"],
            [{"x": 20.0, "y": 3.0}, {"x": 20.0, "y": -2.0}],
        )

    def test_payload_uses_the_best_remaining_candidate_as_a_unique_result(self):
        map_path = self.directory / "overlapping.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(textwrap.dedent(OVERLAPPING_MAP).strip(), encoding="utf-8")
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        npc = build_visualization_payload(map_path, scenario_path)["npcs"][0]

        self.assertEqual(npc["resolvedRoadIds"], ["1"])
        self.assertEqual(npc["candidateRoadIds"], [])
        self.assertEqual(npc["resolvedPointCount"], 3)
        self.assertEqual(npc["candidatePointCount"], 0)
        self.assertEqual(npc["problemPointCount"], 0)
        self.assertTrue(
            all(point["resolution"] == "ranked" for point in npc["points"])
        )

    def test_payload_uses_scene_height_to_select_an_elevated_road(self):
        map_path = self.directory / "elevated-overlapping.xodr"
        scenario_path = self.directory / "elevated.json"
        map_path.write_text(
            textwrap.dedent(ELEVATED_OVERLAPPING_MAP).strip(),
            encoding="utf-8",
        )
        scenario = copy.deepcopy(SIMPLE_SCENARIO)
        scenario["agents"][0]["transform"]["position"]["y"] = 5
        for waypoint in scenario["agents"][0]["waypoints"]:
            waypoint["position"]["y"] = 5
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

        npc = build_visualization_payload(map_path, scenario_path)["npcs"][0]

        self.assertEqual(npc["resolvedRoadIds"], ["2"])
        self.assertTrue(all(point["resolution"] == "direct" for point in npc["points"]))

    def test_endpoint_cap_uses_width_from_short_terminal_lane_section(self):
        map_path = self.directory / "short-terminal-section.xodr"
        scenario_path = self.directory / "simple.json"
        map_path.write_text(
            textwrap.dedent(SHORT_TERMINAL_SECTION_MAP).strip(),
            encoding="utf-8",
        )
        scenario_path.write_text(json.dumps(SIMPLE_SCENARIO), encoding="utf-8")

        road = build_visualization_payload(map_path, scenario_path)["roads"][0]

        self.assertEqual(
            road["endCap"],
            [{"x": 20.0, "y": 5.0}, {"x": 20.0, "y": 0.0}],
        )


if __name__ == "__main__":
    unittest.main()
