"""Read-only OpenDRIVE waypoint-to-lane locator.

The public coordinate adapter follows the existing simulator convention used by
openDriveMapEditor: simulator ``position.x`` maps to OpenDRIVE x,
``position.z`` maps to OpenDRIVE y, and optional ``position.y`` maps to the
OpenDRIVE elevation z.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
from pathlib import Path
from statistics import median
from typing import Iterable, Literal, Mapping
import xml.etree.ElementTree as ET

import numpy as np
from scipy.integrate import quad


_ENDPOINT_TOLERANCE = 1e-2
_BOUNDARY_TOLERANCE = 1e-8
_MAX_TRAJECTORY_DETOUR_RATIO = 2.0
_MAX_DIRECTION_SCORE = math.radians(15.0)
_MIN_DIRECTION_ADVANTAGE = math.radians(5.0)
_DEFAULT_HEIGHT_TOLERANCE = 2.5
_DEFAULT_QUERY_CACHE_SIZE = 65536
_MAX_INDEX_CELLS_PER_GEOMETRY = 256
_INDEX_BOUND_PADDING = 1e-5
_SUPPORTED_GEOMETRIES = {"line", "paramPoly3"}


class UnsupportedGeometryError(ValueError):
    """Raised when a map contains geometry this locator cannot evaluate."""


@dataclass(frozen=True)
class MapLocation:
    """A waypoint location expressed in OpenDRIVE road coordinates."""

    road_id: str
    lane_id: int
    s: float
    t: float
    reference_heading: float
    heading_error: float | None
    distance_to_lane_center: float
    reference_elevation: float = 0.0
    height_error: float | None = None


@dataclass(frozen=True)
class TrajectoryMatch:
    """A road-level trajectory result with its original point candidates."""

    location: MapLocation | None
    resolution: Literal[
        "direct",
        "propagated",
        "ranked",
        "ambiguous",
        "inconsistent",
        "unmatched",
    ]
    candidates: tuple[MapLocation, ...]
    confidence: float
    feasible_candidates: tuple[MapLocation, ...] = ()


@dataclass(frozen=True)
class LaneGeometry:
    """Lane boundaries and road grade at one resolved map location."""

    road_id: str
    lane_id: int
    lane_type: str
    s: float
    inner_t: float
    outer_t: float
    center_t: float
    width: float
    grade: float


@dataclass(frozen=True)
class _Projection:
    s: float
    t: float
    heading: float
    distance: float


@dataclass(frozen=True)
class _RoadConnection:
    target_road_id: str
    source_contact: Literal["start", "end"] | None
    target_contact: Literal["start", "end"] | None


@dataclass(frozen=True)
class _RoadBounds:
    min_x: float
    max_x: float
    min_y: float
    max_y: float


@dataclass(frozen=True)
class _RoadSpatialIndex:
    origin_x: float
    origin_y: float
    cell_size: float
    geometry_counts: tuple[int, ...]
    cells: dict[tuple[int, int], tuple[tuple[int, int], ...]]
    always_include: tuple[tuple[int, int], ...]

    def query(
        self,
        map_x: float,
        map_y: float,
        margin: float,
    ) -> dict[int, tuple[int, ...]]:
        min_cell_x = math.floor((map_x - margin - self.origin_x) / self.cell_size)
        max_cell_x = math.floor((map_x + margin - self.origin_x) / self.cell_size)
        min_cell_y = math.floor((map_y - margin - self.origin_y) / self.cell_size)
        max_cell_y = math.floor((map_y + margin - self.origin_y) / self.cell_size)
        cell_count = (max_cell_x - min_cell_x + 1) * (
            max_cell_y - min_cell_y + 1
        )
        if cell_count > _MAX_INDEX_CELLS_PER_GEOMETRY:
            return {
                road_index: tuple(range(geometry_count))
                for road_index, geometry_count in enumerate(self.geometry_counts)
            }

        geometry_refs = set(self.always_include)
        for cell_x in range(min_cell_x, max_cell_x + 1):
            for cell_y in range(min_cell_y, max_cell_y + 1):
                geometry_refs.update(self.cells.get((cell_x, cell_y), ()))
        grouped: dict[int, set[int]] = {}
        for road_index, geometry_index in geometry_refs:
            grouped.setdefault(road_index, set()).add(geometry_index)
        return {
            road_index: tuple(sorted(geometry_indices))
            for road_index, geometry_indices in grouped.items()
        }


class OpenDriveMapLocator:
    """Load one XODR map and locate waypoints on its lanes."""

    def __init__(
        self,
        xodr_path: str | Path,
        *,
        enable_acceleration: bool = True,
    ):
        self.path = Path(xodr_path)
        self._acceleration_enabled = enable_acceleration
        self._query_cache: OrderedDict[tuple[object, ...], tuple[MapLocation, ...]] = (
            OrderedDict()
        )
        self.tree = ET.parse(self.path)
        self.root = self.tree.getroot()
        self.roads = tuple(self.root.findall("road"))

        if not self.roads:
            raise ValueError("The XODR file does not contain any roads.")
        self._validate_geometries()
        self._road_lengths = {
            road.get("id", ""): _attr_float(road, "length", math.nan)
            for road in self.roads
        }
        self._roads_by_id = {
            road.get("id", ""): road
            for road in self.roads
        }
        self._road_connections = _build_road_connections(self.root, self.roads)
        self._spatial_index = (
            _build_spatial_index(self.roads) if enable_acceleration else None
        )

    def locate_xy(
        self,
        map_x: float,
        map_y: float,
        heading_rad: float | None = None,
        *,
        road_edge_margin: float = 0.0,
        endpoint_tolerance: float = _ENDPOINT_TOLERANCE,
        map_z: float | None = None,
        height_tolerance: float = _DEFAULT_HEIGHT_TOLERANCE,
    ) -> tuple[MapLocation, ...]:
        """Return lane candidates containing or near an OpenDRIVE x/y/z point."""

        map_x = _finite_float(map_x, "map_x")
        map_y = _finite_float(map_y, "map_y")
        if heading_rad is not None:
            heading_rad = _finite_float(heading_rad, "heading_rad")
        if map_z is not None:
            map_z = _finite_float(map_z, "map_z")
        road_edge_margin = _finite_float(road_edge_margin, "road_edge_margin")
        if road_edge_margin < 0:
            raise ValueError("road_edge_margin must be greater than or equal to zero")
        endpoint_tolerance = _finite_float(endpoint_tolerance, "endpoint_tolerance")
        if endpoint_tolerance < 0:
            raise ValueError("endpoint_tolerance must be greater than or equal to zero")
        height_tolerance = _finite_float(height_tolerance, "height_tolerance")
        if height_tolerance < 0:
            raise ValueError("height_tolerance must be greater than or equal to zero")

        cache_key = (
            map_x,
            map_y,
            heading_rad,
            road_edge_margin,
            endpoint_tolerance,
            map_z,
            height_tolerance,
        )
        if self._acceleration_enabled:
            cached_matches = self._query_cache.get(cache_key)
            if cached_matches is not None:
                self._query_cache.move_to_end(cache_key)
                return cached_matches

        candidate_geometry_indices = (
            self._spatial_index.query(
                map_x,
                map_y,
                max(road_edge_margin, endpoint_tolerance) + _BOUNDARY_TOLERANCE,
            )
            if self._spatial_index is not None
            else None
        )
        if self._spatial_index is not None and not candidate_geometry_indices:
            candidate_geometry_indices = None
        matches: list[MapLocation] = []
        for road_index, road in enumerate(self.roads):
            if candidate_geometry_indices is not None:
                geometry_indices = candidate_geometry_indices.get(road_index)
                if geometry_indices is None:
                    continue
            else:
                geometry_indices = None
            projection = _project_road(
                road,
                map_x,
                map_y,
                endpoint_tolerance=endpoint_tolerance,
                geometry_indices=geometry_indices,
            )
            if projection is None:
                continue
            longitudinal_residual_squared = max(
                0.0, projection.distance**2 - projection.t**2
            )
            if longitudinal_residual_squared > endpoint_tolerance**2:
                continue
            reference_elevation = _road_elevation_at(road, projection.s)
            height_error = (
                None if map_z is None else map_z - reference_elevation
            )
            if height_error is not None and abs(height_error) > height_tolerance:
                continue

            for lane_id, lane_center in _lane_matches(
                road,
                projection.s,
                projection.t,
                road_edge_margin=road_edge_margin,
            ):
                heading_error = None
                if heading_rad is not None:
                    heading_error = _wrap_pi(heading_rad - projection.heading)
                matches.append(
                    MapLocation(
                        road_id=road.get("id", ""),
                        lane_id=lane_id,
                        s=projection.s,
                        t=projection.t,
                        reference_heading=projection.heading,
                        heading_error=heading_error,
                        distance_to_lane_center=abs(projection.t - lane_center),
                        reference_elevation=reference_elevation,
                        height_error=height_error,
                    )
                )

        matches.sort(key=_location_sort_key)
        result = tuple(matches)
        if self._acceleration_enabled:
            self._query_cache[cache_key] = result
            self._query_cache.move_to_end(cache_key)
            if len(self._query_cache) > _DEFAULT_QUERY_CACHE_SIZE:
                self._query_cache.popitem(last=False)
        return result

    def locate_waypoint(
        self,
        position: Mapping[str, object],
        yaw_degrees: float | None = None,
        *,
        height_tolerance: float = _DEFAULT_HEIGHT_TOLERANCE,
    ) -> MapLocation | None:
        """Return the best match for a simulator ``position`` mapping."""

        map_x, map_y, heading = _waypoint_query(position, yaw_degrees)
        matches = self.locate_xy(
            map_x,
            map_y,
            heading,
            map_z=_waypoint_height(position),
            height_tolerance=height_tolerance,
        )
        return matches[0] if matches else None

    def lane_geometry(self, location: MapLocation) -> LaneGeometry | None:
        """Return lane edges and elevation grade for a resolved location."""

        road = self._roads_by_id.get(location.road_id)
        if road is None:
            return None
        lane = _lane_geometry_at(road, location.s, location.lane_id)
        if lane is None:
            return None
        lane_type, inner_t, outer_t = lane
        return LaneGeometry(
            road_id=location.road_id,
            lane_id=location.lane_id,
            lane_type=lane_type,
            s=location.s,
            inner_t=inner_t,
            outer_t=outer_t,
            center_t=(inner_t + outer_t) / 2,
            width=abs(outer_t - inner_t),
            grade=_road_elevation_grade_at(road, location.s),
        )

    def locate_trajectory(
        self,
        positions: Iterable[Mapping[str, object]],
        yaw_degrees: Iterable[float | None] | None = None,
        *,
        road_edge_margin: float = 0.0,
        road_endpoint_margin: float = 0.0,
        height_tolerance: float = _DEFAULT_HEIGHT_TOLERANCE,
        resolve_remaining: bool = False,
    ) -> tuple[TrajectoryMatch, ...]:
        """Resolve road ownership across an ordered simulator trajectory."""

        positions = tuple(positions)
        road_edge_margin = _finite_float(road_edge_margin, "road_edge_margin")
        if road_edge_margin < 0:
            raise ValueError("road_edge_margin must be greater than or equal to zero")
        road_endpoint_margin = _finite_float(
            road_endpoint_margin, "road_endpoint_margin"
        )
        if road_endpoint_margin < 0:
            raise ValueError("road_endpoint_margin must be greater than or equal to zero")
        height_tolerance = _finite_float(height_tolerance, "height_tolerance")
        if height_tolerance < 0:
            raise ValueError("height_tolerance must be greater than or equal to zero")
        if yaw_degrees is None:
            headings: tuple[float | None, ...] = (None,) * len(positions)
        else:
            headings = tuple(yaw_degrees)
            if len(headings) != len(positions):
                raise ValueError("yaw_degrees must contain one value per position")

        queries = tuple(
            _waypoint_query(position, yaw)
            for position, yaw in zip(positions, headings)
        )
        heights = tuple(_waypoint_height(position) for position in positions)
        candidate_layers = tuple(
            self.locate_xy(
                map_x,
                map_y,
                heading,
                map_z=height,
                height_tolerance=height_tolerance,
            )
            for (map_x, map_y, heading), height in zip(queries, heights)
        )
        if road_edge_margin or road_endpoint_margin:
            candidate_layers = tuple(
                candidates
                or self.locate_xy(
                    map_x,
                    map_y,
                    heading,
                    road_edge_margin=road_edge_margin,
                    endpoint_tolerance=max(
                        _ENDPOINT_TOLERANCE,
                        road_endpoint_margin,
                    ),
                    map_z=height,
                    height_tolerance=height_tolerance,
                )
                for candidates, (map_x, map_y, heading), height in zip(
                    candidate_layers, queries, heights
                )
            )
        road_candidate_layers = tuple(
            _best_candidates_by_road(candidates)
            for candidates in candidate_layers
        )
        road_layers = tuple(set(candidates) for candidates in road_candidate_layers)
        feasible_road_layers = [set(road_ids) for road_ids in road_layers]
        resolved_roads: list[str | None] = [
            next(iter(road_ids)) if len(road_ids) == 1 else None
            for road_ids in road_layers
        ]
        step_distances = (0.0,) + tuple(
            math.hypot(
                queries[index][0] - queries[index - 1][0],
                queries[index][1] - queries[index - 1][1],
            )
            for index in range(1, len(queries))
        )
        motion_headings = _trajectory_motion_headings(queries)

        index = 0
        while index < len(road_layers):
            if len(road_layers[index]) <= 1:
                index += 1
                continue
            start = index
            while index < len(road_layers) and len(road_layers[index]) > 1:
                index += 1
            end = index
            left_road = resolved_roads[start - 1] if start > 0 else None
            right_road = resolved_roads[end] if end < len(road_layers) else None
            left_location = (
                road_candidate_layers[start - 1][left_road]
                if left_road is not None
                else None
            )
            right_location = (
                road_candidate_layers[end][right_road]
                if right_road is not None
                else None
            )
            transition_limits = (
                step_distances[start] if left_location is not None else None,
                *(step_distances[item] for item in range(start + 1, end)),
                step_distances[end] if right_location is not None else None,
            )
            feasible_layers = list(
                _feasible_candidate_layers(
                    road_candidate_layers[start:end],
                    left_location,
                    right_location,
                    transition_limits,
                    self._road_connections,
                    self._road_lengths,
                )
            )
            next_road = (
                resolved_roads[end + 1]
                if end + 1 < len(road_layers)
                else None
            )
            # Parallel connector roads can end one sample apart before sharing
            # the same exit. Keep the proven incoming route through that overlap.
            if (
                left_road is not None
                and right_road is not None
                and next_road is not None
                and not any(feasible_layers)
                and left_road in road_candidate_layers[end - 1]
                and _locations_connected(
                    road_candidate_layers[end - 1][left_road],
                    road_candidate_layers[end + 1][next_road],
                    step_distances[end] + step_distances[end + 1],
                    self._road_connections,
                    self._road_lengths,
                )
                and _locations_connected(
                    right_location,
                    road_candidate_layers[end + 1][next_road],
                    step_distances[end + 1],
                    self._road_connections,
                    self._road_lengths,
                )
            ):
                incoming_layers = list(
                    _feasible_candidate_layers(
                        road_candidate_layers[start:end],
                        left_location,
                        None,
                        transition_limits,
                        self._road_connections,
                        self._road_lengths,
                    )
                )
                if all(layer == {left_road} for layer in incoming_layers):
                    feasible_layers = incoming_layers
            preferred_road = right_road
            if preferred_road is None and end == len(road_layers):
                preferred_road = _terminal_direction_anchor(
                    road_candidate_layers[start:end],
                    feasible_layers,
                    queries[start:end],
                    motion_headings[start:end],
                )
            if preferred_road is not None:
                for layer_index in range(len(feasible_layers) - 1, -1, -1):
                    if preferred_road not in feasible_layers[layer_index]:
                        break
                    feasible_layers[layer_index] = {preferred_road}
            for item, feasible_roads in enumerate(feasible_layers, start):
                feasible_road_layers[item] = feasible_roads
                if len(feasible_roads) == 1:
                    resolved_roads[item] = next(iter(feasible_roads))

        results: list[TrajectoryMatch] = []
        for candidates, road_ids, feasible_roads, road_id in zip(
            candidate_layers,
            road_layers,
            feasible_road_layers,
            resolved_roads,
        ):
            if not candidates:
                results.append(
                    TrajectoryMatch(
                        location=None,
                        resolution="unmatched",
                        candidates=(),
                        confidence=0.0,
                    )
                )
                continue
            feasible_candidates = tuple(
                candidate
                for candidate in candidates
                if candidate.road_id in feasible_roads
            )
            if road_id is None:
                if resolve_remaining:
                    ranked_candidates = feasible_candidates or candidates
                    location = min(
                        ranked_candidates,
                        key=_ranked_location_sort_key,
                    )
                    ranked_road_count = len(
                        {candidate.road_id for candidate in ranked_candidates}
                    )
                    results.append(
                        TrajectoryMatch(
                            location=location,
                            resolution="ranked",
                            candidates=candidates,
                            confidence=1.0 / ranked_road_count,
                            feasible_candidates=feasible_candidates,
                        )
                    )
                    continue
                unresolved_resolution: Literal["ambiguous", "inconsistent"] = (
                    "ambiguous" if feasible_roads else "inconsistent"
                )
                results.append(
                    TrajectoryMatch(
                        location=None,
                        resolution=unresolved_resolution,
                        candidates=candidates,
                        confidence=(
                            1.0 / len(feasible_roads) if feasible_roads else 0.0
                        ),
                        feasible_candidates=feasible_candidates,
                    )
                )
                continue
            location = next(
                candidate for candidate in candidates if candidate.road_id == road_id
            )
            resolved_resolution: Literal["direct", "propagated"] = (
                "direct" if len(road_ids) == 1 else "propagated"
            )
            results.append(
                TrajectoryMatch(
                    location=location,
                    resolution=resolved_resolution,
                    candidates=candidates,
                    confidence=1.0,
                    feasible_candidates=feasible_candidates,
                )
            )
        return tuple(results)

    def _validate_geometries(self) -> None:
        for road in self.roads:
            plan_view = road.find("planView")
            if plan_view is None:
                raise ValueError(
                    f"Road {road.get('id', '')!r} does not contain a planView."
                )
            for geometry in plan_view.findall("geometry"):
                children = list(geometry)
                if len(children) != 1 or children[0].tag not in _SUPPORTED_GEOMETRIES:
                    geometry_name = children[0].tag if children else "missing"
                    raise UnsupportedGeometryError(
                        f"Road {road.get('id', '')!r} uses unsupported geometry "
                        f"{geometry_name!r}. Supported types: line, paramPoly3."
                    )
                if children[0].tag == "paramPoly3":
                    p_range = children[0].get("pRange", "normalized")
                    if p_range not in {"normalized", "arcLength"}:
                        raise UnsupportedGeometryError(
                            f"Unsupported paramPoly3 pRange {p_range!r}."
                        )


def _build_spatial_index(
    roads: tuple[ET.Element, ...],
) -> _RoadSpatialIndex | None:
    geometry_entries: list[tuple[int, int, _RoadBounds]] = []
    geometry_counts: list[int] = []
    for road_index, road in enumerate(roads):
        plan_view = road.find("planView")
        if plan_view is None:
            return None
        lateral_bound = _road_lateral_bound(road)
        if not math.isfinite(lateral_bound):
            return None
        geometries = plan_view.findall("geometry")
        geometry_counts.append(len(geometries))
        for geometry_index, geometry in enumerate(geometries):
            bound = _geometry_bounds(geometry)
            if bound is None:
                return None
            geometry_entries.append(
                (
                    road_index,
                    geometry_index,
                    _RoadBounds(
                        bound[0] - lateral_bound - _INDEX_BOUND_PADDING,
                        bound[1] + lateral_bound + _INDEX_BOUND_PADDING,
                        bound[2] - lateral_bound - _INDEX_BOUND_PADDING,
                        bound[3] + lateral_bound + _INDEX_BOUND_PADDING,
                    ),
                )
            )
    if not geometry_entries:
        return None

    resolved_bounds = tuple(entry[2] for entry in geometry_entries)
    min_x = min(bound.min_x for bound in resolved_bounds)
    max_x = max(bound.max_x for bound in resolved_bounds)
    min_y = min(bound.min_y for bound in resolved_bounds)
    max_y = max(bound.max_y for bound in resolved_bounds)
    typical_extent = median(
        max(bound.max_x - bound.min_x, bound.max_y - bound.min_y)
        for bound in resolved_bounds
    )
    cell_size = max(8.0, typical_extent * 2.0)
    if not math.isfinite(cell_size) or cell_size <= 0:
        return None

    cells: dict[tuple[int, int], list[int]] = {}
    always_include: list[tuple[int, int]] = []
    for road_index, geometry_index, bound in geometry_entries:
        min_cell_x = math.floor((bound.min_x - min_x) / cell_size)
        max_cell_x = math.floor((bound.max_x - min_x) / cell_size)
        min_cell_y = math.floor((bound.min_y - min_y) / cell_size)
        max_cell_y = math.floor((bound.max_y - min_y) / cell_size)
        cell_count = (max_cell_x - min_cell_x + 1) * (
            max_cell_y - min_cell_y + 1
        )
        if cell_count > _MAX_INDEX_CELLS_PER_GEOMETRY:
            always_include.append((road_index, geometry_index))
            continue
        for cell_x in range(min_cell_x, max_cell_x + 1):
            for cell_y in range(min_cell_y, max_cell_y + 1):
                cells.setdefault((cell_x, cell_y), []).append(
                    (road_index, geometry_index)
                )

    return _RoadSpatialIndex(
        origin_x=min_x,
        origin_y=min_y,
        cell_size=cell_size,
        geometry_counts=tuple(geometry_counts),
        cells={key: tuple(value) for key, value in cells.items()},
        always_include=tuple(always_include),
    )


def _geometry_bounds(
    geometry: ET.Element,
) -> tuple[float, float, float, float] | None:
    start_x = _attr_float(geometry, "x")
    start_y = _attr_float(geometry, "y")
    heading = _attr_float(geometry, "hdg")
    length = _attr_float(geometry, "length")
    if not all(math.isfinite(value) for value in (start_x, start_y, heading, length)):
        return None
    if length <= 0:
        return None

    line = geometry.find("line")
    if line is not None:
        local_points = ((0.0, 0.0), (length, 0.0))
    else:
        curve = geometry.find("paramPoly3")
        if curve is None:
            return None
        u_coefficients = np.array(
            [_attr_float(curve, name, 0.0) for name in ("aU", "bU", "cU", "dU")]
        )
        v_coefficients = np.array(
            [_attr_float(curve, name, 0.0) for name in ("aV", "bV", "cV", "dV")]
        )
        parameter_end = length if curve.get("pRange") == "arcLength" else 1.0
        parameters = {0.0, parameter_end}
        cos_heading = math.cos(heading)
        sin_heading = math.sin(heading)
        world_x_coefficients = (
            cos_heading * u_coefficients - sin_heading * v_coefficients
        )
        world_y_coefficients = (
            sin_heading * u_coefficients + cos_heading * v_coefficients
        )
        for coefficients in (world_x_coefficients, world_y_coefficients):
            derivative = np.array(
                [coefficients[1], 2 * coefficients[2], 3 * coefficients[3]]
            )
            parameters.update(_real_roots_in_range(derivative, parameter_end))
        local_points = tuple(
            (
                float(np.polynomial.polynomial.polyval(parameter, u_coefficients)),
                float(np.polynomial.polynomial.polyval(parameter, v_coefficients)),
            )
            for parameter in parameters
        )

    cos_heading = math.cos(heading)
    sin_heading = math.sin(heading)
    points = tuple(
        (
            start_x + cos_heading * local_u - sin_heading * local_v,
            start_y + sin_heading * local_u + cos_heading * local_v,
        )
        for local_u, local_v in local_points
    )
    if not all(math.isfinite(value) for point in points for value in point):
        return None
    return (
        min(point[0] for point in points),
        max(point[0] for point in points),
        min(point[1] for point in points),
        max(point[1] for point in points),
    )


def _real_roots_in_range(coefficients: np.ndarray, upper: float) -> tuple[float, ...]:
    if len(coefficients) <= 1 or not np.any(np.abs(coefficients[1:]) > 1e-12):
        return ()
    roots = np.polynomial.polynomial.polyroots(coefficients)
    return tuple(
        min(max(float(root.real), 0.0), upper)
        for root in roots
        if abs(root.imag) <= 1e-8 and -1e-10 <= root.real <= upper + 1e-10
    )


def _road_lateral_bound(road: ET.Element) -> float:
    lanes = road.find("lanes")
    if lanes is None:
        return 0.0
    road_length = _attr_float(road, "length", math.nan)
    if not math.isfinite(road_length) or road_length < 0:
        return math.inf

    lane_offset_bound = _piecewise_cubic_bound(
        lanes.findall("laneOffset"), "s", road_length
    )
    sections = sorted(
        lanes.findall("laneSection"), key=lambda section: _attr_float(section, "s")
    )
    max_section_width = 0.0
    for index, section in enumerate(sections):
        section_start = _attr_float(section, "s")
        section_end = (
            _attr_float(sections[index + 1], "s")
            if index + 1 < len(sections)
            else road_length
        )
        section_length = max(0.0, section_end - section_start)
        total_width = 0.0
        for side_name in ("left", "right"):
            side = section.find(side_name)
            if side is None:
                continue
            for lane in side.findall("lane"):
                if lane.get("id") == "0":
                    continue
                total_width += _piecewise_cubic_bound(
                    lane.findall("width"), "sOffset", section_length
                )
        max_section_width = max(max_section_width, total_width)
    return lane_offset_bound + max_section_width


def _piecewise_cubic_bound(
    records: list[ET.Element],
    start_attribute: str,
    interval_end: float,
) -> float:
    sorted_records = sorted(
        records, key=lambda record: _attr_float(record, start_attribute)
    )
    bounds = []
    for index, record in enumerate(sorted_records):
        next_start = (
            _attr_float(sorted_records[index + 1], start_attribute)
            if index + 1 < len(sorted_records)
            else interval_end
        )
        bounds.append(
            _cubic_absolute_bound(
                record,
                max(0.0, next_start - _attr_float(record, start_attribute)),
            )
        )
    return max(bounds, default=0.0)


def _cubic_absolute_bound(record: ET.Element, interval: float) -> float:
    if not math.isfinite(interval) or interval < 0:
        return math.inf
    coefficients = np.array(
        [_attr_float(record, coefficient, 0.0) for coefficient in ("a", "b", "c", "d")]
    )
    parameters = {0.0, interval}
    derivative = np.array(
        [coefficients[1], 2 * coefficients[2], 3 * coefficients[3]]
    )
    parameters.update(_real_roots_in_range(derivative, interval))
    values = tuple(
        float(np.polynomial.polynomial.polyval(parameter, coefficients))
        for parameter in parameters
    )
    if not all(math.isfinite(value) for value in values):
        return math.inf
    return max(abs(value) for value in values)


def _project_road(
    road: ET.Element,
    target_x: float,
    target_y: float,
    endpoint_tolerance: float = _ENDPOINT_TOLERANCE,
    geometry_indices: tuple[int, ...] | None = None,
) -> _Projection | None:
    plan_view = road.find("planView")
    if plan_view is None:
        return None

    best: _Projection | None = None
    selected_geometry_indices = (
        None if geometry_indices is None else frozenset(geometry_indices)
    )
    for geometry_index, geometry in enumerate(plan_view.findall("geometry")):
        if (
            selected_geometry_indices is not None
            and geometry_index not in selected_geometry_indices
        ):
            continue
        if geometry.find("line") is not None:
            projection = _project_line(
                geometry,
                target_x,
                target_y,
                endpoint_tolerance=endpoint_tolerance,
            )
        else:
            projection = _project_param_poly3(
                geometry,
                target_x,
                target_y,
                endpoint_tolerance=endpoint_tolerance,
            )
        if projection is not None and (
            best is None or projection.distance < best.distance
        ):
            best = projection
    return best


def _build_road_connections(
    root: ET.Element,
    roads: tuple[ET.Element, ...],
) -> dict[str, frozenset[_RoadConnection]]:
    connections: dict[str, set[_RoadConnection]] = {
        road.get("id", ""): set() for road in roads
    }
    junction_contacts: dict[tuple[str, str], Literal["start", "end"]] = {}

    def connect(
        first: str | None,
        second: str | None,
        first_contact: Literal["start", "end"] | None,
        second_contact: Literal["start", "end"] | None,
    ) -> None:
        if not first or not second:
            return
        connections.setdefault(first, set()).add(
            _RoadConnection(second, first_contact, second_contact)
        )
        connections.setdefault(second, set()).add(
            _RoadConnection(first, second_contact, first_contact)
        )

    for road in roads:
        road_id = road.get("id", "")
        link = road.find("link")
        if link is None:
            continue
        for relation_name in ("predecessor", "successor"):
            relation = link.find(relation_name)
            if relation is None:
                continue
            road_contact: Literal["start", "end"] = (
                "start" if relation_name == "predecessor" else "end"
            )
            if relation.get("elementType") == "road":
                connect(
                    road_id,
                    relation.get("elementId"),
                    road_contact,
                    _contact_point(relation.get("contactPoint")),
                )
            elif relation.get("elementType") == "junction":
                junction_id = relation.get("elementId")
                if junction_id:
                    junction_contacts[(road_id, junction_id)] = road_contact

    for junction in root.findall("junction"):
        junction_id = junction.get("id", "")
        for connection in junction.findall("connection"):
            incoming_road = connection.get("incomingRoad")
            connect(
                incoming_road,
                connection.get("connectingRoad"),
                junction_contacts.get((incoming_road or "", junction_id)),
                _contact_point(connection.get("contactPoint")),
            )

    return {
        road_id: frozenset(neighbors)
        for road_id, neighbors in connections.items()
    }


def _trajectory_motion_headings(
    queries: tuple[tuple[float, float, float | None], ...],
) -> tuple[float | None, ...]:
    result: list[float | None] = []
    for index, query in enumerate(queries):
        previous = index - 1
        while previous >= 0 and math.hypot(
            queries[previous][0] - query[0],
            queries[previous][1] - query[1],
        ) <= _BOUNDARY_TOLERANCE:
            previous -= 1
        following = index + 1
        while following < len(queries) and math.hypot(
            queries[following][0] - query[0],
            queries[following][1] - query[1],
        ) <= _BOUNDARY_TOLERANCE:
            following += 1

        if previous >= 0 and following < len(queries):
            start_x, start_y = queries[previous][:2]
            end_x, end_y = queries[following][:2]
        elif previous >= 0:
            start_x, start_y = queries[previous][:2]
            end_x, end_y = query[:2]
        elif following < len(queries):
            start_x, start_y = query[:2]
            end_x, end_y = queries[following][:2]
        else:
            result.append(None)
            continue
        result.append(math.atan2(end_y - start_y, end_x - start_x) % math.tau)
    return tuple(result)


def _terminal_direction_anchor(
    candidate_layers: tuple[dict[str, MapLocation], ...],
    feasible_layers: list[set[str]],
    queries: tuple[tuple[float, float, float | None], ...],
    motion_headings: tuple[float | None, ...],
) -> str | None:
    if not feasible_layers or len(feasible_layers[-1]) < 2:
        return None

    terminal_roads = feasible_layers[-1]
    common_start = len(feasible_layers) - 1
    while (
        common_start > 0
        and terminal_roads.issubset(feasible_layers[common_start - 1])
    ):
        common_start -= 1

    road_scores: list[tuple[float, str]] = []
    for road_id in terminal_roads:
        point_scores = []
        for index in range(common_start, len(candidate_layers)):
            location = candidate_layers[index][road_id]
            lane_heading = location.reference_heading
            if location.lane_id > 0:
                lane_heading += math.pi
            direction_errors = []
            query_heading = queries[index][2]
            if query_heading is not None:
                direction_errors.append(abs(_wrap_pi(query_heading - lane_heading)))
            motion_heading = motion_headings[index]
            if motion_heading is not None:
                direction_errors.append(abs(_wrap_pi(motion_heading - lane_heading)))
            if direction_errors:
                point_scores.append(sum(direction_errors) / len(direction_errors))
        if point_scores:
            road_scores.append((median(point_scores), road_id))

    if len(road_scores) < 2:
        return None
    road_scores.sort(key=lambda item: (item[0], _road_id_sort_key(item[1])))
    best_score, best_road = road_scores[0]
    second_score = road_scores[1][0]
    if (
        best_score <= _MAX_DIRECTION_SCORE
        and second_score - best_score >= _MIN_DIRECTION_ADVANTAGE
    ):
        return best_road
    return None


def _feasible_candidate_layers(
    candidate_layers: tuple[dict[str, MapLocation], ...],
    left_location: MapLocation | None,
    right_location: MapLocation | None,
    transition_limits: tuple[float | None, ...],
    connections: dict[str, frozenset[_RoadConnection]],
    road_lengths: dict[str, float],
) -> tuple[set[str], ...]:
    forward: list[dict[str, MapLocation]] = []
    previous = (
        {left_location.road_id: left_location}
        if left_location is not None
        else None
    )
    for index, candidates in enumerate(candidate_layers):
        reachable = dict(candidates)
        if previous is not None:
            reachable = {
                road_id: location
                for road_id, location in candidates.items()
                if any(
                    _locations_connected(
                        previous_location,
                        location,
                        transition_limits[index],
                        connections,
                        road_lengths,
                    )
                    for previous_location in previous.values()
                )
            }
        forward.append(reachable)
        previous = reachable

    backward: list[dict[str, MapLocation]] = [{} for _ in candidate_layers]
    following = (
        {right_location.road_id: right_location}
        if right_location is not None
        else None
    )
    for index in range(len(candidate_layers) - 1, -1, -1):
        candidates = candidate_layers[index]
        reachable = dict(candidates)
        if following is not None:
            reachable = {
                road_id: location
                for road_id, location in candidates.items()
                if any(
                    _locations_connected(
                        location,
                        following_location,
                        transition_limits[index + 1],
                        connections,
                        road_lengths,
                    )
                    for following_location in following.values()
                )
            }
        backward[index] = reachable
        following = reachable

    return tuple(
        set(forward_roads) & set(backward_roads)
        for forward_roads, backward_roads in zip(forward, backward)
    )


def _locations_connected(
    first: MapLocation,
    second: MapLocation,
    transition_limit: float | None,
    connections: dict[str, frozenset[_RoadConnection]],
    road_lengths: dict[str, float],
) -> bool:
    if transition_limit is None:
        return False
    allowed_distance = (
        transition_limit * _MAX_TRAJECTORY_DETOUR_RATIO
        + _ENDPOINT_TOLERANCE
    )
    if first.road_id == second.road_id:
        return abs(first.s - second.s) <= allowed_distance

    for connection in connections.get(first.road_id, ()):
        if connection.target_road_id != second.road_id:
            continue
        first_distance = _distance_to_road_contact(
            first,
            connection.source_contact,
            road_lengths,
        )
        second_distance = _distance_to_road_contact(
            second,
            connection.target_contact,
            road_lengths,
        )
        if first_distance + second_distance <= allowed_distance:
            return True
    return False


def _distance_to_road_contact(
    location: MapLocation,
    contact: Literal["start", "end"] | None,
    road_lengths: dict[str, float],
) -> float:
    road_length = road_lengths.get(location.road_id, math.nan)
    if contact is None or not math.isfinite(road_length):
        return math.inf
    if contact == "start":
        return max(0.0, location.s)
    return max(0.0, road_length - location.s)


def _contact_point(value: str | None) -> Literal["start", "end"] | None:
    if value == "start":
        return "start"
    if value == "end":
        return "end"
    return None


def _best_candidates_by_road(
    candidates: tuple[MapLocation, ...],
) -> dict[str, MapLocation]:
    result: dict[str, MapLocation] = {}
    for candidate in candidates:
        result.setdefault(candidate.road_id, candidate)
    return result


def _project_line(
    geometry: ET.Element,
    target_x: float,
    target_y: float,
    endpoint_tolerance: float = _ENDPOINT_TOLERANCE,
) -> _Projection | None:
    start_x = _attr_float(geometry, "x")
    start_y = _attr_float(geometry, "y")
    heading = _attr_float(geometry, "hdg")
    length = _attr_float(geometry, "length")
    if length <= 0:
        return None

    cos_heading = math.cos(heading)
    sin_heading = math.sin(heading)
    delta_x = target_x - start_x
    delta_y = target_y - start_y
    longitudinal = delta_x * cos_heading + delta_y * sin_heading
    if longitudinal < -endpoint_tolerance or longitudinal > length + endpoint_tolerance:
        return None

    local_s = min(max(longitudinal, 0.0), length)
    closest_x = start_x + local_s * cos_heading
    closest_y = start_y + local_s * sin_heading
    offset_x = target_x - closest_x
    offset_y = target_y - closest_y
    lateral = -sin_heading * offset_x + cos_heading * offset_y

    return _Projection(
        s=_attr_float(geometry, "s") + local_s,
        t=lateral,
        heading=heading % math.tau,
        distance=math.hypot(offset_x, offset_y),
    )


def _project_param_poly3(
    geometry: ET.Element,
    target_x: float,
    target_y: float,
    endpoint_tolerance: float = _ENDPOINT_TOLERANCE,
) -> _Projection | None:
    curve = geometry.find("paramPoly3")
    if curve is None:
        return None

    start_x = _attr_float(geometry, "x")
    start_y = _attr_float(geometry, "y")
    base_heading = _attr_float(geometry, "hdg")
    length = _attr_float(geometry, "length")
    if length <= 0:
        return None

    cos_heading = math.cos(base_heading)
    sin_heading = math.sin(base_heading)
    delta_x = target_x - start_x
    delta_y = target_y - start_y
    target_u = delta_x * cos_heading + delta_y * sin_heading
    target_v = -delta_x * sin_heading + delta_y * cos_heading

    u_coefficients = np.array(
        [_attr_float(curve, name, 0.0) for name in ("aU", "bU", "cU", "dU")]
    )
    v_coefficients = np.array(
        [_attr_float(curve, name, 0.0) for name in ("aV", "bV", "cV", "dV")]
    )
    du_coefficients = np.array(
        [u_coefficients[1], 2 * u_coefficients[2], 3 * u_coefficients[3]]
    )
    dv_coefficients = np.array(
        [v_coefficients[1], 2 * v_coefficients[2], 3 * v_coefficients[3]]
    )

    p_range = curve.get("pRange", "normalized")
    parameter_end = 1.0 if p_range == "normalized" else length
    target_u_coefficients = u_coefficients.copy()
    target_v_coefficients = v_coefficients.copy()
    target_u_coefficients[0] -= target_u
    target_v_coefficients[0] -= target_v
    stationarity = np.polynomial.polynomial.polyadd(
        np.polynomial.polynomial.polymul(
            target_u_coefficients, du_coefficients
        ),
        np.polynomial.polynomial.polymul(
            target_v_coefficients, dv_coefficients
        ),
    )

    candidates = {0.0, parameter_end}
    if not np.all(np.abs(stationarity) < 1e-12):
        for root in np.polynomial.polynomial.polyroots(stationarity):
            if abs(root.imag) <= 1e-8 and -1e-10 <= root.real <= parameter_end + 1e-10:
                candidates.add(min(max(float(root.real), 0.0), parameter_end))

    best_parameter: float | None = None
    best_values: tuple[float, float, float, float, float] | None = None
    best_distance = math.inf
    for parameter in candidates:
        u = float(np.polynomial.polynomial.polyval(parameter, u_coefficients))
        v = float(np.polynomial.polynomial.polyval(parameter, v_coefficients))
        du = float(np.polynomial.polynomial.polyval(parameter, du_coefficients))
        dv = float(np.polynomial.polynomial.polyval(parameter, dv_coefficients))
        tangent_length = math.hypot(du, dv)
        if tangent_length <= 1e-12:
            continue

        offset_u = target_u - u
        offset_v = target_v - v
        longitudinal = (offset_u * du + offset_v * dv) / tangent_length
        if parameter <= 1e-10 and longitudinal < -endpoint_tolerance:
            continue
        if parameter >= parameter_end - 1e-10 and longitudinal > endpoint_tolerance:
            continue

        distance = math.hypot(offset_u, offset_v)
        if distance < best_distance:
            best_parameter = parameter
            best_values = (u, v, du, dv, tangent_length)
            best_distance = distance

    if best_parameter is None or best_values is None:
        return None

    u, v, du, dv, tangent_length = best_values
    offset_u = target_u - u
    offset_v = target_v - v
    lateral = (-dv * offset_u + du * offset_v) / tangent_length
    if p_range == "arcLength":
        local_s = best_parameter
    else:
        local_s, _ = quad(
            lambda parameter: math.hypot(
                float(np.polynomial.polynomial.polyval(parameter, du_coefficients)),
                float(np.polynomial.polynomial.polyval(parameter, dv_coefficients)),
            ),
            0.0,
            best_parameter,
        )
    local_s = min(max(local_s, 0.0), length)

    return _Projection(
        s=_attr_float(geometry, "s") + local_s,
        t=lateral,
        heading=(base_heading + math.atan2(dv, du)) % math.tau,
        distance=best_distance,
    )


def _lane_matches(
    road: ET.Element,
    road_s: float,
    lateral: float,
    *,
    road_edge_margin: float = 0.0,
) -> list[tuple[int, float]]:
    lanes = road.find("lanes")
    if lanes is None:
        return []

    sections = sorted(
        lanes.findall("laneSection"), key=lambda section: _attr_float(section, "s")
    )
    if not sections:
        return []

    section = sections[0]
    for candidate in sections:
        if _attr_float(candidate, "s") <= road_s + _BOUNDARY_TOLERANCE:
            section = candidate
        else:
            break

    section_s = _attr_float(section, "s")
    section_position = max(0.0, road_s - section_s)
    lane_offset = _piecewise_cubic_value(
        lanes.findall("laneOffset"), road_s, "s", 0.0
    )

    matches: list[tuple[int, float]] = []
    lane_intervals: list[tuple[int, float, float, float]] = []
    for side_name, direction in (("left", 1.0), ("right", -1.0)):
        side = section.find(side_name)
        if side is None:
            continue

        cumulative_width = 0.0
        side_lanes = sorted(
            side.findall("lane"), key=lambda lane: abs(int(lane.get("id", "0")))
        )
        for lane in side_lanes:
            lane_id = int(lane.get("id", "0"))
            if lane_id == 0:
                continue
            width = _piecewise_cubic_value(
                lane.findall("width"), section_position, "sOffset", math.nan
            )
            if not math.isfinite(width) or width <= 0:
                continue

            inner = lane_offset + direction * cumulative_width
            outer = lane_offset + direction * (cumulative_width + width)
            low, high = sorted((inner, outer))
            lane_intervals.append((lane_id, low, high, (inner + outer) / 2))
            if low - _BOUNDARY_TOLERANCE <= lateral <= high + _BOUNDARY_TOLERANCE:
                matches.append((lane_id, (inner + outer) / 2))
            cumulative_width += width

    if matches or road_edge_margin <= 0 or not lane_intervals:
        return matches

    road_low = min(interval[1] for interval in lane_intervals)
    road_high = max(interval[2] for interval in lane_intervals)
    if road_low - road_edge_margin <= lateral < road_low:
        lane_id, _, _, lane_center = min(
            lane_intervals,
            key=lambda interval: (interval[1], abs(interval[0]), interval[0]),
        )
        return [(lane_id, lane_center)]
    if road_high < lateral <= road_high + road_edge_margin:
        lane_id, _, _, lane_center = max(
            lane_intervals,
            key=lambda interval: (interval[2], -abs(interval[0]), -interval[0]),
        )
        return [(lane_id, lane_center)]
    return matches


def _lane_geometry_at(
    road: ET.Element,
    road_s: float,
    lane_id: int,
) -> tuple[str, float, float] | None:
    lanes = road.find("lanes")
    if lanes is None or lane_id == 0:
        return None
    sections = sorted(
        lanes.findall("laneSection"), key=lambda section: _attr_float(section, "s")
    )
    if not sections:
        return None
    section = sections[0]
    for candidate in sections:
        if _attr_float(candidate, "s") <= road_s + _BOUNDARY_TOLERANCE:
            section = candidate
        else:
            break

    section_s = _attr_float(section, "s")
    section_position = max(0.0, road_s - section_s)
    lane_offset = _piecewise_cubic_value(
        lanes.findall("laneOffset"), road_s, "s", 0.0
    )
    side = section.find("left" if lane_id > 0 else "right")
    if side is None:
        return None

    direction = 1.0 if lane_id > 0 else -1.0
    cumulative_width = 0.0
    for lane in sorted(
        side.findall("lane"), key=lambda item: abs(int(item.get("id", "0")))
    ):
        candidate_id = int(lane.get("id", "0"))
        if candidate_id == 0:
            continue
        width = _piecewise_cubic_value(
            lane.findall("width"), section_position, "sOffset", math.nan
        )
        if not math.isfinite(width) or width <= 0:
            continue
        inner_t = lane_offset + direction * cumulative_width
        outer_t = lane_offset + direction * (cumulative_width + width)
        if candidate_id == lane_id:
            return lane.get("type", "none"), inner_t, outer_t
        cumulative_width += width
    return None


def _piecewise_cubic_value(
    records: list[ET.Element],
    position: float,
    start_attribute: str,
    default: float,
) -> float:
    active: ET.Element | None = None
    active_start = 0.0
    for record in sorted(records, key=lambda item: _attr_float(item, start_attribute)):
        record_start = _attr_float(record, start_attribute)
        if record_start <= position + _BOUNDARY_TOLERANCE:
            active = record
            active_start = record_start
        else:
            break
    if active is None:
        return default

    delta = max(0.0, position - active_start)
    a = _attr_float(active, "a", 0.0)
    b = _attr_float(active, "b", 0.0)
    c = _attr_float(active, "c", 0.0)
    d = _attr_float(active, "d", 0.0)
    return a + b * delta + c * delta**2 + d * delta**3


def _road_elevation_at(road: ET.Element, road_s: float) -> float:
    return _piecewise_cubic_value(
        road.findall("elevationProfile/elevation"),
        road_s,
        "s",
        0.0,
    )


def _road_elevation_grade_at(road: ET.Element, road_s: float) -> float:
    records = road.findall("elevationProfile/elevation")
    active: ET.Element | None = None
    active_s = 0.0
    for record in sorted(records, key=lambda item: _attr_float(item, "s")):
        record_s = _attr_float(record, "s")
        if record_s <= road_s + _BOUNDARY_TOLERANCE:
            active = record
            active_s = record_s
        else:
            break
    if active is None:
        return 0.0
    delta = max(0.0, road_s - active_s)
    b = _attr_float(active, "b", 0.0)
    c = _attr_float(active, "c", 0.0)
    d = _attr_float(active, "d", 0.0)
    return b + 2 * c * delta + 3 * d * delta**2


def _location_sort_key(location: MapLocation) -> tuple[object, ...]:
    height_score = (
        abs(location.height_error) if location.height_error is not None else 0.0
    )
    heading_score = (
        abs(location.heading_error) if location.heading_error is not None else 0.0
    )
    road_group, road_value = _road_id_sort_key(location.road_id)
    return (
        height_score,
        heading_score,
        location.distance_to_lane_center,
        road_group,
        road_value,
        abs(location.lane_id),
        location.lane_id,
    )


def _ranked_location_sort_key(location: MapLocation) -> tuple[object, ...]:
    height_score = (
        abs(location.height_error) if location.height_error is not None else 0.0
    )
    if location.heading_error is None:
        lane_heading_score = 0.0
    else:
        lane_heading_error = location.heading_error
        if location.lane_id > 0:
            lane_heading_error = _wrap_pi(lane_heading_error - math.pi)
        lane_heading_score = abs(lane_heading_error)
    road_group, road_value = _road_id_sort_key(location.road_id)
    return (
        height_score,
        lane_heading_score,
        location.distance_to_lane_center,
        road_group,
        road_value,
        abs(location.lane_id),
        location.lane_id,
    )


def _road_id_sort_key(road_id: str) -> tuple[int, int | str]:
    try:
        return 0, int(road_id)
    except ValueError:
        return 1, road_id


def _wrap_pi(angle: float) -> float:
    return (angle + math.pi) % math.tau - math.pi


def _waypoint_query(
    position: Mapping[str, object],
    yaw_degrees: float | None,
) -> tuple[float, float, float | None]:
    try:
        map_x = position["x"]
        map_y = position["z"]
    except (KeyError, TypeError) as exc:
        raise ValueError("position must contain numeric 'x' and 'z' values") from exc

    heading = None
    if yaw_degrees is not None:
        yaw_degrees = _finite_float(yaw_degrees, "yaw_degrees")
        heading = (math.pi / 2 - math.radians(yaw_degrees)) % math.tau
    return _finite_float(map_x, "map_x"), _finite_float(map_y, "map_y"), heading


def _waypoint_height(position: Mapping[str, object]) -> float | None:
    value = position.get("y")
    return None if value is None else _finite_float(value, "map_z")


def _finite_float(value: object, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _attr_float(element: ET.Element, name: str, default: float | None = None) -> float:
    value = element.get(name)
    if value is None:
        if default is None:
            raise ValueError(f"Missing required attribute {name!r} on <{element.tag}>.")
        return default
    return float(value)
