import json
import math
import random

from utils import agent_road_context
from xodrs.xodrParser import XParser


class DslReferenceConverter:
    """Convert legacy DSL into commands used only as a human-review reference."""

    def __init__(
        self,
        curve_optimizer,
        width_road_info_getter,
        lane_avg_width_getter,
        slope_road_info_getter,
    ):
        self.curve_optimizer = curve_optimizer
        self.width_road_info_getter = width_road_info_getter
        self.lane_avg_width_getter = lane_avg_width_getter
        self.slope_road_info_getter = slope_road_info_getter

    def convert(self, dsl, source_command=None):
        commands = []
        for operation in dsl.get("operations", []):
            commands.extend(self._operation_to_commands(operation, source_command))
        return commands

    def _operation_to_commands(self, operation, source_command=None):
        operation_type = operation.get("operation")
        if operation_type == "width":
            return self._width_to_commands(operation, source_command)
        if operation_type == "slope":
            return self._slope_to_commands(operation, source_command)
        if operation_type == "curve":
            return self._curve_to_commands(operation, source_command)
        raise ValueError(f"Unsupported DSL operation type: {operation_type}")

    def _width_to_commands(self, operation, source_command=None):
        road_ids = self._resolve_road_ids(
            operation.get("id", operation.get("road_id")), source_command
        )
        lane_ids = self._normalize_lane_ids(operation.get("lane_ids"))
        scope = self._road_limit_to_scope(operation.get("road_limit", "none"))
        commands = []
        for road_id in road_ids:
            effective_lane_ids = lane_ids
            proportional_whole_road = self._is_proportional_whole_road_width_change(
                operation
            )
            if not effective_lane_ids and proportional_whole_road:
                width_info = self.width_road_info_getter(road_id)
                effective_lane_ids = [
                    str(lane_id) for lane_id in width_info.get("lane_ids", [])
                ]
            lane_groups = (
                [[lane_id] for lane_id in effective_lane_ids]
                if effective_lane_ids
                else [[]]
            )
            for target_lane_ids in lane_groups:
                current_width = self._get_width_reference_value(
                    road_id, target_lane_ids
                )
                delta = self._convert_width_value(
                    operation,
                    current_width,
                    qualitative_as_percent=proportional_whole_road,
                )
                if delta is None:
                    continue
                if not target_lane_ids:
                    width_info = self.width_road_info_getter(road_id)
                    lane_count = len(width_info.get("lane_ids") or [])
                    if lane_count == 0:
                        raise ValueError(
                            f"Road {road_id} has no lanes for total-width conversion."
                        )
                    delta /= lane_count
                commands.append(
                    self._build_width_command(
                        road_id,
                        delta,
                        1,
                        scope["ms"],
                        scope["sh"],
                        target_lane_ids,
                    )
                )
        return commands

    def _slope_to_commands(self, operation, source_command=None):
        road_ids = self._resolve_road_ids(
            operation.get("id", operation.get("road_id")), source_command
        )
        scope = self._road_limit_to_scope(operation.get("road_limit", "none"))
        position = str(operation.get("position", "end")).lower()
        if position not in ("start", "end", "both"):
            return []
        mv = {"start": 0, "end": 1, "both": 2}[position]
        commands = []
        for road_id in road_ids:
            converted = self._convert_slope_value(operation, road_id, mv)
            if converted is None:
                continue
            if isinstance(converted, tuple):
                converted = [converted]
            for command_mode, command_value, command_mv in converted:
                commands.append(
                    self._build_slope_command(
                        road_id,
                        command_value,
                        command_mode,
                        command_mv,
                        scope["ms"],
                        scope["sh"],
                    )
                )
        return commands

    def _curve_to_commands(self, operation, source_command=None):
        if self.curve_optimizer is None:
            raise ValueError("Curve optimizer service is not configured.")
        user_request = ""
        if isinstance(source_command, dict):
            user_request = (
                source_command.get("description")
                or source_command.get("input")
                or source_command.get("command")
                or ""
            )
        road_ids = self._resolve_road_ids(
            operation.get("id", operation.get("road_id")), source_command
        )
        commands = []
        for road_id in road_ids:
            geometry_index = operation.get("gi")
            if geometry_index is None:
                geometry_index = self.curve_optimizer.select_geometry_index(
                    str(road_id), user_request or json.dumps(operation, ensure_ascii=False)
                )
            curve_info = self.curve_optimizer.build_curve_tool_context(
                str(road_id), int(geometry_index)
            )
            normalized = self._normalize_curve_dsl(operation, curve_info)
            normalized["road_id"] = str(road_id)
            normalized["gi"] = int(geometry_index)
            commands.append(
                self.curve_optimizer.build_curve_command_from_intent(
                    str(road_id),
                    normalized,
                    user_request=user_request,
                    record_metric=False,
                )
            )
        return commands

    def _normalize_curve_dsl(self, payload, curve_info):
        """Normalize the legacy Curve DSL for reference conversion only."""
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError(
                f"Curve reference payload must be a dict, got: {type(payload)}"
            )

        operation = self._extract_curve_operation(payload)
        normalized = self._build_default_curve_intent(curve_info)

        target_shape = operation.get("shape", {}).get("target", "keep")
        if target_shape in ("C-shape", "S-shape"):
            normalized["target_shape"] = target_shape

        semantic = operation.get("semantic", {})
        target_semantic = self._normalize_semantic_value(semantic.get("target"))
        semantic_change = semantic.get("semantic_change", False)
        if semantic_change is True and target_semantic:
            normalized["semantic_change"] = target_semantic
            normalized["target_semantic"] = target_semantic
        elif target_semantic and target_semantic != "keep":
            normalized["semantic_change"] = target_semantic
            normalized["target_semantic"] = target_semantic

        modifications = operation.get("modification", [])
        normalized["modification"] = [
            self._normalize_curve_modification(
                modifications[0] if len(modifications) > 0 else None
            ),
            self._normalize_curve_modification(
                modifications[1] if len(modifications) > 1 else None
            ),
        ]
        return normalized

    def _extract_curve_operation(self, payload):
        if payload.get("operation") == "curve":
            return payload
        for operation in payload.get("operations", []):
            if isinstance(operation, dict) and operation.get("operation") == "curve":
                return operation
        raise ValueError(f"Curve DSL does not contain a curve operation: {payload}")

    def _build_default_curve_intent(self, curve_info):
        return {
            "target_shape": curve_info["current_shape"],
            "semantic_change": "False",
            "target_semantic": "",
            "modification": [
                self._normalize_curve_modification(None),
                self._normalize_curve_modification(None),
            ],
            "current_shape": curve_info["current_shape"],
            "semantic": curve_info["semantic"],
        }

    def _normalize_semantic_value(self, semantic, false_value=""):
        if semantic in (None, ""):
            return false_value
        value = str(semantic)
        if value.lower() in ("false", "none"):
            return false_value
        return {
            "left": "left",
            "right": "right",
            "left_right": "left_right",
            "right_left": "right_left",
        }.get(value, value)

    def _normalize_curve_modification(self, modification):
        if not isinstance(modification, dict):
            modification = {}
        centroid_change = self._normalize_curve_change(
            modification.get("centroid_shift", {}).get("change", {}),
            ("none", "move_to_start", "move_to_end", "set"),
        )
        curvature_block = modification.get("curvature_peak", {})
        curvature_value_type = str(
            curvature_block.get("value_type", "curvature")
        ).lower()
        if curvature_value_type not in ("curvature", "radius"):
            curvature_value_type = "curvature"
        curvature_change = self._normalize_curve_change(
            curvature_block.get("change", {}),
            ("none", "increase", "decrease", "set"),
        )
        return {
            "centroid_shift": {"change": centroid_change},
            "curvature_peak": {
                "value_type": curvature_value_type,
                "change": curvature_change,
            },
        }

    def _normalize_curve_change(self, change, allowed_actions):
        if not isinstance(change, dict):
            change = {}
        action = str(change.get("action", "none")).lower()
        if action not in allowed_actions:
            action = "none"
        mode = str(change.get("mode", "relative")).lower()
        if mode not in ("absolute", "relative"):
            mode = "relative"
        return {
            "action": action,
            "mode": mode,
            "magnitude": self._normalize_curve_magnitude(change.get("magnitude", {})),
        }

    def _normalize_curve_magnitude(self, magnitude):
        if not isinstance(magnitude, dict):
            magnitude = {}
        kind = str(magnitude.get("kind", "none")).lower()
        if kind not in ("numeric", "qualitative", "none"):
            kind = "none"
        value = magnitude.get("value", "none")
        if kind == "numeric":
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = "none"
                kind = "none"
        else:
            value = "none"
        level = str(magnitude.get("level", "none")).lower()
        if level not in ("slight", "default", "significant", "none"):
            level = "none"
        unit = str(magnitude.get("unit", "none")).lower()
        if unit == "radius":
            unit = "m"
        elif unit == "per":
            unit = "percent"
        if unit not in ("m", "curvature", "percent", "none"):
            unit = "none"
        return {
            "kind": kind,
            "value": value,
            "level": level,
            "unit": unit,
        }

    def _convert_width_value(
        self, operation, current_total, qualitative_as_percent=False
    ):
        change = operation.get("change", {})
        magnitude = change.get("magnitude", {})
        unit = str(magnitude.get("unit", "none")).lower()
        action = str(change.get("action", "none")).lower()
        if qualitative_as_percent and magnitude.get("kind") == "qualitative":
            level = str(magnitude.get("level", "default")).lower()
            percent = {
                "slight": 15.0,
                "default": 32.5,
                "significant": 55.0,
            }.get(level, 32.5)
            value = current_total * percent / 100.0
        else:
            value = self._resolve_width_magnitude(magnitude)
        if action == "none":
            return None
        if action == "decrease":
            value = -abs(value)
        elif action == "increase":
            value = abs(value)
        if action == "set":
            if unit in ("percent", "per"):
                return current_total * value / 100.0 - current_total
            return value - current_total
        if unit in ("percent", "per"):
            return current_total * value / 100.0
        return value

    @staticmethod
    def _is_proportional_whole_road_width_change(operation):
        if operation.get("lane_ids") not in (None, "none", "None", []):
            return False
        change = operation.get("change", {})
        magnitude = change.get("magnitude", {})
        action = str(change.get("action", "none")).lower()
        kind = str(magnitude.get("kind", "none")).lower()
        unit = str(magnitude.get("unit", "none")).lower()
        return kind == "qualitative" or (
            kind == "numeric"
            and unit in ("percent", "per")
            and action in ("increase", "decrease")
        )

    def _convert_slope_value(self, operation, road_id, mv):
        change = operation.get("change", {})
        magnitude = change.get("magnitude", {})
        attribute = str(change.get("attribute", "grade")).lower()
        mode = str(change.get("mode", "relative")).lower()
        action = str(change.get("action", "none")).lower()
        kind = str(magnitude.get("kind", "none")).lower()
        unit = str(magnitude.get("unit", "none")).lower()
        if self._is_empty_slope_change(attribute, mode, action, kind, unit):
            return None
        if mode == "absolute" and action != "set":
            return None
        if mode not in ("absolute", "relative") or action not in (
            "increase",
            "decrease",
            "set",
        ):
            return None
        if kind not in ("numeric", "qualitative"):
            return None
        if attribute == "elevation_delta":
            return self._convert_elevation_delta(
                road_id, mv, mode, action, kind, unit, magnitude
            )
        if attribute == "grade":
            return self._convert_grade(
                road_id, mv, mode, action, kind, unit, magnitude
            )
        return None

    def _is_empty_slope_change(self, attribute, mode, action, kind, unit):
        return (
            attribute == "none"
            and mode == "none"
            and action == "none"
            and kind == "none"
            and unit == "none"
        )

    def _convert_grade(self, road_id, mv, mode, action, kind, unit, magnitude):
        metrics = self._get_slope_metrics(road_id)
        length = metrics["length"]
        if mv == 2 or length == 0:
            return None
        if kind == "numeric" and unit not in ("percent", "degree"):
            return None
        if kind == "qualitative" and (unit != "none" or action == "set"):
            return None
        value = self._resolve_slope_magnitude(magnitude)
        current_delta = metrics["current_height_delta"]
        if mode == "absolute":
            if kind != "numeric":
                return None
            target_delta = self._target_delta_from_position_grade(
                length, mv, unit, value
            )
        elif action == "set":
            if kind != "numeric" or unit != "percent":
                return None
            target_delta = current_delta * abs(value) / 100.0
        elif kind == "qualitative":
            target_delta = self._relative_grade_percent_target(
                metrics, mv, action, value
            )
        elif unit == "degree":
            target_delta = self._relative_grade_degree_target(
                metrics, mv, action, value
            )
        else:
            target_delta = self._relative_grade_percent_target(
                metrics, mv, action, abs(value) / 100.0
            )
        height_delta_change = target_delta - current_delta
        command_value = height_delta_change if mv == 1 else -height_delta_change
        return self._slope_conversion(command_value, mv)

    def _convert_elevation_delta(
        self, road_id, mv, mode, action, kind, unit, magnitude
    ):
        metrics = self._get_slope_metrics(road_id)
        value = self._resolve_slope_magnitude(magnitude)
        if kind == "numeric" and unit not in ("m", "percent"):
            return None
        if kind == "qualitative" and unit != "none":
            return None
        if mode == "absolute" and (kind != "numeric" or unit != "m"):
            return None
        if mode == "relative" and action == "set" and (
            kind != "numeric" or unit != "percent"
        ):
            return None
        if mode == "absolute":
            if mv == 2:
                return self._filter_slope_conversions(
                    [
                        ("add", value - metrics["start_elevation"], 0),
                        ("add", value - metrics["end_elevation"], 1),
                    ]
                )
            current_elevation = self._endpoint_elevation(metrics, mv)
            return self._slope_conversion(value - current_elevation, mv)
        if action == "set":
            if mv == 2:
                return self._filter_slope_conversions(
                    [
                        (
                            "add",
                            metrics["start_elevation"] * value / 100.0
                            - metrics["start_elevation"],
                            0,
                        ),
                        (
                            "add",
                            metrics["end_elevation"] * value / 100.0
                            - metrics["end_elevation"],
                            1,
                        ),
                    ]
                )
            current_elevation = self._endpoint_elevation(metrics, mv)
            return self._slope_conversion(
                current_elevation * value / 100.0 - current_elevation, mv
            )
        if kind == "qualitative":
            amount = metrics["height_delta_for_amount"] * value
        elif unit == "percent":
            amount = self._elevation_percent_base(metrics, mv) * abs(value) / 100.0
        else:
            amount = abs(value)
        command_value = amount if action == "increase" else -amount
        if mv == 2 and unit == "percent":
            return self._filter_slope_conversions(
                [("add", command_value, 0), ("add", command_value, 1)]
            )
        return self._slope_conversion(command_value, mv)

    def _get_width_reference_value(self, road_id, lane_ids):
        if agent_road_context.use_manual_road_context():
            return agent_road_context.get_manual_width_reference_value(
                road_id, lane_ids
            )
        road = XParser.roads[str(road_id)]
        width_info = self.width_road_info_getter(road_id)
        target_lane_ids = lane_ids if lane_ids else width_info["lane_ids"]
        return sum(
            self.lane_avg_width_getter(road, str(lane_id))
            for lane_id in target_lane_ids
        )

    def _get_slope_metrics(self, road_id):
        slope_info = self.slope_road_info_getter(road_id)
        length = slope_info["length"]
        start_elevation = slope_info["start_elevation"]
        end_elevation = slope_info["end_elevation"]
        current_height_delta = end_elevation - start_elevation
        return {
            "length": length,
            "start_elevation": start_elevation,
            "end_elevation": end_elevation,
            "current_height_delta": current_height_delta,
            "height_delta_for_amount": self._height_delta_for_slope_amount(
                length, current_height_delta
            ),
        }

    def _target_delta_from_position_grade(self, length, mv, unit, value):
        if unit == "percent":
            amount = length * abs(value) / 100.0
        else:
            amount = length * abs(math.tan(math.radians(value)))
        position_sign = 1.0 if mv == 0 else -1.0
        value_sign = 1.0 if value >= 0 else -1.0
        return amount * position_sign * value_sign

    def _relative_grade_percent_target(self, metrics, mv, action, ratio):
        current_delta = metrics["current_height_delta"]
        if abs(current_delta) < 1e-12:
            if action == "decrease":
                return 0.0
            direction = 1.0 if mv == 0 else -1.0
            return direction * metrics["height_delta_for_amount"] * ratio
        factor = 1.0 + ratio if action == "increase" else max(0.0, 1.0 - ratio)
        return current_delta * factor

    def _relative_grade_degree_target(self, metrics, mv, action, value):
        length = metrics["length"]
        current_delta = metrics["current_height_delta"]
        angle_change = math.radians(abs(value))
        if abs(current_delta) < 1e-12:
            direction = 1.0 if action == "increase" else -1.0
            return length * math.tan(direction * angle_change)
        current_angle = math.atan(current_delta / length)
        current_abs_angle = abs(current_angle)
        target_abs_angle = (
            current_abs_angle + angle_change
            if action == "increase"
            else max(0.0, current_abs_angle - angle_change)
        )
        direction = 1.0 if current_angle >= 0 else -1.0
        return length * math.tan(direction * target_abs_angle)

    def _endpoint_elevation(self, metrics, mv):
        if mv == 0:
            return metrics["start_elevation"]
        if mv == 1:
            return metrics["end_elevation"]
        return min(metrics["start_elevation"], metrics["end_elevation"])

    def _elevation_percent_base(self, metrics, mv):
        height_delta = abs(metrics["current_height_delta"])
        if height_delta >= 1e-12:
            return height_delta
        if mv == 0:
            return abs(metrics["start_elevation"])
        if mv == 1:
            return abs(metrics["end_elevation"])
        return abs(metrics["start_elevation"])

    def _slope_conversion(self, command_value, mv):
        if abs(command_value) < 1e-12:
            return None
        return ("add", command_value, mv)

    def _filter_slope_conversions(self, conversions):
        filtered = [item for item in conversions if abs(item[1]) >= 1e-12]
        return filtered or None

    def _resolve_width_magnitude(self, magnitude):
        kind = str(magnitude.get("kind", "none")).lower()
        if kind == "numeric":
            return self._to_float(magnitude.get("value", 0))
        if kind == "qualitative":
            unit = str(magnitude.get("unit", "none")).lower()
            level = str(magnitude.get("level", "default")).lower()
            if unit in ("percent", "per"):
                return {"slight": 10.0, "default": 20.0, "significant": 35.0}.get(
                    level, 20.0
                )
            return {"slight": 0.5, "default": 1.0, "significant": 1.5}.get(
                level, 1.0
            )
        return 0.0

    def _height_delta_for_slope_amount(self, length, current_height_delta):
        current_abs = abs(float(current_height_delta))
        if current_abs >= 1e-12:
            return current_abs
        return max(abs(float(length)) * 0.2, 1.0)

    def _resolve_slope_magnitude(self, magnitude):
        kind = str(magnitude.get("kind", "none")).lower()
        if kind == "numeric":
            return self._to_float(magnitude.get("value", 0))
        if kind == "qualitative":
            level = str(magnitude.get("level", "default")).lower()
            if level == "slight":
                return random.uniform(0.15, 0.25)
            if level == "significant":
                return random.uniform(0.40, 0.70)
            return random.uniform(0.25, 0.40)
        return 0.0

    def _build_width_command(self, road_id, value, smooth, ms, sh, lane_ids):
        command = [
            "width",
            f"id={road_id}",
            f"v={self._format_number(value)}",
            f"s={int(bool(smooth))}",
            f"ms={int(ms)}",
            f"sh={int(sh)}",
        ]
        if lane_ids:
            command.append(f"li={','.join(str(item) for item in lane_ids)}")
        return command

    def _build_slope_command(self, road_id, value, mode, mv, ms, sh):
        return [
            "slope",
            f"id={road_id}",
            f"v={self._format_number(value)}",
            f"m={mode}",
            f"mv={int(mv)}",
            f"ms={int(ms)}",
            f"sh={int(sh)}",
        ]

    def _road_limit_to_scope(self, road_limit):
        if road_limit in (None, "none", "None"):
            return {"ms": 99, "sh": 1}
        limit = int(road_limit)
        return {"ms": 0 if limit <= 1 else limit - 1, "sh": 1}

    def _resolve_road_ids(self, payload_id, source_command=None):
        del source_command
        if payload_id is None:
            raise ValueError("Road id is missing in agent result.")
        return self._normalize_road_ids(payload_id)

    def _normalize_road_ids(self, payload_id):
        if payload_id == "all":
            return sorted(XParser.roads.keys(), key=lambda item: int(item))
        if isinstance(payload_id, list):
            road_ids = []
            for item in payload_id:
                road_ids.extend(self._normalize_road_ids(item))
            return road_ids
        value = str(payload_id)
        return [value.split("_", 1)[1] if value.startswith("road_") else value]

    def _normalize_lane_ids(self, lane_ids):
        if lane_ids in (None, "none", "None", []):
            return []
        if not isinstance(lane_ids, (list, tuple, set)):
            lane_ids = [lane_ids]
        normalized = []
        for lane_id in lane_ids:
            value = str(lane_id)
            if value.startswith("lane_"):
                value = value.split("_", 1)[1]
            normalized.append(int(value))
        return normalized

    def _to_float(self, value):
        if isinstance(value, (int, float)):
            return float(value)
        if value in (None, "none", "None", ""):
            return 0.0
        return float(str(value))

    def _format_number(self, value):
        value = float(value)
        if abs(value) < 1e-8:
            value = 0.0
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text else "0"
