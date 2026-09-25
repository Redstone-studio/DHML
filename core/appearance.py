"""外观设置：背景 + 入场动效 + 卡片透明度（纯 Python，**不 import Qt**）

从实验项目 `experiments/Downloading mod test` 的
`ui/widgets/appearance.py` + `ui/widgets/anim_settings.py` 搬过来的，
**只换了一层存储**：那边自己管一个 `setting.json`，这边统一进
`core/config.py`（启动器只有一个配置文件，主题/语言/内存都在里面）。

## 和实验项目不一样的两处（都是刻意的）

1. **游戏目录 / 线程数不在这里** —— 启动器本来就有（`config.minecraft_dir`、
   `config.multi_thread` + `download_threads`），所以这里的同名函数只是**转发**，
   免得同一个设置有两份、改一处不生效。
2. **默认背景色不写死** —— 那边是 `FALLBACK_COLOR = "#1b1c1f"`，这边由画布去问
   当前主题（`ui/theme_state.palette()["bg_page"]`），浅色主题才不会画成深灰。
   `FALLBACK_COLOR` 只留给"对话框里的预览色块"用。

## 值都要纠正一遍

配置文件是纯文本，用户手改过之后可能是 `"120"`（字符串）、`"false"`、
或者超出范围的数字。这里每个 getter 都做**类型 + 范围**纠正，
读坏了就回默认值 —— 背景/动效这种东西坏了不该让启动器起不来。
"""

from pathlib import Path

from core.config import DEFAULT_THREADS, THREAD_RANGE, as_bool, config
from core.config import get_multi_thread as _config_multi_thread
from core.config import get_thread_count as _config_thread_count

# ---------- 键名（跟 config 里的键一一对应）----------

KEY_BG_COLOR = "bg_color"          # 纯色背景，"" = 用主题的页面色
KEY_BG_IMAGE = "bg_image"          # 背景图路径，"" = 不用图
KEY_BG_MODE = "bg_mode"            # fill / fit / stretch / center / span
KEY_BG_DIM = "bg_dim"              # 压暗程度 0~200
KEY_CARD_OPACITY = "card_opacity"  # 卡片底色不透明度 10~100
KEY_ANIM_FADE = "anim_fade"        # 入场要不要淡入
KEY_ANIM_PRESET = "anim_preset"    # 动效风格（见 core/anim_prefs.py）
KEY_ANIM_SPEED = "anim_speed"      # 速度档（见 core/anim_prefs.py）
KEY_ANIM_ENABLED = "anim_enabled"  # 动效**总开关**（关掉=列表不包动画层）
KEY_MULTI_THREAD = "multi_thread"          # 转发用（真值在 core/config.py）
KEY_DOWNLOAD_THREADS = "download_threads"  # 转发用
KEY_MC_DIR = "minecraft_dir"               # 转发用

DIM_RANGE = (0, 200)

DEFAULT = {
    KEY_BG_COLOR: "",
    KEY_BG_IMAGE: "",
    # 默认居中：原图大小放中间，四周留边（实验项目里用户明确要的）
    KEY_BG_MODE: "center",
    KEY_BG_DIM: 90,
    KEY_CARD_OPACITY: 100,
    # 默认开：位移 + 淡入比纯位移自然得多。
    # 但它要给每张卡片挂 QGraphicsOpacityEffect（渲染到离屏缓冲再合成），
    # 显卡弱/驱动不合的机器上会掉帧 —— 觉得卡就把它关掉试试。
    KEY_ANIM_FADE: True,
    KEY_ANIM_PRESET: "slide_left",
    KEY_ANIM_SPEED: "normal",
    # 默认开。关掉 = 列表**根本不包动画层**（不是"动画时长为 0"），
    # 顺带省掉淡入那个离屏合成 —— 弱机/核显上这才是真开销。
    KEY_ANIM_ENABLED: True,
}


# 卡片透明度的范围。下限给 10 而不是 0：全透明卡片就看不见了，
# 用户会以为界面坏了，没有意义。
CARD_OPACITY_RANGE = (10, 100)

# 背景图的铺法。命名和顺序**对齐 Windows 的"选择适合度"**：
#   填充 / 适应 / 拉伸 / 居中 / 跨区
# 这样用户在系统里怎么挑的，在这儿照着挑就行，不用重新学一套说法。
#
# ⚠️ 这些是**界面文案**：`tr()` 在设置对话框里做（core/ 里也能用 tr，
# 见 core/i18n.py），这里保持原样，别在这一层翻译两遍。
MODES = {
    "fill": "填充",       # 等比放大到铺满，多出来的裁掉（不留黑边）
    "fit": "适应",        # 等比缩放到全图可见，不够的地方留黑边
    "stretch": "拉伸",    # 不管比例，直接拉满（会变形）
    "center": "居中",     # 原始大小，居中放一张
    "span": "跨区",       # 铺满宽度、纵向居中裁切（对应多屏时"横跨所有显示器"）
}  # noqa: i18n  —— 这些中文在背景设置对话框里过 tr()

MODE_ORDER = ("fill", "fit", "stretch", "center", "span")

# 每种铺法的一句话说明（设置界面里当提示用）
MODE_HINTS = {
    "fill": "等比放大铺满窗口，多出来的部分裁掉 —— 最常用",
    "fit": "等比缩放到整张图都能看见，不够的地方留边",
    "stretch": "直接拉满窗口，比例会变形",
    "center": "原图大小放中间，四周留边",
    "span": "横向铺满、纵向居中裁切（对应多显示器横跨显示）",
}  # noqa: i18n  —— 同上，对话框里的 tr() 负责翻

# 对话框里"没设背景"时预览色块用的颜色（深色主题的页面色）。
# ⚠️ 画布**不用**它 —— 画布问 ui/theme_state 要当前主题的页面色，
# 不然浅色主题下会画成深灰。
FALLBACK_COLOR = "#1b1c1f"


# ---------- 读写 ----------

def save_key(key: str, value) -> bool:
    """改一个键并落盘（走 core/config，失败也不会抛）"""
    return bool(config.set(key, value))


def save_keys(pairs: dict) -> bool:
    """一次改好几个键（对话框"确定"时用，少落几次盘）"""
    data = dict(config.data)
    data.update(pairs or {})
    config.data = data
    return bool(config.save())


def get(key: str):
    value = config.get(key)
    return DEFAULT.get(key) if value is None else value


def _int_in(key: str, low: int, high: int, fallback: int) -> int:
    try:
        value = int(config.get(key))
    except (TypeError, ValueError):
        value = fallback
    return max(low, min(high, value))


def _is_hex(text: str) -> bool:
    text = str(text or "").strip()
    if len(text) != 7 or not text.startswith("#"):
        return False
    try:
        int(text[1:], 16)
    except ValueError:
        return False
    return True


def get_bg() -> dict:
    """当前背景设置（都做过类型/范围纠正）"""
    color = str(config.get(KEY_BG_COLOR) or "")
    if color and not _is_hex(color):
        color = ""                     # 手改坏了就当没设

    image = str(config.get(KEY_BG_IMAGE) or "")
    if image and not Path(image).is_file():
        # 图片被删了/挪走了：**保留这个值但不用它**（用户把文件放回来就恢复），
        # 这里只负责告诉调用方"这次没有图"
        image_ok = ""
    else:
        image_ok = image

    mode = str(config.get(KEY_BG_MODE) or DEFAULT[KEY_BG_MODE])
    if mode not in MODES:
        mode = DEFAULT[KEY_BG_MODE]

    dim = _int_in(KEY_BG_DIM, DIM_RANGE[0], DIM_RANGE[1], DEFAULT[KEY_BG_DIM])

    return {
        "color": color,
        "image": image_ok,       # 实际可用的图片路径
        "image_raw": image,      # 用户填的原值（界面上要显示出来）
        "mode": mode,
        "dim": dim,
    }


def has_custom() -> bool:
    """有没有设过自定义背景（决定要不要给背景控件开绘制）"""
    bg = get_bg()
    return bool(bg["image"] or bg["color"])


def get_card_opacity() -> int:
    """卡片底色不透明度（10~100）。卡片样式每次构建时读它，所以一改就生效"""
    low, high = CARD_OPACITY_RANGE
    return _int_in(KEY_CARD_OPACITY, low, high, DEFAULT[KEY_CARD_OPACITY])


def get_anim_fade() -> bool:
    """入场动画要不要带淡入（关掉可以省掉 QGraphicsOpacityEffect 的合成开销）

    ⚠️ 动效总开关关掉时这里**永远返回 False**：不然"关了动效"还会给每张卡片
    挂一个离屏合成，那就是"关了个寂寞"（开关的意义就是省这个开销）。
    """
    if not get_anim_enabled():
        return False
    return as_bool(config.get(KEY_ANIM_FADE), DEFAULT[KEY_ANIM_FADE])


def get_anim_enabled() -> bool:
    """动效总开关。关掉之后列表连动画层都不包（见 ui/widgets/slide_in.py）"""
    return as_bool(config.get(KEY_ANIM_ENABLED), DEFAULT[KEY_ANIM_ENABLED])


def set_anim_enabled(enabled: bool) -> bool:
    return save_key(KEY_ANIM_ENABLED, bool(enabled))


# ---------- 动效预设（原 anim_settings.py）----------

def get_preset_key() -> str:
    """当前选的是哪个动效预设（认不出来就回默认）"""
    from core.anim_prefs import DEFAULT_PRESET, PRESETS
    key = str(config.get(KEY_ANIM_PRESET) or "")
    return key if key in PRESETS else DEFAULT_PRESET


def set_preset_key(key: str) -> bool:
    from core.anim_prefs import PRESETS
    return save_key(KEY_ANIM_PRESET, key if key in PRESETS else "")


def get_speed_key() -> str:
    """当前速度档（认不出来就回标准）"""
    from core.anim_prefs import DEFAULT_SPEED, SPEEDS
    key = str(config.get(KEY_ANIM_SPEED) or "")
    return key if key in SPEEDS else DEFAULT_SPEED


def set_speed_key(key: str) -> bool:
    from core.anim_prefs import SPEEDS
    return save_key(KEY_ANIM_SPEED, key if key in SPEEDS else "")


# ---------- 转发给 core/config 的那几个（实验项目里它们在这）----------

def get_multi_thread() -> bool:
    """多线程下载/访问的开关（真实现在 core/config.py）"""
    return bool(_config_multi_thread())


def get_thread_count() -> int:
    """滑块上填的线程数（真实现在 core/config.py）"""
    return int(_config_thread_count())


def get_mc_dir():
    """游戏目录（`.minecraft` 那一层）"""
    return config.get_minecraft_dir()


def set_mc_dir(path) -> bool:
    """记住游戏目录（传空串 = 清掉，回到自动探测）"""
    return bool(config.set("minecraft_dir", str(path or "")))


def is_mc_dir_auto() -> bool:
    """当前用的是不是"自动探测"的结果（设置界面里要提示一下）"""
    return not str(config.get("minecraft_dir") or "").strip()
