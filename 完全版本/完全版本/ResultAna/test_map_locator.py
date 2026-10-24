import math
from pathlib import Path
import tempfile
import textwrap
import unittest
import xml.etree.ElementTree as ET

from map_locator import OpenDriveMapLocator, UnsupportedGeometryError


STRAIGHT_MAP = """
<OpenDRIVE>
  <road name="straight" length="20" id="1" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry>
    </planView>
    <lanes>
      <laneOffset s="0" a="0" b="0" c="0" d="0" />
      <laneSection s="0">
        <left>
          <lane id="1" type="driving"><width sOffset="0" a="3.5" b="0" c="0" d="0" /></lane>
          <lane id="2" type="driving"><width sOffset="0" a="3.5" b="0" c="0" d="0" /></lane>
        </left>
        <center><lane id="0" type="none" /></center>
        <right>
          <lane id="-1" type="driving"><width sOffset="0" a="3.5" b="0" c="0" d="0" /></lane>
        </right>
      </laneSection>
    </lanes>
  </road>
</OpenDRIVE>
"""


VARIABLE_WIDTH_MAP = """
<OpenDRIVE>
  <road name="sections" length="20" id="7" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry>
    </planView>
    <lanes>
      <laneSection s="0">
        <left>
          <lane id="1" type="driving">
            <width sOffset="0" a="2" b="0" c="0" d="0" />
            <width sOffset="5" a="4" b="0" c="0" d="0" />
          </lane>
        </left>
        <center><lane id="0" type="none" /></center>
      </laneSection>
      <laneSection s="10">
        <left>
          <lane id="1" type="driving"><width sOffset="0" a="1" b="0" c="0" d="0" /></lane>
        </left>
        <center><lane id="0" type="none" /></center>
      </laneSection>
    </lanes>
  </road>
</OpenDRIVE>
"""


CURVE_LENGTH = 5 * (math.sqrt(2) + math.asinh(1))
PARAM_POLY_MAP = f"""
<OpenDRIVE>
  <road name="curve" length="{CURVE_LENGTH}" id="9" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="{CURVE_LENGTH}">
        <paramPoly3 aU="0" bU="10" cU="0" dU="0"
                    aV="0" bV="0" cV="5" dV="0" pRange="normalized" />
      </geometry>
    </planView>
    <lanes>
      <laneSection s="0">
        <left>
          <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
        </left>
        <center><lane id="0" type="none" /></center>
      </laneSection>
    </lanes>
  </road>
</OpenDRIVE>
"""


ARC_LENGTH_PARAM_POLY_MAP = """
<OpenDRIVE>
  <road name="arc-length-parameter" length="10" id="10" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="10">
        <paramPoly3 aU="0" bU="1" cU="0" dU="0"
                    aV="0" bV="0" cV="0" dV="0" pRange="arcLength" />
      </geometry>
    </planView>
    <lanes>
      <laneSection s="0">
        <left>
          <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
        </left>
        <center><lane id="0" type="none" /></center>
      </laneSection>
    </lanes>
  </road>
</OpenDRIVE>
"""


OVERLAP_MAP = """
<OpenDRIVE>
  <road name="east" length="20" id="1" junction="-1">
    <planView><geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
      <right><lane id="-1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></right>
    </laneSection></lanes>
  </road>
  <road name="west" length="20" id="2" junction="-1">
    <planView><geometry s="0" x="20" y="0" hdg="3.141592653589793" length="20"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
      <right><lane id="-1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></right>
    </laneSection></lanes>
  </road>
</OpenDRIVE>
"""


ELEVATED_OVERLAP_MAP = """
<OpenDRIVE>
  <road name="ground" length="20" id="1" junction="-1">
    <planView><geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry></planView>
    <elevationProfile><elevation s="0" a="0" b="0" c="0" d="0" /></elevationProfile>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
  <road name="elevated" length="20" id="2" junction="-1">
    <planView><geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry></planView>
    <elevationProfile><elevation s="0" a="4" b="0.1" c="0" d="0" /></elevationProfile>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
</OpenDRIVE>
"""


PARTIAL_OVERLAP_MAP = """
<OpenDRIVE>
  <road name="through" length="30" id="1" junction="-1">
    <planView><geometry s="0" x="0" y="0" hdg="0" length="30"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
  <road name="overlap" length="10" id="2" junction="-1">
    <planView><geometry s="0" x="10" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
</OpenDRIVE>
"""


TOPOLOGY_OVERLAP_MAP = """
<OpenDRIVE>
  <road name="entry" length="10" id="1" junction="-1">
    <link><successor elementType="road" elementId="2" contactPoint="start" /></link>
    <planView><geometry s="0" x="0" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
  <road name="connected" length="10" id="2" junction="-1">
    <link>
      <predecessor elementType="road" elementId="1" contactPoint="end" />
      <successor elementType="road" elementId="4" contactPoint="start" />
    </link>
    <planView><geometry s="0" x="10" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
  <road name="disconnected" length="10" id="3" junction="-1">
    <planView><geometry s="0" x="10" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
  <road name="exit" length="10" id="4" junction="-1">
    <link><predecessor elementType="road" elementId="2" contactPoint="end" /></link>
    <planView><geometry s="0" x="20" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
</OpenDRIVE>
"""


DISTANT_CONNECTION_OVERLAP_MAP = """
<OpenDRIVE>
  <road name="entry" length="10" id="1" junction="-1">
    <link><successor elementType="road" elementId="2" contactPoint="start" /></link>
    <planView><geometry s="0" x="-10" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
  <road name="outbound" length="30" id="2" junction="-1">
    <link>
      <predecessor elementType="road" elementId="1" contactPoint="end" />
      <successor elementType="road" elementId="3" contactPoint="start" />
    </link>
    <planView><geometry s="0" x="0" y="0" hdg="0" length="30"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
      <right><lane id="-1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></right>
    </laneSection></lanes>
  </road>
  <road name="return" length="30" id="3" junction="-1">
    <link>
      <predecessor elementType="road" elementId="2" contactPoint="end" />
      <successor elementType="road" elementId="5" contactPoint="start" />
    </link>
    <planView><geometry s="0" x="30" y="0" hdg="3.141592653589793" length="30"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
      <right><lane id="-1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></right>
    </laneSection></lanes>
  </road>
  <road name="exit" length="10" id="5" junction="-1">
    <link><predecessor elementType="road" elementId="3" contactPoint="end" /></link>
    <planView><geometry s="0" x="0" y="0" hdg="-1.5707963267948966" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
</OpenDRIVE>
"""


TURN_OVERLAP_MAP = """
<OpenDRIVE>
  <road name="entry" length="10" id="1" junction="-1">
    <link><successor elementType="road" elementId="2" contactPoint="start" /></link>
    <planView><geometry s="0" x="0" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
      <right><lane id="-1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></right>
    </laneSection></lanes>
  </road>
  <road name="turn" length="10" id="2" junction="-1">
    <link><predecessor elementType="road" elementId="1" contactPoint="end" /></link>
    <planView><geometry s="0" x="10" y="0" hdg="1.5707963267948966" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
      <right><lane id="-1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></right>
    </laneSection></lanes>
  </road>
  <road name="overlap" length="10" id="3" junction="-1">
    <planView><geometry s="0" x="10" y="0" hdg="1.5707963267948966" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
      <right><lane id="-1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></right>
    </laneSection></lanes>
  </road>
</OpenDRIVE>
"""


SAME_ROAD_JUMP_OVERLAP_MAP = """
<OpenDRIVE>
  <road name="jump" length="100" id="1" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="10"><line /></geometry>
      <geometry s="90" x="0" y="2" hdg="0" length="10"><line /></geometry>
    </planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
  <road name="entry" length="1" id="2" junction="-1">
    <link><successor elementType="road" elementId="1" contactPoint="start" /></link>
    <planView><geometry s="0" x="0" y="2" hdg="0" length="1"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="1" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
</OpenDRIVE>
"""


RIGHT_ANCHOR_OVERLAP_MAP = """
<OpenDRIVE>
  <road name="left" length="10" id="1" junction="-1">
    <link><successor elementType="road" elementId="2" contactPoint="start" /></link>
    <planView><geometry s="0" x="0" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
  <road name="right" length="10" id="2" junction="-1">
    <link><predecessor elementType="road" elementId="1" contactPoint="end" /></link>
    <planView><geometry s="0" x="8" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
</OpenDRIVE>
"""


CONVERGING_OVERLAP_MAP = """
<OpenDRIVE>
  <road name="left-route" length="10" id="1" junction="-1">
    <link><successor elementType="road" elementId="3" contactPoint="start" /></link>
    <planView><geometry s="0" x="0" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
  <road name="overlapping-route" length="6" id="2" junction="-1">
    <link><successor elementType="road" elementId="3" contactPoint="start" /></link>
    <planView><geometry s="0" x="5" y="0" hdg="0" length="6"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
  <road name="common-exit" length="10" id="3" junction="-1">
    <planView><geometry s="0" x="11" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
</OpenDRIVE>
"""


DIRECTIONAL_TERMINAL_OVERLAP_MAP = """
<OpenDRIVE>
  <road name="entry" length="10" id="1" junction="-1">
    <link><successor elementType="junction" elementId="100" /></link>
    <planView><geometry s="0" x="0" y="0" hdg="0" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <center><lane id="0" type="none" /></center>
      <right><lane id="-1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></right>
    </laneSection></lanes>
  </road>
  <road name="aligned" length="10" id="2" junction="100">
    <planView><geometry s="0" x="20" y="0" hdg="3.141592653589793" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <left><lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></left>
      <center><lane id="0" type="none" /></center>
    </laneSection></lanes>
  </road>
  <road name="turning" length="10" id="3" junction="100">
    <planView><geometry s="0" x="10" y="0" hdg="0.349065850398866" length="10"><line /></geometry></planView>
    <lanes><laneSection s="0">
      <center><lane id="0" type="none" /></center>
      <right><lane id="-1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane></right>
    </laneSection></lanes>
  </road>
  <junction id="100" name="fork">
    <connection id="0" incomingRoad="1" connectingRoad="2" contactPoint="end" />
    <connection id="1" incomingRoad="1" connectingRoad="3" contactPoint="start" />
  </junction>
</OpenDRIVE>
"""


class OpenDriveMapLocatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_map(self, name, contents):
        path = self.directory / name
        path.write_text(textwrap.dedent(contents).strip(), encoding="utf-8")
        return path

    def test_locates_left_right_and_outer_lanes(self):
        locator = OpenDriveMapLocator(self.write_map("straight.xodr", STRAIGHT_MAP))

        left = locator.locate_xy(10, 1)[0]
        right = locator.locate_xy(10, -1)[0]
        outer = locator.locate_xy(10, 4)[0]

        self.assertEqual((left.road_id, left.lane_id), ("1", 1))
        self.assertEqual(right.lane_id, -1)
        self.assertEqual(outer.lane_id, 2)
        self.assertAlmostEqual(left.s, 10)
        self.assertAlmostEqual(left.t, 1)

    def test_waypoint_adapter_uses_x_z_and_unity_yaw(self):
        locator = OpenDriveMapLocator(self.write_map("straight.xodr", STRAIGHT_MAP))

        match = locator.locate_waypoint({"x": 5, "y": 0, "z": -2}, 90)

        self.assertIsNotNone(match)
        self.assertEqual(match.lane_id, -1)
        self.assertAlmostEqual(match.heading_error, 0)
        self.assertAlmostEqual(match.reference_elevation, 0)
        self.assertAlmostEqual(match.height_error, 0)

    def test_returns_none_outside_road_and_past_endpoint(self):
        locator = OpenDriveMapLocator(self.write_map("straight.xodr", STRAIGHT_MAP))

        self.assertIsNone(locator.locate_waypoint({"x": 10, "z": 8}))
        self.assertIsNone(locator.locate_waypoint({"x": 20.1, "z": 1}))
        self.assertEqual(locator.locate_waypoint({"x": 20, "z": 1}).lane_id, 1)

    def test_lane_boundary_returns_both_adjacent_lanes(self):
        locator = OpenDriveMapLocator(self.write_map("straight.xodr", STRAIGHT_MAP))

        matches = locator.locate_xy(10, 3.5)

        self.assertEqual({match.lane_id for match in matches}, {1, 2})
        self.assertEqual(matches[0].lane_id, 1)

    def test_applies_lane_offset(self):
        shifted_map = STRAIGHT_MAP.replace('a="0"', 'a="1"', 1)
        locator = OpenDriveMapLocator(self.write_map("shifted.xodr", shifted_map))

        self.assertEqual(locator.locate_xy(10, 0.5)[0].lane_id, -1)
        self.assertEqual(locator.locate_xy(10, 1.5)[0].lane_id, 1)

    def test_uses_width_record_and_current_lane_section(self):
        locator = OpenDriveMapLocator(
            self.write_map("variable.xodr", VARIABLE_WIDTH_MAP)
        )

        self.assertEqual(locator.locate_xy(4, 3), ())
        self.assertEqual(locator.locate_xy(6, 3)[0].lane_id, 1)
        self.assertEqual(locator.locate_xy(15, 1.5), ())

    def test_lane_geometry_reports_edges_type_width_and_grade(self):
        sloped_map = STRAIGHT_MAP.replace(
            "</planView>",
            (
                "</planView><elevationProfile>"
                '<elevation s="0" a="2" b="0.05" c="0.001" d="0" />'
                "</elevationProfile>"
            ),
        )
        locator = OpenDriveMapLocator(self.write_map("sloped.xodr", sloped_map))

        left = locator.locate_xy(10, 1)[0]
        right = locator.locate_xy(10, -1)[0]
        left_geometry = locator.lane_geometry(left)
        right_geometry = locator.lane_geometry(right)

        self.assertEqual(left_geometry.lane_type, "driving")
        self.assertAlmostEqual(left_geometry.inner_t, 0)
        self.assertAlmostEqual(left_geometry.outer_t, 3.5)
        self.assertAlmostEqual(left_geometry.width, 3.5)
        self.assertAlmostEqual(left_geometry.grade, 0.07)
        self.assertAlmostEqual(right_geometry.inner_t, 0)
        self.assertAlmostEqual(right_geometry.outer_t, -3.5)

    def test_projects_normalized_param_poly3(self):
        locator = OpenDriveMapLocator(self.write_map("curve.xodr", PARAM_POLY_MAP))
        parameter = 0.5
        tangent_length = math.hypot(10, 10 * parameter)
        reference_x = 10 * parameter
        reference_y = 5 * parameter**2
        target_x = reference_x - (10 * parameter / tangent_length)
        target_y = reference_y + (10 / tangent_length)

        match = locator.locate_xy(target_x, target_y)[0]

        expected_s = 5 * (
            parameter * math.sqrt(1 + parameter**2) + math.asinh(parameter)
        )
        self.assertEqual((match.road_id, match.lane_id), ("9", 1))
        self.assertAlmostEqual(match.s, expected_s, places=6)
        self.assertAlmostEqual(match.t, 1, places=6)
        self.assertAlmostEqual(match.reference_heading, math.atan2(5, 10), places=6)

    def test_rejects_distant_param_poly3_endpoint_projection(self):
        locator = OpenDriveMapLocator(self.write_map("curve.xodr", PARAM_POLY_MAP))

        self.assertEqual(locator.locate_xy(30, 0), ())

    def test_projects_arc_length_param_poly3(self):
        locator = OpenDriveMapLocator(
            self.write_map("arc_length.xodr", ARC_LENGTH_PARAM_POLY_MAP)
        )

        match = locator.locate_xy(5, 1)[0]

        self.assertEqual((match.road_id, match.lane_id), ("10", 1))
        self.assertAlmostEqual(match.s, 5)
        self.assertAlmostEqual(match.t, 1)

    def test_acceleration_keeps_rotated_param_poly3_candidates(self):
        rotated_map = PARAM_POLY_MAP.replace('hdg="0"', 'hdg="0.7"')
        map_path = self.write_map("rotated-param.xodr", rotated_map)
        accelerated = OpenDriveMapLocator(map_path)
        full_scan = OpenDriveMapLocator(map_path, enable_acceleration=False)

        for parameter in (0.2, 0.8):
            local_u = 10 * parameter
            local_v = 5 * parameter**2
            heading = 0.7
            center_x = math.cos(heading) * local_u - math.sin(heading) * local_v
            center_y = math.sin(heading) * local_u + math.cos(heading) * local_v
            position_x = center_x - math.sin(heading)
            position_y = center_y + math.cos(heading)

            self.assertEqual(
                accelerated.locate_xy(position_x, position_y),
                full_scan.locate_xy(position_x, position_y),
            )

    def test_heading_orders_overlapping_roads(self):
        locator = OpenDriveMapLocator(self.write_map("overlap.xodr", OVERLAP_MAP))

        matches = locator.locate_xy(10, 1, heading_rad=0)

        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0].road_id, "1")
        self.assertAlmostEqual(matches[0].heading_error, 0)

    def test_waypoint_height_selects_the_matching_vertical_road(self):
        locator = OpenDriveMapLocator(
            self.write_map("elevated-overlap.xodr", ELEVATED_OVERLAP_MAP)
        )

        ground = locator.locate_waypoint({"x": 10, "y": 0.2, "z": 1})
        elevated = locator.locate_waypoint({"x": 10, "y": 5.2, "z": 1})

        self.assertEqual(ground.road_id, "1")
        self.assertEqual(elevated.road_id, "2")
        self.assertAlmostEqual(elevated.reference_elevation, 5.0)
        self.assertAlmostEqual(elevated.height_error, 0.2)

    def test_trajectory_height_resolves_a_stacked_road_without_2d_anchors(self):
        locator = OpenDriveMapLocator(
            self.write_map("elevated-trajectory.xodr", ELEVATED_OVERLAP_MAP)
        )

        matches = locator.locate_trajectory(
            [
                {"x": 5, "y": 4.5, "z": 1},
                {"x": 10, "y": 5.0, "z": 1},
                {"x": 15, "y": 5.5, "z": 1},
            ]
        )

        self.assertEqual(
            [(match.location.road_id, match.resolution) for match in matches],
            [("2", "direct"), ("2", "direct"), ("2", "direct")],
        )

    def test_missing_height_preserves_the_existing_2d_candidates(self):
        locator = OpenDriveMapLocator(
            self.write_map("optional-height.xodr", ELEVATED_OVERLAP_MAP)
        )

        candidates = locator.locate_xy(10, 1)
        best = locator.locate_waypoint({"x": 10, "z": 1})

        self.assertEqual([candidate.road_id for candidate in candidates], ["1", "2"])
        self.assertTrue(all(candidate.height_error is None for candidate in candidates))
        self.assertEqual(best.road_id, "1")

    def test_accelerated_queries_match_the_full_scan(self):
        roads = []
        for index in range(48):
            road_y = index * 20
            roads.append(
                f'''<road name="road-{index}" length="40" id="{index + 1}" junction="-1">
  <planView><geometry s="0" x="0" y="{road_y}" hdg="0" length="40"><line /></geometry></planView>
  <lanes><laneSection s="0"><left>
    <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
  </left><center><lane id="0" type="none" /></center></laneSection></lanes>
</road>'''
            )
        map_text = "<OpenDRIVE>" + "".join(roads) + "</OpenDRIVE>"
        map_path = self.write_map("accelerated-grid.xodr", map_text)
        accelerated = OpenDriveMapLocator(map_path)
        full_scan = OpenDriveMapLocator(map_path, enable_acceleration=False)

        positions = [
            {"x": float(index % 40), "z": float((index % 48) * 20 + 1)}
            for index in range(240)
        ]
        for position in positions:
            accelerated_matches = accelerated.locate_xy(
                position["x"],
                position["z"],
                road_edge_margin=0.25,
                endpoint_tolerance=0.5,
            )
            full_scan_matches = full_scan.locate_xy(
                position["x"],
                position["z"],
                road_edge_margin=0.25,
                endpoint_tolerance=0.5,
            )
            self.assertEqual(accelerated_matches, full_scan_matches)

    def test_height_tolerance_rejects_or_accepts_a_vertical_offset(self):
        locator = OpenDriveMapLocator(
            self.write_map("height-tolerance.xodr", ELEVATED_OVERLAP_MAP)
        )

        strict = locator.locate_waypoint({"x": 10, "y": 8, "z": 1})
        relaxed = locator.locate_waypoint(
            {"x": 10, "y": 8, "z": 1},
            height_tolerance=3.1,
        )

        self.assertIsNone(strict)
        self.assertEqual(relaxed.road_id, "2")
        self.assertAlmostEqual(relaxed.height_error, 3.0)

    def test_trajectory_uses_unique_anchors_to_resolve_partial_overlap(self):
        locator = OpenDriveMapLocator(
            self.write_map("partial_overlap.xodr", PARTIAL_OVERLAP_MAP)
        )

        matches = locator.locate_trajectory(
            [
                {"x": 5, "z": 1},
                {"x": 12, "z": 1},
                {"x": 18, "z": 1},
                {"x": 25, "z": 1},
            ]
        )

        self.assertEqual(
            [match.location.road_id for match in matches],
            ["1", "1", "1", "1"],
        )
        self.assertEqual(
            [match.resolution for match in matches],
            ["direct", "propagated", "propagated", "direct"],
        )
        self.assertEqual(
            {candidate.road_id for candidate in matches[1].candidates},
            {"1", "2"},
        )

    def test_trajectory_allows_unique_single_anchor_propagation(self):
        locator = OpenDriveMapLocator(
            self.write_map("single_anchor.xodr", PARTIAL_OVERLAP_MAP)
        )

        matches = locator.locate_trajectory(
            [
                {"x": 5, "z": 1},
                {"x": 12, "z": 1},
                {"x": 18, "z": 1},
            ]
        )

        self.assertEqual(
            [match.location.road_id for match in matches],
            ["1", "1", "1"],
        )
        self.assertEqual(
            [match.resolution for match in matches],
            ["direct", "propagated", "propagated"],
        )

    def test_trajectory_uses_road_links_between_different_anchors(self):
        locator = OpenDriveMapLocator(
            self.write_map("topology_overlap.xodr", TOPOLOGY_OVERLAP_MAP)
        )

        matches = locator.locate_trajectory(
            [
                {"x": 5, "z": 1},
                {"x": 12, "z": 1},
                {"x": 18, "z": 1},
                {"x": 25, "z": 1},
            ]
        )

        self.assertEqual(
            [match.location.road_id for match in matches],
            ["1", "2", "2", "4"],
        )
        self.assertEqual(
            [match.resolution for match in matches],
            ["direct", "propagated", "propagated", "direct"],
        )

    def test_trajectory_can_bridge_a_small_gap_between_connected_road_ends(self):
        root = ET.fromstring(textwrap.dedent(TOPOLOGY_OVERLAP_MAP).strip())
        root.remove(root.find("road[@id='3']"))
        root.find("road[@id='2']/planView/geometry").set("x", "10.1")
        locator = OpenDriveMapLocator(
            self.write_map(
                "endpoint_gap.xodr",
                ET.tostring(root, encoding="unicode"),
            )
        )
        positions = [
            {"x": 9.9, "z": 1},
            {"x": 10.05, "z": 1},
            {"x": 10.2, "z": 1},
        ]

        strict_matches = locator.locate_trajectory(positions)
        bridged_matches = locator.locate_trajectory(
            positions,
            road_endpoint_margin=0.1,
        )

        self.assertEqual(strict_matches[1].resolution, "unmatched")
        self.assertEqual(bridged_matches[1].location.road_id, "2")
        self.assertEqual(bridged_matches[1].resolution, "propagated")
        self.assertEqual(
            {candidate.road_id for candidate in bridged_matches[1].candidates},
            {"1", "2"},
        )

    def test_trajectory_uses_junction_connections(self):
        root = ET.fromstring(textwrap.dedent(TOPOLOGY_OVERLAP_MAP).strip())
        entry_successor = root.find("road[@id='1']/link/successor")
        entry_successor.set("elementType", "junction")
        entry_successor.set("elementId", "10")
        entry_successor.attrib.pop("contactPoint", None)
        connected_predecessor = root.find("road[@id='2']/link/predecessor")
        connected_predecessor.set("elementType", "junction")
        connected_predecessor.set("elementId", "10")
        connected_predecessor.attrib.pop("contactPoint", None)
        junction = ET.SubElement(root, "junction", {"id": "10", "name": "test"})
        ET.SubElement(
            junction,
            "connection",
            {
                "id": "0",
                "incomingRoad": "1",
                "connectingRoad": "2",
                "contactPoint": "start",
            },
        )
        locator = OpenDriveMapLocator(
            self.write_map(
                "junction_overlap.xodr",
                ET.tostring(root, encoding="unicode"),
            )
        )

        matches = locator.locate_trajectory(
            [
                {"x": 5, "z": 1},
                {"x": 12, "z": 1},
                {"x": 18, "z": 1},
                {"x": 25, "z": 1},
            ]
        )

        self.assertEqual(
            [match.location.road_id for match in matches],
            ["1", "2", "2", "4"],
        )

    def test_trajectory_preserves_multiple_routes_between_unique_anchors(self):
        root = ET.fromstring(textwrap.dedent(TOPOLOGY_OVERLAP_MAP).strip())
        disconnected_road = root.find("road[@id='3']")
        link = ET.Element("link")
        ET.SubElement(
            link,
            "predecessor",
            {
                "elementType": "road",
                "elementId": "1",
                "contactPoint": "end",
            },
        )
        ET.SubElement(
            link,
            "successor",
            {
                "elementType": "road",
                "elementId": "4",
                "contactPoint": "start",
            },
        )
        disconnected_road.insert(0, link)
        locator = OpenDriveMapLocator(
            self.write_map(
                "multiple_routes.xodr",
                ET.tostring(root, encoding="unicode"),
            )
        )

        matches = locator.locate_trajectory(
            [
                {"x": 5, "z": 1},
                {"x": 12, "z": 1},
                {"x": 18, "z": 1},
                {"x": 25, "z": 1},
            ]
        )

        self.assertEqual(matches[0].location.road_id, "1")
        self.assertIsNone(matches[1].location)
        self.assertIsNone(matches[2].location)
        self.assertEqual(matches[3].location.road_id, "4")
        self.assertEqual(matches[1].resolution, "ambiguous")
        self.assertEqual(matches[1].confidence, 0.5)
        self.assertEqual(
            {candidate.road_id for candidate in matches[1].feasible_candidates},
            {"2", "3"},
        )

    def test_trajectory_rejects_switches_away_from_connected_endpoints(self):
        locator = OpenDriveMapLocator(
            self.write_map(
                "distant_connection.xodr",
                DISTANT_CONNECTION_OVERLAP_MAP,
            )
        )

        matches = locator.locate_trajectory(
            [
                {"x": -5, "z": 1},
                {"x": 10, "z": 1},
                {"x": 12, "z": 1},
                {"x": 1, "z": -5},
            ]
        )

        self.assertEqual(matches[0].location.road_id, "1")
        self.assertEqual(matches[1].resolution, "inconsistent")
        self.assertEqual(matches[2].resolution, "inconsistent")
        self.assertEqual(matches[1].confidence, 0.0)
        self.assertEqual(matches[3].location.road_id, "5")

    def test_trajectory_allows_reasonable_distance_around_a_turn(self):
        locator = OpenDriveMapLocator(
            self.write_map("turn_overlap.xodr", TURN_OVERLAP_MAP)
        )

        matches = locator.locate_trajectory(
            [
                {"x": 6, "z": 0},
                {"x": 10, "z": 4},
            ]
        )

        self.assertEqual(matches[0].location.road_id, "1")
        self.assertEqual(matches[1].location.road_id, "2")
        self.assertEqual(matches[1].resolution, "propagated")

    def test_trajectory_rejects_same_road_station_jump(self):
        locator = OpenDriveMapLocator(
            self.write_map("same_road_jump.xodr", SAME_ROAD_JUMP_OVERLAP_MAP)
        )

        matches = locator.locate_trajectory(
            [
                {"x": 1, "z": 2},
                {"x": 1, "z": 0},
            ]
        )

        self.assertIsNotNone(matches[0].location)
        self.assertEqual(matches[0].location.road_id, "2")
        self.assertEqual(matches[0].resolution, "propagated")
        self.assertEqual(matches[1].location.road_id, "1")

    def test_trajectory_prefers_the_right_anchor_through_overlap(self):
        locator = OpenDriveMapLocator(
            self.write_map("right_anchor.xodr", RIGHT_ANCHOR_OVERLAP_MAP)
        )

        matches = locator.locate_trajectory(
            [
                {"x": 6, "z": 1},
                {"x": 9, "z": 1},
                {"x": 12, "z": 1},
            ]
        )

        self.assertEqual(
            [match.location.road_id if match.location else None for match in matches],
            ["1", "2", "2"],
        )
        self.assertEqual(matches[1].resolution, "propagated")

    def test_trajectory_keeps_the_incoming_route_before_a_common_successor(self):
        locator = OpenDriveMapLocator(
            self.write_map("converging_overlap.xodr", CONVERGING_OVERLAP_MAP)
        )

        matches = locator.locate_trajectory(
            [
                {"x": 4, "z": 1},
                {"x": 6, "z": 1},
                {"x": 8, "z": 1},
                {"x": 9.5, "z": 1},
                {"x": 10.5, "z": 1},
                {"x": 11.5, "z": 1},
            ]
        )

        self.assertEqual(
            [match.location.road_id if match.location else None for match in matches],
            ["1", "1", "1", "1", "2", "3"],
        )
        self.assertEqual(
            [match.resolution for match in matches[1:4]],
            ["propagated", "propagated", "propagated"],
        )

    def test_trajectory_uses_lane_direction_for_a_terminal_anchor(self):
        locator = OpenDriveMapLocator(
            self.write_map(
                "directional_terminal.xodr",
                DIRECTIONAL_TERMINAL_OVERLAP_MAP,
            )
        )

        matches = locator.locate_trajectory(
            [
                {"x": 9, "z": -1},
                {"x": 10.5, "z": -1},
                {"x": 11.5, "z": -1},
                {"x": 12.5, "z": -1},
            ],
            yaw_degrees=[90, 90, 90, 90],
        )

        self.assertEqual(
            [match.location.road_id if match.location else None for match in matches],
            ["1", "2", "2", "2"],
        )
        self.assertTrue(
            all(match.resolution == "propagated" for match in matches[1:])
        )

    def test_trajectory_preserves_ambiguity_without_anchors(self):
        locator = OpenDriveMapLocator(self.write_map("ambiguous.xodr", OVERLAP_MAP))

        matches = locator.locate_trajectory(
            [
                {"x": 5, "z": 1},
                {"x": 10, "z": 1},
                {"x": 15, "z": 1},
            ]
        )

        self.assertTrue(all(match.location is None for match in matches))
        self.assertTrue(all(match.resolution == "ambiguous" for match in matches))
        self.assertTrue(all(match.confidence == 0.5 for match in matches))
        self.assertEqual(
            locator.locate_waypoint({"x": 10, "z": 1}).road_id,
            "1",
        )

    def test_trajectory_can_rank_the_best_remaining_candidate(self):
        root = ET.fromstring(textwrap.dedent(OVERLAP_MAP).strip())
        second_geometry = root.find("road[@id='2']/planView/geometry")
        second_geometry.set("y", "-0.4")
        locator = OpenDriveMapLocator(
            self.write_map(
                "direction_ranked.xodr",
                ET.tostring(root, encoding="unicode"),
            )
        )

        default_match = locator.locate_trajectory(
            [{"x": 10, "z": 1}],
        )[0]
        ranked_match = locator.locate_trajectory(
            [{"x": 10, "z": 1}],
            resolve_remaining=True,
        )[0]

        self.assertEqual(default_match.resolution, "ambiguous")
        self.assertEqual(ranked_match.location.road_id, "2")
        self.assertEqual(ranked_match.resolution, "ranked")
        self.assertEqual(ranked_match.confidence, 0.5)
        self.assertEqual(
            {candidate.road_id for candidate in ranked_match.candidates},
            {"1", "2"},
        )

    def test_trajectory_resolution_is_road_level_and_reports_unmatched_points(self):
        locator = OpenDriveMapLocator(self.write_map("straight.xodr", STRAIGHT_MAP))

        matches = locator.locate_trajectory(
            [
                {"x": 10, "z": 3.5},
                {"x": 10, "z": 8},
            ]
        )

        self.assertEqual(matches[0].resolution, "direct")
        self.assertEqual(matches[0].location.road_id, "1")
        self.assertEqual(
            {candidate.lane_id for candidate in matches[0].candidates},
            {1, 2},
        )
        self.assertEqual(matches[1].resolution, "unmatched")
        self.assertIsNone(matches[1].location)
        self.assertEqual(matches[1].confidence, 0.0)

    def test_trajectory_can_match_a_point_just_outside_the_road_edge(self):
        locator = OpenDriveMapLocator(self.write_map("straight.xodr", STRAIGHT_MAP))

        self.assertEqual(locator.locate_xy(10, 7.4), ())

        match = locator.locate_trajectory(
            [{"x": 10, "z": 7.4}],
            road_edge_margin=0.5,
        )[0]

        self.assertEqual(match.location.road_id, "1")
        self.assertEqual(match.location.lane_id, 2)
        self.assertEqual(match.resolution, "direct")

    def test_trajectory_validates_parallel_yaw_values(self):
        locator = OpenDriveMapLocator(self.write_map("straight.xodr", STRAIGHT_MAP))

        match = locator.locate_trajectory(
            [{"x": 5, "z": -2}],
            yaw_degrees=[90],
        )[0]

        self.assertAlmostEqual(match.location.heading_error, 0)
        with self.assertRaises(ValueError):
            locator.locate_trajectory(
                [{"x": 5, "z": -2}],
                yaw_degrees=[],
            )
        with self.assertRaises(ValueError):
            locator.locate_trajectory(
                [{"x": 5, "z": -2}],
                road_edge_margin=-0.1,
            )
        with self.assertRaises(ValueError):
            locator.locate_trajectory(
                [{"x": 5, "z": -2}],
                road_endpoint_margin=-0.1,
            )
        with self.assertRaises(ValueError):
            locator.locate_trajectory(
                [{"x": 5, "y": 0, "z": -2}],
                height_tolerance=-0.1,
            )

    def test_rejects_missing_malformed_and_unsupported_maps(self):
        with self.assertRaises(FileNotFoundError):
            OpenDriveMapLocator(self.directory / "missing.xodr")

        malformed = self.write_map("malformed.xodr", "<OpenDRIVE>")
        with self.assertRaises(ET.ParseError):
            OpenDriveMapLocator(malformed)

        unsupported = self.write_map(
            "arc.xodr",
            """
            <OpenDRIVE><road id="1" length="10"><planView>
              <geometry s="0" x="0" y="0" hdg="0" length="10">
                <arc curvature="0.1" />
              </geometry>
            </planView></road></OpenDRIVE>
            """,
        )
        with self.assertRaises(UnsupportedGeometryError):
            OpenDriveMapLocator(unsupported)

    def test_rejects_invalid_waypoint_values(self):
        locator = OpenDriveMapLocator(self.write_map("straight.xodr", STRAIGHT_MAP))

        with self.assertRaises(ValueError):
            locator.locate_waypoint({"x": 1})
        with self.assertRaises(ValueError):
            locator.locate_waypoint({"x": "not-a-number", "z": 1})
        with self.assertRaises(ValueError):
            locator.locate_waypoint({"x": 1, "y": "not-a-number", "z": 1})


if __name__ == "__main__":
    unittest.main()
