# 400-case real-road run

Model: DeepSeek `deepseek-chat`. Flow: normal (Manager + domain agents).
Source database: `../完全版本/完全版本/openDriveMapEditor/testfucs/agent_trace_viewer/request_tests.sqlite3` (read only).

## Separate batches

- `width_200/inputs.csv`: frozen width input list, 200 cases.
- `mixed_200/inputs.csv`: frozen mixed input list, 200 cases, including slope+curve cases.
- `<group>/<group>_trace.jsonl`: merged trace for the trace viewer; task IDs are database case IDs.
- `<group>/progress.json`: finished count and execution statuses, refreshed after each case.
- `<group>/results.json`: execution results for finished cases.
- `<group>/cases/<case_id>/`: original input, per-case trace, worker log, execution result, before/after XODR and JSON.

## Execution and review

Each case uses its aligned original scene, maps the Road 21 placeholder to the actual road, and obtains a new model DSL and tool plan with real road context. After planning, original files are reloaded in an isolated worker and the generated commands are replayed through the existing handlers, matching the approved single-case demonstration. Source code and database answers are not changed.

`done` means the execution finished; it is not an accuracy judgement. Existing width limits and propagation behavior remain active. Missing original maps are recorded as errors rather than replaced. Failed cases may lack after files.

Trace `road_metric` events are the project's projected metrics from planning. Actual widths, changed road IDs, handler commands and source-file hash checks are in each `execution_result.json`. Use the saved after XODR for final scene review. The merged trace does not rewrite database standard answers or write automatic approval results.

## Runner

`run_real_batches.py --group width_200` and `--group mixed_200` run independently. Each case is a separate subprocess with a 20-minute timeout. Re-running the same group skips cases that already have an execution result, including recorded failures. Stop a batch before deciding to retry failures.

Logs: `width_batch.log`, `mixed_batch.log` and corresponding `_error.log` files.

## Single-agent comparison started 2026-10-19

- `single_width_200/` and `single_mixed_200/` use the same frozen input CSVs as the original batches.
- Invoke `run_real_batches.py --group single_width_200` or `--group single_mixed_200`.
- Model: `deepseek-chat`; flow: `single`. One model response generates all DSL and tool plans, followed by local validation and execution.
- Each output group has its own merged `<group>_trace.jsonl`, `progress.json`, `results.json`, and `cases/` maps.
- Full-map road context is retained. A curve request fits only its target road before building all contexts; width-only and width+slope inputs do not trigger curve fitting.
- The original multi-agent results predate curve pre-fitting. Mixed cases involving curves need the updated multi-agent rerun before comparing the two flows under the same geometry preparation.
