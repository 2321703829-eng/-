import inspect
import unittest
import xml.etree.ElementTree as ET

from jsons.jsonParser import jsonParser
from xodrs.xodrParser import XParser


STACKED_ROADS = """<OpenDRIVE>
  <road name="ground" length="20" id="1" junction="-1">
    <planView><geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry></planView>
    <elevationProfile><elevation s="0" a="0" b="0" c="0" d="0" /></elevationProfile>
    <lanes><laneSection s="0"><left>
      <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
    </left><center><lane id="0" type="none" /></center></laneSection></lanes>
  </road>
  <road name="bridge" length="20" id="2" junction="-1">
    <planView><geometry s="0" x="0" y="0" hdg="0" length="20"><line /></geometry></planView>
    <elevationProfile><elevation s="0" a="10" b="0" c="0" d="0" /></elevationProfile>
    <lanes><laneSection s="0"><left>
      <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
    </left><center><lane id="0" type="none" /></center></laneSection></lanes>
  </road>
</OpenDRIVE>"""

OVERLAPPING_CONNECTED_ROADS = """<OpenDRIVE>
  <road name="incoming" length="10" id="1" junction="-1">
    <link><successor elementType="road" elementId="2" contactPoint="start" /></link>
    <planView><geometry s="0" x="0" y="0" hdg="0" length="10"><line /></geometry></planView>
    <elevationProfile><elevation s="0" a="0" b="0" c="0" d="0" /></elevationProfile>
    <lanes><laneSection s="0"><left>
      <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
    </left><center><lane id="0" type="none" /></center></laneSection></lanes>
  </road>
  <road name="outgoing" length="10" id="2" junction="-1">
    <link><predecessor elementType="road" elementId="1" contactPoint="end" /></link>
    <planView><geometry s="0" x="8" y="0" hdg="0" length="10"><line /></geometry></planView>
    <elevationProfile><elevation s="0" a="0" b="0" c="0" d="0" /></elevationProfile>
    <lanes><laneSection s="0"><left>
      <lane id="1" type="driving"><width sOffset="0" a="3" b="0" c="0" d="0" /></lane>
    </left><center><lane id="0" type="none" /></center></laneSection></lanes>
  </road>
</OpenDRIVE>"""


class JsonParserRoadLocatorCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.original_root = XParser.root
        self.original_roads = XParser.roads
        self.original_max_widths = XParser.maxWidths
        self._load_map(STACKED_ROADS)
        self.parser = jsonParser()

    def _load_map(self, map_text):
        root = ET.fromstring(map_text)
        XParser.root = root
        XParser.roads = {road.get("id"): road for road in root.findall("road")}
        XParser.maxWidths = {road_id: 3.0 for road_id in XParser.roads}

    def tearDown(self):
        XParser.root = self.original_root
        XParser.roads = self.original_roads
        XParser.maxWidths = self.original_max_widths

    def test_legacy_signatures_and_candidate_shape_are_preserved(self):
        self.assertEqual(
            list(inspect.signature(self.parser.findRoad).parameters),
            ["pos", "rot", "speed"],
        )
        self.assertEqual(
            list(inspect.signature(self.parser.projectPoint).parameters),
            ["road", "tarX", "tarY", "stop_car"],
        )

        points = self.parser.findRoad(
            {"x": 5, "y": 10, "z": 1},
            {"y": 90},
        )

        self.assertIsNotNone(points)
        self.assertEqual(len(points), 1)
        self.assertEqual(len(points[0]), 5)
        self.assertEqual(points[0][0:2], ["2", "1"])
        self.assertAlmostEqual(points[0][2], 5.0)
        self.assertAlmostEqual(points[0][3], -1.0)
        self.assertAlmostEqual(points[0][4], 0.0)

        ans, ansL = self.parser.projectPoint(XParser.roads["2"], 5, 1)
        self.assertAlmostEqual(ans, -1.0)
        self.assertAlmostEqual(ansL, 5.0)

    def test_update_car_data_uses_the_full_trajectory_without_changing_schema(self):
        self._load_map(OVERLAPPING_CONNECTED_ROADS)
        self.parser.addData(
            {
                "agents": [
                    {
                        "uid": "",
                        "transform": {
                            "position": {"x": 4, "y": 0, "z": 1},
                            "rotation": {"y": 90},
                        },
                        "waypoints": [
                            {
                                "ordinalNumber": 0,
                                "position": {"x": 9, "y": 0, "z": 1},
                                "angle": {"y": 90},
                                "speed": 5,
                            },
                            {
                                "ordinalNumber": 1,
                                "position": {"x": 12, "y": 0, "z": 1},
                                "angle": {"y": 90},
                                "speed": 5,
                            },
                        ],
                    }
                ]
            }
        )

        self.parser.updateCarData()

        road_1_points = self.parser.carPosition[-1]["1"]["1"]
        road_2_points = self.parser.carPosition[-1]["2"]["1"]
        self.assertEqual([item["ordId"] for item in road_1_points], [0])
        self.assertEqual([item["ordId"] for item in road_2_points], [1, 2])
        for item in road_1_points + road_2_points:
            self.assertEqual(
                set(item),
                {"carId", "ordId", "pos", "dis", "dhdg"},
            )

    def test_ego_candidate_lists_and_car_position_schema_are_preserved(self):
        self._load_map(OVERLAPPING_CONNECTED_ROADS)
        self.parser.addData(
            {
                "agents": [
                    {
                        "uid": "ego",
                        "transform": {
                            "position": {"x": 4, "y": 0, "z": 1},
                            "rotation": {"y": 90},
                        },
                        "destinationPoint": {
                            "position": {"x": 12, "y": 0, "z": 1},
                            "rotation": {"y": 90},
                        },
                    }
                ]
            }
        )

        self.parser.updateCarData()

        self.assertEqual(len(self.parser.egoTs[0]), 5)
        self.assertEqual(len(self.parser.egoDs[0]), 5)
        self.assertEqual(self.parser.egoTs[0][0:2], ["1", "1"])
        self.assertEqual(self.parser.egoDs[0][0:2], ["2", "1"])
        self.assertEqual(
            self.parser.carPosition[-1]["1"]["1"][0]["ordId"],
            0,
        )
        self.assertEqual(
            self.parser.carPosition[-1]["2"]["1"][0]["ordId"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
