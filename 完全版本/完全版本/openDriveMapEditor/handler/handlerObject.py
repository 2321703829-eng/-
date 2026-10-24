from handler.handler import handler
from editor.editorObject import editorObject
from xodrs.xodrParser import XParser
from jsons.jsonParser import JParser
from utils.random import *

from collections import defaultdict


class handlerObject(handler):
    def __init__(self):
        handler.__init__(self)
        self.editorO = editorObject()

    def handle(self, command):
        XParser.pushNewData()
        JParser.pushNewData()
        info = defaultdict(dict)

        for i in range(1, len(command)):
            param = command[i].split("=")
            match param[0]:
                case "id":
                    id = getRandomId(param[1])
                case "oi":
                    oi = int(param[1])
                case "name":
                    name = param[1]
                case "info":
                    xList = param[1].split(",")
                    for x in xList:
                        nameValue = x.split(":")
                        info[nameValue[0]] = nameValue[1]

        if "id" not in locals():
            print("Id not given!")
            return "Object id not given!"
        if "oi" not in locals():
            print("Si not given!")
            return "Object oi not given!"
        if "name" not in locals():
            print("Name not given!")
            return "Object name not given!"

        self.editorO.edit(id=id, objectId=oi, name=name, infoMap=info)
        return f"object id={id} oi={oi} name={name} info={info}"


handlerO = handlerObject()
