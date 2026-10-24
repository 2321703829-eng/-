import math
import unittest
from xml.etree import ElementTree

from utils.lambdas import is_curve


def make_road(geometries):
    road = ElementTree.Element("road")
    plan_view = ElementTree.SubElement(road, "planView")
    for hdg, coefficients in geometries:
        geometry = ElementTree.SubElement(plan_view, "geometry", hdg=str(hdg))
        if coefficients is None:
            ElementTree.SubElement(geometry, "line")
        else:
            ElementTree.SubElement(
                geometry,
                "paramPoly3",
                bU="1",
                cU="0",
                dU="0",
                bV=str(coefficients[0]),
                cV=str(coefficients[1]),
                dV=str(coefficients[2]),
            )
    return road


class IsCurveTest(unittest.TestCase):
    def test_straight_road_is_not_curve(self):
        road = make_road([(0, None), (math.radians(2), None)])

        self.assertFalse(is_curve(road))

    def test_single_param_poly_curve_is_detected(self):
        road = make_road([(0, (0, math.tan(math.radians(3)) / 2, 0))])

        self.assertTrue(is_curve(road))

    def test_curve_returning_to_start_heading_is_detected(self):
        road = make_road([(0, (0, 0.25, -1 / 6))])

        self.assertTrue(is_curve(road))

    def test_heading_wrap_is_not_mistaken_for_large_turn(self):
        road = make_road(
            [(math.radians(179), None), (math.radians(-179), None)]
        )

        self.assertFalse(is_curve(road))


if __name__ == "__main__":
    unittest.main()
