"""Check real-geometry fitting parity without model calls or database writes."""
import asyncio
import copy
import math
import os
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import numpy as np

from handler import agent_dispatcher as dispatcher_module
from handler.curve_optimizer import CurveOptimizationService
from handler.handlerCurve import handlerC
from jsons.jsonParser import JParser
from utils import agent_road_context
from xodrs.xodrDataGetter import xDataGetter
from xodrs.xodrParser import XParser


def load_split_curve():
    """Load two exact halves of one curved cubic, with empty vehicle context."""
    points = np.array([[0., 0.], [3., 2.], [7., 2.], [10., 0.]])
    first = (points[:-1] + points[1:]) / 2
    second = (first[:-1] + first[1:]) / 2
    middle = (second[0] + second[1]) / 2
    halves = ([points[0], first[0], second[0], middle],
              [middle, second[1], first[2], points[3]])
    root = ET.Element('OpenDRIVE')
    road = ET.SubElement(root, 'road', id='1')
    plan = ET.SubElement(road, 'planView')
    length = 0.
    for p0, p1, p2, p3 in halves:
        d0, d1 = p1 - p0, p3 - p2
        delta = p3 - p0
        coefficients = xDataGetter.bezierToPoly3([
            *delta, np.linalg.norm(d0), np.linalg.norm(d1),
            math.atan2(d0[1], d0[0]), math.atan2(d1[1], d1[0]),
        ])
        size = xDataGetter.poly3ToLength(coefficients, 1.)
        geometry = ET.SubElement(plan, 'geometry', s=str(length), x=str(p0[0]),
                                 y=str(p0[1]), hdg='0', length=str(size))
        attributes = dict(zip(('bU', 'cU', 'dU', 'bV', 'cV', 'dV'), map(str, coefficients)))
        ET.SubElement(geometry, 'paramPoly3', aU='0', aV='0', pRange='normalized', **attributes)
        length += size
    road.set('length', str(length))
    profile = ET.SubElement(road, 'elevationProfile')
    ET.SubElement(profile, 'elevation', s='0', a='0', b='0', c='0', d='0')
    section = ET.SubElement(ET.SubElement(road, 'lanes'), 'laneSection', s='0')
    lane = ET.SubElement(ET.SubElement(section, 'right'), 'lane', id='-1', type='driving')
    ET.SubElement(lane, 'width', sOffset='0', a='3.5', b='0', c='0', d='0')
    XParser.clearAll()
    XParser.addData(ET.ElementTree(root))
    XParser.roads['1'] = road
    XParser.hdgs['1'] = [math.atan2(2, 3), math.atan2(-2, 3)]
    JParser.clearAll()
    JParser.addData({'agents': []})
    JParser.addCarData({'1': {}})


class CurvePrefitTest(unittest.TestCase):
    def setUp(self):
        self.saved_x = copy.deepcopy(XParser.__dict__)
        self.saved_j = copy.deepcopy(JParser.__dict__)
        self.manual = agent_road_context.use_manual_road_context()
        agent_road_context.set_use_manual_road_context(False)
        self.environment = patch.dict(os.environ, {'AGENT_TRACE_PATH': ''})
        self.environment.start()
        self.no_plot = patch('editor.editorFit.showCurve', side_effect=AssertionError('GUI plot in fitting'))
        self.no_plot.start()
        load_split_curve()

    def tearDown(self):
        self.no_plot.stop()
        self.environment.stop()
        XParser.__dict__.clear()
        XParser.__dict__.update(self.saved_x)
        JParser.__dict__.clear()
        JParser.__dict__.update(self.saved_j)
        agent_road_context.set_use_manual_road_context(self.manual)

    def test_planning_and_explicit_handler_share_one_fit(self):
        service = CurveOptimizationService(None)
        with patch.object(handlerC.editorF, 'edit', wraps=handlerC.editorF.edit) as fitting:
            initial_points, info = service._build_curve_context('1', 0)
            self.assertEqual(info['gi'], 0)
            self.assertEqual(len(XParser.roads['1'].findall('./planView/geometry')), 1)
            self.assertGreater(abs(initial_points[3][0]), 9.9)
            # The before state retained for undo still has the original two geometries.
            self.assertEqual(len(XParser.data[0].getroot().findall('./road/planView/geometry')), 2)
            service._build_curve_context('1', 0)
            handlerC.handle(['curve', 'id=1', 'gi=0', 'h0=0', 'h1=0'])
            actual_points, _ = service._build_curve_context('1', 0)
            np.testing.assert_allclose(actual_points, initial_points, atol=1e-8)
            self.assertEqual(fitting.call_count, 1)
            self.assertFalse(fitting.call_args.kwargs['plot'])
            self.assertAlmostEqual(XParser.hdgs['1'][1], math.atan2(-2, 3), places=6)

    def test_reload_original_scene_requires_new_fit(self):
        service = CurveOptimizationService(None)
        with patch.object(handlerC.editorF, 'edit', wraps=handlerC.editorF.edit) as fitting:
            points, _ = service._build_curve_context('1', 0)
            load_split_curve()
            replay_points, _ = service._build_curve_context('1', 0)
            self.assertEqual(fitting.call_count, 2)
            np.testing.assert_allclose(replay_points, points, atol=1e-10)

    def test_all_parallel_agents_read_fitted_geometry(self):
        dispatcher = dispatcher_module.AgentDispatcher()
        commands = [
            {'type_name': kind, 'description': kind + ' Road 1', 'road_id': 'road_1'}
            for kind in ('width', 'slope', 'curve')
        ]
        seen = []

        async def prepared(index, command):
            seen.append(len(XParser.roads['1'].findall('./planView/geometry')))
            return {'index': index, 'command': command, 'agent_result': None}

        with patch.object(dispatcher_module, 'USE_MANUAL_ROAD_CONTEXT', False), \
             patch.object(dispatcher, '_prepare_agent_command_async', side_effect=prepared):
            asyncio.run(dispatcher._prepare_split_commands_async(commands))
        self.assertEqual(seen, [1, 1, 1])

    def test_manual_context_does_not_fit_real_map(self):
        service = CurveOptimizationService(None)
        context = {'control_points': [[0, 0], [3, 2], [7, 2], [10, 0]]}
        with patch.object(agent_road_context, 'use_manual_road_context', return_value=True), \
             patch.object(agent_road_context, 'get_manual_curve_road_info', return_value=context), \
             patch.object(handlerC, 'prepare_road_fit') as fitting:
            service._build_curve_context('1', 0)
        fitting.assert_not_called()

    def test_single_width_context_does_not_fit_roads(self):
        from testfucs.agent_flow_real_trace import _build_real_single_agent_context
        dispatcher = dispatcher_module.AgentDispatcher()
        with patch.object(handlerC.editorF, 'edit', wraps=handlerC.editorF.edit) as fitting:
            context = _build_real_single_agent_context(dispatcher, CurveOptimizationService(None), curve_road_ids=set())
        fitting.assert_not_called()
        self.assertEqual(len(context['roads']), 1)
        self.assertEqual(len(XParser.roads['1'].findall('./planView/geometry')), 2)

    def test_single_curve_context_reads_fitted_length(self):
        from testfucs.agent_flow_real_trace import _build_real_single_agent_context
        dispatcher = dispatcher_module.AgentDispatcher()
        with patch.object(handlerC.editorF, 'edit', wraps=handlerC.editorF.edit) as fitting:
            context = _build_real_single_agent_context(dispatcher, CurveOptimizationService(None), curve_road_ids={'1'})
        self.assertEqual(fitting.call_count, 1)
        self.assertEqual(context['roads'][0]['curve']['gi'], 0)
        self.assertEqual(len(XParser.roads['1'].findall('./planView/geometry')), 1)
        self.assertAlmostEqual(context['roads'][0]['slope']['length'],
                               float(XParser.roads['1'].get('length')), places=4)


if __name__ == '__main__':
    unittest.main()
