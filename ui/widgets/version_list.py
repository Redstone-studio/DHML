"""版本卡片列表（搜索 + 筛选 + 分组）

首页和「版本管理」页用的是**同一个** —— 列表只有一份实现，免得两边各改各的
慢慢长歪（跟"主界面和日志窗口各显示一份日志"那个坑是一回事）。

从 ui/pages/versions_page.py 搬过来的，下面这两条 Qt 细节依然有效：

1. 行控件是普通 QWidget 子类，默认不绘制 QSS 背景 —— 正好让 QListWidget 的
   选中底色透出来，不用额外设透明
2. 行控件必须设 WA_TransparentForMouseEvents，否则鼠标事件被它吃掉，
   点上去根本选不中（setItemWidget 的经典坑）

信号：
    selection_changed(dict | None)   选中的版本变了
    activated(dict)                  双击 / 回车（首页拿它直接启动）
"""

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QVBoxLayout, QWidget
)

from core.i18n import tr
from core.versions import VersionScanner
from core.versions import type_label as version_type_label
from ui.icons import version_pixmap
from ui.translatable import TranslatableWidget

# (kind, 分组标题的源文案)
GROUPS = (
    ("vanilla", "官方版本"),
    ("loader", "加载器版本"),
    ("pack", "整合包"),
)  # noqa: i18n  —— 这些中文在 _add_group_header / _fill_kind_filter 里过 tr()

KIND_FILTERS = (("all", "全部"),) + GROUPS  # noqa: i18n

ROW_HEIGHT = 62

# 左边那个方块的边长（有图标时图比它小一圈，留点呼吸）
BADGE_SIZE = 38

_BADGE_NAMES = {
    "vanilla": "BadgeVanilla",
    "loader": "BadgeLoader",
    "pack": "BadgePack",
}

# 徽章里的一两个字（对应上面的 kind）
_BADGE_TEXTS = {
    "vanilla": "原",
    "loader": None,     # None = 用加载器名缩写
    "pack": "包",
}  # noqa: i18n  —— 在 _badge_text 里过 tr()


class _VersionRow(QWidget):
    """一行版本：左侧色块徽章 + 名称/元信息 + 右侧标签"""

    def __init__(self, version: dict):
        super().__init__()
        # 让点击穿透到 QListWidget，否则选中功能整个失效
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 12, 6)
        layout.setSpacing(12)

        badge = QLabel()
        badge.setFixedSize(BADGE_SIZE, BADGE_SIZE)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = version_pixmap(version, BADGE_SIZE - 6)
        if pixmap is not None:
            # 有版本类型图（草方块 / 圆石 / 铁砧 / …）就用图；用的是透明底，
            # 所以 objectName 换成 BadgeIcon，别套那个彩色圆角方块
            badge.setObjectName("BadgeIcon")
            badge.setPixmap(pixmap)
        else:
            badge.setObjectName(_BADGE_NAMES.get(version["kind"], "Badge"))
            badge.setText(self._badge_text(version))
        layout.addWidget(badge)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)

        name = QLabel(version["display_name"])
        name.setObjectName("VersionRowName")
        text_box.addWidget(name)

        meta = QLabel(self._meta_text(version))
        meta.setObjectName("VersionRowMeta")
        text_box.addWidget(meta)

        layout.addLayout(text_box, 1)

        if not version["complete"]:
            tag = QLabel(tr("⚠ 缺少 jar"))
            tag.setObjectName("BadgeWarn")
            layout.addWidget(tag)
        elif version["loader_label"]:
            tag = QLabel(version["loader_label"])
            tag.setObjectName("BadgeAccent")
            layout.addWidget(tag)

    @staticmethod
    def _badge_text(version: dict) -> str:
        text = _BADGE_TEXTS.get(version["kind"])
        if text is None:
            return (version["loader"] or "")[:2].upper()
        return tr(text)

    @staticmethod
    def _meta_text(version: dict) -> str:
        parts = [version_type_label(version["type"])]
        if version["java_major"]:
            parts.append(f"Java {version['java_major']}")
        if version["isolated"]:
            parts.append(tr("版本隔离"))
        if version["dir_name"] != version["display_name"]:
            parts.append(version["dir_name"])
        return "    ·    ".join(parts)


class VersionList(TranslatableWidget):
    """版本清单。自己管扫描、筛选和分组，外面只管接信号"""

    selection_changed = pyqtSignal(object)      # dict 或 None
    activated = pyqtSignal(object)              # 双击 / 回车

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scanner = VersionScanner()
        self.versions = []
        self._selected_id = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---------- 工具条 ----------
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.bind(self.search, "搜索版本名…", "placeholderText")
        bar.addWidget(self.search, 1)

        self.kind_filter = QComboBox()
        self.kind_filter.setMinimumWidth(130)
        self.kind_filter.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.kind_filter)
        self._fill_kind_filter()

        self.refresh_btn = self.button("刷新")
        self.refresh_btn.clicked.connect(self.reload)
        bar.addWidget(self.refresh_btn)

        root.addLayout(bar)

        # ---------- 卡片列表 ----------
        self.list = QListWidget()
        self.list.setObjectName("VersionList")
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._on_selection_changed)
        self.list.itemActivated.connect(self._on_activated)
        root.addWidget(self.list, 1)

        self.footer = QLabel()
        self.footer.setObjectName("HintText")
        root.addWidget(self.footer)

        self.reload()

    # ---------- 给外面用的 ----------

    def current(self):
        """当前选中的版本；分组标题或空列表时返回 None"""
        item = self.list.currentItem()
        version = item.data(Qt.ItemDataRole.UserRole) if item else None
        return version if isinstance(version, dict) else None

    def reload(self):
        self.versions = self.scanner.scan()
        self._apply_filter()
        return self.versions

    def reset_scanner(self):
        """游戏目录变了以后重建 scanner 并重扫"""
        self.scanner = VersionScanner()
        return self.reload()

    def select_id(self, version_id: str) -> bool:
        """按版本 id 选中。找不到就选第一个版本行；返回是否选中了指定那个"""
        self._selected_id = version_id or ""
        found = self._select_row()
        return found

    # ---------- 语言切换 ----------

    def retranslate(self):
        super().retranslate()
        self._fill_kind_filter()
        self._apply_filter()        # 分组标题、列表行、脚注都要重建

    def _fill_kind_filter(self):
        """筛选下拉项的文字要跟着语言走（选项本身不变，只换文字）"""
        index = self.kind_filter.currentIndex()
        self.kind_filter.blockSignals(True)
        self.kind_filter.clear()
        for key, text in KIND_FILTERS:
            self.kind_filter.addItem(tr(text), key)
        self.kind_filter.setCurrentIndex(max(0, index))
        self.kind_filter.blockSignals(False)

    # ---------- 筛选与构建 ----------

    def _apply_filter(self):
        keyword = self.search.text().strip().lower()
        kind = self.kind_filter.currentData()

        rows = []
        for version in self.versions:
            if kind != "all" and version["kind"] != kind:
                continue
            if keyword and keyword not in version["display_name"].lower():
                continue
            rows.append(version)

        self._fill_list(rows)

        total = len(self.versions)
        if not total:
            # 一个版本都没有：把游戏目录报出来，不然用户不知道去哪放版本
            self.footer.setText(
                tr("没有扫描到版本 —— 当前游戏目录：{path}", path=self.scanner.mc_dir))
        elif len(rows) == total:
            self.footer.setText(tr("共 {n} 个本地版本", n=total))
        else:
            self.footer.setText(
                tr("共 {n} 个本地版本，符合筛选的 {m} 个", n=total, m=len(rows)))

    def _fill_list(self, rows):
        # 重建期间屏蔽信号：clear() 会先报一次"没选中"，选中新行又报一次，
        # 外面就会看到闪一下的空状态。整段建完再统一报一次。
        self.list.blockSignals(True)
        self.list.clear()
        for kind, title in GROUPS:
            group = [v for v in rows if v["kind"] == kind]
            if not group:
                continue
            self._add_group_header(tr(title), len(group))
            for version in group:
                self._add_version_item(version)
        self.list.blockSignals(False)

        self._select_row()

    def _select_row(self) -> bool:
        """尽量保住当前选中：按 id 找，找不到选第一个版本行"""
        want = self._selected_id or (self.current() or {}).get("id", "")
        first = None
        for i in range(self.list.count()):
            version = self.list.item(i).data(Qt.ItemDataRole.UserRole)
            if not isinstance(version, dict):
                continue
            if first is None:
                first = i
            if want and version["id"] == want:
                self.list.setCurrentRow(i)
                self._emit_selection()
                return True
        if first is not None:
            self.list.setCurrentRow(first)
            self._emit_selection()
            return False
        self._emit_selection()
        return False

    def _add_group_header(self, title: str, count: int):
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)      # 分组标题不可选中
        item.setSizeHint(QSize(0, 36))
        self.list.addItem(item)

        label = QLabel(f"{title}    {count}")
        label.setObjectName("VersionGroupTitle")
        self.list.setItemWidget(item, label)

    def _add_version_item(self, version: dict):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, version)
        item.setSizeHint(QSize(0, ROW_HEIGHT))
        item.setToolTip(str(version["path"]))
        self.list.addItem(item)
        self.list.setItemWidget(item, _VersionRow(version))

    # ---------- 信号 ----------

    def _emit_selection(self):
        version = self.current()
        if version:
            self._selected_id = version["id"]
        self.selection_changed.emit(version)

    def _on_selection_changed(self, current, _previous):
        version = current.data(Qt.ItemDataRole.UserRole) if current else None
        if isinstance(version, dict):
            self._selected_id = version["id"]
        self.selection_changed.emit(version if isinstance(version, dict) else None)

    def _on_activated(self, item):
        version = item.data(Qt.ItemDataRole.UserRole) if item else None
        if isinstance(version, dict):
            self.activated.emit(version)
