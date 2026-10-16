import numpy as np
import matplotlib.pyplot as plt
from scipy.integrate import quad

def analyze_bezier_shape(points):
  """
  计算并分析三阶贝塞尔曲线的“完整形态描述符”。

  此函数基于我们共同完善的双向量模型，该模型包含一个“弯曲向量”
  和一个“布局向量”，用于全面描述曲线的宏观形态。

  参数:
      points (list of tuples): 包含四个控制点坐标的列表或元组。
                               格式: [(x0, y0), (x1, y1), (x2, y2), (x3, y3)]

  返回:
      dict: 一个包含所有关键定义参数的字典。如果起点和终点重合，
            则返回包含零值和提示信息的字典。
  """
  # --- 1. 基础向量定义 ---
  # 将输入点转换为NumPy数组以便于向量运算
  L0, L1, L2, L3 = [np.array(p, dtype=float) for p in points]
  # 基准向量
  V = L3 - L0

  # 计算基准向量长度的平方，用于归一化
  L_sq = np.dot(V, V)

  # 处理退化情况：起点和终点重合
  if L_sq == 0:
    return {
      "status": "输入点位错误",
    }

  # --- 2. 弯曲向量 S 的计算 ---
  V_start = L1 - L0
  V_end = L2 - L3

  # 使用行列式（二维叉乘）计算偏离量
  d_start = np.linalg.det(np.array([V, V_start])) / L_sq
  d_end = np.linalg.det(np.array([V, V_end])) / L_sq

  # 计算C型和S型分量
  Sc = (d_start + d_end)
  Ss = (d_start - d_end)

  # 计算总体弯曲度
  magnitude = np.sqrt(Sc ** 2 + Ss ** 2)

  # --- 3. 布局向量 P 的计算 ---
  V1 = L1 - L0
  V2 = L3 - L2

  # 使用点积计算纵向位置参数
  p_start = np.dot(V, V1) / L_sq
  p_end = np.dot(V, V2) / L_sq

  # --- 4. 结果整合 ---
  # 判断曲线类型
  if d_start * d_end > 0:
    shape_type = "C型占主要"
  elif d_start * d_end < 0:
    shape_type = "S型占主要"
  else:
    # 其中一个偏离量为0，可能是直线或带有尖锐转折的曲线
    shape_type = "Line or Degenerate"

  if abs(Sc)>abs(Ss):
    shape_type = "C型占主要"
  elif abs(Sc)<abs(Ss):
    shape_type = "S型占主要"

  dis = "无记录"
  if 0<p_start<1 and 0<p_end<1:
    if shape_type=="C型占主要":
      if p_start<p_end:
        dis = "起点更弯曲的C型"
      elif p_start>p_end:
        dis = "终点更弯曲的C型"
    else:
      if  d_start < d_end:
        dis = "起点陡，终点缓"
      elif d_start > d_end:
        dis = "终点陡，起点缓"
    if p_start==p_end and (Sc==0 or Ss==0):
      if Sc==0:
        dis = "对称S型"
      elif Ss==0:
        dis = "对称C型"
  elif (p_start<0<p_end<1) or (0 < p_start < 1 < p_end):
    if p_start<0<p_end<1:
      if shape_type=="C型占主要":
        dis = "起点附近大幅度弯曲，终点端较为平缓"
      else:
        dis="起点附近大幅度弯曲，终点端较为平缓的S型曲线"
    else:
      if shape_type=="C型占主要":
        dis = "终点附近大幅度弯曲，起点端较为平缓"
      else:
        dis="终点附近大幅度弯曲，起点端较为平缓的S型曲线"
  elif (0>p_start or p_start>1) and (0>p_end or p_end>1):
    if shape_type == "C型占主要":
      dis = "大幅度内扣的C型"
    else:
      dis = "两端大幅度弯曲中间段平缓的S形曲线"

  dis_start = "无"
  if 0 <= p_start <= 1:
    dis_start = "两点之间"
  elif p_start < 0:
    dis_start = "L3L0的延长线方向"
  else:
    dis_start = "L0L3的延长线方向"

  dis_end = "无"
  if 0 <= p_end <= 1:
    dis_end = "两点之间"
  elif p_end < 0:
    dis_end = "L0L3的延长线方向"
  else:
    dis_end = "L3L0的延长线方向"


  # results = {
  #   f"（C型，S型）分量({shape_type})": (Sc, Ss),
  #   f"两端偏离度(L1:{dis_start},L2:{dis_end})(Sc,Ss)": (p_start, p_end),
  #   "总体弯曲度（S）": magnitude,
  #   "起始端偏离度(d_start)": d_start,
  #   "终点偏离度(d_end)": d_end,
  #   "总趋势": dis
  # }
  results={
    "Sc":Sc,
    "Ss":Ss,
    "d_start":d_start,
    "d_end":d_end,
    "dis_start":dis_start,
    "dis_end":dis_end,
    "S":magnitude,
    "p_start":p_start,
    "p_end": p_end,
    "shape_type":shape_type,
    "type":dis
  }
  return results

def plot_bezier_curve(points, title="Bézier curve"):
  """
  使用matplotlib绘制贝塞尔曲线、控制点和控制多边形。
  """
  # 将点转换为NumPy数组
  p = np.array(points)

  # 生成贝塞尔曲线上的点
  t = np.linspace(0, 1, 100)
  curve_points = (1 - t) ** 3 * p[0][:, np.newaxis] + \
                 3 * (1 - t) ** 2 * t * p[1][:, np.newaxis] + \
                 3 * (1 - t) * t ** 2 * p[2][:, np.newaxis] + \
                 t ** 3 * p[3][:, np.newaxis]

  # 绘图
  plt.figure(figsize=(8, 6))

  # 绘制贝塞尔曲线
  plt.plot(curve_points[0], curve_points[1], 'b-', label="Bézier curve")

  # 绘制控制多边形
  plt.plot(p[:, 0], p[:, 1], 'r--o', label="Control Polygon")

  # 标记控制点
  for i, (x, y) in enumerate(p):
    plt.text(x, y + 0.5, f'L{i}', fontsize=12, ha='center')

  plt.title(title, fontsize=14)
  plt.xlabel("X")
  plt.ylabel("Y")
  plt.legend()
  plt.grid(True)
  plt.axis('equal')  # 保持x,y轴比例一致，形状不会失真
  plt.show()


def print_result(analysis_data):
  print("计算结果:")
  print(f"--(C型，S型)分量：({analysis_data['Sc']:.3f}, {analysis_data['Ss']:.3f})-->{analysis_data['shape_type']}")
  print(f"--两端偏离度(p_start,p_end)：{analysis_data['p_start']:.3f}, {analysis_data['p_end']:.3f}")
  print(f"--(L1:{analysis_data['dis_start']},L2:{analysis_data['dis_end']})")
  print(f"--总体弯曲度（S）：{analysis_data['S']:.3f}")
  print(f"--起始端偏离度(d_start):{analysis_data['d_start']:.3f}")
  print(f"--终点偏离度(d_end):{analysis_data['d_end']:.3f}")
  print(f"--总趋势：{analysis_data['type']}")




def B_prime(t, P0, P1, P2, P3):
  return 3 * (1 - t) ** 2 * (P1 - P0) + \
    6 * (1 - t) * t * (P2 - P1) + \
    3 * t ** 2 * (P3 - P2)


def B_double_prime(t, P0, P1, P2, P3):
  return 6 * (1 - t) * (P2 - 2 * P1 + P0) + \
    6 * t * (P3 - 2 * P2 + P1)


# 被积函数
def integrand(t, points):
  P0, P1, P2, P3=points
  v = B_prime(t, P0, P1, P2, P3)
  a = B_double_prime(t, P0, P1, P2, P3)

  # 如果是2D, cross_product_norm 是一个标量
  cross_product_norm = np.abs(np.cross(v, a))

  v_norm_sq = np.dot(v, v)

  if v_norm_sq < 1e-12:  # 避免除以零，例如在尖点
    return 0.0

  return cross_product_norm / v_norm_sq
# --- 主程序：验证我们之前讨论的例子 ---
if __name__ == "__main__":
  # 定义所有示例
  examples = {
    "yuanshi": [
      (0, 0), (1, 5), (8, 2), (10, 0)
    ],
    "yuanshi2": [
      (0, 0), (10, 50), (80, 20), (100, 0)
    ],

    "接近直线": [
      (0, 0), (3, 0.1), (7, 0.1), (10, 0)
    ],
    " C 型": [
      (0.5, 0.5), (2.5, 4), (7.5, 4), (10, 0.5)
    ],
    "更弯的 C 型": [
      (0, 0), (2.5, 8), (7.5, 8), (10, 0)
    ],
    "非对称C型1": [
      (0, 0), (2, 4), (5, 4), (10, 0)
    ],
    "左侧超范围C型1": [
      (0, 0), (-1, 2), (4, 2), (6, 0)
    ],
    "两侧超范围C型1": [
      (0, 0), (-1, 2), (7, 3), (6, 0)
    ],

    "对称 S 型": [
      (0, 0), (2.5, 4), (7.5, -4), (10, 0)
    ],
    "更弯的 S 型": [
      (0, 0), (2.5, 8), (7.5, -8), (10, 0)
    ],
    "非对称S型2": [
      (0, 0), (1, -1), (4, 2), (6, 0)
    ],
    "非对称S型1": [
      (0, 0), (1, 0.5), (4, -2), (6, 0)
    ],
    "不对称复合型 (偏S)": [
      (0, 0), (3, 8), (8, -2), (10, 0)
    ],
    "左侧超范围S型1": [
      (0, 0),(-1, 2),(4, -2),(6, 0)
    ],
    "逆向S": [
      (0,0), (-0.5,1.805),(-2.64,-0.31),(-4.99,2.35)
    ],
    "逆向超边界S": [
      (0,0), (1,1.805),(-7.64,-0.31),(-4.99,2.35)
    ],

  }



  # 循环遍历每个例子，进行计算和绘图
  for name, points in examples.items():
    print(f"\n--- 分析案例: {name} ---")
    print(f"输入坐标: {points}")

    # 调用核心函数进行计算
    analysis_data = analyze_bezier_shape(points)
    total_curvature, error = quad(integrand, 0, 1, args=(np.array(points)))
    # 格式化输出结果
    print("计算结果:")
    print(f"--(C型，S型)分量：({analysis_data['Sc']:.3f}, {analysis_data['Ss']:.3f})-->{analysis_data['shape_type']}")
    print(f"--两端偏离度(p_start,p_end)：{analysis_data['p_start']:.3f}, {analysis_data['p_end']:.3f}")
    print(f"--(L1:{analysis_data['dis_start']},L2:{analysis_data['dis_end']})")
    print(f"--总体弯曲度（S）：{analysis_data['S']:.3f}")
    print(f"--起始端偏离度(d_start):{analysis_data['d_start']:.3f}")
    print(f"--终点偏离度(d_end):{analysis_data['d_end']:.3f}")
    print(f"--总趋势：{analysis_data['type']}")
    print(f"--总曲率：{total_curvature}")



    plot_bezier_curve(points)