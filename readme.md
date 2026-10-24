# 自然语言驱动的 OpenDRIVE 地图编辑与评测

将自然语言道路修改需求转为 RoadEdit DSL、工具调用和内部命令，支持车道宽度、坡度及曲线修改，并提供单智能体与多智能体实验、Trace 审核和场景可视化。

## 源码目录

保留本地工作区的相对目录关系，便于实验脚本找到编辑器和配套工具：

```text
.
├── 完全版本/完全版本/
│   ├── openDriveMapEditor/   # 地图编辑器、智能体、Trace 审核器及测试
│   └── ResultAna/            # 场景可视化和车辆分析
└── experiment_runs/          # 批次运行、复跑、合并和独立审查脚本
```

本次源码整理将原先位于仓库根目录的编辑器移至上述路径；原有 Git 历史保留。原编辑器说明见 `完全版本/完全版本/openDriveMapEditor/readme.md`，场景网站说明见 `完全版本/完全版本/ResultAna/README.md`。

## 本地运行

先创建 Python 虚拟环境，并分别参考两个子项目的 `requirements.txt` 安装依赖。

编辑器和 Trace 审核器在编辑器目录启动：

```powershell
cd 完全版本/完全版本/openDriveMapEditor
python main.py
python testfucs/agent_trace_viewer/server.py --port 8730
```

Trace 审核器地址为 `http://127.0.0.1:8730/`。审核器需要用户在本地准备请求测试数据库；可参考 `testfucs/test_data_store.py` 的数据库迁移和数据访问实现。本仓库不提供已审核的实验数据库，也不会在启动时自动恢复本地实验答案。

场景可视化在其自身目录启动：

```powershell
cd 完全版本/完全版本/ResultAna
python -m visualization.server
```

场景网站默认地址为 `http://127.0.0.1:8765/`，使用者自行导入配套 XODR 和场景 JSON。

实验脚本保留原工作区的输入和路径约定，运行前需自行准备实验输入、数据库以及 `完全版本/完全版本/SCTrans-data/SCTrans-data/` 数据目录。部分批次脚本是历史数据修复脚本，并非通用入口；运行前应查看其中的批次名、任务编号和备份逻辑。当前主批次入口为 `experiment_runs/run_real_batches.py`。

## API 配置与发布范围

`.env.example` 只包含占位符。按所用入口将配置复制到本地相应 `.env` 文件，例如真实道路实验读取 `openDriveMapEditor/testfucs/.env` 中的 `DEEPSEEK_API_KEY`。密钥由用户自行提供，不得提交。

提交内容为代码、测试、提示词及说明文档。不包含地图与场景数据、请求及审核数据库、实验 trace 和结果、历史备份、虚拟环境或模拟器。原有本地文件不会因本次发布被删除。数据库和运行结果须由使用者自行保管。

`done` 表示执行完成，不等于人工审核通过。当前编辑器保留原有宽度系数上下限及传播行为，Trace 中预测宽度与实际地图可能不同，应以具体实验的验收口径为准。
