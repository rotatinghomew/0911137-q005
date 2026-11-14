"""仅依赖标准库的精确统计原语。

用于 Poisson 率的精确置信区间与预测分位数，避免把第三方数值库引入运行依赖。
所有函数对非有限输入直接抛错，保证输出可被 JSON 序列化。
"""

import math


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('统计输入必须为有限实数')
    return float(value)


def normal_quantile(probability):
    """Acklam 标准正态分位数近似，|误差| < 1.15e-9。"""
    p = finite(probability)
    if not 0.0 < p < 1.0:
        raise ValueError('概率必须位于开区间 (0,1)')
    lower = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
             1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    middle_low = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
                  6.680131188771972e+01, -1.328068155288572e+01]
    upper_low = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
                 -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    upper_den = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
                 3.754408661907416e+00]
    low, high = 0.02425, 1 - 0.02425
    if p < low:
        q = math.sqrt(-2 * math.log(p))
        numerator = (((((upper_low[0] * q + upper_low[1]) * q + upper_low[2]) * q + upper_low[3]) * q + upper_low[4]) * q + upper_low[5])
        denominator = (((upper_den[0] * q + upper_den[1]) * q + upper_den[2]) * q + upper_den[3]) * q + 1
        return numerator / denominator
    if p <= high:
        q = p - 0.5
        r = q * q
        numerator = (((((lower[0] * r + lower[1]) * r + lower[2]) * r + lower[3]) * r + lower[4]) * r + lower[5]) * q
        denominator = ((((middle_low[0] * r + middle_low[1]) * r + middle_low[2]) * r + middle_low[3]) * r + middle_low[4]) * r + 1
        return numerator / denominator
    q = math.sqrt(-2 * math.log(1 - p))
    numerator = (((((upper_low[0] * q + upper_low[1]) * q + upper_low[2]) * q + upper_low[3]) * q + upper_low[4]) * q + upper_low[5])
    denominator = (((upper_den[0] * q + upper_den[1]) * q + upper_den[2]) * q + upper_den[3]) * q + 1
    return -numerator / denominator


def regularized_gamma(shape, x):
    """正则化下不完全伽马 P(shape, x)，级数加连分式实现。"""
    a, x = finite(shape), finite(x)
    if a <= 0:
        raise ValueError('伽马函数形状参数必须为正')
    if x <= 0:
        return 0.0
    tolerance = 1e-12
    if x < a + 1:
        term, total, ap = 1 / a, 1 / a, a
        for _ in range(10000):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * tolerance:
                break
        return total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    b = x + 1 - a
    c, d, h = 1e300, 1 / b, 1 / b
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        if abs(d) < 1e-300:
            d = 1e-300
        c = b + an / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < tolerance:
            break
    return 1 - math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi_square_quantile(probability, df):
    """卡方分位数，对正则化伽马做二分求解。"""
    p = finite(probability)
    k = int(df)
    if k <= 0 or k != df:
        raise ValueError('自由度必须为正整数')
    if not 0.0 < p < 1.0:
        raise ValueError('概率必须位于开区间 (0,1)')
    lo, hi = 0.0, max(1.0, k * 2.0)
    while regularized_gamma(k / 2, hi / 2) < p:
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if regularized_gamma(k / 2, mid / 2) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    return (lo + hi) / 2


def poisson_rate_interval(count, exposure, confidence=0.95):
    """Poisson 事件率的精确双侧区间；零事件给出基于 3 法则的单侧上界。"""
    n = int(count)
    t = finite(exposure)
    if n < 0 or t <= 0:
        raise ValueError('事件数须非负且暴露须为正')
    if not 0.0 < confidence < 1.0:
        raise ValueError('置信水平必须位于 (0,1)')
    alpha = 1 - confidence
    if n == 0:
        upper = chi_square_quantile(1 - alpha / 2, 2) / 2
        return 0.0, upper / t
    lower = chi_square_quantile(alpha / 2, 2 * n) / 2
    upper = chi_square_quantile(1 - alpha / 2, 2 * n + 2) / 2
    return lower / t, upper / t


def poisson_cdf(k, mean):
    """Poisson 累积概率，使用对数空间递推避免均值较大时的下溢。"""
    n = int(k)
    lam = finite(mean)
    if lam <= 0:
        return 1.0
    if n < 0:
        return 0.0
    log_term, total = -lam, math.exp(-lam)
    for j in range(1, n + 1):
        log_term += math.log(lam / j)
        total += math.exp(log_term)
    return min(1.0, total)


def poisson_quantile(probability, mean):
    """最小的 k 使 P(X<=k) >= probability。"""
    p = finite(probability)
    lam = finite(mean)
    if not 0.0 < p < 1.0:
        raise ValueError('概率必须位于开区间 (0,1)')
    if lam <= 0:
        return 0
    k = 0
    while poisson_cdf(k, lam) < p:
        k += 1
    return k
