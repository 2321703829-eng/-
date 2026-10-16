from collections import deque, defaultdict
import copy
import json
import os

from xodrs.xodrParser import XParser
from xodrs.xodrDataGetter import OpenDriveMapLocator, xDataGetter

import utils.path as path
from utils.lambdas import ang2hdg


class jsonParser:
    def __init__(self):
        # json文件data
        self.data = deque()
        # [序号]=[道路id] [车道id] ={车辆序号, 这辆车的路径点序号, 路面上(弧线)距离, 车离参考线距离距离, 与参考线角度差}
        self.carPosition = deque()
        # [道路id, 车道id, 这里应该是车道上第几个路径点？]
        self.egoTs = []
        self.egoDs = []
        self._activeLocator = None

    def addData(self, new_val):
        self.data.append(copy.deepcopy(new_val))
        if len(self.data) > 256:
            self.data.popleft()

    def addCarData(self, new_val):
        self.carPosition.append(copy.deepcopy(new_val))
        if len(self.carPosition) > 256:
            self.carPosition.popleft()

    def undo(self):
        if len(self.data) > 1:
            self.data.pop()
            self.carPosition.pop()

    def reInit(self):
        if len(self.data) > 1:
            self.data = deque([self.data[0]])
            self.carPosition = deque([self.carPosition[0]])

    def clearAll(self):
        self.data.clear()
        self.carPosition.clear()
        self.egoTs.clear()
        self.egoDs.clear()

    def pushNewData(self):
        self.addData(self.data[-1])
        self.addCarData(self.carPosition[-1])

    def readJson(self, name):
        print("FINDING THE POSITION OF CARS...")
        try:
            with open(path.jsonPath + name + ".json", "r") as file:
                jsonData = json.load(file)
        except FileNotFoundError:
            print(f"Xodr Error: File not found: {path.jsonPath+name}.xodr")
            return False
        except json.JSONDecodeError:
            print("Error: Invalid JSON format!")
            return False
        self.clearAll()
        self.addData(jsonData)
        # self.rectifyJsonData()
        self.updateCarData()
        return True

    def updateCarData(self):
        carInfos = defaultdict(dict)

        for id, road in XParser.roads.items():
            carInfos[id] = defaultdict(dict)
            lanes = road.find("lanes").findall(".//lane")
            for lane in lanes:
                # 结构：[道路id][车道id]=[]
                carInfos[id][lane.get("id")] = []

        self._activeLocator = self._newRoadLocator()
        agents = self.data[-1]["agents"]
        for i in range(len(agents)):
            tPos = agents[i]["transform"]["position"]
            tRot = agents[i]["transform"]["rotation"]
            # 返回【道路id，车道id，路面上(弧线)距离，车离参考线距离距离，角度差】
            carTs = self.findRoad(tPos, tRot)
            carT = carTs[0] if carTs is not None else None
            # ego车
            if agents[i]["uid"] != "":
                dPos = agents[i]["destinationPoint"]["position"]
                dRot = agents[i]["destinationPoint"]["rotation"]
                carDs = self.findRoad(dPos, dRot)
                carD = carDs[0] if carDs is not None else None
                if carT is not None and carD is not None:
                    # 一个待选道路列表[【道路id，车道id，路面上(弧线)距离，车离参考线距离距离，角度差】,,,,]
                    self.egoTs.extend(carTs)
                    self.egoDs.extend(carDs)
                    # [道路id] [车道id] ={车辆序号, 这辆车的路径点序号, 路面上(弧线)距离, 车离参考线距离距离, 角度差}
                    carInfos[carT[0]][carT[1]].append(
                        {
                            "carId": i,
                            "ordId": 0,
                            "pos": carT[2],
                            "dis": carT[3],
                            "dhdg": carT[4],
                        }
                    )
                    carInfos[carD[0]][carD[1]].append(
                        {
                            "carId": i,
                            "ordId": 1,
                            "pos": carD[2],
                            "dis": carD[3],
                            "dhdg": carD[4],
                        }
                    )
                    print("Ego found!")
                else:
                    print("Ego not found: ", i)

            else:
                wayPoints = agents[i].get("waypoints", [])
                trajectoryMatches = self._findTrajectory(tPos, tRot, wayPoints)
                if trajectoryMatches:
                    carTs = self._legacyCandidates(
                        trajectoryMatches[0].candidates,
                        trajectoryMatches[0].location,
                    )
                    carT = carTs[0] if carTs else None
                if carT is not None:
                    carInfos[carT[0]][carT[1]].append(
                        {
                            "carId": i,
                            "ordId": 0,
                            "pos": carT[2],
                            "dis": carT[3],
                            "dhdg": carT[4],
                        }
                    )
                else:
                    print("Not found: ", i, " ", 0)
                if "waypoints" in agents[i]:
                    for j in range(len(wayPoints)):
                        pos = wayPoints[j]["position"]
                        rot = wayPoints[j]["angle"]
                        speed = wayPoints[j]["speed"]
                        match = trajectoryMatches[j + 1]
                        points = self._legacyCandidates(
                            match.candidates,
                            match.location,
                        )
                        point = points[0] if points else None
                        # 在这里判断waypoint的精确位置
                        if point is not None:
                            # 在当前车道上的每一个路径点 [道路id] [车道id] ={车辆序号, 这辆车的路径点序号, 路面上(弧线)距离, 车离参考线距离距离, 角度差}
                            carInfos[point[0]][point[1]].append(
                                {
                                    "carId": i,
                                    "ordId": j + 1,
                                    "pos": point[2],
                                    "dis": point[3],
                                    "dhdg": point[4],
                                }
                            )
                        else:
                            print("waypoints Not found: ", i, " ", j + 1)

        self._activeLocator = None
        print("FINDING ENDED.")
        self.addCarData(carInfos)

    def writeJson(self):
        os.makedirs(path.savePath or ".", exist_ok=True)
        with open(path.savePath + path.jsonSaveName + ".json", "w") as file:
            json.dump(self.data[-1], file, indent=4)
            file.write("\n")
        pass

    def rectifyJsonData(self):
        agents = self.data[-1]["agents"]
        index, length = 0, len(agents)
        for i in range(length):
            index = i
            if agents[i]["uid"] != "":
                agents[i]["initial_speed"] = 0
            else:
                break
        for i in range(index, length):
            agents.pop()

    # json中的名字
    def editSaveName(self, saveName):
        map = self.data[-1]["map"]
        # xodr地图名
        map["name"] = saveName
        return "xodrsavename: " + map["name"]

    """
  def findRoad(self, pos, rot):
      input:
        pos:["transform"]["position"]
        rot:["transform"]["rotation"]      

      output:
        candidateRoads[0]
        [id,    order,  ansL,             ans,                deltaHdg]
        道路id, 车道id,  路面上(弧线)距离， 车离参考线距离距离，  车辆与参考线角度差
                                            |
                           距离参考线的距离使用正负来区分在左侧还是右侧
  """

    ## PRIVATE METHOD
    def _newRoadLocator(self):
        return OpenDriveMapLocator(XParser.root, XParser.roads.values())

    def _legacyCandidate(self, location):
        deltaHdg = 0 if location.heading_error is None else location.heading_error
        return [
            location.road_id,
            str(location.lane_id),
            location.s,
            -location.t,
            deltaHdg,
        ]

    def _legacyCandidates(self, candidates, selected=None):
        orderedCandidates = list(candidates)
        if selected is not None:
            orderedCandidates = [selected] + [
                candidate
                for candidate in orderedCandidates
                if candidate != selected
            ]
        candidateRoads = [
            self._legacyCandidate(candidate)
            for candidate in orderedCandidates
        ]
        return candidateRoads

    def _findTrajectory(self, tPos, tRot, wayPoints):
        positions = [tPos] + [
            waypoint["position"]
            for waypoint in wayPoints
        ]
        yawDegrees = [tRot.get("y")] + [
            waypoint["angle"].get("y")
            for waypoint in wayPoints
        ]
        locator = self._activeLocator or self._newRoadLocator()
        return locator.locate_trajectory(
            positions,
            yawDegrees,
            road_edge_margin=1.0,
            road_endpoint_margin=0.1,
            resolve_remaining=True,
        )

    def findRoad(self, pos, rot, speed=1):
        """
        返回一个待选道路列表
        其中每个元素结构为【道路id，车道id，路面上(弧线)距离，车离参考线距离距离，角度差】
        """

        locator = self._activeLocator or self._newRoadLocator()
        carHdg = ang2hdg(rot["y"]) if "y" in rot else None
        matches = locator.locate_xy(
            pos["x"],
            pos["z"],
            carHdg,
            map_z=pos.get("y"),
        )
        candidateRoads = self._legacyCandidates(matches)
        return candidateRoads if candidateRoads else None

    def projectPoint(self, road, tarX, tarY, stop_car=False):
        projection = xDataGetter.projectRoadPoint(road, tarX, tarY)
        if projection is None:
            return float("inf"), 0
        return -projection.t, projection.s


JParser = jsonParser()
