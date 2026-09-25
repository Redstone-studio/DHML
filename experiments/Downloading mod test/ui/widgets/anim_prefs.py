"""
入场动画的预设

界面里所有"卡片滑入"都用这里的预设决定**方向、时长、错峰间隔、回弹**。
想要第四种风格，只要往 PRESETS 里加一行、再给个名字就行，
不用去改 slide_in.py 和各个页面。

## 关于 PCL2 那种风格

PCL2 是 C# / WPF 写的，它的 `DoubleAnimation` + `CubicEase` 那套没法直接搬到
Qt 上，但"感觉"是能对出来的。它给人顺滑的原因大概三条：

  1. **位移很短**（几十像素，不是从屏幕外面飞进来）
  2. **时长偏短**（0.2 秒上下，不做慢动作）
  3. **缓动是"快出慢收"**（EaseOut），并且**不做回弹**（回弹会显得软件味重）

所以「仿 PCL2」这一档刻意把位移调到 28px、时长 170ms、无回弹 ——
跟其他几档比，它是最"没存在感"的那个，也就是最像原生控件的那种。

## 回弹（overshoot）

回弹不是靠 QEasingCurve.Back 做的：Back 曲线是先往**反方向**拉一下再过去，
用在"从左边滑入"上会先往右退一点，看着很别扭。
真正的"滑到位再弹一下"是三段：滑到 0 → 冲过头 → 收回来，见 slide_in.py 的 ANIM_BOUNCE。
"""
from dataclasses import dataclass

# 方向
DIR_HORIZONTAL = "h"
DIR_VERTICAL = "v"

# 动画形态
ANIM_SIMPLE = "simple"
ANIM_BOUNCE = "bounce"     # 滑到位 → 再补一段小回弹（两段，比较"硬"）
ANIM_SPRING = "spring"     # 真·弹簧：阻尼正弦，过冲后自己收敛回来（连贯）
ANIM_PCL = "pcl"           # PCL 那套：位移拆两条并行曲线 + 淡入（最自然）

# 主段滑入的缓动曲线
#
# 前三个是 Qt 内置的；带 `back:` / `elastic:` / `spring:` 前缀的是自己在
# ui/widgets/pcl_ease.py 里实现的**参数化**版本 —— Qt 内置的过冲是写死的
# （OutBack 永远冲 10%），参数化之后才能"想弹多少写多少"。
EASE_CUBIC = "cubic"       # OutCubic：起步快、结尾缓
EASE_QUINT = "quint"       # OutQuint：起步更快、收尾更绵长 —— 更"顺"
EASE_INOUT = "inout"       # InOutCubic：两头慢中间快，最柔和
EASE_BACK = "back:0.18"    # 冲过头 18% 再回来（Qt 的 OutBack 只有 10%）
EASE_ELASTIC = "elastic"   # 弹性：来回弹好几下
EASE_SPRING = "spring:0.14"  # 阻尼正弦：弹一下收住


@dataclass(frozen=True)
class AnimationPreset:
    key: str
    name: str            # 界面上显示的名字
    description: str     # 一句话说明
    direction: str
    duration: int        # 单张卡片滑完要多久（毫秒）
    offset: int          # 起点离终点多远（像素，正数）
    interval: int        # 相邻两张错开多久（毫秒）
    style: str = ANIM_SIMPLE      # simple / bounce / spring
    overshoot: int = 12           # 只对回弹类有效：冲过头多少像素
    bounce_ratio: float = 0.72    # 只对 bounce 有效：多久滑到位（剩下的用来回弹）
    ease: str = EASE_CUBIC        # 主段滑入用哪条缓动曲线


PRESETS = {
    "slide_left": AnimationPreset(
        key="slide_left",
        name="左侧滑入",
        description="从左边滑进来，干脆利落（默认）",
        direction=DIR_HORIZONTAL,
        duration=420,
        offset=120,
        interval=95,
    ),
    "slide_up": AnimationPreset(
        key="slide_up",
        name="下方滑入",
        description="从下面升上来，收尾绵长（easeOutQuint）",
        direction=DIR_VERTICAL,
        duration=460,
        offset=80,
        interval=100,
        ease=EASE_QUINT,
    ),
    "slide_left_bounce": AnimationPreset(
        key="slide_left_bounce",
        name="左侧滑入（弹簧）",
        description="从左边滑进来，用弹簧的方式过冲一下再自己收住（连贯，不是两段拼的）",
        direction=DIR_HORIZONTAL,
        duration=760,
        offset=120,
        interval=115,
        style=ANIM_SPRING,
        overshoot=28,
        bounce_ratio=0.5,
    ),
    "pcl_like": AnimationPreset(
        key="pcl_like",
        name="仿 PCL2（克制）",
        description="位移很短、不回弹、收尾绵长 —— 最接近原生控件的那种顺滑",
        direction=DIR_VERTICAL,
        duration=340,
        offset=28,
        interval=55,
        ease=EASE_QUINT,
    ),
    "slide_up_elastic": AnimationPreset(
        key="slide_up_elastic",
        name="下方滑入（弹性）",
        description="从下面升上来，像弹簧一样来回弹几下才停（参数化 elastic）",
        direction=DIR_VERTICAL,
        duration=900,
        offset=110,
        interval=95,
        ease=EASE_ELASTIC,
    ),
    "slide_left_back": AnimationPreset(
        key="slide_left_back",
        name="左侧滑入（过冲 18%）",
        description="滑过头 18% 再退回来。过冲量可调，不是 Qt 那个写死的 10%",
        direction=DIR_HORIZONTAL,
        duration=520,
        offset=120,
        interval=85,
        ease=EASE_BACK,
    ),
    "pcl_recipe": AnimationPreset(
        key="pcl_recipe",
        name="PCL 原味（拆两条 + 淡入）",
        description="照 PCL 的真实配方：位移拆成快、慢两条并行曲线，同时淡入。"
                    "最自然的一档，建议先试它",
        direction=DIR_VERTICAL,
        duration=350,
        offset=16,
        interval=25,
        style=ANIM_PCL,
    ),
}

# 给版本列表用的更短参数：分组展开后组内卡片一下出来很多，
# 用搜索页那套参数会显得拖沓
VERSION_VARIANT = {
    "slide_left": dict(duration=330, interval=70, offset=90),
    "slide_up": dict(duration=360, interval=75, offset=60),
    "slide_left_bounce": dict(duration=560, interval=85, offset=100),
    "pcl_like": dict(duration=280, interval=40, offset=22),
    "slide_left_back": dict(duration=440, interval=65, offset=95),
    "slide_up_elastic": dict(duration=760, interval=75, offset=85),
    # PCL 那条列表进场的原数值就是"位移很小"（16px），组内卡片照搬
    "pcl_recipe": dict(duration=300, interval=22, offset=14),
}

DEFAULT_PRESET = "slide_left"

# 下拉框 / 设置界面里的顺序
PRESET_ORDER = ("slide_left", "slide_up", "slide_left_bounce", "pcl_like",
                "slide_left_back", "slide_up_elastic", "pcl_recipe")


def preset(key: str) -> AnimationPreset:
    """按 key 取预设；认不出来就退回默认（不抛异常 —— 配置是被手改过的）"""
    return PRESETS.get(key) or PRESETS[DEFAULT_PRESET]


def preset_for_versions(key: str) -> AnimationPreset:
    """版本列表里的卡片用哪套参数（同一风格、更短的时长）"""
    base = preset(key)
    overrides = VERSION_VARIANT.get(base.key, {})
    if not overrides:
        return base
    from dataclasses import replace
    return replace(base, **overrides)


def direction_text(direction: str) -> str:
    return "水平（左右）" if direction == DIR_HORIZONTAL else "垂直（上下）"


# ============================================================
# 速度档位
# ============================================================
#
# 时长和错峰是**乘**上去的，不是各自写死一份 —— 这样加档位不用改每个预设，
# 也不会出现"改了一档忘了改另一档"。
#
# 换算一下"整批大概多久铺完"（一批 12 张）：
#     铺完时间 ≈ 错峰 × (张数 - 1) + 单张时长
# 以左侧滑入为例（原始 420ms / 95ms）：
#     标准 1.0× → 95×11 + 420  ≈ 1.47s   ← 默认档就差不多是你说的 1.5 秒
#     慢   1.5× → 143×11 + 630 ≈ 2.2s
#     夸张 2.2× → 209×11 + 924 ≈ 3.2s
#
# 注意"太快"多半不是单张滑得太快，而是**错峰太小**：卡片几乎同时出现，
# 眼睛就来不及捕捉"依次滑入"的过程。要放慢就调错峰，这个倍率是两者一起放大的。

SPEEDS = {
    "fast": (0.7, "快（0.7×）"),
    "normal": (1.0, "标准（1×）"),
    "slow": (1.5, "慢（1.5×）"),
    "very_slow": (2.2, "很慢（2.2×）"),
    "extreme": (3.2, "夸张（3.2×）"),
}

SPEED_ORDER = ("fast", "normal", "slow", "very_slow", "extreme")
DEFAULT_SPEED = "normal"


def speed_factor(key: str) -> float:
    """速度档位 → 倍率（认不出来就按标准）"""
    return SPEEDS.get(key, SPEEDS[DEFAULT_SPEED])[0]


def speed_text(key: str) -> str:
    return SPEEDS.get(key, SPEEDS[DEFAULT_SPEED])[1]


def scaled(p: AnimationPreset, factor: float) -> AnimationPreset:
    """把预设施加一个速度倍率

    错峰也跟着一起缩放：只放慢单张、不放慢错峰的话，卡片会一张张慢慢出现，
    整体节奏反而变得断断续续。
    """
    from dataclasses import replace
    if factor == 1.0:
        return p
    return replace(
        p,
        duration=max(40, int(round(p.duration * factor))),
        interval=max(8, int(round(p.interval * factor))),
    )
