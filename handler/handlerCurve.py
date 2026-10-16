import math

from editor.editorCurve import editorCurve
from editor.editorFit import editorFit
from handler.handler import handler
from jsons.jsonParser import JParser
from utils.random import *
from xodrs.xodrParser import XParser


class handlerCurve(handler):
    def __init__(self):
        handler.__init__(self)
        self.editorC = editorCurve()
        self.editorF = editorFit()

    def handle(self, command):
        XParser.pushNewData()
        JParser.pushNewData()
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

        returnString = f"curve id={id} gi={gi} x0={x0} y0={y0} h0={h0} v0={v0} x1={x1} y1={y1} h1={h1} v1={v1} "
        h0 = degrees_to_radians(h0)
        h1 = degrees_to_radians(h1)
        if gi == "random":
            id, lenGs = self.editorF.edit(id=id, md=0.01, st=1.0)
            gi = getRandomInt(lenGs)
            self.editorC.edit(
                id=id, gi=gi, x0=x0, y0=y0, h0=h0, v0=v0, x1=x1, y1=y1, h1=h1, v1=v1
            )
            return "fit" + returnString

        self.editorC.edit(
            id=id, gi=gi, x0=x0, y0=y0, h0=h0, v0=v0, x1=x1, y1=y1, h1=h1, v1=v1
        )
        return returnString


handlerC = handlerCurve()
