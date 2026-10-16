import xml.etree.ElementTree as ET
import math

from editor.editor import editor
from xodrs.xodrParser import XParser
from xodrs.xodrDataGetter import xDataGetter

from utils.lambdas import *
from utils.pltShow import showCurve


class editorCurve(editor):
    def __init__(self):
        editor.__init__(self)

    ## 拟合圆弧时, v0=v1=cos(theta/2)/(3*cos^2(theta/4))
    ## 参数含义见 readme
    def edit(self, id, x0, y0, v0, h0, x1, y1, v1, h1, gi):
        road = XParser.roads[id]
        planView = road.find("planView")
        gs = planView.findall("geometry")
        # 序号溢出
        if gi >= len(gs):
            print("ERROR, out of range!")
            return
        # 道路全局起点的坐标
        g_x0, g_y0 = getData(gs[0], "x"), getData(gs[0], "y")
        params = []

        ## 计算道路方程
        # 新的道路总长度
        lengthNew = 0
        # 应该是理解为从第几个 geomotry 开始修改
        # 那看来修改的还是就是一段 geomotry
        B = self.getGBezier(gs[gi])
        # 路段始末点的直线距离
        euc_dis = math.sqrt(B[0] ** 2 + B[1] ** 2)
        turn0 = math.atan2(math.sin(B[5] - B[4]), math.cos(B[5] - B[4]))
        turn1 = math.atan2(
            math.sin(B[5] + h1 - B[4] - h0), math.cos(B[5] + h1 - B[4] - h0)
        )
        if turn0 * turn1 < 0:
            h0, h1 = 0, 0
        edit0 = x0 != 0 or y0 != 0 or h0 != 0
        edit1 = x1 != 0 or y1 != 0 or h1 != 0

        for i in range(len(gs)):
            #  [x, y, l0, l1, h0, h1]
            bezier = self.getGBezier(gs[i])
            deltaX, deltaY = 0, 0
            edit = False

            # 起始点偏移  euc_dis提供了一个缩放比例，为相对位置调整，因此合理的输入参数范围应该是[-1, 1]
            if i == gi - 1 and edit0:
                edit = True
                bezier[0] += x0
                bezier[1] += y0
                bezier[5] += h0
            elif i == gi:
                edit = True
                deltaX, deltaY = x0, y0
                bezier[0] += x1 - x0
                bezier[1] += y1 - y0
                # 限制切线长度的修改幅度不能超出原始方向，否则会改变道路语义,bezier[2]/abs(bezier[2])/1000000为设置一个原方向的极小数
                bezier[2] = (
                    bezier[2] + v0 * euc_dis
                    if (
                        bezier[2] != 0 and (bezier[2] + v0 * euc_dis) / (bezier[2]) > 0
                    )
                    else (bezier[2]) / 10
                )
                bezier[3] = (
                    bezier[3] + v1 * euc_dis
                    if (
                        bezier[3] != 0 and (bezier[3] + v1 * euc_dis) / (bezier[3]) > 0
                    )
                    else (bezier[3]) / 10
                )
                bezier[4] += h0
                bezier[5] += h1
            elif i == gi + 1 and edit1:
                edit = True
                deltaX, deltaY = x1, y1
                bezier[0] -= x1
                bezier[1] -= y1
                bezier[4] += h1

            x = getData(gs[i], "x") + deltaX
            y = getData(gs[i], "y") + deltaY
            param = xDataGetter.bezierToPoly3(bezier)
            l = xDataGetter.poly3ToLength(param, 1)
            # 修改后的起点
            param.append(x - g_x0)
            param.append(y - g_y0)
            # print(param)
            params.append(param)

            # 修改xodr数据
            if edit:
                gs[i].clear()
                setData(gs[i], "s", lengthNew)
                setData(gs[i], "x", x)
                setData(gs[i], "y", y)
                setData(gs[i], "hdg", 0)
                setData(gs[i], "length", l)
                poly = ET.Element("paramPoly3")
                setData(poly, "aU", 0)
                setData(poly, "bU", param[0])
                setData(poly, "cU", param[1])
                setData(poly, "dU", param[2])
                setData(poly, "aV", 0)
                setData(poly, "bV", param[3])
                setData(poly, "cV", param[4])
                setData(poly, "dV", param[5])
                gs[i].append(poly)

                if i == 0:
                    XParser.hdgs[id][0] = bezier[4]
                elif i == len(gs) - 1:
                    XParser.hdgs[id][1] = bezier[5]

            elif i >= gi + 1:
                setData(gs[i], "s", lengthNew)
            lengthNew += l

        # showCurve(params)
        self.rectifyRoadData(road, lengthNew)

    def getGBezier(self, g):
        """
        输入一个geometry 返回贝塞尔曲线参数
        返回 一段geomotry的全局x，y终点坐标，起点切线长度、终点切线长度、起点倾斜角、终点倾斜角
                              [x, y,        l0,         l1,       h0,     h1]
        :param g:
        :return:
        """
        x, y, l0, l1 = 0, 0, 1, 1
        h0, h1 = getData(g, "hdg"), getData(g, "hdg")
        line = g.find("line")
        # 是直线
        if line != None:
            x = getData(g, "length") * math.cos(h0)
            y = getData(g, "length") * math.sin(h0)

        poly = g.find("paramPoly3")
        if poly != None:
            bU, cU, dU, bV, cV, dV = xDataGetter.getPoly3Params(poly)
            # 路段终点
            x_, y_ = bU + cU + dU, bV + cV + dV
            # 全局终点坐标
            x = x_ * math.cos(h0) - y_ * math.sin(h0)
            y = x_ * math.sin(h0) + y_ * math.cos(h0)
            # 起点/终点的全局弧度角
            h0 = (h0 + math.atan2(bV, bU)) % (2 * math.pi)
            #                   曲线在 t=1 时导数即 dv/dt 和 du/dt  再加上起始弧度角就是全局弧度角
            h1 = (h1 + math.atan2(bV + 2 * cV + 3 * dV, bU + 2 * cU + 3 * dU)) % (
                2 * math.pi
            )
            # 起点/终点切线长度(l0l1和l2l3)
            l0 = math.sqrt((bU / 3) ** 2 + (bV / 3) ** 2)
            l1 = math.sqrt(
                (bU / 3 + cU / 3 * 2 + dU) ** 2 + (bV / 3 + cV / 3 * 2 + dV) ** 2
            )
        return [x, y, l0, l1, h0, h1]
