import json


def validate_dsl_format(agent, payload, road_info=None):
    errors = []
    payload = _parse_payload(payload, errors)
    if errors:
        return _result(errors)
    if not isinstance(payload, dict):
        errors.append("top-level DSL output must be an object")
        return _result(errors)

    _check_keys("root", payload, {"operations"}, errors)
    operations = payload.get("operations")
    if not isinstance(operations, list):
        errors.append("operations must be an array")
        return _result(errors)

    for index, operation in enumerate(operations):
        path = f"operations[{index}]"
        if not isinstance(operation, dict):
            errors.append(f"{path} must be an object")
            continue
        if agent == "width":
            _validate_width_operation(path, operation, road_info or {}, errors)
        elif agent == "slope":
            _validate_slope_operation(path, operation, errors)
        elif agent == "curve":
            _validate_curve_operation(path, operation, errors)
        else:
            errors.append(f"unsupported DSL agent type: {agent}")

    return _result(errors)


def _parse_payload(payload, errors):
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            errors.append(f"DSL output is not valid JSON: {exc}")
    return payload


def _validate_width_operation(path, operation, road_info, errors):
    _check_keys(
        path,
        operation,
        {"operation", "road_id", "lane_ids", "road_limit", "change"},
        errors,
    )
    _check_literal(f"{path}.operation", operation.get("operation"), "width", errors)
    _check_road_id(f"{path}.road_id", operation.get("road_id"), errors)
    _check_lane_ids(f"{path}.lane_ids", operation.get("lane_ids"), road_info, errors)
    _check_road_limit(f"{path}.road_limit", operation.get("road_limit"), errors)
    _check_change(
        f"{path}.change",
        operation.get("change"),
        action_values={"increase", "decrease", "set", "none"},
        mode_values={"absolute", "relative", "none"},
        magnitude_units={"m", "percent", "none"},
        errors=errors,
    )


def _validate_slope_operation(path, operation, errors):
    _check_keys(
        path,
        operation,
        {"operation", "road_id", "position", "road_limit", "change"},
        errors,
    )
    _check_literal(f"{path}.operation", operation.get("operation"), "slope", errors)
    _check_road_id(f"{path}.road_id", operation.get("road_id"), errors)
    _check_enum(
        f"{path}.position", operation.get("position"), {"start", "end", "both"}, errors
    )
    _check_road_limit(f"{path}.road_limit", operation.get("road_limit"), errors)
    _check_change(
        f"{path}.change",
        operation.get("change"),
        action_values={"increase", "decrease", "set", "none"},
        mode_values={"absolute", "relative", "none"},
        magnitude_units={"percent", "degree", "m", "none"},
        errors=errors,
        extra_change_keys={"attribute"},
        attribute_values={"grade", "elevation_delta", "none"},
    )


def _validate_curve_operation(path, operation, errors):
    _check_keys(
        path,
        operation,
        {"operation", "road_id", "road_limit", "shape", "semantic", "modification"},
        errors,
    )
    _check_literal(f"{path}.operation", operation.get("operation"), "curve", errors)
    _check_road_id(f"{path}.road_id", operation.get("road_id"), errors)
    _check_road_limit(f"{path}.road_limit", operation.get("road_limit"), errors)

    shape = operation.get("shape")
    if not isinstance(shape, dict):
        errors.append(f"{path}.shape must be an object")
    else:
        _check_keys(f"{path}.shape", shape, {"target"}, errors)
        _check_enum(
            f"{path}.shape.target",
            shape.get("target"),
            {"keep", "C-shape", "S-shape"},
            errors,
        )

    semantic = operation.get("semantic")
    if not isinstance(semantic, dict):
        errors.append(f"{path}.semantic must be an object")
    else:
        _check_keys(f"{path}.semantic", semantic, {"semantic_change", "target"}, errors)
        if not isinstance(semantic.get("semantic_change"), bool):
            errors.append(f"{path}.semantic.semantic_change must be boolean")
        _check_enum(
            f"{path}.semantic.target",
            semantic.get("target"),
            {"keep", "left", "right", "left_right", "right_left"},
            errors,
        )

    modification = operation.get("modification")
    if not isinstance(modification, list):
        errors.append(f"{path}.modification must be an array")
        return
    if len(modification) != 2:
        errors.append(f"{path}.modification must contain exactly 2 items")
    for index, item in enumerate(modification):
        _validate_curve_modification(f"{path}.modification[{index}]", item, errors)


def _validate_curve_modification(path, item, errors):
    if not isinstance(item, dict):
        errors.append(f"{path} must be an object")
        return
    _check_keys(path, item, {"centroid_shift", "curvature_peak"}, errors)
    centroid_shift = item.get("centroid_shift")
    if not isinstance(centroid_shift, dict):
        errors.append(f"{path}.centroid_shift must be an object")
    else:
        _check_keys(f"{path}.centroid_shift", centroid_shift, {"change"}, errors)
        _check_change(
            f"{path}.centroid_shift.change",
            centroid_shift.get("change"),
            action_values={"move_to_start", "move_to_end", "set", "none"},
            mode_values={"absolute", "relative", "none"},
            magnitude_units={"m", "percent", "none"},
            errors=errors,
        )

    curvature_peak = item.get("curvature_peak")
    if not isinstance(curvature_peak, dict):
        errors.append(f"{path}.curvature_peak must be an object")
    else:
        _check_keys(
            f"{path}.curvature_peak", curvature_peak, {"value_type", "change"}, errors
        )
        _check_enum(
            f"{path}.curvature_peak.value_type",
            curvature_peak.get("value_type"),
            {"curvature", "radius", "none"},
            errors,
        )
        _check_change(
            f"{path}.curvature_peak.change",
            curvature_peak.get("change"),
            action_values={"increase", "decrease", "set", "none"},
            mode_values={"absolute", "relative", "none"},
            magnitude_units={"curvature", "m", "percent", "none"},
            errors=errors,
        )


def _check_change(
    path,
    change,
    action_values,
    mode_values,
    magnitude_units,
    errors,
    extra_change_keys=None,
    attribute_values=None,
):
    if not isinstance(change, dict):
        errors.append(f"{path} must be an object")
        return
    expected_keys = {"mode", "action", "magnitude"} | set(extra_change_keys or ())
    _check_keys(path, change, expected_keys, errors)
    if attribute_values is not None:
        _check_enum(f"{path}.attribute", change.get("attribute"), attribute_values, errors)
    _check_enum(f"{path}.mode", change.get("mode"), mode_values, errors)
    _check_enum(f"{path}.action", change.get("action"), action_values, errors)
    _check_magnitude(f"{path}.magnitude", change.get("magnitude"), magnitude_units, errors)


def _check_magnitude(path, magnitude, unit_values, errors):
    if not isinstance(magnitude, dict):
        errors.append(f"{path} must be an object")
        return
    _check_keys(path, magnitude, {"kind", "value", "level", "unit"}, errors)
    kind = magnitude.get("kind")
    _check_enum(f"{path}.kind", kind, {"numeric", "qualitative", "none"}, errors)
    _check_enum(
        f"{path}.level",
        magnitude.get("level"),
        {"slight", "default", "significant", "none"},
        errors,
    )
    _check_enum(f"{path}.unit", magnitude.get("unit"), unit_values, errors)
    value = magnitude.get("value")
    if kind == "numeric":
        if not _is_number(value):
            errors.append(f"{path}.value must be a number when kind is numeric")
    elif value != "none":
        errors.append(f"{path}.value must be 'none' when kind is not numeric")


def _check_lane_ids(path, lane_ids, road_info, errors):
    if lane_ids == "none":
        return
    if not isinstance(lane_ids, list):
        errors.append(f"{path} must be 'none' or an array")
        return
    valid_lane_ids = set()
    for lane_id in road_info.get("lane_ids", []) or []:
        valid_lane_ids.add(f"lane_{lane_id}")
    for index, lane_id in enumerate(lane_ids):
        item_path = f"{path}[{index}]"
        if not isinstance(lane_id, str) or not lane_id.startswith("lane_"):
            errors.append(f"{item_path} must be a string like 'lane_-1'")
            continue
        try:
            int(lane_id.split("_", 1)[1])
        except ValueError:
            errors.append(f"{item_path} must end with a numeric lane id")
            continue
        if valid_lane_ids and lane_id not in valid_lane_ids:
            errors.append(f"{item_path} is outside road_data.lane_ids")


def _check_road_id(path, value, errors):
    if not isinstance(value, str) or not value:
        errors.append(f"{path} must be a non-empty string")


def _check_road_limit(path, value, errors):
    if value == "none":
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        errors.append(f"{path} must be 'none' or an integer greater than or equal to 1")


def _check_keys(path, obj, expected, errors):
    actual = set(obj.keys())
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append(f"{path} missing fields: {', '.join(missing)}")
    if extra:
        errors.append(f"{path} contains undefined fields: {', '.join(extra)}")


def _check_literal(path, value, expected, errors):
    if value != expected:
        errors.append(f"{path} must be '{expected}'")


def _check_enum(path, value, allowed, errors):
    if value not in allowed:
        allowed_text = ", ".join(sorted(str(item) for item in allowed))
        errors.append(f"{path} must be one of: {allowed_text}")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _result(errors):
    if not errors:
        return {
            "passed": True,
            "problem_source": "none",
            "check_result": "格式问题：无",
        }
    return {
        "passed": False,
        "problem_source": "extraction_error",
        "check_result": "格式问题：" + "；".join(errors) + "；语义问题：未进入 DSL 语义检查",
    }
