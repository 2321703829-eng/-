import json
import math
import sys
import time
from pathlib import Path

from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
)
from langchain_core.output_parsers import StrOutputParser

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TESTFUCS_DIR = Path(__file__).resolve().parent
if str(TESTFUCS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTFUCS_DIR))

from llm.agent_tools import escape_langchain_template_text, get_model
from llm.manager_agent import ManagerAgent
from testfucs import test_data_store
from td import all_user_cases
from utils import agent_trace

DATABASE_PATH = test_data_store.DEFAULT_DATABASE_PATH
DRAFT_LOG_PATH = Path(__file__).resolve().parent / "run_data" / "LLMjudge_draft.jsonl"
ROAD_TYPES = (
    "上坡路",
    "下坡路",
    "平路",
    "C型-左转弯",
    "C型-右转弯",
    "S型-先左后右",
    "S型-先右后左",
)
ROAD_TYPES_BY_COMMAND_TYPE = {
    "slope": ROAD_TYPES[:3],
    "curve": ROAD_TYPES[3:],
}


judge_prompt="""
    你是一个专业的道路几何分析助手。

    用户会输入一条针对某段道路的自然语言修改请求。你需要通过理解该请求的**空间含义**，推理出被修改的道路在修改之前是什么形态，并返回原始道路类型。

    ---

    ## 背景知识：七种原始道路类型

    在这个系统中，所有道路都属于以下七种基本形态之一：

    1. **上坡路** — 道路沿行进方向持续升高，起点低、终点高，整体呈正坡度。
    2. **下坡路** — 道路沿行进方向持续降低，起点高、终点低，整体呈负坡度。
    3. **平路** — 道路水平延伸，无坡度变化。
    4. **C型-左转弯** — 道路为单一弯道，整体弯曲方向朝左（从起点看向终点时向左偏转）。
    5. **C型-右转弯** — 道路为单一弯道，整体弯曲方向朝右。
    6. **S型-先左后右** — 道路含两段连续弯道，先向左弯，再向右弯。
    7. **S型-先右后左** — 道路含两段连续弯道，先向右弯，再向左弯。

    ---

    ## 你的思考方式

    不要做关键词匹配。请按以下步骤进行空间推理：

    **第一步：理解请求在做什么**
    把请求翻译成一个具体的几何操作：用户想对道路的哪个部分施加什么样的空间变化？
    例如："把道路的整体高程降低" / "把弯道最弯的那个点往起点方向挪" / "让第二个弯道变平缓"

    **第二步：这个操作有意义的前提是什么**
    一个修改操作只有在原始道路具备某种结构时才有意义。
    例如：
    - "调整坡度陡缓"这个操作，前提是道路本来就有坡度 → 原路是上坡或下坡
    - "把弯道顶点移向起点"这个操作，前提是道路本来就有一个单一弯道及其顶点 → 原路是C型弯道
    - "让第二个弯道更平缓"这个操作，前提是道路本来就有两个弯道 → 原路是S型
    - "把左转弯改成右转弯"，说明原始路本来就是一个左转弯 → C型-左转弯

    **第三步：缩小可能的原始路型范围**
    结合操作的具体细节进一步判断：
    - 如果是坡度类操作，思考：操作会让坡度变陡还是变平？操作的是起点还是终点？这些能帮助判断原路偏向上坡还是下坡。但若操作是"整体抬升/降低"而不改变坡度本身，三种坡度路型均有可能。
    - 如果是弯道类操作但没有明确说左/右转向，则C型左右均有可能；若明确提到转向方向，则可以确定。
    - 如果操作涉及两个弯道，则是S型；若进一步说明了"先右后左"或"先左后右"的结构，则可以确定具体类型。

    **第四步：给出判断**
    - 能确定唯一类型 → 直接给出
    - 存在合理歧义（如操作本身不含足够信息区分左右）→ 列出所有合理的可能类型，并解释为什么无法进一步区分
    - 如果坡度类操作无法区分上坡/下坡/平路，则默认选择”上坡路“
    - 如果是C型且无法进一步区分则默认“右转弯”
    - 如果是S型且无法进一步区分则默认“先右后左”类型
    ---

    ## 输出格式
    最终只需要输出唯一确定的原始道路类型，不需要输出理解请求和推理过程。
    ---

    ## 示例

    输入：Raise the start point of Road 61 resulting in a noticeably steeper grade.
    思考过程：
        【理解请求】
        抬高道路的起点，使整体坡度变得更陡。
        【推理过程】
        这个操作的前提是道路原本就有坡度——只有存在坡度的道路，"让坡度变陡"才有意义。
        抬高起点会让起点相对终点更高，这会加剧一条上坡路的坡度（起点更高→行进方向更陡），
        或者减缓一条下坡路的坡度（起点抬高→下坡变平）。
        但请求说结果是"变陡"，说明抬高起点后坡度增加，这符合下坡路的逻辑
        （起点抬高→起点和终点高差增大→下坡更陡），也符合上坡路坡度加大的场景。
        进一步思考：上坡路起点低终点高，抬高起点会让起点接近终点高度，坡度反而减小；
        下坡路起点高终点低，抬高起点会让高差增大，坡度变陡。
        因此"抬高起点→坡度变陡"更符合下坡路的行为。

    output:下坡路

    ---

    输入：Modify Road 61 to turn right, then left.
    思考过程：
        【理解请求】
        将道路修改为"先右转、再左转"的双弯结构。
        【推理过程】
        请求描述的是修改后的结果形态：先右后左的双弯道。
        这是一个形态重构操作，而非对已有弯道的微调。
        但要思考：为什么系统要把这个操作与某种原始路绑定？
        最合理的解释是：原始路已经是一条S型道路（有两段弯），
        现在只是调整两段弯的方向顺序——从"先左后右"改为"先右后左"，
        或者原路就是某种双弯结构被整体翻转。
  
    output:S型-先左后右

    ---

    输入：Could you decrease the curvature of the peak-curvature segment on Road 61?
    思考过程：
        【理解请求】
        降低道路上曲率最大那一段的弯曲程度，让弯道变得更平缓。
        【推理过程】
        "曲率最大的路段（peak-curvature segment）"这一概念只在单弯道上有明确意义——
        它指的是C型弯道中弯曲最剧烈的核心区域（即弯道顶点附近的路段）。
        S型道路有两个弯，会有两个局部曲率峰值，不会单独称某一段为"the peak-curvature segment"。
        因此，这个操作的前提是道路有且仅有一个弯道顶点区域，即C型弯道。
        请求未指明转弯方向（左/右），默认使用右。

    output:C型-右转弯

    

---
"""
def get_fake_road_context(road_type):
    # road_id = str(road_id)
    # formatted_road_id = road_id if road_id.startswith("road_") else f"road_{road_id}"

    def slope_info(start_elevation, end_elevation):
        length = 50.0
        height_delta = end_elevation - start_elevation
        grade_fraction = 0.0 if length == 0 else height_delta / length
        return {
            # "road_id": formatted_road_id,
            "length": length,
            "start_elevation": start_elevation,
            "end_elevation": end_elevation,
            "height_delta": height_delta,
            "grade_fraction": grade_fraction,
            "grade_degree": math.degrees(math.atan(grade_fraction)),
        }

    def curve_info(control_points, geometry_index=0):
        return {
            # "road_id": formatted_road_id,
            "gi": int(geometry_index),
            "control_points": control_points,
        }

    fake_context = {
        "上坡路": slope_info(-5, 5),
        "下坡路": slope_info(5, -5),
        "平路": slope_info(0, 0),
        "C型-左转弯": curve_info([[72,272],[228,100],[402,116],[465,342]]),
        "C型-右转弯": curve_info([[70,120],[205,358],[490,360],[630,120]]),
        "S型-先左后右": curve_info([[72,272],[360,94],[312,542],[528,248]]),
        "S型-先右后左": curve_info([[72,272],[264,458],[224,68],[528,248]]),
    }

    if road_type not in fake_context:
        raise ValueError(f"Unsupported fake road type: {road_type}")
    return fake_context[road_type]


class LLMJudgeRunner:
    def __init__(self, model=""):
        self.model = model
        self.manager = ManagerAgent(model, enable_dsl_check=False)
        self.judge_chain = _build_judge_chain(self.manager.model)

    def judge_request_and_save(
        self,
        user_input,
        database_path=DATABASE_PATH,
        verbose=True,
        draft_log_path=DRAFT_LOG_PATH,
    ):
        commands = _get_manager_commands(user_input, manager=self.manager)
        if verbose:
            _print_manager_summary(commands)
        results = []
        draft_record = {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "user_input": user_input,
            "manager_commands": _jsonable(commands),
            "judge_items": [],
        }
        for command in commands:
            try:
                result = _judge_command(command, self.judge_chain)
            except Exception as exc:
                result = _build_error_result(command, exc)
                if result is None:
                    raise
            if result is None:
                continue
            append_judge_result(user_input, result, database_path=database_path)
            results.append(result)
            draft_record["judge_items"].append(_jsonable(result))
            if verbose:
                print(f"  [{result['type_name']}] {result['road_type']} saved")
        if verbose and not results:
            print("  No slope/curve tasks to judge.")
        _append_draft_record(draft_record, draft_log_path)
        return results

    def judge_request_road_types(self, user_input):
        commands = _get_manager_commands(user_input, manager=self.manager)
        results = []
        for command in commands:
            result = _judge_command(command, self.judge_chain)
            if result is not None:
                results.append(result)
        return results


def judge_request_and_save(user_input, model="", database_path=DATABASE_PATH, verbose=True):
    runner = LLMJudgeRunner(model)
    return runner.judge_request_and_save(
        user_input, database_path=database_path, verbose=verbose
    )


def judge_request_road_types(user_input, model=""):
    runner = LLMJudgeRunner(model)
    return runner.judge_request_road_types(user_input)


def append_judge_result(user_input, result, database_path=DATABASE_PATH):
    command_type = result["type_name"]
    if command_type not in ("slope", "curve"):
        return
    connection = test_data_store.connect_database(database_path)
    try:
        with connection:
            test_data_store.save_road_context_values(
                connection,
                user_input,
                {command_type: [result["road_type"]]},
                append=True,
            )
    finally:
        connection.close()


def save_judge_results(user_input, results, database_path=DATABASE_PATH):
    values_by_type = {"slope": [], "curve": []}
    for item in results:
        command_type = item["type_name"]
        if command_type in values_by_type:
            values_by_type[command_type].append(item["road_type"])

    connection = test_data_store.connect_database(database_path)
    try:
        with connection:
            test_data_store.save_road_context_values(
                connection, user_input, values_by_type
            )
    finally:
        connection.close()


def _get_manager_commands(user_input, model="", manager=None):
    manager = manager or ManagerAgent(model, enable_dsl_check=False)
    commands = manager.get_commands(user_input, request_kind="road_context_manager")
    if isinstance(commands, dict):
        return commands.get("commands", [])
    return commands


def _print_manager_summary(commands):
    counts = {"width": 0, "slope": 0, "curve": 0, "other": 0}
    other_commands = []
    for command in commands or []:
        command_type = ""
        if isinstance(command, dict):
            command_type = str(command.get("type_name", "")).lower()
        if command_type in counts:
            counts[command_type] += 1
        else:
            counts["other"] += 1
            other_commands.append(command)

    parts = [
        f"slope x{counts['slope']}",
        f"curve x{counts['curve']}",
        f"width skipped x{counts['width']}",
    ]
    if counts["other"]:
        parts.append(f"other skipped x{counts['other']}")
    print("  Manager: " + ", ".join(parts))
    if not other_commands:
        return
    print("  Other manager output:")
    for command in other_commands:
        print(f"    - {_format_manager_output(command)}")


def _format_manager_output(command, max_length=300):
    try:
        text = json.dumps(command, ensure_ascii=False)
    except TypeError:
        text = str(command)
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text


def _append_draft_record(record, draft_log_path=DRAFT_LOG_PATH):
    draft_log_path = Path(draft_log_path)
    draft_log_path.parent.mkdir(parents=True, exist_ok=True)
    with draft_log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _jsonable(value):
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _judge_command(command, chain):
    if not isinstance(command, dict):
        return None
    command_type = str(command.get("type_name", "")).lower()
    if command_type not in ROAD_TYPES_BY_COMMAND_TYPE:
        return None

    description = command.get("description", "")
    allowed_types = ROAD_TYPES_BY_COMMAND_TYPE[command_type]
    judge_output = agent_trace.invoke_llm(
        chain,
        {
            "command_type": command_type,
            "description": description,
        },
        request_kind="road_context_judge",
        attempt=1,
        agent="road_context_judge",
        operation=command_type,
    )
    road_type = _extract_road_type(judge_output, allowed_types)
    return {
        "type_name": command_type,
        "description": description,
        "road_type": road_type,
        "judge_output": judge_output,
    }


def _build_error_result(command, error):
    if not isinstance(command, dict):
        return None
    command_type = str(command.get("type_name", "")).lower()
    if command_type not in ROAD_TYPES_BY_COMMAND_TYPE:
        return None
    return {
        "type_name": command_type,
        "description": command.get("description", ""),
        "road_type": "ERROR",
        "judge_output": str(error),
    }


def _build_judge_chain(model=""):
    system_prompt = SystemMessagePromptTemplate.from_template(
        escape_langchain_template_text(judge_prompt)
    )
    human_prompt = HumanMessagePromptTemplate.from_template(
        "Manager拆分后的请求类型：{command_type}\n"
        "Manager拆分后的子请求：{description}\n"
        "请判断该子请求对应道路在修改前的原始道路类型。"
    )
    chat_prompt = ChatPromptTemplate.from_messages([system_prompt, human_prompt])
    llm = model if hasattr(model, "invoke") else get_model(model)
    return chat_prompt | llm | StrOutputParser()


def _extract_road_type(text, allowed_types):
    marker = "【原始道路类型】"
    search_text = text.split(marker)[-1] if marker in text else text
    for road_type in allowed_types:
        if road_type in search_text:
            return road_type
    for road_type in allowed_types:
        if road_type in text:
            return road_type
    raise ValueError(f"Cannot extract road type from judge output: {text}")


def _format_duration(seconds):
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {seconds:05.2f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes):02d}m {seconds:05.2f}s"


def main():
    # 用于一个个测试请求，判断其原始道路类型，并将结果写入 Excel 文件。
    test_inputs = all_user_cases
    if not test_inputs:
        print("No test inputs configured in testfucs/LLMjudge.py::main().")
        return

    runner = LLMJudgeRunner()
    total = len(test_inputs)
    total_start = time.perf_counter()
    for index, user_input in enumerate(test_inputs, start=1):
        task_start = time.perf_counter()
        print(f"[{index}/{total}] \n  Processing: {user_input}")
        runner.judge_request_and_save(user_input)
        task_elapsed = time.perf_counter() - task_start
        total_elapsed = time.perf_counter() - total_start
        print(
            "  Time: "
            f"task {_format_duration(task_elapsed)} | "
            f"total {_format_duration(total_elapsed)}"
        )
    print(f"Done. Total time: {_format_duration(time.perf_counter() - total_start)}")


