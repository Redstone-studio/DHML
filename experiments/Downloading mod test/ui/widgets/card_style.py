"""
卡片样式

卡片（搜索结果、版本分组、具体版本）的背景色统一从这儿出，只为了一个目的：
**让"卡片透明度"能一处调、处处生效**。

## 为什么用 rgba 而不是 QGraphicsOpacityEffect

`QGraphicsOpacityEffect` 会让整张卡片（含文字和图标）一起变淡，字也跟着糊；
我们想要的是"卡片底色透出背景图，字仍然清楚"，所以只把**背景色**换成带 alpha 的：

    background-color: rgba(35, 36, 40, α)

alpha 从设置里的 0~100 换算过来。

## 谁在用

    ui/widgets/mod_card.py             搜索结果卡片
    ui/widgets/collapsible_group.py    版本分组（那个 QToolButton）
    ui/widgets/version_card.py         具体版本卡片

改完透明度以后，已经建出来的卡片要重新 setStyleSheet 才会更新 ——
各页面自己提供 refresh_card_style() 做这件事。
"""
from ui.widgets import appearance

# 卡片底色（深色）。透明度是加在这个颜色上的
CARD_BG = (35, 36, 40)        # #232428
CARD_BG_HOVER = (42, 44, 48)  # #2a2c30
CARD_BORDER = (46, 48, 52)    # #2e3034
VERSION_BG = (31, 32, 35)     # #1f2023
VERSION_BORDER = (42, 44, 48)  # #2a2c30
ACCENT = (94, 194, 105)       # #5ec269


def _alpha(opacity: int = None) -> int:
    """0~100 的不透明度 → QSS 用的 0~255 alpha"""
    if opacity is None:
        opacity = appearance.get_card_opacity()
    try:
        opacity = int(opacity)
    except (TypeError, ValueError):
        opacity = 100
    return int(round(max(0, min(100, opacity)) / 100 * 255))


def _rgb(color, opacity: int = None) -> str:
    return f"rgba({color[0]}, {color[1]}, {color[2]}, {_alpha(opacity)})"


def search_card_qss(object_name: str = "ModResultCard", opacity: int = None) -> str:
    """搜索结果卡片的样式（带透明度）"""
    return f"""
        {object_name} {{
            background-color: {_rgb(CARD_BG, opacity)};
            border-radius: 10px;
            border: 1px solid {_rgb(CARD_BORDER, opacity)};
        }}
        {object_name}:hover {{
            background-color: {_rgb(CARD_BG_HOVER, opacity)};
            border-color: rgb({ACCENT[0]}, {ACCENT[1]}, {ACCENT[2]});
        }}
    """


def group_button_qss(opacity: int = None) -> str:
    """版本分组那个折叠按钮的样式（带透明度）"""
    return f"""
        QToolButton {{
            background-color: {_rgb(CARD_BG, opacity)};
            color: #e9e9ec;
            border: 1px solid {_rgb(CARD_BORDER, opacity)};
            border-radius: 8px;
            padding: 10px 14px;
            font-size: 13px;
            text-align: left;
        }}
        QToolButton:hover {{
            background-color: {_rgb(CARD_BG_HOVER, opacity)};
            border-color: rgb({ACCENT[0]}, {ACCENT[1]}, {ACCENT[2]});
        }}
        QToolButton:checked {{
            background-color: {_rgb(CARD_BG, opacity)};
            border-color: rgb({CARD_BORDER[0]}, {CARD_BORDER[1]}, {CARD_BORDER[2]});
        }}
    """


def version_card_qss(object_name: str = "VersionCard", opacity: int = None) -> str:
    """具体版本卡片的样式（带透明度）"""
    return f"""
        {object_name} {{
            background-color: {_rgb(VERSION_BG, opacity)};
            border-radius: 8px;
            border: 1px solid {_rgb(VERSION_BORDER, opacity)};
        }}
    """
