import warnings


# 屏蔽 google.api_core 的 FutureWarning
warnings.filterwarnings("ignore", category=FutureWarning, module="google.api_core")

import json
import random
import time
from scipy.optimize import minimize
import numpy as np
from matplotlib import pyplot as plt

from td import modify_test_cases,m_curve_td

from  llm.curve_test_agent import curveTestAgent

# =======================================================================================
# =======================================================================================
# ====================                工具函数              ===========================
# =======================================================================================
# =======================================================================================

def get_bezier_point(P, t):
    """
    计算三阶贝塞尔曲线上 t (0 <= t <= 1) 对应的点坐标 B(t)
    P0, P1, P2, P3: np.array 格式的控制点
    """
    # B(t) = (1-t)^3 P0 + 3(1-t)^2 t P1 + 3(1-t) t^2 P2 + t^3 P3
    P0, P1, P2, P3 = P
    t = np.asarray(t)
    omt = 1.0 - t
    # 三次Bernstein基
    b0 = omt ** 3
    b1 = 3 * omt ** 2 * t
    b2 = 3 * omt * t ** 2
    b3 = t ** 3

    return (b0[..., None] * P0 + b1[..., None] * P1 +
            b2[..., None] * P2 + b3[..., None] * P3)


def get_bezier_first_derivative(P0, P1, P2, P3, t):
    """
    计算贝塞尔曲线的一阶导数 B'(t)
    """
    # B'(t) = 3(1-t)^2 (P1-P0) + 6(1-t)t (P2-P1) + 3t^2 (P3-P2)
    t = np.asarray(t)
    omt = 1.0 - t
    return (3 * omt ** 2)[..., None] * (P1 - P0) + (6 * omt * t)[..., None] * (P2 - P1) + (3 * t ** 2)[..., None] * (
                P3 - P2)


def get_bezier_second_derivative(P0, P1, P2, P3, t):
    """
    计算贝塞尔曲线的二阶导数 B''(t)
    """
    # B''(t) = 6(1-t) (P2 - 2P1 + P0) + 6t (P3 - 2P2 + P1)
    t = np.asarray(t)
    omt = 1.0 - t
    A = P2 - 2 * P1 + P0
    B = P3 - 2 * P2 + P1
    return (6 * omt)[..., None] * A + (6 * t)[..., None] * B

# --------  单个的点曲率计算  -----------
def get_curvature(t, P0, P1, P2, P3):
    """
    计算在 t 点的瞬时曲率 k(t)
    2D曲率公式: k(t) = |x'(t)y''(t) - y'(t)x''(t)| / ((x'(t)^2 + y'(t)^2)^(3/2))
    """

    # 确保t在[0, 1]范围内
    t = np.asarray(t)
    Bp = get_bezier_first_derivative(P0, P1, P2, P3, t)  # (...,2)
    Bpp = get_bezier_second_derivative(P0, P1, P2, P3, t)  # (...,2)
    num = np.abs(Bp[..., 0] * Bpp[..., 1] - Bp[..., 1] * Bpp[..., 0])
    den2 = Bp[..., 0] ** 2 + Bp[..., 1] ** 2
    eps = 1e-12
    safe = den2 > eps
    out = np.empty_like(den2)
    out[~safe] = 1e12
    out[safe] = num[safe] / (den2[safe] ** 1.5 + 1e-18)
    return out


def get_numpy_list(list):
    """
    将元组列表转换为 NumPy 数组列表
    """
    return [np.array(p, dtype=float) for p in list]


def get_angle_cos_sin(p0, p3):
    """
    计算两个二维向量的角度余弦值和正弦值
    """
    p0 = np.asarray(p0)
    p3 = np.asarray(p3)
    v = p3 - p0
    Len = np.linalg.norm(v)
    cos_theta = v[0] / Len
    sin_theta = v[1] / Len

    return cos_theta, sin_theta


def get_inflection_points_t(control_points):
    """
        计算三阶贝塞尔曲线拐点 t 值。

        逻辑:
        1. 优先计算物理拐点 (曲率=0)。
        2. 若无拐点，则计算 P1 和 P2 在曲线上的投影参数 t1, t2，返回 (t1+t2)/2。

        返回:[bool,float]
        - bool: 是否正确算出拐点
        - float: 保留5位小数的 t 值
        """
    try:
        points = np.array(control_points)
        if len(points) != 4:
            # 极端错误情况返回 0.5
            return 0.5

        P0, P1, P2, P3 = points

        # --- 第一部分：尝试计算拐点 ---

        # 向量计算
        A_vec = P1 - P0
        B_vec = P2 - P1
        C_vec = P3 - P2

        def _cross_2d(v1, v2):
            """
                计算两个二维向量的叉乘 (返回一个标量)
                v1 = (x1, y1), v2 = (x2, y2)
                叉乘 = x1*y2 - y1*x2
            """
            return v1[0] * v2[1] - v1[1] * v2[0]
        # 叉乘 (2D向量叉乘得标量)
        cross_ab = _cross_2d(A_vec, B_vec)
        cross_bc = _cross_2d(B_vec, C_vec)
        cross_ac = _cross_2d(A_vec, C_vec)

        # 二次方程系数 A*t^2 + B*t + C = 0
        A = cross_ab + cross_bc - cross_ac
        B = cross_ac - 2 * cross_ab
        C = cross_ab

        inflection_ts = []
        epsilon = 1e-9  # 用于判断是否为0的极小值

        # 求解方程
        if abs(A) < epsilon:
            # 线性方程 B*t + C = 0
            if abs(B) > epsilon:
                t = -C / B
                if 0 < t < 1: inflection_ts.append(t)
        else:
            # 二次方程求解
            # delta = B ** 2 - 4 * A * C
            delta = np.float64(B) ** 2 - 4 * np.float64(A) * np.float64(C)

            if delta >= 0:
                sqrt_delta = np.sqrt(delta)
                t1 = (-B + sqrt_delta) / (2 * A)
                t2 = (-B - sqrt_delta) / (2 * A)

                # 过滤 (0, 1) 区间内的解
                if epsilon < t1 < 1 - epsilon: inflection_ts.append(t1)
                if epsilon < t2 < 1 - epsilon: inflection_ts.append(t2)

        # 存在拐点时正常返回
        if inflection_ts:
            # 如果有多个拐点，返回最小的那个，或者根据您的业务逻辑调整
            return [True,round(min(inflection_ts), 5)]

        # 兜底逻辑 (计算 P1, P2 投影的平均 t) ---

        # 1. 生成采样点 t (为了实时性，采样 100-200 个点通常足够精确)
        # 如果需要更高精度，可以增加到 1000，但在 numpy 下 200 极快
        num_samples = 500
        t_samples = np.linspace(0, 1, num_samples)

        # 2. 批量计算贝塞尔曲线上的点 B(t)
        # B(t) = (1-t)^3 P0 + 3(1-t)^2 t P1 + 3(1-t) t^2 P2 + t^3 P3
        # 使用 numpy 广播机制一次性计算所有点
        mt = 1 - t_samples
        mt2 = mt * mt
        mt3 = mt2 * mt
        t2 = t_samples * t_samples
        t3 = t2 * t_samples

        # shape: (num_samples, 1) * (1, 2) -> (num_samples, 2)
        curve_points = (
                mt3[:, None] * P0 +
                3 * mt2[:, None] * t_samples[:, None] * P1 +
                3 * mt[:, None] * t2[:, None] * P2 +
                t3[:, None] * P3
        )

        # 3. 找到离 P1 最近的 t
        # 计算 curve_points 到 P1 的欧氏距离平方 (避免开方以加速)
        dist_sq_p1 = np.sum((curve_points - P1) ** 2, axis=1)
        idx_p1 = np.argmin(dist_sq_p1)
        t_closest_p1 = t_samples[idx_p1]

        # 4. 找到离 P2 最近的 t
        dist_sq_p2 = np.sum((curve_points - P2) ** 2, axis=1)
        idx_p2 = np.argmin(dist_sq_p2)
        t_closest_p2 = t_samples[idx_p2]

        # 5. 计算平均值
        avg_t = (t_closest_p1 + t_closest_p2) / 2

        return [False,round(avg_t, 5)]

    except Exception as e:
        # 发生不可预知的计算错误时
        print(f"get_inflection_points_t：Calculation Error: {e}")
        return [False,0.50000]


def handle_semantic_change(control_points, ai_input):
    """
    处理语义变化，返回处理后的控制点
    """
    shapes = {
        "to_left_turn": [-1, -1],
        "to_right_turn": [1, 1],
        "to_left_then_right": [-1, 1],
        "to_right_then_left": [1, -1],
    }

    semantic_change = ai_input["target_semantic"]
    if semantic_change not in shapes:
        return control_points
    ana = analyze_bezier_shape(control_points)
    tem = max(abs(ana["d_start"]), abs(ana["d_end"]))
    ana["d_start"] = tem * shapes[semantic_change][0]
    ana["d_end"] = tem * shapes[semantic_change][1]

    final_points = get_points_by_control_direction(control_points, ana)
    return final_points


def get_global_offset_point_from_p0(control_points, origin_point, offset_x, offset_y):
    """
        根据偏移量获取偏移后的点
    """
    point = np.array(control_points[0])
    cos0, sin0 = get_angle_cos_sin(control_points[0], control_points[3])
    p0p3 = np.linalg.norm(np.asarray(control_points[0]) - np.asarray(control_points[3]))
    x_, y_ = p0p3 * (offset_x * cos0 - offset_y * sin0), p0p3 * (offset_x * sin0 + offset_y * cos0)
    return point + np.array([x_, y_])


def get_max_curvature_point_x_y_locally(control_points, max_curvature_point):
    """
        获取最大曲率点的局部坐标分量
    """
    cp = [np.asarray(p, dtype=float) for p in control_points]
    p0 = cp[0]
    p3 = cp[3]
    p_target = np.asarray(max_curvature_point)

    # 定义目标向量 v (从新原点 p0 指向目标点)
    v = p_target - p0

    # 定义 x 轴向量
    x_axis_vec = p3 - p0

    # 计算 L^2 (长度的平方)
    L_squared = np.dot(x_axis_vec, x_axis_vec)

    # 检查 L^2 是否接近 0
    if L_squared < 1e-18:  # 使用一个更小的值，因为它是平方
        return 0.0, 0.0

    # 定义y 轴向量
    y_axis_vec = np.array([-x_axis_vec[1], x_axis_vec[0]])

    # 计算 v 在 x_axis_vec 上的投影
    x_dot_prod = np.dot(v, x_axis_vec)

    # 计算 v 在 y_axis_vec 上的投影
    y_dot_prod = np.dot(v, y_axis_vec)

    # 按推导的公式，除以 L^2 进行归一化
    x_normalized = x_dot_prod / L_squared
    y_normalized = y_dot_prod / L_squared

    return x_normalized, y_normalized


def get_points_by_control_direction(control_points, analysis):
    """
        根据四个向量获得p1，p2控制点的坐标
    """
    P0, P1, P2, P3 = get_numpy_list(control_points)
    p0p3 = np.linalg.norm(P0 - P3)
    cos0, sin0 = get_angle_cos_sin(control_points[0], control_points[3])
    d_start = analysis["d_start"]
    p_start = analysis["p_start"]
    d_end = analysis["d_end"]
    p_end = analysis["p_end"]
    p1 = P0[0] + p0p3 * (p_start * cos0 - d_start * sin0), P0[1] + p0p3 * (p_start * sin0 + d_start * cos0)
    p2 = P3[0] - p0p3 * (p_end * cos0 + d_end * sin0), P3[1] + p0p3 * (d_end * cos0 - p_end * sin0)

    return np.array([P0, p1, p2, P3])


def need_division(ai_input):
    if ((ai_input["target_shape"] == "S-shape") or
        (ai_input["start_handle"]["movement_direction"] != ai_input["end_handle"]["movement_direction"])
    ):
        return True
    return False


def get_modify_degree(level):
    final_degree = 0.3
    if level == "slight":
        final_degree = random.uniform(0.15, 0.25)
    elif level == "default":
        final_degree = random.uniform(0.25, 0.40)
    elif level == "significant":
        final_degree = random.uniform(0.4, 0.7)
    return final_degree


def get_predict_point(control_points, ai_input, search_range, control_points_num):
    """
        获取预期的位置
    """
    # analyzes = analyze_bezier_shape(control_points)
    is_need_division = need_division(ai_input)
    # final_degree=get_modify_degree(ai_input)

    # 根据search range获取现在的位置
    result_segment, max_curvature_point, avg_curvature_value = find_max_avg_curvature_segment(control_points,search_range)

    handle_json=ai_input["start_handle"] if control_points_num==1 else ai_input["end_handle"]
    # target_point = ai_input["target_point"]
    movement_direction = handle_json["movement_direction"]
    movement_level=handle_json["movement_level"]
    target_shape = ai_input["target_shape"]
    curvature_change = handle_json["curvature_change"]
    curvature_change_level=handle_json["change_level"]
    max_point_x, max_point_y = get_max_curvature_point_x_y_locally(control_points, max_curvature_point)
    # 提取修改方向
    flag = {
        # movement_direction
        "left": -1,
        "right": 1,
        "none":0,
        # target_point
        "start": 1,
        "end": 2,
        # control_points_num
        1:1,
        2:-1

    }
    base=1
    # 这横向移动怎么只剩一行了
    max_point_x += base * flag[movement_direction] * get_modify_degree(movement_level)

    base_flag=1 if max_point_y>=0 else -1
    if curvature_change == "increase":
        max_point_y += base_flag*get_modify_degree(curvature_change_level) * 0.5
        # 处理一部分极端例子
        if movement_direction=="none":
            max_point_x += flag[control_points_num] * get_modify_degree(curvature_change_level)

    elif curvature_change == "decrease":
        max_point_y -= base_flag*get_modify_degree(curvature_change_level) * 0.5


    return get_global_offset_point_from_p0(control_points, max_curvature_point, max_point_x, max_point_y)


def get_predict_avg_curvature(control_points, ai_input, search_range, control_points_num):
    """
        获取预期的平均曲率
    """
    result_segment, max_curvature_point, avg_curvature_value = find_max_avg_curvature_segment(control_points,
                                                                                              search_range)

    # movement_direction=ai_input["movement_direction"]
    # target_shape=ai_input["target_shape"]
    handle_json=ai_input["start_handle"] if control_points_num==1 else ai_input["end_handle"]
    curvature_change = handle_json["curvature_change"]
    curvature_change_level=handle_json["change_level"]
    # is_need_division = need_division(ai_input)

    flag = {
        # curvature_change
        "increase": 1,
        "decrease": -1,
        # target_point
        "start": 1,
        "end": 2
    }
    if curvature_change in flag :
        avg_curvature_value *= (1 + flag[curvature_change] * get_modify_degree(curvature_change_level))

    return avg_curvature_value


# =======================================================================================
# =======================================================================================
# ====================                梯度下降函数              ===========================
# =======================================================================================
# =======================================================================================

def to_set_optimize_parameters(control_points, ai_input, target_p1, target_p2):
    # result_segment, max_curvature_point, avg_k_value = find_max_avg_curvature_segment(control_points, [0, 1])

    loss_params = {
        "original_points": control_points,
        "target_p1": target_p1,
        "target_p2": target_p2,
        # "fix_sample_points": fix_sample_points,
        "weights": {},
        "ai_input": ai_input,
        "is_need_division": need_division(ai_input) and get_inflection_points_t(control_points)[0],
    }
    balanced_weights = auto_balance_weights(control_points, ai_input, target_p1, target_p2, loss_params)


    # 将计算好的权重塞回 params
    loss_params['weights'] = balanced_weights
    # 打印最终使用的权重供调试
    # print("Final optimization weights:", balanced_weights)
    points_after_optimize = run_optimizer(control_points, master_loss_function, loss_params)
    return points_after_optimize


def auto_balance_weights(control_points, ai_input, target_p1, target_p2, loss_params):
    """
    计算每个Loss分量对控制点的梯度范数，并反推权重，使它们符合预期的梯度贡献比例。
    预期比例 (Target Gradients Ratios):
    - 距离(dis_point): 2.0
    - 曲率(dis_curvature): 2.0
    - 约束(fitting/move): 1.0 (如果存在)
    """

    print("\n--- 开始权重自动平衡 (Gradient Normalization) ---")

    # 1. 定义我们想要平衡的 Key 和它们的目标梯度贡献值 (Ratio)
    #    2:2:1 对应这里的 2.0, 2.0, 1.0
    #    注意：fitting_loss 对应你的 Loss C (移动距离/拟合)
    target_ratios = {
        'dis_point': 2.0,
        'dis_curvature': 2.0,
        'semantic': 1000.0
    }

    # 初始化计算出来的权重
    calculated_weights = {}

    # 临时参数，用于试运行
    temp_params = loss_params.copy()
    temp_params['weights'] = {k: 0.0 for k in target_ratios}  # 先全清零

    # 2. 逐个分量计算梯度能量
    points = np.array(control_points, dtype=float)

    for key in target_ratios:
        # a. 开启当前 Loss，关闭其他
        current_weights = {k: 0.0 for k in target_ratios}
        current_weights[key] = 1.0  # 设为 1.0，计算原始梯度
        temp_params['weights'] = current_weights

        # 计算梯度 (利用现有的 get_gradient_numeric)
        #    需要综合 P1 和 P2 的梯度
        grad = get_gradient_numeric(points, master_loss_function, temp_params, delta=1e-4)

        # 计算梯度的平均绝对值 (L1 Norm 的平均)
        # grad 是 (2, 2) 数组，对应 P1, P2 的 x,y 梯度
        grad_energy = np.mean(np.abs(grad))

        # d. 计算需要的权重
        #    Target = Weight * Energy  =>  Weight = Target / Energy
        if grad_energy < 1e-9:
            # 如果梯度几乎为0（例如该Loss当前没有贡献，比如semantic未触发），给一个默认小权重
            if key == 'semantic':
                calculated_weights[key] = 1000.0  # 默认值
            else:
                calculated_weights[key] = 1.0
            print(f"  [{key}] 梯度能量极小 ({grad_energy:.2e})，使用默认权重。")
        else:
            scale = target_ratios[key] / grad_energy
            # scale =10000 if scale>10000 else scale
            calculated_weights[key] = scale
            print(f"  [{key}] 梯度能量: {grad_energy:.2e} -> 修正权重: {scale:.2f}")

    calculated_weights['semantic'] = 1000.0

    print("--- 权重自动平衡完成 ---\n")
    return calculated_weights




def run_optimizer(control_points, loss_function, loss_params):
    """
    使用 Scipy 的 L-BFGS-B 算法替代手写循环
    """
    # 1. 提取需要优化的变量：P1 和 P2 的 (x, y)
    # 扁平化为 1D 数组 [P1x, P1y, P2x, P2y]
    initial_guess = np.concatenate([control_points[1], control_points[2]])

    # 2. 定义适配 Scipy 的 Loss Wrapper
    def objective_func(variables):
        # 还原回 (4, 2) 的形状
        # P0, P3 保持不变，P1, P2 更新
        current_p1 = variables[0:2]
        current_p2 = variables[2:4]

        temp_points = np.array(control_points, dtype=float)
        temp_points[1] = current_p1
        temp_points[2] = current_p2

        # 计算 Loss (由于 master_loss_function 需要 control_points_num，我们需要结合两者)
        # 这里假设你要同时优化 P1 和 P2，通常建议把 P1/P2 的 Loss 加在一起作为总 Loss
        loss_p1 = loss_function(temp_points, loss_params, 1)
        loss_p2 = loss_function(temp_points, loss_params, 2)
        return loss_p1 + loss_p2

    # 3. 运行优化
    # 'L-BFGS-B' 适合有边界约束的问题，且收敛速度极快
    def print_process(xk):
        # xk 是当前的参数值向量
        val = objective_func(xk)  # 计算当前目标函数值（可选）
        print(f"当前控制点: {xk}, 当前loss: {val}")
    result = minimize(
        objective_func,
        initial_guess,
        method='L-BFGS-B',
        tol=1e-5,
        callback=print_process,
        options={'maxiter': 2000,
                 'disp': False,
                 'iprint': 101}  # disp=True 会打印收敛过程
    )

    # 4. 将结果填回 control_points
    optimized_vars = result.x
    final_points = np.array(control_points, dtype=float)
    final_points[1] = optimized_vars[0:2]
    final_points[2] = optimized_vars[2:4]
    print(f"Scipy 优化结束: Success={result.success}, Iterations={result.nit}, Final Loss={result.fun:.6e}")
    if not result.success:
        print("备用")
        final_points = run_optimizer_baodi(control_points, master_loss_function, loss_params)


    return final_points

def run_optimizer_baodi(control_points, loss_function, loss_params,
                  iterations=3000, learning_rate=0.05,
                  beta1=0.9, beta2=0.999, eps=1e-8,
                  early_stop=True, eval_every=10, patience=80, min_delta=1e-6,
                  verbose=False):
    """
        使用 Adam 优化器替代原始的裸梯度下降，并加入早停机制。
        参数:
        control_points: 原始控制点数组
        loss_function: master_loss_function
        loss_params: 传递给 loss_function 的参数字典
        iterations: 最多迭代次数
        learning_rate: 初始学习率
        beta1, beta2, eps: Adam 超参数
        early_stop: 是否启用早停
        eval_every: 每多少步计算一次监控loss
        patience: 早停耐心值（连续多少次没有改进则停止）
        min_delta: 认为"改进"的最小loss下降量
        verbose: 如果 True，会打印一些诊断信息
        返回值: 优化后的控制点列表
    """

    points = np.array(control_points, dtype=float)

    # Adam state (仅针对P1,P2这两个点)
    m = np.zeros_like(points[1:3], dtype=float)
    v = np.zeros_like(points[1:3], dtype=float)

    best_points = points.copy()
    best_loss = None
    wait = 0

    def _compute_combined_loss(points, loss_function, loss_params):
        """
        计算当前 P1 和 P2 上的组合损失（用于判定是否需要提前终止）。
        注意：你的 master_loss_function 接收 control_points, loss_params, control_points_num
        这里我们把两个点对应的损失都算一下并求和，作为监控指标。
        """
        # 计算针对P1（control_points_num=1）的损失
        loss_p1 = loss_function(points, loss_params, 1)
        # 计算针对P2（control_points_num=2）的损失
        loss_p2 = loss_function(points, loss_params, 2)
        return float(loss_p1 + loss_p2)
    # 初始监控loss
    if early_stop:
        try:
            current_loss = _compute_combined_loss(points, loss_function, loss_params)
        except Exception:
            current_loss = float('inf')
        best_loss = current_loss
        if verbose:
            print(f"初始化 loss={best_loss:.6e}")
    delta=1e-3
    for i in range(1, iterations + 1):
    # 计算数值梯度（针对 P1,P2）
        grad = get_gradient_numeric(points, loss_function, loss_params,delta)

        # Adam 更新
        m = beta1 * m + (1 - beta1) * grad
        v = beta2 * v + (1 - beta2) * (grad ** 2)
        m_hat = m / (1 - beta1 ** i)
        v_hat = v / (1 - beta2 ** i)

        update = learning_rate * m_hat / (np.sqrt(v_hat) + eps)
        points[1:3] -= update

        # 每隔 eval_every 次评估一次 loss，用于早停与记录最佳点
        if early_stop and (i % eval_every == 0):
            # print(f"iter={i:5d},评估")
            try:
                current_loss = _compute_combined_loss(points, loss_function, loss_params)
            except Exception:
                current_loss = float('inf')

            if verbose:
                print(f"iter={i:5d}, loss={current_loss:.6e}, lr={learning_rate:.3e}")

            # 如果有改进
            if best_loss - current_loss > min_delta:
                best_loss = current_loss
                best_points = points.copy()
                # print(f"找到新的最佳点，loss={current_loss:.6e}")
                wait = 0

                if delta>1e-3:
                    delta /=2
                    learning_rate=0.05
            else:
                wait += 10
                # 当耐心值耗尽时提前停止
                if wait >= patience:

                    if verbose:
                        print(f"早停触发 (no improvement for {patience} evaluations). Best loss={best_loss:.6e}")
                        delta *=10
                        learning_rate *= 1
                        print(f"尝试扩大步长delta={delta}")
                        wait = 0
                    points = best_points
                    if delta>101:
                        print("无效结果，结束")
                        break
        # print(f"梯度：{np.linalg.norm(grad)}")
    # 可选：简单的学习率衰减（当没有启用early_stop时也能避免震荡）
    # 这里使用指数衰减或按步衰减都可以；为了保留原始行为我们不强制衰减。
    current_loss = _compute_combined_loss(points, loss_function, loss_params)
    print(f"最终 loss={current_loss:.6e}")
    return [np.asarray(p) for p in points]

def get_gradient_numeric(points, loss_function, loss_params, delta=1e-3):
    grad = np.zeros_like(points[1:3], dtype=float)
    for i in range(2):
        for j in range(2):
            points_plus = np.copy(points)
            points_minus = np.copy(points)
            points_plus[i + 1, j] += delta
            points_minus[i + 1, j] -= delta

            loss_plus = loss_function(points_plus, loss_params, i + 1)
            loss_minus = loss_function(points_minus, loss_params, i + 1)
            grad[i, j] = (loss_plus - loss_minus) / (2 * delta)
    return grad



# ====================  损失计算函数 ===========================
def master_loss_function(new_control_points, loss_params, control_points_num):
    """
    模块化主损失函数
    根据 loss_params 的指令来组合不同的损失项
    """
    ai_input = loss_params["ai_input"]
    # fixed_elements=ai_input["fixed_elements"]
    metrics = analyze_bezier_shape(new_control_points)
    if not metrics: return 0.0

    # t = loss_params.get("fixed_t", None)

    # 分割处理参数t
    # backup
    t = None
    if need_division(loss_params["ai_input"]):
        # 虽然要求是要分割但是不一定能分
        t = get_inflection_points_t(new_control_points)[1]
    # 目标搜索位置
    search_range = [0, 1]
    if t is not None:
        search_range = [0, t] if control_points_num == 1 else [t, 1]

    control_points_num_index = {
        1: "target_p1",
        2: "target_p2"
    }
    # 获取到对应当前控制点的优化方向
    current_target = loss_params[control_points_num_index[control_points_num]]

    # --- 计算主要目标损失 ---

    total_loss = calculate_target_loss(
        new_control_points, current_target, loss_params, search_range,control_points_num
    )

    # 语义约束惩罚

    semantic_change = ai_input.get('semantic_change', "False")
    if semantic_change == "False":

        total_loss+= calculate_semantic_constraint_loss(new_control_points, loss_params,control_points_num_index)


    return total_loss


def calculate_target_loss(control_points, targets, loss_params, search_range,control_points_num):
    """
        计算目标点距离惩罚和目标平均差距惩罚
    """

    loss = 0.0
    weights=loss_params["weights"]
    ai_input = loss_params["ai_input"]
    handles={
        1:"start_handle",
        2:"end_handle"
    }
    balance_weights={
        1:0.3 if ai_input[handles[control_points_num]]["movement_direction"]=="none" else 1,
        2:0.3 if ai_input[handles[control_points_num]]["curvature_change"]=="none" else 1
    }
    # 计算当前点的最大点和平均曲率然后计算差距塞入loss
    new_result_segment, new_max_curvature_point, new_avg_k_value = find_max_avg_curvature_segment(control_points,
                                                                                                  search_range)
    target_avg_curvature_value = targets["target_avg_curvature_value"]
    target_center_point = targets["target_center_point"]
    dis_point_loss = np.sum((target_center_point - new_max_curvature_point) ** 2) * weights['dis_point'] *balance_weights[1]
    # print(f"dis_point_loss: {dis_point_loss}")
    loss += dis_point_loss
    # 使用平方使梯度数值更稳定，有利于优化
    curvature_loss= np.abs(new_avg_k_value - target_avg_curvature_value) ** 2 * weights['dis_curvature']*balance_weights[2]
    loss += curvature_loss
    # print(f"dis_cur_loss: {loss}")

    return loss


def calculate_semantic_constraint_loss(points, loss_params,control_points_num_index):
    """
        语义约束: 基于您指定的几何规则, 防止控制点越界。
    """
    """
        语义约束: 全局拓扑一致性保持 (Global Topology Preservation)。
        无论微调哪个点，都必须同时维护所有三角剖分的相对方向（手性）。
    """
    weights = loss_params['weights']
    original_points = loss_params['original_points']
    EPS = 1e-6
    # 当前点与原始点
    P0, P1, P2, P3 = points[0], points[1], points[2], points[3]
    OP0, OP1, OP2, OP3 = original_points[0], original_points[1], original_points[2], original_points[3]

    def cross_2d(v1, v2):
        return v1[0] * v2[1] - v1[1] * v2[0]

    def get_topology_penalty(curr_triplet, orig_triplet):
        curr_S, curr_E, curr_P = curr_triplet
        orig_S, orig_E, orig_P = orig_triplet

        # 1. 原始符号
        ref_vec_line = orig_E - orig_S
        ref_vec_point = orig_P - orig_S
        ref_cross = cross_2d(ref_vec_line, ref_vec_point)
        ref_sign = np.sign(ref_cross)

        # 2. 当前值
        curr_vec_line = curr_E - curr_S
        curr_vec_point = curr_P - curr_S
        curr_cross = cross_2d(curr_vec_line, curr_vec_point)

        # --- 改进逻辑 ---

        # 如果手性已经反转 (硬错误)，给予巨大线性/平方惩罚
        if curr_cross * ref_sign <= 0:
            return abs(curr_cross) ** 2 * 1000.0

        # 如果手性正确，但非常接近 0 (即将共线)，给予对数障碍惩罚
        # Barrier Function: -log(distance)
        # 当 curr_cross 接近 0 时，-log 趋向正无穷
        safe_margin = 100.0  # 根据坐标系大小调整，例如 100.0

        # 为了数值稳定，我们通常限制 log 的范围，或者使用 1/x
        # 这里使用 1/x 形式更平滑: penalty = 1 / (cross_val + epsilon)
        if abs(curr_cross) < safe_margin:
            # 越接近 0，惩罚越大
            return (safe_margin - abs(curr_cross)) ** 5 * 1.0
        return 0.0


    total_penalty = 0.0

    # Constraint 1: 检查 P1 是否保持在 P0->P2 弦的正确一侧
    # 原始: (OP0, OP2, OP1), 当前: (P0, P2, P1)
    total_penalty += get_topology_penalty((P0, P2, P1), (OP0, OP2, OP1))

    # Constraint 2: 检查 P2 是否保持在 P3->P1 弦的正确一侧
    # 原始: (OP3, OP1, OP2), 当前: (P3, P1, P2)
    # 注意: 这里 P1 是动态的! 移动 P1 会改变这个约束的状态，这正是我们想要的。
    total_penalty += get_topology_penalty((P3, P1, P2), (OP3, OP1, OP2))

    # 计算总加权损失
    semantic_loss = weights['semantic'] * (total_penalty ** 2)

    return semantic_loss



# =======================================================================================
# =======================================================================================
# ====================                主要分析函数              ===========================
# =======================================================================================
# =======================================================================================


def analyze_bezier_shape(control_points):
    """分析函数，返回所有需要的参数"""
    L0, L1, L2, L3 = [np.array(p, dtype=float) for p in control_points]
    V = L3 - L0
    # L_sq为基线长度
    L_sq = np.dot(V, V)
    if L_sq < 1e-12: return None

    d_start = np.linalg.det(np.array([V, L1 - L0])) / L_sq
    d_end = np.linalg.det(np.array([V, L2 - L3])) / L_sq
    p_start = np.dot(L1 - L0, V) / L_sq
    p_end = np.dot(L3 - L2, V) / L_sq

    # 返回L_sq以供下游函数进行损失归一化
    return {"d_start": d_start, "d_end": d_end, "p_start": p_start, "p_end": p_end, "L_sq": L_sq}


def get_bezier_semantics(control_points):
    analyze = analyze_bezier_shape(control_points)
    has_intersection = get_inflection_points_t(control_points)[0]
    d_s = analyze["d_start"] if analyze else None
    d_e = analyze["d_end"] if analyze else None

    if d_s==0 and d_e==0:
        return "Straight line"
    # shape_of_line = "Straight line"
    road_shape = "Straight line"
    # 存在拐点
    if has_intersection:

        if d_s * d_e >= 0:
            # C型下的
            # shape_of_line = "C-shape"
            if d_s>d_e:
                road_shape = "to_right_then_left"
            else:
                road_shape = "to_left_then_right"
        #             S型
        elif d_s * d_e < 0:
            # shape_of_line = "S-shape"
            road_shape = "to_right_then_left" if d_s > 0 else "to_left_then_right"
    else:
        # shape_of_line = "C-shape"
        if d_s > 0:
            road_shape = "to_right_turn"
        elif d_s < 0:
            road_shape = "to_left_turn"
    return road_shape


def get_bezier_c_s_shape(control_points):
    analyze = analyze_bezier_shape(control_points)
    d_s = analyze["d_start"] if analyze else None
    d_e = analyze["d_end"] if analyze else None
    if d_s is  None and d_e is  None:
        return "None"
    if d_s*d_e>=0:
        return "C-shape"
    else:
        return "S-shape"


def process_before_optimization(ai_input, control_points):
    # 先处理变形
    if ai_input["semantic_change"] != "False":
        semantic_points = handle_semantic_change(control_points, ai_input)
        # return semantic_points
        control_points=semantic_points

    # 先判断是否需要分割处理
    is_need_division = need_division(ai_input)
    if is_need_division:
        #     将曲线以拐点位置分为两部分（没有拐点就默认0.5），然后找目标位置，
        division_t = get_inflection_points_t(control_points)[1]
        target_p1 = {
            "target_avg_curvature_value": get_predict_avg_curvature(control_points, ai_input, [0, division_t], 1),
            "target_center_point": get_predict_point(control_points, ai_input, [0, division_t], 1)}
        target_p2 = {
            "target_avg_curvature_value": get_predict_avg_curvature(control_points, ai_input, [division_t, 1], 2),
            "target_center_point": get_predict_point(control_points, ai_input, [division_t, 1], 2)}
    else:
        # 直接获取预测曲率和预测位置
        target_p1 = {
            "target_avg_curvature_value": get_predict_avg_curvature(control_points, ai_input, [0.0, 1.0], 1),
            "target_center_point": get_predict_point(control_points, ai_input, [0.0, 1.0], 1)}
        target_p2 = target_p1
    #     接下来应该是梯度优化
    print("target_p1:", target_p1)
    print("target_p2:", target_p2)
    # template_points=get_template_points(control_points)
    final_points=to_set_optimize_parameters(control_points, ai_input, target_p1, target_p2)
        # final_points2=to_set_optimize_parameters(template_points, ai_input, target_p1, target_p2)
    # return final_points,final_points2
    return final_points


# =======================================================================================
# =======================================================================================
# ====================                主要计算函数              ===========================
# =======================================================================================
# =======================================================================================





# --- 目标位置寻找 ---

def find_max_avg_curvature_segment(control_points, search_range,samples=501,fixed_length=0.3):
    """
    计算基于曲率能量 (k^2) 的质心位置。
    返回:
        - result_segment: 原样返回 search_range
        - centroid_point: 质心坐标 (x, y)
        - avg_k_value: 平均曲率
    """

    # 1. 基础验证
    P = np.asarray(control_points, dtype=float)
    if P.shape != (4, 2):
        return None, None, None

    t0 = float(search_range[0])
    t1 = float(search_range[1])

    # 容错处理
    if not (0.0 <= t0 <= 1.0) or not (0.0 <= t1 <= 1.0):
        t0 = max(0.0, min(1.0, t0))
        t1 = max(0.0, min(1.0, t1))

    if t0 >= t1:
        dummy_pt = get_bezier_point(P, t0)
        return [t0, t1], dummy_pt, 0.0

    # 2. 采样 (仅需一次采样，利用 Numpy 广播机制极其迅速)
    ts = np.linspace(t0, t1, samples, dtype=float)

    # 3. 批量计算曲率和坐标
    # 这里的开销是 O(samples)，与原方法第一步相同
    ks = get_curvature(ts, *P)
    points = get_bezier_point(P, ts)

    # 4. 计算权重 (使用能量权重 k^2)
    # 使用平方能让质心更强烈地吸附在弯曲最剧烈的地方，同时保持梯度光滑
    # 加上 1e-9 避免直线段权重全为 0
    weights = ks ** 2 + 1e-9

    total_weight = np.sum(weights)

    # 5. 计算质心 (Centroid) - 向量化操作，微秒级完成
    # Centroid = Sum(P_i * w_i) / Sum(w_i)
    centroid_x = np.sum(points[:, 0] * weights) / total_weight
    centroid_y = np.sum(points[:, 1] * weights) / total_weight
    centroid_point = np.array([centroid_x, centroid_y])

    # 6. 计算加权曲率
    # 如果区间比 fixed_length 短，直接整段
    rng = t1 - t0
    if rng <= fixed_length + 1e-12:
        # 段内最大瞬时曲率点
        idx = int(np.argmax(ks))
        t_star = ts[idx]
        avg_k = float(np.trapz(ks, ts) / (rng + 1e-18))
        return [t0, t1], centroid_point, avg_k

    # 滑窗长度对应的采样点数
    win = max(2, int(round(fixed_length * (samples - 1) / rng)))
    # 用前缀和/卷积拿窗口和
    window_sum = np.convolve(ks, np.ones(win, dtype=float), mode='valid')
    # 窗口平均
    window_avg = window_sum / win
    s_idx = int(np.argmax(window_avg))  # 起点索引
    e_idx = s_idx + win - 1  # 终点索引
    t_start = ts[s_idx]
    t_end = ts[e_idx]
    avg_k_value = float(window_avg[s_idx])

    # weighted_avg_k = np.sum(ks * weights) / total_weight
    # weighted_avg_k = np.sum(ks) / samples

    # 这里的 refine 参数已经不再需要了，因为质心法不需要二次采样来细化
    return [t_start, t_end], centroid_point, avg_k_value


def plot_test_result(curve_list, title=""):
    """
    通用绘图函数, 用于可视化测试结果
    :param curve_list: 一个包含多个控制点列表的列表, 
                     顺序应为 [original, modified, line3(可选)]
    :param title: 图表标题
    """
    plt.figure(figsize=(10, 7))

    # 预定义名称顺序
    names = ['Original', 'Modified', 'Template']

    # 扩充 styles 字典，添加 line3 为蓝色(b)点状(.)虚线(--)
    styles = {
        'Original': 'k--o',  # 黑色，虚线，圆点
        'Modified': 'r--^',  # 红色，虚线，三角
        'Template': 'b--.'  # 蓝色，虚线，小点
    }

    # 将输入的列表合并为 curves 字典
    # 使用 zip 将名称与数据对应，如果输入数据多于预定义名称，需要处理（这里假设最多3个或根据输入动态处理）
    curves = {}
    for i, p_data in enumerate(curve_list):
        if i < len(names):
            curves[names[i]] = p_data
        else:
            # 如果输入超过3条线，给予默认命名
            curves[f'Line{i + 1}'] = p_data
            styles[f'Line{i + 1}'] = 'g--+'  # 给予一个默认样式

    for name, p in curves.items():
        t = np.linspace(0, 1, 100)
        p_np = np.array(p)

        # 假设 get_bezier_point 已经在外部定义
        # 如果没有定义，这里需要定义或者替换
        curve_pts = np.array([get_bezier_point(p_np, val) for val in t])

        # 绘图部分
        # styles[name][0] 获取颜色字符 (k, r, b)
        base_color = styles[name][0]

        plt.plot(curve_pts[:, 0], curve_pts[:, 1], base_color, linewidth=2.0, alpha=0.9, label=f"{name} Curve")

        # 绘制多边形点 (使用相同的颜色，空心标记)
        # 注意: styles[name]包含了线型和标记，这里单独画点时可能希望只保留颜色和标记
        # 原代码直接用 styles[name] 画多边形连线和点
        plt.plot(p_np[:, 0], p_np[:, 1], styles[name], markersize=8, markerfacecolor='none', label=f"{name} Polygon")

        for i, point in enumerate(p_np):
            x, y = point
            label = f"P{i}"
            plt.text(
                x,
                y,
                f"  {label}",
                color=base_color,
                fontsize=14,
                ha='left',
                va='bottom'
            )

    plt.title(title, fontsize=14)
    plt.legend()
    plt.grid(True)
    plt.axis('equal')
    plt.show()


def show_data_1(title,control_points,ai_input):
    t=get_inflection_points_t(control_points)[0]
    print(f"{title}")
    if t:
        print(f"Inflection Points: {t}")
        result_segment, max_curvature_point, avg_k_value=find_max_avg_curvature_segment(control_points, [0, t])
        print(f"取值段：{result_segment}\t最大点：{max_curvature_point}\t最大平均曲率值：{avg_k_value}")
        result_segment, max_curvature_point, avg_k_value=find_max_avg_curvature_segment(control_points, [t, 1])
        print(f"取值段：{result_segment}\t最大点：{max_curvature_point}\t最大平均曲率值：{avg_k_value}")
    else:
        result_segment, max_curvature_point, avg_k_value=find_max_avg_curvature_segment(control_points, [0, 1])
        print(f"取值段：{result_segment}\t最大点：{max_curvature_point}\t最大平均曲率值：{avg_k_value}")


def get_template_points(control_points):
    analysis = analyze_bezier_shape(control_points)
    P0, P1, P2, P3 = get_numpy_list(control_points)
    p0p3 = np.linalg.norm(P0 - P3)
    cos0, sin0 = get_angle_cos_sin(control_points[0], control_points[3])
    def get_flag(num):
        if num > 0:
            return 1
        elif num < 0:
            return -1
        return 0
    d_start = get_flag(analysis["d_start"])*0.5
    p_start = get_flag(analysis["p_start"])*0.25
    d_end = get_flag(analysis["d_end"])*0.5
    p_end = get_flag(analysis["p_end"])*0.25
    p1 = P0[0] + p0p3 * (p_start * cos0 - d_start * sin0), P0[1] + p0p3 * (p_start * sin0 + d_start * cos0)
    p2 = P3[0] - p0p3 * (p_end * cos0 + d_end * sin0), P3[1] + p0p3 * (d_end * cos0 - p_end * sin0)
    return [P0,p1,p2,P3]

# --- 5. 示例用法 (Example Usage) ---
def main():
    for case in modify_test_cases:
        print(case["title"])
        control_points = [np.asarray(i) for i in case["control_point"]]
        originalInfo = get_bezier_semantics(control_points)
        # agent = curveTestAgent()
        # for request in user_request:
        #     print(request)
        print(originalInfo)
        # agent_ans = agent.get_commands(request, originalInfo)["command"]
        agent_ans = case["command"]
        # formatted_output = json.dumps(agent_ans["command"], indent=4, ensure_ascii=False)
        print()
        print(case["modification"])
        print(agent_ans)
        show_data_1("初始", control_points,agent_ans)
        start_time = time.perf_counter()
        template_points=get_template_points(control_points)
        # 处理结果
        # final_points,final_points2 = process_before_optimization(agent_ans, control_points)
        final_points = process_before_optimization(agent_ans, control_points)
        # 计算时间
        end_time = time.perf_counter()
        elapsed_time = end_time - start_time
        print(f"总共耗时: {elapsed_time:.4f} 秒")
        # 作图
        # curve_list = [control_points, final_points,template_points,final_points2]
        curve_list = [control_points, final_points]
        plot_test_result(curve_list,title=case["title"][:8])
        show_data_1("处理后", final_points,agent_ans)

        print("--" * 50)
        # break

def test_main():
    """
        获取大模型参数化指令
    """
    test_data=m_curve_td
    # curve_agent=curveTestAgent(model="GPT")
    curve_agent=curveTestAgent(model="GPT")



    for i in test_data:
        start_time = time.perf_counter()
        print(i["title"])
        curve_inf = {}
        control_points = [np.asarray(i) for i in i["control_point"]]
        ana = analyze_bezier_shape(control_points)
        curve_inf.update(ana)
        shape_info = get_bezier_semantics(control_points)
        curve_inf["current_semantics"]=shape_info
        c_s=get_bezier_c_s_shape(control_points)
        curve_inf["current_shape"]=c_s
        agent_ans = curve_agent.get_commands_manager(i["modification"], curve_inf)
        agent_ans["current_shape"]=curve_inf["current_shape"]
        agent_ans["current_semantics"]=curve_inf["current_semantics"]
        print(f"agent_ans:{agent_ans}")
        # final_points = process_before_optimization(agent_ans, control_points)
        # show_data_1("初始", control_points,agent_ans)
        # curve_list = [control_points, final_points]
        # plot_test_result(curve_list, title=i["title"])
        end_time = time.perf_counter()
        elapsed_time = end_time - start_time
        print(f"总共耗时: {elapsed_time:.4f} 秒")
        print("--" * 50)

def test2_main():
    test_data=m_curve_td[0:5]
    test_data=[m_curve_td[1],m_curve_td[11],m_curve_td[12],m_curve_td[14]]
    test_data=m_curve_td
    # test_data=m_curve_td
    # curve_agent=curveTestAgent(model="GPT")
    # curve_agent=curveTestAgent(model="")

    for index in range(len(test_data)):
        i=test_data[index]
        start_time = time.perf_counter()
        title=f"Test {index+1}"
        print(title)
        print(i["modification"])
        print(i['control_point'])
        curve_inf ={}
        control_points = [np.asarray(i) for i in i["control_point"]]
        ana = analyze_bezier_shape(control_points)
        curve_inf.update(ana)
        shape_info = get_bezier_semantics(control_points)
        curve_inf["current_semantics"]=shape_info
        c_s=get_bezier_c_s_shape(control_points)
        curve_inf["current_shape"]=c_s

        agent_ans=i["command"]
        print(f"agent_ans:{agent_ans}")
        print(f"start_handle:{agent_ans['start_handle']}")
        print(f"end_handle:{agent_ans['end_handle']}")
        final_points = process_before_optimization(agent_ans, control_points)
        show_data_1("初始", control_points,agent_ans)
        curve_list = [control_points, final_points]
        plot_test_result(curve_list, title=title)
        end_time = time.perf_counter()
        elapsed_time = end_time - start_time
        show_data_1("处理后", final_points, agent_ans)
        print(f"控制点：{[arr.tolist() for arr in final_points]}")
        print(f"总共耗时: {elapsed_time:.4f} 秒")
        print("--" * 50)

if __name__ == "__main__":




    # user_request = [
    #     "将曲线的中段位置曲率变化率提高，曲率顶点的锐度提高",
    #     "将曲线的曲率顶点左移",
    #     "将曲线的曲率顶点右移",
    #     "将曲线修改为S型",
    #     "将曲线修改为开口朝上的C型",
    #     "将曲线修改为开口朝下的C型",
    #     "将曲线最弯曲的部位左移",
    #     "将曲线最弯曲的部位向开始端移动",
    #     "增大曲率顶点的锐度", ]
    # main()
    # test_main()
    test2_main()



