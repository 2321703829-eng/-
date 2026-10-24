import math

from utils.lambdas import *
from jsons.jsonParser import JParser
from xodrs.xodrDataGetter import xDataGetter
from jsons.jsonDataGetter import jDataGetter


class editor(object):
    def __init__(self):
        pass

    def edit(self):
        pass

    """
  输入参数：
    road: 道路id
    length_new: 新的道路长度
 
  """

    def rectifyRoadData(self, road, length_new):
        id = road.get("id")
        length = getData(road, "length")
        # 设置 新长度
        setData(road, "length", length_new)
        sections = road.find("lanes").findall("laneSection")
        elevas = road.find("elevationProfile").findall("elevation")
        widths = road.find("lanes").findall(".//width")

        k_l = length_new / length

        # 起始位置的s坐标，但好像都是0
        for section in sections:
            setData(section, "s", getData(section, "s") * k_l)

        elevaNum = len(elevas)
        for i in range(elevaNum):
            eleva = elevas[i]
            s0 = getData(eleva, "s")
            s1 = (
                getData(road, "length")
                if i == elevaNum - 1
                else getData(elevas[i + 1], "s")
            )

            # rectify the cars' position:
            # 等比例缩放高程
            setData(eleva, "s", getData(eleva, "s") * k_l)
            setData(eleva, "b", getData(eleva, "b") / k_l)
            setData(eleva, "c", getData(eleva, "c") / (k_l**2))
            setData(eleva, "d", getData(eleva, "d") / (k_l**3))

            for infos in JParser.carPosition[-1][id].values():
                # 遍历车道上的每一个车辆路径点
                for carInfo in infos:
                    pos = carInfo["pos"]
                    if pos >= s0 and pos < s1:
                        ord = jDataGetter.getOrd(carInfo)
                        # 修正角度，如果是直道，这里两个角度分量都是0
                        #
                        if ord.get("angle") != None:
                            ord["angle"]["x"] /= k_l
                            ord["angle"]["z"] /= k_l
                        else:
                            ord["rotation"]["x"] /= k_l
                            ord["rotation"]["z"] /= k_l

        # 等比例缩放宽度
        for width in widths:
            setData(width, "sOffset", getData(width, "sOffset") * k_l)
            setData(width, "b", getData(width, "b") / k_l)
            setData(width, "c", getData(width, "c") / (k_l**2))
            setData(width, "d", getData(width, "d") / (k_l**3))

        gs = road.find("planView").findall("geometry")
        id = road.get("id")
        for infos in JParser.carPosition[-1][id].values():
            for carInfo in infos:
                carInfo["pos"] *= k_l
                for i in range(len(gs)):
                    s0 = getData(gs[i], "s")
                    s1 = (
                        getData(road, "length")
                        if i == len(gs) - 1
                        else getData(gs[i + 1], "s")
                    )

                    if carInfo["pos"] >= s0 and carInfo["pos"] < s1:
                        # xodr文件中的数值
                        hdg = getData(gs[i], "hdg")
                        x, y = getData(gs[i], "x"), getData(gs[i], "y")
                        # 车辆在这个路段上的曲线距离(相对路段起点)
                        s = carInfo["pos"] - s0

                        line = gs[i].find("line")

                        # 计算车辆横纵坐标分量位置
                        if line != None:
                            # 参考点位置
                            x += s * math.cos(hdg)
                            y += s * math.sin(hdg)
                        else:
                            poly = gs[i].find("paramPoly3")
                            u, v, h = xDataGetter.getPolyPosXYH(poly, s)
                            x += u * math.cos(hdg) - v * math.sin(hdg)
                            y += u * math.sin(hdg) + v * math.cos(hdg)
                            hdg += h

                        # 车的平面位置 这之前的位置是在参考线上的位置，现在挪到正确的位置
                        # dis使用正负号区分是在道路左侧还是右侧
                        x += carInfo["dis"] * math.sin(hdg)
                        y -= carInfo["dis"] * math.cos(hdg)
                        ord = jDataGetter.getOrd(carInfo)
                        ord["position"]["x"] = x
                        ord["position"]["z"] = y
                        if ord.get("angle") != None:
                            ord["angle"]["y"] = hdg2ang(carInfo["dhdg"] + hdg)
                        else:
                            ord["rotation"]["y"] = hdg2ang(carInfo["dhdg"] + hdg)
                        break
