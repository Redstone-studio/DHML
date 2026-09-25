"""模组搜索 / 详情页（下载页左栏「社区资源 → 模组」）

从 `experiments/Downloading mod test` 的 `ui/search_page.py` + `ui/detail_page.py`
移植过来，按本项目约定重写。**故意的差异**（都是铁律级的）：

1. **不用 QThread / 信号** —— 实验是 `SearchWorker(QThread)` + `pyqtSignal`。
   本项目统一「**普通线程只写结果字段 + 主线程 QTimer 轮询取**」
   （QThread 在运行中被析构会直接终止进程，被坑过，见 core/download.py 的说明）。
2. **颜色不写死** —— 全部走 `assets/styles/parts/80-mods.qss` 的 `@token@`。
3. **文案走 `tr()`** —— 源码中文就是 key。
4. **入场动画搬到主项目了（2026-09 用户要求）** —— `slide_in` / `anim_prefs` /
   `pcl_ease` 已经进主项目（`core/anim_prefs.py` + `ui/widgets/slide_in.py`），
   这里的结果卡片和版本分组都走 `StaggerReveal` 依次滑入；
   风格和速度在「个性化 → 动效设置」里调，卡片透明度在「背景设置」里调。
   ⚠️ 之前这里写的是"先不移植那四件套"，那一条已经被用户的新要求取代了。

## 一页两态

    QStackedWidget:
        0  搜索结果（筛选卡 + 结果列表）
        1  详情（固定信息卡 + 可滚版本列表）
        2  出错页（网络不通 / 没有结果）

## 下载怎么接的

**不自己写下载**：`core/mc_dir.target_dir()` 算出落点（版本隔离）→
主项目的 `DownloadManager` + `DownloadWindow`，任务 `kind=tr("模组")`。
进度窗口、镜像回退、失败重试、sha1 跳过已有全是白拿的（见 core/download.py）。

## 图标

`core/cache.py` 是磁盘缓存（`%APPDATA%/Mosslight/cache/icons`），
取图走普通线程 + 定时器收，**绝不在主线程联网**（实验项目在这上面卡过 2.4 秒）。
"""

import threading
import time
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractAnimation, QEasingCurve, QPropertyAnimation, Qt, QTimer, pyqtSignal
)
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget
)

from core import mc_dir as mcd
from core import modpack as mpk
from core import modrinth_api as api
from core.cache import install_version_icon, load_icon, save_icon
from core.config import config, effective_threads
from core.download import DownloadManager, human_size
from core.i18n import tr
from core import install as install_mod
from core import loader_install
from core import standalone
from core.loaders import LOADER_NAMES
from ui.translatable import TranslatableWidget

# 轮询间隔：跟下载页那边一致（150ms，人眼够用，也不至于空转）
POLL_MS = 150

# 一页搜多少条。12 而不是 20：首批要快、要"秒出"，滚到底再要下一批
PAGE_SIZE = 12
# 离底部还有这么多像素就开始抓下一批（只留一点余量，免得贴底那瞬间空白）
LOAD_MORE_THRESHOLD = 60
# 详情页加载条**至少显示这么久**（毫秒）。
# 比搜索页的默认值（180ms）长：点进详情时用户是"在等一个页面"，
# 请求命中缓存时几十毫秒就回来了，不兜一下这条会一闪而过，等于没有。
DETAIL_MIN_VISIBLE_MS = 550

# 版本分组一次铺几个。Fabric API 那种项目有 **400 多个**分组，
# 全铺出来一眼望不到底、建控件也慢。滚到底再铺下一批。
GROUP_BATCH = 8
# 折叠动画时长（跟 collapsible_group / loading_bar 用一套节奏）
EXPAND_MS = 220
# 折叠标题行的最小高度（像素）。
# ⚠️ 这个值不只是好看的问题：折叠态下标题行的 `sizeHint` 由它决定，
# 而"一屏能装几行"直接决定**滚动条有没有可滚范围**。
# 不给的话 QToolButton 只量出 19px（QSS 的 padding 在某些场景不进 sizeHint），
# 于是十几个分组摞起来还没视口高 → `maximum == 0` → 滚不动 →
# "滚到底自动加载"永远触发不了，用户只能点「加载更多分组」按钮。
# 36px ≈ 13px 字 + 上下各 10px 内边距，也是在屏幕上比较好点的高度。
GROUP_HEADER_MIN_H = 36
# 「内容不够一屏就自动再补几批」的上限。
#
# ⚠️ 别把它调回 1。折叠标题 36px、8 个才 288px，**大窗口下一屏能放十几个** ——
# 只补一批（16 组 ≈ 605px）在很多窗口尺寸下仍然撑不满，于是：
# 滚动条 `maximum == 0` → 滚不动 → "滚到底自动加载"永远触发不了 →
# 用户面对一屏分组 + 一个要一直点的按钮（2026-09 用户报的就是这个）。
# 3 = 最多补到 32 组，足够把任何常见窗口撑出滚动条；补满之后才交给滚动加载。
AUTO_FILL_MAX = 3
# 布局稳定下来要等多久（毫秒）—— 布局期 rangeChanged 会连发几十次，
# 而且那时候视口高度还没定，必须等它稳定再判断"内容够不够一屏"
LAYOUT_SETTLE_MS = 120

# Qt 的 QWIDGETSIZE_MAX：不限高
_NO_LIMIT = 16777215

# 下拉框里的选项。源文案在 tr() 里，值是 Modrinth 接口原文（**不翻译**）
#
# ⚠️ 「全部」这个哨兵**只存中文原文、不要在这里 tr() 求值** ——
# 模块级求值只发生一次（导入时），语言一换它就永远是旧语言了
# （实测：切到英文后下拉框第一项还是"全部"）。显示时才 tr()，
# 见 `_fill_combo()`；`core/mc_dir.py` / `core/modrinth_api.py` 那边
# 也统一认这个中文字面量。
ANY = "全部"

MC_VERSIONS = (
    "1.21.5", "1.21.4", "1.21.3", "1.21.2", "1.21.1", "1.21",
    "1.20.6", "1.20.4", "1.20.2", "1.20.1", "1.20",
    "1.19.4", "1.19.2", "1.19",
    "1.18.2", "1.18.1",
    "1.17.1",
    "1.16.5", "1.16.4",
    "1.15.2", "1.14.4",
    "1.12.2", "1.7.10",
)

# (接口值, 显示文案)。加载器名是产品名，不翻译
LOADERS = (
    ("fabric", "Fabric"), ("forge", "Forge"), ("neoforge", "NeoForge"),
    ("quilt", "Quilt"),
)

# Modrinth 的分类。键是接口原文，值是源文案
CATEGORIES = (
    ("adventure", "冒险"), ("decoration", "装饰"), ("economy", "经济"),
    ("equipment", "装备"), ("food", "食物"), ("game-mechanics", "游戏机制"),
    ("library", "支持库"), ("magic", "魔法"), ("management", "管理"),
    ("minigame", "小游戏"), ("mobs", "生物"), ("optimization", "性能优化"),
    ("social", "社交"), ("storage", "存储"), ("technology", "科技"),
    ("transportation", "交通"), ("utility", "实用工具"), ("worldgen", "世界生成"),
)  # noqa: i18n  —— 这些中文在 _fill_combos() 里过 tr()

# 排序。键是接口原文（index 参数），值是源文案
SORTS = (
    ("relevance", "相关度"), ("downloads", "下载量"), ("follows", "关注数"),
    ("newest", "最新发布"), ("updated", "最近更新"),
)  # noqa: i18n  —— 同上

# 「社区资源」那几栏对应的 Modrinth project_type。
#
# ⚠️ 顺序跟下载页左栏的导航一致（模组 / 整合包 / 数据包 / 资源包 / 光影）。
# ⚠️ **没有"世界"**：Modrinth 只索引 模组 / 整合包 / 资源包 / 光影 / 数据包，
# 世界存档不在它的项目类型里。所以左栏那一栏保持占位页，不硬做。
# 各类型落到版本文件夹下的哪个子目录见 core/mc_dir.py 的 TYPES
# （mods / shaderpacks / resourcepacks / datapacks）。
MOD_TYPES = (
    ("mod", "模组"),
    ("modpack", "整合包"),
    ("datapack", "数据包"),
    ("resourcepack", "资源包"),
    ("shader", "光影包"),
)  # noqa: i18n  —— 这些中文在 project_type_label() / 标签栏里过 tr()

DEFAULT_PROJECT_TYPE = "mod"

# **哪几类有"加载器"这个概念**。资源包 / 光影包 / 数据包没有 ——
# 给它们塞 `categories:fabric` 这类 facet，Modrinth 会返回 **0 条**而且不报错
# （最难查的那种）。界面据此把加载器下拉禁掉，facets 也据此跳过。
# ⚠️ 只在这一处定义：`core/modrinth_api.search_projects` 里那份判断是从
# 调用方传进去的 project_type 决定的，两边语义要对齐。
LOADER_TYPES = ("mod", "modpack")

# 加载器显示名（键是 Modrinth 的 categories 值）
LOADER_LABELS = {
    "fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge",
    "quilt": "Quilt", "liteloader": "LiteLoader", "rift": "Rift",
}


# ============================================================
# 纯函数（能离线单测，不碰 Qt）
# ============================================================

def project_type_label(ptype: str) -> str:
    """Modrinth 的 project_type → 界面文案（认不出来原样返回）"""
    key = (ptype or DEFAULT_PROJECT_TYPE).lower()
    for value, text in MOD_TYPES:
        if value == key:
            return tr(text)
    return key


def format_number(n) -> str:
    """1200000 → "1.2M"（搜索结果里下载量/关注数用，太长的数字扫不动）"""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fK" % (n / 1_000)
    return "%d" % n


def format_date(raw: str) -> str:
    """`2024-03-05T12:00:00+00:00` → `2024-03-05`

    解析不出来就截前 10 个字符（宁可难看也别显示空白）。
    """
    text = str(raw or "")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def loaders_label(key: str) -> str:
    """加载器的显示名（Forge / Fabric / …）—— 没有就用原 key，别显示成空白"""
    text = str(key or "").lower()
    return LOADER_NAMES.get(text, key or "")


def version_file(version: dict):
    """版本里该下的那个文件（**转发到 core.modrinth_api**，保留这个名字给测试用）"""
    return api.primary_file(version)


def group_versions(versions: list, prefer_version: str = "", prefer_loader: str = "") -> list:
    """把版本按「游戏版本 + 加载器 + 是否预览版」分组

    照实验 `detail_page.group_versions()` 的规则，返回：

        [{"game_version": str, "loader": str, "prerelease": bool,
          "versions": [...], "sort_key": tuple, "matches_filter": bool}, ...]

    一个版本可能支持多个游戏版本 / 多个加载器（Fabric 版本也常支持 Quilt），
    所以是**一对多展开**——同一个版本会出现在多个分组里。

    预览版（beta / alpha）单独成组且**排在正式版后面**：用户绝大多数时候要
    正式版，把预览版混在里面会让他点错。

    `prefer_version` / `prefer_loader` 是**用户在搜索页选的筛选条件**
    （比如 1.21.5 + Fabric）。给了这两个，就：
      · 给对得上的分组打上 `matches_filter=True`
      · **把它们排到最前面**（用户刚筛完就点进来，多半就是想要这几个）

    ⚠️ 传进来的可以是「全部」这个哨兵（下拉框第一项就是它），
    那等于没筛（`_real_any()` 归一）。
    """
    want_v = _real_any(prefer_version)
    want_l = _real_any(prefer_loader)

    groups = {}
    for v in versions or []:
        if not isinstance(v, dict):
            continue
        vtype = str(v.get("version_type") or "release")
        prerelease = vtype in ("beta", "alpha")
        for gv in v.get("game_versions") or []:
            for loader in v.get("loaders") or []:
                groups.setdefault((gv, loader, prerelease), []).append(v)

    order = {"neoforge": 0, "fabric": 1, "forge": 2, "quilt": 3}
    out = []
    for (gv, loader, prerelease), items in groups.items():
        items = sorted(items, key=lambda x: str(x.get("date_published") or ""),
                       reverse=True)
        matched = bool(want_v) and gv == want_v and \
            (not want_l or (loader or "").lower() == want_l.lower())
        out.append({
            "game_version": gv,
            "loader": loader,
            "prerelease": prerelease,
            "versions": items,
            "matches_filter": matched,
            # 排序键：**对得上筛选的排最前**，然后游戏版本新→旧、正式版→预览版、
            # 加载器按常用度。0/1 放最前面就是为了让 pinned 赢过版本号比较。
            "sort_key": (0 if matched else 1,
                         tuple(-x for x in _numbers(gv)),
                         1 if prerelease else 0,
                         order.get(loader, 99),
                         str(gv)),
        })
    out.sort(key=lambda g: g["sort_key"])
    return out


# 版本类型的显示文案（`tr()` 的源文案）。键是 Modrinth 的 version_type
VERSION_TYPE_LABELS = {
    "release": "正式版",
    "beta": "测试版",
    "alpha": "快照",
}  # noqa: i18n  —— 这些中文在 version_type_label() 里过 tr()


def version_type_label(vtype: str) -> str:
    """Modrinth 的 version_type → 界面文案（认不出来原样返回）

    用在**版本行上的类型徽章**（`_VersionRow`）。
    折叠标题上**不显示类型**——那是标准格式 `Fabric 26w14a 预览版`，
    展开后每行本来就有徽章，标题上再写一遍是重复（用户 2026-09 要求去掉）。
    """
    return tr(VERSION_TYPE_LABELS.get(vtype, vtype))


def _real_any(value) -> str:
    """把哨兵「全部」/ 空值归一成空串（等于"没筛"）"""
    text = str(value or "").strip()
    return "" if text == ANY else text


def _numbers(text: str) -> tuple:
    """`1.20.1` → `(1, 20, 1)`（排序用）"""
    out = []
    for part in str(text or "").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def _version_dir_names(mc_dir) -> set:
    """`versions/` 下现有哪些目录名（装前装后各取一次，多的就是这次装的）"""
    import os as _os
    from pathlib import Path as _Path
    root = _Path(mc_dir) / "versions"
    try:
        return {p.name for p in root.iterdir() if p.is_dir()}
    except (_os.OSError, AttributeError):
        return set()


def _log_win(win, text: str) -> None:
    """往下载窗口那个控制台写一句（没有窗口就丢掉）"""
    if win is not None and text:
        try:
            win.stage_write(text)
        except Exception:                                   # noqa: BLE001
            pass


def _drop_version_dir(mc_dir, version_id: str, win=None) -> bool:
    """删掉一个**这次安装才造出来**的中间层版本目录

    ⚠️ 只在"里面的东西已经合进实例了、而且没有任何版本还继承它"时才删 ——
    不然就是把人家的版本吃了。调用方负责确认它确实是这次造的（见 `_fresh_dirs`）。
    """
    import shutil as _shutil
    from pathlib import Path as _Path
    if not version_id:
        return False
    if standalone.referenced_by(mc_dir, version_id):
        _log_win(win, tr("还有版本继承着 {name}，先留着", name=version_id))
        return False
    folder = _Path(mc_dir) / "versions" / version_id
    if not folder.is_dir():
        return False
    try:
        _shutil.rmtree(folder)
        _log_win(win, tr("中间那层 {name} 已经合进实例，目录撤掉了",
                         name=version_id))
        return True
    except OSError as e:
        _log_win(win, tr("撤掉 {name} 失败：{err}", name=version_id, err=e))
        return False


def group_title(group: dict) -> str:
    """分组标题：`Fabric 1.20.4` / `Fabric 1.20.4 预览版`

    ⚠️「预览版」**不带括号**（用户 2026-09 要求）：括号挤在版本号后面看着像
    版本名的一部分（`26.3-rc-2（预览版）`），去掉括号、空一格更像一个标记。
    """
    loader = LOADER_LABELS.get(group["loader"], group["loader"] or "")
    text = "%s %s" % (loader, group["game_version"])
    if group["prerelease"]:
        text += " " + tr("预览版")
    return text


def target_dir_for(hit: dict, game_version: str, loader: str):
    """算下载落点：转发到 `core.mc_dir.target_dir`（游戏目录从配置/探测来）"""
    return mcd.target_dir(mcd.mc_dir_from_config(), game_version, loader,
                          (hit or {}).get("project_type") or "mod")


# ============================================================
# 界面
# ============================================================

class _ModCard(QFrame):
    """一条搜索结果：图标 + 名字 + 作者 + 描述 + 类型/加载器徽章 + 下载量"""

    def __init__(self, hit: dict, on_click=None, parent=None):
        super().__init__(parent)
        self.hit = hit
        self._on_click = on_click
        self.setObjectName("ModCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(96)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(14)

        self.icon = QLabel("?")
        self.icon.setObjectName("ModCardIcon")
        self.icon.setFixedSize(64, 64)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.icon)

        mid = QVBoxLayout()
        mid.setSpacing(4)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self.name = QLabel(str(hit.get("title") or ""))
        self.name.setObjectName("ModCardName")
        name_row.addWidget(self.name)

        author = str(hit.get("author") or "")
        if author:
            self.author = QLabel(tr("作者：{name}", name=author))
            self.author.setObjectName("ModCardAuthor")
            name_row.addWidget(self.author)
        name_row.addStretch()
        mid.addLayout(name_row)

        desc = QLabel(str(hit.get("description") or ""))
        desc.setObjectName("ModCardDesc")
        desc.setWordWrap(True)
        mid.addWidget(desc)

        meta = QHBoxLayout()
        meta.setSpacing(6)

        badge = QLabel(project_type_label(hit.get("project_type")))
        badge.setObjectName("ModTypeBadge")
        meta.addWidget(badge)

        # 加载器徽章：搜索接口只在 categories 里给加载器名
        cats = set(hit.get("display_categories") or hit.get("categories") or [])
        for key in ("fabric", "forge", "neoforge", "quilt"):
            if key in cats:
                tag = QLabel(LOADER_LABELS.get(key, key))
                tag.setObjectName("BadgeAccent")
                meta.addWidget(tag)

        versions = hit.get("versions") or []
        if versions:
            # 只报"支持几个版本"，不列具体版本号：列表里那串太占地方，
            # 而且 Modrinth 的 `versions` 只给版本 id，看不出游戏版本
            info = QLabel(tr("支持 {n} 个游戏版本", n=len(versions)))
            info.setObjectName("ModCardMeta")
            meta.addWidget(info)

        meta.addStretch()

        downloads = QLabel(tr("下载 {n}", n=format_number(hit.get("downloads"))))
        downloads.setObjectName("ModCardMeta")
        meta.addWidget(downloads)

        follows = QLabel(tr("关注 {n}", n=format_number(hit.get("follows"))))
        follows.setObjectName("ModCardMeta")
        meta.addWidget(follows)

        mid.addLayout(meta)
        row.addLayout(mid, 1)

    def set_icon_data(self, data: bytes):
        """图标到了（主线程调用 —— 定时器收的结果）"""
        from PyQt6.QtGui import QPixmap
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        if pixmap.isNull():
            return
        self.icon.setText("")
        self.icon.setPixmap(pixmap.scaled(
            64, 64, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._on_click:
            self._on_click(self.hit)
        super().mouseReleaseEvent(event)


class _VersionRow(QFrame):
    """一个版本：名字 + 类型徽章 + 元信息 + 下载按钮"""

    def __init__(self, version: dict, game_version: str, loader: str,
                 on_download=None, parent=None):
        super().__init__(parent)
        self.version = version
        self.setObjectName("ModVersionCard")

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(3)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name = QLabel(str(version.get("name") or version.get("version_number") or ""))
        name.setObjectName("ModVersionName")
        name_row.addWidget(name)

        vtype = str(version.get("version_type") or "release")
        badge = QLabel(version_type_label(vtype))
        badge.setObjectName("ModVersionBadge")
        badge.setProperty("vtype", vtype)
        name_row.addWidget(badge)
        name_row.addStretch()
        left.addLayout(name_row)

        f = version_file(version) or {}
        bits = [format_date(version.get("date_published"))]
        if f.get("size"):
            bits.append(human_size(f["size"]))
        if version.get("downloads"):
            bits.append(tr("{n} 次下载", n=format_number(version["downloads"])))
        vnum = str(version.get("version_number") or "")
        if vnum:
            bits.append(vnum)
        meta = QLabel("    ·    ".join([b for b in bits if b]))
        meta.setObjectName("ModVersionMeta")
        left.addWidget(meta)
        row.addLayout(left, 1)

        self.download_btn = QPushButton(tr("下载"))
        self.download_btn.setObjectName("PrimaryButton")
        self.download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_btn.setEnabled(f.get("url") is not None)
        self.download_btn.clicked.connect(
            lambda: on_download and on_download(version, game_version, loader))
        row.addWidget(self.download_btn)


class ModVersionGroup(QWidget):
    """一个可折叠的版本分组（`Fabric 1.21.5`

    ▸ 折叠  /  ▾ 展开；展开时才把组内的版本行建出来。

    ## 为什么要折叠

    Fabric API 那种项目有 **400 多个分组**（每个「游戏版本 × 加载器 × 正式/预览」
    一个组），全平铺出来是一眼望不到底的列表，找东西只能靠滚。折叠之后
    标题行就是目录，一眼能扫完。

    ## 为什么不用实验那套 ClipBox + 高度动画

    实验项目为了"收起时不闪"专门写了 `clip_box.py`（内容高度定死、只裁剪可见
    部分），加上 `slide_in` 的入场动画，一套四五个模块互相咬合。
    用户 2026-09 明确决定**先不移植那套**（要大量适配、可能引 bug）。
    这里用的是主项目自己的老办法：`setMaximumHeight` 动画 —— `LoadingBar`
    的收起就是同一个路子，项目里已经有先例，不引入新依赖。

    ## 组内行是**懒建**的

    折叠状态下不建行（400 组 × 每组十几行 = 几千个控件，建出来就卡）。
    `_loaded` 保证只建一次：收起再展开不会重复建（第一版那样写会一直涨行数）。
    """

    toggled = pyqtSignal(bool)

    def __init__(self, group: dict, on_download=None, expanded: bool = False,
                 parent=None):
        super().__init__(parent)
        self.group = group
        self._on_download = on_download
        self._versions = list(group.get("versions") or [])
        self._loaded = False
        self._expanded = False

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        # ⚠️ 用 **QPushButton 而不是 QToolButton**：QToolButton 的文字**永远居中**
        # （`text-align` 对它不生效，实测 TextBesideIcon / TextOnly / NoArrow
        # 三种模式全都居中），而分组标题要左对齐才像列表的标题行。
        # QPushButton 认 `text-align: left`（QSS 里那条本来就在）。
        self.header = QPushButton()
        self.header.setObjectName("ModGroupHeader")
        self.header.setCheckable(True)
        self.header.setChecked(False)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        # ⚠️ **必须显式设成横向 Expanding**：不给的话它会缩到只包住文字
        # （实测容器 688px、它只占 436px）—— 表现就是"分组标题那个框没撑满一行"。
        # 竖向给 Minimum：`setMinimumHeight` 已经咬了下限，需要时还能加点高度。
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Minimum)
        # 高度咬死（理由见 GROUP_HEADER_MIN_H 的说明：它决定滚动条有没有可滚范围）
        self.header.setMinimumHeight(GROUP_HEADER_MIN_H)
        self.header.clicked.connect(self._on_clicked)
        box.addWidget(self.header)

        # 内容区：用 maximumHeight 动画收起/展开
        self.body = QWidget()
        self.body.setObjectName("ModGroupBody")
        self.body_box = QVBoxLayout(self.body)
        self.body_box.setContentsMargins(14, 6, 0, 8)
        self.body_box.setSpacing(6)
        self.body.setMaximumHeight(0)
        self.body.setVisible(False)
        box.addWidget(self.body)

        self.anim = QPropertyAnimation(self.body, b"maximumHeight", self)
        self.anim.setDuration(EXPAND_MS)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.anim.finished.connect(self._after_anim)

        self._refresh_header()
        if expanded:
            self.set_expanded(True, animate=False)

    # ---------- 标题 ----------

    def _refresh_header(self) -> str:
        """标题文字：`▾   Fabric 26w14a 预览版`

        ⚠️ 箭头用字符画、不用 `setArrowType`：自带的小箭头在 QSS 改过 padding
        之后会对不齐，而且颜色不跟主题走。

        ⚠️ 标题就是**标准格式** `加载器 游戏版本 [预览版]`，**后面不再跟
        组内版本类型/数量**（用户 2026-09 明确要求去掉）。
        展开之后每个版本行上本来就有类型徽章，折叠标题上再写一遍是重复信息。
        """
        arrow = "\u25be" if self._expanded else "\u25b8"      # ▾ / ▸
        text = "%s   %s" % (arrow, group_title(self.group))
        if self.group.get("matches_filter"):
            text += "   %s" % tr("匹配你选的版本")
        self.header.setText(text)
        self.header.setToolTip(tr("点一下展开 / 收起这个分组"))
        return text

    def is_expanded(self) -> bool:
        return self._expanded

    # ---------- 展开 / 收起 ----------

    def _on_clicked(self):
        self.set_expanded(self.header.isChecked(), animate=True)

    def _anim_enabled_for_group(self) -> bool:
        """动效总开关（模块级函数，方便分组控件自己问一次）"""
        from core import appearance
        return appearance.get_anim_enabled()

    def set_expanded(self, expanded: bool, animate: bool = True):
        expanded = bool(expanded)
        # 动效总开关关掉时一律不播展开动画（这里每天要展开几十个分组）
        if animate and not self._anim_enabled_for_group():
            animate = False
        self._expanded = expanded
        self.header.setChecked(expanded)
        self._refresh_header()

        if expanded:
            if not self._loaded:
                self._build_rows()
            self.body.setVisible(True)
            target = self._content_height()
            if not animate:
                self.anim.stop()
                self.body.setMaximumHeight(target)
            else:
                current = self.body.maximumHeight()
                self.anim.stop()
                self.anim.setStartValue(max(0, current))
                self.anim.setEndValue(target)
                self.anim.start()
        else:
            if not animate:
                self.anim.stop()
                self.body.setMaximumHeight(0)
                self.body.setVisible(False)
            else:
                self.anim.stop()
                self.anim.setStartValue(max(1, self.body.height()))
                self.anim.setEndValue(0)
                self.anim.start()
        self.toggled.emit(expanded)

    def _build_rows(self):
        """组内的版本行（**只建一次**）"""
        for version in self._versions:
            row = _VersionRow(version, self.group.get("game_version", ""),
                              self.group.get("loader", ""),
                              on_download=self._on_download)
            self.body_box.addWidget(row)
        self._loaded = True

    def _content_height(self) -> int:
        """展开后内容该多高：逐行累加，不依赖 sizeHint

        ⚠️ 本项目没有 `SlideInRow` 那种"隐藏着等到动画才显示"的子控件，
        用 `sizeHint()` 本来也行；但逐行累加在**窗口宽度变化导致文字换行**时
        更稳（`collapsible_group.py` 里就是因为 sizeHint 偏小踩过坑）。
        """
        margins = self.body_box.contentsMargins()
        rows = [self.body_box.itemAt(i).widget()
                for i in range(self.body_box.count())]
        rows = [w for w in rows if w is not None]
        if not rows:
            return 0
        spacing = self.body_box.spacing() * max(0, len(rows) - 1)
        return (sum(w.sizeHint().height() for w in rows) + spacing
                + margins.top() + margins.bottom())

    def _after_anim(self):
        if not self._expanded:
            self.body.setVisible(False)
        else:
            # ⚠️ 展开完要把上限收到**刚算出来的高度**，不能放开成无限大：
            # 放开之后外层布局会把内容拉伸（实验项目实测 132 → 331，动画最后
            # 会"弹"一下）。宽度变了在 resizeEvent 里重算。
            self.body.setMaximumHeight(self._content_height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if (self._expanded and self._loaded
                and self.anim.state() != QAbstractAnimation.State.Running):
            self.body.setMaximumHeight(self._content_height())


class ModsPage(TranslatableWidget):
    """搜索 + 详情。对外信号：

        installed()  有一个 mod 下完了（外面可以据此刷新）

    ⚠️ 顶层窗口（下载进度窗）必须传 parent，否则拿不到主窗口的样式表。
    """

    PAGE_RESULTS = 0
    PAGE_DETAIL = 1
    PAGE_ERROR = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mc_dir = config.get_minecraft_dir()

        # ---------- 后台任务的状态字段（线程只写、主线程读）----------
        self._search_result = None
        self._detail_result = None
        self._icon_lock = threading.Lock()
        self._icon_queue = []           # [(url, bytes)] 主线程来收
        self._icon_asked = set()        # 已经发过请求的 url（去重）
        # 还在路上的图标请求数（`_is_busy()` 靠它把轮询留到图标回来为止）
        self._icon_pending = 0

        self._query = ""
        self._offset = 0
        self._total = 0
        self._loading = False
        self._current_hit = {}
        self._current_project = {}
        self._groups = []
        self._cards = []
        # 入场动画：结果列表和版本列表**各一个**调度器（互不打扰，见 _stagger_for）
        self._staggers = {}
        self._row_hosts = {"results": [], "versions": []}
        # 详情页的批次状态：已经铺了几组、末尾那个「加载更多分组」按钮
        self._shown_groups = 0
        self._more_groups_btn = None
        # 正在往布局里插分组 —— 这期间要挡住滚动回调，否则会被自己触发、
        # 一次把所有批次铺完（见 _load_more_groups 的说明）
        self._populating = False
        # 连续自动补了几批（见 _check_short_content）
        self._auto_fills = 0
        # 这一轮搜索用的筛选条件（详情页要拿它把匹配的版本分组置顶）
        self._prefs = {}
        # 当前搜的是哪一类（标签栏切；决定 facets 和下载落到哪个子目录）
        self._ptype = DEFAULT_PROJECT_TYPE
        self._install_win = None
        self._install_manager = None
        self._install_done = False
        self._install_note = ""
        # 整合包安装的状态（线程只写这个 dict，主线程轮询来读）
        self._mp_state = {"stage": "idle", "msg": "", "error": "", "path": None}
        # 整合包安装用的进度窗口 + 引擎（装完在 _poll_modpack 里收掉）
        self._mp_win = None
        self._mp_manager = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._make_results_page())
        self.stack.addWidget(self._make_detail_page())
        self.stack.addWidget(self._make_error_page())
        root.addWidget(self.stack)

        # 轮询定时器：搜索 / 详情 / 图标 / 下载收尾
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

        self._fill_combos()
        self._fill_type_tabs()
        self.stack.setCurrentIndex(self.PAGE_RESULTS)

    # ---------- 类型 ----------

    def _fill_type_tabs(self):
        """同步"当前在搜哪一类"的文字

        ⚠️ 名字里的 tabs 是历史叫法：**顶部那排类型标签已经去掉了**
        （用户 2026-09 要的 —— 左栏「社区资源」下面就是模组 / 整合包 / 数据包 /
        资源包 / 光影包，顶上再摆一排一模一样的属于重复；见 `_make_filter_panel`
        里那段说明）。现在这里只干两件事：把面板标题和搜索按钮的文字换成当前
        类型（**这很重要**：标签没了之后，用户得能从标题看出自己在搜哪一类），
        以及给还存在的按钮同步选中态（以后要加回标签栏就还用得上）。
        """
        for value, text in MOD_TYPES:
            btn = self._type_buttons.get(value)
            if btn is None:
                continue
            btn.setText(tr(text))
            # ⚠️ 选中态靠 `:checked`（QSS 里写 [checked]），不是 setStyleSheet
            btn.setChecked(value == self._ptype)
        label = project_type_label(self._ptype)
        self.panel_title.setText(tr("搜索{kind}", kind=label))
        self.search_btn.setText(tr("搜索{kind}", kind=label))

    def current_project_type(self) -> str:
        """当前搜的是哪一类（外面切分类时先问一下，避免重复触发重搜）"""
        return self._ptype

    def set_project_type(self, ptype: str):
        """切一类（点标签栏 / 左栏换栏都走这里）→ **直接重搜**

        切了却什么都不发生会让人以为坏了（实验项目那版的做法）。
        加载器筛选只对模组 / 整合包有意义（资源包和光影包没有加载器概念），
        所以切到别的类型时把它禁掉 —— 不禁的话用户筛了也没用（facets 会忽略它）。
        """
        ptype = (ptype or DEFAULT_PROJECT_TYPE).lower()
        if ptype == self._ptype:
            self._fill_type_tabs()
            return
        self._ptype = ptype
        self.loader_combo.setEnabled(ptype in LOADER_TYPES)
        self._fill_type_tabs()
        self.do_search()

    # ============================================================
    # 页面骨架
    # ============================================================

    def _make_results_page(self) -> QWidget:
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        panel = QFrame()
        panel.setObjectName("ModFilterPanel")
        pbox = QVBoxLayout(panel)
        pbox.setContentsMargins(14, 12, 14, 12)
        pbox.setSpacing(10)

        # ⚠️ 标题的引用要留着：切类型 / 换语言时它得跟着改（`搜索光影包`）
        self.panel_title = self.label("搜索模组 / 光影 / 资源包…", "ModPanelTitle")
        pbox.addWidget(self.panel_title)

        # ---- 类型标签栏：**去掉了**（用户 2026-09）----
        # 左栏「社区资源」下面本来就是模组 / 整合包 / 数据包 / 资源包 / 光影包，
        # 顶上再摆一排同样的东西是重复的入口 —— 点哪边都一样，还会让人以为
        # 是两个不同的筛选维度。
        # 左栏切栏走的是 `download_page` → `set_project_type()`（同一个函数），
        # 所以功能一点没少；「现在搜的是哪一类」由上面的面板标题和搜索按钮
        # 显示（`_fill_type_tabs` 里跟着类型改）。
        # `self._type_buttons` 仍是空字典，`_fill_type_tabs()` 会跳过 —— 留着
        # 这个结构是为了万一以后要加回标签栏时不用再改一遍调用点。
        self._type_buttons = {}

        self.search_input = QLineEdit()
        self.bind(self.search_input, "输入关键词，或留空看最热门的", "placeholderText")
        self.search_input.returnPressed.connect(self.do_search)
        pbox.addWidget(self.search_input)

        row = QHBoxLayout()
        row.setSpacing(8)
        # (attr, 标题, 值列表, 是否可翻译的值)
        self.mc_combo = self._make_combo(row, "游戏版本", ANY, MC_VERSIONS, False)
        self.loader_combo = self._make_combo(
            row, "加载器", ANY, [k for k, _ in LOADERS], False, labels=LOADERS)
        self.cat_combo = self._make_combo(
            row, "分类", ANY, [k for k, _ in CATEGORIES], True, labels=CATEGORIES)
        self.sort_combo = self._make_combo(
            row, "排序", api.DEFAULT_SORT, [k for k, _ in SORTS], True,
            labels=SORTS)
        row.addStretch()
        pbox.addLayout(row)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.search_btn = self.button("搜索", "PrimaryButton")
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.clicked.connect(self.do_search)
        btns.addWidget(self.search_btn)

        self.reset_btn = self.button("重置")
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.clicked.connect(self.do_reset)
        btns.addWidget(self.reset_btn)

        btns.addStretch()
        self.status = QLabel()
        self.status.setObjectName("HintText")
        btns.addWidget(self.status)
        pbox.addLayout(btns)

        box.addWidget(panel)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("ModVersionList")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        host.setObjectName("ModResultsHost")
        self.results_box = QVBoxLayout(host)
        self.results_box.setContentsMargins(0, 0, 8, 0)
        self.results_box.setSpacing(8)
        # ⚠️ 末尾常驻撑开项：内容不满一屏时，没有它每行会被拉高
        # （见实验项目使用说明第 26 条）
        self.results_box.addStretch()
        self.scroll.setWidget(host)
        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_load_more)
        box.addWidget(self.scroll, 1)

        # 加载条（主项目已有，QSS 化过的那个）
        from ui.widgets.loading_bar import LoadingBar
        self.loading = LoadingBar()
        box.addWidget(self.loading)
        return holder

    def _make_detail_page(self) -> QWidget:
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        top = QHBoxLayout()
        self.back_btn = self.button("← 返回")
        self.back_btn.setObjectName("LinkButton")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.clicked.connect(self._back_to_results)
        top.addWidget(self.back_btn)
        top.addStretch()
        box.addLayout(top)

        # ---------- 信息卡（固定不滚）----------
        card = QFrame()
        card.setObjectName("ModDetailCard")
        cbox = QHBoxLayout(card)
        cbox.setContentsMargins(16, 16, 16, 16)
        cbox.setSpacing(16)

        self.detail_icon = QLabel("?")
        self.detail_icon.setObjectName("ModDetailIcon")
        self.detail_icon.setFixedSize(96, 96)
        self.detail_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cbox.addWidget(self.detail_icon, 0, Qt.AlignmentFlag.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(6)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self.detail_name = QLabel()
        self.detail_name.setObjectName("ModDetailName")
        name_row.addWidget(self.detail_name)
        self.detail_slug = QLabel()
        self.detail_slug.setObjectName("ModDetailSlug")
        name_row.addWidget(self.detail_slug)
        name_row.addStretch()
        right.addLayout(name_row)

        self.detail_meta = QLabel()
        self.detail_meta.setObjectName("ModDetailMeta")
        right.addWidget(self.detail_meta)

        self.detail_desc = QLabel()
        self.detail_desc.setObjectName("ModDetailDesc")
        self.detail_desc.setWordWrap(True)
        right.addWidget(self.detail_desc)

        self.detail_stats = QLabel()
        self.detail_stats.setObjectName("ModDetailStats")
        right.addWidget(self.detail_stats)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.modrinth_btn = self.button("在 Modrinth 打开")
        self.modrinth_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.modrinth_btn.clicked.connect(self._open_modrinth)
        btns.addWidget(self.modrinth_btn)

        self.mcmod_btn = self.button("在 MC 百科搜索")
        self.mcmod_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mcmod_btn.clicked.connect(self._open_mcmod)
        btns.addWidget(self.mcmod_btn)
        btns.addStretch()
        right.addLayout(btns)

        cbox.addLayout(right, 1)
        box.addWidget(card)

        # ---------- 版本列表（只有它滚）----------
        self.version_scroll = QScrollArea()
        self.version_scroll.setObjectName("ModVersionList")
        self.version_scroll.setWidgetResizable(True)
        self.version_scroll.setFrameShape(QFrame.Shape.NoFrame)
        vhost = QWidget()
        vhost.setObjectName("ModVersionHost")
        self.version_box = QVBoxLayout(vhost)
        self.version_box.setContentsMargins(0, 0, 8, 0)
        self.version_box.setSpacing(8)
        self.version_box.addStretch()
        self.version_scroll.setWidget(vhost)
        box.addWidget(self.version_scroll, 1)

        # 滚到详情页底部 → 铺下一批版本分组（Fabric API 有 400 多个分组）
        self.version_scroll.verticalScrollBar().valueChanged.connect(
            self._maybe_load_more_groups)
        # "内容够不够一屏"必须等布局稳定了再看：rangeChanged 在布局期会连发很多次，
        # 而且那时候视口高度还没定，当场判断会把"一屏放得下"错判成"不够一屏"
        self._short_timer = QTimer(self)
        self._short_timer.setSingleShot(True)
        self._short_timer.setInterval(LAYOUT_SETTLE_MS)
        self._short_timer.timeout.connect(self._check_short_content)
        # 铺设结束后"放行"用的定时器（归零触发，见 _load_more_groups 的说明）
        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.timeout.connect(self._release_populating)

        # 详情页的加载条。
        # ⚠️ 搜索结果页和详情页**各有一个**：它们在不同 stack 页里，
        # 共用一个的话切页时会跟着跑过去。
        # ⚠️ 最短显示时间给到 550ms（搜索页是默认 180ms）：点进详情时用户是
        # "在等一个页面"，请求命中缓存时几十毫秒就回来了，不兜一下这条会一闪而过
        # 等于没有（实验项目那版也是这么调的值）。
        from ui.widgets.loading_bar import LoadingBar
        self.detail_loading = LoadingBar(min_visible_ms=DETAIL_MIN_VISIBLE_MS)
        box.addWidget(self.detail_loading)

        self.pick_hint = QLabel()
        self.pick_hint.setObjectName("ModPickHint")
        self.pick_hint.setWordWrap(True)
        box.addWidget(self.pick_hint)
        return holder

    def _make_error_page(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(28, 40, 28, 40)
        box.setSpacing(12)
        box.addStretch()

        mark = self.label("！", "NoticeIcon")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(mark)

        title = self.label("网络环境不佳", "NoticeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(title)

        msg = self.label(
            "拿不到模组数据。请稍后再试，或者换个网络（必要时用代理 / VPN）。",
            "HintText")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        box.addWidget(msg)

        self.error_detail = QLabel()
        self.error_detail.setObjectName("HintText")
        self.error_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_detail.setWordWrap(True)
        box.addWidget(self.error_detail)

        row = QHBoxLayout()
        row.addStretch()
        self.retry_btn = self.button("重试", "PrimaryButton")
        self.retry_btn.clicked.connect(self._retry)
        row.addWidget(self.retry_btn)
        row.addStretch()
        box.addLayout(row)
        box.addStretch()
        return card

    def _make_combo(self, row, title: str, default: str, values, translate_values: bool,
                    labels=()) -> QComboBox:
        """一个 "标题: [下拉]" 组合（下拉项的文字要能跟着语言走）"""
        row.addWidget(self.label(title + "："))
        combo = QComboBox()
        combo.setMinimumWidth(110)
        combo.setProperty("values", list(values))
        combo.setProperty("translate_values", bool(translate_values))
        combo.setProperty("labels", [(k, v) for k, v in labels])
        row.addWidget(combo)
        self._fill_combo(combo, default)
        return combo

    @staticmethod
    def _fill_combo(combo: QComboBox, default: str):
        """按 property 里存的原始值重建下拉项（语言切换时也要重来一遍）

        ⚠️ "全部"这一项要**在这里**过 tr()，不能在模块级求值 ——
        那样切语言不会更新（见 ANY 的说明）。
        """
        values = combo.property("values") or []
        translate_values = bool(combo.property("translate_values"))
        labels = dict(combo.property("labels") or [])

        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr(ANY), ANY)
        for value in values:
            text = labels.get(value) or value
            combo.addItem(tr(text) if translate_values else text, value)
        index = combo.findData(current if current is not None else default)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _fill_combos(self):
        for combo, default in ((self.mc_combo, ANY), (self.loader_combo, ANY),
                               (self.cat_combo, ANY),
                               (self.sort_combo, api.DEFAULT_SORT)):
            self._fill_combo(combo, default)

    # ============================================================
    # 搜索
    # ============================================================

    def do_search(self):
        """开一轮新搜索（按钮 / 回车 / 重置都走这里）"""
        self._clear_results()
        self._query = self.search_input.text().strip()
        self._offset = 0
        self._total = 0
        # 把这一轮的筛选条件记下来 —— 点进详情时要用它把匹配的版本分组置顶
        # （用户选了 1.21.5 + Fabric，进去第一眼就该看到那一组）
        self._prefs = {
            "mc": self.mc_combo.currentData() or ANY,
            "loader": self.loader_combo.currentData() or ANY,
        }
        self.stack.setCurrentIndex(self.PAGE_RESULTS)
        self.status.setText(tr("正在搜索…"))
        self.loading.show_state(tr("正在搜索「{q}」…", q=self._query)
                                if self._query else tr("正在加载推荐…"))
        self.search_btn.setEnabled(False)
        self._request(self._offset)

    def _request(self, offset: int, append: bool = False):
        """取一页搜索结果（普通线程只写结果，主线程轮询来收）"""
        if self._loading:
            return
        self._loading = True
        self._append = append
        self._search_result = None

        params = dict(
            query=self._query,
            mc_version=self.mc_combo.currentData() or ANY,
            loader=self.loader_combo.currentData() or ANY,
            category=self.cat_combo.currentData() or ANY,
            project_type=self._ptype,
            sort=self.sort_combo.currentData() or api.DEFAULT_SORT,
            limit=PAGE_SIZE,
            offset=offset,
        )

        def work():
            try:
                self._search_result = ("ok", api.search_projects(**params))
            except Exception as e:                      # noqa: BLE001
                self._search_result = ("err", e)

        threading.Thread(target=work, daemon=True).start()
        self._timer.start()

    def _maybe_load_more(self, *_):
        """滚动条动了：**拖到底**才接着抓"""
        if self._loading or self._offset >= self._total:
            return
        bar = self.scroll.verticalScrollBar()
        if bar.value() >= bar.maximum() - LOAD_MORE_THRESHOLD:
            # 给个"在加载"的反馈 —— 不显示的话用户滚到底之后那几百毫秒
            # 界面完全没动静，看着就像"滚到底什么都没发生"
            self.loading.show_state(tr("正在加载更多…"))
            self._request(self._offset, append=True)

    def _clear_results(self):
        while self.results_box.count():
            item = self.results_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        # ⚠️ 撑开项每次清空后都要补回来（spacer 没有 widget()，takeAt 已经把它
        # 从布局里摘掉了；少了它内容不满一屏时高度会被平摊给每一行）
        self.results_box.addStretch()
        self._clear_stagger("results")
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards = []

    def _anim_enabled(self) -> bool:
        """动效总开关（"关闭动效省性能"）。关掉时列表**不包那层动画控件**

        为什么要单独一个方法而不是直接读设置：这里被问得很频繁（每张卡片一次），
        以后要缓存也只需要改这一处。读的是 `core/config` 里的内存字典，不落盘。
        """
        from core import appearance
        return appearance.get_anim_enabled()

    def _stagger_for(self, kind: str):
        """结果 / 版本列表的入场动画调度器（懒建，每次读一遍设置）

        ⚠️ 两个列表**各用一个**：调度器内部是"队列 + 一个定时器"，
        共用一个的话，正在铺结果时又去铺版本会互相插队（而且 clear 一个会把
        另一个也清掉）。
        """
        st = self._staggers.get(kind)
        if st is not None:
            return st
        from core import anim_prefs, appearance
        from ui.widgets.slide_in import StaggerReveal

        try:
            base = anim_prefs.preset_for_versions(appearance.get_preset_key())
            preset = anim_prefs.scaled(
                base, anim_prefs.speed_factor(appearance.get_speed_key()))
        except Exception:                                   # noqa: BLE001
            preset = anim_prefs.preset_for_versions(anim_prefs.DEFAULT_PRESET)
        st = StaggerReveal(
            self, interval=preset.interval, initial_offset=preset.offset,
            direction=preset.direction, style=preset.style,
            duration=preset.duration, overshoot=preset.overshoot,
            bounce_ratio=preset.bounce_ratio, ease=preset.ease)
        self._staggers[kind] = st
        return st

    def _start_stagger(self, kind: str):
        """一批铺完之后点名开播

        ⚠️ 必须收尾调一次：`StaggerReveal` 一次只放一张，靠 `start()` 点第一张、
        之后由自己的定时器往下走。漏了这句的话整批卡片就**一张都不出现**
        （实验项目使用说明第 12 条就是踩这个）。
        """
        st = self._staggers.get(kind)
        if st is not None:
            st.start()

    def _clear_stagger(self, kind: str):
        """叫停并清空（换搜索词 / 离开详情页时调）"""
        st = self._staggers.get(kind)
        if st is not None:
            st.clear()
        for host in self._row_hosts.get(kind, []):
            try:
                host.stop()
            except RuntimeError:            # 控件已经没了
                pass
        self._row_hosts[kind] = []

    def _add_result_widget(self, w, animate: bool = True):
        """插到末尾撑开项**前面**，并确保真的显示出来

        不能 `addWidget`：布局末尾常驻一个撑开项，`addWidget` 会把它追加到
        撑开项**后面**（见实验项目使用说明第 26 条）。

        ⚠️ `setVisible(True)` 的理由跟 `_add_version_widget` 完全一样：
        页面已经显示时新建的控件默认是隐藏的。

        `animate=True` 时把控件包进 `SlideInRow`，由页面级 `_stagger` 依次点名
        滑入（那套调度器一次只放一张，所以**必须**在批次结束后 `_start_stagger()`）。
        """
        host = w
        if animate and self._anim_enabled():
            host = self._stagger_for("results").add(w)
            self._row_hosts["results"].append(host)
        self.results_box.insertWidget(self.results_box.count() - 1, host)
        host.setVisible(True)
        return host

    # ============================================================
    # 详情
    # ============================================================

    def open_mod(self, hit: dict):
        """点搜索结果 → 详情页

        先用搜索结果把卡片填上（详情接口要 1~3 秒，不预填的话滑进来时
        还挂着上一个 mod 的内容），再后台取完整详情 + 版本列表。
        """
        if not hit:
            return
        self._current_hit = hit
        slug = hit.get("slug") or hit.get("project_id") or hit.get("title") or ""
        self._fill_detail(hit, partial=True)
        self._clear_versions()
        # ⚠️ **先切页、再显示加载条**。反过来写的话，setVisible(True) 会作用在
        # 一个还没被 QStackedWidget 显示出来的页面上 —— Qt 会把"可见"记下来、
        # 等父控件显示时再兑现，功能上没错（实测两种顺序最终都能显示），
        # 但依赖这种隐式行为没必要，顺序写对了一眼能看懂。
        self.stack.setCurrentIndex(self.PAGE_DETAIL)
        # 详情要连打"项目"和"版本"两个接口，等待感比搜索更明显 ——
        # 必须给个东西告诉用户"在跑"（不然就是一页空白）
        self.detail_loading.show_state(tr("正在加载项目详情…"))
        self.pick_hint.setText(tr("正在获取版本列表…"))
        self._detail_result = None
        self._loading = True

        def work():
            try:
                project = api.get_project(slug) or {}
                versions = api.get_project_versions(project.get("slug") or slug)
                self._detail_result = ("ok", project, versions)
            except Exception as e:                      # noqa: BLE001
                self._detail_result = ("err", e, None)

        threading.Thread(target=work, daemon=True).start()
        self._timer.start()

    def _fill_detail(self, data: dict, partial: bool = False):
        """把项目数据铺到信息卡上（partial=True 时缺的字段留空）"""
        self._current_project = data
        self.detail_name.setText(str(data.get("title") or ""))
        self.detail_slug.setText(("|  %s" % data.get("slug")) if data.get("slug") else "")
        self.detail_desc.setText(str(data.get("description") or ""))

        ptype = project_type_label(data.get("project_type"))
        loaders = [LOADER_LABELS.get(l, l)
                   for l in (data.get("loaders") or [])]
        cats = [c for c in (data.get("categories") or [])
                if c not in LOADER_LABELS]
        bits = [ptype]
        if loaders:
            bits.append(" / ".join(loaders))
        if cats:
            bits.append(", ".join(cats))
        self.detail_meta.setText("    ".join(bits))

        self.detail_stats.setText(
            "%s    %s" % (
                tr("下载 {n}", n=format_number(data.get("downloads"))),
                tr("上次更新：{date}", date=format_date(
                    data.get("updated") or data.get("date_modified"))),
            ))

        # 图标（partial 时用搜索结果的 icon_url，全量时用详情接口的）
        self._load_icon(str(data.get("icon_url") or ""), self.detail_icon, 96)

    def _clear_versions(self):
        self._clear_stagger("versions")
        while self.version_box.count():
            item = self.version_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.version_box.addStretch()
        self._groups = []
        self._shown_groups = 0
        self._more_groups_btn = None
        self._auto_fills = 0

    def _fill_versions(self, versions: list):
        """把版本列表铺出来（**折叠分组 + 分批**）

        · 按「游戏版本 + 加载器 + 是否预览版」分组，新的在前、正式版优先
        · **用户在搜索页选的筛选条件会置顶**（选了 1.21.5 + Fabric，
          点进来第一眼就是那一组）—— 见 `group_versions(prefer_...)`
        · 一次只铺 `GROUP_BATCH` 组，滚到底再铺下一批（Fabric API 有 400+ 组）
        """
        self._clear_versions()
        prefs = self._prefs or {}
        self._groups = group_versions(
            versions, prefs.get("mc", ""), prefs.get("loader", ""))
        if not self._groups:
            hint = self.label("这个项目没有可下载的版本", "HintText")
            self._add_version_widget(hint)
            self.pick_hint.setText(tr("没有可用版本"))
            return

        # ⚠️ 对得上筛选的分组**一进来就展开**：用户刚在搜索页筛完 1.21.5 + Fabric，
        # 点进来还要自己再点一下展开，等于白筛了。
        self._load_more_groups()
        self._refresh_pick_hint()

    def _load_more_groups(self):
        """再铺一批分组（对得上筛选的那些始终都在第一批里）

        ⚠️ `_populating` 那一小段是必须的：往布局里插控件会让
        `QScrollArea` 的滚动条**反复发 valueChanged**，而每批铺完内容都还
        撑不满一屏（`value() >= maximum() - 阈值` 恒成立）→ 于是"铺一批、
        又被自己触发铺下一批"，三批一口气全铺完，末尾那个
        「加载更多分组」按钮刚建出来就被撤掉（用户根本点不到）。
        铺设期间先挡住滚动回调。

        ⚠️ 这个标志**不用定时器去释放**：`QTimer.singleShot(0)` / `start(0)`
        在某些运行方式下不会按预期跑到（离屏测试里实测就跑了 0 次），一旦没跑到
        它就**永久停在 True** —— 自动补和滚动加载全被挡住，表现成"首屏只有
        一批、之后再也加载不动"。
        现在的做法是：**铺设一结束就同步清掉**，而"会不会被自己触发"靠
        进入时先置 True 挡住 —— 进入时必然是上一批已经清干净的状态，
        所以既不丢保护、也不可能死锁。
        """
        if self._shown_groups >= len(self._groups):
            self._populating = False
            return
        self._populating = True
        end = min(len(self._groups), self._shown_groups + GROUP_BATCH)
        batch = self._groups[self._shown_groups:end]

        # 「加载更多分组」按钮常驻在末尾（撑开项前面），有剩的才显示
        if self._more_groups_btn is not None:
            self._more_groups_btn.setParent(None)
            self._more_groups_btn.deleteLater()
            self._more_groups_btn = None
        for group in batch:
            widget = ModVersionGroup(
                group, on_download=self._on_download,
                expanded=bool(group.get("matches_filter")))
            self._add_version_widget(widget)
            self._shown_groups += 1

        if self._shown_groups < len(self._groups):
            left = len(self._groups) - self._shown_groups
            btn = QPushButton(tr("加载更多分组（还有 {n} 个）", n=left))
            btn.setObjectName("LinkButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(self._load_more_groups)
            self._add_version_widget(btn)
            self._more_groups_btn = btn

        self._populating = False
        # 这批铺完 → 点名开播入场动画（见 _start_stagger 的说明：漏了会整批不出现）
        self._start_stagger("versions")
        # 结算之后再判一次"够不够一屏"（见 _check_short_content）
        self._schedule_short_check()

    def _release_populating(self):
        self._populating = False

    def _add_version_widget(self, w, animate: bool = True):
        """插到版本列表末尾撑开项**前面**，并确保它真的显示出来

        ⚠️ 那句 `setVisible(True)` 不是多余的。Qt 有个很反直觉的规矩：
        **父页面已经显示出来时**，往它的布局里新加的控件会被标成
        "未显式显示"→ 保持隐藏（`isHidden()` 为 True，父链看着全正常，
        但它在屏幕上就是不出现、也点不到）。反过来，父页面还是隐藏的时候
        加进去反而没事。
        实测：可见页里新建 = hidden；隐藏页里新建 = 正常。
        详情页从 QStackedWidget 里被切出来之后就是"已显示"，所以每一批
        分组、那个「加载更多分组」按钮、以及"没有版本"的提示行**都会中招** ——
        表现成"点进来只有一页空白"。
        统一在这一个出口上显式显示，比在每个调用点各写一遍可靠。

        `animate=True` 时包一层 `SlideInRow` 滑入（见 `_add_result_widget`）。
        动效总开关关掉时不包 —— 见 `_anim_enabled`。
        """
        host = w
        if animate and self._anim_enabled():
            host = self._stagger_for("versions").add(w)
            self._row_hosts["versions"].append(host)
        self.version_box.insertWidget(self.version_box.count() - 1, host)
        host.setVisible(True)
        return host

    def _refresh_pick_hint(self):
        """底部提示：会装到哪（版本隔离算出来的目录）"""
        mc = mcd.mc_dir_from_config()
        ptype = (self._current_project or self._current_hit).get("project_type", "mod")
        sub = mcd.subfolder_for(ptype)
        if not mc:
            self.pick_hint.setText(tr("还没设置游戏目录 —— 去设置页选到 .minecraft 那一层"))
            return
        prefs = self._prefs or {}
        if _real_any(prefs.get("mc")) or _real_any(prefs.get("loader")):
            # 说明"为什么这几个分组在最前面"，不然用户会以为列表顺序是随机的
            self.pick_hint.setText(
                tr("共 {n} 个版本分组，已按你筛选的 {version} + {loader} 置顶。下载会落到：{path}",
                   n=len(self._groups), version=_real_any(prefs.get("mc")) or tr("全部"),
                   loader=_real_any(prefs.get("loader")) or tr("全部"),
                   path=Path_text(mc, sub)))
            return
        self.pick_hint.setText(
            tr("共 {n} 个版本分组。下载会落到：{path}",
               n=len(self._groups), path=Path_text(mc, sub)))

    def _maybe_load_more_groups(self, *_):
        """滚到详情页底部 → 铺下一批分组

        ⚠️ `_populating` 期间**必须直接返回**：插控件会让滚动条反复发信号，
        不挡的话会自动把所有批次铺完（见 `_load_more_groups` 的说明）。
        """
        if self._populating or self._shown_groups >= len(self._groups):
            return
        bar = self.version_scroll.verticalScrollBar()
        if bar.value() >= bar.maximum() - LOAD_MORE_THRESHOLD:
            # 用户自己滚到底 = 明确想看更多，允许后面重新自动补
            self._auto_fills = 0
            self._load_more_groups()

    def _schedule_short_check(self):
        """排一次"内容够不够一屏"的检查（等布局稳定）"""
        self._short_timer.start()

    def _check_short_content(self):
        """布局稳定后：内容真的不够一屏吗？不够就再补一批

        ⚠️ 这一段是**滚动加载的必要补充**，不写就有个死局：
        一批只有 `GROUP_BATCH` 个分组，而折叠状态下每个标题行才 30 来像素 ——
        窗口一大，8 行根本撑不满一屏，**滚动条没有可滚范围**
        （`maximum == 0`），于是 `valueChanged` 永远不触发，
        "滚到底自动加载"永远等不到，用户只能点那个「加载更多分组」按钮。

        补一批之后如果还撑不满就再补（最多 `AUTO_FILL_MAX` 次），
        然后才把「加载更多分组」按钮露出来兜底。
        """
        if self._populating or self._shown_groups >= len(self._groups):
            return
        bar = self.version_scroll.verticalScrollBar()
        # 有可滚范围就交给滚动加载（用视口高兜底：布局刚结算时
        # max/height 可能还是 0，只信 maximum 会误判成"不够一屏"）
        viewport_h = max(bar.height(), self.version_scroll.viewport().height())
        if bar.maximum() > 0 and bar.maximum() > viewport_h * 0.1:
            return                      # 能滚了，交给滚动加载
        if self._auto_fills >= AUTO_FILL_MAX:
            return                      # 补够了，让按钮兜底（它一直可见）
        self._auto_fills += 1
        self._load_more_groups()

    def resizeEvent(self, event):
        """窗口变大后重新判一次"要不要补"

        变宽/变高之后一屏能放下更多，可能又变成"内容不满一屏、滚不动"的死局；
        顺便把自动补的次数归零，允许它再补一次。
        """
        super().resizeEvent(event)
        try:
            if self.stack.currentIndex() == self.PAGE_DETAIL:
                self._auto_fills = 0
                self._schedule_short_check()
        except (AttributeError, RuntimeError):
            pass        # 构造期间控件还没建齐

    def _back_to_results(self):
        # 返回时把详情页的加载条收掉：用户可能在加载途中就返回了，
        # 不收的话下次进来它还挂着（看着像"一进来就在加载"）
        self.detail_loading.hide_now()
        self.stack.setCurrentIndex(self.PAGE_RESULTS)

    # ============================================================
    # 下载
    # ============================================================

    def _on_download(self, version: dict, game_version: str, loader: str):
        """点「下载」

        整合包走**另一条路**（`_start_modpack_install`）：它不是一个文件，
        而是"下 .mrpack → 解析 → 下里面几十~几百个文件 → 解压 overrides"。
        普通模组/资源包还是老样子（一个文件塞进引擎）。

        ⚠️ **不自己写下载**：任务塞给 `DownloadManager`，进度窗口用现有的
        `DownloadWindow`。镜像回退、失败重试、sha1 跳过、取消、原子改名全是白拿的。
        """
        ptype = (self._current_project or {}).get("project_type") \
            or (self._current_hit or {}).get("project_type") or "mod"
        if ptype == "modpack":
            self._start_modpack_install(version)
            return

        f = version_file(version)
        if not f or not f.get("url"):
            self.pick_hint.setText(tr("这个版本没有可下载的文件"))
            return

        mc = mcd.mc_dir_from_config()
        target, why = mcd.target_dir(mc, game_version, loader, ptype)
        if target is None:
            self.pick_hint.setText(
                tr("还没设置游戏目录。去设置页把「游戏目录」选到 .minecraft 那一层。"))
            return

        # ⚠️ 建目录失败要**中止**：不中止的话下载线程会在不存在的目录里写 .tmp，
        # 最后报一个和真实原因八竿子打不着的错（实验项目第 25 条）
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.pick_hint.setText(tr("建不了目录：{path}（{err}）",
                                      path=target, err=e))
            return

        filename = str(f.get("filename") or "").strip() \
            or str(f["url"]).rsplit("/", 1)[-1]
        if not filename:
            self.pick_hint.setText(tr("这个版本的文件没有名字，没法下载"))
            return

        manager = DownloadManager(max_workers=effective_threads())
        # ⚠️ parent 一定要给（铁律 1）：不给的话这个顶层窗口拿不到主窗口的样式表
        from ui.dialogs.download_window import DownloadWindow
        win = DownloadWindow(
            manager, title=tr("正在下载 {name}", name=filename), parent=self)
        win.show()
        self._install_win = win
        self._install_manager = manager
        self._install_done = False

        manager.add(str(f["url"]), target / filename, sha1=str(f.get("sha1") or ""),
                    size=int(f.get("size") or 0), label=filename,
                    kind=tr("模组"))
        manager.start()
        self.pick_hint.setText(tr("已开始下载：{path}（{why}）", path=target, why=why))

        def work():
            manager.wait_all(timeout=3600)
            snap = manager.snapshot()
            if snap["failed"]:
                self._install_note = tr("下载结束，但有 {n} 个文件失败",
                                        n=snap["failed"])
            elif snap["count"] == 0:
                self._install_note = tr("没有可下载的文件")
            else:
                self._install_note = tr("下载完成：{name}", name=filename)
            self._install_done = True

        threading.Thread(target=work, daemon=True).start()
        self._timer.start()

    # ============================================================
    # 整合包安装（多步流程）
    # ============================================================
    #
    # 跟"下一个文件"不一样，整合包是**一串步骤**：
    #
    #     下 .mrpack → 解析 → 下里面几十~几百个文件 → 解压 overrides → 写版本 JSON
    #
    # 所以状态用一个 dict 在后台线程里推进，主线程的 `_poll_modpack()` 来读
    # （铁律 6：后台线程只写结果，主线程定时器去收，绝不在子线程碰控件）。

    def _start_modpack_install(self, version: dict):
        """开始装一个整合包（点详情页的「下载」触发）"""
        f = version_file(version)
        if not f or not f.get("url"):
            self.pick_hint.setText(tr("这个整合包没有可下载的文件"))
            return
        if self._mp_state.get("stage") == "working":
            self.pick_hint.setText(tr("已经在装一个整合包了，等它装完"))
            return

        mc = mcd.mc_dir_from_config()
        if not mc:
            self.pick_hint.setText(
                tr("还没设置游戏目录。去设置页把「游戏目录」选到 .minecraft 那一层。"))
            return

        project = self._current_project or self._current_hit or {}
        name = str(project.get("title") or "").strip() or str(version.get("name") or "")
        inst_name = mpk.sanitize_instance_name(name)
        # **版本隔离**：一个整合包一个实例目录（`versions/<名字>/`），
        # 这样它跟别的版本互不干扰，而且 `core/versions.py` 扫到 `mods/`
        # 特征时会认出这是隔离版本（game_dir 就是这个目录）。
        instance_dir = Path(mc) / "versions" / inst_name

        try:
            instance_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.pick_hint.setText(tr("建不了目录：{path}（{err}）",
                                      path=instance_dir, err=e))
            return

        pack_url = str(f["url"])
        pack_size = int(f.get("size") or 0)
        pack_sha1 = str(f.get("sha1") or "")
        ptype = str(project.get("project_type") or "modpack")
        # 临时文件放在实例目录里（点开头，`versions.py` 不会认成版本 json），
        # 装完就删 —— 不用去猜系统临时目录在哪。
        tmp_pack = instance_dir / (".modpack-%s.mrpack" % inst_name[:24])

        self.pick_hint.setText(tr("整合包要装到：{path}", path=instance_dir))
        self._mp_state = {"stage": "working", "msg": tr("正在下载整合包"),
                          "error": "", "path": str(instance_dir)}

        # ⚠️ **必须摆出进度窗口**。第一版漏了这一步：只造了管理器、没造窗口，
        # 于是文件确实在下、界面上却一点动静都没有（用户 2026-09 报"没显示出
        # 下载框"）。而且没有窗口就没法看进度、没法取消、失败也没法重试。
        # ⚠️ parent 一定要给（铁律 1）：不给的话这个顶层窗口拿不到主窗口的样式表。
        manager = DownloadManager(max_workers=effective_threads())
        from ui.dialogs.download_window import DownloadWindow
        win = DownloadWindow(manager, title=tr("正在安装整合包 {name}", name=inst_name),
                             parent=self)
        win.show()
        self._mp_win = win
        self._mp_manager = manager

        threading.Thread(
            target=self._modpack_worker,
            args=(pack_url, instance_dir, tmp_pack, inst_name, version,
                  pack_size, pack_sha1, ptype, manager, Path(mc),
                  str(project.get("icon_url") or ""), win),
            daemon=True).start()
        self._timer.start()

    def _modpack_worker(self, pack_url, instance_dir, tmp_pack, inst_name,
                        version, pack_size, pack_sha1, ptype, mgr, mc_dir,
                        icon_url="", win=None):
        """后台线程：下 .mrpack → 解析 → 下所有文件 → 解压 overrides
        → **装它要的原版 + 加载器** → 写版本 JSON

        ⚠️ 这里**只写 `self._mp_state`**，不碰任何控件。
        `mgr` 由调用方建好（连同进度窗口一起），这里只管往里塞任务。

        最后那一步是照 PCL 补的（`ModModpack.cs` 的 `InstallPackModrinth`：
        包文件装完之后还有一道 `McInstallLoader`）。**只装我们装得了的加载器**
        （Fabric / Quilt，见 `core/loader_install.py`）；Forge 那几家会明说
        "加载器还没做"，那时实例里只有整合包的文件、不能直接启动。
        """
        state = self._mp_state
        try:
            # ---- 第 1 步：把 .mrpack 下下来（用引擎，白拿重试/取消/校验）----
            mgr.add(pack_url, tmp_pack, sha1=pack_sha1,
                    size=pack_size, label=tmp_pack.name,
                    kind=tr("整合包"))
            mgr.start()
            mgr.wait_all(timeout=3600)
            snap = mgr.snapshot()
            if snap["failed"] or not Path(tmp_pack).is_file():
                state.update(stage="error", msg="",
                             error=tr("整合包下载失败，装不下去"))
                return

            # ---- 第 2 步：解析（纯本地，不联网）----
            state["msg"] = tr("正在解压整合包")
            blob = Path(tmp_pack).read_bytes()
            plan = mpk.plan_from_bytes(blob)

            # ---- 第 3 步：把计划里的文件都下下来（同一个引擎 + 进度窗口）----
            tasks = plan.download_tasks(instance_dir)
            if tasks:
                state["msg"] = tr("正在下载整合包里的 {n} 个文件", n=len(tasks))
                mgr.add_all(tasks)
                # ⚠️ 上一次 start() 之后引擎的 `_started` 是 True，这里**必须**再
                # start() 一次才会把新任务投进线程池（不重启就永远卡在"等待"）
                mgr.start()
                mgr.wait_all(timeout=7200)
                snap = mgr.snapshot()
                failed = snap["failed"]
            else:
                failed = 0
            # ---- 第 4 步：展开 overrides / client-overrides ----
            state["msg"] = tr("正在解压整合包")
            written, errors = mpk.apply_overrides(blob, plan, instance_dir)

            # ---- 第 5 步：装它要的原版 + 加载器（PCL 的 McInstallLoader 那一步）----
            #
            # ⚠️ 这一步是**照 PCL 补的**：光把整合包的文件铺好，实例是"能列出
            # 来、点启动缺 mainClass"。装上加载器之后版本 JSON 才是完整的一份
            # （`inheritsFrom` 指原版、`mainClass` 是加载器的）。
            # 只装我们装得了的（Fabric / Quilt）；Forge 那几家会明说没做。
            mc_id = plan.minecraft
            loader_key, loader_version = plan.loader()
            loader_note = ""
            installed_loader = None
            loader_inherit = ""      # 实例 JSON 要继承谁（只有安装器那条路会填）
            # 装之前记一份 `versions/` 名单：装完之后多出来的就是**这次造的**，
            # 只有"这次造的中间层"才允许在合并之后撤掉（见 `_drop_version_dir`）
            _dirs_before = _version_dir_names(mc_dir)
            _fresh_dirs = set()
            # 原版那层可能被上一次安装收进了 `.mosslight/vanilla/`：装之前搬回来
            # （安装器和版本 JSON 都要求它在 `versions/` 下露着）
            if standalone.materialize(mc_dir, mc_id):
                _dirs_before = _version_dir_names(mc_dir) - {mc_id}
                _log_win(win, tr("把原版 {mc} 从 .mosslight 搬回来了", mc=mc_id))
            if mc_id and loader_install.installable(loader_key):
                state["msg"] = tr("正在安装原版 {mc} 和 {loader}…",
                                  mc=mc_id,
                                  loader=loaders_label(loader_key))
                try:
                    url = loader_install.vanilla_json_url(mc_id)
                    vj = install_mod.http_json(url) if url else None
                    if not vj:
                        raise loader_install.LoaderError(
                            tr("清单里没有原版 {mc}，装不了它的加载器", mc=mc_id))
                    _vplan, loader_plan, loader_error = (
                        loader_install.install_stack(
                            mc_id, vj, mc_dir, mgr, version_id=inst_name,
                            loader_key=loader_key,
                            loader_version=loader_version,
                            fetch=install_mod.http_json,
                            meta=plan.modpack_meta(overrides_written=written)))
                    if loader_plan is not None:
                        mgr.start()
                        mgr.wait_all(timeout=3600)
                        # ⚠️ 是**总数**不是增量：snapshot 里含前面那批整合包文件，
                        # 写成 `+=` 会把之前的失败数算两遍
                        failed = mgr.snapshot()["failed"]
                        installed_loader = loader_key
                    else:
                        loader_note = loader_error
                except Exception as e:                  # noqa: BLE001
                    loader_note = "%s: %s" % (type(e).__name__, e)
            elif mc_id and loader_key in loader_install.INSTALLER_LOADERS:
                # Forge / NeoForge：得跑**官方安装器**（要 Java，三分钟起）
                #
                # ⚠️ 这里跟上面那条路**落点不一样**，故意不一样：
                # 安装器自己会在 `versions/` 下建一个目录（`1.20.1-forge-47.4.0`），
                # 而整合包的实例目录**已经存在**了（包里的文件就铺在那儿），
                # 改名过去会撞上"目标已存在"直接放弃 —— 那就成了两个互不相干的版本。
                # 所以加载器装进它**自己**那个目录（PCL 的命名），实例目录写一份
                # `inheritsFrom` 指过去的 JSON：实例 → 加载器 → 原版，三层，
                # `load_version()` 的 jar 回退正好一路找到原版的客户端 jar。
                state["msg"] = tr("正在安装原版 {mc} 和 {loader}…",
                                  mc=mc_id,
                                  loader=loaders_label(loader_key))
                try:
                    url = loader_install.vanilla_json_url(mc_id)
                    vj = install_mod.http_json(url) if url else None
                    if not vj:
                        raise loader_install.LoaderError(
                            tr("清单里没有原版 {mc}，装不了它的加载器", mc=mc_id))
                    # version_id 传空 → 用 PCL 那套名字（`1.20.1-Forge_47.4.0`），
                    # **不是**实例名
                    _vplan, loader_plan, loader_error = (
                        loader_install.install_stack(
                            mc_id, vj, mc_dir, mgr, version_id="",
                            loader_key=loader_key,
                            loader_version=loader_version,
                            fetch=install_mod.http_json))
                    if loader_plan is None:
                        raise loader_install.LoaderError(loader_error)
                    mgr.start()
                    mgr.wait_all(timeout=3600)
                    failed = mgr.snapshot()["failed"]
                    if failed:
                        loader_note = tr("有 {n} 个文件没下下来，加载器先不装了",
                                         n=failed)
                    else:
                        what = loaders_label(loader_key)
                        if win is not None:
                            win.stage_begin(tr(
                                "正在跑 {name} 的官方安装器…（要几分钟，别关这个窗口）",
                                name=what))
                            if loader_plan.installer_jar:
                                win.stage_write(tr("安装器：{path}",
                                                   path=loader_plan.installer_jar))
                        final, error = loader_install.finish_installer(
                            loader_plan, mc_dir,
                            on_line=(win.stage_write if win is not None else None),
                            manager=mgr)
                        if error:
                            if win is not None:
                                win.stage_end(tr("{name} 没装上", name=what))
                                win.stage_write(tr("出了点问题：{err}", err=error))
                            loader_note = error
                        else:
                            if win is not None:
                                win.stage_end(tr("{name} 装好了", name=what))
                                win.stage_write(tr("完成：{name}", name=final))
                            installed_loader = loader_key
                            loader_inherit = final
                except Exception as e:                  # noqa: BLE001
                    loader_note = "%s: %s" % (type(e).__name__, e)
            elif loader_key:
                # 剩下那几家（Cleanroom / LiteLoader …）确实没做，如实说
                loader_note = tr("{name} 得跑安装器才能装，这个还没做",
                                 name=loaders_label(loader_key))
            elif mc_id:
                loader_note = tr("这个整合包没写加载器")

            # ---- 第 6 步：写版本 JSON，让启动器认得出这个实例 ----
            #
            # 加载器装上去了 → 那份 profile 已经落盘了（install_stack 干的），
            # 这里**别覆盖**它；没装上才退回"只有元数据"的那份。
            _fresh_dirs = _version_dir_names(mc_dir) - _dirs_before
            if installed_loader is None:
                vj = plan.version_json(inst_name, overrides_written=written)
                install_mod.write_version_json(vj, instance_dir, inst_name)
            elif loader_inherit:
                # 安装器那条路：加载器在自己的目录里，实例先**指过去**，
                # 后面 `_fold_into_instance` 再把它们合进实例、把中间那层清掉
                # （顺带把整合包元数据带上，设置页/排查都用得上）
                vj = plan.version_json(inst_name, overrides_written=written)
                vj["mainClass"] = ""
                vj["inheritsFrom"] = loader_inherit
                install_mod.write_version_json(vj, instance_dir, inst_name)

            # ---- 第 6b 步：**把加载器/原版都合进实例这一个目录** ----
            #
            # 用户 2026-09 要的："一个版本一个独立文件夹，分多版本才好管理"。
            # 整合包尤其明显：装一个包却看到「原版 + 加载器 + 我的包」三行，
            # 找自己那个包都费劲。合完之后列表里只剩包名这一行。
            # ⚠️ 失败不影响安装结果（实例 JSON 还是能用的，只是多留一层），
            # 所以这里只写一句日志。
            if installed_loader is not None:
                try:
                    _ok, _note = standalone.make_standalone(mc_dir, inst_name,
                                                            mc_id)
                    if _note:
                        _log_win(win, _note)
                    if _ok:
                        # 中间那层加载器目录是我们这次装的吗？是就撤掉
                        if loader_inherit and loader_inherit in _fresh_dirs:
                            _drop_version_dir(mc_dir, loader_inherit, win)
                        _tail = standalone.cleanup_vanilla(
                            mc_dir, mc_id, created_by_us=(mc_id in _fresh_dirs))
                        if _tail:
                            _log_win(win, _tail)
                except Exception as _e:                     # noqa: BLE001
                    _log_win(win, "%s: %s" % (type(_e).__name__, _e))

            # ---- 收尾：把整合包的图标设成实例的图标（PCL 也这么干）----
            # ⚠️ 失败不影响安装结果，`install_version_icon` 自己吞异常并留一行线索
            if icon_url:
                icon_name = install_version_icon(inst_name, icon_url)
                if icon_name:
                    state["icon"] = icon_name

            # ---- 收尾 ----
            can_launch = installed_loader is not None
            if failed or errors:
                state.update(stage="done", msg=tr(
                    "整合包安装结束，但有 {n} 个文件失败", n=max(failed, len(errors))))
            elif can_launch:
                state.update(stage="done", msg=tr(
                    "整合包安装完成：{name}（自带 {loader}，可以直接启动）",
                    name=plan.name, loader=loaders_label(installed_loader)))
            elif loader_note:
                state.update(stage="done", msg=tr(
                    "整合包文件装好了：{name}（{why}，实例还不能直接启动）",
                    name=plan.name, why=loader_note))
            else:
                state.update(stage="done", msg=tr("整合包安装完成：{name}",
                                                  name=plan.name))
            state["path"] = str(instance_dir)
        except mpk.ModpackError as e:
            state.update(stage="error", msg="",
                         error=tr("安装整合包失败：{err}", err=e))
        except Exception as e:                              # noqa: BLE001
            state.update(stage="error", msg="",
                         error=tr("安装整合包失败：{err}",
                                  err="%s: %s" % (type(e).__name__, e)))
        finally:
            # 临时 .mrpack 不留着（几十上百 MB）
            try:
                Path(tmp_pack).unlink(missing_ok=True)
            except OSError:
                pass

    def _poll_modpack(self):
        """主线程收整合包安装的结果（**这里才碰控件**）"""
        st = self._mp_state
        if st.get("stage") == "working":
            if st.get("msg"):
                self.pick_hint.setText(st["msg"])
            return
        if st.get("stage") in ("done", "error"):
            text = st.get("msg") or st.get("error") or tr("整合包安装完成")
            self._mp_state = {"stage": "idle", "msg": "", "error": "", "path": None}
            self.pick_hint.setText(text)
            # 收掉进度窗口：`stop()` 会停它的定时器、把还在缓冲里的"完成"行撤掉
            # （不调的话那些行再也没人撤，会一直挂在列表上）。
            # ⚠️ 只关窗口**不停下载**是 DownloadWindow 的约定，但这里已经装完了，
            # 所以直接关掉更省事（用户不用自己点关闭）。
            if self._mp_win is not None:
                try:
                    self._mp_win.stop()
                    self._mp_win.close()
                except RuntimeError:
                    pass                # 用户已经自己关掉了
                self._mp_win = None
            self._mp_manager = None

    # ============================================================
    # 图标（磁盘缓存 + 后台线程）
    # ============================================================

    def _load_icon(self, url: str, label: QLabel, size: int):
        """给一个 QLabel 挂图标：先查磁盘缓存，没有就后台下

        ⚠️ 图标**绝不在主线程联网**（实验项目在这上面卡过 2.4 秒）。
        ⚠️ 结果回来时控件可能已经换了对象（列表重建）——用 QLabel 自己的
        `_icon_url` 核对，对不上就丢（同类竞态的教训见实验使用说明第 11/14 条）。
        """
        if not url:
            return
        cached = load_icon(url)
        if cached:
            self._apply_icon(label, size, cached)
            return
        label.setProperty("_icon_url", url)
        if url in self._icon_asked:
            return
        self._icon_asked.add(url)
        # ⚠️ 记"有几个图标请求还在路上"，`_is_busy()` 要拿它决定轮询停不停。
        # 少了这一步就是真 bug：`_load_icon` 起了线程后只 `self._timer.start()`，
        # 而定时器 150ms 后第一次 `_poll` 时队列**还是空的**、又没有别的活在跑，
        # `_is_busy()` 返回 False → 定时器自己停掉；等图标真的回来时已经没人来收，
        # 那一行就永远是问号（实测撞到过：6 张卡片的图标一个都没上）。
        # 线程里只做 +=/-=（CPython 下够安全），碰 QTimer 那种 GUI 对象才是禁区。
        with self._icon_lock:
            self._icon_pending += 1

        def work():
            try:
                import requests
                r = requests.get(url, timeout=(10, 20), headers={
                    "User-Agent": "Mosslight/0.5"})
                r.raise_for_status()
                data = r.content
                save_icon(url, data)
            except Exception:               # noqa: BLE001 —— 图标失败不影响功能
                data = b""
            with self._icon_lock:
                self._icon_queue.append((url, data))
                self._icon_pending = max(0, self._icon_pending - 1)

        threading.Thread(target=work, daemon=True).start()
        self._timer.start()

    @staticmethod
    def _apply_icon(label: QLabel, size: int, data: bytes):
        from PyQt6.QtGui import QPixmap
        if not data:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        if pixmap.isNull():
            return
        label.setText("")
        label.setPixmap(pixmap.scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    # ============================================================
    # 轮询：把后台结果搬到界面上（**主线程**，这里才碰控件）
    # ============================================================

    def _poll(self):
        self._poll_search()
        self._poll_detail()
        self._poll_icons()
        self._poll_install()
        self._poll_modpack()
        # 没活了就把定时器停掉（不然它 150ms 空转一辈子）。
        # ⚠️ 判据要**把所有在跑的活都算上**：只看"搜索完了"就停的话，
        # 正在进行中的**下载**会再也没人来收尾（_install_done 永远没人读）。
        if not self._is_busy():
            self._timer.stop()

    def _is_busy(self) -> bool:
        """还有后台活在跑吗（决定轮询要不要继续）

        ⚠️ `_icon_pending` 这一项**不能少**：图标是"起个线程去下、下完推进队列"，
        少了它就会出现"队列还空着 → 判定没活 → 定时器停 → 图标回来没人收"
        （见 `_load_icon` 里那段说明）。
        """
        return bool(
            self._loading
            or self._search_result is not None
            or self._detail_result is not None
            or self._icon_queue
            or self._icon_pending
            or not self._install_done and self._install_manager is not None
            # 整合包安装是一串步骤，状态收完之前不能让轮询停掉
            or self._mp_state.get("stage") == "working"
        )

    def _wake(self):
        """有新的后台活要收 → 确保定时器在跑"""
        if not self._timer.isActive():
            self._timer.start()

    def _poll_search(self):
        result = self._search_result
        if result is None:
            return
        self._search_result = None
        self._loading = False
        kind, payload = result
        self.search_btn.setEnabled(True)

        if kind == "err":
            self.loading.hide_now()
            self._show_error(str(payload))
            return

        hits = [h for h in (payload.get("hits") or []) if isinstance(h, dict)]
        self._total = int(payload.get("total_hits") or 0)
        if not hits and not self._cards:
            self.loading.hide_now()
            hint = self.label("没有找到结果，换个关键词或放宽筛选试试", "HintText")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._add_result_widget(hint)
            self.status.setText(tr("0 个结果"))
            return

        for hit in hits:
            card = _ModCard(hit, on_click=self.open_mod)
            self._add_result_widget(card)
            self._cards.append(card)
            self._load_icon(str(hit.get("icon_url") or ""), card.icon, 64)

        self._start_stagger("results")
        self._offset += len(hits)
        self.loading.hide_now()
        if self._total > self._offset:
            self.status.setText(tr("显示 {n} / 共 {m} 个结果",
                                   n=self._offset, m=self._total))
        else:
            self.status.setText(tr("共 {n} 个结果", n=self._offset))

    def _poll_detail(self):
        result = self._detail_result
        if result is None:
            return
        self._detail_result = None
        self._loading = False
        kind, payload, versions = result
        if kind == "err":
            # ⚠️ 先收加载条**再**切错误页：_show_error 会把 stack 切到第 2 页，
            # 而加载条挂在详情页上，切走之后它就没人管了 —— 下次进来还是
            # "正在加载…"的状态（看着像永远加载不完）
            self.detail_loading.hide_now()
            self._show_error(str(payload))
            return
        self._fill_detail(payload or {})
        self._fill_versions(versions or [])
        self.detail_loading.hide_now(
            tr("已加载 {n} 个版本分组", n=len(self._groups)))

    def _poll_icons(self):
        with self._icon_lock:
            pending, self._icon_queue = self._icon_queue, []
        if not pending:
            return
        by_url = {url: data for url, data in pending}
        # 搜索结果里的卡片
        for card in self._cards:
            url = card.hit.get("icon_url") or ""
            if url in by_url:
                card.set_icon_data(by_url[url])
        # 详情卡
        url = str(self._current_project.get("icon_url") or "")
        if url in by_url:
            self._apply_icon(self.detail_icon, 96, by_url[url])

    def _poll_install(self):
        if not self._install_done:
            return
        self._install_done = False
        if self._install_win is not None:
            self._install_win.stop()        # 停掉它的定时器再放掉引用
            self._install_win = None
        self._install_manager = None
        if self._install_note:
            self.pick_hint.setText(self._install_note)
            self._install_note = ""

    def _show_error(self, detail: str):
        self.error_detail.setText(detail or "")
        self.stack.setCurrentIndex(self.PAGE_ERROR)

    def _retry(self):
        if self._current_hit and self.stack.currentIndex() != self.PAGE_ERROR:
            self.open_mod(self._current_hit)
        else:
            self.do_search()

    # ============================================================
    # 其它
    # ============================================================

    def do_reset(self):
        self.search_input.clear()
        for combo, default in ((self.mc_combo, ANY), (self.loader_combo, ANY),
                               (self.cat_combo, ANY),
                               (self.sort_combo, api.DEFAULT_SORT)):
            index = combo.findData(default)
            combo.setCurrentIndex(max(0, index))
        self.do_search()

    def _open_modrinth(self):
        slug = (self._current_project or {}).get("slug") or \
               (self._current_hit or {}).get("slug")
        if slug:
            self._open_url(str(slug))

    def _open_url(self, slug: str):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        ptype = (self._current_project or {}).get("project_type") or "mod"
        QDesktopServices.openUrl(
            QUrl("https://modrinth.com/%s/%s" % (ptype, slug)))

    def _open_mcmod(self):
        """MC 百科：目前只开搜索页（逐条解析的实现见 core/mcmod.py 的移植计划）"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        name = (self._current_project or {}).get("title") or \
               (self._current_hit or {}).get("title") or ""
        QDesktopServices.openUrl(QUrl(api.mcmod_search_url(str(name))))

    def retranslate(self):
        super().retranslate()
        self._fill_combos()
        self._fill_type_tabs()      # 类型标签 + 标题 + 搜索按钮的文字
        # 状态行 / 提示是运行时拼的，重建一次
        if self.stack.currentIndex() == self.PAGE_DETAIL and self._groups:
            self._refresh_pick_hint()


def Path_text(mc, sub: str) -> str:
    """`<游戏目录>/<子目录>` 的显示文本（只是给提示行用）

    ⚠️ 单独抽出来是因为**只是展示**：真实落点由 `mc_dir.target_dir()` 算
    （那里才会按版本隔离进 `versions/<版本文件夹>/`），这里给的是"没匹配到
    版本文件夹时"的兜底位置，让用户知道大概会去哪。
    """
    return str(Path(mc) / sub)
