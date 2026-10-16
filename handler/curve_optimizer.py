"""
曲线优化执行模块。

这个模块负责把“自然语言曲线修改请求”或“curve 优化 Agent 的结构化意图 JSON”，
接入现有的曲线优化流程，并最终换算回当前系统已经支持的具体命令：

`curve id=... gi=... h0=... v0=... h1=... v1=...`

整体流程如下：
1. 根据道路当前几何结构挑选最合适的 geometry 片段。
2. 把该 geometry 转成四控制点三次贝塞尔表示。
3. 构造当前曲线的上下文信息，交给 curve 优化 Agent 分析用户意图。
4. 用优化器调整中间两个控制点 P1/P2。
5. 把优化后的控制点与原控制点做差，反算成现有 curve 命令支持的参数。

这样做的好处是：
- 不需要推翻项目现有的 `handlerCurve -> editorCurve` 执行链路。
- curve 的新能力集中在“参数分析 + 控制点优化”这层，不会污染底层 editor。
"""

import json
import math
import random

# Codex 撤销按钮测试标记。
import numpy as np
from scipy.optimize import minimize

from editor.editorCurve import editorCurve
from utils import agent_road_context, agent_trace
from xodrs.xodrParser import XParser

# 调试开关
CURVE_OPTIMIZER_DEBUG = False
# 是否用弓高 / 弦长计算曲率强度修改的目标曲率；关闭时使用原有曲率/半径数值规则。
USE_SAGITTA_PERCENT_CURVATURE_TARGET = False


class CurveOptimizationService:
    """负责执行曲线语义优化，并生成现有系统可执行的 curve 命令。"""

    def __init__(self, curve_optimizer_agent):
        """保存曲线优化 Agent，并准备复用 editorCurve 的几何读取能力。"""
        self.curve_optimizer_agent = curve_optimizer_agent
        self.curve_editor = editorCurve()

    def build_curve_dsl_payload(self, user_request, road_id):
        """从自然语言请求生成 CurveAgent DSL，并补齐 road_id/gi 元数据。"""
        if self.curve_optimizer_agent is None:
            raise ValueError("Curve optimizer agent is not configured.")

        road_id = str(road_id).replace("road_", "")
        if not road_id.isdigit():
            raise ValueError("Curve optimization requires a concrete road id.")
        geometry_index = self.select_geometry_index(str(road_id), user_request)
        control_points, curve_info = self._build_curve_context(
            str(road_id), geometry_index
        )
        llm_curve_info = self._build_curve_llm_context(control_points, curve_info)
        raw_result = self.curve_optimizer_agent.get_commands(
            user_request, llm_curve_info
        )
        payload = self._normalize_curve_payload(raw_result)
        return self._ensure_curve_payload_metadata(payload, road_id, geometry_index)

    def build_curve_command_from_tool_calls(self, tool_calls, user_request=None):
        """Merge CurveAgent tool calls and run the optimizer once."""
        if not tool_calls:
            return None

        first_args = tool_calls[0].get("args", {})
        road_id = str(first_args.get("road_id", "")).replace("road_", "")
        geometry_index = first_args.get("gi")
        if not road_id or geometry_index is None:
            raise ValueError("Curve tool calls require road_id and gi.")

        _, curve_info = self._build_curve_context(road_id, int(geometry_index))
        normalized_intent = self.build_curve_intent_from_tool_calls(
            tool_calls, curve_info
        )
        normalized_intent["road_id"] = road_id
        normalized_intent["gi"] = int(geometry_index)
        return self.build_curve_command_from_intent(
            road_id,
            normalized_intent,
            user_request=user_request,
        )

    def build_curve_intent_from_tool_calls(self, tool_calls, curve_info):
        """Build one normalized optimizer intent from side-effect-free tool calls."""
        normalized = self._build_default_curve_intent(curve_info)
        target = None
        seen = set()

        for call in tool_calls:
            name = call.get("name")
            arguments = call.get("args", {})
            current_target = (
                str(arguments.get("road_id", "")).replace("road_", ""),
                arguments.get("gi"),
            )
            if target is None:
                target = current_target
            elif target != current_target:
                raise ValueError("All curve tool calls must target the same road and gi.")

            side = arguments.get("side")
            key = (name, side)
            if key in seen:
                raise ValueError(f"Duplicate curve tool call: {key}")
            seen.add(key)

            if name == "change_curve_topology":
                self._apply_topology_tool(normalized, arguments)
            elif name == "shift_curve_centroid":
                modification = self._tool_side_modification(normalized, side)
                modification["centroid_shift"] = {
                    "change": self._tool_change(arguments)
                }
            elif name == "adjust_curve_curvature":
                modification = self._tool_side_modification(normalized, side)
                modification["curvature_peak"] = {
                    "value_type": arguments["value_type"],
                    "change": self._tool_change(arguments),
                }
            else:
                raise ValueError(f"Unsupported curve tool call: {name}")

        return normalized

    def _apply_topology_tool(self, normalized, arguments):
        target_shape = arguments.get("target_shape")
        target_semantic = arguments.get("target_semantic")
        allowed = {
            "C-shape": {"left", "right"},
            "S-shape": {"left_right", "right_left"},
        }
        if target_semantic not in allowed.get(target_shape, set()):
            raise ValueError(
                f"Curve topology mismatch: {target_shape} with {target_semantic}"
            )
        normalized["target_shape"] = target_shape
        normalized["semantic_change"] = target_semantic
        normalized["target_semantic"] = target_semantic

    def _tool_side_modification(self, normalized, side):
        side_index = {"start_side": 0, "end_side": 1}
        if side not in side_index:
            raise ValueError(f"Unsupported curve side: {side}")
        return normalized["modification"][side_index[side]]

    def _tool_change(self, arguments):
        value = arguments.get("value")
        return {
            "mode": arguments["mode"],
            "action": arguments["action"],
            "magnitude": {
                "kind": arguments["magnitude_kind"],
                "value": "none" if value is None else value,
                "level": arguments["level"],
                "unit": arguments["unit"],
            },
        }

    def build_curve_command_from_intent(
        self, road_id, normalized_ai_input, user_request=None, record_metric=True
    ):
        """把归一化后的曲线优化意图转换为 handlerC 可执行的 curve 命令。"""
        if road_id is None:
            road_id = normalized_ai_input.get("road_id")
        if road_id is None:
            raise ValueError("Curve optimization requires a concrete road id.")

        geometry_index = normalized_ai_input.get("gi")
        if geometry_index is None:
            geometry_index = self.select_geometry_index(
                str(road_id),
                user_request or json.dumps(normalized_ai_input, ensure_ascii=False),
            )

        control_points, curve_info = self._build_curve_context(
            str(road_id), int(geometry_index)
        )
        optimized_points = self.process_before_optimization(
            normalized_ai_input, control_points
        )
        agent_trace.update_agent_step(
            resolved_result_json={"optimized_control_points": optimized_points}
        )
        command_params = self._optimized_points_to_command_params(
            str(road_id),
            control_points,
            optimized_points,
            int(geometry_index),
        )
        if record_metric:
            agent_trace.record_road_metric(
                operation="curve",
                road_id=str(road_id),
                internal_command=["curve"]
                + [f"{key}={value}" for key, value in command_params.items()],
                before_json={
                    "metric_mode": "current",
                    "initial_control_points": control_points,
                    "curve_info": curve_info,
                },
                after_json={
                    "metric_mode": "projected",
                    "optimized_control_points": optimized_points,
                },
                delta_json={
                    "source": "curve_optimizer",
                    "command_params": command_params,
                    "control_point_delta": optimized_points - control_points,
                },
                status="ok",
            )
        return ["curve"] + [f"{key}={value}" for key, value in command_params.items()]

    def select_geometry_index(self, road_id, user_request):
        """选择要优化的 geometry 片段；当前默认使用第一个片段。"""
        del road_id, user_request
        return 0

    def _build_curve_context(self, road_id, geometry_index):
        """构造发给 curve 优化 Agent 的上下文信息。"""
        curve_context = None
        if agent_road_context.use_manual_road_context():
            curve_context = agent_road_context.get_manual_curve_road_info(
                road_id, geometry_index
            )
        # 获取真实数据
        if curve_context is None:
            control_points = self._get_geometry_control_points(
                road_id, geometry_index
            )
        else:
            control_points = np.array(curve_context["control_points"], dtype=float)
        analysis = self.analyze_bezier_shape(control_points)
        semantic = self.get_bezier_semantics(control_points)
        current_shape = self.get_bezier_c_s_shape(control_points)

        curve_info = {
            "road_id": str(road_id),
            "gi": int(geometry_index),
            "current_shape": current_shape,
            "semantic": semantic,
            "p_start": analysis["p_start"],
            "p_end": analysis["p_end"],
            "d_start": analysis["d_start"],
            "d_end": analysis["d_end"],
            "p_s": analysis["p_start"],
            "p_e": analysis["p_end"],
            "d_s": analysis["d_start"],
            "d_e": analysis["d_end"],
            "control_points": [
                [float(point[0]), float(point[1])] for point in control_points
            ],
        }
        return control_points, curve_info

    def _build_curve_llm_context(self, control_points, curve_info):
        """构造上传给 CurveAgent 的简化上下文，只暴露形状、语义和 p_s/p_e。"""
        del control_points

        return {
            "road_id": self._format_llm_road_id(curve_info["road_id"]),
            "current_shape": curve_info["current_shape"],
            "semantic": self._semantic_to_llm_value(curve_info["semantic"]),
            "p_s": round(float(curve_info["p_s"]), 4),
            "p_e": round(float(curve_info["p_e"]), 4),
        }

    def build_curve_tool_context(self, road_id, geometry_index):
        """Return the same current-geometry context used by Curve DSL extraction."""
        control_points, curve_info = self._build_curve_context(
            str(road_id), int(geometry_index)
        )
        return self._build_curve_llm_context(control_points, curve_info)

    def _get_curvature_peak(self, control_points):
        """采样估计当前曲线峰值曲率。"""
        t_values = np.linspace(0.0, 1.0, 501)
        curvatures = np.asarray(
            self.get_curvature(t_values, *control_points), dtype=float
        )
        finite_curvatures = curvatures[np.isfinite(curvatures)]
        if finite_curvatures.size == 0:
            return None
        return float(np.max(finite_curvatures))

    def _semantic_to_llm_value(self, semantic):
        """返回供 LLM 使用的曲线语义值；当前内部语义已可直接复用。"""
        return semantic

    def _format_llm_road_id(self, road_id):
        """把内部数字 road id 格式化为 LLM 上下文使用的 road_x。"""
        value = str(road_id)
        return value if value.startswith("road_") else f"road_{value}"

    def _round_optional(self, value):
        """把可为空的曲线指标格式化为六位小数；None 原样返回。"""
        if value is None:
            return None
        return round(float(value), 6)

    def _get_geometry_control_points(self, road_id, geometry_index):
        """
        把项目里的 geometry 片段转换成三次贝塞尔四控制点。

        转换后控制点定义如下：
        - P0: 起点
        - P1: 起点切线控制点
        - P2: 终点切线控制点
        - P3: 终点
        """
        road = XParser.roads[str(road_id)]
        geometry = road.find("planView").findall("geometry")[int(geometry_index)]
        x3, y3, l0, l1, h0, h1 = self.curve_editor.getGBezier(geometry)

        p0 = np.array([0.0, 0.0], dtype=float)
        p1 = np.array([l0 * math.cos(h0), l0 * math.sin(h0)], dtype=float)
        p3 = np.array([x3, y3], dtype=float)
        p2 = np.array([x3 - l1 * math.cos(h1), y3 - l1 * math.sin(h1)], dtype=float)
        return np.array([p0, p1, p2, p3], dtype=float)

    def _normalize_curve_payload(self, payload):
        """把 CurveAgent 的返回值统一转换为字典格式的 DSL payload。"""
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError(
                f"Curve agent payload must be a dict, got: {type(payload)}"
            )
        return payload

    def _ensure_curve_payload_metadata(self, payload, road_id, geometry_index):
        """确保 Curve DSL 携带当前选中的道路编号和 geometry 索引。"""
        payload = dict(payload)
        if isinstance(payload.get("operations"), list):
            payload["operations"] = [
                self._ensure_curve_operation_metadata(
                    operation, road_id, geometry_index
                )
                for operation in payload["operations"]
            ]
            return payload

        if payload.get("operation") == "curve":
            return self._ensure_curve_operation_metadata(
                payload, road_id, geometry_index
            )
        return payload

    def _ensure_curve_operation_metadata(self, operation, road_id, geometry_index):
        """为单个 curve operation 补齐缺失的道路编号和 geometry 索引。"""
        operation = dict(operation)
        expected_road_id = str(road_id).replace("road_", "")
        for key in ("id", "road_id"):
            value = operation.get(key)
            if value is None:
                continue
            actual_road_id = str(value).replace("road_", "")
            if actual_road_id != expected_road_id:
                raise ValueError(
                    f"Curve DSL {key}={value} does not match extracted road id "
                    f"{road_id}."
                )
        if operation.get("gi") is not None and int(operation["gi"]) != int(
            geometry_index
        ):
            raise ValueError(
                f"Curve DSL gi={operation['gi']} does not match selected geometry "
                f"index {geometry_index}."
            )
        operation.setdefault("road_id", self._format_llm_road_id(road_id))
        operation.setdefault("id", str(road_id))
        operation.setdefault("gi", int(geometry_index))
        return operation

    def _build_default_curve_intent(self, curve_info):
        """构造优化器使用的默认意图结构。"""
        return {
            "target_shape": curve_info["current_shape"],
            "semantic_change": "False",
            "target_semantic": "",
            "modification": [
                self._empty_modification(),
                self._empty_modification(),
            ],
            "current_shape": curve_info["current_shape"],
            "semantic": curve_info["semantic"],
        }

    def _empty_modification(self):
        """Build the no-op modification used by normalized tool-call intents."""
        empty_change = {
            "action": "none",
            "mode": "relative",
            "magnitude": {
                "kind": "none",
                "value": "none",
                "level": "none",
                "unit": "none",
            },
        }
        return {
            "centroid_shift": {"change": dict(empty_change)},
            "curvature_peak": {
                "value_type": "curvature",
                "change": dict(empty_change),
            },
        }

    def _optimized_points_to_command_params(
        self, road_id, original_points, optimized_points, geometry_index
    ):
        """
        把优化后的 P1/P2 控制点变化，换算成现有 `curve` 命令参数。

        反算思路：
        - `v0/v1` 用起终点切线长度相对变化表示
        - `h0/h1` 用起终点切线方向的角度增量表示
        - 命令始终包含 `id` 和 `gi`
        - 因为优化器固定了 P0/P3，所以这里不输出 x0/y0/x1/y1
        - 小于阈值的 h/v 变化会被省略，避免生成无效参数
        """
        p0, p1, p2, p3 = original_points
        q0, q1, q2, q3 = optimized_points

        # 以端点连线作为统一尺度，把控制柄长度变化转成 handlerC 接收的相对 v0/v1。
        base_length = float(np.linalg.norm(p3 - p0))
        if base_length < 1e-8:
            base_length = 1.0

        # P1 控制起点切线，P2 控制终点切线；优化器只移动这两个中间控制点。
        original_l0 = float(np.linalg.norm(p1 - p0))
        optimized_l0 = float(np.linalg.norm(q1 - q0))
        original_l1 = float(np.linalg.norm(p3 - p2))
        optimized_l1 = float(np.linalg.norm(q3 - q2))

        # h0/h1 表示切线方向增量，因此这里比较优化前后的控制柄方向角。
        original_h0 = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        optimized_h0 = math.atan2(q1[1] - q0[1], q1[0] - q0[0])
        original_h1 = math.atan2(p3[1] - p2[1], p3[0] - p2[0])
        optimized_h1 = math.atan2(q3[1] - q2[1], q3[0] - q2[0])

        delta_h0 = self._normalize_degrees(math.degrees(optimized_h0 - original_h0))
        delta_h1 = self._normalize_degrees(math.degrees(optimized_h1 - original_h1))
        delta_v0 = self._clamp((optimized_l0 - original_l0) / base_length, -1.0, 1.0)
        delta_v1 = self._clamp((optimized_l1 - original_l1) / base_length, -1.0, 1.0)

        command = {
            "id": str(road_id),
            "gi": str(int(geometry_index)),
        }
        # 小于阈值的变化不输出，避免生成没有实际效果的冗余命令参数。
        if abs(delta_h0) > 1e-6:
            command["h0"] = self._format_number(delta_h0)
        if abs(delta_v0) > 1e-6:
            command["v0"] = self._format_number(delta_v0)
        if abs(delta_h1) > 1e-6:
            command["h1"] = self._format_number(delta_h1)
        if abs(delta_v1) > 1e-6:
            command["v1"] = self._format_number(delta_v1)
        return command

    def _normalize_degrees(self, value):
        """把角度差规整到 `[-180, 180]`，避免出现等价的大角度增量。"""
        return ((float(value) + 180.0) % 360.0) - 180.0

    def _clamp(self, value, lower, upper):
        """把数值限制在闭区间 `[lower, upper]` 内。"""
        return max(lower, min(upper, float(value)))

    def _format_number(self, value):
        """把浮点数格式化成更紧凑的命令参数。"""
        text = f"{float(value):.6f}".rstrip("0").rstrip(".")
        return text if text else "0"

    def get_bezier_point(self, points, t):
        """计算三次贝塞尔曲线在参数 `t` 处的坐标，支持标量或数组参数。"""
        p0, p1, p2, p3 = points
        t = np.asarray(t)
        omt = 1.0 - t
        return (
            (omt**3)[..., None] * p0
            + (3 * omt**2 * t)[..., None] * p1
            + (3 * omt * t**2)[..., None] * p2
            + (t**3)[..., None] * p3
        )

    def get_bezier_first_derivative(self, p0, p1, p2, p3, t):
        """计算三次贝塞尔曲线在参数 `t` 处的一阶导数。"""
        t = np.asarray(t)
        omt = 1.0 - t
        return (
            (3 * omt**2)[..., None] * (p1 - p0)
            + (6 * omt * t)[..., None] * (p2 - p1)
            + (3 * t**2)[..., None] * (p3 - p2)
        )

    def get_bezier_second_derivative(self, p0, p1, p2, p3, t):
        """计算三次贝塞尔曲线在参数 `t` 处的二阶导数。"""
        t = np.asarray(t)
        omt = 1.0 - t
        a_vec = p2 - 2 * p1 + p0
        b_vec = p3 - 2 * p2 + p1
        return (6 * omt)[..., None] * a_vec + (6 * t)[..., None] * b_vec

    def get_curvature(self, t, p0, p1, p2, p3):
        """计算三次贝塞尔曲线在参数 `t` 处的非负曲率；导数退化时返回大曲率兜底。"""
        t = np.asarray(t)
        first = self.get_bezier_first_derivative(p0, p1, p2, p3, t)
        second = self.get_bezier_second_derivative(p0, p1, p2, p3, t)
        numerator = np.abs(
            first[..., 0] * second[..., 1] - first[..., 1] * second[..., 0]
        )
        denominator_squared = first[..., 0] ** 2 + first[..., 1] ** 2
        epsilon = 1e-12
        result = np.empty_like(denominator_squared)
        safe_mask = denominator_squared > epsilon
        result[~safe_mask] = 1e12
        result[safe_mask] = numerator[safe_mask] / (
            denominator_squared[safe_mask] ** 1.5 + 1e-18
        )
        return result

    def get_angle_cos_sin(self, p0, p3):
        """获取基线 `P0 -> P3` 的方向余弦与正弦。"""
        direction = np.asarray(p3) - np.asarray(p0)
        length = np.linalg.norm(direction)
        if length < 1e-12:
            return 1.0, 0.0
        return direction[0] / length, direction[1] / length

    def get_inflection_points_t(self, control_points):
        """
        计算曲线拐点的参数 `t`。

        返回 `[是否存在真实拐点, t值]`。
        如果没有真实拐点，则退化为用 P1/P2 在曲线上的最近投影均值估算分界位置。
        """
        points = np.asarray(control_points, dtype=float)
        if len(points) != 4:
            return [False, 0.5]

        p0, p1, p2, p3 = points
        a_vec = p1 - p0
        b_vec = p2 - p1
        c_vec = p3 - p2

        def cross_2d(v1, v2):
            """计算二维向量叉乘标量值。"""
            return v1[0] * v2[1] - v1[1] * v2[0]

        cross_ab = cross_2d(a_vec, b_vec)
        cross_bc = cross_2d(b_vec, c_vec)
        cross_ac = cross_2d(a_vec, c_vec)

        a = cross_ab + cross_bc - cross_ac
        b = cross_ac - 2 * cross_ab
        c = cross_ab

        inflection_ts = []
        epsilon = 1e-9
        if abs(a) < epsilon:
            if abs(b) > epsilon:
                t_value = -c / b
                if 0 < t_value < 1:
                    inflection_ts.append(t_value)
        else:
            delta = np.float64(b) ** 2 - 4 * np.float64(a) * np.float64(c)
            if delta >= 0:
                sqrt_delta = np.sqrt(delta)
                t1 = (-b + sqrt_delta) / (2 * a)
                t2 = (-b - sqrt_delta) / (2 * a)
                if epsilon < t1 < 1 - epsilon:
                    inflection_ts.append(t1)
                if epsilon < t2 < 1 - epsilon:
                    inflection_ts.append(t2)

        if inflection_ts:
            return [True, round(min(inflection_ts), 5)]

        sample_t = np.linspace(0, 1, 500)
        curve_points = np.array(
            [self.get_bezier_point(points, t_value) for t_value in sample_t]
        )
        p1_index = np.argmin(np.sum((curve_points - p1) ** 2, axis=1))
        p2_index = np.argmin(np.sum((curve_points - p2) ** 2, axis=1))
        avg_t = (sample_t[p1_index] + sample_t[p2_index]) / 2
        return [False, round(float(avg_t), 5)]

    def handle_semantic_change(self, control_points, ai_input):
        """按目标语义重设 d_start/d_end 的符号，先粗调左右转拓扑。"""
        semantic_map = {
            "left": [-1, -1],
            "right": [1, 1],
            "left_right": [-1, 1],
            "right_left": [1, -1],
        }
        semantic_target = ai_input["target_semantic"]
        if semantic_target not in semantic_map:
            return control_points

        # 语义变化先改控制点在基线两侧的拓扑符号，再交给数值优化细调形状。
        analysis = self.analyze_bezier_shape(control_points)
        scale = max(abs(analysis["d_start"]), abs(analysis["d_end"]))
        analysis["d_start"] = scale * semantic_map[semantic_target][0]
        analysis["d_end"] = scale * semantic_map[semantic_target][1]
        return self.get_points_by_control_direction(control_points, analysis)

    def get_global_offset_point_from_p0(self, control_points, offset_x, offset_y):
        """把以 P0 为原点、P0-P3 为基线的归一化局部坐标换算为全局坐标。"""
        point = np.array(control_points[0], dtype=float)
        cos0, sin0 = self.get_angle_cos_sin(control_points[0], control_points[3])
        base_length = np.linalg.norm(
            np.asarray(control_points[0]) - np.asarray(control_points[3])
        )
        x_value = base_length * (offset_x * cos0 - offset_y * sin0)
        y_value = base_length * (offset_x * sin0 + offset_y * cos0)
        return point + np.array([x_value, y_value], dtype=float)

    def get_max_curvature_point_x_y_locally(self, control_points, max_curvature_point):
        """把给定曲线点投影到以 `P0 -> P3` 为基线的归一化局部坐标系中。"""
        p0 = np.asarray(control_points[0], dtype=float)
        p3 = np.asarray(control_points[3], dtype=float)
        target = np.asarray(max_curvature_point, dtype=float)
        vector = target - p0
        x_axis = p3 - p0
        length_squared = np.dot(x_axis, x_axis)
        if length_squared < 1e-18:
            return 0.0, 0.0
        y_axis = np.array([-x_axis[1], x_axis[0]], dtype=float)
        x_value = np.dot(vector, x_axis) / length_squared
        y_value = np.dot(vector, y_axis) / length_squared
        return x_value, y_value

    def get_points_by_control_direction(self, control_points, analysis):
        """根据 `d_start/p_start/d_end/p_end` 反算四个控制点的坐标。"""
        p0, _, _, p3 = [np.array(point, dtype=float) for point in control_points]
        base_length = np.linalg.norm(p0 - p3)
        cos0, sin0 = self.get_angle_cos_sin(control_points[0], control_points[3])
        d_start = analysis["d_start"]
        p_start = analysis["p_start"]
        d_end = analysis["d_end"]
        p_end = analysis["p_end"]

        p1 = np.array(
            [
                p0[0] + base_length * (p_start * cos0 - d_start * sin0),
                p0[1] + base_length * (p_start * sin0 + d_start * cos0),
            ],
            dtype=float,
        )
        p2 = np.array(
            [
                p3[0] - base_length * (p_end * cos0 + d_end * sin0),
                p3[1] + base_length * (d_end * cos0 - p_end * sin0),
            ],
            dtype=float,
        )
        return np.array([p0, p1, p2, p3], dtype=float)

    def need_division(self, ai_input, control_points=None):
        """
        判断当前曲线优化是否需要按拐点把曲线拆成前后两段。

        触发场景：
        - 目标形状是 S-shape
        - modification[0] 与 modification[1] 都有 centroid_shift，且移动方向相反
        """
        if ai_input["target_shape"] == "S-shape":
            return True
        if "_need_division" in ai_input:
            return bool(ai_input["_need_division"])
        first = self._get_modification_intent(ai_input, 1)
        second = self._get_modification_intent(ai_input, 2)
        first_direction = self._centroid_move_direction(first, control_points)
        second_direction = self._centroid_move_direction(second, control_points)
        if first_direction == 0 or second_direction == 0:
            return False
        return first_direction != second_direction

    def _centroid_move_direction(self, modification, control_points=None):
        change = self._get_change_block(modification, "centroid_shift")
        action = change.get("action", "none")
        if action == "move_to_start":
            return -1
        if action == "move_to_end":
            return 1
        if action != "set" or control_points is None:
            return 0

        _, centroid_point, _ = self.find_max_avg_curvature_segment(
            control_points, [0.0, 1.0]
        )
        current_x, _ = self.get_max_curvature_point_x_y_locally(
            control_points, centroid_point
        )
        target_x = self._resolve_centroid_x(
            control_points, current_x, modification
        )
        if target_x < current_x - 1e-8:
            return -1
        if target_x > current_x + 1e-8:
            return 1
        return 0

    def get_modify_degree(self, level):
        """把离散强度等级映射成随机但可控的实际修改幅度。"""
        # 定性幅度保留随机区间，让同一类自然语言修改每次有轻微差异。
        if level == "slight":
            return random.uniform(0.15, 0.25)/2
        if level == "significant":
            return random.uniform(0.4, 0.7)/2
        return random.uniform(0.25, 0.40)/2

    def _get_change_block(self, modification, block_name):
        """Read a normalized intent change block, falling back to a no-op."""
        block = modification.get(block_name, {})
        if not isinstance(block, dict):
            return self._empty_modification()[block_name]["change"]
        change = block.get("change", {})
        if not isinstance(change, dict):
            return self._empty_modification()[block_name]["change"]
        return change

    def _get_curvature_value_type(self, modification):
        """判断 curvature_peak DSL 当前按曲率还是半径语义解释。"""
        # value_type 是曲率/半径语义的依据；unit 只表示数值单位，半径长度统一用 m 表达。
        block = modification.get("curvature_peak", {})
        value_type = (
            str(block.get("value_type", "curvature")).lower()
            if isinstance(block, dict)
            else "curvature"
        )
        return value_type if value_type in ("curvature", "radius") else "curvature"

    def _get_magnitude_number(self, magnitude):
        """读取 numeric magnitude 的数值；非 numeric 或无效数值返回 None。"""
        # 只有 kind=numeric 的 magnitude 才能给出明确数值目标，其它情况交给定性随机幅度。
        if not isinstance(magnitude, dict) or magnitude.get("kind") != "numeric":
            return None
        value = magnitude.get("value")
        if value == "none":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _resolve_distance_degree(
        self, magnitude, control_points, use_default_when_none=False
    ):
        """把质心偏移幅度转换为局部基线坐标系中的归一化距离。"""
        kind = magnitude.get("kind", "none") if isinstance(magnitude, dict) else "none"
        # qualitative 使用当前随机幅度区间，得到局部基线坐标中的位移比例。
        if kind == "qualitative":
            level = magnitude.get("level", "default")
            return self.get_modify_degree(level if level != "none" else "default")

        value = self._get_magnitude_number(magnitude)
        if kind == "numeric" and value is not None:
            unit = magnitude.get("unit", "none")
            # percent 表示沿 P0->P3 基线长度的比例；per 仅作为旧 DSL 兼容别名。
            if unit in ("percent", "per"):
                return abs(value) / 100.0
            # m 表示真实距离，需要除以当前 geometry 的端点基线长度，转成局部归一化距离。
            if unit == "m":
                base_length = np.linalg.norm(
                    np.asarray(control_points[3]) - np.asarray(control_points[0])
                )
                return abs(value) / max(base_length, 1e-8)
            # unit=none 时直接把 value 当作局部坐标尺度使用。
            return abs(value)

        # action 已经表达了移动意图但没有给幅度时，用 default 随机幅度兜底。
        if use_default_when_none:
            return self.get_modify_degree("default")
        return 0.0

    def _resolve_centroid_x(self, control_points, current_x, modification):
        """根据 centroid_shift DSL 更新曲率加权质心的局部 x 坐标。"""
        change = self._get_change_block(modification, "centroid_shift")
        action = change.get("action", "none")
        # 不移动曲率质心时，保持当前曲率加权质心的局部 x 坐标。
        if action == "none":
            return current_x

        magnitude = change.get("magnitude", {})
        if action == "set":
            if change.get("mode") == "absolute" and magnitude.get("kind") == "numeric":
                degree = self._resolve_distance_degree(magnitude, control_points)
                return self._clamp(degree, 0.0, 1.0)
            return current_x

        # move_to_start 沿道路起点方向减小 x；move_to_end 沿道路终点方向增大 x。
        direction = -1 if action == "move_to_start" else 1
        degree = self._resolve_distance_degree(
            magnitude, control_points, use_default_when_none=True
        )

        # absolute + numeric 表示直接指定质心目标位置，而不是在当前 x 上叠加偏移。
        if change.get("mode") == "absolute" and magnitude.get("kind") == "numeric":
            target_x = degree if action == "move_to_start" else 1.0 - degree
            return self._clamp(target_x, 0.0, 1.0)

        # relative 或非 numeric absolute 都按当前质心位置做相对偏移。
        return current_x + direction * degree

    def _resolve_curvature_ratio(self, magnitude, use_default_when_none=False):
        """把曲率或半径的变化幅度转换为相对比例。"""
        kind = magnitude.get("kind", "none") if isinstance(magnitude, dict) else "none"
        # qualitative 使用随机区间作为曲率/半径的相对变化比例。
        if kind == "qualitative":
            level = magnitude.get("level", "default")
            return self.get_modify_degree(level if level != "none" else "default")

        value = self._get_magnitude_number(magnitude)
        if kind == "numeric" and value is not None:
            unit = magnitude.get("unit", "none")
            # percent 是标准相对比例；per 仅作为旧 DSL 兼容别名，其它 numeric 单位直接作为比例系数使用。
            if unit in ("percent", "per"):
                return abs(value) / 100.0
            return abs(value)

        # 已经要求 increase/decrease 但没有幅度时，用 default 随机比例兜底。
        if use_default_when_none:
            return self.get_modify_degree("default")
        return 0.0

    def _safe_radius_from_curvature(self, curvature_value):
        """把正曲率安全转换为半径，并避免除零。"""
        # 曲率过小会导致半径爆炸，这里用下限保护避免除零。
        return 1.0 / max(abs(float(curvature_value)), 1e-8)

    def _safe_curvature_from_radius(self, radius_value):
        """把正半径安全转换为曲率，并避免除零。"""
        # 半径过小会导致曲率爆炸，这里用下限保护避免除零。
        return 1.0 / max(abs(float(radius_value)), 1e-8)

    def _get_chord_length_from_search_range(self, control_points, search_range):
        """用 P0-P3 基线长度乘以搜索区间比例，估算目标片段弦长。"""
        if control_points is None or search_range is None or len(search_range) < 2:
            return None
        points = np.asarray(control_points, dtype=float)
        if points.shape != (4, 2):
            return None
        t0 = max(0.0, min(1.0, float(search_range[0])))
        t1 = max(0.0, min(1.0, float(search_range[1])))
        base_length = float(np.linalg.norm(points[3] - points[0]))
        return base_length * abs(t1 - t0)

    def _sagitta_from_curvature_and_chord(self, curvature_value, chord_length):
        """用圆弧近似根据曲率和弦长反算弓高；半弦过大时退化为半径。"""
        curvature_value = max(abs(float(curvature_value)), 1e-8)
        chord_length = abs(float(chord_length))
        if chord_length < 1e-8:
            return None
        radius = self._safe_radius_from_curvature(curvature_value)
        half_chord = chord_length * 0.5
        if half_chord >= radius:
            return radius
        sqrt_value = math.sqrt(max(radius * radius - half_chord * half_chord, 0.0))
        return (half_chord * half_chord) / max(radius + sqrt_value, 1e-8)

    def _curvature_from_chord_and_sagitta(self, chord_length, sagitta):
        """用圆弧近似根据弦长和弓高反算曲率。"""
        chord_length = abs(float(chord_length))
        sagitta = abs(float(sagitta))
        if chord_length < 1e-8 or sagitta < 1e-12:
            return None
        return max(
            (8.0 * sagitta) / (chord_length * chord_length + 4.0 * sagitta * sagitta),
            1e-8,
        )

    def _resolve_sagitta_curvature_target(
        self, current_curvature, action, mode, magnitude, control_points, search_range
    ):
        """用弓高变化计算目标曲率；适用于非半径语义的曲率强度修改。"""
        chord_length = self._get_chord_length_from_search_range(
            control_points, search_range
        )
        if chord_length is None or chord_length < 1e-8:
            return None
        current_sagitta = self._sagitta_from_curvature_and_chord(
            current_curvature, chord_length
        )
        if current_sagitta is None:
            return None

        ratio = self._resolve_curvature_ratio(magnitude, use_default_when_none=True)
        if action == "increase":
            factor = 1.0 + ratio
        elif action == "decrease":
            factor = max(1.0 - ratio, 1e-8)
        elif action == "set":
            if mode == "absolute":
                factor = max(ratio, 1e-8)
            else:
                factor = max(ratio, 1e-8)
        else:
            return None

        target_sagitta = current_sagitta * factor
        return self._curvature_from_chord_and_sagitta(chord_length, target_sagitta)

    def _resolve_curvature_target(
        self, current_curvature, modification, control_points=None, search_range=None
    ):
        """
        根据 curvature_peak DSL 计算峰值曲率片段的目标平均曲率。

        规则覆盖：
        - action=none 时保持当前平均曲率
        - set 直接设置目标曲率或目标半径
        - increase/decrease 按曲率或半径语义做相对/绝对变化
        - 开启弓高开关时，非半径语义的曲率强度修改会先修改弓高再反算曲率
        """
        # 当前曲率峰值片段平均曲率是所有 curvature_peak 修改的基准。
        current_curvature = max(abs(float(current_curvature)), 1e-8)
        change = self._get_change_block(modification, "curvature_peak")
        action = change.get("action", "none")
        # action=none 表示不修改曲率峰值目标。
        if action == "none":
            return current_curvature

        value_type = self._get_curvature_value_type(modification)
        magnitude = change.get("magnitude", {})
        mode = change.get("mode", "relative")
        value = self._get_magnitude_number(magnitude)
        unit = magnitude.get("unit", "none") if isinstance(magnitude, dict) else "none"
        metric_type = value_type
        # 半径语义由 value_type=radius 表达；unit=m 只表示长度单位，不能单独把曲率修改改判为半径修改。
        if unit == "curvature":
            metric_type = "curvature"

        explicit_absolute_curvature_value = (
            action in ("increase", "decrease", "set")
            and mode == "absolute"
            and unit == "curvature"
            and value is not None
        )
        if (
            USE_SAGITTA_PERCENT_CURVATURE_TARGET
            and metric_type == "curvature"
            and not explicit_absolute_curvature_value
            and action in ("increase", "decrease", "set")
        ):
            target_curvature = self._resolve_sagitta_curvature_target(
                current_curvature,
                action,
                mode,
                magnitude,
                control_points,
                search_range,
            )
            if target_curvature is not None:
                return max(target_curvature, 1e-8)

        # set 表示直接设置目标曲率/半径；没有 numeric value 时无法确定目标，保持原曲率。
        if action == "set":
            if value is None:
                return current_curvature
            if metric_type == "radius":
                current_radius = self._safe_radius_from_curvature(current_curvature)
                if unit == "percent":
                    # set + radius + percent：把目标半径设为当前半径的指定百分比。
                    target_radius = current_radius * max(abs(value) / 100.0, 1e-8)
                elif mode == "absolute":
                    # set + radius + absolute + m/none：用户给的是目标半径，需要转换成目标曲率。
                    target_radius = abs(value)
                else:
                    # set + radius + relative + m/none：在当前半径基础上设置一个相对半径偏移。
                    target_radius = current_radius + abs(value)
                return self._safe_curvature_from_radius(target_radius)

            if mode == "absolute":
                # absolute + curvature：用户给的是目标曲率，直接作为目标值。
                return max(abs(value), 1e-8)

            # relative + curvature：按比例设置目标曲率。
            ratio = abs(value) / 100.0 if unit == "percent" else abs(value)
            return max(current_curvature * max(ratio, 1e-8), 1e-8)

        # increase/decrease 在曲率语义下是同向；在半径语义下会通过半径反算体现反向曲率效果。
        direction = 1 if action == "increase" else -1
        if metric_type == "radius":
            current_radius = self._safe_radius_from_curvature(current_curvature)
            if value is not None and unit in ("m", "none"):
                # numeric m/none 表示在当前半径基础上加减一个具体半径量。
                target_radius = current_radius + direction * abs(value)
            else:
                # percent 或 qualitative radius：按比例放大或缩小半径，再转换成曲率。
                ratio = self._resolve_curvature_ratio(
                    magnitude, use_default_when_none=True
                )
                target_radius = current_radius * max(1.0 + direction * ratio, 1e-8)
            return self._safe_curvature_from_radius(target_radius)

        if mode == "absolute" and value is not None and unit == "curvature":
            # absolute + curvature increase/decrease：在当前曲率上加减一个绝对曲率量。
            target_curvature = current_curvature + direction * abs(value)
        else:
            # relative/qualitative curvature：按比例放大或缩小当前曲率。
            ratio = self._resolve_curvature_ratio(magnitude, use_default_when_none=True)
            target_curvature = current_curvature * (1.0 + direction * ratio)
        return max(target_curvature, 1e-8)

    def _resolve_curvature_point_degree(
        self, current_curvature, target_curvature, modification
    ):
        """把目标曲率差转换为曲率质心目标点的局部 y 方向偏移量。"""
        change = self._get_change_block(modification, "curvature_peak")
        action = change.get("action", "none")
        # 曲率不变时，目标点不做侧向 y 偏移。
        if action == "none":
            return 0.0

        magnitude = change.get("magnitude", {})
        if magnitude.get("kind") == "qualitative":
            # 定性曲率变化继续使用随机幅度，但 y 方向只取一半，避免目标点过度外扩。
            level = magnitude.get("level", "default")
            return self.get_modify_degree(level if level != "none" else "default") * 0.5

        if magnitude.get("kind") == "numeric":
            # 数值曲率变化用目标曲率相对当前曲率的变化率决定 y 偏移，上限限制为 0.5。
            ratio = abs(target_curvature - current_curvature) / max(
                abs(current_curvature), 1e-8
            )
            return min(ratio, 1.0) * 0.5

        # 已表达曲率变化但没有幅度时，用 default 随机幅度兜底。
        return self.get_modify_degree("default") * 0.5

    def get_predict_point(
        self, control_points, ai_input, search_range, control_points_num
    ):
        """预测曲率加权质心目标点：centroid_shift 改 x，curvature_peak 改 y。"""
        # 先在当前搜索区间内找到曲率加权质心和曲率峰值片段平均曲率。
        _, max_curvature_point, avg_curvature_value = (
            self.find_max_avg_curvature_segment(control_points, search_range)
        )
        modification = self._get_modification_intent(ai_input, control_points_num)

        # 把全局质心点投影到 P0->P3 基线坐标系，x 表示沿道路方向，y 表示侧向偏移。
        max_x, max_y = self.get_max_curvature_point_x_y_locally(
            control_points, max_curvature_point
        )
        # centroid_shift 只负责改局部 x，也就是曲率峰值出现得更早/更晚。
        max_x = self._resolve_centroid_x(control_points, max_x, modification)

        # curvature_peak 先被转换为目标平均曲率，再用曲率差决定局部 y 是否远离/靠近基线。
        target_curvature = self._resolve_curvature_target(
            avg_curvature_value,
            modification,
            control_points=control_points,
            search_range=search_range,
        )
        curvature_delta = target_curvature - max(abs(float(avg_curvature_value)), 1e-8)
        curvature_direction = (
            1 if curvature_delta > 1e-8 else -1 if curvature_delta < -1e-8 else 0
        )
        if curvature_direction == 0:
            return self.get_global_offset_point_from_p0(control_points, max_x, max_y)

        y_degree = self._resolve_curvature_point_degree(
            avg_curvature_value, target_curvature, modification
        )
        # y 的正负保持在原曲线所在侧；曲率增大时远离基线，曲率减小时靠近基线。
        base_flag = 1 if max_y >= 0 else -1
        max_y += base_flag * curvature_direction * y_degree

        centroid_shift_action = self._get_change_block(
            modification, "centroid_shift"
        ).get("action", "none")
        direction_map = {"move_to_start": -1, "move_to_end": 1, "none": 0, 1: 1, 2: -1}

        # 如果用户只说“更弯”而没有指定质心移动，给 P1/P2 一个辅助 x 偏移，帮助优化器形成局部峰值。
        # if curvature_direction > 0 and centroid_shift_action == "none":
        #   max_x += direction_map[control_points_num] * (y_degree * 2.0)

        # 目标点最终需要从局部坐标转回全局坐标，交给后续 loss 计算。
        return self.get_global_offset_point_from_p0(control_points, max_x, max_y)

    def get_predict_avg_curvature(
        self, control_points, ai_input, search_range, control_points_num
    ):
        """根据 curvature_peak 意图预测峰值曲率片段的目标平均曲率。"""
        # 目标平均曲率只由 curvature_peak 决定，不使用 centroid_shift。
        _, _, avg_curvature_value = self.find_max_avg_curvature_segment(
            control_points, search_range
        )
        modification = self._get_modification_intent(ai_input, control_points_num)
        return self._resolve_curvature_target(
            avg_curvature_value,
            modification,
            control_points=control_points,
            search_range=search_range,
        )

    def _get_modification_intent(self, ai_input, control_points_num):
        """根据控制点编号取出 modification[0] 或 modification[1]，缺失时返回默认 none 意图。"""
        # control_points_num=1 对应 modification[0]，control_points_num=2 对应 modification[1]。
        modifications = ai_input.get("modification", [])
        index = 0 if control_points_num == 1 else 1
        if len(modifications) <= index:
            return self._empty_modification()
        return modifications[index]

    def to_set_optimize_parameters(
        self, control_points, ai_input, target_p1, target_p2
    ):
        """组装目标、权重和原始控制点，随后启动控制点优化。"""
        # loss_params 是后续 loss/梯度函数共享的上下文，避免优化器闭包里反复传递零散参数。
        loss_params = {
            "original_points": control_points,
            "target_p1": target_p1,
            "target_p2": target_p2,
            "weights": {},
            "ai_input": ai_input,
            # "is_need_division": self.need_division(ai_input) or self.get_inflection_points_t(control_points)[0],
        }
        # 先根据数值梯度自动估权重，再启动真正的控制点优化。
        loss_params["weights"] = self.auto_balance_weights(
            control_points, ai_input, target_p1, target_p2, loss_params
        )
        return self.run_optimizer(
            control_points, self.master_loss_function, loss_params
        )

    def auto_balance_weights(
        self, control_points, ai_input, target_p1, target_p2, loss_params
    ):
        """用数值梯度估计各 loss 项权重，减少手工调参负担。"""
        del ai_input, target_p1, target_p2
        target_ratios = {"dis_point": 2.0, "dis_curvature": 2.0, "semantic": 1000.0}
        temp_params = loss_params.copy()
        temp_params["weights"] = {key: 0.0 for key in target_ratios}
        points = np.array(control_points, dtype=float)
        calculated_weights = {}

        for key in target_ratios:
            # 每次只打开一个 loss 项，估计它对 P1/P2 的平均梯度强度。
            current_weights = {name: 0.0 for name in target_ratios}
            current_weights[key] = 1.0
            temp_params["weights"] = current_weights
            gradient = self.get_gradient_numeric(
                points, self.master_loss_function, temp_params, delta=1e-4
            )
            grad_energy = np.mean(np.abs(gradient))
            if grad_energy < 1e-9:
                calculated_weights[key] = 1000.0 if key == "semantic" else 1.0
            else:
                calculated_weights[key] = target_ratios[key] / grad_energy

        # 语义约束是硬约束倾向，固定给高权重，避免普通微调造成左右转拓扑翻转。
        calculated_weights["semantic"] = 1000.0
        return calculated_weights

    def run_optimizer(self, control_points, loss_function, loss_params):
        """优先使用 SciPy L-BFGS-B 优化 P1/P2；失败时切换到 Adam 兜底。"""
        # 优化变量只包含 P1/P2 的 x/y，P0/P3 固定，保证道路片段端点不漂移。
        initial_guess = np.concatenate([control_points[1], control_points[2]])

        def objective_func(variables):
            # SciPy 只认识一维变量数组，这里把它还原成候选贝塞尔四控制点。
            current_p1 = variables[0:2]
            current_p2 = variables[2:4]
            temp_points = np.array(control_points, dtype=float)
            temp_points[1] = current_p1
            temp_points[2] = current_p2
            loss_p1 = loss_function(temp_points, loss_params, 1)
            loss_p2 = loss_function(temp_points, loss_params, 2)
            return loss_p1 + loss_p2

        optimizer_options = {"maxiter": 2000, "disp": CURVE_OPTIMIZER_DEBUG}
        callback_func = None
        if CURVE_OPTIMIZER_DEBUG:
            optimizer_options["iprint"] = 101

            def print_process(current_variables):
                current_loss = objective_func(current_variables)
                print(
                    f"current control points: {current_variables}, current loss: {current_loss}"
                )

            callback_func = print_process

        result = minimize(
            objective_func,
            initial_guess,
            method="L-BFGS-B",
            tol=1e-5,
            callback=callback_func,
            options=optimizer_options,
        )

        final_points = np.array(control_points, dtype=float)
        final_points[1] = result.x[0:2]
        final_points[2] = result.x[2:4]
        if CURVE_OPTIMIZER_DEBUG:
            print(
                f"SciPy optimization finished: Success={result.success}, Iterations={result.nit}, Final Loss={result.fun:.6e}"
            )
        if not result.success:
            if CURVE_OPTIMIZER_DEBUG:
                print("SciPy failed; using fallback Adam optimizer.")
            # L-BFGS-B 失败时使用数值梯度 Adam 兜底，牺牲速度换稳定输出。
            final_points = self.run_optimizer_baodi(
                control_points,
                loss_function,
                loss_params,
                verbose=CURVE_OPTIMIZER_DEBUG,
            )
        return final_points

    def run_optimizer_baodi(
        self,
        control_points,
        loss_function,
        loss_params,
        iterations=3000,
        learning_rate=0.05,
        beta1=0.9,
        beta2=0.999,
        eps=1e-8,
        early_stop=True,
        eval_every=10,
        patience=80,
        min_delta=1e-6,
        verbose=False,
    ):
        """在 SciPy 未收敛时使用 Adam 作为兜底优化器。"""
        points = np.array(control_points, dtype=float)
        momentum = np.zeros_like(points[1:3], dtype=float)
        velocity = np.zeros_like(points[1:3], dtype=float)
        best_points = points.copy()
        best_loss = None
        wait = 0
        delta = 1e-3

        def compute_combined_loss(current_points):
            loss_p1 = loss_function(current_points, loss_params, 1)
            loss_p2 = loss_function(current_points, loss_params, 2)
            return float(loss_p1 + loss_p2)

        if early_stop:
            try:
                best_loss = compute_combined_loss(points)
            except Exception:
                best_loss = float("inf")
            if verbose:
                print(f"Initial fallback loss={best_loss:.6e}")

        for step in range(1, iterations + 1):
            # fallback 不依赖 SciPy 内部梯度，直接用中心差分估计 P1/P2 的梯度。
            gradient = self.get_gradient_numeric(
                points, loss_function, loss_params, delta
            )
            momentum = beta1 * momentum + (1 - beta1) * gradient
            velocity = beta2 * velocity + (1 - beta2) * (gradient**2)
            momentum_hat = momentum / (1 - beta1**step)
            velocity_hat = velocity / (1 - beta2**step)
            points[1:3] -= learning_rate * momentum_hat / (np.sqrt(velocity_hat) + eps)

            if early_stop and step % eval_every == 0:
                try:
                    current_loss = compute_combined_loss(points)
                except Exception:
                    current_loss = float("inf")

                if verbose:
                    print(
                        f"fallback iter={step:5d}, loss={current_loss:.6e}, lr={learning_rate:.3e}"
                    )

                if best_loss - current_loss > min_delta:
                    best_loss = current_loss
                    best_points = points.copy()
                    wait = 0
                    if delta > 1e-3:
                        delta /= 2
                        learning_rate = 0.05
                else:
                    wait += eval_every
                    if wait >= patience:
                        if verbose:
                            print(
                                f"Fallback early-stop probe. Best loss={best_loss:.6e}"
                            )
                        delta *= 10
                        wait = 0
                        points = best_points.copy()
                        # 逐步放大差分探测范围，尝试跳出局部平台；过大则认为兜底也无法改进。
                        if delta > 101:
                            break

        return np.array(best_points if early_stop else points, dtype=float)

    def get_gradient_numeric(self, points, loss_function, loss_params, delta=1e-3):
        """用中心差分近似计算 P1/P2 的数值梯度。"""
        gradient = np.zeros_like(points[1:3], dtype=float)
        for point_index in range(2):
            for axis_index in range(2):
                # 中心差分比单边差分更稳，适合当前没有解析梯度的组合 loss。
                points_plus = np.copy(points)
                points_minus = np.copy(points)
                points_plus[point_index + 1, axis_index] += delta
                points_minus[point_index + 1, axis_index] -= delta
                loss_plus = loss_function(points_plus, loss_params, point_index + 1)
                loss_minus = loss_function(points_minus, loss_params, point_index + 1)
                gradient[point_index, axis_index] = (loss_plus - loss_minus) / (
                    2 * delta
                )
        return gradient

    def master_loss_function(self, new_control_points, loss_params, control_points_num):
        """计算单侧控制点视角下的位置、曲率和语义稳定组合损失。"""
        metrics = self.analyze_bezier_shape(new_control_points)
        if not metrics:
            return 0.0

        t_value = None
        if self.need_division(loss_params["ai_input"]):
            t_value = self.get_inflection_points_t(new_control_points)[1]

        # 分段优化时，P1 只看前半段，P2 只看后半段；否则都看整条曲线。
        search_range = [0.0, 1.0]
        if t_value is not None:
            search_range = [0.0, t_value] if control_points_num == 1 else [t_value, 1.0]

        target_key = "target_p1" if control_points_num == 1 else "target_p2"
        current_target = loss_params[target_key]
        total_loss = self.calculate_target_loss(
            new_control_points,
            current_target,
            loss_params,
            search_range,
            control_points_num,
        )

        if loss_params["ai_input"].get("semantic_change", "False") == "False":
            # 用户没有要求改变左右转语义时，加入拓扑稳定惩罚。
            total_loss += self.calculate_semantic_constraint_loss(
                new_control_points, loss_params
            )
        return total_loss

    def calculate_target_loss(
        self, control_points, targets, loss_params, search_range, control_points_num
    ):
        """计算曲率质心位置和峰值片段平均曲率的加权二次损失。"""
        weights = loss_params["weights"]
        ai_input = loss_params["ai_input"]
        modification = self._get_modification_intent(ai_input, control_points_num)
        # 如果某类意图没有被用户明确提出，就降低对应 loss 的影响，但不完全关闭。
        centroid_shift_action = self._get_change_block(
            modification, "centroid_shift"
        ).get("action", "none")
        curvature_peak_action = self._get_change_block(
            modification, "curvature_peak"
        ).get("action", "none")
        # centroid_shift.action 控制目标点 loss 权重；curvature_peak.action 控制目标曲率 loss 权重。
        direction_balance = 0.3 if centroid_shift_action == "none" else 1.0
        curvature_balance = 0.3 if curvature_peak_action == "none" else 1.0

        # 当前候选曲线的曲率重心和平均曲率，会被拉向 process_before_optimization 生成的目标。
        _, max_curvature_point, avg_k_value = self.find_max_avg_curvature_segment(
            control_points, search_range
        )
        target_avg = targets["target_avg_curvature_value"]
        target_point = targets["target_center_point"]
        point_loss = (
            np.sum((target_point - max_curvature_point) ** 2)
            * weights["dis_point"]
            * direction_balance
        )
        curvature_loss = (
            abs(avg_k_value - target_avg) ** 2
            * weights["dis_curvature"]
            * curvature_balance
        )
        return point_loss + curvature_loss

    def calculate_semantic_constraint_loss(self, points, loss_params):
        """计算语义稳定惩罚，尽量避免小改动把左右转语义彻底翻转。"""
        weights = loss_params["weights"]
        original_points = loss_params["original_points"]
        p0, p1, p2, p3 = points[0], points[1], points[2], points[3]
        op0, op1, op2, op3 = (
            original_points[0],
            original_points[1],
            original_points[2],
            original_points[3],
        )

        def cross_2d(v1, v2):
            """计算二维向量叉乘。"""
            return v1[0] * v2[1] - v1[1] * v2[0]

        def get_topology_penalty(curr_triplet, orig_triplet):
            """根据控制点相对拓扑关系判断是否出现语义翻转风险。"""
            curr_s, curr_e, curr_p = curr_triplet
            orig_s, orig_e, orig_p = orig_triplet
            # 用叉乘符号判断控制点在参考线哪一侧；符号翻转说明曲线语义可能翻转。
            ref_sign = np.sign(cross_2d(orig_e - orig_s, orig_p - orig_s))
            curr_cross = cross_2d(curr_e - curr_s, curr_p - curr_s)
            if curr_cross * ref_sign <= 0:
                return abs(curr_cross) ** 2 * 1000.0
            safe_margin = 100.0
            if abs(curr_cross) < safe_margin:
                return (safe_margin - abs(curr_cross)) ** 5
            return 0.0

        penalty = 0.0
        penalty += get_topology_penalty((p0, p2, p1), (op0, op2, op1))
        penalty += get_topology_penalty((p3, p1, p2), (op3, op1, op2))
        return weights["semantic"] * (penalty**2)

    def analyze_bezier_shape(self, control_points):
        """分析控制点在 P0-P3 基线坐标系中的横向偏移和纵向投影比例。"""
        p0, p1, p2, p3 = [np.array(point, dtype=float) for point in control_points]
        vector = p3 - p0
        length_squared = np.dot(vector, vector)
        if length_squared < 1e-12:
            return None
        d_start = np.linalg.det(np.array([vector, p1 - p0])) / length_squared
        d_end = np.linalg.det(np.array([vector, p2 - p3])) / length_squared
        p_start = np.dot(p1 - p0, vector) / length_squared
        p_end = np.dot(p3 - p2, vector) / length_squared
        return {
            "d_start": d_start,
            "d_end": d_end,
            "p_start": p_start,
            "p_end": p_end,
            "L_sq": length_squared,
        }

    def get_bezier_semantics(self, control_points):
        """根据拐点和 d_start/d_end 符号估计直线、单向转弯或 S 型转弯语义。"""
        analysis = self.analyze_bezier_shape(control_points)
        has_inflection = self.get_inflection_points_t(control_points)[0]
        d_start = analysis["d_start"] if analysis else None
        d_end = analysis["d_end"] if analysis else None

        if d_start == 0 and d_end == 0:
            return "Straight line"
        if has_inflection:
            if d_start * d_end >= 0:
                return "right_left" if d_start > d_end else "left_right"
            return "right_left" if d_start > 0 else "left_right"
        return "right" if d_start > 0 else "left"

    def get_bezier_c_s_shape(self, control_points):
        """判断当前曲线更接近 C 型还是 S 型。"""
        analysis = self.analyze_bezier_shape(control_points)
        if analysis is None:
            return "None"
        return "C-shape" if analysis["d_start"] * analysis["d_end"] >= 0 else "S-shape"

    def process_before_optimization(self, ai_input, control_points):
        """
        曲线优化主流程入口。

        处理顺序：
        1. 如需改变语义，先按目标语义粗调控制点拓扑
        2. 根据 shape 和 centroid_shift 方向判断是否按拐点分段
        3. 为 P1/P2 分别生成目标曲率和目标质心位置
        4. 把目标交给数值优化器求解
        """
        current_points = np.array(control_points, dtype=float)
        if ai_input["semantic_change"] != "False":
            current_points = self.handle_semantic_change(current_points, ai_input)

        ai_input = dict(ai_input)
        ai_input["_need_division"] = self.need_division(ai_input, current_points)
        if ai_input["_need_division"]:
            # S 型或前后意图相反时，按拐点把曲线拆成入弯段和出弯段分别设目标。
            division_t = self.get_inflection_points_t(current_points)[1]
            target_p1 = {
                "target_avg_curvature_value": self.get_predict_avg_curvature(
                    current_points, ai_input, [0.0, division_t], 1
                ),
                "target_center_point": self.get_predict_point(
                    current_points, ai_input, [0.0, division_t], 1
                ),
            }
            target_p2 = {
                "target_avg_curvature_value": self.get_predict_avg_curvature(
                    current_points, ai_input, [division_t, 1.0], 2
                ),
                "target_center_point": self.get_predict_point(
                    current_points, ai_input, [division_t, 1.0], 2
                ),
            }
        else:
            # C 型或整体调整时，P1/P2 都参考整条曲线，但各自使用对应 modification。
            target_p1 = {
                "target_avg_curvature_value": self.get_predict_avg_curvature(
                    current_points, ai_input, [0.0, 1.0], 1
                ),
                "target_center_point": self.get_predict_point(
                    current_points, ai_input, [0.0, 1.0], 1
                ),
            }
            target_p2 = {
                "target_avg_curvature_value": self.get_predict_avg_curvature(
                    current_points, ai_input, [0.0, 1.0], 2
                ),
                "target_center_point": self.get_predict_point(
                    current_points, ai_input, [0.0, 1.0], 2
                ),
            }

        if CURVE_OPTIMIZER_DEBUG:
            print("target_p1:", target_p1)
            print("target_p2:", target_p2)

        return self.to_set_optimize_parameters(
            current_points, ai_input, target_p1, target_p2
        )

    def plot_test_result(self, curve_list, title=""):
        """调试用：绘制贝塞尔曲线和控制多边形。"""
        from matplotlib import pyplot as plt

        plt.figure(figsize=(10, 7))
        names = ["Original", "Modified", "Template"]
        styles = {
            "Original": "k--o",
            "Modified": "r--^",
            "Template": "b--.",
        }

        curves = {}
        for index, points in enumerate(curve_list):
            if index < len(names):
                curves[names[index]] = points
            else:
                name = f"Line{index + 1}"
                curves[name] = points
                styles[name] = "g--+"

        for name, points in curves.items():
            t_values = np.linspace(0, 1, 100)
            points_np = np.array(points, dtype=float)
            curve_points = np.array(
                [self.get_bezier_point(points_np, t_value) for t_value in t_values]
            )
            base_color = styles[name][0]

            plt.plot(
                curve_points[:, 0],
                curve_points[:, 1],
                base_color,
                linewidth=2.0,
                alpha=0.9,
                label=f"{name} Curve",
            )
            plt.plot(
                points_np[:, 0],
                points_np[:, 1],
                styles[name],
                markersize=8,
                markerfacecolor="none",
                label=f"{name} Polygon",
            )

            for point_index, point in enumerate(points_np):
                x_value, y_value = point
                plt.text(
                    x_value,
                    y_value,
                    f"  P{point_index}",
                    color=base_color,
                    fontsize=14,
                    ha="left",
                    va="bottom",
                )

        plt.title(title, fontsize=14)
        plt.legend()
        plt.grid(True)
        plt.axis("equal")
        plt.show()

    def find_max_avg_curvature_segment(
        self, control_points, search_range, samples=501, fixed_length=0.3
    ):
        """返回峰值曲率片段区间、曲率加权质心和该片段平均曲率。"""
        points = np.asarray(control_points, dtype=float)
        if points.shape != (4, 2):
            return None, None, None

        # search_range 来自是否分段：整条 C 型曲线用 [0,1]，S 型或前后相反意图会分前后段。
        t0 = max(0.0, min(1.0, float(search_range[0])))
        t1 = max(0.0, min(1.0, float(search_range[1])))
        if t0 >= t1:
            return [t0, t1], self.get_bezier_point(points, t0), 0.0

        # 对搜索区间采样，计算每个采样点的曲率和全局坐标。
        ts = np.linspace(t0, t1, samples, dtype=float)
        curvatures = self.get_curvature(ts, *points)
        curve_points = self.get_bezier_point(points, ts)
        # 曲率平方作为权重，曲率越大的位置对“曲率重心”影响越强。
        weights = curvatures**2 + 1e-9
        total_weight = np.sum(weights)
        centroid_point = np.array(
            [
                np.sum(curve_points[:, 0] * weights) / total_weight,
                np.sum(curve_points[:, 1] * weights) / total_weight,
            ],
            dtype=float,
        )

        range_size = t1 - t0
        if range_size <= fixed_length + 1e-12:
            # 区间短于固定窗口时，直接用整个区间的积分平均曲率作为峰值片段曲率。
            avg_k = float(np.trapz(curvatures, ts) / (range_size + 1e-18))
            return [t0, t1], centroid_point, avg_k

        # 区间较长时，用滑动窗口找平均曲率最大的固定长度片段，作为曲率峰值片段。
        window_size = max(2, int(round(fixed_length * (samples - 1) / range_size)))
        window_sum = np.convolve(
            curvatures, np.ones(window_size, dtype=float), mode="valid"
        )
        window_avg = window_sum / window_size
        start_index = int(np.argmax(window_avg))
        end_index = start_index + window_size - 1
        return (
            [ts[start_index], ts[end_index]],
            centroid_point,
            float(window_avg[start_index]),
        )
