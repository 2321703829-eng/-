import math
import copy
from collections import deque
import numpy as np

# from lambdas import getData
from xodrs.xodrParser import XParser
from xodrs.xodrDataGetter import xodrDataGetter

getData = lambda a, b: float(a.get(b))


def calculateLineEndpoint(x0, y0, hdg0, length):
    """计算一段直线的终点坐标"""
    x1 = x0 + length * math.cos(hdg0)
    y1 = y0 + length * math.sin(hdg0)
    return x1, y1


def calculateParamPoly3Endpoint(x0, y0, hdg0, length, params):
    """计算一段paramPoly3的终点坐标"""
    p = length  # 在终点处，参数p等于该段的长度

    # 1. 计算局部坐标 (u, v)
    bU, cU, dU, bV, cV, dV = params
    u = bU + cU + dU
    v = bV + cV + dV
    up1 = bU + 2 * cU + 3 * dU
    vp1 = bV + 2 * cV + 3 * dV
    # 2. 旋转局部坐标(u, v)得到全局位移(dx, dy)
    cos_hdg = math.cos(hdg0)
    sin_hdg = math.sin(hdg0)

    dx = u * cos_hdg - v * sin_hdg
    dy = u * sin_hdg + v * cos_hdg

    # 3. 计算全局终点坐标
    end_x = x0 + dx
    end_y = y0 + dy
    delta_hdg = math.atan2(vp1, up1)
    end_hdg = hdg0 + delta_hdg
    return end_x, end_y, end_hdg


def calculate_line_point(x, y, hdg, s_local):
    """
    计算直线类型的采样点坐标和朝向。
    参数:
        x, y (float): 起点坐标
        hdg (float): 起点朝向（弧度）
        s_local (float): 在当前 geometry 内的 s 偏移量
    返回:
        tuple: (x, y, hdg)
    """
    x_point = x + s_local * math.cos(hdg)
    y_point = y + s_local * math.sin(hdg)
    return x_point, y_point, hdg


def calculate_param_poly3_point(x, y, hdg, length, s_local, params):
    """
    计算 paramPoly3 类型的采样点坐标和朝向。
    参数:
        x, y (float): 起点坐标
        hdg (float): 起点朝向（弧度）
        length (float): geometry 长度
        s_local (float): 在当前 geometry 内的 s 偏移量
        params (dict): 参数化三阶多项式参数
    返回:
        tuple: (x, y, hdg)
    """
    # 参数 p 归一化到 [0, 1]
    p = s_local / length if length > 0 else 0
    # 计算 u(p) 和 v(p)
    u = params["aU"] + params["bU"] * p + params["cU"] * p**2 + params["dU"] * p**3
    v = params["aV"] + params["bV"] * p + params["cV"] * p**2 + params["dV"] * p**3
    # 转换到全局坐标
    x_point = x + u * math.cos(hdg) - v * math.sin(hdg)
    y_point = y + u * math.sin(hdg) + v * math.cos(hdg)
    # 计算导数 u'(p) 和 v'(p)
    up = params["bU"] + 2 * params["cU"] * p + 3 * params["dU"] * p**2
    vp = params["bV"] + 2 * params["cV"] * p + 3 * params["dV"] * p**2
    # 计算朝向
    delta_hdg = math.atan2(vp, up)
    hdg_point = hdg + delta_hdg
    return x_point, y_point, hdg_point


def getRoadStartAndEndPoints(roadId):
    gs = XParser.roads[id].find("planView").findfirst("geometry")
    sx, sy = getData(gs, "x"), getData(gs, "y")
    start = {"x": sx, "y": sy, "z": 0}

    gs = XParser.roads[id].find("planView").findend("geometry")
    hdg = gs.get("hdg")
    x0 = getData(gs, "x")
    y0 = getData(gs, "y")
    length = getData(gs, "length")
    if gs.find("line") is not None:
        end_x, end_y = calculateLineEndpoint(x0, y0, hdg, length)
    elif gs.find("paramPoly3") is not None:
        end_x, end_y = calculateParamPoly3Endpoint(
            x0, y0, hdg, length, [XParser.getPoly3Params(gs.find("paramPoly3"))]
        )
    try:
        end = {"x": end_x, "y": end_y, "z": 0}
    except NameError:
        print("Error: not line or paramPoly3 type")
        return {}

    return {"start": start, "end": end}


def get_roads_sample_points(roads_id, sample_count_per_road):
    """
    从道路对象列表中获取每条道路的采样点，采样点均匀分布。
    参数:
        roads (list): OpenDRIVE 的 road 对象列表（由 ElementTree 解析）
        sample_count_per_road (int): 每条道路的采样点数量
    返回:
        list: 包含所有采样点的列表，每个点为字典 {'road_id': str, 'x': float, 'y': float, 'z': float, 'hdg': float}
    异常:
        ValueError: 如果道路数据缺失、几何数据缺失或类型不受支持
    """
    if sample_count_per_road < 1:
        raise ValueError("每条道路的采样点数量必须至少为 1")

    all_sample_points = []

    for road_id in roads_id:
        road = XParser.roads[road_id]
        if road_id is None:
            raise ValueError("道路缺少 ID 属性")

        # 获取 planView 和 elevationProfile
        plan_view = road.find("planView")
        elevation_profile = road.find("elevationProfile")
        if plan_view is None:
            raise ValueError(f"道路 {road_id} 未包含 planView 元素")

        # 获取所有 geometry 元素
        geometries = plan_view.findall("geometry")
        if not geometries:
            raise ValueError(f"道路 {road_id} 的 planView 中未找到 geometry 元素")

        # 获取高程信息（如果存在）
        elevations = (
            elevation_profile.findall("elevation")
            if elevation_profile is not None
            else []
        )

        # 计算道路总长度
        total_length = sum(float(g.get("length")) for g in geometries)
        if total_length <= 0:
            raise ValueError(f"道路 {road_id} 的总长度为 0 或负值")

        # 计算采样点 s 坐标（均匀分布）
        s_values = np.linspace(0, total_length, sample_count_per_road, endpoint=True)

        road_points = []
        for s in s_values:
            # 找到当前 s 所在的 geometry
            geometry, s_offset = find_geometry_at_s(geometries, s)
            s_local = s - s_offset
            x_start = float(geometry.get("x"))
            y_start = float(geometry.get("y"))
            hdg_start = float(geometry.get("hdg"))
            length = float(geometry.get("length"))

            # 计算平面坐标和朝向
            if geometry.find("line") is not None:
                x, y, hdg = calculate_line_point(x_start, y_start, hdg_start, s_local)
            elif geometry.find("paramPoly3") is not None:
                params = get_param_poly3_params(geometry.find("paramPoly3"))
                x, y, hdg = calculate_param_poly3_point(
                    x_start, y_start, hdg_start, length, s_local, params
                )
            else:
                raise ValueError(f"道路 {road_id} 在 s={s} 处有不支持的几何类型")

            # 计算 z 坐标（高程）
            z = calculate_elevation(elevations, s)

            # 添加采样点
            # road_points.append({"road_id": road_id, "x": x, "y": y, "z": z, "hdg": hdg})
            road_points.append([x, y, z, hdg])

        all_sample_points.extend(road_points)

    return all_sample_points


def find_geometry_at_s(geometries, s_target):
    """
    找到包含目标 s 坐标的 geometry 及其在道路中的 s 偏移量。
    参数:
        geometries (list): geometry 元素列表
        s_target (float): 目标 s 坐标
    返回:
        tuple: (geometry 元素, 该 geometry 的 s 起始偏移量)
    """
    s_offset = 0.0
    for geometry in geometries:
        length = float(geometry.get("length"))
        if s_offset <= s_target <= s_offset + length:
            return geometry, s_offset
        s_offset += length
    # 如果 s_target 超出范围，返回最后一个 geometry
    return geometries[-1], s_offset - float(geometries[-1].get("length"))


def get_param_poly3_params(poly3_elem):
    return {
        "aU": getData(poly3_elem, "aU"),
        "bU": getData(poly3_elem, "bU"),
        "cU": getData(poly3_elem, "cU"),
        "dU": getData(poly3_elem, "dU"),
        "aV": getData(poly3_elem, "aV"),
        "bV": getData(poly3_elem, "bV"),
        "cV": getData(poly3_elem, "cV"),
        "dV": getData(poly3_elem, "dV"),
    }


def calculate_elevation(elevations, s):
    """
    根据 s 坐标计算高程（z 坐标）。
    参数:
        elevations (list): elevation 元素列表
        s (float): 目标 s 坐标
    返回:
        float: 高程值 z
    """
    if not elevations:
        return 0.0  # 无高程数据，返回 0
    z = 0.0
    for elevation in elevations:
        s_start = float(elevation.get("s"))
        if s >= s_start:
            a = getData(elevation, "a")
            b = getData(elevation, "b")
            c = getData(elevation, "c")
            d = getData(elevation, "d")
            ds = s - s_start
            z = a + b * ds + c * ds**2 + d * ds**3
        else:
            break
    return z


def get_modified_road_id(roads):
    """
    查找输入的一个road_id列表中修改过高度的道路,一个交叉口可能会有多个道路被修改高度
    """

    ans = []
    for road_id in roads:
        road_info = XParser.roads[road_id]
        elevations = road_info.find("elevationProfile").findall("elevation")
        for elevation in elevations:
            if getData(elevation, "a") != 0 and getData(elevation, "b") != 0:
                ans.append(road_id)
                break

    return ans


def get_other_roads_id(modified_roads_id):
    list1 = get_modified_road_id(modified_roads_id)
    print(f"modified_roads_id={list1}:")
    if len(modified_roads_id) == len(list1):
        return modified_roads_id
    return diff_lists(list1, modified_roads_id)


def diff_lists(a, b):
    a_only = [x for x in a if x not in b]
    b_only = [x for x in b if x not in a]
    return a_only + b_only
