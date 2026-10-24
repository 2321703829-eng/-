import numpy as np
import matplotlib.pyplot as plt
from scipy.integrate import quad
from testfucs.testfunc6 import analyze_bezier_shape,plot_bezier_curve,print_result
# --- 贝塞尔曲线基础函数 ---
def get_bezier_point(points, t):
  t_inv = 1 - t
  b = (t_inv ** 3 * points[0] +
       3 * t_inv ** 2 * t * points[1] +
       3 * t_inv * t ** 2 * points[2] +
       t ** 3 * points[3])
  return b


def get_bezier_2nd_derivative(points):
  """返回二阶导数曲线的控制点 (用于计算能量)"""
  p0, p1, p2, p3 = points
  # B''(t) = 6(1-t)(L2-2L1+L0) + 6t(L3-2L2+L1)
  # 这是一个线性贝塞尔曲线 B(t) = (1-t)A + tB
  A = 6 * (p2 - 2 * p1 + p0)
  B = 6 * (p3 - 2 * p2 + p1)
  # 能量 Loss = ∫|B''(t)|² dt = A·A + A·B + B·B
  energy = np.dot(A, A) + np.dot(A, B) + np.dot(B, B)
  return energy


def calculate_d_values(points):
  L0, L1, L2, L3 =[np.array(p, dtype=float) for p in points]
  V = L3 - L0
  L_sq = np.dot(V, V)
  d_start = np.linalg.det(np.array([V, L1 - L0]))
  d_end = np.linalg.det(np.array([V, L2 - L3]))
  return d_start, d_end


# --- 梯度下降核心 ---
def get_gradient_numeric(points, loss_function, loss_params):
  """使用有限差分法数值计算梯度"""
  epsilon = 1e-5
  grad = np.zeros_like(points[1:3])  # 只计算L1, L2的梯度

  for i in range(2):  # 遍历L1, L2
    for j in range(2):  # 遍历x, y
      points_plus = np.copy(points)
      points_plus[i + 1][j] += epsilon
      loss_plus = loss_function(points_plus, **loss_params)

      points_minus = np.copy(points)
      points_minus[i + 1][j] -= epsilon
      loss_minus = loss_function(points_minus, **loss_params)

      grad[i][j] = (loss_plus - loss_minus) / (2 * epsilon)

  return grad


def loss_function(points, d_targets, original_points, weights, mode):
  """计算总损失"""
  d_start, d_end = calculate_d_values(points)

  # 主要目标损失
  loss_d = (d_start - d_targets[0]) ** 2 + (d_end - d_targets[1]) ** 2

  total_loss = weights['d'] * loss_d

  if mode == 'fairness':
    # 平滑度/渐变损失：惩罚与原始点的距离
    loss_f = np.sum((points[1:3] - original_points[1:3]) ** 2)
    total_loss += weights['f'] * loss_f

  elif mode == 'energy':
    # 能量损失：惩罚曲线自身的不平滑度
    loss_e = get_bezier_2nd_derivative(points)
    total_loss += weights['e'] * loss_e

  return total_loss


def gradient_descent_optimizer(initial_points, d_targets, mode, weights, iterations=200, learning_rate=0.001):
  """梯度下降优化器"""
  points = np.array(initial_points, dtype=float)
  original_points = np.copy(points)

  L_sq=np.linalg.norm(points[3]-points[0])**2

  nd_targets=np.array(d_targets)*L_sq
  print(f"L_sq={L_sq}")
  loss_params = {'d_targets': nd_targets, 'original_points': original_points, 'weights': weights, 'mode': mode}

  for i in range(iterations):
    grad = get_gradient_numeric(points, loss_function, loss_params)
    # 更新 L1 和 L2
    points[1:3] -= learning_rate * grad

  return [tuple(p) for p in points]

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

# --- 场景设定与执行 ---
if __name__ == "__main__":
  # 1. 场景设定
  original_points =  (0, 0), (1, 5), (8,- 2), (10, 0)
  d_targets = (0.01, 1)
  print_result(analyze_bezier_shape(original_points))

  # 设定权重 (这些是超参数，需要调试)
  weights = {
    'd': 1.0,  # 弯曲度匹配的权重
    'f': 0.2,  # 平滑度/渐变约束的权重
    'e': 0.0001  # 能量约束的权重 (能量值通常很大，所以权重需要设小)
  }
  total_curvature, error = quad(integrand, 0, 1, args=(np.array(original_points)))
  print(f"Total Curvature: {total_curvature}")
  print(f"Estimated Error: {error}")
  # 2. 执行两种不同的优化
  print("正在执行优化，请稍候...")
  points_fairness = gradient_descent_optimizer(original_points, d_targets, 'fairness', weights, iterations=3000, learning_rate=0.001)
  # print(points_fairness)
  # points_energy = gradient_descent_optimizer(original_points, d_targets, 'energy', weights, iterations=3000, learning_rate=0.001)
  print("优化完成！")
  print_result(analyze_bezier_shape(points_fairness))
  total_curvature, error = quad(integrand, 0, 1, args=(np.array(points_fairness)))
  print(f"Total Curvature: {total_curvature}")
  print(f"Estimated Error: {error}")
  # 3. 可视化对比
  plt.figure(figsize=(12, 8))

  curves = {
    'Original': original_points,
    'Result (Fairness Constraint)': points_fairness,
    # 'Result (Bending Energy Constraint)': points_energy,
  }
  styles = {
    'Original': 'k--o',
    'Result (Fairness Constraint)': 'r--o',
    # 'Result (Bending Energy Constraint)': 'b-s',
  }

  for name, p in curves.items():
    # 绘制曲线
    t = np.linspace(0, 1, 100)
    curve_pts = np.array([get_bezier_point(np.array(p), val) for val in t])
    plt.plot(curve_pts[:, 0], curve_pts[:, 1], styles[name][0], label=name + " Curve")
    # 绘制控制多边形
    p_np = np.array(p)
    plt.plot(p_np[:, 0], p_np[:, 1], styles[name], markersize=8, label=name + " Polygon")

  # 验证最终的d值
  d_start_fair, d_end_fair = calculate_d_values(points_fairness)
  # d_start_energy, d_end_energy = calculate_d_values(points_energy)

  plt.title(f"Constraint Comparison\n"
            f"Target: d_start={d_targets[0]}, d_end={d_targets[1]}\n"
            f"Fairness Result: d_start={d_start_fair:.1f}, d_end={d_end_fair:.1f}\n",
            # f"Energy Result: d_start={d_start_energy:.1f}, d_end={d_end_energy:.1f}",
            fontsize=14)

  plt.legend(fontsize=12)
  plt.grid(True)
  plt.axis('equal')
  plt.xlabel("X")
  plt.ylabel("Y")
  plt.show()
