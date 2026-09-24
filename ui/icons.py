"""图标加载

图标统一放 assets/icons/，用 SVG 而不是 emoji 或字体符号。

为什么不用 emoji：🛡 🔗 ✂ 这类字符的渲染完全取决于系统装了什么 emoji 字体
（Windows 上是 Segoe UI Emoji），大小、颜色、基线和风格都不受我们控制，
放在界面里跟旁边的文字对不齐，观感很违和。SVG 则由我们决定一切，
而且打包后也能跟着 assets/ 一起走。

路径解析交给 core/resources.py，所以源码运行和打包运行都能找到。
"""

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QPixmap

from core.config import get_config_dir
from core.resources import resource_path
from core.version_settings import version_settings

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
# 程序图标（窗口 / 任务栏 / exe）
# ============================================================
#
# 用户给的是**四个不同尺寸的 ICO**（32/64/128/512），不是一张大图。
# 四个都塞进同一个 QIcon 里：Windows 按场景自己挑 ——
# 任务栏用小图、Alt+Tab 用大图，只给一张的话小尺寸会糊。

APP_ICON_SIZES = (32, 64, 128, 512)
APP_ICON_NAME = "icon{suffix}.ico"

# 给 PyInstaller 用的那张（workflow 里的 --icon；一张就够，它自己会挑）
APP_ICON_FOR_BUILD = "assets/icons/icon512.ico"


def app_icon() -> QIcon:
    """程序图标（多尺寸）。缺文件就返回空 QIcon —— 界面照样能起来"""
    result = QIcon()
    for size in APP_ICON_SIZES:
        path = Path(resource_path("assets", "icons", APP_ICON_NAME.format(suffix=size)))
        if path.is_file():
            result.addFile(str(path), QSize(size, size))
    if result.isNull() and APP_ICON_SIZES:
        # 尺寸名对不上时（用户换了文件）退一步：拿最大那张凑合
        fallback = Path(resource_path("assets", "icons",
                                      APP_ICON_NAME.format(suffix=max(APP_ICON_SIZES))))
        if fallback.is_file():
            return QIcon(str(fallback))
    return result


# ============================================================
# 版本类型图标（版本列表左边的那个方块）
# ============================================================
#
# 放 assets/icons/version/<名字>.png（**PNG 不是 SVG**：这些是渲染好的像素图，
# 矢量化反而费劲，而且以后用户自己换图也方便）。
#
# 文件不存在就返回 None，调用方退回"原/包/FA"那种文字徽章 ——
# 图标是锦上添花，缺一个不该让列表崩掉或者显示成空白。
#
# 这些图是从 E:\item-assets 那套物品图里挑的（256x256 等距渲染），
# 对应关系见下面的 _LOADER_ICONS / _TYPE_ICONS / assets/icons/version/README.md。

VERSION_ICON_DIR = ("assets", "icons", "version")

# 加载器 → 图标名。**顺序有意义**，具体的一定要排在泛化的前面：
#   neoforge   里含 "forge"
#   optifabric 里含 "fabric"
#   cleanroom  虽然不含 "forge"，但它是 Forge 的分支，排前面没坏处
_LOADER_ICONS = (
    ("neoforge", "neoforge"),
    ("cleanroom", "cleanroom"),
    ("optifabric", "optifine"),
    ("labymod", "labymod"),
    ("forge", "forge"),
    ("fabric", "fabric"),
    ("quilt", "quilt"),
    ("optifine", "optifine"),
)

# 版本 type → 图标名。远古版本用圆石，快照用命令方块（开发版）
_TYPE_ICONS = {
    "release": "vanilla",
    "snapshot": "snapshot",
    "old_alpha": "old",
    "old_beta": "old",
}


def version_icon_name(version: dict) -> str:
    """这个版本该用哪张内置图（没有对应图就返回空串）"""
    # 整合包排在加载器前面：右边的标签已经写了加载器名，图标用箱子能多带一点信息
    if version.get("kind") == "pack":
        return "pack"
    loader = (version.get("loader") or "").lower()
    for key, name in _LOADER_ICONS:
        if key in loader:
            return name
    return _TYPE_ICONS.get(version.get("type", ""), "")


def custom_icon_path(version: dict):
    """用户给这个版本挑的图标；没挑 / 找不到就返回 None

    去**配置目录**的 `icons/` 里找（配置目录见设置页底部；便携模式下就是
    启动器旁边的 `Mosslight/icons/`）：

        <配置目录>/icons/diamond.png      写 "diamond" 或 "diamond.png" 都认

    ⚠️ **界面还没做**：想换某个版本的图标，得自己把 png 丢进那个目录、
    再把 `versions.json` 里那个版本写上 `"icon": "diamond.png"`。
    （`icon` 已经在 `core/version_settings.py` 的 FIELDS 里了，不会被丢掉。）

    以前这里还有一条"内置调色板"分支（`assets/icons/version/custom/`，
    64 张 256×256 的图）。那批图没有任何界面能用上、白占 446 KB，
    2026-09 删掉了，所以现在只剩"用户自己的图"这一条。

    只接受**文件名，不接受路径** —— 别让一个配置项能指到 `..\\..` 去。
    """
    raw = version_settings.get(version.get("id", "")).get("icon", "")
    name = str(raw).strip()
    if not name or Path(name).name != name:
        return None

    folder = get_config_dir() / "icons"
    for candidate in (folder / name, folder / f"{Path(name).stem}.png"):
        if candidate.is_file():
            return candidate
    return None


def screen_dpr(widget=None) -> float:
    """当前屏幕缩放（125% 的系统返回 1.25）

    用来决定"要画多少个物理像素"。取不到就退回 1.0，宁可普通也不要崩。
    """
    try:
        if widget is not None:
            dpr = widget.devicePixelRatioF()
            if dpr and dpr > 0:
                return float(dpr)
    except Exception:
        pass
    try:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            return float(screen.devicePixelRatio())
    except Exception:
        pass
    return 1.0


def _scale_crisp(pixmap: QPixmap, target: int) -> QPixmap:
    """把图缩到边长 target

    **分步缩**：256 → 32 一次缩 8 倍，Qt 的双线性会丢很多细节（看着糊）。
    先对半砍到只剩 2 倍以内，再收尾 —— 这等于给了一个粗糙的金字塔滤波，
    代价只是多几次缩放。
    """
    width = max(pixmap.width(), pixmap.height())
    current = pixmap
    while width // 2 >= target * 2:
        width //= 2
        current = current.scaled(width, width,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
    return current.scaled(target, target,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)


def version_pixmap(version: dict, size: int, dpr: float = 1.0):
    """版本图标；size 是**逻辑像素**，dpr 是屏幕缩放

    ⚠️ 必须按 dpr 放大后再交给 Qt：屏幕 125% 时那个 38px 的方块实际要 48 个
    物理像素，只给 32 的话 Qt 会把图拉大 —— 看着就是糊的（用户报过这个）。
    放大以后再用 setDevicePixelRatio 告诉 Qt"这是给 1.25 倍屏用的"，
    它就会按逻辑尺寸摆放，不会重复放大。
    """
    path = custom_icon_path(version)
    if path is None:
        name = version_icon_name(version)
        if not name:
            return None
        path = Path(resource_path(*VERSION_ICON_DIR, f"{name}.png"))
        if not path.is_file():
            return None

    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return None

    target = max(1, int(round(size * max(1.0, dpr))))
    scaled = _scale_crisp(pixmap, target)
    scaled.setDevicePixelRatio(max(1.0, dpr))
    return scaled
