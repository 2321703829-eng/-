import heapq
from collections import defaultdict

from xodrs.xodrParser import XParser
from xodrs.xodrDataGetter import xDataGetter
from jsons.jsonParser import JParser
from utils.lambdas import *
from types import MappingProxyType
import random


class scenePicker:
    def __init__(self):
        self.all_paths_found = []
        self.graph = defaultdict()
        self.candidateRoads = []
        self.candidateLanes = []
        self.rOLaneConnections = XParser.laneConnections
        # self.rOLaneConnections=MappingProxyType(XParser.laneConnections)

    def reinit(self):
        self.candidateRoads.clear()
        self.candidateLanes.clear()
        self.all_paths_found = []
        self.graph = defaultdict()

    def setCandidates(self, ego, npc):
        self.reinit()

        if ego > 0:  # 考虑ego车的途径点
            self.graph = self.buildGRAPH()
            self.rectifyGraph(self.graph)

            # 寻找潜在的ego路径
            paths = []
            for i in range(len(JParser.egoTs)):
                for j in range(len(JParser.egoDs)):
                    x = self.find_paths_in_range(f"start_{i}", f"end_{j}", 10, 10)
                    paths.extend(x)
            self.all_paths_found = paths

            for path in paths:
                cost, path = path
                print(f"{path}")
                for node in path:
                    data = node.split("_")
                    if data[0] == "start":
                        egoT = JParser.egoTs[int(data[1])]
                        id, lid = egoT[0], egoT[1]
                    elif data[0] == "end":
                        egoD = JParser.egoDs[int(data[1])]
                        id, lid = egoD[0], egoD[1]
                    else:
                        id, lid = data[1], data[3]
                    if id not in self.candidateRoads:
                        self.candidateRoads.append(id)
                    if [id, lid] not in self.candidateLanes:
                        self.candidateLanes.append([id, lid])
        print(self.candidateRoads)
        if npc > 0:  # 考虑npc车的途径点
            for id, roadInfo in JParser.carPosition[-1].items():
                if roadInfo and id not in self.candidateRoads:
                    self.candidateRoads.append(id)
                for lid, laneInfo in roadInfo.items():
                    if laneInfo and [id, lid] not in self.candidateLanes:
                        self.candidateLanes.append([id, lid])

    def buildGRAPH(self):
        graph = defaultdict(dict)
        for id, road in XParser.roads.items():
            length = getData(road, "length")
            section = road.find("lanes").find("laneSection")
            LHT = road.find("rule") == "LHT"

            # 获取起始路段和结尾路段的左右车道总宽度
            lws0, rws0 = xDataGetter.getPosWidths(road, 0)
            lws1, rws1 = xDataGetter.getPosWidths(road, length)

            for lane in section.findall(".//lane"):
                lid = lane.get("id")
                if lid == "0":
                    continue
                tailNode = f"road_{id}_lane_{lid}_0"
                headNode = f"road_{id}_lane_{lid}_1"
                ## 道路上行驶
                if int(lid) < 0 ^ LHT:  # 右侧车道向前行驶
                    graph[tailNode][headNode] = length
                    graph[headNode] = {}
                else:
                    graph[headNode][tailNode] = length
                    graph[tailNode] = {}
                ## 变道
                for otherLane in section.findall(".//lane"):
                    otherLid = otherLane.get("id")
                    #                                         选择同侧车道
                    if (
                        otherLid == "0"
                        or otherLid == lid
                        or int(otherLid) * int(lid) < 0
                    ):
                        continue
                    otherTailNode = f"road_{id}_lane_{otherLid}_0"
                    otherHeadNode = f"road_{id}_lane_{otherLid}_1"
                    if int(lid) < 0:  # 右侧车道
                        # 同侧不同车道间的横向宽度
                        graph[tailNode][otherTailNode] = abs(
                            rws0[-int(lid) - 1] - rws0[-int(otherLid) - 1]
                        )
                        graph[headNode][otherHeadNode] = abs(
                            rws1[-int(lid) - 1] - rws1[-int(otherLid) - 1]
                        )
                    else:
                        graph[tailNode][otherTailNode] = abs(
                            lws0[int(lid) - 1] - lws0[int(otherLid) - 1]
                        )
                        graph[headNode][otherHeadNode] = abs(
                            lws1[int(lid) - 1] - lws1[int(otherLid) - 1]
                        )

        ## 道路连接
        for id, item in self.rOLaneConnections.items():
            for lid, item_ in item.items():
                if int(lid) < 0 ^ LHT:  # 右侧车道向前行驶
                    node = f"road_{id}_lane_{lid}_1"
                    for target in item_[1]:
                        targetNode = f"road_{target[0]}_lane_{target[1]}_{target[2]}"
                        graph[node][targetNode] = 0
                else:
                    node = f"road_{id}_lane_{lid}_0"
                    for target in item_[0]:
                        targetNode = f"road_{target[0]}_lane_{target[1]}_{target[2]}"
                        graph[node][targetNode] = 0

        return graph

    def rectifyGraph(self, graph):
        for i in range(len(JParser.egoTs)):
            for j in range(len(JParser.egoDs)):
                # 【道路id，车道id，路面上(弧线)距离，车离参考线距离距离，角度差】
                egoT, egoD = JParser.egoTs[i], JParser.egoDs[j]
                if egoT is None or egoD is None:
                    continue

                road0 = XParser.roads[egoT[0]]
                length0 = getData(road0, "length")
                if int(egoT[1]) < 0:  # 道路右侧
                    graph[f"start_{i}"][f"road_{egoT[0]}_lane_{egoT[1]}_1"] = (
                        length0 - egoT[2]
                    )
                    graph[f"road_{egoT[0]}_lane_{egoT[1]}_0"][f"start_{i}"] = egoT[2]
                else:
                    graph[f"start_{i}"][f"road_{egoT[0]}_lane_{egoT[1]}_0"] = egoT[2]
                    graph[f"road_{egoT[0]}_lane_{egoT[1]}_1"][f"start_{i}"] = (
                        length0 - egoT[2]
                    )

                road1 = XParser.roads[egoD[0]]
                length1 = getData(road1, "length")
                if int(egoD[1]) < 0:  # 道路右侧
                    graph[f"end_{i}"][f"road_{egoD[0]}_lane_{egoD[1]}_1"] = (
                        length1 - egoD[2]
                    )
                    graph[f"road_{egoD[0]}_lane_{egoD[1]}_0"][f"end_{i}"] = egoD[2]
                else:
                    graph[f"end_{i}"][f"road_{egoD[0]}_lane_{egoD[1]}_0"] = egoD[2]
                    graph[f"road_{egoD[0]}_lane_{egoD[1]}_1"][f"end_{i}"] = (
                        length1 - egoD[2]
                    )

                if egoT[0] == egoD[0] and egoT[1] == egoD[1]:
                    if (int(egoT[1]) > 0) ^ (egoT[2] < egoD[2]):
                        graph[f"start_{i}"][f"end_{i}"] = abs(egoT[2] - egoD[2])
                    else:
                        graph[f"end_{i}"][f"start_{i}"] = abs(egoT[2] - egoD[2])

    def dijkstra(self, start, end, blocked_edges=None):
        blocked_edges = blocked_edges or set()
        heap = [(0, start)]
        distances = {node: float("inf") for node in self.graph}
        distances[start] = 0
        predecessors = {start: None}

        while heap:
            cost, current = heapq.heappop(heap)
            if current == end:
                path = []
                while current is not None:
                    path.append(current)
                    current = predecessors.get(current)
                return [cost, path[::-1]]
            if cost > distances[current]:
                continue
            if current not in self.graph:
                continue
            for neighbor, weight in self.graph[current].items():
                # 如果边被阻塞，则跳过
                if (current, neighbor) in blocked_edges:
                    continue
                new_cost = cost + weight
                if new_cost < (distances[neighbor] if distances.get(neighbor) else -1):
                    distances[neighbor] = new_cost
                    predecessors[neighbor] = current
                    heapq.heappush(heap, (new_cost, neighbor))
        return [None, None]

    def find_paths_in_range(self, start, end, k, delta):
        """
        :param start: 起点节点
        :param end: 终点节点
        :param k: 最多寻找的路径数量
        :param delta: 允许路径成本与最短路径成本的最大差值
        :return: 一个包含 (成本, 路径) 元组的列表
        """
        result_paths = []
        candidate_paths_heap = []

        # 找到第一条最短路径作为基准
        min_cost, first_path = self.dijkstra(start, end)
        if not first_path:
            print("起点和终点之间没有可用路径。")
            return []

        cost_threshold = min_cost + delta
        print(f"基准最短成本为: {min_cost}, 成本上限为: {cost_threshold}")

        result_paths.append((min_cost, first_path))

        # 主循环：迭代K-1次，寻找更多路径
        for i in range(1, k):
            # 基于上一条找到的最佳路径，来生成新的候选路径
            # previous_best_path: 上一条加入结果集的路径
            previous_best_path = result_paths[-1][1]

            cost_to_pivot = 0
            # 遍历路径上的每个节点，将其作为枢纽点来尝试绕路
            for j in range(len(previous_best_path) - 1):
                # 枢纽节点，是绕路的起点
                pivot_node = previous_best_path[j]
                # 从起点到枢纽节点的路径
                path_to_pivot = previous_best_path[: j + 1]

                # 每次迭代中，累加到枢纽节点的成本
                if j > 0:
                    u, v = previous_best_path[j - 1], previous_best_path[j]
                    cost_to_pivot += self.graph[u][v]

                # 准备阻塞边：阻塞所有已知路径中与 path_to_pivot 重合的部分
                blocked_edges = set()
                for _, path in result_paths:
                    if len(path) > j and path[: j + 1] == path_to_pivot:
                        blocked_edges.add((path[j], path[j + 1]))

                # 计算从枢纽节点到终点的“绕路”路径
                cost_from_pivot, path_from_pivot = self.dijkstra(
                    pivot_node, end, blocked_edges
                )

                if path_from_pivot:
                    # 组合成一条完整的新候选路径
                    new_candidate_path = path_to_pivot[:-1] + path_from_pivot
                    # 计算新路径总成本
                    new_candidate_cost = cost_to_pivot + cost_from_pivot

                    # 成本在上限内，加入候选集
                    if new_candidate_cost <= cost_threshold:
                        candidate_tuple = (new_candidate_cost, new_candidate_path)
                        if (
                            candidate_tuple not in candidate_paths_heap
                            and candidate_tuple not in result_paths
                        ):
                            heapq.heappush(candidate_paths_heap, candidate_tuple)

            # 从所有候选路径中，选出最优的一个作为下一条结果
            if not candidate_paths_heap:
                # 如果没有候选路径了，说明已找不到任何新路径
                print("\n已无更多候选路径。")
                break

            # 检查下一个最佳候选是否超限
            if candidate_paths_heap[0][0] > cost_threshold:
                print(
                    f"\n候选路径中成本最低的({candidate_paths_heap[0][0]})也已超过上限，停止搜索。"
                )
                break

            # 将找到的最佳候选路径加入结果集
            result_paths.append(heapq.heappop(candidate_paths_heap))

        print(f"--- 搜索完成，共找到 {len(result_paths)} 条路径 ---")
        return result_paths

    def dfs_recursive(
        self, current_node, end_node, current_path, current_cost, threshold
    ):
        """
        内部辅助函数：通过递归的深度优先搜索寻找路径。
        """
        # 剪枝：如果当前路径成本已超过阈值，则放弃此路径
        if current_cost > threshold:
            return

        # 找到一条有效路径：当前节点是终点
        if current_node == end_node:
            self.all_paths_found.append([current_cost, current_path])
            return

        if current_node not in self.graph:
            return

        # 探索所有邻居
        for neighbor, weight in self.graph[current_node].items():
            # 避免在当前路径中形成环路
            if neighbor not in current_path:
                self.dfs_recursive(
                    neighbor,
                    end_node,
                    current_path + [neighbor],  # 创建新列表，传递下一层
                    current_cost + weight,
                    threshold,
                )

    def getRandomId1(self, curve):
        while True:
            id = random.choice(self.candidateRoads)
            gs = XParser.roads[id].find("planView").findall("geometry")
            ok = False
            for g in gs:
                if g.find("line") == None:
                    ok = True
                    break
            if curve == False or ok:
                print("Randomly select: ", id)
                return id

    def getRandomId2(self, curve):
        while True:
            id, laneId = random.choice(self.candidateLanes)
            gs = XParser.roads[id].find("planView").findall("geometry")
            ok = False
            for g in gs:
                if g.find("line") == None:
                    ok = True
                    break
            if curve == False or ok:
                print("Randomly select: ", id, laneId)
                return id, laneId

    def get_ego_route_roads(self):
        self.setCandidates(1, 0)
        if len(self.candidateRoads) == 0:
            return []
        # 排除ego出生在道路重叠部分对出生点的修改
        if len(JParser.egoTs) > 1 and self.candidateRoads:
            for road_info in JParser.egoTs:
                if road_info[0] in self.candidateRoads:
                    self.candidateRoads.remove(road_info[0])
        return self.candidateRoads


sPicker = scenePicker()
