import copy
import json
import math
import random
from collections import defaultdict
from logger.logger import logger
import numpy as np

from utils.lambdas import getData, ang2hdg2, is_curve, probability_to_use
from xodrs.xodrParser import XParser
from jsons.jsonParser import JParser
from utils.random import *
from scenePicker.scenePicker import sPicker


class handlerRandom:
    def __init__(self):
        self.cross_roads = None
        self.commandJson = defaultdict()
        # 连接到同一个路口的道路连接点
        self.cross_roads_connections = defaultdict()
        pass

    def handle(self, command):
        """

        :param command:
        :return: 可执行的commandList
        """
        self.init_cross_roads()
        self.init_cross_road_connection()

        # 初始化ego行驶路径
        candidateRoadsId = sPicker.get_ego_route_roads()
        candidateRoads = defaultdict()
        # if  len(candidateRoadsId)==0:
        if True:
            print(f"handlerRandom:ego routing failed,modify all roads")
            candidateRoads = XParser.roads
        else:
            print(f"handlerRandom:ego routing found,modify selected roads")
            for road_id in candidateRoadsId:
                candidateRoads[str(road_id)] = XParser.roads[str(road_id)]

        logger.write(
            f"ego routing:{candidateRoadsId if len(candidateRoadsId)>0 else 'all'}"
        )

        # 命令列表
        commandList = []
        for road_id, road in candidateRoads.items():

            single_command = None
            # 可用命令种类选择
            type_list = []
            while not single_command:
                # type_list为空就初始化一下
                if not type_list:
                    # 所有道路都可以使用width命令
                    type_list.append(1)
                    # 不处于交叉路口中,可以使用slope命令
                    if not self.is_in_cross(road):
                        type_list.append(3)
                        # type_list=[3]
                    # 是曲线，可以使用curve
                    if is_curve(road):
                        type_list.append(2)
                single_command = self.get_random_command(road, type_list)
                if single_command:
                    self.note_command(single_command)

            commandList.extend(single_command)
        # print(json.dumps(self.commandJson,indent=4))
        return commandList
        # return []

    def get_random_command(self, road, choices=[1, 2, 3]):
        """
        返回一个命令列表
        """
        command_list = []
        match random.choice(choices):
            case 1:
                command_list = self.get_random_width(road)
            case 2:
                command_list = self.get_random_curve(road)
            case 3:
                type_list = self.get_type_for_slope_command(road.get("id"))
                if not type_list:
                    print(f"Not suitable for the slope command Id:{road.get('id')}")
                    return []
                command_list = self.get_random_slope(road, type_list)

        return command_list

    def get_random_curve(self, road):
        road_id = road.get("id")

        #   不是曲线
        if not is_curve(road):
            return None
        geometrys = road.find("planView").findall("geometry")
        hdg_s = getData(geometrys[0], "hdg") % (2 * math.pi)
        hdg_e = getData(geometrys[-1], "hdg") % (2 * math.pi)
        # 顺应原弯曲方向(末端要*-alpha)
        alpha = 1 if hdg_e - hdg_s > 0 else -1
        h0 = self.get_random_value(0, 25) * alpha * probability_to_use(30)
        # h0=random.randint(-10,20)
        v0 = (
            self.get_random_value2(0, 0.5)
            * random.choice([-1, 1])
            * probability_to_use(60)
        )
        h1 = self.get_random_value(0, 25) * (-alpha) * probability_to_use(30)
        v1 = (
            self.get_random_value2(0, 0.5)
            * random.choice([-1, 1])
            * probability_to_use(60)
        )
        # h1=random.randint(-10,20)
        # 正方向就顺应原弯曲方向
        # if h0>0:
        #   h0*=alpha
        # if h1>0:
        #   h1*=-alpha

        return [f"curve id={road_id} v0={v0} h0={h0} v1={v1} h1={h1}"]
        pass

    def get_random_slope(self, road, typeList):
        """
        返回一个slope命令
        param road：命令作用的道路
        param typeList：mv取值范围
        """
        # 设置最大角度max_angle
        max_angle = 10
        road_id = road.get("id")
        road_len = getData(road, "length")
        m = "add"
        value = 0
        mv = random.choice(typeList)
        road_connects = XParser.roadConnections.get(road_id)

        # 限制两端连接的道路的长度和连接到同一十字路口的道路长度
        road_connect_len = [[], []]
        for i in [0, 1]:
            for connected_road in road_connects[i]:
                n_len = getData(XParser.roads[connected_road[0]], "length")
                road_connect_len[i].append(n_len)
            cross_related_roads = self.get_cross_connect_road_by_road_connect_point(
                road_id, i
            )
            if cross_related_roads:
                for cross_related_road in cross_related_roads:
                    n_len = getData(
                        XParser.roads[cross_related_road["road_id"]], "length"
                    )
                    road_connect_len[i].append(n_len)

        # 长的迁就短的
        if mv != 2:
            value = self.get_random_value2(
                0,
                math.tan(ang2hdg2(max_angle))
                * min(
                    min(road_connect_len[mv]) if road_connect_len[mv] else float("inf"),
                    road_len,
                ),
            )
        else:
            min_len = min(
                min(road_connect_len[0]) if road_connect_len[0] else float("inf"),
                min(road_connect_len[1]) if road_connect_len[1] else float("inf"),
                road_len,
            )
            value = self.get_random_value2(0, math.tan(ang2hdg2(max_angle)) * min_len)

        return [f"slope id={road_id} v={value} mv={mv} m={m}"]

    def get_random_width(self, road):
        commands = []
        road_id = road.get("id")
        lanes = road.find("lanes").find("laneSection").findall(".//lane")
        for lane in lanes:
            lane_id = lane.get("id")
            if lane_id != "0":
                value = self.get_random_value2(0, 1.5) * random.choice([-1, 1])
                commands.append(
                    f"width id={road_id} v={value} li={lane_id} sh=1 ms=99 s=1"
                )

        return commands
        pass

    def get_random_value(self, low, high, sigma=0.3):
        """
        :param low: 取值下限
        :param high: 取值上限
        :param sigma: 标准差（可调整，值越小数据越集中）
        :return:
        """
        # 均值为区间中点
        mu = (high - low) / 2
        # 先生成正态分布随机数
        while True:
            candidate = np.random.normal(mu, sigma)
            # 只保留在区间内的值
            if low <= candidate <= high:
                return candidate

    def get_random_value2(self, low, high):
        """
        :param low: 取值下限
        :param high: 取值上限
        :return:
        """

        return random.uniform(low, high)

    def is_in_cross(self, road):

        road_id = road.get("id")

        return self.cross_roads[road_id] != -1

    def init_cross_roads(self):
        ans = copy.deepcopy(XParser.roadBackup)
        # 所有道路标记初始化为-1
        ans = {key: -1 for key in ans}
        # 按照crossRoads中的cross组来标价各条道路
        for info in XParser.crossRoads:
            for road_id in info[1]:
                ans[road_id] = info[0]

        self.cross_roads = ans

    def note_command(self, commandList):
        for command in commandList:
            command_split = command.split(" ")
            command_json = {
                "type": command_split[0],
            }
            for i in range(1, len(command_split)):
                param = command_split[i].split("=")
                command_json[param[0]] = param[1]

            self.commandJson[command_json["id"]] = command_json

    def get_type_for_slope_command(self, roadId):
        """
        返回一个当前道路能够选择的slope类型列表,为空则说明不适合使用该命令
        """
        ans = [0, 1]

        for i in [0, 1]:
            # 寻找同一交叉路口的道路
            cross_roads_connection = self.get_cross_connect_road_by_road_connect_point(
                roadId, i
            )
            # 补充直接相连的道路，
            road_connects = XParser.roadConnections[roadId][i]
            if road_connects:
                cross_roads_connection.extend(
                    [
                        {"road_id": connect_point[0], "connect_point": connect_point[1]}
                        for connect_point in road_connects
                    ]
                )

            if not cross_roads_connection:
                continue
            for connect_point in cross_roads_connection:
                road_id = connect_point["road_id"]
                # 排除自己
                if road_id != roadId:
                    # 相关道路的命令类型
                    road_commends = self.commandJson.get(road_id)
                    # 存在该命令再获取类型
                    commend_type = road_commends.get("type") if road_commends else None
                    # 相关道路有命令
                    if commend_type:
                        # 不是slope类型就令mv=2，使if条件不成立
                        mv = (
                            int(road_commends.get("mv"))
                            if commend_type == "slope"
                            else -1
                        )
                        if commend_type == "slope" and (
                            mv == 2 or mv == connect_point["connect_point"]
                        ):
                            ans.remove(i)
                            break
                        else:
                            continue

        # 两端都能使用slope命令
        if len(ans) == 2:
            ans.append(2)
        return ans

    def init_cross_road_connection(self):
        """
        获取到与十字路口链接的道路以及其连接点

        """
        self.cross_roads_connections = defaultdict()

        for item in XParser.crossRoads:
            cross_id, roadId_in_cross = item[0], item[1]
            if str(cross_id) not in self.cross_roads_connections:
                self.cross_roads_connections[str(cross_id)] = []
            for road_id in roadId_in_cross:
                road_connect = XParser.roadConnections[road_id]
                for dis in [0, 1]:
                    for connected_road in road_connect[dis]:
                        connections_json = {
                            "road_id": connected_road[0],
                            "connect_point": connected_road[1],
                        }
                        if (
                            connections_json
                            not in self.cross_roads_connections[str(cross_id)]
                        ):
                            self.cross_roads_connections[str(cross_id)].append(
                                connections_json
                            )

    def get_cross_connect_road_by_road_connect_point(self, roadId, connected_point):
        """
        返回一组与交叉口相连的道路
        """
        connections_json = {"road_id": str(roadId), "connect_point": connected_point}
        if self.cross_roads_connections:
            for key in self.cross_roads_connections:
                cross_roads_connection = self.cross_roads_connections[key]
                if connections_json in cross_roads_connection:
                    return cross_roads_connection

        return []


handlerR = handlerRandom()
