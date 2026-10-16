import numpy as np
import matplotlib.pyplot as plt
from scipy.special import comb


def visualize_bezier_max_adjacent_distance(control_points, num_samples=50):
    """
    可视化贝塞尔曲线采样点分布，并展示相邻采样点中距离最远的一段。
    这意味着在均匀的 t 变化下，该段曲线的弧长（近似）最大，即瞬时速度最快。

    参数:
    control_points (list of tuples/lists): 控制点坐标列表
    num_samples (int): t从0到1的采样数量
    """
    points = np.array(control_points)
    n_points = len(points)
    n_order = n_points - 1

    # 1. 生成贝塞尔曲线上的采样点
    t = np.linspace(0, 1, num_samples)
    curve_points = np.zeros((num_samples, 2))

    for i in range(n_points):
        # 伯恩斯坦基函数
        bernstein = comb(n_order, i) * (t ** i) * ((1 - t) ** (n_order - i))
        curve_points += points[i] * bernstein[:, np.newaxis]

    # 2. 计算相邻采样点之间的距离 (Step size)
    # curve_points[1:] 是后一个点数组, curve_points[:-1] 是前一个点数组
    deltas = curve_points[1:] - curve_points[:-1]
    # 计算欧几里得距离 (L2 norm)
    adjacent_dists = np.sqrt(np.sum(deltas ** 2, axis=1))

    # 3. 找到最大相邻距离及其索引
    max_idx = np.argmax(adjacent_dists)
    max_dist = adjacent_dists[max_idx]

    # 获取距离最远的那两个相邻点
    p_start = curve_points[max_idx]
    p_end = curve_points[max_idx + 1]

    # 4. 图形化展示
    plt.figure(figsize=(10, 6), dpi=100)

    # A. 绘制控制多边形
    plt.plot(points[:, 0], points[:, 1], 'k--o', alpha=0.3, label='Control Polygon')
    for i, (px, py) in enumerate(points):
        plt.text(px, py, f'$P_{i}$', fontsize=12, verticalalignment='bottom')

    # B. 绘制贝塞尔曲线轨迹
    plt.plot(curve_points[:, 0], curve_points[:, 1], 'b-', alpha=0.3, label='Bezier Trace')

    # C. 绘制所有采样点
    plt.scatter(curve_points[:, 0], curve_points[:, 1], c='blue', s=15, alpha=0.6, label='Sampled Points')

    # D. 高亮显示最长的相邻段
    plt.plot([p_start[0], p_end[0]], [p_start[1], p_end[1]], 'r-', linewidth=3, label=f'Max Step: {max_dist:.4f}')
    plt.scatter([p_start[0], p_end[0]], [p_start[1], p_end[1]], c='red', s=60, zorder=5)

    # 标注信息
    mid_x = (p_start[0] + p_end[0]) / 2
    mid_y = (p_start[1] + p_end[1]) / 2

    plt.annotate(f'Max Gap: {max_dist:.4f}\n(t={t[max_idx]:.2f}->{t[max_idx + 1]:.2f})',
                 xy=(mid_x, mid_y),
                 xytext=(mid_x + 1, mid_y - 2),
                 arrowprops=dict(facecolor='red', shrink=0.05),
                 bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="red", lw=1))

    plt.title(f'Bezier Curve Adjacent Sampling Gaps\n(Max Gap indicates highest velocity region)')
    plt.xlabel('X')
    plt.ylabel('Y')
    plt.axis('equal')
    plt.grid(True, linestyle=':', alpha=0.6)
    plt.legend(loc='best')

    plt.show()

    return max_dist, p_start, p_end


# --- 示例调用 ---
if __name__ == "__main__":
    # 使用一组容易产生速度变化的控制点
    # 中间的控制点离得远，会导致中间部分的曲线“拉伸”，采样点变稀疏，间距变大
    control_pts = [[123.498146,321],[253.562017,446],[578.721694,135],[630,480]]

    max_d, p_s, p_e = visualize_bezier_max_adjacent_distance(control_pts, num_samples=1000)
    print(f"最大相邻点间距: {max_d:.4f}")
    print(f"起始点: {p_s}")
    print(f"结束点: {p_e}")