# import numpy as np
#
# from testfucs.testfunc10_finalway import get_bezier_shape
# from llm.curve_test_agent import curveTestAgent
# from testfucs.td import modify_test_cases
#
#
#
#
# agent = curveTestAgent()
# for i in range(len(modify_test_cases)):
#     test_case=modify_test_cases[i]
#     originalInfo = get_bezier_shape(test_case["control_point"])
#     result=agent.get_commands(test_case["modification"],originalInfo)["command"]
#     print("{")
#     print(f'"title": "Test {i+1}  : {originalInfo}",')
#     print(f'"control_point": {test_case["control_point"]},')
#     print(f'"modification": "{test_case["modification"]}",')
#     print(f'"command": {result},')
#     print("},")


import os
from dotenv import load_dotenv
from langchain_classic.agents.xml.prompt import agent_instructions
from langchain_classic.output_parsers import ResponseSchema, StructuredOutputParser,OutputFixingParser
from langchain_core.prompts import ChatPromptTemplate,SystemMessagePromptTemplate,HumanMessagePromptTemplate
from langchain_openai import ChatOpenAI

# from llm.agent_prompts import widthPrompt,get_model
# from llm.agent_tools import curveTestPrompt,get_model

load_dotenv(override=True)

class curveTestAgent:

    def __init__(self,model=""):
        # self.apikey = None
        self.model = self.get_model() or None
        # self.init(model)
        self.chain = self.init_chain()

    def get_model(self):
        # model = ChatOpenAI(
        #     base_url="https://api.cerebras.ai/v1/",
        #     api_key="REPLACE_WITH_CEREBRAS_API_KEY_FROM_CEREBRAS_CONSOLE",
        #     model="qwen-3-235b-a22b-instruct-2507",
        # )
        model = ChatOpenAI(
            base_url="https://api.deepseek.com",
            api_key="REPLACE_WITH_DEEPSEEK_API_KEY_FROM_DEEPSEEK_CONSOLE",
            model="deepseek-chat",
        )
        return model


    def init_chain(self):


        schemas=[
            ResponseSchema(name="words", description="你的回答")
        ]

        parser = StructuredOutputParser.from_response_schemas(schemas)

        system_prompt = SystemMessagePromptTemplate.from_template(
            """{curveTestPrompt}请严格遵守以下JSON输出规范（仅返回JSON格式，不得包含任何额外字符）: {format_instructions}"""
        )

        human_prompt = HumanMessagePromptTemplate.from_template(
            """根据用户的描述，提取所要求的信息并严格返回JSON。描述: {userInput}"""
        )

        chat_prompt = ChatPromptTemplate.from_messages(
            [system_prompt, human_prompt]
        ).partial(
            curveTestPrompt="",
            format_instructions=parser.get_format_instructions()
        )

        fixing_parser = OutputFixingParser.from_llm(parser=parser, llm=self.model)

        chain = chat_prompt | self.model | fixing_parser

        return chain

    def get_commands(self, userInput):
        user_input = {"userInput":userInput,
                    }
        result=self.chain.invoke(user_input)
        # print(result)
        return result

agent=curveTestAgent()
print(agent.get_commands("gpt-oss-120b和GPT4有什么区别"))
print(agent.get_commands("详细介绍你自己，包括模型参数，运营商，知识范围，你的模型名称"))
print(agent.get_commands("你知不知道我的上一个问题是什么"))