import math
import threading
import weakref
import xml.etree.ElementTree as ET

from editor.editorCurve import editorCurve
from editor.editorFit import editorFit
from handler.handler import handler
from jsons.jsonParser import JParser
from utils import agent_trace
from utils.random import *
from xodrs.xodrParser import XParser


class handlerCurve(handler):
    def __init__(self):
        handler.__init__(self)
        self.editorC = editorCurve()
        self.editorF = editorFit()
        self._fit_cache = weakref.WeakKeyDictionary()
        self._fit_lock = threading.RLock()

    def prepare_road_fit(self, road_id):
        """Fit before planning/execution, once per loaded road geometry version."""
        road_id = str(road_id).replace("road_", "")
        with self._fit_lock:
            session = XParser.data[0]
            road = XParser.roads[road_id]
            signature = ET.tostring(road.find("planView"))
            fitted = self._fit_cache.setdefault(session, {})
            if fitted.get(road_id) == signature:
                return len(road.findall("./planView/geometry"))
            XParser.pushNewData()
            JParser.pushNewData()
            original_count = len(road.findall("./planView/geometry"))
            _, count = self.editorF.edit(id=road_id, md=0.01, st=1.0, plot=False)
            self._remember_fitted_road(road_id)
            with agent_trace.trace_context(step_id=f"fit:{road_id}", agent="road_preparation"):
                agent_trace.record_agent_step(
                    agent="road_preparation", operation="curve_fit", status="ok",
                    road_context_json={"road_id": f"road_{road_id}", "md": 0.01, "st": 1.0,
                                       "original_geometry_count": original_count,
                                       "fitted_geometry_count": count, "selected_gi": 0},
                )
            return count

    def _remember_fitted_road(self, road_id):
        # Geometry after curve editing remains fitted; avoid refitting the next command.
        fitted = self._fit_cache.setdefault(XParser.data[0], {})
        fitted[str(road_id)] = ET.tostring(XParser.roads[str(road_id)].find("planView"))

    def handle(self, command):
        gi = "random"
        x0, y0, h0, v0 = 0, 0, 0, 0
        x1, y1, h1, v1 = 0, 0, 0, 0
        c1, c2 = 0, 0

        def degrees_to_radians(degrees):
            # 这里必须保留角度增量的正负号。
            # 新的 curve 优化链路会返回负角度微调量，如果先做 `% 360`
            # 会把 `-5` 度错误地转成 `355` 度，最终导致曲线方向被翻转。
            radians = degrees * (math.pi / 180)
            return radians

        for i in range(1, len(command)):
            param = command[i].split("=")
            match param[0]:
                case "id":
                    id = getRandomId(param[1], curve=True)
                case "x0":
                    x0 = getRandomValue(param[1])
                case "y0":
                    y0 = getRandomValue(param[1])
                case "h0":
                    h0 = getRandomValue(param[1])
                case "v0":
                    v0 = getRandomValue(param[1])
                case "x1":
                    x1 = getRandomValue(param[1])
                case "y1":
                    y1 = getRandomValue(param[1])
                case "h1":
                    h1 = getRandomValue(param[1])
                case "v1":
                    v1 = getRandomValue(param[1])
                case "gi":
                    if param[1] != "random":
                        gi = int(param[1])
                case _:
                    print("Illegal parameter!")

        if "id" not in locals():
            print("Id not given!")
            return "Id not given!"

        lenGs = self.prepare_road_fit(id)
        XParser.pushNewData()
        JParser.pushNewData()

        returnString = f"curve id={id} gi={gi} x0={x0} y0={y0} h0={h0} v0={v0} x1={x1} y1={y1} h1={h1} v1={v1} "
        h0 = degrees_to_radians(h0)
        h1 = degrees_to_radians(h1)
        if gi == "random":
            gi = getRandomInt(lenGs)
            self.editorC.edit(
                id=id, gi=gi, x0=x0, y0=y0, h0=h0, v0=v0, x1=x1, y1=y1, h1=h1, v1=v1
            )
            self._remember_fitted_road(id)
            return "fit" + returnString

        self.editorC.edit(
            id=id, gi=gi, x0=x0, y0=y0, h0=h0, v0=v0, x1=x1, y1=y1, h1=h1, v1=v1
        )
        self._remember_fitted_road(id)
        return returnString


handlerC = handlerCurve()
