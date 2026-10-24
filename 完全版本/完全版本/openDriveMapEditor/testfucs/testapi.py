from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
import os
from dotenv import load_dotenv

load_dotenv()

llm = ChatOpenAI(
    model="gpt-5.6-luna",
    api_key="REPLACE_WITH_CLOUDEX_API_KEY_FROM_CLOUDEX_CONSOLE",

    base_url="https://api.cloudex.ink/v1",
    default_headers={
        "User-Agent": "Mozilla/5.0"
    },
    use_responses_api=True,
)

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个有帮助的助手。"),
    ("human", "{input}"),
])

chain = prompt | llm | StrOutputParser()

request = {
    "input": "你是什么模型"
}

response = chain.invoke(request)

print(response)





def curve_transformation(
    curve_dsl,
    roads,
    npc,
):
    """
    Transform the selected 
    road curves according to 
    the Curve DSL and evolve 
    the paths of NPCs 
    traveling on those roads.
    """
pass











