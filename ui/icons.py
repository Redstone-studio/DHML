"""SVG 图标加载

图标统一放 assets/icons/，用 SVG 而不是 emoji 或字体符号。

为什么不用 emoji：🛡 🔗 ✂ 这类字符的渲染完全取决于系统装了什么 emoji 字体
（Windows 上是 Segoe UI Emoji），大小、颜色、基线和风格都不受我们控制，
放在界面里跟旁边的文字对不齐，观感很违和。SVG 则由我们决定一切，
而且打包后也能跟着 assets/ 一起走。

路径解析交给 core/resources.py，所以源码运行和打包运行都能找到。
"""

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap

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


# ============================================================
# 版本类型图标（版本列表左边的那个方块）
# ============================================================
#
# 放 assets/icons/version/<名字>.png（**PNG 不是 SVG**：这些是像素画，
# 方块/铁砧那种质感用矢量画反而费劲，而且以后用户自己换图也方便）。
#
# 文件不存在就返回 None，调用方退回"原/包/FA"那种文字徽章 ——
# 图标是锦上添花，缺一个不该让列表崩掉或者显示成空白。
#
# 加新图标只要往这儿加一行，然后把 png 丢进 assets/icons/version/。

VERSION_ICON_DIR = ("assets", "icons", "version")

# 顺序有意义：先看加载器，再看版本类型（加载器版本也是 release，
# 但用户想看到的是 Forge/Fabric 的图标）
_LOADER_ICONS = (
    ("neoforge", "neoforge"),
    ("forge", "forge"),
    ("fabric", "fabric"),
    ("quilt", "quilt"),
    ("optifine", "optifine"),
)

# 版本 type → 图标名。远古版本（alpha/beta）用圆石，正式版用草方块
_TYPE_ICONS = {
    "release": "vanilla",
    "snapshot": "vanilla",
    "old_alpha": "old",
    "old_beta": "old",
}


def version_icon_name(version: dict) -> str:
    """这个版本该用哪张图（没有对应图就返回空串）"""
    loader = (version.get("loader") or "").lower()
    for key, name in _LOADER_ICONS:
        if key in loader:
            return name
    return _TYPE_ICONS.get(version.get("type", ""), "")


def version_pixmap(version: dict, size: int):
    """版本图标，按边长 size 等比缩放；没有图就返回 None"""
    name = version_icon_name(version)
    if not name:
        return None
    path = Path(resource_path(*VERSION_ICON_DIR, f"{name}.png"))
    if not path.is_file():
        return None
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return None
    return pixmap.scaled(QSize(size, size),
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
