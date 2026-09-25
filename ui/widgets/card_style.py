"""卡片透明度：让"卡片底色"能透出背景图，字和图标保持清楚（移植自实验项目）

## 为什么是「改底色 alpha」而不是 QGraphicsOpacityEffect

`QGraphicsOpacityEffect` 会把整张卡片（含文字、图标）一起变淡，字也跟着糊。
我们要的是"背景透出来、字还是清楚的"，所以只把**卡片底色**换成带 alpha 的
`rgba(r, g, b, α)`。

## 和实验项目的实现差在哪（我们是 QSS 驱动的）

实验项目里卡片是各自 `setStyleSheet` 的，所以它有个 `card_style.py` 一处出样式、
各页面提供 `refresh_card_style()` 挨个重套。我们的卡片**全在
`assets/styles/parts/*.qss` 里**，所以这里换个做法：

    主题调色板 + 卡片不透明度  →  一段**追加到末尾**的 QSS 覆盖块

这样一处生效、不用挨个控件刷；而且颜色照样只来自主题（`core/theme.py`），
不写死 RGB（实验项目那边的 (35, 36, 40) 是它自己那套深色，我们有浅色主题）。

⚠️ 覆盖块必须**排在所有片段之后**（`main_window.load_styles` 里最后追加）：
QSS 的规则冲突按"后者胜"，排前面会被原来的 `background-color: @bg_card@` 盖掉。
"""

from core import appearance
from ui import theme_state

# 要跟着透明度的**卡片级**选择器（用卡片底的都算）。
# ⚠️ 只列"一整块卡片"，别把按钮/输入框/滚动条塞进来 ——
# 那是控件不是卡片，跟着变透明会变成"界面缺一块"。
CARD_SELECTORS = (
    "#Card",
    "#AccountChip",
    "#CollapsibleHeader",
    "#DownloadNavCard",
    "#DownloadRow",
    "#ModCard",
    "#ModDetailCard",
    "#ModFilterPanel",
    "#ModGroupHeader",
    "#ModVersionCard",
    "#RemoteVersionRow",
)

# 悬停/选中态（比底色亮一级，跟着一起算 alpha）
HOVER_SELECTORS = (
    "#AccountChip:hover",
    "#CollapsibleHeader:hover",
    "#ModCard:hover",
    "#ModGroupHeader:hover",
    "#ModGroupHeader:checked",
    "#RemoteVersionRow:hover",
    "#VersionList::item:hover",
)

_BG_TOKEN = "bg_card"
_BORDER_TOKEN = "border"
_HOVER_TOKEN = "bg_elevated"
_ACCENT_TOKEN = "accent"

# 「外壳」的固定不透明度（左侧竖栏 + 下载页那列分类栏）。
#
# 为什么不跟着"卡片透明度"那一档：那一档是给**内容卡片**用的，用户完全可以
# 把它留在 100%（卡片实心）而仍然希望背景图从侧栏透出来一点 —— 用户 2026-09
# 就是这么要的（"左侧要变成半透明"）。所以外壳单独给一个固定 alpha：
# 图能透出来、字仍然清楚。
#
# ⚠️ **只在设了背景图**时才这么干：没图时把侧栏弄半透明只会让它和页面糊成
# 一片；纯色背景（bg_color）下透出来的就是同一种颜色，等于把层次抹掉。
CHROME_ALPHA = 0.82

# (选择器, 用哪个调色板 token 当底色) —— 外壳各有各的底色，别一律用 bg_card
CHROME_SELECTORS = (
    ("#Sidebar", "bg_sidebar"),
    ("#DownloadNavCard", "bg_card"),
)


def _rgb(color) -> "tuple[int, int, int]":
    """`#rrggbb` → (r, g, b)；认不出来给个中性灰（别抛，样式坏了界面就白屏）"""
    text = str(color or "").strip().lstrip("#")
    if len(text) != 6:
        return 35, 36, 40
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return 35, 36, 40


def alpha(opacity: int = None) -> int:
    """0~100 的不透明度 → QSS 用的 0~255 alpha"""
    if opacity is None:
        opacity = appearance.get_card_opacity()
    try:
        opacity = int(opacity)
    except (TypeError, ValueError):
        opacity = 100
    low, high = appearance.CARD_OPACITY_RANGE
    opacity = max(low, min(high, opacity))
    return int(round(opacity / 100 * 255))


def _rgba(rgb, a: int) -> str:
    return "rgba(%d, %d, %d, %d)" % (rgb[0], rgb[1], rgb[2], a)


def opacity_stylesheet(opacity: int = None, palette: dict = None) -> str:
    """卡片透明度的覆盖样式（100% 时返回空串 —— 不用白写一段）

    `palette` 不给就问 `ui/theme_state` 要当前主题（含用户自定义强调色）。
    """
    try:
        value = int(opacity if opacity is not None
                    else appearance.get_card_opacity())
    except (TypeError, ValueError):
        value = 100
    if value >= 100:
        return ""

    if not palette:
        try:
            palette = theme_state.palette()
        except Exception:                                   # noqa: BLE001
            palette = {}

    a = alpha(value)
    bg = _rgba(_rgb(palette.get(_BG_TOKEN, "#232428")), a)
    border = _rgba(_rgb(palette.get(_BORDER_TOKEN, "#2e3034")), a)
    hover = _rgba(_rgb(palette.get(_HOVER_TOKEN, "#26282d")), a)
    accent = palette.get(_ACCENT_TOKEN) or "#5ec269"

    lines = ["/* 卡片透明度：card_opacity=%d（core/appearance.py）*/" % value]
    for sel in CARD_SELECTORS:
        lines.append("%s { background-color: %s; border-color: %s; }"
                     % (sel, bg, border))
    for sel in HOVER_SELECTORS:
        lines.append("%s { background-color: %s; border-color: %s; }"
                     % (sel, hover, accent))
    return "\n".join(lines) + "\n"


def chrome_stylesheet(palette: dict = None) -> str:
    """外壳（左侧竖栏 / 分类栏）的半透明覆盖

    只在**设了背景图**时才有内容（理由见 CHROME_ALPHA 那段）。

    ⚠️ 追加时**排在卡片透明度那段后面**：用户如果把卡片透明度也调低了，
    外壳应该听自己的固定值，而不是被卡片那一档带走（外壳没有"卡片"这个语义）。
    """
    try:
        bg_settings = appearance.get_bg()
    except Exception:                                       # noqa: BLE001
        return ""
    if not (bg_settings.get("image") or ""):
        return ""

    if not palette:
        try:
            palette = theme_state.palette()
        except Exception:                                   # noqa: BLE001
            palette = {}

    a = int(round(max(0.0, min(1.0, CHROME_ALPHA)) * 255))
    lines = ["/* 外壳半透明：设了背景图时让图透出来（%d%%）*/"
             % round(CHROME_ALPHA * 100)]
    for sel, token in CHROME_SELECTORS:
        rgb = _rgb(palette.get(token, "#16171a"))
        lines.append("%s { background-color: %s; }" % (sel, _rgba(rgb, a)))
    return "\n".join(lines) + "\n"
