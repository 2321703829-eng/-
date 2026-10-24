import xml.etree.ElementTree as ET
from editor.editor import editor

from xodrs.xodrParser import XParser


class editorObject(editor):
    def __init__(self):
        editor.__init__(self)

    ## 修改指定道路的信号信息.
    def edit(self, id, objectId, name, infoMap):
        road = XParser.roads[id]
        objects = road.find("signals")
        allObjects = objects.findall("signal")

        if -1 < objectId < len(allObjects):
            obj = allObjects[objectId]
            # 删除 object
            if infoMap["remove"] == "1":
                objects.remove(obj)
                return
        else:
            name = "object" if not infoMap.has_key("name") else infoMap["name"]
            obj = ET.Element(name)
            objects.append(obj)

        for key, value in infoMap.item():
            if key != "name":
                obj.set(key, value)
