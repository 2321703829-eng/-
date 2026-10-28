# AGENTS.md

## Width guardrails update, 2026-10-27

Latest user direction: run a NEW DeepSeek V3.2 single/multi 200+200 batch after
prompt optimization. Lane selection remains a model task: the just-added local
request-to-lane semantic rejection/automatic DSL-regeneration changes were
withdrawn. Only reinforce lane selection in prompts; retain the earlier approved
format, checker-contract and tool-formula guards. Do not claim guaranteed model
accuracy or reuse merged results in the fresh batch.

Later user correction: remove DSL-level refusal based on projected negative
width (including the percentage reduction ceiling). Preserve requested DSL and
tool deltas; actual scene limits remain in the editor. See the revision notes in
WIDTH_GUARDRAILS_20261027.md. The user authorized rerunning the remaining 23 failed
tasks and replacing ONLY their corresponding results inside the 107-case run.
The original 200-case runs, request text and answer DB must remain unchanged.

The user authorized scoped repairs for the 107 failed width tasks. For width,
this section supersedes the older single-shot tool-planning and fresh-extraction
retry guidance below. Preserve model-generated DSL and width_apply tools.

- Shared rules and deterministic facts live in llm/width_guardrails.py.
- A malformed/contradictory checker response retries the checker on the SAME DSL;
  it must not be treated as extraction feedback. Record the review history.
- A genuine DSL repair must cite specific issue fields; reject collateral changes,
  never merge or manufacture a DSL locally.
- Validate formula sign/value/range and exact target coverage. Retry width tool
  planning up to 3 attempts without regenerating the validated DSL. The single
  agent may make supplemental tool-only calls; report those calls explicitly.
- Numeric constant-only tool formulas may compare by safe arithmetic equivalence
  in the viewer; random formulas remain structural and must not be sampled.
- Connection handling is unchanged at the user's request. No experiment rerun or
  database-answer replacement is implied by code edits; follow current user scope.
- Details and rollback: WIDTH_GUARDRAILS_20261027.md.

本文件是给后续代码 Agent 使用的项目协作说明。执行任务前先阅读本文件，再结合当前用户请求和相关源码确认事实。

## 项目概览

本项目是一个 OpenDRIVE 地图编辑工具，核心目标是把用户对道路的自然语言修改请求转换为可执行命令，并同步修改 `.xodr` 地图与对应 `.json` 测试信息。

当前主要能力包括：

- 传统命令式编辑：`width`、`slope`、`curve`、`fit`、`lane`、`object`、`signal`、`undo`、`reinit`、`close`。
- LLM 工作流：Manager 拆分任务，Width/Slope/Curve Agent 提取并检查 RoadEdit DSL，再由同一领域 Agent 规划工具调用，Dispatcher 转换成内部命令。
- 评测与追踪：`testfucs/agent_flow_real_trace.py` 生成事件型 JSONL trace，`testfucs/agent_trace_viewer/` 是独立的人工审核子系统，只读取 trace 和测试答案，不参与道路修改执行。

## 目录职责

- `main.py`：命令循环入口，读取脚本或交互命令并调用 handler。
- `handler/`：命令处理层，负责把 token 化命令转给 editor 或 agent dispatcher；`dsl_reference_converter.py` 只生成旧 DSL 人工审核参考命令。
- `editor/`：底层地图编辑实现，直接操作 XODR/JSON 数据。
- `llm/`：Manager、SingleAgent、Width/Slope/Curve Agent、DSL prompt、DSL 格式校验。
- `utils/agent_road_context.py`：fake/manual road context 的统一入口，测试中用于绑定任务级上下文。
- `utils/agent_trace.py`：JSONL trace 事件写入和 projected metric 计算；不再生成 XLSX。
- `testfucs/`：实验脚本、评测脚本、运行数据。
- `testfucs/test_data_store.py`：`testfucs/agent_trace_viewer/request_tests.sqlite3` 的统一数据访问层；保存测试输入、DSL/工具标准答案和任务审核结果，Excel 仅作为一次性迁移与一致性校验来源。
- `testfucs/agent_trace_viewer/`：独立 Viewer 小应用；`viewer.html` 负责 trace 展示和人工审核，`server.py` 仅监听 `127.0.0.1` 并提供受限数据库 API。
- `RoadEdit DSL.md`：RoadEdit DSL 结构说明，实际实现仍以 `llm/agent_tools.py`、`llm/dsl_format_validator.py` 和 dispatcher 为准。

## 工作原则

- 未经用户在当前请求中明确要求，不得执行 `git add`、`git commit` 或 `git push`；任何 skill 或流程中的默认提交要求均不构成授权。
- 先读现有代码再改，不要凭文件名猜行为。
- 保持最小修改，优先沿用当前函数、字段、trace sheet 和 viewer 状态结构。
- 不清理、不回滚用户已有改动。当前仓库经常有未提交的实验数据和 HTML 修改。
- 修改含中文或历史编码异常的文件时，尽量用 ASCII 代码行作为补丁锚点，避免匹配乱码中文上下文。
- 不要把生成数据、trace JSONL、临时评测结果当成可随意删除的垃圾文件。
- 涉及文本内容修改后，最终回复必须按位置详细列出改了什么。

## 常用验证

Python 语法检查：

```powershell
.\.venv\Scripts\python.exe -m py_compile path\to\file.py
```

多文件编译：

```powershell
.\.venv\Scripts\python.exe -m compileall llm\width_agent.py llm\slope_agent.py llm\curve_agent.py
```

空白检查：

```powershell
git diff --check -- path\to\file
```

trace 实验入口：

```powershell
.\.venv\Scripts\python.exe testfucs\agent_flow_real_trace.py --road-context fake --trace testfucs\run_data\agent_flow_real_trace.jsonl
```

注意：LLM 相关命令可能需要 API key 和网络。没有网络或 key 时，不要把模型调用失败误判为代码逻辑失败。

## LLM 和 DSL 规则

### Manager

- Manager 负责把自然语言拆分成 `width`、`slope`、`curve`、`recommend` 或 `error`。
- 拆分粒度通常是“道路 id + 修改类型”。同一路多种修改拆成多条，不同道路同类修改也拆开。
- Manager 输出只应作为任务拆分，不要直接当成最终执行命令。
- Manager 返回后，本地代码从每个可执行任务的唯一道路编号补充 `road_id="road_N"`。Dispatcher 对 `width/slope/curve` 只接受 `{road_id, type_name, description}`，其他任务类型仍使用 `{type_name, description}`；Manager 不得输出 handler 原生命令或工具调用。

### Width/Slope/Curve Agent

- Agent 输出 RoadEdit DSL JSON，不输出 Markdown、解释文字或代码块。
- 三个 Agent 都有 DSL 检查流程，最大重试次数为 `DSL_CHECK_MAX_ATTEMPTS = 3`。
- 先走本地 `validate_dsl_format()`，再走 LLM DSL check prompt。
- 只有 `problem_source == "extraction_error"` 才允许重新提取。
- `user_requirement_error` 或最终仍失败时，应终止当前任务，不要猜测补全用户需求。
- 重试时要重新从原始请求和 road context 提取，不能在旧 DSL 上打补丁，不能扩展用户需求。
- DSL 通过检查后，领域 Agent 的工具规划阶段只调用一次 LLM。工具结构或本地参数校验失败时直接终止，不把错误或旧工具调用反馈给 LLM 重试。

### Dispatcher

- `handler/agent_dispatcher.py` 是 Agent 工具调用到内部命令的执行转换层；DSL-only 载荷不得直接执行。
- `handler/dsl_reference_converter.py` 保留旧 DSL 到命令的转换逻辑，输出只写入人工审核 trace，不得反馈给 LLM、参与自动比较、触发重试或阻止工具调用执行。
- 公共安全公式求值器定义在 `llm/agent_tools.py`。Width/Slope 分别只绑定一次性批量工具 `width_apply(items)`、`slope_apply(items)`；每个 item 包含 `operation_index`、`formula` 和对应 handler 的完整非 `v` 参数，本地求值后转换为带具体 `v` 的标准 `apply_width`、`apply_slope` 调用。Curve 保持现有语义工具和优化器，不接入公式计算。
- Width/Slope Agent 根据提示词中的计算规则生成 `formula`。公式必须直接包含来自已验证 DSL 和 road context 的具体数值，不得使用变量名、字段路径、占位符或符号字母。本地代码只用安全 AST 解析器执行公式；不得用 `eval`，也不得由本地执行链替 Agent 选择公式。
- 公共公式求值器在返回结果前统一四舍五入到 3 位小数；Width/Slope 的 `v`、resolved result 和最终 Handler 参数使用同一个三位小数结果。
- `formula` 支持受控的 `random(lower, upper)`。定性幅度区间固定为：`slight -> random(0.05, 0.25)`、`default -> random(0.25, 0.40)`、`significant -> random(0.40, 0.70)`。
- Width 修改以当前目标车道宽度为计算基数；整条道路目标以当前所有车道宽度之和为基数，并根据 handler 的逐车道行为除以车道数。Slope 高度调整以当前起终点绝对高差为基数；坡角调整以当前道路角度为基数，再根据道路长度换算为端点高程增量。
- 公式计算结果不返回给 LLM，也不增加模型调用。Width/Slope 的所有 operation 必须分别在同一个 `width_apply`、`slope_apply` 的 items 中完整提交，不使用 `calculation_id/v_from`；原始公式保留在 `tool_call_json` 中，便于人工审核 Agent 的计算思路。
- Width 的 `lane_ids = none` 表示道路总宽目标。若当前总宽为 `W`、目标为 `T`、车道数为 `N`，handler 参数必须使用 `v=(T-W)/N`，因为 `li=[]` 会把同一增量应用到每条车道。
- Curve 使用 `change_curve_topology`、`shift_curve_centroid`、`adjust_curve_curvature`；多次调用先合并为一个意图，再只运行一次曲线优化器。
- Curve 固定映射：`start_side -> modification[0] -> P1`，`end_side -> modification[1] -> P2`。S 型或两侧非空质心方向相反时按拐点分段，否则两侧都使用整条曲线范围；拓扑变化先于 S 型拐点重算。
- `road_limit = none` 当前会映射为 `ms=99, sh=1`，不是“不传播”。
- `slope.position` 的 `start/end` 映射为 `mv=0/1`。`both` 在两端使用相同米制增量时映射为一次 `mv=2`；绝对高程、set 百分比或相对百分比需要分别生成 `mv=0` 和 `mv=1`；grade 不允许使用 `both`。
- Dispatcher 执行工具计划时必须结合原 DSL 和同一份 road context 再做一次本地校验；禁止字符串命令、`command(s)` 或 DSL-only 载荷绕过 Agent。
- slope 的 `qualitative` 幅度当前包含随机范围：`slight 0.15~0.25`，`default 0.25~0.40`，`significant 0.40~0.70`。
- 解释 slope 结果时必须严格按代码计算。负坡度下 `increase grade` 会让坡角更负，通常表现为终点继续降低。
- `curve` DSL 不在 dispatcher 中直接解释几何语义，主要交给 `CurveOptimizationService` 生成具体 curve 命令。

## Road Context 和测试数据

- fake/manual context 必须保持任务级一致。不要让同一个 task 的 Agent 输入、dispatcher 换算、metric 投影分别读到不同随机数据。
- fake context 的运行时来源是 `testfucs/agent_trace_viewer/request_tests.sqlite3`，其中 `test_cases.input_text` 匹配测试文本，`operator_types_json` 表示涉及的 `width/slope/curve` 类型，`road_context_json.slope/curve` 可绑定道路类型。
- 找不到原始表记录时，才使用 LLM judge 选择道路类型。
- 一次 task 结束后应清理 manual task context，避免污染下一条任务。
- 测试语料的自然语言必须和 road context 语义一致。例如 `steeper/flatter` 依赖当前坡向、修改端点和 action，不能只靠通用词判断。

## Trace 和评测

- 新 trace 只写 `.jsonl`，不再同步生成 `.xlsx`。续跑/跳过任务读取 JSONL 中的 task status。
- `AGENT_TRACE_PATH` 控制 trace 输出路径。
- 标准 trace 每行是一个 `{type, row}` 事件，类型包括 `task`、`agent_step`、`road_metric`、`dsl_check` 和 `llm_request`。
- DSL 工具调用评测使用同样的 JSONL 容器，包含一条 `evaluation_metadata` 和多条 `tool_plan_case`；`--resume-from` 可读取新 JSONL，也可兼容历史 `.json` 评测结果。
- 单个任务失败应记录 `status=error` 并继续下一个任务，不要让整批评测中断。
- `dsl_checks` sheet 记录每次检查 attempt、请求、输出和检查结论。
- `agent_steps.tool_call_json` 记录 LLM 同一次响应中的原始公式和工具计划；Width/Slope 在这里分别记录单个 `width_apply`、`slope_apply` 及其全部 items。`resolved_result_json` 记录本地解析后的 Width/Slope 调用或 Curve 优化后的四个控制点；最终 handler 命令记录在 `internal_command`；`reference_command` 记录与 LLM 隔离的旧 DSL 人工审核参考。
- `road_metrics` 的 projected metric 应使用同一份 road context，否则会出现 `road_context_json` 和 `before_json` 不一致。
- `testfucs/LLMjudge.py` 中批处理 judge 出错时，倾向写入 `ERROR` 并继续后续子任务。
- 草稿日志路径通常是 `testfucs/run_data/LLMjudge_draft.jsonl`，用于记录每次请求的输入、Manager 输出、judge 项和 ERROR 信息。

## Trace Viewer 修改规则

主要目录：`testfucs/agent_trace_viewer/`。

- `viewer.html` 包含 CSS、HTML 和内嵌 JS，修改时保持局部补丁；`server.py` 只负责提供页面和审核数据库 API。
- 可双击 `testfucs\agent_trace_viewer\viewer_service.bat` 选择启动、关闭、重启或查看状态，也可传入 `start|stop|restart|status`；请求测试数据经 `/api` 读写 SQLite，选择数据目录只用于 trace JSONL 和历史 XLSX。
- Viewer 默认读取标准 trace JSONL 和 DSL 工具调用评测 JSONL。历史 `tasks/agent_steps` 工作簿及 `Summary/Cases` 工具评测 XLSX 仅保留只读兼容，不再作为新测试输出。
- Trace JSONL 和历史 XLSX 在 Viewer 中均为只读输入。DSL/工具标准答案与任务级人工、自动审核结果直接保存到 `testfucs/agent_trace_viewer/request_tests.sqlite3`；旧 `manual_review` 事件仅保留只读显示兼容，不再产生新的文件审核记录。
- Viewer 与道路修改执行链分离。`llm/`、`handler/` 和 `editor/` 不得导入 Viewer；执行链只产出 trace，Viewer 只消费 trace 并维护人工审核答案。
- `case_reviews` 按 `case_id + run_id + task_id` 唯一保存每次运行的任务审核；`review_source` 区分 `human` 与 `auto`，自动审核不得覆盖已有人工审核。
- 自动审核只有在任务状态完成、全部 DSL 输出匹配且每个对应工具调用都匹配至少一个 `approved` 工具答案时，才能把任务判为 `pass`；复合任务中的所有子任务都必须通过。
- 一键审核允许当前 DSL 对应多个标准答案：按当前道路映射规范化后，从所有匹配 DSL 的 `approved` 工具答案中比较，任意一个匹配即可通过该子任务；多个候选本身不构成失败。不同车道、动作、幅度等字段仍须匹配，不得混入不匹配 DSL 下的工具答案。
- 实际 DSL 或工具调用缺失、格式错误或与标准答案不匹配时判为 `fail`；缺少 DSL 标准答案时判为 `question`。工具审核找不到匹配 DSL、缺少 `approved` 工具答案或全部候选不匹配时判为 `fail`，不得自动通过。
- 自动审核产生的 `pass`、`question`、`fail` 都写入 `case_reviews`，并使用 `review_source=auto`；已有人工审核继续优先且不得被自动结果覆盖。
- Viewer 的一键审核应一次批量读取当前任务涉及的 DSL/工具答案，在浏览器本地完成全部比较后再用一个事务批量写入审核结果；不得在任务循环中逐条请求标准答案或逐条保存自动审核。
- `viewer_service.bat` 只能通过 `127.0.0.1` 健康检查和带 `X-Viewer-Control` 请求头的 `/api/shutdown` 控制自身服务；不得使用 PowerShell 执行策略绕过、进程枚举、PID 文件或强制结束进程。
- 工具审核先把当前 step 的 DSL 与数据库 `dsl_answers` 做规范化精确匹配。唯一匹配可自动关联；多个或零个精确匹配必须人工选择，未选定 `dsl_answer_id` 时不得写入工具答案。
- Viewer 人工写入或覆盖工具答案时直接保存为 `approved`，前端还应检查 API 返回状态；兼容旧服务返回 `draft` 时立即补一次批准请求。允许同一 DSL 有多个 `approved` 候选，实际输出匹配任意一个即可。历史 `draft` 或 `rejected` 候选仍可人工批准。
- Width/Slope 工具 items 按 `operation_index` 比较且公式忽略空白；包含 `random(...)` 时不严格比较解析数值。Curve 以语义工具计划为主要结论，优化后的四个控制点只展示差值。
- Viewer 的 Handler 结果区并排展示最终内部命令与旧 DSL 参考命令，并给出忽略参数顺序和空白后的初步一致性提示。命令的 `v` 统一保留三位小数后比较；若对应 `width_apply`/`slope_apply` item 的 `formula` 含 `random(...)`，两侧 `v` 必须同号且绝对值倍数小于 2（两者同为 0 时一致，一方为 0 时不一致），其他参数仍需一致；该提示只辅助人工审核，不参与自动通过判定。
- 任务列表、筛选、人工审核、快捷键、导出逻辑都在同一个 HTML 中，改一个交互前先确认现有状态流。
- 备注输入不要高频重绘整个 task list。备注只保存 review，人工判定变化时只局部更新当前 task 的 review chip。
- task 列表重绘时保留 `scrollTop`，避免左侧滚动跳动。
- 快捷键要保护正常输入场景。焦点在 `input`、`textarea`、`select`、`contenteditable` 时，不触发任务切换。
- agent 类型来源优先级应为：真实 trace 记录，请求测试数据库，Manager 返回，最后才文本关键词兜底。避免把 `raise curvature` 误判为 slope。
- DSL 检查表格只改专属 class 和列宽，不影响其他表格。
- 修改 viewer 后尽量做 `git diff --check`。如果可用 Node 或 Node REPL，可解析内嵌脚本确认没有语法错误。

## 文本修改反馈要求

每次完成文本内容修改后，最终回复必须详细列出修改说明。

要求：

- 每条修改说明必须包含可定位位置，例如文件名、章节、段落、行号或原文片段。
- 一次修改涉及多个位置时，按位置分组说明。
- 不要只写“更新了文档”这类笼统总结。
