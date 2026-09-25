"""
参数化缓动曲线

Qt 内置的曲线够用但**不可调**：`OutBack` 的过冲是写死的 1.70158，`OutElastic`
的振幅/周期也写死。所以之前几档动画看着"一个味道" —— 无非是 OutCubic 和
OutQuint 来回换，都做不出"轻轻弹一下"和"夸张地弹"的区别。

这里按 Robert Penner 的标准公式实现一遍，把关键系数开放成参数：
想弹多少、弹几次，都是参数的事。

## 用 QEasingCurve.Custom

Qt 允许塞一个自己的函数：`QEasingCurve.setCustomType(fn)`，fn 收 0~1 返回 0~1。
（注意 Qt 期望的签名是 `float (*)(double)`，PyQt6 里直接传 Python 函数即可。）

## 和 PCL2 的关系

PCL2 CE 里有一整套自己的缓动（`PCL.Core/UI/Animation/Easings/`），其中
`BackEaseWithPower` / `ElasticEaseWithPower` 这种"带幂次"的版本 Qt 完全没有，
只有它自己实现。这个模块就是那部分的对应物。
"""
import math

from PyQt6.QtCore import QEasingCurve


def _clamp01(t: float) -> float:
    return 0.0 if t < 0 else (1.0 if t > 1 else float(t))


# ============================================================
# 幂次缓动（Penner）：pow 的指数就是"力度"
# ============================================================

def make_power_in(power: float = 3.0):
    def fn(t):
        return math.pow(t, power)
    return fn


def make_power_out(power: float = 3.0):
    def fn(t):
        return 1.0 - math.pow(1.0 - t, power)
    return fn


def make_power_in_out(power: float = 3.0):
    def fn(t):
        if t < 0.5:
            return math.pow(2 * t, power) / 2
        return 1.0 - math.pow(2 * (1 - t), power) / 2
    return fn


# ============================================================
# Back：先往反方向退一点再冲过去。s 越大过冲越狠
# ============================================================

# Penner 原版用的系数（约等于 1.70158）
BACK_S = 1.70158


def make_back_in(s: float = BACK_S):
    def fn(t):
        return t * t * ((s + 1) * t - s)
    return fn


def make_back_out(s: float = BACK_S):
    def fn(t):
        u = t - 1.0
        return u * u * ((s + 1) * u + s) + 1.0
    return fn


def make_back_in_out(s: float = BACK_S):
    s2 = s * 1.525

    def fn(t):
        if t < 0.5:
            u = 2 * t
            return (u * u * ((s2 + 1) * u - s2)) / 2
        u = 2 * t - 2
        return (u * u * ((s2 + 1) * u + s2) + 2) / 2
    return fn


def overshoot_for_s(s: float) -> float:
    """反推：想要"冲过头 x 倍距离"需要多大的 s

    过冲峰值出现在 t = (s+1+sqrt(...))/(...) 处，不好解；这里用数值方式反推
    （二分 40 次，精度足够）。用途是让"过冲 12px"这种直觉参数能直接填。
    """
    if s <= 0:
        return 0.0
    fn = make_back_out(s)
    peak = 0.0
    for i in range(1, 200):
        peak = max(peak, fn(i / 200.0))
    return peak - 1.0        # 超出终点的部分


def s_for_overshoot(ratio: float) -> float:
    """给定"想过冲多少比例"（0.1 = 冲过头 10%），反推 s"""
    if ratio <= 0:
        return 0.0
    lo, hi = 0.0, 12.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if overshoot_for_s(mid) < ratio:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ============================================================
# Elastic：弹簧。振幅和"来回几次"都能调
# ============================================================

def _elastic_phase(amplitude: float, period: float) -> float:
    """弹性曲线的相位偏移

    ⚠️ 别在闭包里写 `if period <= 0: period = 0.3` —— Python 会把 period
    当成局部变量，报 UnboundLocalError（踩过）。要改就先拷一份。
    另外 asin 的参数卡在 1.0 上会出现 -0.0 这种值，往里收一点点更稳。
    """
    amp = max(1.0, float(amplitude))
    per = float(period) if period and period > 0 else 0.3
    ratio = min(1.0, 1.0 / amp)
    return per, amp, per / (2 * math.pi) * math.asin(ratio)


def make_elastic_out(amplitude: float = 1.0, period: float = 0.3):
    per, amp, s = _elastic_phase(amplitude, period)

    def fn(t):
        if t <= 0:
            return 0.0
        if t >= 1:
            return 1.0
        return (amp * math.pow(2, -10 * t)
                * math.sin((t - s) * (2 * math.pi) / per) + 1.0)
    return fn


def make_elastic_in(amplitude: float = 1.0, period: float = 0.3):
    per, amp, s = _elastic_phase(amplitude, period)

    def fn(t):
        if t <= 0:
            return 0.0
        if t >= 1:
            return 1.0
        return -(amp * math.pow(2, 10 * (t - 1))
                 * math.sin((t - 1 - s) * (2 * math.pi) / per))
    return fn


# ============================================================
# 阻尼正弦（我自己那版弹簧用的就是这个形状，这里统一收进来）
# ============================================================

def make_damped_sine(overshoot_ratio: float = 0.12, cycles: float = 1.15,
                     decay: float = 5.0):
    """过冲后自己收敛回来的一条连续曲线

    和 Back 的区别：Back 是"退一步再冲过去"，这个是"直接冲过去再收回来"，
    更像弹簧。overshoot_ratio 是"冲过头的距离占位移的比例"。
    归一化过，所以 ratio=0.12 就是真冲过头 12%。
    """
    t_peak = (1.0 / (2 * math.pi * cycles)) * math.atan(
        2 * math.pi * cycles / decay)
    peak = math.exp(-decay * t_peak) * math.sin(2 * math.pi * cycles * t_peak)
    scale = overshoot_ratio / peak if peak > 1e-9 else 0.0

    def fn(t):
        # 前 1/8 用幂次加速起步（纯正弦起步太"木"），之后走阻尼正弦
        if t <= 0:
            return 0.0
        if t >= 1:
            return 1.0
        shaped = 1.0 - math.pow(1.0 - t, 2.2)
        return 1.0 - scale * math.exp(-decay * shaped) * math.sin(
            2 * math.pi * cycles * shaped)
    return fn


# ============================================================
# PCL 旧版（实际在用的那套）公式
# ============================================================
#
# ⚠️ 澄清一件事：PCL.Core\UI\Animation\Easings\ 里那套新缓动**没有被程序使用**
# （在 Plain Craft Launcher 2 全目录搜 AnimationService / PCL.Core.UI.Animation
# 是 0 引用）。界面真正跑的是旧版 Modules\Base\ModAnimation.cs 里的 AniEase*。
# 两套公式不同（尤其 Back / Elastic），所以下面按**旧版**实现。
#
# 和 Qt 内置的区别：Qt 的 OutBack 过冲固定 10%、峰值在 0.58；PCL 这套过冲
# 从 17.8% 到 60.2% 可调，而且**峰值位置会跟着前移**（0.61 → 0.51），
# 形状本身不一样 —— 光调 Qt 的 setOvershoot() 补不出来。

# PCL 旧版 Back 的力度档
PCL_BACK_POWER = {
    "weak": 2.0,          # 过冲 17.78%
    "middle": 3.0,        # 过冲 25.81%
    "strong": 4.0,        # 过冲 38.62%
    "extra_strong": 5.0,  # 过冲 60.24%
}


def make_pcl_back_out(power: float = 2.0):
    """PCL 旧版缓出回弹：f(t) = 1 - (1-t)^p · cos(1.5πt)

    注意 p 越大过冲越猛：p = 3 - 0.5 * power（power 就是上面那几档）。
    """
    p = 3.0 - 0.5 * float(power)

    def fn(t):
        u = 1.0 - t
        return 1.0 - math.pow(u, p) * math.cos(1.5 * math.pi * t)
    return fn


def make_pcl_elastic_out(power: float = 2.0):
    """PCL 旧版弹性缓出

    用的是一条闭式曲线（常数 ln2*10 和 pi*6.5），p = power + 4 决定弹几下：
    p 越大过冲越大（1.394 → 1.470），而且**峰值位置会往左移**（0.508 → 0.307），
    所以手感从"软弹"变成"硬弹簧"。
    """
    p = float(power) + 4.0
    ln2_10 = math.log(2) * 10.0
    pi65 = math.pi * 6.5

    def fn(t):
        if t <= 0:
            return 0.0
        if t >= 1:
            return 1.0
        return 1.0 - math.pow(2, -ln2_10 * t) * math.cos(pi65 * t)
    return fn

# ============================================================
# 打包成 QEasingCurve
# ============================================================
#
# ⚠️ **Qt 最多只支持 10 个自定义缓动函数**（超了会抛
# "a maximum of 10 different easing functions are supported"）。
# 所以这里必须缓存：同样的名字+参数只造一条，反复取用的是同一个对象。
# 也意味着**别在循环里现造曲线**，能复用就复用。

_CURVE_CACHE = {}


def as_curve(fn, key=None) -> QEasingCurve:
    """把函数包成 QEasingCurve（Qt 的自定义曲线），同名同参复用同一个对象"""
    if key is not None and key in _CURVE_CACHE:
        return _CURVE_CACHE[key]
    curve = QEasingCurve()
    curve.setCustomType(lambda t: float(fn(_clamp01(t))))
    if key is not None:
        _CURVE_CACHE[key] = curve
    return curve


def cached_count() -> int:
    """已经注册过几条自定义曲线（上限 10，调试用）"""
    return len(_CURVE_CACHE)


# 常用的几条，按名字取
def named(name: str, **kwargs) -> QEasingCurve:
    """按名字造一条曲线。

    名字保持和 Qt / Penner 的习惯一致，方便和 QEasingCurve 那些内置的对照：

        power_out(power=3)      三次方缓出（= Qt 的 OutCubic）
        power_in_out(power=5)   五次方两端缓（= Qt 的 InOutQuint）
        back_out(overshoot=0.12) 冲过头 12% 再回来（Qt 的 OutBack 写死 10%）
        elastic_out(amplitude=1, period=0.3)
        spring(overshoot=0.12, cycles=1.15)   阻尼正弦（"弹进去"那种）
    """
    def key_for(extra=None):
        parts = [name] + [f"{k}={v}" for k, v in sorted(kwargs.items())]
        if extra:
            parts += [f"{k}={v}" for k, v in sorted(extra.items())]
        return "|".join(parts)

    if name == "power_in":
        return as_curve(make_power_in(kwargs.get("power", 3.0)), key_for())
    if name == "power_out":
        return as_curve(make_power_out(kwargs.get("power", 3.0)), key_for())
    if name == "power_in_out":
        return as_curve(make_power_in_out(kwargs.get("power", 3.0)), key_for())
    if name == "back_in":
        return as_curve(make_back_in(kwargs.get("s", BACK_S)), key_for())
    if name == "back_out":
        ratio = kwargs.get("overshoot")
        s = kwargs.get("s")
        if s is None:
            s = s_for_overshoot(ratio if ratio is not None else 0.10)
        return as_curve(make_back_out(s), key_for({"s": round(s, 4)}))
    if name == "back_in_out":
        return as_curve(make_back_in_out(kwargs.get("s", BACK_S)), key_for())
    if name == "elastic_out":
        return as_curve(make_elastic_out(kwargs.get("amplitude", 1.0),
                                        kwargs.get("period", 0.3)), key_for())
    if name == "elastic_in":
        return as_curve(make_elastic_in(kwargs.get("amplitude", 1.0),
                                       kwargs.get("period", 0.3)), key_for())
    if name == "spring":
        return as_curve(make_damped_sine(
            kwargs.get("overshoot", 0.12),
            kwargs.get("cycles", 1.15),
            kwargs.get("decay", 5.0)), key_for())
    if name == "pcl_back":
        # PCL 旧版：过冲比 Qt 的 OutBack 猛，峰值位置也不同
        return as_curve(make_pcl_back_out(kwargs.get("power", 2.0)), key_for())
    if name == "pcl_elastic":
        return as_curve(make_pcl_elastic_out(kwargs.get("power", 2.0)), key_for())
    raise KeyError(f"没有这条缓动：{name}")
