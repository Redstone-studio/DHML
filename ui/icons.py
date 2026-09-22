"""SVG 图标加载

图标统一放 assets/icons/，用 SVG 而不是 emoji 或字体符号。

为什么不用 emoji：🛡 🔗 ✂ 这类字符的渲染完全取决于系统装了什么 emoji 字体
（Windows 上是 Segoe UI Emoji），大小、颜色、基线和风格都不受我们控制，
放在界面里跟旁边的文字对不齐，观感很违和。SVG 则由我们决定一切，
而且打包后也能跟着 assets/ 一起走。

路径解析交给 core/resources.py，所以源码运行和打包运行都能找到。
"""

from PyQt6.QtGui import QIcon

from core.resources import resource_path

# 账户类型 → 图标名（对应 assets/icons/<name>.svg）
ACCOUNT_TYPE_ICONS = {
    "microsoft": "shield",
    "thirdparty": "network",
    "offline": "offline",
}


def icon(name: str) -> QIcon:
    """按名字加载 assets/icons/<name>.svg"""
    return QIcon(str(resource_path("assets", "icons", f"{name}.svg")))
