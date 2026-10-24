"""Rerun the manually rejected single-agent width cases with DeepSeek V4 Pro."""
import csv
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from run_real_batches import DATABASE, rebuild, write_json


BASE = Path(__file__).resolve().parent
GROUP = "single_width_retry_v4pro"
OUTPUT = BASE / GROUP
SOURCE = BASE / "single_width_200" / "inputs.csv"
SOURCE_RUN_ID = "single_width_200_real_20261019"
MODEL = "deepseek-v4-pro"


def prepare_manifest():
    connection = sqlite3.connect(DATABASE.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rejected = {
            row[0]
            for row in connection.execute(
                "SELECT case_id FROM case_reviews WHERE run_id=? AND decision='fail'",
                (SOURCE_RUN_ID,),
            )
        }
    finally:
        connection.close()

    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    rows = [row for row in source_rows if row["case_id"] in rejected]
    if len(rejected) != 56 or len(rows) != 56:
        raise RuntimeError(
            f"Expected 56 rejected cases, found {len(rejected)} reviews and {len(rows)} inputs"
        )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = OUTPUT / "inputs.csv"
    with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(source_rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def run_case(item):
    folder = OUTPUT / "cases" / item["case_id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "input.json").write_text(
        json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    env = os.environ.copy()
    env.update(DEEPSEEK_MODEL=MODEL, PYTHONIOENCODING="utf-8", MPLBACKEND="Agg")
    with (folder / "worker.log").open("a", encoding="utf-8") as log:
        process = subprocess.run(
            [sys.executable, str(BASE / "run_real_batches.py"), "--worker", GROUP,
             "--case", item["case_id"]],
            cwd=str(BASE.parent / "完全版本" / "完全版本" / "openDriveMapEditor"),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=1200,
        )
    if not (folder / "execution_result.json").exists():
        write_json(
            folder / "execution_result.json",
            {"case_id": item["case_id"], "group": GROUP, "model": MODEL,
             "agent_flow": "single", "status": "error",
             "error": f"Worker exited without result: {process.returncode}"},
        )
    return item["case_id"], process.returncode


def main():
    rows = prepare_manifest()
    write_json(
        OUTPUT / "run_config.json",
        {"group": GROUP, "model": MODEL, "agent_flow": "single",
         "road_context": "aligned-real", "source_run_id": SOURCE_RUN_ID,
         "count": len(rows), "execution": "model plan then isolated handler replay"},
    )
    rebuild(GROUP, rows)
    todo = [
        row for row in rows
        if not (OUTPUT / "cases" / row["case_id"] / "execution_result.json").exists()
    ]
    print(f"parallel {len(todo)} workers=3", flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run_case, row) for row in todo]
        for future in as_completed(futures):
            print(future.result(), flush=True)
            print(json.dumps(rebuild(GROUP, rows), ensure_ascii=False), flush=True)
    print(json.dumps(rebuild(GROUP, rows), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
