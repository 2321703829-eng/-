import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from matplotlib import cm

from sklearn.linear_model import Ridge
from td import points,waypoints

def prepare_verification_data():
    """
    加载并解析用于“验证”的JSON格式新数据集。
    """
    verification_data_json =waypoints

    extracted_points = [
        [item["position"]["x"], item["position"]["z"], item["position"]["y"]]
        for item in verification_data_json
    ]
    return np.array(extracted_points)


def filter_points_by_distance(verification_points, fitting_points, max_dist=10.0):
    """
    根据水平距离筛选验证点。
    """
    valid_points = []
    fitting_xy = fitting_points[:,:2]  # 只取拟合点的X, Y坐标

    for v_point in verification_points:
        v_xy = v_point[:2]
        # 计算当前验证点与所有拟合点之间的水平距离
        distances = np.linalg.norm(fitting_xy - v_xy, axis=1)
        # 如果存在任何一个距离小于阈值，则保留该点
        if np.any(distances < max_dist):
            valid_points.append(v_point)

    return np.array(valid_points)

def main(points):
    """
    使用岭回归（Ridge Regression）对三维点云进行4次曲面拟合，以平滑边缘。
    Args:
        points (np.ndarray): 一个 N x 3 的Numpy数组，每一行代表一个 (x, y, z) 点。
    """
    xs = points[:, 0]
    ys = points[:, 1]
    zs = points[:, 2]
    # for i in range(len(points)):
    #     print(f"x:{xs[i]}")
    #     print(f"y:{ys[i]}")
    #     print(f"z:{zs[i]}\n")

    degree = 4

    # 构造多项式特征矩阵 A (在代码中为 X)
    X = []
    for i in range(degree + 1):
        for j in range(degree + 1 - i):
            X.append(xs ** i * ys ** j)
    X = np.array(X).T





    # alpha 是正则化强度参数。值越大，对大系数的惩罚越强，曲面就越平滑。

    alpha_value = 1000.0

    # fit_intercept 设置为 False。
    model = Ridge(alpha=alpha_value, fit_intercept=False, solver='auto')
    model.fit(X, zs)
    coeffs = model.coef_

    # --- 修改结束 ---

    # 定义拟合出的曲面函数
    def fit_function(x, y, coeffs):
        z = np.zeros_like(x)
        k = 0
        for i in range(degree + 1):
            for j in range(degree + 1 - i):
                z += coeffs[k] * x ** i * y ** j
                k += 1
        return z

    valid_verification_points = filter_points_by_distance(prepare_verification_data(), points, max_dist=5.0)
    print(f"\n根据“水平距离<5”的规则，筛选出 {len(valid_verification_points)} 个有效验证点。")
    if len(valid_verification_points) > 0:
        vx, vy, vz_actual = valid_verification_points[:, 0], valid_verification_points[:, 1], valid_verification_points[:, 2]

        vz_fitted = fit_function( vx, vy, coeffs)

        errors = vz_actual - vz_fitted
        mae = np.mean(np.abs(errors))
        rmse = np.sqrt(np.mean(errors ** 2))

        print(f"验证集平均绝对误差 (MAE): {mae:.4f}")
        print(f"验证集均方根误差 (RMSE): {rmse:.4f}")
        print("-" * 90)
        print(f"{'X':>12s} | {'Y':>12s} | {'Original Z':>12s} | {'Fitted Z':>12s} | {'Error':>12s}")
        print("-" * 90)
        for i in range(len(valid_verification_points)):
            print(f"{vx[i]:12.4f} | {vy[i]:12.4f} | {vz_actual[i]:12.4f} | {vz_fitted[i]:12.4f} | {errors[i]:12.4f}")
    else:
        print("没有符合条件的验证点")

    # 可视化
    # 创建用于绘图的网格
    x_grid, y_grid = np.meshgrid(np.linspace(xs.min(), xs.max(), 50),
                                np.linspace(ys.min(), ys.max(), 50))
    z_fit = fit_function(x_grid, y_grid, coeffs)
    # 计算误差（原始值 - 拟合值）
    # 绘图
    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection='3d')

    # 绘制拟合的曲面
    surf = ax.plot_surface(x_grid, y_grid, z_fit, cmap=cm.viridis, alpha=0.7)

    # 绘制原始采样点
    ax.scatter(xs, ys, zs, c='blue', marker='.', s=50, label='Sample Points', depthshade=False)
    if len(valid_verification_points) > 0:
        ax.scatter(vx, vy, vz_actual, c='red', marker='o', s=60, label='Verification Points (Valid)')
    # 设置坐标轴标签和标题
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    ax.set_title(f'4th Degree Surface Fit with Ridge Regularization (alpha={alpha_value})')

    # 添加图例
    surf_proxy = plt.Rectangle((0, 0), 1, 1, fc=cm.viridis(0.5))
    scatter_proxy = plt.Line2D([0], [0], linestyle="none", c='r', marker='.')
    ax.legend([surf_proxy, scatter_proxy], ['Fitted Surface', 'Sample Points'])

    plt.show()


for i in range(len(points)):
    _points=np.array(points[i])
    main(_points)