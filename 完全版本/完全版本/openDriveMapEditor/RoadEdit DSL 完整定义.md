# RoadEdit DSL 完整定义

本文档整理自 `llm/agent_tools.py` 当前工作区版本中的三个定义：

- `WIDTH_DSL_DEFINE`，源码起始位置为 `llm/agent_tools.py:618`
- `SLOPE_DSL_DEFINE`，源码起始位置为 `llm/agent_tools.py:1190`
- `CURVE_DSL_DEFINE`，源码起始位置为 `llm/agent_tools.py:2004`

文档描述 DSL 的数据结构、字段语义、合法组合、默认规则、计算公式和无效请求处理。若本文档与源码定义不一致，以源码定义为准。

## 所有 DSL 共用的输出约束

三个 DSL 都使用同一个顶层结构：

```json
{
  "operations": []
}
```

`operations` 是操作数组。没有有效修改，或缺少形成唯一操作所需的信息时，输出空数组。

实际提取结果必须是严格 JSON，不能包含 Markdown、代码块标记、分析过程、解释、注释或 JSON 之外的文字。JSON 中不使用 `null`，定义中的空值统一写成字符串 `"none"`。

`"none"` 和空 `operations` 的用途不同：

- Width 和 Slope 的有效 operation 必须使用合法的实际字段组合。不能用一组 `none` 字段代替失败操作。
- Curve 的 `modification` 固定包含两个完整对象，未修改的子结构必须用完整的 `none` 结构补齐。
- 整个请求没有有效操作时，三个 DSL 都输出 `{"operations": []}`。

三个 DSL 都支持 `road_limit`：

| 用户语义 | `road_limit` |
|---|---:|
| 只改当前道路，不影响其他道路 | `1` |
| 相邻或后续道路跟随，但未指定数量 | `2` |
| 最多影响 N 条道路 | 正整数 `N` |
| 没有表达传播范围 | `"none"` |

`road_limit` 统计初始目标道路，只能是大于等于 1 的整数或字符串 `"none"`。`"none"` 表示用户未额外限制传播数量，后续使用系统默认传播机制。

## Width DSL

### 支持范围

Width DSL 支持：

- 设置道路整体或指定车道的最终宽度
- 按米或百分比增加、减少宽度
- 将最终宽度设置为当前宽度的指定比例
- 定性加宽或缩窄
- 根据当前车道宽度计算最终目标宽度
- 限制修改传播的道路数量

道路坡度、高程、车道数量、弯道、曲率、材质和颜色不属于 Width DSL。混合请求只保留 Width 支持的部分。

### 输入数据

Width 规则依赖以下输入：

```json
{
  "road_id": "road_12",
  "lane_ids": [3, 2, 1, -1, -2],
  "lane_avg_width": {
    "3": 2.5,
    "2": 2.5,
    "1": 2.5,
    "-1": 3.2,
    "-2": 3.0
  }
}
```

字段含义：

- `road_id` 是当前选中道路 ID。
- `lane_ids` 是车道成员及其从道路物理左侧到右侧的排列顺序，也是车道是否存在的权威来源。
- `lane_avg_width` 是各车道当前平均宽度，单位默认为米，也是宽度计算的权威来源。

### 数据结构

下面是结构示意，其中带 `|` 的字符串表示可选类型或枚举，不是实际输出值：

```json
{
  "operations": [
    {
      "operation": "width",
      "road_id": "string",
      "lane_ids": "string[] | none",
      "road_limit": "number | none",
      "change": {
        "mode": "absolute | relative",
        "action": "increase | decrease | set",
        "magnitude": {
          "kind": "numeric | qualitative ",
          "value": "number | none",
          "level": "slight | default | significant | none",
          "unit": "m | percent | none"
        }
      }
    }
  ]
}
```

`operation` 固定为 `"width"`。有效 operation 的字段必须完整，不能包含未定义字段。

### road_id

`road_id` 是必填字段，来源优先级为：

1. 用户明确指定的有效道路 ID。
2. `road_data.road_id`。

`road_12`、`12号道路`、`12号路` 都规范化为 `"road_12"`。

“当前道路”“这条路”“选中的道路”“所有道路”“全部道路”“全局修改”都使用 `road_data.road_id`。全局类表达不会生成 `"all"` 等特殊道路 ID，`road_data.road_id` 是修改和传播起点。

无法从用户指令或 `road_data` 确定道路时，输出空 `operations`，不能编造 ID。

### lane_ids

`lane_ids` 只能是：

- 字符串 `"none"`，表示修改道路整体宽度
- 由 `"lane_数字"` 组成的字符串数组，表示明确指定的车道

车道 ID 的转换方式：

- `3` 转为 `"lane_3"`
- `-1` 转为 `"lane_-1"`

输出多条车道时，原则上保持 `road_data.lane_ids` 的原始顺序。

道路整体和所有车道是两种不同目标：

- “整条道路加宽”“调整道路整体宽度”“把路宽设为 10 米”使用 `"lane_ids": "none"`。
- “所有车道”“全部车道”“每条车道”“整条道路的每一条车道”输出 `road_data.lane_ids` 中的全部车道。

给定：

```json
[3, 2, 1, -1, -2]
```

所有车道输出为：

```json
["lane_3", "lane_2", "lane_1", "lane_-1", "lane_-2"]
```

#### 半幅位置与完整道路序号

正数 ID 是左侧车道，负数 ID 是右侧车道。

“左侧第 N 条”和“右侧第 N 条”只在对应半幅内计数，方向都是从道路外侧到道路中心：

- 左侧正数车道按 ID 从大到小计数。
- 右侧负数车道按绝对值从大到小计数。
- “左侧车道”选择全部正数车道。
- “右侧车道”选择全部负数车道。

对于 `[3, 2, 1, -1, -2]`：

- 左侧第 1 条是 `"lane_3"`，第 2 条是 `"lane_2"`，第 3 条是 `"lane_1"`。
- 右侧第 1 条是 `"lane_-2"`，第 2 条是 `"lane_-1"`。

“从左数第 N 条”和“从右数第 N 条”在完整 `lane_ids` 中计数：

- 从左数，从完整数组第一项向右计数。
- 从右数，从完整数组最后一项向左计数。

#### 中间车道

- 车道数为奇数时，选择正中间的一条。
- 车道数为偶数时，选择中间的两条。

示例：

```json
[3, 2, 1, -1, -2]
```

中间车道是 `["lane_1"]`。

```json
[2, 1, -1, -2]
```

中间车道是 `["lane_1", "lane_-1"]`。

用户直接指定 `lane_2`、`lane_-1`、`2号车道` 或“车道 ID 为 2”时，按对应 ID 处理。所有目标车道必须存在于 `road_data.lane_ids`。位置越界、车道不存在或描述无法形成唯一目标集合时，输出空 `operations`。

### change 和 magnitude

#### 设置最终米制宽度

“改成 3.5 米”“设为 4 米”“加宽到 3.5 米”“缩窄到 2.8 米”都表示设置最终值：

```json
{
  "mode": "absolute",
  "action": "set",
  "magnitude": {
    "kind": "numeric",
    "value": 3.5,
    "level": "none",
    "unit": "m"
  }
}
```

`value` 是最终目标宽度，不是增减量。“加宽到”和“缩窄到”仍使用 `action = "set"`。

#### 按米或百分比增减

数值增减统一使用 `mode = "relative"`、`kind = "numeric"`、`level = "none"`：

| 请求 | `action` | `value` | `unit` |
|---|---|---:|---|
| 加宽 0.5 米 | `increase` | `0.5` | `m` |
| 缩窄 0.3 米 | `decrease` | `0.3` | `m` |
| 加宽 20% | `increase` | `20` | `percent` |
| 缩窄 10% | `decrease` | `10` | `percent` |

`value` 是正的变化幅度，方向由 `action` 表示。百分之二十写成 `20`，不写成 `0.2`。

计算公式：

```text
increase + m:
target = current + value

decrease + m:
target = current - value

increase + percent:
target = current × (1 + value / 100)

decrease + percent:
target = current × (1 - value / 100)
```

#### 设置为当前宽度的比例

“设置到现在的 90%”“调整为当前宽度的 80%”“设置为原来的一半”“设置为当前宽度的两倍”使用：

```json
{
  "mode": "relative",
  "action": "set",
  "magnitude": {
    "kind": "numeric",
    "value": 90,
    "level": "none",
    "unit": "percent"
  }
}
```

公式：

```text
target = current × value / 100
```

常见比例：

- 当前的一半：`50`
- 当前的四分之一：`25`
- 当前的两倍：`200`
- 与当前相同：`100`

`relative + set` 只能与 `numeric + percent` 配合，`value` 必须大于等于 0。“设置到现在的 90%”不能写成“减少 90%”，也不能改写成“减少 10%”。

#### 定性增减

有明确加宽或缩窄方向但没有数值时，使用：

```json
{
  "mode": "relative",
  "action": "increase",
  "magnitude": {
    "kind": "qualitative",
    "value": "none",
    "level": "default",
    "unit": "none"
  }
}
```

程度映射：

| 自然语言 | `level` |
|---|---|
| 稍微、略微、一点、小幅 | `slight` |
| 一些、适当、正常调整 | `default` |
| 大幅、明显、显著、很多 | `significant` |

只说“加宽”“变宽”“缩窄”或“变窄”时，`level = "default"`。只有“调整”“优化”“修改”，没有方向或最终值时，不构成有效操作。

### 根据 road_data 计算最终宽度

最终目标能由 `lane_avg_width` 唯一算出时，输出计算后的米制绝对值：

```text
absolute + set + numeric + m
```

支持的计算包括：

- 设置为某条参考车道的当前宽度
- 设置为最宽或最窄车道的当前宽度
- 设置为指定车道当前宽度的算术平均值

“把最右侧车道加宽到和最宽车道一样”时，`value` 是最宽车道的当前宽度，不是两条车道之间的差值。

“将所有车道设置为所有车道的平均宽度”时，`lane_ids` 输出全部车道，`value` 输出 `lane_avg_width` 的算术平均值。

只能使用输入中真实存在的数据。参考车道不存在、宽度缺失或结果无法唯一计算时，输出空 `operations`。

### Width 合法字段组合

未列出的组合全部非法。

| `mode` | `action` | `kind` | `unit` | 含义 |
|---|---|---|---|---|
| `absolute` | `set` | `numeric` | `m` | 设置最终米制宽度 |
| `relative` | `increase` | `numeric` | `m` | 按米增加 |
| `relative` | `decrease` | `numeric` | `m` | 按米减少 |
| `relative` | `increase` | `numeric` | `percent` | 按百分比增加 |
| `relative` | `decrease` | `numeric` | `percent` | 按百分比减少 |
| `relative` | `set` | `numeric` | `percent` | 设置为当前宽度的指定比例 |
| `relative` | `increase` | `qualitative` | `none` | 定性加宽 |
| `relative` | `decrease` | `qualitative` | `none` | 定性缩窄 |

补充约束：

- `numeric.value` 必须是有限数字，`level` 必须为 `"none"`。
- `qualitative.value` 和 `unit` 必须为 `"none"`，`level` 不能为 `"none"`。
- `increase` 和 `decrease` 的数值 `value` 必须大于 0。
- `absolute` 只能与 `set` 配合。
- `absolute + set` 只允许 `numeric + m`。
- `relative + set` 只允许 `numeric + percent`，且 `value >= 0`。
- `percent` 只能用于相对增加、相对减少或相对比例设置。

### Width 多 operation 规则

下列情况拆成多个 operation：

- 道路不同
- 修改参数不同
- `mode`、`action`、`kind`、`value`、`level`、`unit` 或 `road_limit` 不同
- 用户要求多个按顺序执行的独立修改

多条车道执行完全相同的修改时，合并为一个 operation，并把车道放进同一个 `lane_ids` 数组。这里的“完全相同”指除 `lane_ids` 外的字段全部相同。多个 operation 按用户表达顺序排列。

### Width 提取顺序与规则优先级

提取顺序：

1. 判断请求是否包含有效宽度修改。
2. 确定 `road_id`。
3. 判断目标是道路整体，还是一条或多条车道。
4. 解析并验证 `lane_ids`。
5. 确定 `road_limit`。
6. 判断请求属于最终宽度设置、相对增减、当前宽度比例设置还是定性修改。
7. 确定 `mode`、`action` 和 `magnitude`。
8. 必要时根据 `lane_avg_width` 计算目标宽度。
9. 拆分不同修改，合并完全相同的多车道修改。
10. 检查字段组合和数据合法性。
11. 输出严格 JSON。

规则优先级由高到低：

1. 用户明确指定的道路、车道、动作、数值、单位和传播范围
2. `road_data.road_id` 保底规则
3. 字段合法性和组合约束
4. 道路整体与每条车道的区分规则
5. 车道位置映射规则
6. `road_data` 计算规则
7. 多 operation 规范化规则
8. 其他默认规则
9. 示例

### Width 空 operations 条件

以下情况输出空 `operations`：

- 请求不属于 Width DSL
- 无法确定 `road_id`
- 没有明确的 `set`、`increase` 或 `decrease`
- 目标车道不存在、位置越界或描述不唯一
- 缺少计算目标值所需的 `lane_avg_width`
- 请求内部冲突
- 缺少形成唯一操作所需的信息
- 请求过于模糊，无法确定实际宽度修改

不能通过编造道路、车道、宽度，或输出一组 `none change` 代替空数组。

### Width 完整示例

用户要求：“将 21 号道路的 1 号车道宽度设置到现在的 90%。”

```json
{
  "operations": [
    {
      "operation": "width",
      "road_id": "road_21",
      "lane_ids": ["lane_1"],
      "road_limit": "none",
      "change": {
        "mode": "relative",
        "action": "set",
        "magnitude": {
          "kind": "numeric",
          "value": 90,
          "level": "none",
          "unit": "percent"
        }
      }
    }
  ]
}
```

## Slope DSL

### 支持范围

Slope DSL 支持：

- 道路坡度百分比和坡角
- 道路变陡或变缓
- 起点或终点的绝对高程
- 起点或终点的相对升降
- 起点和终点等量整体升降
- 两端设置为同一个绝对高程
- 两端分别按各自当前高程应用相同比例
- 单个端点设置为当前高程的指定比例
- 限制修改传播的道路数量

道路宽度、车道数量、弯道、曲率、转弯半径、材质和颜色不属于 Slope DSL。混合请求只表达 Slope 支持的部分。

Slope 规则会读取 `road_data.road_id`、`start_elevation`、`end_elevation` 和 `grade_degree`。其中：

- `start_elevation` 和 `end_elevation` 是端点高程的权威来源。
- `grade_degree` 固定表示 `start → end` 方向的坡角。
- `grade_degree > 0` 表示 `start → end` 上坡。
- `grade_degree < 0` 表示 `start → end` 下坡。
- `grade_degree = 0` 表示平路。

如果 `grade_degree` 与端点高程表达的坡向明显冲突，不能自行修正数据，输出空 `operations`。

### 数据结构

```json
{
  "operations": [
    {
      "operation": "slope",
      "road_id": "string",
      "position": "start | end | both",
      "road_limit": "number | none",
      "change": {
        "attribute": "grade | elevation_delta",
        "mode": "absolute | relative",
        "action": "increase | decrease | set",
        "magnitude": {
          "kind": "numeric | qualitative",
          "value": "number | none",
          "level": "slight | default | significant | none",
          "unit": "percent | degree | m | none"
        }
      }
    }
  ]
}
```

`operation` 固定为 `"slope"`。有效 operation 的字段必须完整。

### road_id

`road_id` 的来源优先级为：

1. 用户明确指定的道路 ID。
2. “当前道路”“这条路”“选中的道路”等表达对应的 `road_data.road_id`。

用户明确 ID 的优先级高于上下文道路。无法确定时输出空 `operations`。

### position

`position` 表示实际被修改或受控的端点：

- `start`：起点
- `end`：终点
- `both`：起点和终点应用同一组水平高度修改参数

#### grade 的 position

`grade` 不允许 `position = "both"`。

用户明确指定端点时：

- 起点、起始端、入口端：`start`
- 终点、结束端、出口端：`end`

用户未指定受控端点时：

- `end_elevation > start_elevation`：`end`
- `start_elevation > end_elevation`：`start`
- 两端相等：`end`

#### elevation_delta 的 position

- 明确修改起点：`start`
- 明确修改终点：`end`
- 明确整条道路、全段或起终点同时等量升降：`both`

### attribute

`attribute` 决定修改的是坡度还是端点高程：

- 坡度、坡角、上坡、下坡、变陡、变缓：`grade`
- 起终点高程、水平高度、抬高、降低、升高、下沉、设置为某个高程、给出端点高程变化量、整条道路整体升降：`elevation_delta`

端点词只决定 `position`，不会单独把 `attribute` 变成 `elevation_delta`。

比如“Adjust the end point to achieve a slightly flatter grade”中：

- `end point` 决定 `position = "end"`。
- `slightly flatter grade` 决定 `attribute = "grade"`、`mode = "relative"`、`action = "decrease"`、`level = "slight"`。
- `adjust` 只是通用修改动词，不是明确的高程修改手段。

只有“调整一下”“优化一下”等通用表达，没有坡度、高程、动作方向或目标语义时，输出空 `operations`。

### mode、action 和 magnitude

#### absolute + set

表示设置不依赖当前值的绝对目标：

- 坡度设为 5%
- 坡角设为 3 度
- 终点高程设为 10 米

#### relative + increase/decrease

表示在当前值基础上变化：

- 坡度增加 20%
- 坡度减少 30%
- 坡角增加 3 度
- 终点抬高 4 米
- 起点降低 2 米

数值 `value` 必须大于 0，方向由 `action` 表示，不能用负数表示增加或减少。

#### relative + set

表示把最终值设置为当前值的指定比例，只允许 `numeric + percent`：

```text
target = current × value / 100
```

“调到当前的一半”对应 `value = 50`。`relative + set + degree` 和 `relative + set + m` 都非法。

#### numeric 与 qualitative

`numeric` 用于用户给出明确数值，或可以从完整输入唯一算出数值的情况：

- `value` 是有限数字。
- `level = "none"`。
- `unit` 是 `percent`、`degree` 或 `m`。
- 不能假设道路长度或其他未提供的数据。

`qualitative` 用于有明确方向和程度但没有数值的情况：

- `value = "none"`。
- `unit = "none"`。
- 程度映射与 Width 相同：`slight`、`default`、`significant`。
- 只说变陡或变缓而没有程度词时，`level = "default"`。

当请求同时表达直接修改手段和预期目的时，直接修改手段决定 `attribute`、`position`、`mode` 和 `action`。预期目的中的程度词可以补充同一 operation 的 `level`，但不能改变已经确定的字段。

### 单位

`grade`：

- “坡度”在没有其他单位说明时默认为 `degree`。
- 百分比、`%`、百分比坡度使用 `percent`。
- 坡度、角度、度使用 `degree`。

`elevation_delta`：

- 绝对高程和米制高程变化使用 `m`。
- 按百分比增减高程使用 `percent`，计算方式见后文。
- 设置为当前高程的指定比例使用 `percent`。
- 只有“抬高”“降低”但没有数字时，可以使用 `qualitative + none`。

### grade 的方向与符号

#### 输入方向和输出方向

`road_data.grade_degree` 始终使用 `start → end` 方向。DSL 中 grade operation 的观察方向由 `position` 决定：

- `position = "start"`：DSL 方向是 `start → end`。
- `position = "end"`：DSL 方向是 `end → start`。

当前坡度换算为：

```text
position = start:
current_position_grade = road_data.grade_degree

position = end:
current_position_grade = -road_data.grade_degree
```

用户要求、当前坡度、目标坡度和 DSL `value` 必须在同一个 `position` 方向中比较。

从端点 A 朝端点 B 观察时：

- `value > 0`：A 到 B 上坡
- `value < 0`：A 到 B 下坡
- `value = 0`：平路

只有有符号的当前坡度和绝对目标坡度需要方向换算。相对增减量和相对比例是无符号量，`position` 不改变它们的正负。

#### 新坡向目标

“将道路设置成 X% 的上坡或下坡”“将道路改成 X 度的上坡或下坡”是在建立新的绝对坡向：

- 用户没有明确固定方向时，上坡或下坡统一以 `start → end` 解释。
- 上坡是 `start → end` 上坡，下坡是 `start → end` 下坡。
- 该请求可以改变或反转当前物理坡向。
- 确定 `start → end` 的目标后，再换算成 `position` 方向的有符号 `value`。

比如目标是 `start → end` 下坡 15%，而 `position = "end"`，DSL 方向是 `end → start`，因此 `value = 15`。

#### 调整当前坡度大小

“将这个下坡设置到 15%”“将当前上坡调整到 5 度”引用当前物理坡面，只调整坡度大小：

- 保持当前高端和低端的关系。
- 不主动反转物理坡向。
- “当前下坡”是从当前高端到低端。
- “当前上坡”是从当前低端到高端。
- 设置用户给出的绝对大小后，再转换到 `position` 方向。

比如当前道路 `start → end` 上坡，当前下坡方向是 `end → start`。用户要求“Set this downhill slope to 15%”，且 `position = "end"`，则 `value = -15`。

不能只根据“上坡”或“下坡”单个词判断规则，要结合是在建立新坡向，还是调整当前坡度大小。

#### 默认方向和默认端点

用户没有明确描述方向或受控端点，并且不属于前两类明确句式时：

- 原道路有坡度：描述方向默认沿原道路上坡方向，`position` 取当前高端。
- 原道路是平路：描述方向默认 `start → end`，`position = "end"`。

用户说“起点的坡度”时，方向是 `start → end`，`position = "start"`。

用户说“终点的坡度”时，方向是 `end → start`，`position = "end"`。

用户只说“从起点到终点上坡或下坡”时，只确定物理坡向。没有明确受控端点时，`position` 仍按默认规则确定。

#### absolute + set 的有符号 value

该规则适用于：

```text
attribute = grade
mode = absolute
action = set
kind = numeric
```

目标是 `start → end` 上坡 5 度时：

- `position = "start"`：`value = 5`
- `position = "end"`：`value = -5`

目标是 `start → end` 下坡 5 度时：

- `position = "start"`：`value = -5`
- `position = "end"`：`value = 5`

不能直接复制用户原文中的正负号。必须先确定描述方向和物理坡向，再换算为相对于 `position` 的 `value`。

用户给出无符号绝对目标但未明确坡向时，按以下优先级确定符号：

1. 用户明确指定 `start` 或 `end`：使用该 `position`，正数目标直接作为该输出方向的正坡度，不继承当前物理坡向。
2. 用户明确要求保持当前坡向：根据 `road_data` 保持当前物理方向，再换算到 `position` 方向。
3. 用户明确表达上坡、下坡或固定道路方向：按完整句式判断新坡向或当前坡度大小调整，再换算。
4. 没有端点也没有坡向：原道路有坡度时保持当前物理坡向；平路默认形成 `start → end` 上坡。`position` 使用默认规则。

### grade 的相对修改

`relative + increase/decrease` 修改坡度或坡角的绝对大小：

- `increase` 表示变陡，增加绝对值。
- `decrease` 表示变缓，减小绝对值。
- 默认保持当前物理坡向。
- `value` 是正的无符号变化量。

#### 按百分比增减

```text
increase:
target_magnitude = current_magnitude × (1 + value / 100)

decrease:
target_magnitude = current_magnitude × (1 - value / 100)
```

`increase` 和 `decrease` 的 `value` 必须大于 0。`decrease` 还必须满足 `value <= 100`。

#### 按角度增减

```text
increase:
target_angle = current_angle + value

decrease:
target_angle = max(current_angle - value, 0)
```

规则：

- `value > 0`。
- 原道路有坡度时保持当前物理坡向。
- `decrease` 不能越过 0 后反转坡向。
- 最终坡角满足 `0 <= angle < 90`。

#### 定性增减

- `increase` 表示变陡。
- `decrease` 表示变缓。
- 默认保持当前物理坡向。
- 定性变缓最多到平路，不能反转坡向。
- `value = "none"`、`unit = "none"`，`level` 按程度词确定。

#### 设置为当前坡度的比例

```text
target_magnitude = current_magnitude × value / 100
```

- `value >= 0`。
- `0` 表示变为平路。
- `50` 表示当前坡度的一半。
- `100` 表示保持不变。
- 大于 `100` 表示更陡。
- 不能用负值反转坡向。

#### 平路特殊规则

平路默认方向是 `start → end`，`position = "end"`。

- “坡度增加 5 度”形成 `start → end` 上坡 5 度。
- “坡度降低 5 度”形成 `start → end` 下坡 5 度。
- 百分比相对增加或降低仍生成 DSL，具体结果由执行代码处理。
- 定性变缓没有实际效果，输出空 `operations`。
- 定性变陡有效，默认形成 `start → end` 上坡。
- `relative + set + percent` 仍生成 DSL，具体结果由执行代码处理。

平路的数值降低不使用 `max(current_angle - value, 0)` 公式，平路特殊规则优先。

### elevation_delta 的语义

#### 按米升降

`relative + increase/decrease + numeric + m`：

- `position = "start"`：只修改起点。
- `position = "end"`：只修改终点。
- `position = "both"`：两端增加或减少相同米数。
- `increase` 表示抬高，`decrease` 表示降低。
- `value > 0`。

#### 按百分比升降单个端点

`relative + increase/decrease + numeric + percent` 先根据当前高程绝对值计算无符号变化量，再由 `action` 决定方向：

```text
amount = abs(current_elevation) × value / 100

increase:
target_elevation = current_elevation + amount

decrease:
target_elevation = current_elevation - amount
```

这不是把带符号当前高程直接乘以 `1 ± value / 100`。当前高程为负数也不会反转 `increase` 或 `decrease` 的含义。

当前端点高程为 `-5` 米，`increase 9%` 时：

```text
amount = abs(-5) × 9 / 100 = 0.45
target = -5 + 0.45 = -4.55
```

#### 按百分比升降两个端点

`position = "both"` 时使用共同基数：

```text
base = abs(end_elevation - start_elevation)
```

当前高程差为 0 时，`base = 10` 米。

```text
amount = base × value / 100
```

两个端点分别增加或减少同一个 `amount`，因此通常保持原高程差不变。

#### 设置绝对高程

`absolute + set + numeric + m`：

- `start`：设置起点最终高程。
- `end`：设置终点最终高程。
- `both`：把起点和终点设置为同一个最终绝对高程。

#### 设置为当前高程的比例

`relative + set + numeric + percent`：

```text
target_elevation = current_elevation × value / 100
```

- `value >= 0`。
- `start` 以当前起点高程为基准。
- `end` 以当前终点高程为基准。
- `both` 让两个端点分别以各自当前高程为基准应用相同比例。

`position = "both"` 时，两个端点使用相同 change 参数，但不保证产生相同米数的变化，也不表示把两端设置为同一高程。

#### position = both 的完整对照

| 组合 | 两个端点的语义 |
|---|---|
| `relative + increase/decrease + m` | 两端变化相同米数 |
| `absolute + set + m` | 两端设置为相同绝对高程 |
| `relative + set + percent` | 两端分别按各自当前高程应用相同比例 |
| `relative + increase/decrease + percent` | 用当前高程差绝对值作为共同基数，两端变化相同米数 |

`both` 只适用于 `elevation_delta`。

### Slope 合法字段组合

#### grade

| `mode` | `action` | `kind` | `unit` |
|---|---|---|---|
| `absolute` | `set` | `numeric` | `percent` |
| `absolute` | `set` | `numeric` | `degree` |
| `relative` | `increase` | `numeric` | `percent` |
| `relative` | `decrease` | `numeric` | `percent` |
| `relative` | `increase` | `numeric` | `degree` |
| `relative` | `decrease` | `numeric` | `degree` |
| `relative` | `set` | `numeric` | `percent` |
| `relative` | `increase` | `qualitative` | `none` |
| `relative` | `decrease` | `qualitative` | `none` |

#### elevation_delta

| `mode` | `action` | `kind` | `unit` |
|---|---|---|---|
| `absolute` | `set` | `numeric` | `m` |
| `relative` | `increase` | `numeric` | `m` |
| `relative` | `decrease` | `numeric` | `m` |
| `relative` | `increase` | `qualitative` | `none` |
| `relative` | `decrease` | `qualitative` | `none` |
| `relative` | `set` | `numeric` | `percent` |
| `relative` | `increase` | `numeric` | `percent` |
| `relative` | `decrease` | `numeric` | `percent` |

补充约束：

- `grade` 不能使用 `position = "both"`。
- `numeric.value` 必须是有限数字。
- 数值 `increase/decrease` 的 `value > 0`。
- `grade + absolute + set + degree` 满足 `-90 < value < 90`。
- `relative + set` 只允许 `percent`，且 `value >= 0`。
- `elevation_delta + absolute + set` 只允许 `m`。
- `qualitative.value` 和 `unit` 必须为 `"none"`。
- `numeric.level` 必须为 `"none"`。

### Slope 多 operation 与空 operations

下列情况拆成多个 operation：

- 道路不同
- `attribute` 不同
- 位置不同，并且动作、模式、数值、单位或程度不同
- 用户明确要求多个按顺序执行的独立修改

起点和终点执行完全相同的 `elevation_delta` 时，合并成一个 `position = "both"` 的 operation。多个 operation 按用户表达顺序排列。

以下情况输出空 `operations`：

- 请求不属于 Slope DSL
- 无法确定 `road_id`
- 无法确定必要的 `position`
- 无法确定 `attribute`
- 请求内部冲突
- 缺少形成唯一操作所需的信息
- 请求过于模糊，无法确定实际修改动作
- `grade_degree` 和端点高程表达的坡向明显冲突

Slope 规则优先级由高到低：

1. 用户明确指定的道路、受控端点、方向、动作和数值
2. 字段合法性与组合约束
3. `attribute` 专属规则
4. `position` 默认规则
5. 坡向默认规则
6. 其他默认规则
7. 示例

通用规则和 `attribute` 专属规则冲突时，以 `attribute` 专属规则为准。

### Slope 完整示例

用户要求：“把 road_7 的终点抬高 4 米，只改当前道路。”

```json
{
  "operations": [
    {
      "operation": "slope",
      "road_id": "road_7",
      "position": "end",
      "road_limit": 1,
      "change": {
        "attribute": "elevation_delta",
        "mode": "relative",
        "action": "increase",
        "magnitude": {
          "kind": "numeric",
          "value": 4,
          "level": "none",
          "unit": "m"
        }
      }
    }
  ]
}
```

## Curve DSL

### 支持范围

Curve DSL 支持：

- 设置整体弯道为 C-shape 或 S-shape
- 修改左转、右转、先左后右或先右后左语义
- 修改入弯侧、出弯侧或整体弯道的曲率、半径和弯曲强度
- 设置或移动入弯侧、出弯侧的弯折位置
- 整体提前或推后弯道
- 调整 S 型弯两个弯之间的间距
- 根据 `p_s` 和 `p_e` 做单侧对称性修正
- 限制修改传播的道路数量

宽度、坡度、高程、车道数量、材质和颜色不属于 Curve DSL。混合请求只表达 Curve 支持的部分。

### 输入数据与内部模型

```json
{
  "road_id": "road_30",
  "current_shape": "C-shape",
  "semantic": "right",
  "p_s": 0.35,
  "p_e": 0.42
}
```

字段含义：

- `road_id`：当前选中道路 ID
- `current_shape`：`"C-shape"` 或 `"S-shape"`
- `semantic`：`"left"`、`"right"`、`"left_right"` 或 `"right_left"`
- `p_s`：起点侧内部控制位置距起点的归一化距离
- `p_e`：终点侧内部控制位置距终点的归一化距离

内部曲线使用三阶贝塞尔模型：

- `p0`、`p3` 是固定端点。
- `p1` 对应 `modification[0]`。
- `p2` 对应 `modification[1]`。

以下是内部字段，不能出现在最终 DSL 中：

```text
control_point
point_id
index
p0
p1
p2
p3
p_s
p_e
```

`p_s` 和 `p_e` 只用于对称性和位置合法性判断，不能因为输入中存在这两个值就自动生成修改。

### 数据结构

```json
{
  "operations": [
    {
      "operation": "curve",
      "road_id": "string",
      "road_limit": "number | none",
      "shape": {
        "target": "keep | C-shape | S-shape"
      },
      "semantic": {
        "semantic_change": true,
        "target": "keep | left | right | left_right | right_left"
      },
      "modification": [
        {
          "centroid_shift": {
            "change": {
              "action": "move_to_start | move_to_end | set | none",
              "mode": "absolute | relative | none",
              "magnitude": {
                "kind": "numeric | qualitative | none",
                "value": "number | none",
                "level": "slight | default | significant | none",
                "unit": "m | percent | none"
              }
            }
          },
          "curvature_peak": {
            "value_type": "curvature | radius | none",
            "change": {
              "mode": "absolute | relative | none",
              "action": "increase | decrease | set | none",
              "magnitude": {
                "kind": "numeric | qualitative | none",
                "value": "number | none",
                "level": "slight | default | significant | none",
                "unit": "curvature | m | percent | none"
              }
            }
          }
        },
        {
          "centroid_shift": {
            "change": {
              "action": "move_to_start | move_to_end | set | none",
              "mode": "absolute | relative | none",
              "magnitude": {
                "kind": "numeric | qualitative | none",
                "value": "number | none",
                "level": "slight | default | significant | none",
                "unit": "m | percent | none"
              }
            }
          },
          "curvature_peak": {
            "value_type": "curvature | radius | none",
            "change": {
              "mode": "absolute | relative | none",
              "action": "increase | decrease | set | none",
              "magnitude": {
                "kind": "numeric | qualitative | none",
                "value": "number | none",
                "level": "slight | default | significant | none",
                "unit": "curvature | m | percent | none"
              }
            }
          }
        }
      ]
    }
  ]
}
```

结构约束：

- 顶层只包含 `operations`。
- `operation` 固定为 `"curve"`。
- `modification` 必须严格包含两个完整对象。
- `modification[0]` 对应起点侧。
- `modification[1]` 对应终点侧。
- 未修改部分也要输出完整 `none` 结构。
- 不能使用 `null` 或未定义字段。

### road_id

`road_id` 的来源优先级为：

1. 用户明确指定的道路 ID。
2. `road_data.road_id`。

`road_12`、`12号道路`、`12号路` 都规范化为 `"road_12"`。“当前道路”“这条路”“选中的道路”使用 `road_data.road_id`。无法确定时输出空 `operations`。

### shape

`shape.target`：

- 未要求修改形态：`"keep"`
- 改成 C 型：`"C-shape"`
- 改成 S 型：`"S-shape"`

### semantic

明确语义目标的映射：

| 请求 | `semantic_change` | `target` |
|---|---|---|
| 未修改或保持语义 | `false` | `keep` |
| 改成左转 | `true` | `left` |
| 改成右转 | `true` | `right` |
| 改成先左后右 | `true` | `left_right` |
| 改成先右后左 | `true` | `right_left` |

`semantic_change` 必须是 JSON 布尔值：

- `false` 时，`target` 必须是 `"keep"`。
- `true` 时，`target` 不能是 `"keep"`。

用户在有效 Curve 修改中明确表达“允许语义变化”时，`semantic_change = true`。

#### shape 改变时推导 semantic

用户修改 shape 但没有给出目标 semantic 时，根据当前语义推导：

目标为 S-shape：

| 当前语义 | 目标语义 |
|---|---|
| `left` | `left_right` |
| `right` | `right_left` |
| `left_right` | `left_right` |
| `right_left` | `right_left` |

目标为 C-shape：

| 当前语义 | 目标语义 |
|---|---|
| `left_right` | `left` |
| `right_left` | `right` |
| `left` | `left` |
| `right` | `right` |

S-shape 转 C-shape 时保留进入弯道后的第一个转向方向。该推导产生具体的非 `keep` target，因此同时满足 `semantic_change = true`。

如果存在其他有效 Curve 修改，用户允许语义变化，但没有修改 shape，也没有指定目标 semantic：

```json
{
  "semantic_change": true,
  "target": "road_data.semantic 的当前值"
}
```

如果请求只有“允许语义变化”，没有形态、目标语义、位置、曲率、半径、间距、对称性或其他实际目标，输出空 `operations`。无法获得合法 semantic 时也输出空数组。

只修改 shape 或 semantic 时，两个 `modification` 都使用完整 `none` 结构。

### modification 对象映射

`modification[0]` 对应：

- 起点侧
- 入弯侧
- 前半段
- 前面的弯
- 第一个弯
- 第一段弯
- 左边的弯

`modification[1]` 对应：

- 终点侧
- 出弯侧
- 后半段
- 后面的弯
- 第二个弯
- 第二段弯
- 右边的弯

“左边的弯”表示 `modification[0]`，“往左移动”表示向道路起点移动。相应地，“右边的弯”表示 `modification[1]`，“往右移动”表示向道路终点移动。

“整体弯道”“整个弯”“整段弯道”“弯道整体”“整体曲率或半径”“整体弯道位置”“主要弯折位置”以及未区分入弯侧和出弯侧的修改，通常覆盖两个 `modification`。

对象范围不会自动补出动作。“调整一下整体弯道位置”没有方向、幅度或最终位置，不构成可执行修改。

### centroid_shift

`centroid_shift` 只表示弯折位置沿道路起点到终点方向的移动，不表示横向移动或地图坐标方向。

方向映射：

| 自然语言 | `action` |
|---|---|
| 往起点、往前、往左、提前、早点转、出现得更早 | `move_to_start` |
| 往终点、往后、往右、推后、晚点转、出现得更晚 | `move_to_end` |

#### 相对移动

| 移动类型 | `mode` | `action` | `kind` | `value` | `level` | `unit` |
|---|---|---|---|---|---|---|
| 按米 | `relative` | `move_to_start/end` | `numeric` | 正数 | `none` | `m` |
| 按百分比 | `relative` | `move_to_start/end` | `numeric` | 百分数 | `none` | `percent` |
| 定性移动 | `relative` | `move_to_start/end` | `qualitative` | `none` | 程度枚举 | `none` |

程度映射：

- 稍微、略微、一点、小幅：`slight`
- 一些、适当、普通调整或无程度词：`default`
- 明显、大幅、显著、很多：`significant`

#### 设置最终位置

明确给出最终位置时使用：

```text
absolute + set + numeric + m/percent
```

位置基准：

- `modification[0]` 以道路起点为基准。
- `modification[1]` 以道路终点为基准。

示例：

- “第一个弯设置在距起点 30 米处”：`modification[0] + absolute + set + 30 + m`
- “第二个弯设置在距终点 20% 处”：`modification[1] + absolute + set + 20 + percent`

用道路全长百分比从相反端点描述时可以换算：

```text
modification[0] 距终点 q%:
value = 100 - q

modification[1] 距起点 q%:
value = 100 - q
```

米制位置从相反端点描述时需要道路总长度。总长度缺失时输出空 `operations`。

只给一个整体最终位置，但不能分别确定两个 `modification` 的最终位置时，输出空数组。

#### C-shape 的位置顺序约束

本次 operation 的最终形态为 C-shape 时，起点侧控制位置不能晚于终点侧控制位置：

```text
p_s + p_e <= 1
```

对于 `absolute + set + percent`：

- `modification[0].value` 是修改后的 `p_s` 百分值。
- `modification[1].value` 是修改后的 `p_e` 百分值。
- 两侧都设置时，两个 `value` 之和必须小于等于 100。
- 只设置一侧时，使用另一侧当前 `p_s` 或 `p_e` 检查。

用户的最终位置必然造成控制位置交叉时，输出空 `operations`。

相对或定性修改无法唯一算出新位置时，提取仍需选择不会必然导致交叉的对象和方向。最终几何优化或执行过程必须把结果限制在 `p_s + p_e <= 1` 内，不能为满足其他目标放宽该约束。

### curvature_peak

用户没有明确说“半径”时，默认 `value_type = "curvature"`。

语义映射：

| 自然语言 | `value_type` | `action` | 几何效果 |
|---|---|---|---|
| 更急、更弯、更鼓、更明显 | `curvature` | `increase` | 曲率增大 |
| 更缓、不那么弯、更平顺 | `curvature` | `decrease` | 曲率减小 |
| 半径增大 | `radius` | `increase` | 弯道变缓 |
| 半径减小 | `radius` | `decrease` | 弯道变急 |

不能把“变急”提取为 `radius increase`，也不能把“变缓”提取为 `radius decrease`。

#### 设置最终值

- 最终曲率：`curvature + absolute + set + numeric + curvature`
- 最终半径：`radius + absolute + set + numeric + m`

#### 相对修改

- 曲率按数值增减：`curvature + relative + increase/decrease + numeric + curvature`
- 半径按米增减：`radius + relative + increase/decrease + numeric + m`
- 曲率或半径按百分比增减：`curvature/radius + relative + increase/decrease + numeric + percent`
- 设置为当前值的比例：`curvature/radius + relative + set + numeric + percent`
- 定性增减：`curvature/radius + relative + increase/decrease + qualitative + none`

比例设置中，`80` 表示当前值的 80%，`50` 表示一半，`200` 表示两倍。

数值和定性约束：

- `numeric.value` 必须是有限数字，`level = "none"`。
- `qualitative.value` 和 `unit` 必须为 `"none"`，`level` 不能为 `"none"`。
- 数值增加、减少和移动的 `value` 必须为正数。
- 方向不能编码为负数。
- 百分之二十写成 `20`，不写成 `0.2`。

### 整体弯道调整

#### C-shape 的整体解释

当前形态是 C-shape，或用户同时要求改成 C-shape 时，两个 `modification` 是同一个 C 型弯道的起点侧和终点侧，不能当作两个独立弯道。

对 `centroid_shift`：

- 两侧同向移动表示整个 C 型弯沿道路方向提前或推后。
- 两侧反向移动可以改变同一个 C 型弯的纵向范围或整体形态，但必须来自用户明确指定的两侧方向，或明确且唯一的整体形态语义。
- C-shape 的反向移动不能自动解释成 S 型弯的两个弯间距调整。
- 同向或反向移动都不会自动改变 `shape.target`。

“将弯道两端向外突出”表示扩大 C 型弯两侧弯折位置之间的纵向范围：

```text
modification[0].centroid_shift.action = move_to_start
modification[1].centroid_shift.action = move_to_end
```

该表达只修改两个 `centroid_shift`，不修改 `curvature_peak`、shape 或 semantic。没有幅度或程度词时，两侧都使用 `relative + qualitative + default + none`。

整个 C 型弯统一变急、变缓、增大半径或减小半径时，同时修改两个 `curvature_peak`，表示同一弯道两侧的强度调整。

#### 整体平移

| 请求 | `modification[0]` | `modification[1]` |
|---|---|---|
| 整体提前或向起点移动 | `move_to_start` | `move_to_start` |
| 整体推后或向终点移动 | `move_to_end` | `move_to_end` |

整体平移不会自动修改 `curvature_peak`、shape 或 semantic。

### S-shape 的两个弯间距

间距调整只在当前道路为 S-shape，或用户同时要求改成 S-shape 时有效。

| 请求 | `modification[0]` | `modification[1]` |
|---|---|---|
| 增大间距 | `move_to_start` | `move_to_end` |
| 减小间距 | `move_to_end` | `move_to_start` |

两个 `modification` 反向移动。没有幅度时，两侧使用：

```text
relative + qualitative + default + none
```

间距修改不会自动修改 `curvature_peak`。不满足 S-shape 条件时输出空 `operations`。

### 对称性修正

“对称”“更对称”“匀称”“两边一样”“入弯和出弯一致”触发对称性修正。`p_s` 和 `p_e` 必须合法。

最终形态按以下顺序确定：

1. `shape.target = "C-shape"` 时使用 C-shape。
2. `shape.target = "S-shape"` 时使用 S-shape。
3. `shape.target = "keep"` 时使用 `road_data.current_shape`。

计算：

```text
difference = abs(p_s - p_e)
```

`difference < 0.05` 时，当前道路已基本满足对称要求，两个 `centroid_shift` 都使用完整 `none`，不生成位置修改。

最终形态为 C-shape 时，还要先检查当前数据满足：

```text
p_s + p_e <= 1
```

当前 C 型弯不满足该条件时，不能用 `p_s` 和 `p_e` 生成对称性修正。

#### C-shape 的单侧修正规则

当 `p_s < p_e`：

1. 优先增大 `p_s`，只修改 `modification[0]`，`action = "move_to_end"`。
2. 该方案只在 `2 × p_e <= 1` 时允许。
3. 如果 `2 × p_e > 1`，改为减小 `p_e`，只修改 `modification[1]`，`action = "move_to_end"`。

当 `p_s > p_e`：

1. 优先增大 `p_e`，只修改 `modification[1]`，`action = "move_to_start"`。
2. 该方案只在 `2 × p_s <= 1` 时允许。
3. 如果 `2 × p_s > 1`，改为减小 `p_s`，只修改 `modification[0]`，`action = "move_to_start"`。

#### S-shape 的单侧修正规则

最终形态为 S-shape，且当前 `p_s + p_e >= 1` 时，不应用 C-shape 的 `p_s + p_e <= 1` 限制：

- `p_s < p_e`：只修改 `modification[0]`，`action = "move_to_end"`。
- `p_s > p_e`：只修改 `modification[1]`，`action = "move_to_start"`。

#### 对称性修改参数

对称性修正固定使用：

```text
mode = relative
kind = qualitative
value = none
unit = none
```

`level` 根据差值确定：

| `difference` | `level` |
|---:|---|
| `0.05 <= difference < 0.25` | `slight` |
| `0.25 <= difference <= 0.40` | `default` |
| `difference > 0.40` | `significant` |

对称性只允许修改一个 `modification`，不会自动修改 `curvature_peak`、shape 或 semantic。选择的方向必须减小 `abs(p_s - p_e)`，并在 C-shape 下保持 `p_s + p_e <= 1`。

### Curve 的完整 none 结构

未使用的 `centroid_shift`：

```json
{
  "centroid_shift": {
    "change": {
      "action": "none",
      "mode": "none",
      "magnitude": {
        "kind": "none",
        "value": "none",
        "level": "none",
        "unit": "none"
      }
    }
  }
}
```

未使用的 `curvature_peak`：

```json
{
  "curvature_peak": {
    "value_type": "none",
    "change": {
      "mode": "none",
      "action": "none",
      "magnitude": {
        "kind": "none",
        "value": "none",
        "level": "none",
        "unit": "none"
      }
    }
  }
}
```

### Curve 合法字段组合

#### centroid_shift

| 编号 | 合法组合 |
|---:|---|
| 1 | 完整 `none` |
| 2 | `relative + move_to_start/move_to_end + numeric + m` |
| 3 | `relative + move_to_start/move_to_end + numeric + percent` |
| 4 | `relative + move_to_start/move_to_end + qualitative + none` |
| 5 | `absolute + set + numeric + m` |
| 6 | `absolute + set + numeric + percent` |

`absolute + set + percent` 必须满足：

```text
0 < value < 100
```

#### curvature_peak

| 编号 | 合法组合 |
|---:|---|
| 1 | 完整 `none` |
| 2 | `curvature + absolute + set + numeric + curvature` |
| 3 | `radius + absolute + set + numeric + m` |
| 4 | `curvature + relative + increase/decrease + numeric + curvature` |
| 5 | `radius + relative + increase/decrease + numeric + m` |
| 6 | `curvature/radius + relative + increase/decrease + numeric + percent` |
| 7 | `curvature/radius + relative + set + numeric + percent` |
| 8 | `curvature/radius + relative + increase/decrease + qualitative + none` |

未列出的组合全部非法。

### Curve 多 operation 规则

同一道路上互不冲突的 shape、semantic、位置和弯曲强度修改合并到同一个 operation。

以下情况拆成多个 operation：

- 道路不同
- `road_limit` 不同
- 用户明确要求按顺序执行多个独立修改
- 同一字段存在不能合并的顺序修改

多个 operation 按用户表达顺序排列，不能遗漏、增加、重复或错误合并修改。

### Curve 提取顺序

1. 判断是否存在有效 Curve 修改。
2. 确定 `road_id` 和 `road_limit`。
3. 提取 shape 和 semantic。
4. 确定 `modification` 修改对象。
5. 根据当前或目标 shape 判断两个 `modification` 是同一个 C 型弯道的两侧，还是 S 型弯中的前后两个弯。
6. 判断位置、弯曲强度、间距或对称性。
7. 提取 `action`、`mode`、`kind`、`value`、`level` 和 `unit`。
8. 执行必要的位置、semantic 或对称性推导。
9. 合并兼容修改，拆分独立修改。
10. 补全所有 `none` 结构。
11. 校验字段和组合。
12. 输出严格 JSON。

### Curve 空 operations 条件

以下情况输出空 `operations`：

- 请求不属于 Curve DSL
- 无法确定 `road_id`
- 只有“调整”“优化”等模糊表达
- 只有“允许语义变化”，没有实际目标
- 只有“调整一下整体弯道位置”等范围描述，没有方向、幅度或最终位置
- 没有明确形态、语义、位置、曲率、半径、间距或对称性目标
- 请求冲突，无法得到唯一操作
- 依赖的数据缺失
- 直接要求修改内部参数且不能转换为 DSL
- 非 S 型道路要求调整两个弯的间距
- 对称性修改缺少 `p_s` 或 `p_e`
- 绝对位置无法换算
- 整体最终位置不能唯一确定两个 `modification`
- 允许语义变化，但无法确定合法目标 semantic
- C-shape 的明确最终位置必然造成控制位置交叉

### Curve 规则优先级

规则按以下优先级处理：

1. 用户明确指定的目标、动作、数值、单位和传播范围
2. 用户明确指定的 shape 和 semantic
3. “允许语义变化”规则
4. `centroid_shift` 的绝对 `set`
5. C 型弯的整体解释
6. S 型弯间距规则
7. 单侧对称性规则
8. 整体平移规则
9. `modification` 对象映射
10. 曲率和半径语义
11. `road_data` 推导和默认规则
12. 示例

冲突处理：

- 对称性和普通平移冲突时，以对称性规则为准。
- 间距和整体平移冲突时，以间距规则为准。
- 当前形态或目标形态为 C-shape 时，不能只因为两个 `modification` 反向移动就解释成 S 型弯间距调整。
- 正文规则与示例冲突时，以正文规则为准。

### Curve 完整示例

用户要求：“让 road_30 的整个 C 型弯稍微变缓，不限制传播数量。”当前 shape 和 semantic 分别为 `C-shape`、`right`。

```json
{
  "operations": [
    {
      "operation": "curve",
      "road_id": "road_30",
      "road_limit": "none",
      "shape": {
        "target": "keep"
      },
      "semantic": {
        "semantic_change": false,
        "target": "keep"
      },
      "modification": [
        {
          "centroid_shift": {
            "change": {
              "action": "none",
              "mode": "none",
              "magnitude": {
                "kind": "none",
                "value": "none",
                "level": "none",
                "unit": "none"
              }
            }
          },
          "curvature_peak": {
            "value_type": "curvature",
            "change": {
              "mode": "relative",
              "action": "decrease",
              "magnitude": {
                "kind": "qualitative",
                "value": "none",
                "level": "slight",
                "unit": "none"
              }
            }
          }
        },
        {
          "centroid_shift": {
            "change": {
              "action": "none",
              "mode": "none",
              "magnitude": {
                "kind": "none",
                "value": "none",
                "level": "none",
                "unit": "none"
              }
            }
          },
          "curvature_peak": {
            "value_type": "curvature",
            "change": {
              "mode": "relative",
              "action": "decrease",
              "magnitude": {
                "kind": "qualitative",
                "value": "none",
                "level": "slight",
                "unit": "none"
              }
            }
          }
        }
      ]
    }
  ]
}
```

## 提取和校验时的最终检查

输出前检查以下内容：

- 顶层是否只包含 `operations`
- `operations` 是否为数组
- 每个 operation 是否使用正确的固定 `operation` 值
- `road_id`、目标位置或车道是否可以从用户请求和 `road_data` 唯一确定
- `road_limit` 是否为大于等于 1 的整数或 `"none"`
- 字段是否完整，是否混入未定义字段
- `numeric` 和 `qualitative` 的 `value`、`level`、`unit` 是否互相一致
- 数值增减是否使用正 `value`，方向是否只由 `action` 表达
- 百分数是否按 `20` 表示 20%，而不是 `0.2`
- Width 和 Slope 是否只使用各自列出的合法组合
- Curve 是否始终包含两个完整 `modification`，未修改部分是否补全 `none`
- 是否错误合并了参数不同的操作，或拆散了可以合并的相同操作
- 是否增加了用户没有要求的修改
- 无效或无法唯一确定的请求是否返回空 `operations`

各定义的规则优先级都把正文规则放在示例之前。示例与正文冲突时，以正文规则为准。
