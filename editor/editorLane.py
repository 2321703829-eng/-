import xml.etree.ElementTree as ET
from editor.editor import editor

from xodrs.xodrParser import XParser


class editorLane(editor):
    def __init__(self):
        editor.__init__(self)

    ## 修改指定道路的指定车道的道路线信息.
    def edit(self, id, laneId, infoMap):
        road = XParser.roads[id]
        sections = road.find("lanes").findall("laneSection")

        attributes = [
            "type",
            "level",
            "MsOffset",
            "Mtype",
            "Mweight",
            "Mcolor",
            "MlaneChange",
        ]

        for section in sections:
            lanes = section.findall(".//lane")

            for lane in lanes:
                if lane.get("id") != laneId:
                    continue

                roadMark = lane.find("roadMark")
                if roadMark == None:
                    roadMark = ET.Element("roadMark")
                    lane.append(roadMark)

                for a in attributes:
                    if a[0] == "M":
                        roadMark.set(a[1:], infoMap[a[1:]])
                    else:
                        lane.set(a, infoMap[a])
                break
