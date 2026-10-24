import xml.etree.ElementTree as ET
from collections import deque
import copy
import math

from editor.editor import editor
from xodrs.xodrParser import XParser
from jsons.jsonParser import JParser
from xodrs.xodrDataGetter import xDataGetter
from jsons.jsonDataGetter import jDataGetter

from utils.definitions import *
from utils.lambdas import *


class editorWidth(editor):
    def __init__(self):
        editor.__init__(self)

    ## 修改指定道路的指定车道的宽度.
    ## 默认仅有一个LaneSection.
    ## 因车道宽度不均匀可能导致按比例改变宽度时, 可能会出现道路宽度无法对齐的情况, 因此删除了按比例拓宽选项.
    def edit(self, id, value, smooth=0, maxStep=0, sameHdg=0, laneIds=[]):
        XParser.laneEdits = copy.deepcopy(XParser.laneBackup)

        # 标记需要修改的车道
        if laneIds == []:
            for lid in XParser.laneEdits[id].keys():
                XParser.laneEdits[id][lid] = defs.BOTH_EDITED
                self.setChange(
                    id, lid, defs.TAIL, maxStep, sameHdg, XParser.hdgs[id][0]
                )
                self.setChange(
                    id, lid, defs.HEAD, maxStep, sameHdg, XParser.hdgs[id][1]
                )
            # value = value/len(XParser.laneEdits[id].values())
        else:
            for lid in laneIds:
                XParser.laneEdits[id][lid] = defs.BOTH_EDITED
                self.setChange(
                    id, lid, defs.TAIL, maxStep, sameHdg, XParser.hdgs[id][0]
                )
                self.setChange(
                    id, lid, defs.HEAD, maxStep, sameHdg, XParser.hdgs[id][1]
                )
            # value = value/len(laneIds)

        # value按车道数均分
        
        # laneEdits[roadId][laneId] = 标记状态
        for rid, rEdit in XParser.laneEdits.items():
            for lid, info in rEdit.items():
                if info == defs.TAIL_EDITED and smooth:
                    self.setLaneWidth(rid, lid, value, "addt")
                elif info == defs.HEAD_EDITED and smooth:
                    self.setLaneWidth(rid, lid, value, "addh")
                elif info == defs.BOTH_EDITED:
                    self.setLaneWidth(rid, lid, value, "add")

    def setLaneWidth(self, id, lid, value, mode):
        road = XParser.roads[id]
        length = getData(road, "length")
        # 选取所有sections (默认情况下, 道路将仅有一个laneSection).
        sections = road.find("lanes").findall("laneSection")
        # 同步数据
        XParser.maxWidths[id] += value
        for section in sections:
            section = road.find("lanes").find("laneSection")
            S = getData(section, "s")

            for lane in section.findall(".//lane"):
                if lane.get("id") != lid:
                    continue
                widths = lane.findall("width")
                widthNum = len(widths)

                for j in range(widthNum):
                    delta = [0, 0, 0, 0]
                    match mode:
                        case "add":
                            delta = self.editWidth(widths[j], value, "add")
                        case "addt":
                            delta = self.editWidth(widths[j], value, "stail", length)
                        case "addh":
                            delta = self.editWidth(widths[j], value, "shead", length)

                    # rectify the cars' position:
                    s0 = S + getData(widths[j], "sOffset")
                    s1 = (
                        S + getData(road, "length")
                        if j == widthNum - 1
                        else getData(widths[j + 1], "sOffset")
                    )
                    # [序号]=[道路id] [车道id] ={车辆序号, 这辆车的路径点序号, 路面上(弧线)距离, 车离参考线距离距离, 角度差}
                    for laneId, infos in JParser.carPosition[-1][id].items():
                        # 这是为了判断是否在同一侧？那应该可以直接用乘法吧
                        if int(laneId) < 0 < int(lid) or int(laneId) > 0 > int(lid):
                            continue
                        # 超出车道范围判断
                        if int(lid) > int(laneId) > 0 or int(lid) < int(laneId) < 0:
                            continue
                        for carInfo in infos:
                            pos = carInfo["pos"]
                            if pos >= s0 and pos < s1:
                                hdg = xDataGetter.getPosHdg(road, pos)
                                # 这里要算的应该是道路横向的缩短的分量，而原本的是道路倾斜角方向的分量，至于为什么是-，是以道路右半边缩窄看的，
                                # hdg-= math.pi/2
                                ds = pos - s0
                                dw = (
                                    delta[0]
                                    + delta[1] * ds
                                    + delta[2] * ds**2
                                    + delta[3] * ds**3
                                )
                                # 如果是修改的车道，dw要除以2，
                                dw = dw if laneId != lid else dw / 2

                                ord = jDataGetter.getOrd(carInfo)
                                # t为负
                                if int(lid) < 0:
                                    ord["position"]["x"] += dw * math.sin(hdg)
                                    ord["position"]["z"] -= dw * math.cos(hdg)
                                    carInfo["dis"] += dw

                                else:
                                    ord["position"]["x"] -= dw * math.sin(hdg)
                                    ord["position"]["z"] += dw * math.cos(hdg)
                                    carInfo["dis"] -= dw
                break

    def setChange(self, id, lid, di, maxStep, sameHdg, hdg):
        queue = deque()
        step = 0

        for info in XParser.laneConnections[id][lid][int(di)]:
            queue.append({"id": info[0], "lid": info[1], "di": info[2], "step": 0})

        while len(queue) > 0:
            item = queue.popleft()
            id = item["id"]
            lid = item["lid"]
            di = item["di"]
            step = item["step"]

            # 与修改高度中差不多的传播标记
            if sameHdg:
                angle0 = (XParser.hdgs[id][0] - hdg) % (2 * math.pi)
                angle1 = (XParser.hdgs[id][1] - hdg) % (2 * math.pi)
                m1, m2, m3, m4 = (
                    math.pi / 4,
                    math.pi / 4 * 3,
                    math.pi / 4 * 5,
                    math.pi / 4 * 7,
                )
                if angle0 > m1 and angle0 < m2 or angle0 > m3 and angle0 < m4:
                    step = maxStep
                elif angle1 > m1 and angle1 < m2 or angle1 > m3 and angle1 < m4:
                    step = maxStep

            if step < maxStep:
                match XParser.laneEdits[id][lid]:
                    case defs.NOT_EDITED | defs.TAIL_EDITED | defs.HEAD_EDITED:
                        XParser.laneEdits[id][lid] = defs.BOTH_EDITED
                        for info in XParser.laneConnections[id][lid][0]:
                            queue.append(
                                {
                                    "id": info[0],
                                    "lid": info[1],
                                    "di": info[2],
                                    "step": step + 1,
                                }
                            )
                        for info in XParser.laneConnections[id][lid][1]:
                            queue.append(
                                {
                                    "id": info[0],
                                    "lid": info[1],
                                    "di": info[2],
                                    "step": step + 1,
                                }
                            )
                    case _:
                        continue
            # di 在这里是int类型数字 0=tail, 1=head
            elif step == maxStep and not di:
                match XParser.laneEdits[id][lid]:
                    case defs.NOT_EDITED:
                        XParser.laneEdits[id][lid] = defs.TAIL_EDITED
                    case defs.HEAD_EDITED:
                        XParser.laneEdits[id][lid] = defs.BOTH_EDITED
                    case _:
                        continue
                for info in XParser.laneConnections[id][lid][int(defs.TAIL)]:
                    queue.append(
                        {"id": info[0], "lid": info[1], "di": info[2], "step": step}
                    )
            elif step == maxStep and di:
                match XParser.laneEdits[id][lid]:
                    case defs.NOT_EDITED:
                        XParser.laneEdits[id][lid] = defs.HEAD_EDITED
                    case defs.TAIL_EDITED:
                        XParser.laneEdits[id][lid] = defs.BOTH_EDITED
                    case _:
                        continue
                for info in XParser.laneConnections[id][lid][int(defs.HEAD)]:
                    queue.append(
                        {"id": info[0], "lid": info[1], "di": info[2], "step": step}
                    )

    def editWidth(self, width: ET.Element, value, mode, length=0):
        a = getData(width, "a")
        b = getData(width, "b")
        c = getData(width, "c")
        d = getData(width, "d")
        match mode:
            case "add":
                setData(width, "a", getSuitableWidth(a + value))
                # print(f"a:{a}")
                # print(f"modified:{getData(width, 'a')}")
                return [getData(width, "a") - a, 0, 0, 0]
                # return [value, 0, 0, 0]
            case "stail":
                # 当前段的长度
                current_length = length - getData(width, "sOffset")
                # 将其变化率修改成一次函数，这样就实现了递增的效果
                # 使用(value/length)*current_length 使其呈现连续变化
                setData(
                    width, "a", getSuitableWidth(a + value / length * current_length)
                )
                # 用于平滑链接
                setData(width, "b", b - value / length)
                return [getData(width, "a") - a, getData(width, "b") - b, 0, 0]
                # return [value/length*x, -value/length, 0, 0]
            case "shead":
                current_length = getData(width, "sOffset")
                setData(
                    width, "a", getSuitableWidth(a + value / length * current_length)
                )
                setData(width, "b", b + value / length)
                return [getData(width, "a") - a, getData(width, "b") - b, 0, 0]
                # return [value/length*x, value/length, 0, 0]
            case _:
                return


def getSuitableWidth(width):
    min_width = 2.7
    max_width = 4
    result = width
    if width > max_width:
        result = max_width
    elif width < min_width:
        result = min_width
    return result
