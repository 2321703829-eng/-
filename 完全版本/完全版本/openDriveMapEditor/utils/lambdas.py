# from xodrs.xodrParser import XParser
import math
import random

getData = lambda a, b: float(a.get(b))
setData = lambda a, b, c: a.set(b, str(c))
# 将弧度 h 转换为角度（度）
hdg2ang = lambda h: (90 - (h * 180 / math.pi)) % 360
# 将弧度转为角度后取负值
hdg2ang2 = lambda h: -(h * 180 / math.pi) % 360
# 把角度 a 转换为弧度  把“以y轴正方向为0度，顺时针为正”的角度，并保证在[0, 2π)区间内
# 弧度 = 角度 *（pi/180）
ang2hdg = lambda a: (math.pi / 2 - a * math.pi / 180) % (math.pi * 2)
# 直接将角度 a（度）转换为弧度， 以x轴正方向为0度，逆时针为正方向
ang2hdg2 = lambda a: (a * math.pi / 180) % (math.pi * 2)
# 获取另一个连接点
getTailOrEndPoint = lambda a: 1 if a == 0 else 0


def findMaxCubic(a, b, c, d, upperLimit):
    # 返回最大宽度
    # 限制区间在[0, upperLimit]内
    # y = a + b*x + c*x^2 + d*x^3
    candidates = [0.0, float(upperLimit)]
    if d == 0:
        if c != 0:
            # 导数 y' = b + 2*c*x = 0 的解
            x_crit = -b / (2 * c)
            if 0 <= x_crit <= upperLimit:
                candidates.append(x_crit)
    else:
        # y = a + b*x + c*x^2 + d*x^3 求极值--------y' = 3*d*x^2 + 2*c*x + b
        # Δ = 12*d*b - 4*c^2
        discriminant = 4 * c**2 - 12 * d * b
        # 当 Δ >= 0 时，有极值点
        if discriminant >= 0:
            sqrt_disc = math.sqrt(discriminant)
            x1 = (-2 * c + sqrt_disc) / (6 * d)
            x2 = (-2 * c - sqrt_disc) / (6 * d)
            for x in [x1, x2]:
                if 0 <= x <= upperLimit:
                    candidates.append(x)

    maxY = -math.inf
    for x in candidates:
        y = a + b * x + c * x**2 + d * x**3
        if y > maxY:
            maxY = y
    return maxY


def is_curve(road):
    """
      返回输入的道路是否是曲线

    :param road:
    :return:
    """
    gs = road.find("planView").findall("geometry")
    if not gs:
        return False

    def get_hdg(g, t):
        hdg = getData(g, "hdg")
        poly = g.find("paramPoly3")
        if poly is not None:
            bU, cU, dU = getData(poly, "bU"), getData(poly, "cU"), getData(poly, "dU")
            bV, cV, dV = getData(poly, "bV"), getData(poly, "cV"), getData(poly, "dV")
            hdg += math.atan2(
                bV + 2 * cV * t + 3 * dV * t**2,
                bU + 2 * cU * t + 3 * dU * t**2,
            )
        return hdg

    hdg_s = get_hdg(gs[0], 0)
    for g in gs:
        for t in [0, 0.5, 1]:
            delta = get_hdg(g, t) - hdg_s
            delta = math.atan2(math.sin(delta), math.cos(delta))
            # 方向变化超过2.5度时判断为曲线
            if abs(delta) > math.pi / 72:
                return True
    return False


def get_waypoint_location(waypoints, index):
    """
      用于判断当前路径点是处在直线还是曲线上,返回输入的roads中的其中一个值
    :param index:
    :param waypoints:
    :return:
    """
    point1 = waypoints[index]["angle"]["y"] % 360 if index >= 0 else None
    point2 = waypoints[index]["angle"]["y"] % 360
    point3 = (
        waypoints[index + 1]["angle"]["y"] % 360 if index + 1 < len(waypoints) else None
    )
    if point1 and point2 and point3:
        df1 = abs(point1 - point2)
        df2 = abs(point2 - point3)
        df3 = abs(point3 - point1)
        if df1 > 1.55 and df2 > 1.5 and df3 > 3:
            return 1
        return 0
    if point1 and point2:
        df1 = abs(point1 - point2)
        if df1 > 2.5:
            # 判断为曲线
            return 1
    if point2 and point3:
        df2 = abs(point2 - point3)
        if df2 > 2.5:
            # 判断为曲线
            return 1
    return 0


def probability_to_use(num):
    if random.uniform(0, 100) < num:
        return 1
    else:
        return 0
