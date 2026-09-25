"""下载页（PCL 那种左侧分类 + 右侧内容）

    左侧分类                         右侧
    ────────────────────────────────────────────────────────────
    Minecraft          ← 有真内容    最新版本卡 + 四个折叠组
    社区资源 / 收藏夹 / 安装包        「将来是什么 + 还没做」占位

右侧是 `QStackedWidget`：**只有 Minecraft 那一项**显示真实的版本列表
（数据来自 `core/versions_remote.py`）；其它分类保持占位 —— 不留假列表。

## 拉清单为什么用普通线程 + QTimer

清单 270 KB、要联网。放主线程会卡界面；用 QThread 又要管它生死
（我们被 `QThread: Destroyed while thread is still running` 坑过）。
这里用**普通线程写结果 + QTimer 每 150ms 取一次**，最稳，
而且线程是 daemon，页面销毁后它自己会退出。
"""

import threading

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget
)

import requests

from core import install as install_mod
from core import loader_install
from core import loaders as loaders_mod
from core import standalone
from core import versions_remote as vr
from core.config import config, effective_threads
from core.download import USER_AGENT, DownloadManager
from core.i18n import tr
from ui.translatable import TranslatableWidget
from ui.icons import screen_dpr
from ui.widgets.collapsible_group import (
    CollapsibleGroup, RemoteVersionRow, format_release_time
)
from ui.dialogs.download_window import DownloadWindow
from ui.pages.mods_page import ModsPage
from ui.widgets.install_options import InstallOptions

NAV_WIDTH = 200
FETCH_POLL_MS = 150

# 左侧分类。分组标题写字符串，`None` 表示"这一项自己就是一级、没有子项"
NAV = (
    ("版本", (
        ("minecraft", "Minecraft"),
    )),
    ("社区资源", (
        ("mods", "模组"),
        ("modpacks", "整合包"),
        ("datapacks", "数据包"),
        ("resourcepacks", "资源包"),
        ("shaders", "光影包"),
        ("worlds", "世界"),
    )),
    (None, (
        ("favorites", "收藏夹"),
    )),
    ("安装包", (
        ("install_minecraft", "Minecraft"),
        ("optifine", "OptiFine"),
        ("forge", "Forge"),
        ("neoforge", "NeoForge"),
        ("cleanroom", "Cleanroom"),
        ("fabric", "Fabric"),
        ("legacy_fabric", "Legacy Fabric"),
        ("quilt", "Quilt"),
        ("labymod", "LabyMod"),
        ("liteloader", "LiteLoader"),
    )),
)

# 已经有真内容的分类（别的还是占位）
#
# ⚠️ "mods" 那一页（ui/pages/mods_page.py）现在**同时承担整个「社区资源」**：
# 它自带类型标签栏，进来时按左栏点的那一栏切到对应类型。
# 「世界」**故意没接** —— Modrinth 没有世界存档这个项目类型（只索引
# 模组 / 整合包 / 资源包 / 光影 / 数据包），不硬做。
READY_CATEGORIES = ("minecraft",)

# 左栏分类 → Modrinth 的 project_type（跟 core/mc_dir.py 的 TYPES 对齐）
MODS_CATEGORIES = {
    "mods": "mod",
    "modpacks": "modpack",
    "datapacks": "datapack",
    "resourcepacks": "resourcepack",
    "shaders": "shader",
}

DETAILS = {
    "mods": "按游戏版本和加载器筛 Mod，从 Modrinth 取",
    "modpacks": "整合包，从 Modrinth 取",
    "datapacks": "数据包，从 Modrinth 取",
    "resourcepacks": "资源包，从 Modrinth 取",
    "shaders": "光影包，从 Modrinth 取",
    "worlds": "世界存档（Modrinth 没有这类资源，暂时还没做）",
    "favorites": "收藏的版本和资源，集中在这儿一键装",
    "install_minecraft": "各版本的原版安装包",
    "optifine": "OptiFine 各版本",
    "forge": "Forge 各版本（含推荐版和最新版）",
    "neoforge": "NeoForge 各版本",
    "cleanroom": "Cleanroom 各版本（还没研究过怎么下）",
    "fabric": "Fabric 加载器各版本",
    "legacy_fabric": "Legacy Fabric（还没研究过）",
    "quilt": "Quilt 加载器各版本（还没研究过）",
    "labymod": "LabyMod（还没研究出来怎么下）",
    "liteloader": "LiteLoader（太老，还没确定怎么下）",
}

PAGE_PLACEHOLDER = 0        # 「这个分类还没做」
PAGE_VERSIONS = 1           # 版本列表
PAGE_ERROR = 2              # 网络不通、又没有缓存可看
PAGE_INSTALL = 3            # 选好版本之后的「安装选项」
PAGE_MODS = 4               # 社区资源 → 模组（真实内容，见 ui/pages/mods_page.py）


class DownloadPage(TranslatableWidget):
    # 选了一个远程版本（下一步进"安装选项"页；现在只记下来 + 显示一行提示）
    version_chosen = pyqtSignal(dict)
    # 装完了一个版本（主窗口据此重扫，新版本就会出现在「版本」页）
    installed = pyqtSignal()

    def __init__(self):
        super().__init__()
        # 构造期间挡住信号：_fill_nav() 里"选中第一项"会触发 _select()，
        # 而那会儿右侧控件还没建出来（第一版就这么崩过）
        self._loading = True
        self._current = ""
        self._grouped = None
        self._info = {}
        self._fetch_result = None
        self._fetching = False
        self._selected = None
        # 安装：窗口 + 管理器 + 后台线程写、主线程轮询的三个字段
        self._install_win = None
        self._install_manager = None
        self._install_done = False
        self._install_ok = False
        self._install_result = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        layout.addWidget(self._make_nav())
        layout.addWidget(self._make_body(), 1)

        self._timer = QTimer(self)
        self._timer.setInterval(FETCH_POLL_MS)
        self._timer.timeout.connect(self._poll_fetch)

        # 加载器那条线单独一个定时器 + 结果槽（跟清单一样的普通线程模式）
        self._loader_result = None
        self._loading_loaders = False
        self._loader_timer = QTimer(self)
        self._loader_timer.setInterval(FETCH_POLL_MS)
        self._loader_timer.timeout.connect(self._poll_loaders)

        # Fabric API（Modrinth）也单独一条：**用户真选了 Fabric 才去拉**
        self._api_result = None
        self._loading_api = False
        self._api_versions = []
        self._api_mc = ""               # 这批数据是给哪个游戏版本拉的
        self._api_timer = QTimer(self)
        self._api_timer.setInterval(FETCH_POLL_MS)
        self._api_timer.timeout.connect(self._poll_fabric_api)

        # 安装那一步：后台线程只写结果，主线程定时器来收（见 _poll_install）
        self._install_timer = QTimer(self)
        self._install_timer.setInterval(FETCH_POLL_MS)
        self._install_timer.timeout.connect(self._poll_install)

        self._loading = False
        self._select("minecraft")

    # ---------- 左侧分类 ----------

    def _make_nav(self) -> QFrame:
        card = QFrame()
        # ⚠️ 不叫 "Card"：它是**外壳**不是内容卡片 —— 设了背景图时它要跟着
        # 左侧竖栏一起半透明（见 ui/widgets/card_style.py 的 chrome_stylesheet），
        # 沿用 "Card" 的话会跟别的卡片一起被"卡片透明度"带走。
        # 底色/描边仍在 app.qss 里跟 #Card 共用一条规则，所以观感不变。
        card.setObjectName("DownloadNavCard")
        card.setFixedWidth(NAV_WIDTH)
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 16, 14, 16)
        box.setSpacing(8)

        box.addWidget(self.label("下载", "PageTitle"))

        self.nav = QListWidget()
        self.nav.setObjectName("VersionList")       # 借版本列表那套卡片行样式
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.currentItemChanged.connect(self._on_nav_changed)
        box.addWidget(self.nav, 1)

        self._fill_nav()
        return card

    def _fill_nav(self):
        """填分类。分组标题**不可选**（点它不该把右边切走）"""
        self.nav.blockSignals(True)
        self.nav.clear()
        first = None
        for group, items in NAV:
            if group:
                head = QListWidgetItem()
                head.setFlags(Qt.ItemFlag.NoItemFlags)
                label = QLabel(tr(group))       # group 是键，运行时查表
                label.setObjectName("DownloadNavGroup")
                label.setContentsMargins(10, 10, 10, 6)
                font = QFont()
                font.setPixelSize(17)       # 要和 QSS 里 #DownloadNavGroup 一致
                font.setBold(True)
                label.setFont(font)
                head.setSizeHint(label.sizeHint())
                self.nav.addItem(head)                      # ★ 先 addItem
                self.nav.setItemWidget(head, label)         # ★ 再挂控件
            for key, text in items:
                item = QListWidgetItem(tr(text))
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.nav.addItem(item)
                if first is None:
                    first = item
        if first is not None:
            self.nav.setCurrentItem(first)
        self.nav.blockSignals(False)        # ★ 信号一直挡到选完为止

    def _on_nav_changed(self, current, _previous=None):
        if self._loading or current is None:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        if key:
            self._select(key)

    def _select(self, key: str):
        self._current = key
        name = ""
        for _group, items in NAV:
            for item_key, text in items:
                if item_key == key:
                    name = text
        if key in MODS_CATEGORIES:
            # 社区资源那几栏共用**同一页**（模组页自带类型标签栏）。
            # 切栏时把类型也切过去，并重搜一次 —— 不重搜的话用户点了"光影包"
            # 却还看着上一栏的结果，会以为没反应。
            ptype = MODS_CATEGORIES[key]
            if self.mods_page.current_project_type() != ptype:
                self.mods_page.set_project_type(ptype)
            self.stack.setCurrentIndex(PAGE_MODS)
            return

        self.body_title.setText(tr(name))
        self.body_detail.setText(tr(DETAILS.get(key, "")))
        self.body_hint.setText(tr(
            "下载功能还没做（计划在 v0.4）。这里将来会列出可下载的版本，"
            "点一下就装进当前游戏目录。"))

        if key in READY_CATEGORIES:
            # 之前失败过、又没有缓存：保持显示提示页，别又切回空列表
            if self._grouped is None and self.error_detail.text():
                self.stack.setCurrentIndex(PAGE_ERROR)
                return
            self.stack.setCurrentIndex(PAGE_VERSIONS)
            if self._grouped is None and not self._fetching:
                self.reload_manifest()
        else:
            self.stack.setCurrentIndex(PAGE_PLACEHOLDER)

    # ---------- 右侧 ----------

    def _make_body(self) -> QWidget:
        wrapper = QWidget()
        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._make_placeholder())      # PAGE_PLACEHOLDER
        self.stack.addWidget(self._make_versions())         # PAGE_VERSIONS
        self.stack.addWidget(self._make_error_page())       # PAGE_ERROR

        # 第二页：安装选项。返回/开始下载都在这儿接
        self.install_page = InstallOptions()
        self.install_page.back_requested.connect(self._back_to_list)
        self.install_page.install_requested.connect(self._on_install_requested)
        # 用户选了 Fabric → 才去拉 Fabric API 的版本列表（见 _fetch_fabric_api）
        self.install_page.api_wanted.connect(self._fetch_fabric_api)
        self.stack.addWidget(self.install_page)             # PAGE_INSTALL

        # 社区资源 → 模组（搜索 / 详情 / 下载）。构造时就建好 ——
        # 它自己不联网（首次搜索由用户点或 _select 触发），成本只有建控件
        self.mods_page = ModsPage()
        self.stack.addWidget(self.mods_page)                # PAGE_MODS
        outer.addWidget(self.stack)
        return wrapper

    def _make_placeholder(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(24, 22, 24, 22)
        box.setSpacing(10)

        self.body_title = QLabel()
        self.body_title.setObjectName("PageTitle")
        box.addWidget(self.body_title)

        self.body_detail = QLabel()
        self.body_detail.setObjectName("PageSubtitle")
        self.body_detail.setWordWrap(True)
        box.addWidget(self.body_detail)

        box.addSpacing(8)
        self.body_hint = QLabel()
        self.body_hint.setObjectName("HintText")
        self.body_hint.setWordWrap(True)
        box.addWidget(self.body_hint)
        box.addStretch()
        return card

    def _make_error_page(self) -> QFrame:
        """拉不到清单、又没有缓存时显示的那一页（**Mod 下载那边以后也复用这一页**）

        ⚠️ 只给"拿不到元数据"用。**别顺手给大文件也做缓存** ——
        Mod / 整合包动辄几百 MB，全缓存下来是硬盘杀手（用户 2026-09 提醒）。
        清单这种几百 KB 的元数据缓存没问题（见 core/versions_remote.py）。

        用户 2026-09 要求：大感叹号 + 一句能指导操作的提示。
        ⚠️ 别写「（发生一个或多个错误。）」那种 .NET 尾巴 —— 用户看不懂，
        也不告诉他该干什么；真正有用的原始报错放在下面那行小字里（排查用）。
        """
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
            "拿不到版本列表。请稍后再试，或者换个网络（必要时用代理 / VPN）。"
            "已经下载过的版本不受影响。", "HintText")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        box.addWidget(msg)

        # 原始报错：给排查用，故意做得很小很淡
        self.error_detail = QLabel()
        self.error_detail.setObjectName("HintText")
        self.error_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_detail.setWordWrap(True)
        box.addWidget(self.error_detail)

        retry_row = QHBoxLayout()
        retry_row.addStretch()
        self.retry_btn = self.button("重试", "PrimaryButton")
        self.retry_btn.clicked.connect(lambda: self.reload_manifest(force=True))
        retry_row.addWidget(self.retry_btn)
        retry_row.addStretch()
        box.addLayout(retry_row)
        box.addStretch()
        return card

    def _make_versions(self) -> QWidget:
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 8, 0)
        box.setSpacing(10)

        # 状态行：加载中 / 出错 / "这是缓存" / "共 N 个版本"
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.status = QLabel()
        self.status.setObjectName("HintText")
        self.status.setWordWrap(True)
        bar.addWidget(self.status, 1)
        self.refresh_btn = self.button("刷新", "LinkButton")
        self.refresh_btn.clicked.connect(lambda: self.reload_manifest(force=True))
        bar.addWidget(self.refresh_btn)
        box.addLayout(bar)

        # 最新版本卡
        card = QFrame()
        card.setObjectName("Card")
        card_box = QVBoxLayout(card)
        card_box.setContentsMargins(18, 14, 18, 14)
        card_box.setSpacing(6)
        card_box.addWidget(self.label("最新版本", "SectionTitle"))
        # 「最新版本」那两行本身就是下载按钮（用户 2026-09 指出），
        # 所以用 RemoteVersionRow —— 能点、能发 version_chosen。
        # 数据来了才知道内容，所以这里先放两个空容器，set_grouped 里填。
        self._latest = {}
        for key in ("latest_release", "latest_snapshot"):
            # ⚠️ 变量名别叫 holder！外层那个 holder 是滚动区的 QWidget，
            # 重名会把它的 Python 引用覆盖掉 → PyQt 顺手把它和它的布局一起删了
            # （报 wrapped C/C++ object of type QVBoxLayout has been deleted）
            latest_holder = QVBoxLayout()
            latest_holder.setContentsMargins(0, 0, 0, 0)
            latest_holder.setSpacing(2)
            card_box.addLayout(latest_holder)
            self._latest[key] = latest_holder
        box.addWidget(card)

        # 四个折叠组
        self.groups = {}
        for title in vr.GROUP_ORDER:
            group = CollapsibleGroup(title)
            group.item_clicked.connect(self._on_version_clicked)
            self.groups[title] = group
            box.addWidget(group)
        box.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(holder)
        return scroll

    def _fill_latest(self, holder, entry, kind_text: str):
        """把一个「最新版本」行填进容器（可点，跟列表里的行一样）"""
        while holder.count():
            item = holder.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if not entry:
            return
        entry = dict(entry)
        entry["latest_kind"] = kind_text
        row = RemoteVersionRow(entry, screen_dpr(self))
        row.clicked.connect(self._on_version_clicked)
        holder.addWidget(row)

    # ---------- 拉清单 ----------

    def reload_manifest(self, force: bool = False):
        if self._fetching:
            return
        self._fetching = True
        self._fetch_result = None
        self.status.setText(tr("正在获取版本列表…"))
        self.refresh_btn.setEnabled(False)

        def worker():
            try:
                self._fetch_result = ("ok", vr.fetch_grouped(force=force))
            except Exception as e:                              # noqa: BLE001
                self._fetch_result = ("err", e)

        threading.Thread(target=worker, daemon=True).start()
        self._timer.start()

    def _poll_fetch(self):
        result = self._fetch_result
        if result is None:
            return                      # 还没跑完，下一跳再看
        self._fetch_result = None
        self._fetching = False
        self._timer.stop()
        self.refresh_btn.setEnabled(True)

        kind, payload = result
        if kind == "err":
            self._show_error(str(payload))
            return
        grouped, info = payload
        if grouped is None:
            self._show_error((info or {}).get("error", ""))
            return
        self.set_grouped(grouped, info)

    def _show_error(self, detail: str):
        """网络失败：没缓存才占满整页；有缓存就继续显示缓存列表

        （有缓存还摆一个大感叹号，等于把用户能用的东西藏起来）
        """
        self.error_detail.setText(detail or "")
        if self._grouped is not None:
            self.status.setText(tr("⚠ 网络不通，这是本地缓存的列表（可能不是最新的）"))
            return
        self.stack.setCurrentIndex(PAGE_ERROR)

    def set_grouped(self, grouped: dict, info: dict = None):
        """把数据铺到界面上（测试直接调它，不用联网）"""
        self._grouped = grouped
        self._info = info or {}

        april_years = vr.april_fools_years()
        for key, kind_text in (("latest_release", "最新正式版"),
                               ("latest_snapshot", "最新预览版")):
            self._fill_latest(self._latest[key], grouped.get(key), kind_text)

        for title, items in grouped.get("groups", []):
            group = self.groups.get(title)
            if group is None:
                continue
            for entry in items:
                # 愚人节版多一个年份（界面上显示「2026 年愚人节版」）
                if entry["id"] in april_years:
                    entry["april_fools_year"] = april_years[entry["id"]]
            group.set_items(items)

        # 默认展开「正式版」，其它收起。
        # ⚠️ 原来展开的是愚人节版（理由写的是"就 9 个，展开着更有信息量"），
        # 但绝大多数人进来就是装正式版的 —— 一开页看到一堆愚人节版本很莫名其妙。
        # 这里对每组都显式设一次（而不是只 set 一组），重新拉列表时也能复位。
        for title, group in self.groups.items():
            group.set_expanded(title == vr.GROUP_RELEASE)

        if self._info.get("stale"):
            self.status.setText(tr("⚠ 网络不通，这是本地缓存的列表（可能不是最新的）"))
        elif self._info.get("source") == "cache":
            self.status.setText(tr("共 {n} 个版本（本地缓存）", n=_total(grouped)))
        else:
            self.status.setText(tr("共 {n} 个版本", n=_total(grouped)))

    def _on_version_clicked(self, entry: dict):
        """点了一个远程版本 → 进第二页（安装选项）"""
        self._selected = entry
        self.version_chosen.emit(entry)
        self.install_page.set_version(entry)
        # 顺手把"会装到哪"显示出来（这个目录是用户选的，见版本管理页）
        self.install_page.set_target_dir(config.get_minecraft_dir())
        self.stack.setCurrentIndex(PAGE_INSTALL)
        self._fetch_loaders(entry.get("id", ""))

    def _back_to_list(self):
        self.stack.setCurrentIndex(PAGE_VERSIONS)

    def _fetch_loaders(self, mc_version: str):
        """异步拉这个版本的加载器列表（Forge 走镜像、Fabric 走官方）"""
        self._loading_loaders = True
        self._loader_result = None
        self.install_page.set_rows([])          # 先清空，显示「正在获取…」

        def worker():
            try:
                self._loader_result = ("ok", loaders_mod.loader_rows(mc_version))
            except Exception as e:                          # noqa: BLE001
                self._loader_result = ("err", e)

        threading.Thread(target=worker, daemon=True).start()
        self._loader_timer.start()

    def _poll_loaders(self):
        result = self._loader_result
        if result is None:
            return
        self._loader_result = None
        self._loading_loaders = False
        self._loader_timer.stop()
        kind, payload = result
        if kind == "err":
            # 拉不到就把整条线当失败上报：第二页会显示「拉不到…」那一行
            self.install_page.set_rows([])
            self.install_page.set_status(tr("获取加载器列表失败：{err}", err=payload))
            return
        self.install_page.set_rows(payload)

    # ---------- Fabric API（选了 Fabric 才去拉）----------

    def _fetch_fabric_api(self, mc_version: str):
        """用户**真的选了 Fabric** 之后才去拉 Fabric API 的版本列表

        ⚠️ 不做成"进页面就顺手拉"：Modrinth 那边一次请求不便宜，
        而选 Forge / NeoForge 的人根本用不上它。
        """
        if self._loading_api:
            return
        if self._api_mc == mc_version and self._api_versions:
            return                      # 这个游戏版本的已经拉过了
        self._loading_api = True
        self._api_result = None
        self._api_mc = mc_version

        def worker():
            versions = []
            try:
                versions = loader_install.fabric_api_versions(mc_version)
            except Exception:                               # noqa: BLE001
                versions = []       # 拉不到就那一组不出现，不影响装加载器
            self._api_result = versions

        threading.Thread(target=worker, daemon=True).start()
        self._api_timer.start()

    def _poll_fabric_api(self):
        if self._api_result is None:
            return
        versions = self._api_result
        self._api_result = None
        self._loading_api = False
        self._api_timer.stop()
        self._api_versions = list(versions or [])
        self.install_page.set_api_versions(self._api_versions)

    def _on_install_requested(self, payload: dict):
        """「开始下载」：摆出进度窗口 → 后台算清单并下载 → 装完刷新版本列表"""
        entry = self._selected or {}
        if not entry:
            return
        mc_dir = config.get_minecraft_dir()
        name = payload.get("name") or entry.get("id", "")

        if self._install_win is not None:
            # 已经在装了，别开第二个。
            # ⚠️ 用户 2026-09 反应过这里"点了没反应" —— 其实是在装，
            # 只是**正装在哪个版本**那句话在安装选项页上，而他这会儿可能
            # 已经退回列表了。所以这句提示要写在**两个地方**（见下）。
            note = tr("正在安装 {name}…（进度见弹出的下载窗口）", name=name)
            self.install_page.set_status(note)
            self.status.setText(note)
            return

        # ⚠️ 先摆窗口再开跑（顺序反了前几百毫秒的进度会丢）
        # 线程数走设置里的「多线程下载」（开关 + 线程数 1~32）
        manager = DownloadManager(max_workers=effective_threads())
        # ⚠️ parent 一定要给：不给的话这个顶层窗口拿不到主窗口的样式表，
        # 会变成 Qt 原生外观（白底 + 系统进度条）。见 DownloadWindow.__init__。
        win = DownloadWindow(manager, title=tr("正在安装 {name}", name=name),
                             parent=self)
        win.show()
        self._install_win = win
        self._install_manager = manager
        self._install_done = False
        self._install_ok = False
        self._install_result = ""

        threading.Thread(target=self._install_worker,
                         args=(entry, mc_dir, name, manager, payload),
                         daemon=True).start()
        self._install_timer.start()

        # ⚠️ **任务跑起来之后自动退回版本列表**（用户 2026-09 要的）。
        # 为什么：装一个版本要几分钟（Forge 那种还要跑安装器），把用户按在
        # 「安装选项」那一页上没有任何意义 —— 他既改不了选、也看不到别的版本，
        # 只能干等。退回列表后进度由那个下载窗口负责，
        # 而"正在装哪个版本"写在列表页的状态行上（不然退回来以后一点交代都没有）。
        note = tr("正在安装 {name}…（进度见弹出的下载窗口）", name=name)
        self.install_page.set_status(note)
        self._back_to_list()
        self.status.setText(note)

    def _install_worker(self, entry: dict, mc_dir, name: str, manager,
                        job: dict = None):
        """后台线程：取版本 JSON → 算清单 → 下载 → 解 natives（→ 装加载器）

        ⚠️ 这里**只写结果**，不碰界面（Qt 控件只能在主线程动）——
        主线程的 _poll_install() 会来收。

        ## 选了加载器时的落点（跟 PCL 一致）

            versions/<游戏版本>/                原版（客户端 jar + 版本 JSON）
            versions/<用户填的名字>/            加载器 profile（inheritsFrom 指上面那个）
            versions/<用户填的名字>/mods/       一起装的 mod（Fabric API）

        ⚠️ **原版要装到"游戏版本"那个目录**，不能顺手塞进加载器目录：
        加载器 JSON 里的 `inheritsFrom` 是父版本 id，启动时
        `core/launch.py` 正是去 `versions/<父 id>/` 找原版的 JSON 和 jar
        （`jar` 字段回退成父版本 id）。塞错地方的话 classpath 里就没有
        Minecraft 的类，Fabric 会报 "couldn't locate the game"。
        原版已经装过的话这一步只是"检查一遍"，已有文件会被跳过，不重复下载。
        """
        job = job or {}
        loader_key = str(job.get("loader_key") or "")
        loader_version = str(job.get("loader_version") or "")
        loader_extra = job.get("loader_extra") or {}
        api_version = job.get("api_version") or None
        mc_id = str(entry.get("id") or "") or name
        # 没选加载器 = 老路子：整个版本就装成用户填的那个名字
        vanilla_name = mc_id if (loader_key and name != mc_id) else name

        # ⚠️ 上一次把原版那层收进 `.mosslight/vanilla/` 了（见下面的
        # `_finalize_layout`）就先搬回来：安装器（Forge 那几家）和版本 JSON
        # 都要求原版在 `versions/` 下露着，而且搬回来之后那 25 MB 客户端 jar
        # 就不用重下了（引擎看到文件在就跳过）。
        #
        # ⚠️ "原来就在不在"要在**搬回来之前**记：搬回来的那份也是我们收起来的，
        # 装完该再收回去（不然用户会发现"那个原版又冒出来了"）。
        vanilla_was_there = (mc_dir / "versions" / mc_id).is_dir()
        if standalone.materialize(mc_dir, mc_id):
            self._note(tr("把原版 {mc} 从 .mosslight 搬回来了", mc=mc_id),
                       win=self._install_win)

        try:
            vj = self._fetch_json(entry.get("url", ""))
            if not vj:
                raise RuntimeError(tr("拿不到这个版本的 JSON"))

            # 原版 + 可选加载器一整批塞进引擎。
            # ⚠️ 顺序、落点、两份 JSON 为什么要先落盘、加载器失败为什么不能
            # 拖累原版 —— 都在 `core/loader_install.install_stack()` 的说明里，
            # 模组页装整合包走的是同一个函数。
            from core.launch import rules_allow
            plan, loader_plan, loader_error = loader_install.install_stack(
                mc_id, vj, mc_dir, manager, version_id=name,
                loader_key=loader_key, loader_version=loader_version,
                api_version=api_version, rules_allow=rules_allow,
                fetch=self._fetch_json, vanilla_name=vanilla_name,
                extra=loader_extra)

            manager.start()
            finished = manager.wait_all(timeout=3600)

            # ⚠️ **失败的文件自动再试一轮**（网络抖动很常见）。不试的话就是
            # "68 个文件里挂了 1 个"→ 整个加载器不装了，用户得自己点下载窗口
            # 里那个「重试失败的文件」—— 而那时后台线程早跑完了，**重试也
            # 不会再跑安装器**（2026-09 真跑 Forge 时就是这么卡住的）。
            # 引擎内部本来就有单文件重试，这一轮是"全都试完还是失败"之后的兜底。
            if finished and manager.snapshot()["failed"]:
                self._note(tr("有文件没下下来，再试一轮…"), win=self._install_win)
                manager.retry_failed()
                manager.wait_all(timeout=1800)

            if finished:
                natives_dir = plan.version_dir / (vanilla_name + "-natives")
                install_mod.extract_natives(plan, natives_dir)

            # ⚠️ 这个标志在上面（搬回来之前）就记好了，见那里的注释
            vanilla_existed = vanilla_was_there

            snap = manager.snapshot()
            if not finished:
                self._install_result = tr("下载超时（还没下完）")
            elif snap["failed"]:
                self._install_result = tr("安装结束，但有 {n} 个文件失败",
                                          n=snap["failed"])
                self._install_ok = True        # 大部分装上了，版本列表值得刷
            elif snap["count"] == 0:
                self._install_result = tr("没有可下载的文件（版本 JSON 不对？）")
            elif loader_error:
                self._install_result = tr("原版装好了，但加载器没装上：{err}",
                                          err=loader_error)
                self._install_ok = True
            elif loader_plan is not None and loader_plan.installer:
                # Forge / NeoForge / OptiFine：文件下完了，**正戏才开始** ——
                # 得把官方安装器跑起来（要 Java，三分钟起）。这一段
                # 的进度走下载窗口下半截那个控制台（见 stage_begin）。
                self._install_result = self._run_installer_stage(
                    loader_plan, mc_dir, win=self._install_win)
                self._install_ok = True
            elif loader_plan is not None:
                what = loaders_mod.LOADER_NAMES.get(loader_key, loader_key)
                extra = ""
                if loader_plan.mods:
                    extra = tr("，含 {n} 个模组", n=len(loader_plan.mods))
                self._install_result = tr("安装完成：{name}（含 {loader}{extra}）",
                                          name=name, loader=what, extra=extra)
                self._install_ok = True
            else:
                self._install_result = tr("安装完成：{name}", name=name)
                self._install_ok = True

            # 加载器装上了 → **把两层合成一个独立目录**（用户 2026-09 要的：
            # "一个版本一个独立文件夹，分多版本才好管理"）。见
            # `core/standalone.py`：自包含 JSON + 硬链接的客户端 jar + 原版那层
            # 收进 `.mosslight/vanilla/`。
            if self._install_ok and loader_plan is not None and not loader_error:
                self._finalize_layout(name, mc_id, mc_dir, vanilla_existed,
                                      win=self._install_win)
        except Exception as e:                          # noqa: BLE001
            self._install_result = tr("安装失败：{err}", err=e)
        finally:
            self._install_done = True

    def _finalize_layout(self, version_id, mc_id, mc_dir, vanilla_existed,
                         win=None) -> None:
        """装完之后收尾：合两层 → 原版那层收起来

        ⚠️ 一步失败**不能**把整个安装判死：版本 JSON 还是能用的（顶多多留一层
        原版目录），所以这里只往控制台写一句，不改 `_install_result`。
        """
        if version_id == mc_id:
            return                    # 名字跟原版撞了，本来就是同一层
        ok, note = standalone.make_standalone(mc_dir, version_id, mc_id)
        if note:
            self._note(note, win=win)
        if not ok:
            return
        tail = standalone.cleanup_vanilla(mc_dir, mc_id,
                                          created_by_us=not vanilla_existed)
        if tail:
            self._note(tail, win=win)

    def _note(self, text: str, win=None) -> None:
        """往下载窗口下半截那个控制台写一句（没有窗口就丢掉）"""
        if win is not None and text:
            win.stage_write(text)

    def _run_installer_stage(self, loader_plan, mc_dir, win=None) -> str:
        """下载完之后跑官方安装器，返回给人看的结果那句话

        ⚠️ 这一步**能跑好几分钟**，而且跟我们自己的下载引擎没关系（它是
        一个 Java 子进程）。所以进度不走走下载窗口的进度条，走它下半截那个
        控制台 —— `stage_begin()` 一开，主线程那边的定时器就不会停了。
        """
        what = loaders_mod.LOADER_NAMES.get(loader_plan.key, loader_plan.key)
        if win is not None:
            win.stage_begin(tr("正在跑 {name} 的官方安装器…（要几分钟，别关这个窗口）",
                               name=what))
            win.stage_write(tr("安装器：{path}", path=loader_plan.installer_jar))

        def _line(text):
            if win is not None:
                win.stage_write(text)

        try:
            final, error = loader_install.finish_installer(
                loader_plan, mc_dir, on_line=_line,
                manager=self._install_manager)
        except Exception as e:                          # noqa: BLE001
            final, error = "", "%s: %s" % (type(e).__name__, e)

        if error:
            if win is not None:
                win.stage_end(tr("{name} 没装上", name=what))
                win.stage_write(tr("出了点问题：{err}", err=error))
            return tr("原版装好了，但 {name} 没装上：{err}", name=what, err=error)
        if win is not None:
            win.stage_end(tr("{name} 装好了", name=what))
            win.stage_write(tr("完成：{name}", name=final))
        return tr("安装完成：{name}（含 {loader}）", name=final, loader=what)

    def _poll_install(self):
        """主线程收结果：报状态、必要时刷新版本列表、放掉窗口"""
        if not self._install_done:
            return
        self._install_timer.stop()
        self._install_done = False
        self.install_page.set_status(self._install_result or tr("安装结束"))
        # 列表页那行"正在安装…"也要收掉 —— 现在用户多半**正看着列表**
        # （任务一开始就自动退回来了，见 _on_install_requested），
        # 不收的话那句话会一直挂在那儿，看着像还在装。
        self.status.setText(self._install_result or tr("安装结束"))

        if self._install_win is not None:
            self._install_win.stop()        # 停掉它的定时器再放掉引用
            self._install_win = None
        self._install_manager = None

        if self._install_ok:
            self._install_ok = False
            # 装好了：让主窗口重扫版本列表（新版本会出现在「版本」页）
            self.installed.emit()

    @staticmethod
    def _fetch_json(url: str):
        """取版本 JSON / 资源索引

        走 `core.install.http_json`：镜像优先 + 回退官方 + 重试。
        以前这里是一次裸的 requests.get(官方地址)，而这是整条链的入口 ——
        失败就整个安装失败，偏偏它没有镜像也没有重试。
        """
        if not url:
            return None
        return install_mod.http_json(url)

    # ---------- 语言 / 主题 ----------

    def refresh_theme(self):
        """分组标题的样式由 QSS 管，这里不用重刷（留着是为了跟主窗口的约定一致）"""

    def retranslate(self):
        super().retranslate()
        keep = self._current or "minecraft"
        self._loading = True
        self._fill_nav()
        self._select(keep)
        self._loading = False
        if self._grouped is not None:
            # 分组标题、状态行、行里的"发布于 …"都是生成的，重建一遍
            self.set_grouped(self._grouped, self._info)
        # 模组页自己画的文字（筛选卡、状态行、分组标题、提示行）也要重建，
        # 不然切完语言它还是旧语言
        self.mods_page.retranslate()


def _total(grouped: dict) -> int:
    return sum(len(items) for _title, items in grouped.get("groups", []))
