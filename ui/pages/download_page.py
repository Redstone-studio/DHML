"""下载页（**目前只有壳**）

左侧分类照 PCL 的下载页摆：

    Minecraft              原版 / 快照 / 远古版本
    社区资源               模组 / 整合包 / 数据包 / 资源包 / 光影包 / 世界
    收藏夹                 收藏的版本和资源
    安装包                 Minecraft / OptiFine / Forge / NeoForge / Cleanroom /
                          Fabric / Legacy Fabric / Quilt / LabyMod / LiteLoader

⚠️ **核心下载功能还没写**（用户 2026-09：核心功能还在优化测试，暂时不写入）。
所以右侧现在只说明"这个分类将来是什么 + 还没做"，
**故意不画假的版本列表** —— 摆一堆点了没反应的行，用户会以为下载坏了。

几个加载器的研究状态（用户标注的，等技术方案定了再决定做不做）：
    Cleanroom / Legacy Fabric / Quilt —— 没研究
    LabyMod —— 没研究出来
    LiteLoader —— 太老，不知道怎么下

图标：`assets/icons/version/` 里那套已经能直接复用（`version_icon_name()`），
等右侧真的列版本时直接用，不用再做一套。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout
)

from core.i18n import tr
from ui.theme_state import palette
from ui.translatable import TranslatableWidget

NAV_WIDTH = 200

# 左侧分类。分组标题写字符串，`None` 表示"这一项自己就是一级、没有子项"
# （用户给的清单里「收藏夹」就是这种）。
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

# 右侧那句"将来这里是什么"。对着分类说一句，用户就知道点了会得到什么；
# 全都没做，所以只有这一句 + 一句"还没做"。
DETAILS = {
    "minecraft": "原版、快照和远古版本的可下载清单",
    "mods": "按游戏版本和加载器筛 Mod，从 Modrinth 之类的来源取",
    "modpacks": "整合包（会连带把加载器和配置一起装好）",
    "datapacks": "数据包",
    "resourcepacks": "资源包",
    "shaders": "光影包",
    "worlds": "世界存档",
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


class DownloadPage(TranslatableWidget):
    def __init__(self):
        super().__init__()
        # 构造期间别让"选中第一项"触发 _select（那时右侧控件还没建）
        self._loading = True
        self._current = ""          # 当前选中的分类 key

        layout = QHBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        layout.addWidget(self._make_nav())
        layout.addWidget(self._make_body(), 1)

        self._loading = False       # 右侧建好了才放行信号
        self._select("minecraft")

    # ---------- 左侧分类 ----------

    def _make_nav(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        card.setFixedWidth(NAV_WIDTH)
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 16, 14, 16)
        box.setSpacing(8)

        box.addWidget(self.label("下载", "PageTitle"))

        self.nav = QListWidget()
        # 借版本列表那套卡片行样式（选中高亮、悬停都现成）
        self.nav.setObjectName("VersionList")
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.currentItemChanged.connect(self._on_nav_changed)
        box.addWidget(self.nav, 1)

        self._fill_nav()
        return card

    def _fill_nav(self):
        """填分类。分组标题是**不可选**的（点它不该把右边切走）"""
        self.nav.blockSignals(True)
        self.nav.clear()
        # 分组标题：**别用 self.palette().mid()** —— 深色主题下那是个几乎等于
        # 背景的灰，字直接看不见（用户 2026-09 报过）。用主题自己的 text_dim，
        # 字号跟副标题一个量级、不再往小缩（缩小是当初看不见的另一个原因）。
        # 注意这里**不设字体**：全局 QSS 的 `QWidget { font-size: 14px }`
        # 会盖掉 setFont()，标题的字号只能由 `#DownloadNavGroup` 那条 QSS 决定
        # （ID 选择器优先级高于 QWidget，见 assets/styles/parts/30-badges.qss）。

        first = None
        for group, items in NAV:
            if group:
                head = QListWidgetItem()
                head.setFlags(Qt.ItemFlag.NoItemFlags)      # 不可选、不可点
                label = QLabel(group)                        # 内容其实是这个标签
                label.setObjectName("DownloadNavGroup")
                label.setContentsMargins(10, 10, 10, 6)
                # ⚠️ 量尺寸得用**真实字号**：QSS 的 font-size 不会进
                # label.font()，不设的话 sizeHint 按 14px 算，而实际画 17px，
                # 行高不够就把字上下切掉（用户截图：字被挤）。
                # 这个字号要和 #DownloadNavGroup 那条 QSS 保持一致。
                _f = QFont()
                _f.setPixelSize(17)
                _f.setBold(True)
                label.setFont(_f)
                head.setSizeHint(label.sizeHint())
                self.nav.addItem(head)                       # ★ 先 addItem
                self.nav.setItemWidget(head, label)          # ★ 再挂控件
            for key, text in items:
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.nav.addItem(item)
                if first is None:
                    first = item
        self.nav.blockSignals(False)
        if first is not None:
            self.nav.setCurrentItem(first)

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
        self.body_title.setText(name)
        detail = DETAILS.get(key, "")
        self.body_detail.setText(tr(detail) if detail else "")
        self.body_hint.setText(tr(
            "下载功能还没做（计划在 v0.4）。这里将来会列出可下载的版本，"
            "点一下就装进当前游戏目录。"))

    # ---------- 右侧 ----------

    def _make_body(self) -> QFrame:
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

    def refresh_theme(self):
        """主窗口换主题时会调（分组标题的颜色是代码设的，不吃 QSS，得重刷）"""
        self._fill_nav()

    # ---------- 语言切换 ----------

    def retranslate(self):
        super().retranslate()
        # 分组标题是动态塞进 QListWidget 的，得重建；右边那几行也是生成的
        self._fill_nav()
        self._select(self._current or "minecraft")
