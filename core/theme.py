"""主题：调色板 + QSS 变量替换

纯 Python（不 import PyQt6）—— 符合 core/ 的约定。

assets/styles/app.qss 里的颜色全是 `@变量@`，由这个模块换成实际值：

    DARK   当前在用的这套。每个值都是照着原来的 dark.qss 抄的，
           所以换成模板之后界面一个像素都不会变（有验证过）
    LIGHT  浅色一套

为什么不是两个 qss 文件：那样要维护两份 700 行的副本，加条规则得改两次，
迟早漂掉。变量化之后只有**一份结构** + 两份便宜的字典。

关于自定义强调色：给个起点色，其余深浅由 _accent_variants() 按 HLS 算。
算出来的和手挑的肯定有细微差别，所以**自带主题仍然用手挑的精确值**，
只有用户自己指定颜色时才走计算。
"""

import colorsys
import re

# 主题模式。"system" = 跟随系统（由界面层读 Qt 的 colorScheme 决定实际用哪套）
MODES = ("system", "dark", "light")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


DARK = {
    # ---------- 背景层次：从最外层到最内层，一级比一级亮 ----------
    "bg_window": "#0f1012",      # 窗口最底层
    "bg_sidebar": "#16171a",     # 侧边栏
    "bg_page": "#1b1c1f",        # 页面
    "bg_card": "#212226",        # 卡片
    "bg_elevated": "#26282d",    # 浮起：下拉弹层、悬停
    "bg_control": "#292a2f",     # 输入框 / 按钮
    "bg_hover": "#33353a",       # 按钮悬停
    "bg_pressed": "#232428",     # 按钮按下
    "bg_disabled": "#1f2024",    # 禁用
    "bg_check": "#232428",       # 勾选框未选中的底

    # ---------- 边框 ----------
    "border": "#2e3034",
    "border_control": "#3a3c42",
    "border_hover": "#4a4d54",
    "border_selected": "#45484f",
    "border_disabled": "#2b2d31",

    # ---------- 文字 ----------
    "text": "#e9e9ec",
    "text_secondary": "#d8d8dc",
    "text_dim": "#a0a1a7",
    "text_faint": "#6e7076",
    "text_disabled": "#56585e",
    "text_bright": "#ffffff",    # 标题（浅色主题下要变深）
    "on_accent": "#ffffff",      # 画在强调色块上的文字（浅色主题下仍然是白）

    # ---------- 强调色一族 ----------
    "accent": "#5ec269",              # 文字 / 边框 / 悬停
    "accent_fill": "#3f8f4b",         # 填充（按钮底）
    "accent_fill_hover": "#4da25a",
    "accent_fill_pressed": "#357d40",
    "accent_text": "#8fd49a",         # 强调色系里的浅文字
    "accent_text_strong": "#b6f0bf",
    "accent_bg": "#24361f",           # 淡底色（徽章）
    "accent_border": "#37522f",
    "accent_bg_disabled": "#26331f",
    "accent_border_disabled": "#2f3d2a",
    "accent_text_disabled": "#5e6b58",
    "accent_selected_bg": "#33513a",  # 下拉项选中
    "avatar_bg": "#2f5d3a",

    # ---------- 语义色 ----------
    "warn": "#e0b341",
    "warn_bg": "#33290f",
    "warn_border": "#5c4a1a",
    "danger": "#e0575f",
    "danger_hover": "#ff7078",
    "danger_bg": "#331a1c",
    "danger_bg_hover": "#3a2427",
    "danger_border": "#5a3236",
    "danger_border_strong": "#5c2a2e",
    "pack_bg": "#1e2a3d",             # 整合包徽章
    "pack_border": "#2f4260",
    "pack_text": "#7fb0ee",

    # ---------- 输入框里拖选文字时的底色 ----------
    "select_bg": "#3f6ea8",
}


LIGHT = {
    # 浅色下层次反过来：页面是浅灰，卡片是白的
    "bg_window": "#e6e8ec",
    "bg_sidebar": "#eef0f3",
    "bg_page": "#f4f5f7",
    "bg_card": "#ffffff",
    "bg_elevated": "#f0f1f4",
    "bg_control": "#ffffff",
    "bg_hover": "#e9ebef",
    "bg_pressed": "#e2e4e9",
    "bg_disabled": "#f1f2f4",
    "bg_check": "#ffffff",

    "border": "#dcdfe4",
    "border_control": "#ccd0d7",
    "border_hover": "#b3b8c1",
    "border_selected": "#c2c7cf",
    "border_disabled": "#e4e6ea",

    "text": "#1f2328",
    "text_secondary": "#3a4046",
    "text_dim": "#5b6169",
    "text_faint": "#868c95",
    "text_disabled": "#aab0b8",
    "text_bright": "#101317",
    "on_accent": "#ffffff",

    # 浅色下绿色要重一点，否则白字压在按钮上看不清
    "accent": "#2f8f45",
    "accent_fill": "#2f8f45",
    "accent_fill_hover": "#37a350",
    "accent_fill_pressed": "#277a3a",
    "accent_text": "#1d6b2c",
    "accent_text_strong": "#14521f",
    "accent_bg": "#e3f4e6",
    "accent_border": "#a9dcb3",
    "accent_bg_disabled": "#eef5ef",
    "accent_border_disabled": "#d5e6d8",
    "accent_text_disabled": "#9aae9d",
    "accent_selected_bg": "#d8f0dc",
    "avatar_bg": "#2f8f45",

    "warn": "#8a6100",
    "warn_bg": "#fdf3d9",
    "warn_border": "#e8d29a",
    "danger": "#c02b33",
    "danger_hover": "#d93a42",
    "danger_bg": "#fdeaea",
    "danger_bg_hover": "#fadadd",
    "danger_border": "#e8b4b7",
    "danger_border_strong": "#d98b90",
    "pack_bg": "#e8f0fb",
    "pack_border": "#b6cdf0",
    "pack_text": "#2b62b0",

    "select_bg": "#bcd7f5",
}


# ---------- 颜色计算 ----------

def _to_rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _to_hex(rgb) -> str:
    return "#" + "".join(
        f"{max(0, min(255, round(c * 255))):02x}" for c in rgb
    )


def shift(hex_color: str, lightness: float = 0.0, saturation: float = 0.0) -> str:
    """按 HLS 调亮/调暗（lightness 正数变亮，负数变暗）"""
    h, l, s = colorsys.rgb_to_hls(*_to_rgb(hex_color))
    l = min(1.0, max(0.0, l + lightness))
    s = min(1.0, max(0.0, s + saturation))
    return _to_hex(colorsys.hls_to_rgb(h, l, s))


def blend(fg: str, bg: str, ratio: float) -> str:
    """把 fg 按 ratio 混到 bg 上：0 = 全 bg，1 = 全 fg"""
    f, b = _to_rgb(fg), _to_rgb(bg)
    return _to_hex(tuple(b[i] + (f[i] - b[i]) * ratio for i in range(3)))


def readable_on(color: str) -> str:
    """在这个颜色上面写字，用白还是用深色？

    用感知亮度（ITU-R BT.601）判断，所以挑个亮黄当强调色也不会白字糊成一片。
    """
    r, g, b = _to_rgb(color)
    return "#101317" if (0.299 * r + 0.587 * g + 0.114 * b) > 0.62 else "#ffffff"


def _accent_variants(accent: str, base: dict, mode: str) -> dict:
    """从一个强调色派生出一整族（填充 / 悬停 / 按下 / 文字 / 淡底 / 边框）"""
    dark = mode != "light"
    page = base["bg_page"]
    fill = shift(accent, -0.13 if dark else -0.06)
    return {
        "accent": accent,
        "accent_fill": fill,
        "accent_fill_hover": shift(accent, -0.07 if dark else 0.0),
        "accent_fill_pressed": shift(accent, -0.20 if dark else -0.12),
        "accent_text": shift(accent, 0.16 if dark else -0.14),
        "accent_text_strong": shift(accent, 0.30 if dark else -0.22),
        "accent_bg": blend(accent, page, 0.13 if dark else 0.16),
        "accent_border": blend(accent, page, 0.24 if dark else 0.34),
        "accent_bg_disabled": blend(accent, page, 0.07),
        "accent_border_disabled": blend(accent, page, 0.12),
        "accent_text_disabled": blend(accent, page, 0.35),
        "accent_selected_bg": blend(accent, page, 0.18 if dark else 0.24),
        "avatar_bg": fill,
        "on_accent": readable_on(fill),
    }


# ---------- 对外接口 ----------

def effective_mode(mode: str, system_is_dark: bool) -> str:
    """把 "system" 落到实际的 dark / light

    读系统主题是界面层的事（要 Qt），所以这里只接一个布尔值，
    这样这个函数是纯逻辑，能直接单测。
    """
    if mode == "light":
        return "light"
    if mode == "dark":
        return "dark"
    return "dark" if system_is_dark else "light"


def palette(mode: str = "dark", accent: str = "") -> dict:
    """取一套调色板

    accent 传 "#rrggbb" 就在这套主题的基础上换掉强调色一族；
    传空（或者格式不对）就用主题自带的那套手挑值。
    """
    base = dict(LIGHT if mode == "light" else DARK)
    if accent and _HEX_RE.match(accent):
        base.update(_accent_variants(accent, base, mode))
    return base


def resolve(qss: str, values: dict) -> str:
    """把 qss 里的 @变量@ 换成实际值

    注意 @ICONS@ 是另一套机制（由 ui/main_window.py 换成图标绝对路径），
    不在这里处理。
    """
    for key, value in values.items():
        qss = qss.replace(f"@{key}@", str(value))
    return qss


def unresolved(qss: str) -> "list[str]":
    """找出还没被替换的 @变量@（小写那种）

    用来防手滑：在 qss 里写了个不存在（或拼错）的变量名，
    解析后会留下 @xxx@，Qt 会直接忽略那条规则 —— 界面就悄悄少了个样式。
    所以在加载时检查一遍，有问题打日志。
    """
    return sorted(set(re.findall(r"@([a-z_][a-z0-9_]*)@", qss)))
