import copy
from collections import deque, defaultdict
import math

from editor.editor import editor
from xodrs.xodrParser import XParser
from jsons.jsonParser import JParser
from jsons.jsonDataGetter import jDataGetter
from logger.logger import logger
from utils.definitions import *
from utils.lambdas import *
from utils.tools import *


class editorSlope(editor):
    def __init__(self):
        editor.__init__(self)
        self.cross_roads = None

    def edit(self, id, value, mode, move, maxStep=0, sameHdg=0):
        self.cross_roads = self.init_cross_roads()
        # 倾斜角不能和整体抬高同时使用
        if mode == "angle" and move == defs.MOVE_BOTH:
            print("angle modle can not work with m=2!")
            return
        if self.is_in_cross(id) and move != defs.MOVE_BOTH:
            print("can not change cross road in only one direction!")
            print("auto modify : mv=2")
            move = defs.MOVE_BOTH

        XParser.roadEdits = copy.deepcopy(XParser.roadBackup)

        XParser.roadEdits[id] = defs.BOTH_LOCKED
        # 提取对应的道路数据
        road = XParser.roads[id]
        link = road.find("link")
        pre = link.find("predecessor")
        suc = link.find("successor")
        # 锁定没被修改的那一边的道路节点，因此同时修改两端的时候不处理
        if move == defs.MOVE_HEAD and pre != None:
            self.lockChange(defs.TAIL, id)

        if move == defs.MOVE_TAIL and suc != None:
            self.lockChange(defs.HEAD, id)

        # 分别对头尾进行修改类型标记
        # 包括tail修改和两端修改
        if move != defs.MOVE_HEAD and pre != None:
            # 道路起始弧度角
            hdg = XParser.hdgs[id][0]
            self.setChange(id, defs.TAIL, maxStep, sameHdg, hdg)
        # 包括head修改和两端修改
        if move != defs.MOVE_TAIL and suc != None:
            hdg = XParser.hdgs[id][1]
            self.setChange(id, defs.HEAD, maxStep, sameHdg, hdg)

        # 默认加法(angle)
        if mode == "angle":
            # 限制一下，90度以内
            o_value = value
            value = value % 90 if value >= 0 else value % -90
            if value != o_value:
                print(f"adjusted input value of Slope:{value}")
                logger.write(f"adjusted input value of Slope:{value}")
            value = math.tan(value * math.pi / 180) * getData(road, "length")
        elif mode == "add":
            pass

        self.setRoadSlope(id, value, move)
        for newId in XParser.roads.keys():
            num = XParser.roadEdits[newId]
            if num == defs.TAIL_EDITED or num == defs.TAIL_EDITED2:  # change tail
                self.setRoadSlope(newId, value, defs.MOVE_TAIL)
            elif num == defs.HEAD_EDITED or num == defs.HEAD_EDITED2:  # change head
                self.setRoadSlope(newId, value, defs.MOVE_HEAD)
            elif num == defs.BOTH_EDITED:  # change both
                self.setRoadSlope(newId, value, defs.MOVE_BOTH)

        # modified_crosses = []
        # for id_in_edit,num in XParser.roadEdits.items():
        #   if num != defs.NOT_EDITED:
        #     for cross_id,roads in XParser.crossRoads:
        #       if id_in_edit in roads:
        #         if roads not in modified_crosses:
        #           modified_crosses.append(roads)

    def setRoadSlope(self, id, value, move):
        road = XParser.roads[id]
        elevations = road.find("elevationProfile").findall("elevation")
        elevaNum = len(elevations)
        length = getData(road, "length")
        # print(f"xiugai:{id}")
        # 将 value 均分为length份，然后按每一个路段的长度分配高程，使得整体变化更为平滑
        # 在add模式下，value是抬升的高度，在mul模式下，value是抬升的tan斜率
        slope = 0
        # 这里乘 -1 猜测是：为了控制系数 b 的正负号(斜率也反映出变化趋势)，而 value/length 刚好也相当于 tan 角斜率了
        if move == defs.MOVE_TAIL:
            slope = value / length * -1
        elif move == defs.MOVE_HEAD:
            slope = value / length

        for i in range(elevaNum):
            e = elevations[i]
            s0 = getData(e, "s")
            s1 = (
                getData(road, "length")
                if i == elevaNum - 1
                else getData(elevations[i + 1], "s")
            )

            # 关于系数b的修改不是很认同，如果本来就有一定的坡度，不确定能不能这样加（算了既然能正常运行就不要随便改了）
            setData(e, "b", getData(e, "b") + slope)
            if move == defs.MOVE_BOTH:
                setData(e, "a", getData(e, "a") + value)
            elif move == defs.MOVE_TAIL:
                # 之前slope的负号在这里消了
                # 这里是修改tail，所以要把尾部抬高，即将start抬高
                setData(e, "a", getData(e, "a") + slope * (s0 - length))
            elif move == defs.MOVE_HEAD:
                setData(e, "a", getData(e, "a") + slope * s0)

            # rectify the cars' position:
            for infos in JParser.carPosition[-1][id].values():
                for carInfo in infos:
                    pos = carInfo["pos"]
                    if pos >= s0 and pos < s1:
                        de = value
                        if move == defs.MOVE_TAIL:
                            de = slope * (pos - length)
                        elif move == defs.MOVE_HEAD:
                            de = slope * pos
                        # 原数据
                        ord = jDataGetter.getOrd(carInfo)
                        # 高度
                        ord["position"]["y"] += de

                        dx = hdg2ang2(math.atan(slope) * math.cos(carInfo["dhdg"]))
                        dz = hdg2ang2(math.atan(slope) * math.sin(carInfo["dhdg"]))
                        if ord.get("angle") != None:
                            ord["angle"]["x"] += dx
                            ord["angle"]["z"] += dz
                        else:
                            ord["rotation"]["x"] += dx
                            ord["rotation"]["z"] += dz

    def lockChange(self, direction, id):
        # 遍历与该道路在该连接点连接的所有道路，并锁定其连接点
        for info in XParser.roadConnections[id][direction]:
            # 按照之前的读取顺序，[0] 的位置是roadId,   [1] 的位置是连接点 defs.END/defs.HEAD
            if info[1]:
                # 锁定与输入道路连接的道路
                XParser.roadEdits[info[0]] = defs.HEAD_LOCKED
            else:
                XParser.roadEdits[info[0]] = defs.TAIL_LOCKED

    """
    input:
      id: 道路id
      di: 链接位置
      maxStep: 最大步数
      sameHdg: 同向修改
      hdg: 道路起始弧度角
    finish:
      一个标记好需要修改的位置的 roadEdits 列表
  """

    def setChange(self, id, di, maxStep, sameHdg, hdg):
        queue = deque()

        # 遍历添加在该点与当前道路链接的所有道路

        for info in XParser.roadConnections[id][di]:
            #             被连接道路roadId， 连接点(head/tail)
            queue.append({"id": info[0], "di": info[1], "step": 0})

        while len(queue) > 0:
            item = queue.popleft()
            id = item["id"]
            di = item["di"]
            step = item["step"]
            if self.is_in_cross(id) and (
                XParser.roadEdits[id] == defs.NOT_EDITED
                or XParser.roadEdits[id] == defs.TAIL_EDITED
                or XParser.roadEdits[id] == defs.HEAD_EDITED
            ):
                queue.append({"id": id, "di": getTailOrEndPoint(di), "step": step})
            # 同向修改
            if sameHdg:
                angle0 = (XParser.hdgs[id][0] - hdg) % (2 * math.pi)
                angle1 = (XParser.hdgs[id][1] - hdg) % (2 * math.pi)
                # 判断同向的阈值
                # 应该是可能出现道路方向相差180，但实际也是同向的
                # π/4（45°）、3π/4（135°）、5π/4（225°）、7π/4（315°）
                m1, m2, m3, m4 = (
                    math.pi / 4,
                    math.pi / 4 * 3,
                    math.pi / 4 * 5,
                    math.pi / 4 * 7,
                )
                if (angle0 > m1 and angle0 < m2) or (angle0 > m3 and angle0 < m4):
                    step = maxStep
                elif (angle1 > m1 and angle1 < m2) or (angle1 > m3 and angle1 < m4):
                    step = maxStep

            if step < maxStep:
                match XParser.roadEdits[id]:
                    # 向两端传播修改标记
                    case defs.NOT_EDITED | defs.TAIL_EDITED | defs.HEAD_EDITED:
                        XParser.roadEdits[id] = defs.BOTH_EDITED
                        for info in XParser.roadConnections[id][0]:
                            queue.append(
                                {"id": info[0], "di": info[1], "step": step + 1}
                            )
                        for info in XParser.roadConnections[id][1]:
                            queue.append(
                                {"id": info[0], "di": info[1], "step": step + 1}
                            )
                    # tail 锁定，仅编辑 head 并向 head 方向传播修改标记
                    case defs.TAIL_LOCKED:
                        XParser.roadEdits[id] = defs.HEAD_EDITED2
                        for info in XParser.roadConnections[id][defs.HEAD]:
                            queue.append(
                                {"id": info[0], "di": info[1], "step": step + 1}
                            )
                    # 与 tail 同理
                    case defs.HEAD_LOCKED:
                        XParser.roadEdits[id] = defs.TAIL_EDITED2
                        for info in XParser.roadConnections[id][defs.TAIL]:
                            queue.append(
                                {"id": info[0], "di": info[1], "step": step + 1}
                            )
                    case _:
                        continue
            # 对最终点进行检验
            elif step == maxStep and di == defs.TAIL:
                match XParser.roadEdits[id]:
                    case defs.NOT_EDITED:
                        XParser.roadEdits[id] = defs.TAIL_EDITED
                    case defs.HEAD_LOCKED:
                        XParser.roadEdits[id] = defs.TAIL_EDITED2
                    case defs.HEAD_EDITED:
                        XParser.roadEdits[id] = defs.BOTH_EDITED
                    case _:
                        continue
                # 遍历和该节点连接的所有道路节点
                for info in XParser.roadConnections[id][defs.TAIL]:
                    queue.append({"id": info[0], "di": info[1], "step": step})

            elif step == maxStep and di == defs.HEAD:
                match XParser.roadEdits[id]:
                    case defs.NOT_EDITED:
                        XParser.roadEdits[id] = defs.HEAD_EDITED
                    case defs.TAIL_LOCKED:
                        XParser.roadEdits[id] = defs.HEAD_EDITED2
                    case defs.TAIL_EDITED:
                        XParser.roadEdits[id] = defs.BOTH_EDITED
                    case _:
                        continue
                for info in XParser.roadConnections[id][defs.HEAD]:
                    queue.append({"id": info[0], "di": info[1], "step": step})

    def is_in_cross(self, road_id):
        return self.cross_roads.get(road_id) != -1

    def init_cross_roads(self):
        ans = copy.deepcopy(XParser.roadBackup)
        ans = {key: -1 for key in ans}
        for info in XParser.crossRoads:
            for road_id in info[1]:
                ans[road_id] = info[0]
        return ans
