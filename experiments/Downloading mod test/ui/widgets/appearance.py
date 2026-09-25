"""
外观设置（背景）

和动画设置共用配置目录的 setting.json，但**各存各的键** ——
关键约定：保存时必须把整份配置读出来改一个键再写回去，
不能只写自己那几个键，否则会把别的设置覆盖掉（动画预设就这么被吃掉过一次）。
"""
import json
import os
from pathlib import Path

SETTING_FILE_NAME = "setting.json"

# ---------- 键名 ----------
KEY_BG_COLOR = "bg_color"      # 纯色背景，"" = 用默认深色
KEY_BG_IMAGE = "bg_image"      # 背景图路径，"" = 不用图
KEY_BG_MODE = "bg_mode"        # fill / fit / center / tile
KEY_BG_DIM = "bg_dim"          # 压暗程度 0~200
KEY_CARD_OPACITY = "card_opacity"   # 卡片不透明度 0~100（100 = 完全不透明）
KEY_ANIM_FADE = "anim_fade"         # 入场要不要淡入（关掉能省一点合成开销）
KEY_MULTI_THREAD = "multi_thread"     # 多线程下载/访问总开关
KEY_DOWNLOAD_THREADS = "download_threads"   # 线程数
KEY_MC_DIR = "mc_dir"                 # 游戏目录（.minecraft 那一层），"" = 自动探测

DEFAULT = {
    KEY_BG_COLOR: "",
    KEY_BG_IMAGE: "",
    # 默认居中：用户明确要的。原图大小放中间，四周留边
    KEY_BG_MODE: "center",
    KEY_BG_DIM: 90,
    KEY_CARD_OPACITY: 100,
    # 默认开：位移 + 淡入比纯位移自然得多。
    # 但它要给每张卡片挂 QGraphicsOpacityEffect（渲染到离屏缓冲再合成），
    # 显卡弱/驱动不合的机器上会掉帧 —— 觉得卡就把它关掉试试。
    KEY_ANIM_FADE: True,
    # 多线程默认**关**：先让用户自己开，出问题好定位。
    # 关着的时候线程数用 DEFAULT_THREADS，开关打开才用滑块的值。
    KEY_MULTI_THREAD: False,
    KEY_DOWNLOAD_THREADS: 8,
    # 游戏目录默认空 = 每次用时自动探测（见 get_mc_dir）。
    # 用户手动选过一次之后就记住，不再探测。
    KEY_MC_DIR: "",
}

# 线程数范围与默认值
THREAD_RANGE = (1, 32)
DEFAULT_THREADS = 8

# 卡片透明度的范围。下限给 10 而不是 0：全透明的话卡片就完全看不见了，
# 用户会以为界面坏了，没有意义。
CARD_OPACITY_RANGE = (10, 100)

# 背景图的铺法。命名和顺序**对齐 Windows 的"选择适合度"**：
#   填充 / 适应 / 拉伸 / 居中 / 跨区
# 这样用户在系统里怎么挑的，在这儿照着挑就行，不用重新学一套说法。
#
# 注：Windows 的「平铺」在这里**故意不做**（用户 2026-09 明确不要）。
# 真想加回来的话，铺法表里补一行、再去 background_canvas._draw_image 里
# 加一个按行列贴的分支就行（那个分支原来写过，删掉时逻辑就是这个）。
MODES = {
    "fill": "填充",       # 等比放大到铺满，多出来的裁掉（不留黑边）
    "fit": "适应",        # 等比缩放到全图可见，不够的地方留黑边
    "stretch": "拉伸",    # 不管比例，直接拉满（会变形）
    "center": "居中",     # 原始大小，居中放一张
    "span": "跨区",       # 铺满宽度、纵向居中裁切（对应多屏时"横跨所有显示器"）
}

MODE_ORDER = ("fill", "fit", "stretch", "center", "span")

# 每种铺法的一句话说明（设置界面里当提示用）
MODE_HINTS = {
    "fill": "等比放大铺满窗口，多出来的部分裁掉 —— 最常用",
    "fit": "等比缩放到整张图都能看见，不够的地方留边",
    "stretch": "直接拉满窗口，比例会变形",
    "center": "原图大小放中间，四周留边",
    "span": "横向铺满、纵向居中裁切（对应多显示器横跨显示）",
}

# 默认深色（和 main_window 里那套一致）
FALLBACK_COLOR = "#1b1c1f"


def config_dir() -> Path:
    """这个测试项目的配置目录。刻意不 import core.config（那是主项目的）"""
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "MosslightModTest"


def setting_path() -> Path:
    return config_dir() / SETTING_FILE_NAME


def load_all() -> dict:
    """读整份配置（读不出来/坏了 → 空表，调用方各自回退默认）"""
    try:
        raw = json.loads(setting_path().read_bytes())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_key(key: str, value) -> bool:
    """改一个键并写回。**先把整份读出来**，避免覆盖别人的键"""
    data = load_all()
    data[key] = value
    try:
        path = setting_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError as e:
        print(f"[Appearance] 设置写不进去（{e}），这次只在内存里生效")
        return False


def get(key: str):
    value = load_all().get(key, DEFAULT.get(key))
    return value


def get_bg() -> dict:
    """当前背景设置（都做过类型/范围纠正）"""
    raw = load_all()

    color = str(raw.get(KEY_BG_COLOR, DEFAULT[KEY_BG_COLOR]) or "")
    if color and not _is_hex(color):
        color = ""                     # 手改坏了就当没设

    image = str(raw.get(KEY_BG_IMAGE, DEFAULT[KEY_BG_IMAGE]) or "")
    if image and not Path(image).is_file():
        # 图片被删了/挪走了：**保留这个值但不用它**（用户把文件放回来就恢复），
        # 这里只负责告诉调用方"这次没有图"
        image_ok = ""
    else:
        image_ok = image

    mode = str(raw.get(KEY_BG_MODE, DEFAULT[KEY_BG_MODE]) or "fill")
    if mode not in MODES:
        mode = "fill"

    try:
        dim = int(raw.get(KEY_BG_DIM, DEFAULT[KEY_BG_DIM]))
    except (TypeError, ValueError):
        dim = DEFAULT[KEY_BG_DIM]
    dim = max(0, min(200, dim))

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
    """卡片不透明度（10~100）。卡片样式每次构建时读它，所以一改就生效"""
    raw = load_all().get(KEY_CARD_OPACITY, DEFAULT[KEY_CARD_OPACITY])
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT[KEY_CARD_OPACITY]
    low, high = CARD_OPACITY_RANGE
    return max(low, min(high, value))


def get_anim_fade() -> bool:
    """入场动画要不要带淡入（关掉可以省掉 QGraphicsOpacityEffect 的合成开销）"""
    raw = load_all().get(KEY_ANIM_FADE, DEFAULT[KEY_ANIM_FADE])
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes", "on")
    return bool(DEFAULT[KEY_ANIM_FADE])


def get_multi_thread() -> bool:
    """多线程下载/访问的开关"""
    raw = load_all().get(KEY_MULTI_THREAD, DEFAULT[KEY_MULTI_THREAD])
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes", "on")
    return bool(raw)


def get_thread_count() -> int:
    """滑块上填的线程数（不管开关开没开，读的都是这个值）"""
    raw = load_all().get(KEY_DOWNLOAD_THREADS, DEFAULT[KEY_DOWNLOAD_THREADS])
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_THREADS
    low, high = THREAD_RANGE
    return max(low, min(high, value))


def effective_threads() -> int:
    """**实际该用几个线程** —— 干活的地方统一读这个，不要读 get_thread_count()

    开关关着的时候一律用默认值（用户滑块拉多大都不生效），
    这样"关掉就回到默认"这个语义只有一处实现，不会各处各写一遍。
    """
    if not get_multi_thread():
        return DEFAULT_THREADS
    return get_thread_count()


# ---------- 游戏目录 ----------

def get_saved_mc_dir():
    """配置里**存着**的游戏目录（没设过就是 None）"""
    raw = str(get(KEY_MC_DIR) or "").strip()
    if raw and Path(raw).is_dir():
        return Path(raw)
    return None


def get_mc_dir():
    """游戏目录（`.minecraft` 那一层）—— 下载落点全靠它

    没设过就自动探测一个（`core/mc_dir.guess_mc_dir`，会挑"像在玩 mod 的、
    最近玩过的"那份）。**探测结果不写回配置** —— 用户换机器或插移动硬盘时，
    猜出来的东西不该被固化下来。想固定就用设置里的「浏览」选一次。
    """
    saved = get_saved_mc_dir()
    if saved is not None:
        return saved
    from core import mc_dir as mcd
    return mcd.guess_mc_dir()


def set_mc_dir(path) -> bool:
    """记住游戏目录（传空串 = 清掉，回到自动探测）"""
    return save_key(KEY_MC_DIR, str(path or ""))


def is_mc_dir_auto() -> bool:
    """当前用的是不是"自动探测"的结果（设置界面里要提示一下）"""
    return get_saved_mc_dir() is None


def _is_hex(text: str) -> bool:
    text = text.strip()
    if len(text) != 7 or not text.startswith("#"):
        return False
    try:
        int(text[1:], 16)
    except ValueError:
        return False
    return True
