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
            escape_langchain_template_text(MANAGER_PROMPT)
            + """Please strictly adhere to the following JSON output specifications (return only JSON, no extra characters): {format_instructions}"""
        )

        human_prompt = HumanMessagePromptTemplate.from_template(
            """Based on the user's task description, split the task as needed and return JSON. Task description: {input}"""
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
                command["road_id"] = self._extract_unique_road_id(
                    command.get("description", "")
                )
            enriched.append(command)
        return enriched

    def _extract_unique_road_id(self, description):
        text = str(description)
        road_ids = []
        patterns = (
            r"\broad(?:\s*no\.?)?[_\s#-]*(\d+)\b",
            r"(\d+)\s*号\s*(?:道路|路)",
            r"(?:道路|路)\s*(\d+)",
        )
        for pattern in patterns:
            for road_id in re.findall(pattern, text, flags=re.IGNORECASE):
                if road_id not in road_ids:
                    road_ids.append(road_id)
        if len(road_ids) != 1:
            raise ValueError(
                "Executable Manager command must describe exactly one road id: "
                f"{description}"
            )
        return f"road_{road_ids[0]}"

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
