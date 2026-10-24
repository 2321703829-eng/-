from handler.handler import handler
from editor.editorLane import editorLane
from xodrs.xodrParser import XParser
from jsons.jsonParser import JParser
from utils.random import *

from collections import defaultdict


class handlerLane(handler):
    def __init__(self):
        handler.__init__(self)
        self.editorM = editorLane()

    def handle(self, command):
        XParser.pushNewData()
        JParser.pushNewData()
        li = "random"
        info = defaultdict(dict)
        info["type"] = "driving"
        info["level"] = "false"
        info["MsOffset"] = "0"
        info["Mtype"] = "solid"
        info["Mweight"] = "standard"
        info["Mcolor"] = "standard"
        info["MlaneChange"] = "none"

        for i in range(1, len(command)):
            param = command[i].split("=")
            match param[0]:
                case "id":
                    id = getRandomId(param[1])
                case "li":
                    li = param[1]
                case "info":
                    xList = param[1].split(",")
                    for x in xList:
                        nameValue = x.split(":")
                        info[nameValue[0]] = nameValue[1]

        if "id" not in locals():
            print("Id not given!")
            return "Id not given!"
        self.editorM.edit(id=id, laneId=li, infoMap=info)
        return f"lane id={id} li={li} info={info}"


handlerL = handlerLane()
