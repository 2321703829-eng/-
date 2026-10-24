"""Command-line entry point for the standalone scenario visualizer."""

from __future__ import annotations

import argparse
from pathlib import Path

from .scenario_visualizer import build_scenario_visualization


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a self-contained OpenDRIVE and NPC trajectory HTML viewer."
    )
    parser.add_argument("xodr", type=Path, help="OpenDRIVE .xodr map file")
    parser.add_argument("scenario", type=Path, help="LGSVL-VSE scene .json file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("scenario_visualization.html"),
        help="Output HTML path (default: scenario_visualization.html)",
    )
    args = parser.parse_args()

    summary = build_scenario_visualization(args.xodr, args.scenario, args.output)
    print(f"Generated: {summary.output_path}")
    print(
        f"Roads: {summary.road_count}, NPCs: {summary.npc_count}, "
        f"waypoints: {summary.waypoint_count}"
    )


if __name__ == "__main__":
    main()
