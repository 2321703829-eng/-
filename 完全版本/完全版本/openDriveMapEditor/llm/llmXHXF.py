import _thread as thread
import base64
import datetime
import hashlib
import hmac
import json
import copy
from urllib.parse import urlparse
import ssl
from datetime import datetime
from time import mktime
from urllib.parse import urlencode
from wsgiref.handlers import format_date_time
import websocket


class LLMChater:
    def __init__(self):
        self.answer = ""
        # self.isFirstcontent = False

        self.appId = "REPLACE_WITH_IFLYTEK_SPARK_APP_ID_FROM_IFLYTEK_CONSOLE"
        self.apiSecret = "REPLACE_WITH_IFLYTEK_SPARK_API_SECRET_FROM_IFLYTEK_CONSOLE"
        self.apiKey = "REPLACE_WITH_IFLYTEK_SPARK_API_KEY_FROM_IFLYTEK_CONSOLE"
        self.domain = "x1"
        self.sparkUrl = "wss://spark-api.xf-yun.com/v1/x1"

        self.PROMPT = (
            "你将辅助用户完成对道路数据的编辑任务, 你需要将自然语言翻译为命令序列. \
		生成的命令中, 每个命令独占一行, 非必须输入的参数可以省略, 必须输入的参数标注为*. \
		部分参数可指定为random_x_y, 表示取xy之间的随机值, 它们标注为+. \
		id可以取为random_x_y, 表示随机取测试范围内的道路, x代表测试场景考虑的ego车辆行驶路线数量, y代表是否考虑npc车辆. \
		所有默认值均可省略. \
		命令的格式必须严格遵循规范, 不得添加无关部分. \
		可用命令如下: \
		1.  width 命令，用于修改指定道路的指定车道的宽度. \
				参数列表: \
				1. id*: 修改的道路id号. \
				2. v*+: 修改值, 默认为0. \
				3. s: 是否平滑与其它道路连接边缘, 默认为1. \
				4. ms: 广度优先搜索层数, 默认为0. \
				5. sh: 仅在同向道路上搜索, 默认为0.  \
				6. li: 车道信息, 默认为空, 即修改道路的所有车道。示例: li=-1,1,2 \
				注: 同时修改多条车道时, 增加/减少的宽度为道路的总宽度，而非单个车道的宽度. \
		2.  slope 命令 \
				slope命令用于修改指定道路的坡度. \
				参数列表: \
				1. id*: 修改的道路id号. \
				2. v*+: 修改值, 默认为0. \
				3. mv*: 改变道路指定端高度. 0表示首尾同时改变, 1表示尾部, 2表示头部. \
				4. m: 修改模式, 分为mul直接改变坡度和add改变高度, 默认为add. \
				4. ms: 广度优先搜索层数, 默认为0. \
				5. sh: 仅在同向道路上搜索, 默认为0. \
		3. fit 命令 \
				fit命令用于拟合道路曲线, 将整条道路拟合为数段曲线. \
				参数列表: \
				1. id*: 修改的道路id号. \
				2. md+: 可以容忍的最大误差, 默认为0.01. \
				3. st+: 采样途径点的间隔, 默认为1.0. \
		4. curve 命令 \
				curve命令用于修改指定道路的形状. \
				参数列表: \
				1. id*; 修改的道路id号. \
				2. x0+: 曲线起点x坐标的变化值, 默认为0. \
				3. y0+: 曲线起点y坐标的变化值, 默认为0. \
				4. h0+: 曲线起点方向的变化值, 默认为0. \
				5. v0+: 曲线第一控制点到起点的距离变化值, 默认为0. \
				6. x1+: 曲线终点x坐标的变化值, 默认为0. \
				7. y1+: 曲线终点y坐标的变化值, 默认为0. \
				8. h1+: 曲线终点方向的变化值, 默认为0. \
				9. v1+: 参数第二控制点到终点的距离变化值, 默认为0. \
				10. gi: 修改的geomotry编号, 默认为random. \
		5. save 命令. \
			存储修改后的xodr与json文件. \
		6. close 命令. \
			关闭文件. \
		7. undo 命令. \
			撤销上一条编辑指令的编辑效果. \
		8. saveName 命令. \
			设置存储的文件名. \
		命令序列必须以一个文件名起始, 表示读取它, 并有对应的close. \
		命令序列示例如下: \
				ARG_Carcarana-1_1_I-1-1 \
				savename test0 \
				curve id=random v0=random_-0.5_0.5 v1=random_-0.5_0.5 \
				save \
				undo \
				savename test1 \
				curve id=random v0=random_-0.5_0.5 v1=random_-0.5_0.5 \
				save \
				undo \
				savename test2 \
				curve id=random v0=random_-0.5_0.5 v1=random_-0.5_0.5 \
				save \
				close \
		它表示将ARG_Carcarana-1_1_I-1-1随机编辑为test0, test1, test2三个新地图. \
		如果明白了, 请开始工作. 如果工作时无法给出答案, 请报错. "
        )
        self.background = [{"role": "system", "content": self.PROMPT}]

    def translate(self):
        self.answer = ""
        userInput = input("请输入你的要求：")
        question = self.getQuestion("user", userInput)
        self.checklen(question)
        self.operate(question)

        answer = self.answer.split("\n")
        for i in range(len(answer)):
            answer[i] = answer[i].strip()
        print("answer: ")
        print(answer)
        return answer

    def operate(self, question):
        print("星火模型正在运行中...")
        wsParam = websocketParam(self.appId, self.apiKey, self.apiSecret, self.sparkUrl)
        websocket.enableTrace(False)
        wsUrl = wsParam.create_url()
        ws = websocket.WebSocketApp(
            wsUrl,
            on_message=self.onMessage,
            on_error=self.onError,
            on_close=self.onClose,
            on_open=self.onOpen,
        )
        ws.appid = self.appId
        ws.question = question
        ws.domain = self.domain
        ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

    def getQuestion(self, role, content):
        jsoncon = {}
        jsoncon["role"] = role
        jsoncon["content"] = content
        copyText = copy.deepcopy(self.background)
        copyText.append(jsoncon)
        return copyText

    def getlength(self, text):
        length = 0
        for content in text:
            temp = content["content"]
            leng = len(temp)
            length += leng
        return length

    def checklen(self, text):
        while self.getlength(text) > 8000:
            del text[0]

    def onError(self, ws, error):
        print("error: ", error)

    def onClose(self, ws, one, two):
        print(" ")

    def onOpen(self, ws):
        thread.start_new_thread(self.run, (ws,))

    def run(self, ws, *args):
        data = json.dumps(
            self.genParams(appid=ws.appid, domain=ws.domain, question=ws.question)
        )
        ws.send(data)

    def onMessage(self, ws, message):
        data = json.loads(message)
        code = data["header"]["code"]
        content = ""
        if code != 0:
            print(f"请求错误: {code}, {data}")
            ws.close()
        else:
            choices = data["payload"]["choices"]
            status = choices["status"]
            text = choices["text"][0]
            # if 'reasoning_content' in text and '' != text['reasoning_content']:
            #   reasoning_content = text["reasoning_content"]
            #   print(reasoning_content, end="")
            #   self.isFirstcontent = True

            if "content" in text and "" != text["content"]:
                content = text["content"]
                # if self.isFirstcontent:
                # 	print("\n以上为思维链内容, 模型回复内容如下********************\n")
                # print(content, end="")
                # self.isFirstcontent = False
                self.answer += content
            if status == 2:
                ws.close()

    def genParams(self, appid, domain, question):
        data = {
            "header": {
                "app_id": appid,
                "uid": "1234",
            },
            "parameter": {
                "chat": {
                    "domain": domain,
                    "temperature": 1.2,
                    "max_tokens": 10000,  # 请根据不同模型支持范围，适当调整该值的大小
                }
            },
            "payload": {"message": {"text": question}},
        }
        return data


chater = LLMChater()


class websocketParam(object):
    def __init__(self, APPID, APIKey, APISecret, Spark_url):
        self.APPID = APPID
        self.APIKey = APIKey
        self.APISecret = APISecret
        self.host = urlparse(Spark_url).netloc
        self.path = urlparse(Spark_url).path
        self.Spark_url = Spark_url

    def create_url(self):
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))

        signature_origin = "host: " + self.host + "\n"
        signature_origin += "date: " + date + "\n"
        signature_origin += "GET " + self.path + " HTTP/1.1"

        signature_sha = hmac.new(
            self.APISecret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        signature_sha_base64 = base64.b64encode(signature_sha).decode(encoding="utf-8")
        authorization_origin = f'api_key="{self.APIKey}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha_base64}"'
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode(
            encoding="utf-8"
        )

        v = {"authorization": authorization, "date": date, "host": self.host}
        url = self.Spark_url + "?" + urlencode(v)
        return url
