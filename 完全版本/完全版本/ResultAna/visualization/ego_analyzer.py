"""Batch EGO trajectory compliance analysis for ResultAna."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import json
import math
from pathlib import Path
import re
from typing import Any

from map_locator import LaneGeometry, MapLocation, OpenDriveMapLocator, TrajectoryMatch

from .scenario_visualizer import _sample_road


VEHICLE_WIDTH = 1.9
VEHICLE_HALF_WIDTH = VEHICLE_WIDTH / 2
FALL_SPEED_THRESHOLD = 50.0
MIN_SLOPE_GRADE = 0.01
NEAR_STOP_SPEED = 0.3
MIN_SLIDE_POINT_COUNT = 3
MIN_SLIDE_SURFACE_DISTANCE = 0.5
_ROAD_EDGE_MARGIN = 1.0
_ROAD_ENDPOINT_MARGIN = 0.1
_MAX_WORKERS = 4
_LANE_CHANGE_STABLE_POINT_COUNT = 3
_LINE_CROSSING_TYPES = frozenset(
    {"left_line_crossing", "right_line_crossing"}
)


def parse_ego_log(log_text: str) -> list[dict[str, object]]:
    """Parse and validate a simulator EGO waypoint log."""

    value = _decode_ego_log_records(log_text)
    runs: list[dict[str, object]] = []
    for index, raw_run in enumerate(value):
        if not isinstance(raw_run, dict):
            raise ValueError(f"EGO log item {index + 1} must be a JSON object.")
        json_name = raw_run.get("jsonname")
        waypoints = raw_run.get("waypoints")
        if not isinstance(json_name, str) or not json_name.strip():
            raise ValueError(f"EGO log item {index + 1} has no valid jsonname.")
        if Path(json_name).name != json_name:
            raise ValueError(
                f"EGO log item {index + 1} jsonname must be an original file name."
            )
        if not isinstance(waypoints, list):
            raise ValueError(
                f"EGO log item {index + 1} has no valid waypoints array."
            )
        for point_index, waypoint in enumerate(waypoints):
            _validate_waypoint(waypoint, index, point_index)
        runs.append(raw_run)
    return runs


def _decode_ego_log_records(log_text: str) -> list[object]:
    text = log_text[1:] if log_text.startswith("\ufeff") else log_text
    if not text.strip():
        raise ValueError("EGO log contains no records.")
    try:
        root = json.loads(text)
    except json.JSONDecodeError:
        return _decode_jsonl_records(text)
    if isinstance(root, list):
        if not root:
            raise ValueError("EGO log contains no records.")
        return root
    if isinstance(root, dict):
        return [root]
    raise ValueError("EGO log must contain a JSON array or JSONL objects.")


def _decode_jsonl_records(text: str) -> list[object]:
    numbered_lines = [
        (line_number, line)
        for line_number, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    ]
    records: list[object] = []
    terminated = text.endswith(("\n", "\r"))
    for position, (line_number, line) in enumerate(numbered_lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            is_incomplete_tail = (
                position == len(numbered_lines) - 1 and not terminated
                and _looks_like_incomplete_json(line, exc)
            )
            if is_incomplete_tail:
                break
            raise ValueError(
                f"EGO JSONL line {line_number} is not valid JSON: {exc.msg}."
            ) from exc
    if not records:
        raise ValueError("EGO log contains no complete records.")
    return records


def _looks_like_incomplete_json(
    line: str,
    error: json.JSONDecodeError,
) -> bool:
    stripped = line.rstrip()
    if error.pos >= len(stripped):
        return True
    if error.msg.startswith("Unterminated string"):
        return True
    remainder = stripped[error.pos:]
    if remainder in {"t", "tr", "tru", "f", "fa", "fal", "fals", "n", "nu", "nul"}:
        return True
    return bool(re.fullmatch(r"(?:-|\.\d*|[eE][+-]?\d*)", remainder))


def build_ego_analysis(
    log_text: str,
    scenario_directory: str | Path,
    map_directory: str | Path,
    progress_callback: Callable[[int, str], None] | None = None,
    *,
    use_processes: bool = True,
) -> dict[str, object]:
    """Resolve source files and analyze all runs in an EGO log."""

    runs = parse_ego_log(log_text)
    scenario_directory = Path(scenario_directory)
    map_directory = Path(map_directory)
    report = progress_callback or (lambda _progress, _message: None)
    report(3, "正在递归索引场景原文件")
    scenario_index = _index_files(scenario_directory, ".json")
    report(6, "正在索引 OpenDRIVE 地图")
    map_index = _index_files(map_directory, ".xodr")

    prepared: list[dict[str, object]] = []
    for run_index, run in enumerate(runs):
        json_name = str(run["jsonname"])
        scenario_path = _unique_exact_file(scenario_index, json_name, "场景 JSON")
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        map_config = scenario.get("map") if isinstance(scenario, dict) else None
        map_name = map_config.get("name") if isinstance(map_config, dict) else None
        if not isinstance(map_name, str) or not map_name.strip():
            raise ValueError(f"{scenario_path} does not contain a valid map.name.")
        map_file_name = f"{map_name.strip()}.xodr"
        sibling_map_path = scenario_path.with_name(map_file_name)
        map_path = (
            sibling_map_path
            if sibling_map_path.is_file()
            else _unique_exact_file(map_index, map_file_name, "XODR 地图")
        )
        prepared.append(
            {
                "index": run_index,
                "run": run,
                "scenarioPath": scenario_path,
                "egoStart": _scenario_ego_point(scenario, "transform"),
                "egoDestination": _scenario_ego_point(
                    scenario,
                    "destinationPoint",
                ),
                "mapName": map_name.strip(),
                "mapPath": map_path,
            }
        )

    grouped: dict[Path, list[dict[str, object]]] = {}
    for item in prepared:
        grouped.setdefault(item["mapPath"], []).append(item)

    completed = 0
    analyzed_runs: list[dict[str, object] | None] = [None] * len(prepared)

    def record_group(group_results: list[tuple[int, dict[str, object]]]) -> None:
        nonlocal completed
        for run_index, payload in group_results:
            analyzed_runs[run_index] = payload
            completed += 1
            progress = 8 + round(88 * completed / len(prepared))
            report(progress, f"正在分析 EGO 轨迹 {completed}/{len(prepared)}")

    worker_count = min(_MAX_WORKERS, len(grouped))
    if worker_count == 1:
        record_group(_analyze_map_group(next(iter(grouped.values()))))
    else:
        executor_type = ProcessPoolExecutor if use_processes else ThreadPoolExecutor
        with executor_type(max_workers=worker_count) as executor:
            futures = [
                executor.submit(_analyze_map_group, items)
                for items in grouped.values()
            ]
            for future in as_completed(futures):
                record_group(future.result())

    final_runs = [run for run in analyzed_runs if run is not None]
    summary = _batch_summary(final_runs)
    report(100, "EGO 轨迹分析完成")
    return {
        "analysisType": "ego-compliance",
        "runs": final_runs,
        "summary": summary,
        "thresholds": {
            "vehicleWidth": VEHICLE_WIDTH,
            "vehicleHalfWidth": VEHICLE_HALF_WIDTH,
            "fallSpeed": FALL_SPEED_THRESHOLD,
            "minimumSlopeGrade": MIN_SLOPE_GRADE,
            "nearStopSpeed": NEAR_STOP_SPEED,
            "minimumSlidePoints": MIN_SLIDE_POINT_COUNT,
            "minimumSlideSurfaceDistance": MIN_SLIDE_SURFACE_DISTANCE,
        },
    }


def populate_run_roads(
    payload: dict[str, object],
    scenario_directory: str | Path | None = None,
) -> dict[str, object]:
    """Populate cached map and EGO route landmark data for a stored run."""

    if not payload.get("roads"):
        map_path = payload.get("mapPath")
        if not isinstance(map_path, str) or not map_path:
            raise ValueError("Stored EGO run has no map source path.")
        locator = OpenDriveMapLocator(map_path)
        payload["roads"] = [_sample_road(road) for road in locator.roads]
        payload["summary"]["roadCount"] = len(locator.roads)
    if (
        scenario_directory is not None
        and (
            "egoStart" not in payload
            or "egoDestination" not in payload
        )
    ):
        scenario = _stored_run_scenario(payload, Path(scenario_directory))
        payload["egoStart"] = _scenario_ego_point(scenario, "transform")
        payload["egoDestination"] = _scenario_ego_point(
            scenario,
            "destinationPoint",
        )
    return payload


def _stored_run_scenario(
    payload: Mapping[str, object],
    scenario_directory: Path,
) -> object | None:
    scenario_file = payload.get("scenarioFile")
    if not isinstance(scenario_file, str) or not scenario_file:
        return None
    map_path = payload.get("mapPath")
    if isinstance(map_path, str) and map_path:
        sibling = Path(map_path).with_name(scenario_file)
        if sibling.is_file():
            try:
                return json.loads(sibling.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
    if not scenario_directory.is_dir():
        return None
    matches = _index_files(scenario_directory, ".json").get(scenario_file, [])
    if len(matches) != 1:
        return None
    try:
        return json.loads(matches[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _analyze_map_group(
    items: list[dict[str, object]],
) -> list[tuple[int, dict[str, object]]]:
    locator = OpenDriveMapLocator(items[0]["mapPath"])
    results = []
    for item in items:
        map_path = Path(item["mapPath"])
        payload = analyze_ego_run(
            locator,
            item["run"],
            run_index=int(item["index"]),
            scenario_name=Path(item["scenarioPath"]).name,
            map_name=str(item["mapName"]),
            map_file=map_path.name,
        )
        payload["mapPath"] = str(map_path)
        payload["egoStart"] = item["egoStart"]
        payload["egoDestination"] = item["egoDestination"]
        payload["summary"]["roadCount"] = len(locator.roads)
        results.append((int(item["index"]), payload))
    return results


def analyze_ego_run(
    locator: OpenDriveMapLocator,
    run: Mapping[str, object],
    *,
    run_index: int = 0,
    scenario_name: str = "",
    map_name: str = "",
    map_file: str = "",
    roads: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Analyze one ordered EGO trajectory against a loaded map."""

    raw_waypoints = list(run.get("waypoints") or [])
    positions = [waypoint["position"] for waypoint in raw_waypoints]
    yaws = [waypoint["angle"].get("y") for waypoint in raw_waypoints]
    matches = locator.locate_trajectory(
        positions,
        yaws,
        road_edge_margin=_ROAD_EDGE_MARGIN,
        road_endpoint_margin=_ROAD_ENDPOINT_MARGIN,
        resolve_remaining=True,
    )

    points: list[dict[str, object]] = []
    locations: list[MapLocation | None] = []
    geometries: list[LaneGeometry | None] = []
    for point_index, (waypoint, match) in enumerate(zip(raw_waypoints, matches)):
        location, geometry = _driving_location(locator, match)
        position = waypoint["position"]
        yaw = float(waypoint["angle"].get("y", 0.0))
        point = {
            "index": point_index,
            "ordinalNumber": int(waypoint.get("ordinalNumber", point_index)),
            "x": float(position["x"]),
            "y": float(position["z"]),
            "z": float(position.get("y", 0.0)),
            "yaw": yaw,
            "speed": float(waypoint.get("speed", 0.0)),
            "roadIds": _match_road_ids(match),
            "roadId": location.road_id if location is not None else None,
            "laneId": location.lane_id if location is not None else None,
            "s": location.s if location is not None else None,
            "t": location.t if location is not None else None,
            "grade": geometry.grade if geometry is not None else None,
            "resolution": match.resolution if location is not None else "unmatched",
            "leftClearance": None,
            "rightClearance": None,
            "laneChange": False,
            "violationTypes": [],
        }
        if location is not None and geometry is not None:
            left_clearance, right_clearance = _travel_clearances(
                location,
                geometry,
                yaw,
            )
            point["leftClearance"] = left_clearance
            point["rightClearance"] = right_clearance
            if left_clearance < VEHICLE_HALF_WIDTH:
                point["violationTypes"].append("left_line_crossing")
            if right_clearance < VEHICLE_HALF_WIDTH:
                point["violationTypes"].append("right_line_crossing")
        if point["speed"] > FALL_SPEED_THRESHOLD:
            point["violationTypes"].append("fall_off_road")
        points.append(point)
        locations.append(location)
        geometries.append(geometry)

    _suppress_lane_change_crossings(points)
    slide_events = _detect_slide_events(points, locations, geometries)
    for event in slide_events:
        for point_index in range(event["startIndex"], event["endIndex"] + 1):
            points[point_index]["violationTypes"].append("longitudinal_slide")

    events = []
    for violation_type in (
        "left_line_crossing",
        "right_line_crossing",
        "fall_off_road",
    ):
        events.extend(_point_events(points, violation_type))
    events.extend(slide_events)
    events.sort(key=lambda event: (event["startIndex"], event["type"]))

    sim_result = str(run.get("sim_result") or "")
    collision_candidate = "collision" in sim_result.casefold()
    stuck_run = "stuck" in sim_result.casefold()
    if collision_candidate and points:
        points[-1]["violationTypes"].append("collision_candidate")
    road_ids = _ordered_road_ids(points)
    line_crossing_points = sum(
        bool(
            {"left_line_crossing", "right_line_crossing"}
            & set(point["violationTypes"])
        )
        for point in points
    )
    stats = {
        "waypointCount": len(points),
        "resolvedPointCount": sum(point["roadId"] is not None for point in points),
        "unmatchedPointCount": sum(point["roadId"] is None for point in points),
        "lineCrossingPointCount": line_crossing_points,
        "leftCrossingPointCount": sum(
            "left_line_crossing" in point["violationTypes"] for point in points
        ),
        "rightCrossingPointCount": sum(
            "right_line_crossing" in point["violationTypes"] for point in points
        ),
        "fallPointCount": sum(
            "fall_off_road" in point["violationTypes"] for point in points
        ),
        "slideEventCount": len(slide_events),
        "eventCount": len(events),
    }
    return {
        "analysisMode": "ego",
        "runIndex": run_index,
        "jsonName": str(run.get("jsonname") or scenario_name),
        "scenarioFile": scenario_name or str(run.get("jsonname") or ""),
        "mapName": map_name or str(run.get("mapname") or map_file),
        "mapFile": map_file,
        "simResult": sim_result,
        "costTime": _optional_float(run.get("cost_time")),
        "finalDistanceToDestination": _optional_float(
            run.get("final_distance_to_destination")
        ),
        "reportedMaxSpeed": _optional_float(run.get("max_speed")),
        "collisionCandidate": collision_candidate,
        "stuck": stuck_run,
        "suspectedCollisionPointIndex": len(points) - 1 if collision_candidate else None,
        "collisionReview": {
            "status": "unreviewed",
            "note": "",
        },
        "roadIds": road_ids,
        "points": points,
        "events": events,
        "stats": stats,
        "roads": roads or [],
        "npcs": [
            {
                "id": 1,
                "label": "EGO",
                "variant": "EGO",
                "color": "#146c94",
                "points": points,
                "waypointCount": len(points),
                "roadIds": road_ids,
                "resolvedRoadIds": road_ids,
                "candidateRoadIds": [],
                "resolvedPointCount": stats["resolvedPointCount"],
                "candidatePointCount": 0,
                "problemPointCount": stats["unmatchedPointCount"],
                "roadStatus": (
                    "resolved" if stats["unmatchedPointCount"] == 0 else "mixed"
                ),
            }
        ],
        "summary": {
            "roadCount": len(roads or []),
            "npcCount": 1,
            "waypointCount": len(points),
        },
    }


def _validate_waypoint(
    waypoint: object,
    run_index: int,
    point_index: int,
) -> None:
    if not isinstance(waypoint, dict):
        raise ValueError(
            f"EGO log item {run_index + 1} waypoint {point_index + 1} "
            "must be a JSON object."
        )
    position = waypoint.get("position")
    angle = waypoint.get("angle")
    if not isinstance(position, dict) or not isinstance(angle, dict):
        raise ValueError(
            f"EGO log item {run_index + 1} waypoint {point_index + 1} "
            "must contain position and angle objects."
        )
    for key in ("x", "y", "z"):
        _finite(position.get(key), f"waypoint position.{key}")
    _finite(angle.get("y"), "waypoint angle.y")
    _finite(waypoint.get("speed"), "waypoint speed")


def _finite(value: object, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number.")
    return number


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _scenario_ego_point(
    scenario: object,
    point_key: str,
) -> dict[str, float] | None:
    if not isinstance(scenario, dict):
        return None
    agents = scenario.get("agents")
    if not isinstance(agents, list):
        return None
    ego = next(
        (
            agent
            for agent in agents
            if isinstance(agent, dict) and agent.get("type") == 1
        ),
        None,
    )
    if ego is None:
        return None
    point_config = ego.get(point_key)
    if not isinstance(point_config, dict):
        return None
    position = point_config.get("position")
    if not isinstance(position, dict):
        return None
    x = _optional_float(position.get("x"))
    height = _optional_float(position.get("y"))
    map_y = _optional_float(position.get("z"))
    if x is None or height is None or map_y is None:
        return None
    return {"x": x, "y": map_y, "z": height}


def _index_files(directory: Path, suffix: str) -> dict[str, list[Path]]:
    if not directory.is_dir():
        raise ValueError(f"Cannot read source directory: {directory}")
    index: dict[str, list[Path]] = {}
    for path in directory.rglob(f"*{suffix}"):
        if path.is_file() and path.suffix.casefold() == suffix.casefold():
            index.setdefault(path.name, []).append(path)
    return index


def _unique_exact_file(
    index: Mapping[str, list[Path]],
    file_name: str,
    label: str,
) -> Path:
    matches = index.get(file_name, [])
    if not matches:
        raise ValueError(f"Cannot find exact {label} file {file_name!r}.")
    if len(matches) > 1:
        paths = ", ".join(str(path) for path in matches)
        raise ValueError(f"Multiple exact {label} files named {file_name!r}: {paths}")
    return matches[0]


def _driving_location(
    locator: OpenDriveMapLocator,
    match: TrajectoryMatch,
) -> tuple[MapLocation | None, LaneGeometry | None]:
    candidates: list[MapLocation] = []
    for location in (
        (match.location,) if match.location is not None else ()
    ) + match.feasible_candidates + match.candidates:
        if location not in candidates:
            candidates.append(location)
    candidates.sort(
        key=lambda location: (
            location != match.location,
            abs(location.heading_error or 0.0),
            location.distance_to_lane_center,
        )
    )
    for location in candidates:
        geometry = locator.lane_geometry(location)
        if geometry is not None and geometry.lane_type == "driving":
            return location, geometry
    return None, None


def _match_road_ids(match: TrajectoryMatch) -> list[str]:
    candidates: Iterable[MapLocation]
    if match.location is not None:
        candidates = (match.location,)
    else:
        candidates = match.feasible_candidates or match.candidates
    result: list[str] = []
    for candidate in candidates:
        if candidate.road_id not in result:
            result.append(candidate.road_id)
    return result


def _travel_clearances(
    location: MapLocation,
    geometry: LaneGeometry,
    yaw_degrees: float,
) -> tuple[float, float]:
    heading = (math.pi / 2 - math.radians(yaw_degrees)) % math.tau
    aligned = math.cos(heading - location.reference_heading) >= 0
    low_t, high_t = sorted((geometry.inner_t, geometry.outer_t))
    if aligned:
        return high_t - location.t, location.t - low_t
    return location.t - low_t, high_t - location.t


def _suppress_lane_change_crossings(
    points: list[dict[str, object]],
) -> None:
    crossing_indexes = [
        index
        for index, point in enumerate(points)
        if _LINE_CROSSING_TYPES & set(point["violationTypes"])
    ]
    for start, end in _contiguous_groups(crossing_indexes):
        before_start = start - _LANE_CHANGE_STABLE_POINT_COUNT
        after_end = end + _LANE_CHANGE_STABLE_POINT_COUNT
        if before_start < 0 or after_end >= len(points):
            continue
        before_keys = {
            _point_lane_key(point)
            for point in points[before_start:start]
        }
        after_keys = {
            _point_lane_key(point)
            for point in points[end + 1 : after_end + 1]
        }
        if len(before_keys) != 1 or len(after_keys) != 1:
            continue
        source = next(iter(before_keys))
        target = next(iter(after_keys))
        if not _is_adjacent_lane_change(source, target):
            continue
        crossing_keys = {
            _point_lane_key(point)
            for point in points[start : end + 1]
        }
        if crossing_keys != {source, target}:
            continue
        for point in points[start : end + 1]:
            point["violationTypes"] = [
                violation
                for violation in point["violationTypes"]
                if violation not in _LINE_CROSSING_TYPES
            ]
            point["laneChange"] = True


def _point_lane_key(
    point: Mapping[str, object],
) -> tuple[str, int] | None:
    road_id = point.get("roadId")
    lane_id = point.get("laneId")
    if road_id is None or not isinstance(lane_id, int) or lane_id == 0:
        return None
    return str(road_id), lane_id


def _is_adjacent_lane_change(
    source: tuple[str, int] | None,
    target: tuple[str, int] | None,
) -> bool:
    if source is None or target is None or source[0] != target[0]:
        return False
    source_lane, target_lane = source[1], target[1]
    return (
        source_lane * target_lane > 0
        and abs(source_lane - target_lane) == 1
    )


def _contiguous_groups(indexes: list[int]) -> list[tuple[int, int]]:
    if not indexes:
        return []
    groups: list[tuple[int, int]] = []
    start = previous = indexes[0]
    for index in indexes[1:]:
        if index != previous + 1:
            groups.append((start, previous))
            start = index
        previous = index
    groups.append((start, previous))
    return groups


def _detect_slide_events(
    points: list[dict[str, object]],
    locations: list[MapLocation | None],
    geometries: list[LaneGeometry | None],
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    start: int | None = None
    distance = 0.0

    def finish(end_index: int) -> None:
        nonlocal start, distance
        if start is None:
            return
        point_count = end_index - start + 1
        segment_points = points[start : end_index + 1]
        first = segment_points[0]
        heading_uphill = all(
            _heading_points_uphill(points[index], locations[index], geometries[index])
            for index in range(start, end_index + 1)
        )
        restarted_after_stop = (
            float(first["speed"]) <= NEAR_STOP_SPEED
        )
        if (
            point_count >= MIN_SLIDE_POINT_COUNT
            and distance >= MIN_SLIDE_SURFACE_DISTANCE
            and (heading_uphill or restarted_after_stop)
        ):
            events.append(
                {
                    "type": "longitudinal_slide",
                    "side": "",
                    "startIndex": start,
                    "endIndex": end_index,
                    "startOrdinal": int(first["ordinalNumber"]),
                    "endOrdinal": int(segment_points[-1]["ordinalNumber"]),
                    "pointCount": point_count,
                    "roadId": first["roadId"],
                    "laneId": first["laneId"],
                    "minimumClearance": None,
                    "peakSpeed": max(
                        float(point["speed"]) for point in segment_points
                    ),
                    "downhillSurfaceDistance": distance,
                    "evidence": (
                        "heading_uphill"
                        if heading_uphill
                        else "restart_after_near_stop"
                    ),
                }
            )
        start = None
        distance = 0.0

    for index in range(1, len(points)):
        previous_location = locations[index - 1]
        location = locations[index]
        previous_geometry = geometries[index - 1]
        geometry = geometries[index]
        valid = (
            previous_location is not None
            and location is not None
            and previous_geometry is not None
            and geometry is not None
            and previous_location.road_id == location.road_id
        )
        if valid:
            average_grade = (previous_geometry.grade + geometry.grade) / 2
            delta_s = location.s - previous_location.s
            valid = (
                abs(average_grade) >= MIN_SLOPE_GRADE
                and delta_s * average_grade < 0
            )
        if not valid:
            finish(index - 1)
            continue
        if start is None:
            start = index - 1
        distance += abs(delta_s) * math.sqrt(1 + average_grade**2)
    finish(len(points) - 1)
    return events


def _heading_points_uphill(
    point: Mapping[str, object],
    location: MapLocation | None,
    geometry: LaneGeometry | None,
) -> bool:
    if location is None or geometry is None or abs(geometry.grade) < MIN_SLOPE_GRADE:
        return False
    heading = (math.pi / 2 - math.radians(float(point["yaw"]))) % math.tau
    heading_s_direction = 1.0 if math.cos(heading - location.reference_heading) >= 0 else -1.0
    return heading_s_direction * geometry.grade > 0


def _point_events(
    points: list[dict[str, object]],
    violation_type: str,
) -> list[dict[str, object]]:
    indexes = [
        index
        for index, point in enumerate(points)
        if violation_type in point["violationTypes"]
    ]
    if not indexes:
        return []
    groups: list[tuple[int, int]] = []
    start = previous = indexes[0]
    for index in indexes[1:]:
        if index != previous + 1:
            groups.append((start, previous))
            start = index
        previous = index
    groups.append((start, previous))

    side = (
        "left"
        if violation_type == "left_line_crossing"
        else "right"
        if violation_type == "right_line_crossing"
        else ""
    )
    events = []
    for start, end in groups:
        segment = points[start : end + 1]
        clearances = [
            point[f"{side}Clearance"]
            for point in segment
            if side and point[f"{side}Clearance"] is not None
        ]
        events.append(
            {
                "type": violation_type,
                "side": side,
                "startIndex": start,
                "endIndex": end,
                "startOrdinal": int(segment[0]["ordinalNumber"]),
                "endOrdinal": int(segment[-1]["ordinalNumber"]),
                "pointCount": len(segment),
                "roadId": segment[0]["roadId"],
                "laneId": segment[0]["laneId"],
                "minimumClearance": min(clearances) if clearances else None,
                "peakSpeed": max(float(point["speed"]) for point in segment),
                "downhillSurfaceDistance": None,
                "evidence": "trajectory_geometry",
            }
        )
    return events


def _ordered_road_ids(points: Iterable[Mapping[str, object]]) -> list[str]:
    result: list[str] = []
    previous: str | None = None
    for point in points:
        road_id = point.get("roadId")
        if isinstance(road_id, str) and road_id != previous:
            result.append(road_id)
            previous = road_id
    return result


def _batch_summary(runs: Iterable[Mapping[str, object]]) -> dict[str, int]:
    runs = list(runs)
    collision_count = sum(bool(run["collisionCandidate"]) for run in runs)
    return {
        "runCount": len(runs),
        "waypointCount": sum(int(run["stats"]["waypointCount"]) for run in runs),
        "violationRunCount": sum(
            bool(run["events"])
            or bool(run["collisionCandidate"])
            or bool(run["stuck"])
            for run in runs
        ),
        "lineCrossingRunCount": sum(
            int(run["stats"]["lineCrossingPointCount"]) > 0 for run in runs
        ),
        "slideRunCount": sum(int(run["stats"]["slideEventCount"]) > 0 for run in runs),
        "fallRunCount": sum(int(run["stats"]["fallPointCount"]) > 0 for run in runs),
        "collisionCandidateCount": collision_count,
        "unreviewedCollisionCount": collision_count,
        "stuckRunCount": sum(
            bool(run["stuck"]) for run in runs
        ),
        "analysisErrorCount": 0,
    }
