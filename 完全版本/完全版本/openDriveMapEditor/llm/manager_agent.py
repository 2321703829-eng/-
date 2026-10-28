import os
import re

from dotenv import load_dotenv
from langchain_classic.output_parsers import (
    ResponseSchema,
    StructuredOutputParser,
    OutputFixingParser,
)
from langchain_core.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)

# from llm.agent_prompts import get_model
from handler.agent_dispatcher import AgentDispatcher
from llm.agent_tools import escape_langchain_template_text, get_model, MANAGER_PROMPT
from llm.curve_agent import CurveAgent
from llm.slope_agent import SlopeAgent
from llm.width_agent import WidthAgent
from llm.width_guardrails import road_ids
from utils import agent_trace

load_dotenv(override=True)


class ManagerAgent:
    def __init__(self, model="", enable_dsl_check=True):
        self.model = get_model(model)
        # self.init(model)
        self.history = []
        self.max_turns = 5
        self.chain = self.init_chain()
        self.curve_agent = CurveAgent(model, enable_dsl_check=enable_dsl_check)
        self.curve_optimizer_agent = self.curve_agent
        self.width_agent = WidthAgent(model, enable_dsl_check=enable_dsl_check)
        self.slope_agent = SlopeAgent(model, enable_dsl_check=enable_dsl_check)
        self.dispatcher = AgentDispatcher(
            curve_agent=self.curve_agent,
            curve_optimizer_agent=self.curve_optimizer_agent,
            width_agent=self.width_agent,
            slope_agent=self.slope_agent,
        )

    def add(self, role, content):
        self.history.append({"role": role, "content": content})
        if len(self.history) > self.max_turns * 2:
            self.history = self.history[-self.max_turns * 2 :]

    def as_prompt(self):
        return "\n".join(f"{m['role']}: {m['content']}" for m in self.history)

    def init_chain(self):

        schemas = [
            ResponseSchema(
                name="commands",
                description="A list containing the individual command types ‘type_name’ after task splitting, and the individual command descriptions ‘description’ after task splitting.",
            )
            # ResponseSchema(name="topic", description="任务拆分后的单个命令类型"),
            # ResponseSchema(name="description", description="任务拆分后的单个命令描述内容")
        ]
        parser = StructuredOutputParser.from_response_schemas(schemas)

        # prompt_template = PromptTemplate.from_template(
        #     MANAGER_PROMPT+"根据用户的任务描述，将任务按需要进行拆分,并返回JSON格式：\n{input}\n\n{format_instructions}"
        # )

        system_prompt = SystemMessagePromptTemplate.from_template(
            escape_langchain_template_text(MANAGER_PROMPT + "\nFor each executable subtask return exactly road_id, type_name, description. road_id must be road_N from the original explicit road reference. Preserve the original edit meaning, units, degree and lane target. Do not replace signed lane IDs with road IDs. Negative lane IDs are legal OpenDRIVE IDs; never reject a task merely for a negative lane ID. Delegate width validity and boundaries to the width agent rather than inventing restrictions at task splitting. Preserve a bit/a little as slight, and greatly/significantly as significant.\n")
            + """Please strictly adhere to the following JSON output specifications (return only JSON, no extra characters): {format_instructions}"""
        )

        human_prompt = HumanMessagePromptTemplate.from_template(
            """Based on the user's task description, split the task as needed and return JSON. Executable command schema is EXACTLY {{"road_id":"road_N","type_name":"width|slope|curve","description":"faithful subtask description"}}. Never add parameters, degree, lane_sides, tools, or any other keys. Degree and lane details belong in description. Example: moderately narrow the far right lane on roadway 0 => {{"commands":[{{"road_id":"road_0","type_name":"width","description":"Moderately narrow the far right lane on roadway 0."}}]}}. Task description: {input}"""
        )

        chat_prompt = ChatPromptTemplate.from_messages(
            [system_prompt, human_prompt]
        ).partial(
            format_instructions=parser.get_format_instructions(),
        )

        fixing_parser = OutputFixingParser.from_llm(parser=parser, llm=self.model)

        chain = chat_prompt | self.model | fixing_parser

        return chain

    def get_commands(self, commandDescription, request_kind="manager_split"):
        response = agent_trace.invoke_llm(
            self.chain,
            {"input": commandDescription},
            request_kind=request_kind,
            agent="manager",
            operation="manager",
        )
        commands = self._attach_road_ids(response["commands"])
        self.dispatcher.original_user_instruction = commandDescription
        original_roads = set(road_ids(commandDescription))
        for command in commands:
            if command.get("type_name") in {"width", "slope", "curve"} and original_roads:
                if command["road_id"].removeprefix("road_") not in original_roads:
                    raise ValueError("Manager invented a road absent from the original request")
        response = dict(response, commands=commands)
        agent_trace.record_agent_step(
            agent="manager",
            operation="manager",
            input_text=commandDescription,
            llm_output_json=response,
            status="ok",
        )
        self.add("user", commandDescription)
        self.add("manager", commands)

        # 返回列表格式命令
        return commands

    def _attach_road_ids(self, commands):
        enriched = []
        for command in commands:
            command = dict(command)
            if command.get("type_name") in {"width", "slope", "curve"}:
                explicit = command.get("road_id")
                described = road_ids(command.get("description", ""))
                if explicit is not None:
                    if not isinstance(explicit, str) or not re.fullmatch(r"road_[0-9]+", explicit):
                        raise ValueError("Manager road_id must be a concrete road_N string")
                    if described and described != [explicit.removeprefix("road_")]:
                        raise ValueError("Manager structured road_id conflicts with description")
                    command["road_id"] = explicit
                else:
                    command["road_id"] = self._extract_unique_road_id(command.get("description", ""))
            enriched.append(command)
        return enriched

    def _extract_unique_road_id(self, description):
        ids = road_ids(description)
        if len(ids) != 1:
            raise ValueError(
                "Executable Manager command must describe exactly one road id: "
                f"{description}"
            )
        return f"road_{ids[0]}"

    def dispatch_task(self, commands):
        results = self.dispatcher.dispatch_split_commands(commands)
        print("dispatchResults:", results)
        return results


# if __name__ == "__main__":
#         manager = ManagerAgent()
#         result=manager.get_commands("将地图中的8号道路的起始角度顺时针旋转一些，末端弯曲程度减小一些,-1号车道的宽度增大，将其坡度也增加少许")
#         print(result)
#         manager.dispatch_task(result["commands"])
# Manager=ManagerAgent("")
