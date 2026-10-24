# ResultAna

ResultAna 是面向 LGSVL/VSE 仿真数据的本地离线分析工具，提供 OpenDRIVE 地图定位、NPC 配置路径可视化、EGO 实际轨迹异常分析、人工复核和 Excel 导出。

地图定位功能最初从 `openDriveMapEditor` 中独立提取。当前项目不修改输入地图、场景或轨迹日志，但会写入本地分析历史、人工复核信息和导出文件。

> 使用结果前应区分“定位结果”“规则触发”和“人工确认”。页面上的“确定道路”可能来自候选排序；“跌出路面”使用速度阈值；碰撞和停滞来自日志文本。“未检出异常”不代表轨迹已经通过完整安全或交通法规验证。

## 目录

- [1. 功能与适用范围](#1-功能与适用范围)
- [2. 安装与快速启动](#2-安装与快速启动)
- [3. 输入文件和坐标约定](#3-输入文件和坐标约定)
- [4. NPC 场景可视化](#4-npc-场景可视化)
- [5. EGO 批量分析流程](#5-ego-批量分析流程)
- [6. 异常判定规则](#6-异常判定规则)
- [7. 定位结果与可信度](#7-定位结果与可信度)
- [8. Excel 导出与统计口径](#8-excel-导出与统计口径)
- [9. 历史记录与运行限制](#9-历史记录与运行限制)
- [10. Python 接口与自定义配置](#10-python-接口与自定义配置)
- [11. 主要代码与调用关系](#11-主要代码与调用关系)
- [12. 常见问题](#12-常见问题)
- [13. 测试与验证范围](#13-测试与验证范围)

## 1. 功能与适用范围

| 功能 | 输入 | 输出 | 适合解决的问题 |
|---|---|---|---|
| 单点地图定位 | XODR、位置、可选朝向和高度 | 道路、车道、`s/t`、高度和方向误差 | 某个轨迹点位于哪里 |
| 有序轨迹定位 | XODR、按顺序排列的点 | 每个点的候选、道路归属、定位状态 | 重叠道路和路口中的道路归属 |
| NPC 配置路径查看 | XODR、LGSVL/VSE 场景 JSON | 可交互二维地图、NPC 路线和点位信息 | 场景配置要求 NPC 如何行驶 |
| EGO 实际轨迹分析 | 运行日志、关联场景和地图 | 逐点分析、异常事件、运行统计 | 仿真实际运行中触发了哪些规则 |
| 碰撞复核 | 已分析的碰撞候选 | 人工结论和备注 | 区分待复核、确认和排除的碰撞 |
| 结果交付 | NPC 可视化或 EGO 批次 | 独立 HTML 或三表 Excel | 分享路径图、整理实验统计 |

NPC 页面读取场景配置中的 `agents[type == 2]`；EGO 页面分析日志中的 `waypoints`。两者没有进行同步回放、交互关系分析或计划与实际轨迹偏差对比。

当前不提供以下功能：

- 启动或控制仿真器、自动驾驶栈，生成或变异场景。
- 编辑 OpenDRIVE 地图、场景配置或原始运行日志。
- 读取任意格式的文本日志并自动转换成轨迹。
- 红绿灯、限速、路权、安全距离、完整逆行规则或舒适性评估。
- 车辆三维碰撞检测、碰撞对象识别、精确事故位置确认或责任判定。
- 时间轴动画、轨迹插值、控制指令重建或物理仿真。
- 多用户账户、权限管理、远程部署管理或分布式任务调度。

## 2. 安装与快速启动

### 2.1 准备 Python 环境

以下命令在项目根目录执行。建议使用 Python 3.11 创建独立环境；项目没有单独的安装包或前端构建步骤。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

当前固定依赖见 [requirements.txt](requirements.txt)：

| 依赖 | 版本 | 用途 |
|---|---|---|
| NumPy | `2.2.6` | 参数曲线多项式计算和求根 |
| SciPy | `1.15.3` | 参数曲线弧长积分 |
| openpyxl | `3.1.5` | Excel 工作簿生成 |

HTTP 服务、JSON、XML、SQLite 和任务调度使用 Python 标准库；前端使用 HTML、CSS、JavaScript 和 Canvas。

### 2.2 启动页面

双击根目录 [start_visualizer.bat](start_visualizer.bat)，或执行：

```powershell
.\start_visualizer.bat
```

启动脚本按顺序选择解释器：

1. 本项目 `.venv\Scripts\python.exe`。
2. 相邻项目 `..\openDriveMapEditor\.venv\Scripts\python.exe`。
3. 系统 `python`。

解释器选定后不会自动安装依赖，也不会因依赖缺失而继续尝试下一个解释器。

服务默认运行在 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)，并自动打开浏览器。也可以直接运行：

```powershell
.\.venv\Scripts\python.exe -B -m visualization.server
```

端口上已经有 ResultAna 时，启动器复用该服务并打开页面；端口被其他程序占用时会报错，不会自动换端口。

### 2.3 停止服务

关闭运行服务的终端窗口、在该窗口按 `Ctrl+C`，或双击 [stop_visualizer.bat](stop_visualizer.bat)。

停止脚本先请求 `/api/health`，确认返回的应用名称是 `ResultAna`，再终止监听 `127.0.0.1:8765` 的进程。该脚本仅针对默认端口；自定义端口的服务应在其启动终端停止。

关闭浏览器标签页不会停止后端服务。停止服务不会删除已完成的历史记录，但正在分析的任务不能断点恢复。

### 2.4 选择工作入口

| 目的 | 页面入口 | 需要准备的文件 |
|---|---|---|
| 查看场景中的 NPC 配置路线 | 场景可视化 | 场景 JSON，建议同时选择对应 XODR |
| 分析实际 EGO 运行 | EGO 合规分析 | JSONL/JSON 运行日志，以及本机目录中的关联场景和地图 |
| 分享独立 NPC 路径图 | 命令行 `python -m visualization` | XODR、场景 JSON、输出 HTML 路径 |
| 在脚本中定位或分析 | Python API | 见第 10 节 |

## 3. 输入文件和坐标约定

### 3.1 OpenDRIVE 地图

输入地图必须是可解析的 `.xodr` XML，包含道路和受支持的 `planView` 几何。

| 项目 | 当前支持情况 |
|---|---|
| 平面几何 | `line`、`paramPoly3` |
| `paramPoly3.pRange` | `normalized`、`arcLength` |
| 车道结构 | 多个 `laneSection`、分段 `width`、分段 `laneOffset` |
| 道路高程 | `elevationProfile/elevation` 分段三次多项式 |
| 道路拓扑 | road predecessor/successor、junction connection 和连接端点 |
| 未支持的平面几何 | `arc`、`spiral`、`poly3` 等，加载时抛出 `UnsupportedGeometryError` |
| 未实现的车道几何替代 | 以 `border` 替代 `width` 的车道宽度重建 |
| 未参与高程计算 | `superelevation`、`crossfall`、车道级 `height` |
| 未参与合规判断 | `roadMark` 实虚线语义、交通信号、完整车道连接与通行许可 |

道路没有高程记录时按零高程处理。车道缺少有效宽度记录时可能无法定位，而不是自动补出默认宽度。定位和绘图都不是完整 OpenDRIVE 标准校验器。

### 3.2 坐标、朝向和单位

| 输入值 | 解释 |
|---|---|
| `position.x` | OpenDRIVE 平面 `x` |
| `position.z` | OpenDRIVE 平面 `y` |
| `position.y` | 高度，对应 OpenDRIVE 高程 `z` |
| `angle.y` 或 `yaw_degrees` | Unity 风格偏航角，单位为度 |
| 位置、宽度、距离 | 米 |
| `speed` | 米/秒 |
| `s` | 沿道路参考线的纵向位置 |
| `t` | 相对参考线的横向偏移，参考线左侧为正 |

朝向转换为 `heading = (pi / 2 - radians(yaw)) mod (2*pi)`。调用 `locate_xy()` 时直接传 OpenDRIVE 平面坐标，其 `heading_rad` 参数使用弧度，不再进行 Unity 坐标适配。

输入地图与轨迹必须已经处于一致的坐标原点、方向和尺度。程序不读取地理参考来自动转换经纬度，也不估计地图与轨迹间的刚体变换。

底层定位允许省略高度，退回二维匹配；EGO 日志校验则要求 `position.x/y/z` 都存在且为有限数值。默认高度容差是 `2.5 m`，可在底层定位 API 中调整，页面没有对应设置。

### 3.3 NPC 场景 JSON

普通场景入口读取 `agents` 中 `type == 2` 的 NPC。下面展示本工具需要的结构，不代表完整的仿真场景定义：

```json
{
  "map": {"name": "example"},
  "agents": [
    {
      "type": 2,
      "variant": "Sedan",
      "transform": {
        "position": {"x": 1.0, "y": 0.0, "z": -1.5},
        "rotation": {"x": 0.0, "y": 90.0, "z": 0.0}
      },
      "waypoints": [
        {
          "ordinalNumber": 0,
          "position": {"x": 5.0, "y": 0.0, "z": -1.5},
          "angle": {"x": 0.0, "y": 90.0, "z": 0.0},
          "speed": 3.0
        }
      ]
    }
  ]
}
```

NPC 路径点按 `ordinalNumber` 排序，缺省序号按 `0` 参与排序。绘图时会在路径点之前加入 `transform.position` 初始位置；页面总路径点数只统计 `waypoints`，因此逐点列表可能比总路径点数多出每个 NPC 的初始点。

`waitTime` 等其他仿真字段不用于时间回放。普通场景入口不会直接把 `type == 1` 的 EGO 配置当作实际运行轨迹。

### 3.4 EGO 运行日志

推荐使用 `.jsonl`，每个非空行是一个完整运行对象。兼容 `.json` 数组和单个运行对象。

| 字段 | 要求 | 当前用途 |
|---|---|---|
| `jsonname` | 必填，非空原始文件名，不能传路径 | 精确关联场景文件 |
| `waypoints` | 必填数组，可为空 | 按数组顺序分析实际轨迹 |
| `waypoints[].position` | 必须包含有限数值 `x/y/z` | 平面和高度定位 |
| `waypoints[].angle` | 必须包含有限数值 `y` | 朝向与左右边界判定 |
| `waypoints[].speed` | 必填有限数值 | 跌落阈值、接近停止的滑坡证据 |
| `waypoints[].ordinalNumber` | 可选，提供时应能转换为整数 | 点编号；不用于重新排序 |
| `sim_result` | 可选 | 碰撞、停滞文本判定及结果显示 |
| `mapname` | 可选 | 单次分析的回退显示信息；批次关联以场景 `map.name` 为准 |
| `cost_time` | 可选 | 保存运行耗时信息 |
| `max_speed` | 可选 | 保存日志报告值，不代替逐点速度检测 |
| `final_distance_to_destination` | 可选 | 停滞预览时优先显示的终点距离 |
| `category`、`scenario_index`、`completed_at` | 不要求 | 不参与现有定位和异常规则 |

下面是单次运行的 JSON 示例。保存为 JSONL 时，一条运行应压缩成一行；保存为 JSON 时可以保持该缩进形式：

```json
{
  "jsonname": "example_mutated.json",
  "mapname": "example_mutated",
  "waypoints": [
    {
      "ordinalNumber": 0,
      "position": {"x": 1.0, "y": 0.0, "z": -1.5},
      "angle": {"y": 90.0},
      "speed": 3.0
    },
    {
      "ordinalNumber": 1,
      "position": {"x": 2.0, "y": 0.0, "z": -1.5},
      "angle": {"y": 90.0},
      "speed": 3.2
    }
  ],
  "cost_time": 1.0,
  "sim_result": "SUCCESS: reached destination",
  "max_speed": 3.2,
  "final_distance_to_destination": 0.0
}
```

文件名、地图和坐标只是结构示例，需要替换为同一场景的真实数据。仅有这份日志不足以完成页面分析，关联场景和 XODR 仍必须可读取。

日志解析还应注意：

- 输入必须按实际采样顺序排列，程序不按时间或 `ordinalNumber` 重排 EGO 点。
- 零路径点运行会保留，但仍需要通过场景和地图关联。
- 空文件、空数组或没有任何完整记录的日志会报错。
- JSONL 中间行损坏会导致整批失败。
- 只有文件末尾没有换行、最后一个非空行符合不完整 JSON 特征时，才忽略该末尾片段；不是任意坏行都能跳过。
- 轨迹没有车辆身份校验。其他车辆日志即使结构相同并通过校验，也会被作为 EGO 分析，不会自动识别成 NPC。

## 4. NPC 场景可视化

### 4.1 载入地图和场景

1. 启动本地页面，选择场景可视化入口。
2. 选择或拖入场景 `.json`。
3. 建议同时选择对应 `.xodr`，尤其是变异地图。
4. 点击“载入并分析”，等待完成后查看地图。
5. 后续可从历史记录重新打开，不必再次选择文件。

手动选择 XODR 时直接使用该文件，不核实其内容是否与 `map.name` 对应。

没有手动选择 XODR 时，程序按场景 `map.name` 在 `D:\Desktop\SCTrans-data\OpenSCENARIO` 中查找同名地图。该入口只检查目录第一层，不递归；名称比较忽略大小写。找不到或有多个匹配项时返回错误，不尝试删除变异后缀。

### 4.2 地图交互

- 总览显示所有 NPC 路线；选择一个 NPC 后显示其逐点信息，并自动聚焦路线。
- 滚轮以指针位置为中心缩放，拖动平移，“恢复视图”回到初始范围。
- 悬停路径点可查看编号、道路归属、定位方式和速度；悬停车道可查看道路 ID、车道 ID、类型和采样宽度信息。
- 相对初始比例放大到 `1.7` 倍后显示选中 NPC 的点编号与道路标签，达到 `2.5` 倍后显示车道 ID 和宽度标签。
- 放大超过 `1.35` 倍时显示道路鸟瞰图和当前视野范围框。
- 道路首尾画出完整路宽分界线；道路面半透明，车道边界在道路面之后绘制。
- 右下角图例可以展开或收起，开关状态保存在浏览器 `localStorage`。

地图使用二维 Canvas 绘制，车道面由参考线和宽度采样得到。绘图采样与定位数值计算不是同一个精度层级；高度参与定位，但不会把道路渲染成可旋转的三维立交模型。

路线状态的基础样式为确定路径实线、候选路径虚线、异常路径红色点线。初始位置是圆点，后续方向标记依据相邻路径点连线绘制，因此它不等同于逐帧车头姿态回放。当前新导入的 NPC 数据启用候选排序，详见第 7 节。

### 4.3 生成独立 HTML

```powershell
.\.venv\Scripts\python.exe -m visualization `
  "D:\maps\example.xodr" `
  "D:\scenarios\example.json" `
  -o "output\example.html"
```

不指定 `-o` 时输出到当前工作目录的 `scenario_visualization.html`。生成文件内嵌地图、路径数据和交互代码，可以直接在浏览器打开，不需要运行本地服务。

该命令处理一组地图与场景，不提供 EGO 批次页面导出。指定已存在的输出 HTML 路径会覆盖原文件，应为不同交付结果选择不同文件名。

## 5. EGO 批量分析流程

### 5.1 页面操作

1. 在首页切换到“EGO 合规分析”。
2. 选择或拖入一个 `.jsonl` 或 `.json` 日志文件。
3. 点击“分析全部 EGO 运行”，等待后台任务完成。
4. 从批次列表选择一次运行，查看轨迹、道路归属和异常颜色。
5. 使用越线、滑坡、跌落、碰撞候选、碰撞待复核或车辆停滞筛选。
6. 对碰撞候选选择人工结论并填写备注，点击保存。
7. 点击导出按钮生成整批 Excel。

页面一次选择一个日志文件，文件中可以有多次运行。没有扫描某个日志目录并自动合并所有文件的入口。

### 5.2 精确关联原始文件

后端按以下顺序准备整个批次：

1. 递归索引场景目录 `D:\Desktop\变异存档\Random` 中的 JSON。
2. 递归索引地图目录 `D:\Desktop\SCTrans-data\OpenSCENARIO` 中的 XODR。
3. 按每条记录的 `jsonname` 精确找到唯一场景原文件。
4. 从该场景读取 `map.name`，构造 `<map.name>.xodr`。
5. 优先使用场景同目录的该 XODR；不存在时才从地图索引中精确查找。

两个目录在准备阶段都必须存在且可读取，即使全部场景旁边已经有地图，地图目录也仍然会被索引。

场景和索引地图按完整文件名查找，不按前缀匹配，不剥离随机变异后缀。索引键保留文件名大小写，建议日志与原文件保持完全一致。同名场景出现在多个子目录时会报错，而不是随意选择一个。

场景中的首个 `type == 1` agent 用来提取 `transform.position` 配置起点和 `destinationPoint.position` 目标终点。缺失这些信息不会用日志首末点冒充配置起终点。

### 5.3 分析与地图预览

准备完成后，运行按地图路径分组，每组复用一个定位器，组内顺序处理运行，结果仍按原日志运行顺序组织。

浏览器导入显式使用线程模式：多个地图组最多使用 4 个工作线程；只有一个地图组时直接处理。Python API `build_ego_analysis()` 默认使用进程模式，多个地图组最多使用 4 个工作进程。它们不是按每条运行都创建一个工作进程。

导入只计算轨迹分析，不生成完整道路绘图数据。首次打开某条运行时才读取源地图、生成绘图数据并保存到该运行的历史载荷中。

### 5.4 如何阅读预览

预览显示 EGO 轨迹、道路和车道、点位方向、左右边界距离及异常颜色。配置起点使用蓝色圆环，目标终点使用绿色靶心；右侧显示对应坐标。

停滞运行用橙色暂停标记表示最后记录点，并连接目标终点。剩余距离优先取 `final_distance_to_destination`；该值不可用时才计算末点到目标点的平面直线距离，它不是道路剩余里程。

碰撞候选在有路径点时标出最后一个点。零路径点运行仍可有碰撞或停滞的运行级标签，但没有可用于确认位置的轨迹点。

## 6. 异常判定规则

规则实现在 [visualization/ego_analyzer.py](visualization/ego_analyzer.py)。阈值是代码常量，页面不提供车宽或判定阈值编辑。

### 6.1 左右越线

车辆宽度固定为 `1.9 m`，半宽 `0.95 m`。点被匹配到 `driving` 车道后，计算参考点到两侧车道边界的横向距离；距离严格小于 `0.95 m` 时标为相应侧越线，等于阈值不触发。

左右按车辆偏航角与道路参考线方向的关系区分，反向行驶时会交换道路坐标下的左右。该计算没有使用车辆长度、完整旋转包围盒、横滚或俯仰，不是精确车身多边形检测。

若一段连续越线点满足以下全部条件，会被标为变道过程，并去掉该段的左右越线标签：

- 段前和段后各至少有 3 个连续稳定点。
- 段前稳定点属于同一道路和车道，段后稳定点也属于同一道路和车道。
- 前后车道属于同一道路，车道 ID 同号且编号差为 1。
- 越线段内归属只包含这两个车道，并且两者都出现。

返回原车道、跨道路、跨非相邻车道或证据不足都不享受此豁免。这里用车道编号关系近似相邻同向车道，不校验实线、变道许可和完整通行方向配置。

### 6.2 纵向滑坡

相邻有效点必须位于同一道路，其平均纵坡绝对值不小于 `0.01`，且纵向位移与坡度符号相反，即沿坡面向下移动。连续段还必须满足：

- 至少 3 个点。
- 累计下坡路面位移不小于 `0.5 m`。
- 全段车头朝向上坡，或者该段第一个点的速度不大于 `0.3 m/s`。

路面位移按相邻点的 `abs(delta_s) * sqrt(1 + average_grade**2)` 累加，不是垂直落差。跨道路、定位中断或不再向下移动时结束该段。

“接近停止后重新下坡”分支以段首速度和后续下坡位移为证据，没有额外要求每步速度递增，也没有计算连续时间加速度。正常朝下坡方向行驶且段首未接近停止，不会仅因下坡就判滑坡。

该规则不读取油门、制动、档位或轮胎信息，因此不能单独确认位移是否违反控制意图。

### 6.3 跌出路面

任意采样点 `speed > 50.0 m/s` 就触发，单点即可，`50.0 m/s` 本身不触发。该条件不依赖定位成功、`sim_result` 或日志汇总字段 `max_speed`。

“跌出路面”是项目对该规则的显示名称。实现没有校验离地高度、自由落体加速度或车轮接地状态；高速度、速度单位错误或数据异常也可能触发。该标签应作为待检查证据，而非物理跌落已经确认。

### 6.4 碰撞候选与人工复核

`sim_result` 忽略大小写后包含 `collision`，就标记整次运行为碰撞候选。这是子字符串匹配，不是严格结果枚举；包含否定措辞但仍带该单词的文本也可能触发。

人工状态包括：

| 状态值 | 页面含义 | Excel 明细文字 |
|---|---|---|
| `unreviewed` | 未复核 | 待复核 |
| `confirmed` | 确认碰撞 | 已确认 |
| `dismissed` | 排除碰撞 | 已排除 |

备注最长 2000 个字符。复核结果保存到 SQLite，不回写原日志。人工排除不删除原始 `collisionCandidate`，也不从候选统计、异常运行统计或导出矩阵中自动扣除；需要分析确认碰撞时，应结合复核状态进行二次统计。

### 6.5 车辆停滞

`sim_result` 忽略大小写后包含 `stuck`，就标记整次运行停滞。代码不要求文本必须完全等于 `vehicle stuck`，也不根据“速度低于阈值持续多少秒”自行检测。

最后记录点只表示采样终止时的位置；现有实现不确认开始停滞的时刻、精确位置或原因。

### 6.6 未检出异常的含义

它只表示上述规则没有触发，不表示全程定位成功、满足全部交通法规或完成目标任务。

特别是，找不到可用 `driving` 车道的点无法正常判定几何越线或滑坡。该点仍可能通过速度规则触发跌落，但不能因其他标签为空就认定正常。零路径点运行也可能没有异常标签。

## 7. 定位结果与可信度

### 7.1 核心计算

[map_locator.py](map_locator.py) 先将查询点投影到道路参考线，再根据 `laneSection`、`laneOffset` 和分段宽度确定车道候选，并按参考线高程筛选。

直线使用解析投影；`paramPoly3` 求距离函数的驻点并比较端点，归一化参数曲线还会通过数值积分计算纵向弧长。默认启用保守空间索引和最多 65536 项的精确查询缓存；不能安全建立索引或索引查询没有几何候选时会全扫描。

轨迹定位先建立每点候选，再结合唯一道路锚点、道路连接端点、相邻位移和部分方向启发式处理重叠区段。连接距离约束使用相邻平面位移的 `2.0` 倍加端点容差，避免在远离连接端点的重叠道路之间任意切换。

这不是对每个相邻点都执行的完整路径合法性证明，也不是基于 `laneLink` 的完整车道级匹配。`propagated` 中可能包含右侧锚点偏好或末段方向消歧，不能全部解释为唯一拓扑路径证明。

### 7.2 状态含义

| `resolution` | 含义 | 如何使用 |
|---|---|---|
| `direct` | 该点候选只涉及一条道路 | 道路级直接确定，同一道路仍可能有多个车道候选 |
| `propagated` | 多道路候选经轨迹传播或方向消歧选定道路 | 结合上下文和地图质量检查 |
| `ranked` | 对剩余候选排序后选择一个 | 启发式选择，不代表原始歧义已经消失 |
| `ambiguous` | 仍有多个可行道路 | 保留候选，不能视作唯一归属 |
| `inconsistent` | 原始候选存在，但拓扑筛选没有可行道路 | 检查地图连接、采样和候选 |
| `unmatched` | 没有定位候选 | 检查坐标、高度、地图支持和道路范围 |

底层 `locate_trajectory()` 默认 `resolve_remaining=False`，保留 `ambiguous/inconsistent`。单点接口 `locate_waypoint()` 则直接返回排序首选，不报告完整歧义，需要候选时使用 `locate_xy()`。

当前 NPC 和 EGO 页面都显式启用 `resolve_remaining=True`。剩余候选先按高度误差、基于车道 ID 符号的方向误差、车道中心距离排序，再用道路和车道编号打破平局；可行候选为空时，会退回原始候选进行排序。

查看器把 `ranked` 归为确定点，并显示“最高可信候选”。因此不能仅凭确定点数量认定全部点唯一匹配。需要保留原始候选、可行候选和置信字段的研究，应直接使用底层 API；页面载荷没有完整保留 `TrajectoryMatch`。

### 7.3 置信字段和 EGO 后处理

`confidence` 是道路级规则值：直接或传播选定通常为 `1.0`，歧义结果为可行道路数的倒数；排序结果为参与排序道路数的倒数；默认模式下未匹配或不一致为 `0.0`。它不是经过统计校准的概率，不能当作检测准确率。

EGO 分析还会从选中结果、可行候选及原始候选中寻找 `driving` 车道，所以其最终用于计算的车道可能不是定位器最初选中的车道；`resolution` 仍沿用原匹配的标记，不会重新计算确定度。

NPC 与 EGO 页面均在初始定位无候选时允许约 `1.0 m` 道路外缘补偿和 `0.1 m` 端点补偿。超过补偿范围、偏离参考高程超过默认容差或缺少有效车道宽度，都可能未匹配。补偿并不是“驶出道路仍能一直跟踪”的保证。

## 8. Excel 导出与统计口径

### 8.1 导出文件

点击“导出到 `D:\download`”后，后端直接将工作簿写入该目录，浏览器只收到文件名和保存路径 JSON，不接收附件下载响应。

默认名称为 `<日志文件名去扩展名>_ego_analysis.xlsx`。同名文件已经存在时追加时间戳，保留旧导出。Excel 是导出时的快照，后续修改人工复核信息不会更新已经生成的文件。

导出范围始终是整个批次，与页面当前筛选条件无关。

### 8.2 三个工作表

| Sheet | 一行的含义 | 主要字段 |
|---|---|---|
| 运行明细 | 一次 EGO 运行 | 批次 ID、运行序号、场景、地图、仿真结果，以及各异常的次数、极值、道路/车道和碰撞复核 |
| 违规汇总 | 一类异常 | 触发次数、触发场景数、触发场景占比 |
| 场景违规矩阵 | 一次 EGO 运行 | 基础信息，以及六类异常的“是/否” |

运行明细中，左右越线分别记录事件次数、最大车身侵入距离和道路/车道；滑坡记录事件次数、最大单段下坡路面位移和道路/车道；跌落记录事件次数和最高超阈值速度；碰撞记录中文复核状态和备注；停滞记录“是”。未发生的异常明细单元格留空。

最大车身侵入距离按 `max(0, 0.95 - 边界距离)` 计算。道路/车道字符串去重后用 `；` 连接，如 `R8/L1；R9/L2`。当前事件的道路/车道字段取事件首点；连续越线段跨越多个道路或车道时，导出不是该事件途经道路的完整清单。

工作簿不包含全部原始轨迹点、完整候选集或所有日志字段，不能代替原始数据备份。

### 8.3 次数和分母

- 左侧越线、右侧越线、滑坡、跌落按事件段计数，不按异常路径点数计数。
- 左右越线及跌落中的连续同类点合并成一段；同一点可以同时触发多类规则。
- 碰撞候选和停滞在每条运行内最多各计一次，有没有轨迹点都不改变该运行级规则。
- “触发场景数”实际指触发该类异常的运行记录数，不按 `jsonname` 去重。
- “触发场景占比”以批次全部运行数为分母，包括零路径点运行；同一场景重复运行分别计数。
- 页面顶部越线、滑坡等数字主要是触发运行数，不应直接与 Excel 的事件触发次数比较。
- “排除碰撞”保留原候选标记；矩阵中碰撞候选仍为“是”，需结合运行明细的复核状态解读。

## 9. 历史记录与运行限制

### 9.1 持久化与重复导入

默认数据库为项目根目录下 `history/visualization_history.sqlite3`，包含 NPC 历史、EGO 批次和 EGO 运行表。历史按最近更新时间排序，删除记录不删除原始地图、场景、日志或已导出的工作簿。

NPC 记录 ID 基于地图文本和场景文本内容生成；EGO 批次 ID 基于日志文本生成。相同文本重复导入会更新同一条历史，不创建新版本。EGO 重导入会重算分析，并按运行索引保留已有人工复核状态和备注。

EGO 批次 ID 不包含地图、场景内容或算法版本。更改源地图后重新导入同一日志，会更新原批次，而不是保留前后两个实验版本。空白和排版变化也可能改变文本哈希；这里没有按 JSON 语义去重。

### 9.2 源文件依赖

NPC 历史保存完整绘图载荷，重新打开通常不再读取原文件。EGO 在导入时只保存分析结果和源地图路径，尚未打开过的运行首次预览仍需要原 XODR。

EGO 道路绘图数据按运行缓存，而非全局共享地图缓存；源地图在分析和首次预览之间被修改，可能造成显示地图与已有分析结果不一致。用于实验复核时应保留原始输入文件及版本。

旧 EGO 历史缺少起终点字段时，会尝试按原文件名规则回填。场景源文件不可用时可保留已有轨迹显示，但这不意味着缺失且尚未缓存的地图也可以省略。

### 9.3 大小、失败与并发

| 项目 | 当前行为 |
|---|---|
| 普通场景导入 | 请求体上限 `50 * 1024 * 1024` 字节 |
| EGO 日志导入 | 请求体上限 `256 * 1024 * 1024` 字节 |
| 日志读取 | 浏览器和后端都整体读取，不是流式分析 |
| 批次失败 | 任何记录校验、文件关联或分析异常可能导致整批失败，不自动跳过坏运行 |
| 成功结果保存 | 批次全部分析完成后保存，不持久化每个中间完成项 |
| 任务进度 | 存在进程内存中，重启丢失，不支持断点恢复 |
| 全局任务限流 | 没有全局任务队列限流；多个导入可各自创建后台任务 |
| 多地图并发 | 单批最多 4 个线程或进程，具体由入口决定 |

大小限制作用于 UTF-8 编码的整个 JSON 请求体，不等于磁盘文件大小；文本嵌入 JSON 后的转义会增加请求字节数。单个日志小于 256 MiB 仍可能因包装后的请求超限而被拒绝。

建议拆分超大日志为多个保留完整运行对象的文件，避免在一条运行对象中间截断。任务未完成时关闭服务，需要重新导入。

### 9.4 部署边界

默认服务仅监听 `127.0.0.1`，使用 Python `ThreadingHTTPServer`，没有用户鉴权、权限隔离或生产级网关。即使通过代码改变监听地址，也不应将其直接暴露到公网或视为多用户服务。

页面默认路径、启动和停止脚本面向 Windows；迁移到其他机器需要安装依赖、指定有效目录并核对原始数据路径，而不是只复制 HTML 首页。

## 10. Python 接口与自定义配置

### 10.1 单点和轨迹定位

```python
from map_locator import OpenDriveMapLocator

locator = OpenDriveMapLocator(r"D:\maps\example.xodr")

match = locator.locate_waypoint(
    position={"x": 12.5, "y": 0.8, "z": -3.2},
    yaw_degrees=90,
)
if match is not None:
    print(match.road_id, match.lane_id, match.s, match.t)
    print(match.reference_elevation, match.height_error)
    print(locator.lane_geometry(match))

candidates = locator.locate_xy(
    map_x=12.5,
    map_y=-3.2,
    map_z=0.8,
    height_tolerance=2.5,
)

results = locator.locate_trajectory(
    positions=[
        {"x": 5.0, "y": 0.0, "z": -1.5},
        {"x": 12.0, "y": 0.0, "z": -1.5},
        {"x": 18.0, "y": 0.0, "z": -1.5},
    ],
    yaw_degrees=[90, 90, 90],
    resolve_remaining=False,
)
for result in results:
    print(result.resolution, result.location, result.confidence)
    print(result.candidates, result.feasible_candidates)
```

`yaw_degrees` 可以省略；提供时长度必须与点数一致。关闭加速进行对照可构造 `OpenDriveMapLocator(path, enable_acceleration=False)`。

`MapLocation` 包括 `road_id`、`lane_id`、`s`、`t`、参考线朝向 `reference_heading`、朝向差 `heading_error`、到车道中心的横向距离 `distance_to_lane_center`、参考高程 `reference_elevation` 和高度差 `height_error`。未传朝向或高度时，对应误差为 `None`。

`lane_geometry()` 可返回车道类型、内外边界 `inner_t/outer_t`、中心 `center_t`、宽度和纵坡 `grade`。底层定位可以返回非 `driving` 车道；是否适用于某种分析需要调用方判断。

### 10.2 生成 NPC HTML 或数据载荷

```python
from visualization import build_scenario_visualization

summary = build_scenario_visualization(
    xodr_path=r"D:\maps\example.xodr",
    scenario_path=r"D:\scenarios\example.json",
    output_path=r"D:\reports\example.html",
)
print(summary)
```

不需要直接写文件时，使用 `build_visualization_payload()` 生成数据，再调用 `render_visualization_html()` 获取 HTML 字符串。

### 10.3 批量分析 EGO

```python
from pathlib import Path
from visualization import build_ego_analysis

log_text = Path(r"D:\logs\ego.results.jsonl").read_text(encoding="utf-8")
analysis = build_ego_analysis(
    log_text=log_text,
    scenario_directory=r"D:\scenarios",
    map_directory=r"D:\maps",
    progress_callback=lambda progress, message: print(progress, message),
    use_processes=False,
)
print(analysis["summary"])
for run in analysis["runs"]:
    print(run["jsonName"], run["stats"], run["events"])
```

这里显式选择线程模式，与浏览器入口一致。使用默认进程模式时，Windows 脚本应把实际调用放在 `if __name__ == "__main__":` 内。

`build_ego_analysis()` 返回 `analysisType`、`runs`、`summary` 和 `thresholds`，不自动保存 SQLite 或导出 Excel。直接调用 `analyze_ego_run(locator, run)` 可以跳过批次文件关联，但调用方要保证日志结构有效；需要统一校验时可先调用 `visualization.ego_analyzer.parse_ego_log()`。

### 10.4 自定义目录和端口

默认位置如下：

| 配置 | 默认值 |
|---|---|
| 服务地址 | `127.0.0.1:8765` |
| 场景目录 | `D:\Desktop\变异存档\Random` |
| 地图目录 | `D:\Desktop\SCTrans-data\OpenSCENARIO` |
| 导出目录 | `D:\download` |
| 历史数据库 | 项目根目录 `history/visualization_history.sqlite3` |

现有启动入口没有这些配置的命令行选项或环境变量读取逻辑。开发者可以使用 `create_server()`：

```python
from pathlib import Path
from visualization.server import create_server

if __name__ == "__main__":
    project_directory = Path.cwd()
    server = create_server(
        database_path=project_directory / "history" / "custom_history.sqlite3",
        host="127.0.0.1",
        port=8766,
        map_directory=r"D:\maps",
        scenario_directory=r"D:\scenarios",
        export_directory=r"D:\reports",
    )
    print("http://127.0.0.1:8766/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
```

从项目根目录运行该示例；`create_server()` 不自动打开浏览器。前端部分提示文字仍写着默认导出目录，实际导出位置以接口返回的 `savedPath` 为准。停止脚本也不会跟随自定义端口更新。

## 11. 主要代码与调用关系

### 11.1 文件职责

| 文件或目录 | 职责 |
|---|---|
| [map_locator.py](map_locator.py) | XML 地图加载、直线和参数曲线投影、车道几何、高程、空间索引、查询缓存、轨迹候选传播和排序 |
| [visualization/scenario_visualizer.py](visualization/scenario_visualizer.py) | 采样道路和车道、提取 NPC、组装载荷、生成独立 HTML |
| [visualization/ego_analyzer.py](visualization/ego_analyzer.py) | 日志解析、源文件关联、地图分组、逐点判定、变道豁免、滑坡检测、事件合并与汇总 |
| [visualization/ego_store.py](visualization/ego_store.py) | EGO 批次与运行持久化、人工复核、Excel 导出 |
| [visualization/server.py](visualization/server.py) | NPC 历史存储、本地 HTTP 服务、后台任务、进度查询、EGO 预览懒加载 |
| [visualization/app.html](visualization/app.html) | 文件选择和拖放、模式切换、导入进度、历史管理 |
| [visualization/ego_dashboard.html](visualization/ego_dashboard.html) | 批次统计、运行筛选、碰撞复核和导出 |
| [visualization/viewer.html](visualization/viewer.html) | 共用二维地图、路线与点位渲染、缩放平移、悬停和鸟瞰图 |
| [visualization/__main__.py](visualization/__main__.py) | `python -m visualization` 独立 HTML 命令行入口 |
| [visualization/__init__.py](visualization/__init__.py) | 对外暴露的可视化和 EGO 分析接口 |
| [CONTEXT.md](CONTEXT.md) | 领域术语和判定语义；实现细节仍需结合代码 |
| `test_*.py` | 定位、分析、持久化、HTTP 和导出测试 |
| `test_record/` | 已有运行数据，不是自动执行的仿真脚本目录 |
| `history/`、`output/` | 本地历史数据库和已有输出文件 |

### 11.2 主要调用链

```text
NPC:
app.html
  -> POST /api/import-jobs
  -> server._import_visualization
  -> scenario_visualizer.build_visualization_payload
  -> OpenDriveMapLocator.locate_trajectory
  -> HistoryStore.save
  -> GET /view/{id}
  -> viewer.html

EGO:
app.html
  -> POST /api/ego/import-jobs
  -> ego_analyzer.build_ego_analysis
  -> parse_ego_log + exact source resolution
  -> _analyze_map_group
  -> analyze_ego_run + locate_trajectory + rule detection
  -> EgoAnalysisStore.save
  -> ego_dashboard.html
  -> GET /ego-view/{batch_id}/{run_index}
  -> populate_run_roads + viewer.html

Export:
ego_dashboard.html
  -> POST /api/ego/{batch_id}/export
  -> EgoAnalysisStore.export_xlsx
  -> local XLSX + JSON savedPath
```

建议先读 `analyze_ego_run()` 理解一次运行如何变为点、事件和统计，再读 `locate_trajectory()` 理解定位不确定性，最后读 `EgoAnalysisStore.export_xlsx()` 核对最终统计。

### 11.3 本地接口速查

这些接口服务于当前本地前端，不是承诺兼容的公共远程 API。`run_index` 从 `0` 开始，页面和 Excel 的运行序号从 `1` 开始。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 返回应用标识与健康状态 |
| GET | `/api/history` | NPC 历史列表 |
| POST | `/api/import` | 同步 NPC 导入 |
| POST | `/api/import-jobs` | 后台 NPC 导入，返回 `jobId` |
| GET | `/api/import-jobs/{job_id}` | NPC/EGO 共用任务状态查询 |
| GET | `/view/{id}` | NPC 历史地图 HTML |
| DELETE | `/api/history/{id}` | 删除 NPC 历史 |
| POST | `/api/ego/import-jobs` | 后台 EGO 导入，返回 `jobId` |
| GET | `/api/ego/history` | EGO 批次历史 |
| GET | `/api/ego/{batch_id}` | 批次汇总和运行摘要 |
| GET | `/api/ego/{batch_id}/runs/{run_index}` | 单次运行载荷，不触发道路绘图懒加载 |
| GET | `/ego/{batch_id}` | EGO 批次面板 HTML |
| GET | `/ego-view/{batch_id}/{run_index}` | 单次运行预览，按需补全道路绘图数据 |
| POST | `/api/ego/{batch_id}/reviews/{run_index}` | 保存碰撞复核 |
| POST | `/api/ego/{batch_id}/export` | 生成本地 Excel，返回 `fileName` 和 `savedPath` |
| DELETE | `/api/ego/{batch_id}` | 删除 EGO 批次及其运行 |

NPC 导入请求是 JSON，包含 `scenarioName`、`scenarioText`，手选地图时还需同时包含 `xodrName`、`xodrText`。EGO 导入请求包含 `logName`、`logText`；复核请求包含 `status` 和可选 `note`。这些不是 multipart 文件上传接口。

后台任务状态为 `queued`、`running`、`completed` 或 `failed`。提交得到 HTTP `202` 只表示创建任务，不表示分析成功；应继续轮询任务状态，失败时读取 `error`。

## 12. 常见问题

| 现象 | 检查或处理方式 |
|---|---|
| `ModuleNotFoundError: numpy` 等 | 使用启动脚本实际选中的解释器安装依赖；系统 `python` 与项目虚拟环境可能不同 |
| 默认端口被占用 | 确认是否已有 ResultAna；其他应用占用时释放端口或通过 `create_server()` 选择其他端口 |
| 只传场景 JSON 找不到地图 | 普通入口只搜索地图目录第一层；直接选择准确的 XODR，或检查 `map.name` |
| EGO 找不到场景或提示多个同名文件 | 检查原始 `jsonname`、目录及文件名大小写，处理重复同名源文件；不要删掉变异后缀绕过关联 |
| 场景旁边有地图仍报地图目录不可读 | 批次会预先索引配置地图目录，该目录也必须存在 |
| `UnsupportedGeometryError` | 地图存在当前不支持的几何，需扩展解析能力或使用受支持地图；没有自动转换开关 |
| 路径明显错位或大量未匹配 | 检查 `x/z` 平面映射、米制单位、高度基准、地图版本和车道宽度定义 |
| 有“确定道路”，但属于重叠路口 | 检查是否为 `ranked`；需要保留歧义时用底层接口关闭 `resolve_remaining` |
| 只停滞的运行不在“包含异常”列表中 | 当前该筛选只检查轨迹事件和碰撞候选，使用“车辆停滞”或“全部运行”；顶部异常统计仍包含停滞 |
| 人工排除碰撞后候选数没有减少 | 复核状态和原始候选分开保存，这是当前统计口径 |
| 日志损坏后整批失败 | 仅特定末尾残片可忽略，中间坏行或无效字段需要修复后重新导入 |
| 历史第一次预览失败 | EGO 尚未缓存道路数据时仍需要源 XODR，恢复对应路径与文件后重试 |
| Excel 没有浏览器下载弹窗 | 文件由后端直接写到导出目录，查看该目录或接口返回的 `savedPath` |
| 找不到完整逐点 Excel | 当前 Excel 是运行与事件汇总；逐点数据可从 Python 分析结果或运行载荷接口读取 |

## 13. 测试与验证范围

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -B -m unittest -v `
  test_map_locator `
  test_scenario_visualizer `
  test_visualization_server `
  test_ego_analyzer `
  test_ego_store `
  test_ego_export_server
```

如果使用启动器回退的相邻项目环境，解释器路径替换为 `..\openDriveMapEditor\.venv\Scripts\python.exe`。

| 测试模块 | 主要覆盖 |
|---|---|
| `test_map_locator` | 几何投影、车道边界、偏移、分段宽度、高程、轨迹拓扑、排序、加速与全扫描一致性、非法输入 |
| `test_scenario_visualizer` | NPC 载荷、道路与车道采样、独立 HTML、方向与鸟瞰图相关模板逻辑 |
| `test_visualization_server` | 导入任务、自动找图、历史持久化、EGO 页面、复核和删除 |
| `test_ego_analyzer` | 左右越线、变道豁免、滑坡、跌落、碰撞和停滞、源文件关联、JSONL 兼容 |
| `test_ego_store` | 批次存储、人工状态、三表结构、计数口径、导出重名处理 |
| `test_ego_export_server` | 本地工作簿保存与 JSON 响应，不使用附件下载 |

2026-09-22 的代码阅读验证使用相邻 `openDriveMapEditor` 虚拟环境运行以上 6 个测试模块，94 项全部通过。该记录不代表完整真实数据集重跑、浏览器截图验收、大数据性能基准或真实异常检测精度评估；后续代码变更应重新运行测试。
