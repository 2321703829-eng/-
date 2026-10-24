import requests


class LLMChater:
    def __init__(self):
        self.headers = {
            "accept": "application/json",
            "AUTHORIZATION": "REPLACE_WITH_MAXKB_AUTHORIZATION_FROM_MAXKB_CONSOLE",  # api key
        }

    def translate(self, re_chat=False, stream=False):
        profileId = self.getProfileId()
        if profileId:
            chatId = self.getChatId(profileId)
            message = input("请输入你的要求：")
            if chatId:
                chat_message_payload = {
                    "message": message,
                    "re_chat": re_chat,
                    "stream": stream,
                }
                response = self.sendChatMessage(chatId, chat_message_payload)
                if response:
                    # 获取reponse中的content
                    content = response["data"]["content"]
                    answer = content.split("\n")
                    for i in range(len(answer)):
                        answer[i] = answer[i].strip()
                    print("answer: \n", answer)
                    return answer
                else:
                    return None
        else:
            return None

    # 获取 profile_id(即应用id)
    def getProfileId(self):
        profileUrl = "http://localhost:8080/api/application/profile"  # 自己的url
        response = requests.get(profileUrl, headers=self.headers)
        if response.status_code == 200:
            return response.json()["data"]["id"]
        else:
            print("Failed to get profile id!")
            return None

    # 获取 chat_id
    def getChatId(self, profile_id):
        chatOpenUrl = f"http://localhost:8080/api/application/{profile_id}/chat/open"
        response = requests.get(chatOpenUrl, headers=self.headers)
        if response.status_code == 200:
            return response.json()["data"]
        else:
            print("Failed to get chat id!")
            return None

    # 发送聊天消息
    def sendChatMessage(self, chat_id, payload):
        chatMessageUrl = f"http://localhost:8080/api/application/chat_message/{chat_id}"
        response = requests.post(chatMessageUrl, headers=self.headers, json=payload)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Failed to send message, status code: {response.status_code}")
            return None


chater = LLMChater()
