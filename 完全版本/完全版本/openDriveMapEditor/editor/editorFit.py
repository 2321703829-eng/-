import xml.etree.ElementTree as ET
from editor.editor import editor
import math
import numpy

from scipy.optimize import least_squares
from xodrs.xodrParser import XParser, xodrParser
from xodrs.xodrDataGetter import xDataGetter, xodrDataGetter

from utils.lambdas import getData, setData
from utils.pltShow import showCurve


class editorFit(editor):
    def __init__(self):
        editor.__init__(self)

    # 输入参数：道路id号，最大误差比例([0,1]百分数)，采样途径点的间隔(米)
    def edit(self, id, md, st, plot=True):
        road = XParser.roads[id]
        planView = road.find("planView")
        gs = planView.findall("geometry")
        # 获取拟合出的贝塞尔曲线相关参数 列表
        # 路段终点横坐标, 路段终点纵坐标, L0L1长度, L2L3长度, 起点弧度角, 终点弧度角
        beziers = self.solveInitialCurve(gs, md, st)
        # 计算道路方程
        planView.clear()
        length = 0
        # 道路起点坐标
        x, y = getData(gs[0], "x"), getData(gs[0], "y")
        params = []
        for i in range(len(beziers)):
            param = xDataGetter.bezierToPoly3(beziers[i])
            # 道路长度
            l = xDataGetter.poly3ToLength(param, 1)

            # 以道路起点为原点的偏离量
            # 特么的又把x，y的修改写在最后面
            param.append(x - getData(gs[0], "x"))
            param.append(y - getData(gs[0], "y"))
            params.append(param)

            g = ET.Element("geometry")
            # 路段起始位置(弧长)
            setData(g, "s", length)
            # 路段起点坐标
            setData(g, "x", x)
            setData(g, "y", y)
            # 路段起点弧度角
            setData(g, "hdg", 0)
            # 路段长度
            setData(g, "length", l)
            # 路段参数
            poly = ET.Element("paramPoly3")
            setData(poly, "aU", 0)
            setData(poly, "bU", param[0])
            setData(poly, "cU", param[1])
            setData(poly, "dU", param[2])
            setData(poly, "aV", 0)
            setData(poly, "bV", param[3])
            setData(poly, "cV", param[4])
            setData(poly, "dV", param[5])
            length += l
            # 加上当前路段的横纵长度分量，并在下一个循环中用于计算下一个路段的起点位置
            x += beziers[i][0]
            y += beziers[i][1]
            g.append(poly)
            planView.append(g)

            # 设置起点/终点弧度角
            if i == 0:
                XParser.hdgs[id][0] = beziers[i][4]
            if i == len(beziers) - 1:
                XParser.hdgs[id][1] = beziers[i][5]

        if plot:
            showCurve(params)
        self.rectifyRoadData(road, length)
        # 返回: 道路id和曲线段数
        return id, len(beziers)

    # geometry对象列表，最大误差比例([0,1]百分数默认0.01)，采样途径点的间隔(米)
    def solveInitialCurve(self, gs, maxDeviation, step):
        # 道路开始/结尾
        g0, g1 = gs[0], gs[-1]
        h0, h1 = getData(g0, "hdg"), getData(g1, "hdg")
        x0, y0 = getData(g0, "x"), getData(g0, "y")
        # 以道路起点为原点，道路终点的坐标
        x_, y_ = getData(g1, "x") - x0, getData(g1, "y") - y0

        poly = g0.find("paramPoly3")
        if poly != None:
            bU, bV = getData(poly, "bU"), getData(poly, "bV")
            #             math.atan2(bV, bU)曲线起点的局部弧度角算法
            h0 = (h0 + math.atan2(bV, bU)) % (2 * math.pi)

        line = g1.find("line")
        if line != None:
            # 补全道路路段的长度=>道路总长度的分量
            x_ += getData(g1, "length") * math.cos(h1)
            y_ += getData(g1, "length") * math.sin(h1)

        poly = g1.find("paramPoly3")
        if poly != None:
            bU, cU, dU, bV, cV, dV = xDataGetter.getPoly3Params(poly)
            # 末端在局部坐标系中的位置
            u_, v_ = bU + cU + dU, bV + cV + dV
            # 末端路段的坐标长度分量
            x_ += u_ * math.cos(h1) - v_ * math.sin(h1)
            y_ += u_ * math.sin(h1) + v_ * math.cos(h1)
            h1 = (h1 + math.atan2(bV + 2 * cV + 3 * dV, bU + 2 * cU + 3 * dU)) % (
                2 * math.pi
            )

        # 起点与终点角度的余弦和正弦值
        dx0, dy0 = math.cos(h0), math.sin(h0)
        dx1, dy1 = math.cos(h1), math.sin(h1)
        x_data, y_data, h_data = xDataGetter.getMidData(gs, x0, y0, step)
        #     全段的横纵分量(终点坐标),dx0, dy0, dx1, dy1,对角线长度
        params = [x_, y_, dx0, dy0, dx1, dy1, math.sqrt(x_**2 + y_**2)]
        #                              采样点的坐标,弧度角度              误差阈值
        return self.fitConstraindCurve(x_data, y_data, h_data, params, maxDeviation)

    """
    
  """

    def fitConstraindCurve(self, x_data, y_data, h_data, params, MAX_DEVIATION):
        """
          拟合的曲线(fitConstraindCurve)参数:
          x_data: 采样点的X坐标
          y_data: 采样点的Y坐标
          h_data: 采样点的弧度角度
          params: [全段的横纵分量(终点坐标),dx0, dy0, dx1, dy1,对角线长度]
          MAX_DEVIATION: 最大误差比例
        Return:
          ans: 拟合出的贝塞尔曲线相关参数 列表
          # 路段终点横坐标, 路段终点纵坐标, L0L1长度, L2L3长度, 起点弧度角, 终点弧度角
        :param x_data:
        :param y_data:
        :param h_data:
        :param params:
        :param MAX_DEVIATION:
        :return:
        """
        x1, y1, dx0, dy0, dx1, dy1, euc_dis = params

        ## 初始化
        def compute_initial_t(x, y):
            dx = numpy.diff(x)
            dy = numpy.diff(y)
            dist = numpy.sqrt(dx**2 + dy**2)
            # 获取一个以0开始的累加列表
            cum_dist = numpy.insert(numpy.cumsum(dist), 0, 0)

            # 得到 [0, 1] 范围内的参数化值数组，表示点在弧长上的比例位置
            return (
                cum_dist / cum_dist[-1]
                if cum_dist[-1] != 0
                else numpy.linspace(0, 1, len(x))
            )

        t_initial = compute_initial_t(x_data, y_data)
        # 初始的切线长度
        k1_initial, k2_initial = 1.0, 1.0
        initial_guess = numpy.concatenate([[k1_initial, k2_initial], t_initial])

        ## 拟合
        def residuals(params):
            k1, k2, t_vals = params[0], params[1], params[2:]
            # 计算的b,c,d为三阶贝塞尔的控制点(原点作为四个控制点之一)
            # 在拟合过程中省略了部分常数系数，因此这里拟合出的 k1 为实际 k1 的 3 倍，k2 同理
            # 将贝塞尔曲线的控制点的坐标带入代换式中得出
            bX = k1 * dx0
            bY = k1 * dy0
            cX = -2 * k1 * dx0 + 3 * x1 - k2 * dx1
            cY = -2 * k1 * dy0 + 3 * y1 - k2 * dy1
            dX = k1 * dx0 - 2 * x1 + k2 * dx1
            dY = k1 * dy0 - 2 * y1 + k2 * dy1
            # 采样点在拟合出的曲线上的位置
            x_pred = bX * t_vals + cX * t_vals**2 + dX * t_vals**3
            y_pred = bY * t_vals + cY * t_vals**2 + dY * t_vals**3
            # 采样点与实际拟合出的点的差值，将x，y的差值拼接为一个列表
            return numpy.concatenate([x_data - x_pred, y_data - y_pred])

        n_points = len(x_data)
        lower = [0, 0] + [0] * n_points
        upper = [numpy.inf, numpy.inf] + [1] * n_points
        result = least_squares(
            fun=residuals, x0=initial_guess, bounds=(lower, upper), max_nfev=1e3
        )
        ## 计算结果
        ans = []
        # result.fun 包含 x 和 y 残差，长度为 2 * n_points
        size = int(len(result.fun) / 2)
        #                 默认0.01
        index, max = -1, MAX_DEVIATION
        # 寻找最大误差的位置
        for i in range(2, size - 2):
            #                                                         对角线长度
            delta = math.sqrt(result.fun[i] ** 2 + result.fun[i + size] ** 2) / euc_dis
            if max < delta:
                max = delta
                index = i
        if index < 0:
            # print("good")
            k1_opt, k2_opt = result.x[0], result.x[1]
            # bX =    k1_opt*dx0
            # bY =    k1_opt*dy0
            # cX = -2*k1_opt*dx0 + 3*x1 - k2_opt*dx1
            # cY = -2*k1_opt*dy0 + 3*y1 - k2_opt*dy1
            # dX =    k1_opt*dx0 - 2*x1 + k2_opt*dx1
            # dY =    k1_opt*dy0 - 2*y1 + k2_opt*dy1
            # L0L1和L2L3的长度(图.三阶贝塞尔曲线)
            # 这里计算长度除以3是因为在完整代换式中存在一个常数系数3(图.三阶贝塞尔曲线推导)
            # 这里的l0和l1可以直接使用 k1_opt/3 和 k2_opt/3 替换
            # l0 = math.sqrt((bX/3          )**2+(bY/3)          **2)
            # l1 = math.sqrt((bX/3+cX/3*2+dX)**2+(bY/3+cY/3*2+dY)**2)
            l0 = k1_opt / 3
            l1 = k2_opt / 3
            # 相似度验证
            # print(f"l0:{l0:<30} k1_opt:{k1_opt/3:<30} 相似度：{l0/(k1_opt/3)*100:<30}%")
            # print(f"l1:{l1:<30} k2_opt:{k2_opt/3:<30} 相似度：{l1/(k2_opt/3)*100:<30}%")

            # x1, y1,           L0L1长度,L2L3长度,起点弧度角，          终点弧度角
            ans.append([x1, y1, l0, l1, math.atan2(dy0, dx0), math.atan2(dy1, dx1)])
        else:
            # print("bad", index, max, size)
            # 如果拟合的结果不好，就从最差的位置分割，分两段进行递归拟合
            # 将分割点 index 作为后半段的坐标原点 (0, 0)，便于后续计算。后半段的坐标变成了相对于分割点的相对坐标。
            #                   x_data1中的数据改为以分割点作为原点
            x_data0, x_data1 = x_data[:index], x_data[index + 1 :] - x_data[index]
            y_data0, y_data1 = y_data[:index], y_data[index + 1 :] - y_data[index]
            # 弧度角是全局，不需要修改
            h_data0, h_data1 = h_data[:index], h_data[index + 1 :]
            # 前半段的x轴全长，后半段的x轴全长
            x10, x11 = x_data[index], x1 - x_data[index]
            # 后半段的y轴全长，前半段的y轴全长
            y10, y11 = y_data[index], y1 - y_data[index]
            dx2, dy2 = math.cos(h_data[index]), math.sin(h_data[index])
            params0 = [x10, y10, dx0, dy0, dx2, dy2, euc_dis]
            params1 = [x11, y11, dx2, dy2, dx1, dy1, euc_dis]
            ans.extend(
                self.fitConstraindCurve(
                    x_data0, y_data0, h_data0, params0, MAX_DEVIATION
                )
            )
            ans.extend(
                self.fitConstraindCurve(
                    x_data1, y_data1, h_data1, params1, MAX_DEVIATION
                )
            )

        return ans
