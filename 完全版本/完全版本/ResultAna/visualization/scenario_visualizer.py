from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Callable, Iterable
import xml.etree.ElementTree as ET

from map_locator import MapLocation, OpenDriveMapLocator, TrajectoryMatch


_ROAD_SAMPLE_SPACING = 2.0
_TRAJECTORY_ROAD_EDGE_MARGIN = 1.0
_TRAJECTORY_ROAD_ENDPOINT_MARGIN = 0.1


@dataclass(frozen=True)
class VisualizationSummary:
    output_path: Path
    road_count: int
    npc_count: int
    waypoint_count: int


@dataclass(frozen=True)
class _RoadSample:
    s: float
    x: float
    y: float
    heading: float


def build_scenario_visualization(
    xodr_path: str | Path,
    scenario_path: str | Path,
    output_path: str | Path,
) -> VisualizationSummary:
    """Build a self-contained HTML view of one XODR map and LGSVL scenario."""

    xodr_path = Path(xodr_path)
    scenario_path = Path(scenario_path)
    output_path = Path(output_path)
    payload = build_visualization_payload(xodr_path, scenario_path)
    html = render_visualization_html(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    summary = payload["summary"]
    return VisualizationSummary(
        output_path=output_path,
        road_count=summary["roadCount"],
        npc_count=summary["npcCount"],
        waypoint_count=summary["waypointCount"],
    )


def build_visualization_payload(
    xodr_path: str | Path,
    scenario_path: str | Path,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, object]:
    """Build serializable map and NPC data without writing an HTML file."""

    def report(progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(progress, message)

    xodr_path = Path(xodr_path)
    scenario_path = Path(scenario_path)
    report(5, "正在读取地图和场景")
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    locator = OpenDriveMapLocator(xodr_path)

    roads = []
    for index, road in enumerate(locator.roads):
        roads.append(_sample_road(road))
        report(
            10 + round(20 * (index + 1) / len(locator.roads)),
            f"正在解析道路 {index + 1}/{len(locator.roads)}",
        )
    npc_agents = [
        agent
        for agent in scenario.get("agents", [])
        if agent.get("type") == 2
    ]
    npcs = []
    for index, agent in enumerate(npc_agents):
        npcs.append(_build_npc_payload(locator, agent, index))
        report(
            30 + round(65 * (index + 1) / len(npc_agents)),
            f"正在定位 NPC {index + 1}/{len(npc_agents)}",
        )
    report(98, "正在整理可视化数据")
    waypoint_count = sum(npc["waypointCount"] for npc in npcs)
    payload = {
        "mapName": scenario.get("map", {}).get("name") or xodr_path.stem,
        "mapFile": xodr_path.name,
        "scenarioFile": scenario_path.name,
        "roads": roads,
        "npcs": npcs,
        "summary": {
            "roadCount": len(roads),
            "npcCount": len(npcs),
            "waypointCount": waypoint_count,
        },
    }
    report(100, "解析完成")
    return payload


def render_visualization_html(payload: dict[str, object]) -> str:
    """Render one visualization payload into the existing standalone viewer."""

    template_path = Path(__file__).with_name("viewer.html")
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    return template_path.read_text(encoding="utf-8").replace(
        "__VISUALIZATION_DATA__",
        payload_json,
    )


def _build_npc_payload(
    locator: OpenDriveMapLocator,
    agent: dict[str, object],
    index: int,
) -> dict[str, object]:
    transform = _required_mapping(agent, "transform")
    start_position = _required_mapping(transform, "position")
    start_rotation = _required_mapping(transform, "rotation")
    waypoints = sorted(
        agent.get("waypoints") or [],
        key=lambda waypoint: waypoint.get("ordinalNumber", 0),
    )
    positions = [start_position] + [
        _required_mapping(waypoint, "position") for waypoint in waypoints
    ]
    yaws = [start_rotation.get("y")] + [
        _required_mapping(waypoint, "angle").get("y") for waypoint in waypoints
    ]
    matches = locator.locate_trajectory(
        positions,
        yaws,
        road_edge_margin=_TRAJECTORY_ROAD_EDGE_MARGIN,
        road_endpoint_margin=_TRAJECTORY_ROAD_ENDPOINT_MARGIN,
        resolve_remaining=True,
    )

    points = []
    road_ids: list[str] = []
    resolved_road_ids: list[str] = []
    candidate_road_ids: list[str] = []
    resolved_count = 0
    candidate_count = 0
    problem_count = 0
    for point_index, (position, match) in enumerate(zip(positions, matches)):
        point_roads = _match_road_ids(match)
        if match.location is not None:
            resolved_count += 1
            _extend_unique(resolved_road_ids, point_roads)
        elif match.resolution == "ambiguous":
            candidate_count += 1
            _extend_unique(candidate_road_ids, point_roads)
        else:
            problem_count += 1
        _extend_unique(road_ids, point_roads)
        waypoint = waypoints[point_index - 1] if point_index > 0 else None
        points.append(
            {
                "x": float(position["x"]),
                "y": float(position["z"]),
                "roadIds": point_roads,
                "resolution": match.resolution,
                "speed": (
                    float(waypoint.get("speed", 0.0))
                    if waypoint is not None
                    else None
                ),
            }
        )

    if resolved_count == len(matches):
        road_status = "resolved"
    elif resolved_count:
        road_status = "mixed"
    else:
        road_status = "candidate"
    return {
        "id": index + 1,
        "label": f"NPC {index + 1}",
        "variant": str(agent.get("variant") or "Unknown"),
        "color": _npc_color(index),
        "points": points,
        "waypointCount": len(waypoints),
        "roadIds": road_ids,
        "resolvedRoadIds": resolved_road_ids,
        "candidateRoadIds": candidate_road_ids,
        "resolvedPointCount": resolved_count,
        "candidatePointCount": candidate_count,
        "problemPointCount": problem_count,
        "roadStatus": road_status,
    }


def _match_road_ids(match: TrajectoryMatch) -> list[str]:
    if match.location is not None:
        return [match.location.road_id]
    candidates: Iterable[MapLocation] = (
        match.feasible_candidates or match.candidates
    )
    road_ids: list[str] = []
    _extend_unique(road_ids, (candidate.road_id for candidate in candidates))
    return road_ids


def _extend_unique(target: list[str], values: Iterable[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _sample_road(road: ET.Element) -> dict[str, object]:
    samples: list[_RoadSample] = []
    plan_view = road.find("planView")
    if plan_view is not None:
        for geometry in plan_view.findall("geometry"):
            geometry_samples = _sample_geometry(geometry)
            if samples and geometry_samples:
                geometry_samples = geometry_samples[1:]
            samples.extend(geometry_samples)
    lanes = _sample_lanes(road, samples)
    return {
        "id": road.get("id", ""),
        "name": road.get("name", ""),
        "junction": road.get("junction", "-1"),
        "points": [_point(sample.x, sample.y) for sample in samples],
        "lanes": lanes,
        "startCap": _road_endpoint_cap(road, samples[0]) if samples else [],
        "endCap": _road_endpoint_cap(road, samples[-1]) if samples else [],
    }


def _road_endpoint_cap(
    road: ET.Element,
    sample: _RoadSample,
) -> list[dict[str, float]]:
    lanes_element = road.find("lanes")
    if lanes_element is None:
        return []
    sections = sorted(
        lanes_element.findall("laneSection"),
        key=lambda section: float(section.get("s", "0")),
    )
    active_section = None
    for section in sections:
        if float(section.get("s", "0")) <= sample.s + 1e-8:
            active_section = section
        else:
            break
    if active_section is None:
        return []

    section_s = float(active_section.get("s", "0"))
    lane_offset = _piecewise_cubic_value(
        lanes_element.findall("laneOffset"),
        sample.s,
        "s",
        0.0,
    )
    lateral_edges = [lane_offset]
    for side_name, direction in (("left", 1.0), ("right", -1.0)):
        side = active_section.find(side_name)
        if side is None:
            continue
        cumulative_width = 0.0
        for lane in sorted(
            side.findall("lane"),
            key=lambda item: abs(int(item.get("id", "0"))),
        ):
            if int(lane.get("id", "0")) == 0:
                continue
            width = _piecewise_cubic_value(
                lane.findall("width"),
                max(0.0, sample.s - section_s),
                "sOffset",
                math.nan,
            )
            if not math.isfinite(width) or width <= 0:
                continue
            cumulative_width += width
            lateral_edges.append(lane_offset + direction * cumulative_width)
    if len(lateral_edges) < 2:
        return []
    return [
        _offset_point(sample, max(lateral_edges)),
        _offset_point(sample, min(lateral_edges)),
    ]


def _sample_geometry(geometry: ET.Element) -> list[_RoadSample]:
    road_s = float(geometry.get("s", "0"))
    start_x = float(geometry.get("x", "0"))
    start_y = float(geometry.get("y", "0"))
    heading = float(geometry.get("hdg", "0"))
    length = float(geometry.get("length", "0"))
    sample_count = max(2, math.ceil(length / _ROAD_SAMPLE_SPACING) + 1)
    child = next(iter(geometry))

    if child.tag == "line":
        return [
            _RoadSample(
                s=road_s + distance,
                x=start_x + distance * math.cos(heading),
                y=start_y + distance * math.sin(heading),
                heading=heading,
            )
            for distance in _linspace(0.0, length, sample_count)
        ]
    if child.tag != "paramPoly3":
        raise ValueError(f"Unsupported visualization geometry {child.tag!r}.")

    parameter_end = (
        length if child.get("pRange", "normalized") == "arcLength" else 1.0
    )
    coefficients_u = [
        float(child.get(name, "0")) for name in ("aU", "bU", "cU", "dU")
    ]
    coefficients_v = [
        float(child.get(name, "0")) for name in ("aV", "bV", "cV", "dV")
    ]
    cos_heading = math.cos(heading)
    sin_heading = math.sin(heading)
    curve_samples = []
    parameters = list(_linspace(0.0, parameter_end, sample_count))
    for parameter in parameters:
        u = _cubic_value(coefficients_u, parameter)
        v = _cubic_value(coefficients_v, parameter)
        du = _cubic_derivative(coefficients_u, parameter)
        dv = _cubic_derivative(coefficients_v, parameter)
        curve_samples.append(
            (
                start_x + u * cos_heading - v * sin_heading,
                start_y + u * sin_heading + v * cos_heading,
                heading + math.atan2(dv, du),
            )
        )
    if child.get("pRange", "normalized") == "arcLength":
        local_stations = parameters
    else:
        cumulative = [0.0]
        for previous, current in zip(curve_samples, curve_samples[1:]):
            cumulative.append(
                cumulative[-1]
                + math.hypot(current[0] - previous[0], current[1] - previous[1])
            )
        total = cumulative[-1]
        local_stations = (
            [value * length / total for value in cumulative]
            if total > 0
            else list(_linspace(0.0, length, sample_count))
        )
    return [
        _RoadSample(
            s=road_s + local_s,
            x=sample[0],
            y=sample[1],
            heading=sample[2],
        )
        for local_s, sample in zip(local_stations, curve_samples)
    ]


def _lane_average_widths(road: ET.Element) -> dict[int, float]:
    """Match the editor's length-weighted lane width over the whole road."""
    totals: dict[int, list[float]] = {}
    sections = road.find("lanes").findall("laneSection")
    road_length = float(road.get("length", "0"))
    for index, section in enumerate(sections):
        start = float(section.get("s", "0"))
        end = (float(sections[index + 1].get("s", "0"))
               if index + 1 < len(sections) else road_length)
        length = max(0.0, end - start)
        for lane in section.findall(".//lane"):
            lane_id = int(lane.get("id", "0"))
            if lane_id == 0:
                continue
            total = totals.setdefault(lane_id, [0.0, 0.0])
            widths = lane.findall("width")
            for width_index, width in enumerate(widths):
                offset = float(width.get("sOffset", "0"))
                offset_end = (float(widths[width_index + 1].get("sOffset", "0"))
                              if width_index + 1 < len(widths) else length)
                segment = max(0.0, offset_end - offset)
                a, b, c, d = (float(width.get(key, "0")) for key in ("a", "b", "c", "d"))
                average = a + b * segment / 2 + c * segment**2 / 3 + d * segment**3 / 4
                total[0] += average * segment
                total[1] += segment
    return {lane_id: round(weighted / length, 4) if length else 0.0
            for lane_id, (weighted, length) in totals.items()}


def _sample_lanes(
    road: ET.Element,
    samples: list[_RoadSample],
) -> list[dict[str, object]]:
    lanes_element = road.find("lanes")
    if lanes_element is None or not samples:
        return []
    sections = sorted(
        lanes_element.findall("laneSection"),
        key=lambda section: float(section.get("s", "0")),
    )
    if not sections:
        return []

    samples_by_section: list[list[_RoadSample]] = [[] for _ in sections]
    for sample in samples:
        section_index = 0
        for candidate_index, section in enumerate(sections):
            if float(section.get("s", "0")) <= sample.s + 1e-8:
                section_index = candidate_index
            else:
                break
        samples_by_section[section_index].append(sample)

    average_widths = _lane_average_widths(road)
    result: list[dict[str, object]] = []
    lane_offsets = lanes_element.findall("laneOffset")
    for section_index, (section, section_samples) in enumerate(
        zip(sections, samples_by_section)
    ):
        section_s = float(section.get("s", "0"))
        records: dict[tuple[str, int], dict[str, object]] = {}
        for side_name, direction in (("left", 1.0), ("right", -1.0)):
            side = section.find(side_name)
            if side is None:
                continue
            for lane in sorted(
                side.findall("lane"),
                key=lambda item: abs(int(item.get("id", "0"))),
            ):
                lane_id = int(lane.get("id", "0"))
                if lane_id == 0:
                    continue
                records[(side_name, lane_id)] = {
                    "id": lane_id,
                    "type": lane.get("type", "none"),
                    "side": side_name,
                    "sectionIndex": section_index,
                    "averageWidth": average_widths.get(lane_id, 0.0),
                    "innerPoints": [],
                    "outerPoints": [],
                    "centerPoints": [],
                    "widths": [],
                }

        for sample in section_samples:
            lane_offset = _piecewise_cubic_value(
                lane_offsets,
                sample.s,
                "s",
                0.0,
            )
            for side_name, direction in (("left", 1.0), ("right", -1.0)):
                side = section.find(side_name)
                if side is None:
                    continue
                cumulative_width = 0.0
                for lane in sorted(
                    side.findall("lane"),
                    key=lambda item: abs(int(item.get("id", "0"))),
                ):
                    lane_id = int(lane.get("id", "0"))
                    if lane_id == 0:
                        continue
                    width = _piecewise_cubic_value(
                        lane.findall("width"),
                        max(0.0, sample.s - section_s),
                        "sOffset",
                        math.nan,
                    )
                    if not math.isfinite(width) or width < 0:
                        continue
                    inner = lane_offset + direction * cumulative_width
                    outer = lane_offset + direction * (cumulative_width + width)
                    record = records[(side_name, lane_id)]
                    record["innerPoints"].append(_offset_point(sample, inner))
                    record["outerPoints"].append(_offset_point(sample, outer))
                    record["centerPoints"].append(
                        _offset_point(sample, (inner + outer) / 2)
                    )
                    record["widths"].append(width)
                    cumulative_width += width

        result.extend(
            record
            for record in records.values()
            if len(record["centerPoints"]) >= 2 and max(record["widths"]) > 0
        )
    return result


def _offset_point(sample: _RoadSample, lateral: float) -> dict[str, float]:
    return _point(
        sample.x - math.sin(sample.heading) * lateral,
        sample.y + math.cos(sample.heading) * lateral,
    )


def _point(x: float, y: float) -> dict[str, float]:
    return {"x": x, "y": y}


def _linspace(start: float, end: float, count: int) -> Iterable[float]:
    if count == 1:
        yield start
        return
    for index in range(count):
        yield start + (end - start) * index / (count - 1)


def _cubic_value(coefficients: list[float], parameter: float) -> float:
    return (
        coefficients[0]
        + coefficients[1] * parameter
        + coefficients[2] * parameter**2
        + coefficients[3] * parameter**3
    )


def _cubic_derivative(coefficients: list[float], parameter: float) -> float:
    return (
        coefficients[1]
        + 2 * coefficients[2] * parameter
        + 3 * coefficients[3] * parameter**2
    )


def _piecewise_cubic_value(
    records: list[ET.Element],
    position: float,
    start_attribute: str,
    default: float,
) -> float:
    active = None
    active_start = 0.0
    for record in sorted(
        records,
        key=lambda item: float(item.get(start_attribute, "0")),
    ):
        record_start = float(record.get(start_attribute, "0"))
        if record_start <= position + 1e-8:
            active = record
            active_start = record_start
        else:
            break
    if active is None:
        return default
    delta = max(0.0, position - active_start)
    return _cubic_value(
        [float(active.get(name, "0")) for name in ("a", "b", "c", "d")],
        delta,
    )


def _npc_color(index: int) -> str:
    hue = (index * 137.508 + 18) % 360
    return f"hsl({hue:.1f} 72% 56%)"


def _required_mapping(mapping: dict[str, object], key: str) -> dict[str, object]:
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Scenario field {key!r} must be an object.")
    return value
