import xml.etree.ElementTree as ET
from editor.editor import editor

from xodrs.xodrParser import XParser


class editorSignal(editor):
    def __init__(self):
        editor.__init__(self)

    ## 修改指定道路的信号信息.
    def edit(self, id, signalId, infoMap):
        road = XParser.roads[id]
        signals = road.find("signals")
        allSignals = signals.findall("signal")

        if -1 < signalId < len(allSignals):
            signal = allSignals[signalId]
            # 删除 signal
            if infoMap["remove"] == "1":
                signals.remove(signal)
                return
        else:
            signal = ET.Element("signal")
            signals.append(signal)

        attributes = [
            "s",
            "t",
            "id",
            "name",
            "dynamic",
            "orientation",
            "zOffset",
            "country",
            "countryRevision",
            "type",
            "subtype",
            "value",
            "height",
            "width",
            "hOffset",
        ]
        for a in attributes:
            signal.set(a, infoMap[a])
