from handler.handler import handler
from editor.editorSignal import editorSignal
from xodrs.xodrParser import XParser
from jsons.jsonParser import JParser
from utils.random import *

from collections import defaultdict


class handlerSignal(handler):
    def __init__(self):
        handler.__init__(self)
        self.editorSi = editorSignal()

    def handle(self, command):
        XParser.pushNewData()
        JParser.pushNewData()
        info = defaultdict(dict)
        info["s"] = "0"
        info["t"] = "0"
        info["id"] = None
        info["name"] = None
        info["dynamic"] = "no"
        info["orientation"] = "+"
        info["zOffset"] = "0"
        info["country"] = "DE"
        info["countryRevision"] = "2025"
        info["type"] = "none"
        info["subtype"] = "none"
        info["value"] = None
        info["height"] = "0"
        info["width"] = "0"
        info["hOffset"] = "0"

        for i in range(1, len(command)):
            param = command[i].split("=")
            match param[0]:
                case "id":
                    id = getRandomId(param[1])
                case "si":
                    si = int(param[1])
                case "info":
                    xList = param[1].split(",")
                    for x in xList:
                        nameValue = x.split(":")
                        info[nameValue[0]] = nameValue[1]

        if "id" not in locals():
            print("Id not given!")
            return "Id not given!"
        self.editorSi.edit(id=id, signalId=si, infoMap=info)
        return f"signal id={id} si={si} info={info}"


handlerSi = handlerSignal()
