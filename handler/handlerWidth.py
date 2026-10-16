from handler.handler import handler
from editor.editorWidth import editorWidth
from xodrs.xodrParser import XParser
from jsons.jsonParser import JParser
from utils.random import *


class handlerWidth(handler):
    def __init__(self):
        handler.__init__(self)
        self.editorW = editorWidth()

    def handle(self, command):
        XParser.pushNewData()
        JParser.pushNewData()
        v = 0
        s = 0
        ms = 0
        sh = False
        li = []
        for i in range(1, len(command)):
            param = command[i].split("=")
            match param[0]:
                case "id":
                    id, li = getRandomId(param[1], lane=True)
                case "v":
                    v = getRandomValue(param[1])
                case "s":
                    s = int(param[1])
                case "ms":
                    ms = int(param[1])
                case "sh":
                    sh = int(param[1])
                case "li":
                    lanes = param[1].split(",")
                    for lane in lanes:
                        li.append(lane)
                case _:
                    print("Illegal parameter!")

        if "id" not in locals():
            print("Id not given!")
            return "Id not given!"
        self.editorW.edit(id=id, value=v, smooth=s, maxStep=ms, sameHdg=sh, laneIds=li)
        return f"width id={id} v={v} s={s} ms={ms} sh={sh} li={ ','.join(str(i) for i in li) }"


handlerW = handlerWidth()
