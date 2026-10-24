import copy
import math
import os
import xml.etree.ElementTree as ET
from collections import deque, defaultdict

import numpy

import utils.path as path
from utils.definitions import defs
from utils.lambdas import getData, findMaxCubic
# from utils.tools import find_crossroads


class xodrParser:
  def __init__(self):
    # xodr文件数据
    self.data = deque()
    self.root = None
    # roads[id]=road对象
    self.roads = defaultdict(dict)
    #                                                   [连接点0的列表(start)，连接点1的列表(end)]
    # roadConnections[roadId1][contactPoint1] = [[[roadId2, laneId2],[roadId3, laneId3],], [[roadId4, laneId4],,,,]]
    self.roadConnections = defaultdict(dict)
    # laneConnections[roadId1][laneiId1][contactPoint1] = [[roadId2, laneId2, contactPoint2], ...]
    self.laneConnections = defaultdict(dict)
    self.roadEdits = defaultdict(dict)
    self.laneEdits = defaultdict(dict)
    self.roadBackup = defaultdict(dict)
    self.laneBackup = defaultdict(dict)
    self.hdgs = defaultdict(dict)
    # 道路单边最大宽度
    self.maxWidths = defaultdict(dict)
    # crossRoads[numid]=[road_id1,road_id2,,,]
    self.crossRoads = defaultdict(dict)

  def addData(self, new_val):
    self.data.append(copy.deepcopy(new_val))
    if len(self.data) > 256:
      self.data.popleft()
    self.root = self.data[-1].getroot()
  def undo(self):
    if len(self.data) > 1:
      self.data.pop()
  def reInit(self):
    if len(self.data) > 1:
      self.data = deque([self.data[0]])

  def writeXodr(self):
    # xodr保存名字
    os.makedirs(path.savePath or ".", exist_ok=True)
    self.data[-1].write(path.savePath+path.saveName+".xodr", encoding="utf-8", xml_declaration=True)
    print('Already saved.')

  def openXodr(self, name):
    try:
      # 将xodr文件读取为结构树格式
      data = ET.parse(path.xodrPath+name+".xodr")
    except FileNotFoundError:
      print(f"Error: File not found: {path.xodrPath+name}.xodr")
      return False
    except ET.ParseError:
      print("Error: Invalid XODR format!")
      return False
    self.clearAll()
    self.addData(data)
    self.updateData()
    return True

  def updateData(self):
    tmpJSets = {}
    for road in self.root.iter('road'):
      # 获取道路id
      id = road.get('id')
      # 储存道路
      self.roads[id] = road
      self.hdgUpdate(road.findall('.//geometry'), id)
      self.widthUpdate(road)

      self.roadConnections[id] = [[], []]
      self.roadBackup[id] = defs.NOT_EDITED
      self.laneConnections[id] = {}
      self.laneBackup[id] = {}
      tmpJSets[id] = [-1, -1]
      for lane in road.findall('.//lane'):
        lid = lane.get('id')
        # 不记录中间参考线
        if lid != '0':
          self.laneBackup[id][lid] = defs.NOT_EDITED
          # 初始化
          self.laneConnections[id][lid] = [[], []]

    for road in self.root.iter('road'):
      id = road.get('id')
      link = road.find('link')
      lanes = road.findall('.//lane')
      junction_id = road.get('junction')

      # 更新道路的首/尾链接
      self.roadUpdate(id, link, lanes, tmpJSets, defs.TAIL)
      self.roadUpdate(id, link, lanes, tmpJSets, defs.HEAD)


    for junction in self.root.iter('junction'):
      id = junction.get('id')
      connections = junction.findall('connection')
      self.junctionUpdate(id, connections, tmpJSets)

    self.crossRoads=self.find_crossroads(self.roadConnections)




  def pushNewData(self):
    self.roads.clear()
    # 复制最后一份数据，方便以后撤销
    self.addData(self.data[-1])
    for road in self.root.iter('road'):
      id = road.get('id')
      self.roads[id] = road

  # PRIVATE FUNCTION

  def clearAll(self):
    self.data.clear()
    self.roads.clear()
    self.roadConnections.clear()
    self.laneConnections.clear()
    self.roadBackup.clear()
    self.laneBackup.clear()
    self.hdgs.clear()
    self.crossRoads.clear()

  def hdgUpdate(self, gs, id):
    h0 = getData(gs[0],  'hdg')
    h1 = getData(gs[-1], 'hdg')
    poly1 = gs[-1].find('paramPoly3')
    # 结束段为paramPoly3
    if poly1 != None:
      bU, cU, dU = getData(poly1, 'bU'), getData(poly1, 'cU'), getData(poly1, 'dU')
      bV, cV, dV = getData(poly1, 'bV'), getData(poly1, 'cV'), getData(poly1, 'dV')
      # 结束段为paramPoly3,道路在该点的全局偏角方向(图32)
      h1 = (h1+math.atan((bV+2*cV+3*dV)/(bU+2*cU+3*dU)))%(2*numpy.pi)
    # 初始化为数组格式
    self.hdgs[id] = []
    self.hdgs[id].append(h0)
    self.hdgs[id].append(h1)

  def widthUpdate(self, road):
    # 返回单条道路内单侧车道的最大宽度
    id = road.get('id')
    lanes = road.find('lanes').findall('.//lane')
    lWidth, rWidth = 0, 0 
    for lane in lanes:
      lid = lane.get('id')
      widths = lane.findall('width')
      maxSingleWidth = 0

      # 寻找单车道最宽的宽度
      for i in range(len(widths)):
        s0 = getData(widths[i], 'sOffset')
        s1 = getData(road, 'length') if i == len(widths)-1 else getData(widths[i+1], 'sOffset')
        w = widths[i]
        a, b = getData(w, 'a'), getData(w, 'b')
        c, d = getData(w, 'c'), getData(w, 'd')

        upperLimit = s1-s0
        singleWidth = findMaxCubic(a, b, c, d, upperLimit)
        maxSingleWidth = max(singleWidth, maxSingleWidth)
      
      if int(lid) > 0:
        lWidth += maxSingleWidth
      elif int(lid) < 0:
        rWidth += maxSingleWidth
    # 比较左车道和右车道的累积宽度，取较大值
    self.maxWidths[id] = max(lWidth, rWidth)

  def roadUpdate(self, id, link, lanes, tmpJSets, dir):
    # 靠右行驶车道水平放置，近侧车道为向右行驶时，左侧为前驱，右侧为后继（此时远侧车道为向左行驶，同样为左前右后）
    if dir == defs.HEAD:
      # 后继
      p_s = link.find('successor')
    else:
      # 前驱
      p_s = link.find('predecessor')

    # 有前驱或者后继
    if p_s != None:
      # 区别contactPoint为路段的首/尾
      # 在定义中head=1,tail=0
      direction = int(p_s.get("contactPoint") == "end")
      p_sId = p_s.get("elementId")
      # 排除掉交叉口，交叉后在
      if p_s.get("elementType") == "road":
        # 未记录则添加junctionUpdate函数中特殊处理
        # roadConnections[roadId1][contactPoint1] = [roadId2, contactPoint2]
        if [p_sId, direction] not in self.roadConnections[id][dir]:
          self.roadConnections[id][dir].append([p_sId, direction])
        if [id, dir] not in self.roadConnections[p_sId][direction]:
          self.roadConnections[p_sId][direction].append([id, dir])
        for lane in lanes:
          # 当前道路的所有车道
          lid = lane.get('id')
          llink = lane.find('link')
          lp_s = llink.find('predecessor')
          if dir == defs.HEAD:
            lp_s = llink.find('successor')

          if lp_s != None:
            p_sLid = lp_s.get('id')
            # 未记录则添加
            # laneConnections[roadId1][laneiId1][contactPoint1] = [[roadId2, laneId2, contactPoint2],...]
            if [p_sId, p_sLid, direction] not in self.laneConnections[id][lid][dir]:
              self.laneConnections[id][lid][dir].append([p_sId, p_sLid, direction])
            if [id, lid, dir] not in self.laneConnections[p_sId][p_sLid][direction]:    
              self.laneConnections[p_sId][p_sLid][direction].append([id, lid, dir])
      else:
        # 说明链接的是交叉口
        # 暂时保存交叉口的数据
        # 保存道路连接点（首/尾）连接的另一条道路的id
        tmpJSets[id][dir] = p_s.get("elementId")

  def junctionUpdate(self, id, connections, tmpJSets):
    # 记录用于区别contactPoint是start还是end
    for connection in connections:
      direction = int(connection.get("contactPoint") == "end")

      incId = connection.get('incomingRoad'  )
      conId = connection.get('connectingRoad')
      # [1]是end对应代码中的head
      point = int(tmpJSets[incId][1] == id)
      point2 = int(tmpJSets[incId][1] != id)
      # 会出现一端链接多个junction的情况，这样只能通过复查之前是否有链接connectRoad来看incomingRoad的链接点了
      if [conId, direction]in self.roadConnections[incId][point2]:
        point = point2
      incLid = connection.find('laneLink').get('from')
      conLid = connection.find('laneLink').get('to'  )
      # 因为被连接的道路会有不止一个来路，所以不能反向添加道路连接（不对，凭什么不能连接！）
      # laneConnections[roadId1][laneiId1][contactPoint1] = [[roadId2, laneId2, contactPoint2],...]
      if [conId, conLid, direction] not in self.laneConnections[incId][incLid][point]:
        self.laneConnections[incId][incLid][point].append([conId, conLid, direction])
      if [incId, incLid, point] not in self.laneConnections[conId][conLid][direction]:
        self.laneConnections[conId][conLid][direction].append([incId, incLid, point])
      if [conId, direction] not in self.roadConnections[incId][point]:
        self.roadConnections[incId][point].append([conId, direction])
      if [incId, point] not in self.roadConnections[conId][direction]:
        self.roadConnections[conId][direction].append([incId, point])

  def find_crossroads(self,road_connect):
    # 创建访问记录
    visited = set()
    crossroads_list = []
    crossroad_id = 0
    # 识别所有初始交叉路口道路（有多条连接的道路）
    initial_roads = set()
    for road_id, connections in road_connect.items():
      for direction in [0, 1]:
        if len(connections[direction]) >= 2:
          # 将这些连接的道路也标记为需要处理的
          for conn in connections[direction]:
            initial_roads.add(conn[0])

    # 从初始交叉路口道路开始扩展
    for road_id in list(road_connect.keys()):
      if road_id in visited:
        continue

      # 检查当前道路是否与交叉路口相关
      is_relevant = road_id in initial_roads
      if not is_relevant:
        # 检查是否是间接相关的：连接了一个有多连接的道路
        for direction in [0, 1]:
          connections = road_connect[road_id][direction]
          if connections:
            next_road, next_dir = connections[0]
            next_connections = road_connect.get(next_road, [[], []])[next_dir]
            if len(next_connections) >= 2:
              is_relevant = True
              break

      if not is_relevant:
        visited.add(road_id)
        continue

      # BFS遍历
      current_group = set()
      queue = deque([road_id])
      visited.add(road_id)
      current_group.add(road_id)

      while queue:
        cur_road = queue.popleft()

        # 处理当前道路的两个方向
        for direction in [0, 1]:
          connections = road_connect.get(cur_road, [[], []])[direction]

          if not connections:
            continue

          # 1: 当前方向有多个连接
          if len(connections) >= 2:
            for conn in connections:
              next_road = conn[0]
              if next_road not in visited:
                visited.add(next_road)
                current_group.add(next_road)
                queue.append(next_road)

          # 2: 当前方向连接单条道路
          elif len(connections) == 1:
            next_road, next_dir = connections[0]
            next_connections = road_connect.get(next_road, [[], []])[next_dir]

            # 被连接道路在该方向有多个连接
            if len(next_connections) >= 2:
              for n_conn in next_connections:
                nn_road = n_conn[0]
                if nn_road not in visited:
                  visited.add(nn_road)
                  current_group.add(nn_road)
                  queue.append(nn_road)

      # 添加到结果列表
      crossroads_list.append([
        crossroad_id,
        list(current_group)
      ])
      crossroad_id += 1

    return crossroads_list
XParser = xodrParser()
