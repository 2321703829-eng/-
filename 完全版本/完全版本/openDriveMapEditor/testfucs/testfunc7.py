import numpy as np
import json

def modify_bezier_by_bending(points, target_d_start=None, target_d_end=None):
  """
  通过指定新的起点/终点弯曲度来修改一条三阶贝塞尔曲线。

  此函数固定L0和L3，并保持原始曲线的“布局向量P”不变，
  仅根据新的目标“弯曲度d”来反向求解新的L1和L2。

  参数:
      points (list of tuples): 原始曲线的四个控制点。
      target_d_start (float, optional): 新的目标起点弯曲度。如果为None，则使用原始值。
      target_d_end (float, optional): 新的目标终点弯曲度。如果为None，则使用原始值。

  返回:
      list of tuples: 修改后新的四个控制点坐标。
  """
  # 1. 解包并计算基础向量
  L0, L1, L2, L3 = [np.array(p, dtype=float) for p in points]
  V = L3 - L0
  L_sq = np.dot(V, V)

  if L_sq == 0:
    print("Warning: Start and end points are identical. Cannot modify.")
    return points

  # 2. 计算原始曲线的形态描述符 (d和p)
  # 弯曲
  d_start_orig = np.linalg.det(np.array([V, L1 - L0]))
  d_end_orig = np.linalg.det(np.array([V, L2 - L3]))

  # 布局 (对称定义)
  p_start_orig = np.dot(L1 - L0, V) / L_sq
  p_end_orig = np.dot(L3 - L2, V) / L_sq

  # 3. 确定最终的目标值
  final_d_start = d_start_orig if target_d_start is None else target_d_start
  final_d_end = d_end_orig if target_d_end is None else target_d_end
  # 布局参数作为约束保持不变
  final_p_start = p_start_orig
  final_p_end = p_end_orig


  # 4. 求解新的 L1'
  # 我们需要解一个 2x2 线性方程组 Ax = b
  # 方程1 (来自p_start): (x1-x0)*vx + (y1-y0)*vy = final_p_start * L_sq
  #   => x1*vx + y1*vy = final_p_start * L_sq + x0*vx + y0*vy
  # 方程2 (来自d_start): (x1-x0)*vy - (y1-y0)*vx = -final_d_start
  #   => x1*vy - y1*vx = -final_d_start + x0*vy - y0*vx

  A1 = np.array([[V[0], V[1]], [V[1], -V[0]]])
  b1 = np.array([final_p_start * L_sq + np.dot(L0, V),
                 -final_d_start + np.linalg.det(np.array([L0, V]))])
  L1_new = np.linalg.solve(A1, b1)

  # 5. 求解新的 L2'
  # 方程1 (来自p_end): (x3-x2)*vx + (y3-y2)*vy = final_p_end * L_sq
  #   => -x2*vx - y2*vy = final_p_end * L_sq - x3*vx - y3*vy
  #   =>  x2*vx + y2*vy = -final_p_end * L_sq + x3*vx + y3*vy
  # 方程2 (来自d_end): (x2-x3)*vy - (y2-y3)*vx = -final_d_end
  #   => x2*vy - y2*vx = -final_d_end + x3*vy - y3*vx

  A2 = np.array([[V[0], V[1]], [V[1], -V[0]]])
  b2 = np.array([-final_p_end * L_sq + np.dot(L3, V),
                 -final_d_end + np.linalg.det(np.array([L3, V]))])
  L2_new = np.linalg.solve(A2, b2)

  return [tuple(L0), tuple(L1_new), tuple(L2_new), tuple(L3)]

def print_result(analysis_data):
  print("计算结果:")
  print(f"--(C型，S型)分量：({analysis_data['Sc']:.3f}, {analysis_data['Ss']:.3f})-->{analysis_data['shape_type']}")
  print(f"--两端偏离度(p_start,p_end)：{analysis_data['p_start']:.3f}, {analysis_data['p_end']:.3f}")
  print(f"--(L1:{analysis_data['dis_start']},L2:{analysis_data['dis_end']})")
  print(f"--总体弯曲度（S）：{analysis_data['S']:.3f}")
  print(f"--起始端偏离度(d_start):{analysis_data['d_start']:.3f}")
  print(f"--终点偏离度(d_end):{analysis_data['d_end']:.3f}")
  print(f"--总趋势：{analysis_data['type']}")
# --- 示例 ---
if __name__ == "__main__":
  # 定义一条原始曲线 (一个常规的C型)
  original_points = [(1, 2), (2.5, 4), (7.5, 4), (10, 1)]
  print(f"原坐标:{original_points}")
  # 分析原始曲线的弯曲度
  from testfucs.testfunc6 import analyze_bezier_shape,plot_bezier_curve

  original_analysis = analyze_bezier_shape(original_points)
  print("原始曲线分析:")
  print_result(original_analysis)
  # print(f"  新曲线: {json.dumps(original_analysis, indent=4, ensure_ascii=False)}")
  print(f"  d_start: {original_analysis['d_start']:.2f}, d_end: {original_analysis['d_end']:.2f}")

  # 目标：我们希望起点弯曲减半，终点弯曲加倍，并变成S型
  target_start = original_analysis['d_start']
  target_end = 36.0
  # print(f"\n修改目标: d_start -> {target_start}, d_end -> {target_end}\n")

  plot_bezier_curve(original_points)
  # 调用修改函数
  modified_points = modify_bezier_by_bending(original_points,
                                             target_d_start=target_start,
                                             target_d_end=target_end)

  print("修改后的新坐标:")
  print(f"  L0: {modified_points[0]}")
  print(f"  L1: ({modified_points[1][0]:.2f}, {modified_points[1][1]:.2f})")
  print(f"  L2: ({modified_points[2][0]:.2f}, {modified_points[2][1]:.2f})")
  print(f"  L3: {modified_points[3]}")


  # 验证一下新曲线的弯曲度是否符合我们的目标
  new_analysis = analyze_bezier_shape(modified_points)
  print("\n验证修改后的曲线:")
  print_result(new_analysis)
  # print(f"  新曲线: {json.dumps(new_analysis, indent=4, ensure_ascii=False)}")
  print(f"  新的 d_start: {new_analysis['d_start']:.2f} (目标: {target_start})")
  print(f"  新的 d_end: {new_analysis['d_end']:.2f} (目标: {target_end})")
  plot_bezier_curve(modified_points)