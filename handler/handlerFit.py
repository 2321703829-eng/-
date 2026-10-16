from handler.handler import handler
from editor.editorFit import editorFit
from xodrs.xodrParser import XParser
from jsons.jsonParser import JParser
from utils.random import *


class handlerFit(handler):
    def __init__(self):
        handler.__init__(self)
        self.editorF = editorFit()

    def handle(self, command):
        XParser.pushNewData()
        JParser.pushNewData()
        md = 0.01
        st = 1.0
        for i in range(1, len(command)):
            param = command[i].split("=")
            match param[0]:
                case "id":
                    id = getRandomId(param[1], curve=True)
                case "md":
                    md = getRandomValue(param[1])
                case "st":
                    st = getRandomValue(param[1])
                case _:
                    print("Illegal parameter!")

        if "id" not in locals():
            print("Id not given!")
            return "Id not given!"
        self.editorF.edit(id=id, md=md, st=st)
        return f"fit id={id} md={md} st={st}"


handlerF = handlerFit()
