import copy
import math

FAKE_CONTEXT = "fake"
USE_MANUAL_ROAD_CONTEXT = False
_MANUAL_TASK_CONTEXT = {}

FAKE_WIDTH_INFO = {
    "lane_avg_width": {
        "1": 3.4,
        "2": 3.3,
        "3": 2.8,
        "-1": 3.5,
        "-2": 3.0,
        "-3": 2.7,
    },
    "lane_ids": [3, 2, 1, -1, -2, -3],
}

FAKE_CURVE_CONTROL_POINTS = [
    [70.0, 120.0],
    [210.0, 360.0],
    [490.0, 360.0],
    [630.0, 120.0],
]


def set_use_manual_road_context(enabled):
    global USE_MANUAL_ROAD_CONTEXT
    USE_MANUAL_ROAD_CONTEXT = bool(enabled)
    return USE_MANUAL_ROAD_CONTEXT


def use_manual_road_context():
    return USE_MANUAL_ROAD_CONTEXT


def set_manual_task_context(
    width_info=None,
    slope_info=None,
    curve_info=None,
    slope_info_by_road=None,
    curve_info_by_road=None,
):
    global _MANUAL_TASK_CONTEXT
    _MANUAL_TASK_CONTEXT = {
        "width_info": copy.deepcopy(width_info) if width_info is not None else None,
        "slope_info": copy.deepcopy(slope_info) if slope_info is not None else None,
        "curve_info": copy.deepcopy(curve_info) if curve_info is not None else None,
        "slope_info_by_road": copy.deepcopy(slope_info_by_road or {}),
        "curve_info_by_road": copy.deepcopy(curve_info_by_road or {}),
    }
    return copy.deepcopy(_MANUAL_TASK_CONTEXT)


def clear_manual_task_context():
    _MANUAL_TASK_CONTEXT.clear()


def get_manual_width_road_info(road_id):
    info = copy.deepcopy(_MANUAL_TASK_CONTEXT.get("width_info") or FAKE_WIDTH_INFO)
    info["road_id"] = _format_road_id(road_id)
    return info


def get_manual_width_reference_value(road_id, lane_ids=None):
    info = get_manual_width_road_info(road_id)
    target_lane_ids = lane_ids or info["lane_ids"]
    lane_avg_width = info["lane_avg_width"]
    return sum(lane_avg_width[str(lane_id)] for lane_id in target_lane_ids)


def get_manual_slope_road_info(road_id):
    road_key = _normalize_road_id(road_id)
    slope_info_by_road = _MANUAL_TASK_CONTEXT.get("slope_info_by_road") or {}
    if road_key in slope_info_by_road:
        info = copy.deepcopy(slope_info_by_road[road_key])
        info["road_id"] = _format_road_id(road_key)
        return info
    if _MANUAL_TASK_CONTEXT.get("slope_info") is not None:
        info = copy.deepcopy(_MANUAL_TASK_CONTEXT["slope_info"])
        info["road_id"] = _format_road_id(road_id)
        return info
    length = 50.0
    start_elevation = 15
    end_elevation = 0
    return _slope_info(road_id, length, start_elevation, end_elevation)


def get_manual_curve_road_info(road_id, geometry_index):
    road_key = _normalize_road_id(road_id)
    curve_info_by_road = _MANUAL_TASK_CONTEXT.get("curve_info_by_road") or {}
    if road_key in curve_info_by_road:
        info = copy.deepcopy(curve_info_by_road[road_key])
        info["road_id"] = road_key
        info["gi"] = int(geometry_index)
        return info
    if _MANUAL_TASK_CONTEXT.get("curve_info") is not None:
        info = copy.deepcopy(_MANUAL_TASK_CONTEXT["curve_info"])
        info["road_id"] = str(road_id)
        info["gi"] = int(geometry_index)
        return info
    return {
        "road_id": str(road_id),
        "gi": int(geometry_index),
        "control_points": copy.deepcopy(FAKE_CURVE_CONTROL_POINTS),
    }


def _format_road_id(road_id):
    value = str(road_id)
    return value if value.startswith("road_") else f"road_{value}"


def _normalize_road_id(road_id):
    return str(road_id).replace("road_", "")


def _slope_info(road_id, length, start_elevation, end_elevation):
    height_delta = end_elevation - start_elevation
    grade_fraction = 0.0 if length == 0 else height_delta / length
    return {
        "road_id": _format_road_id(road_id),
        "length": length,
        "start_elevation": start_elevation,
        "end_elevation": end_elevation,
        "height_delta": height_delta,
        "grade_fraction": grade_fraction,
        "grade_degree": math.degrees(math.atan(grade_fraction)),
    }
