"""版本页：本地版本清单（卡片式）

从 QTableWidget 换成 QListWidget + 自定义行控件，原因有两个：
1. 表格选中一行时，每个单元格各画各的选中背景，高亮会被切成好几段，观感很差
2. 卡片式一行能放下"名称 + 元信息 + 徽章"，跟 PCL2 的版本列表观感一致

两个 Qt 细节（都踩过）：
- 行控件是普通 QWidget 子类，默认不绘制 QSS 背景 —— 正好让 QListWidget 的
  选中底色透出来，不用额外设透明
- 行控件必须设 WA_TransparentForMouseEvents，否则鼠标事件被它吃掉，
  点上去根本选不中（setItemWidget 的经典坑）
"""

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget
)

from core.versions import TYPE_LABELS, VersionScanner

# 分组顺序 + 标题
GROUPS = (
    ("vanilla", "官方版本"),
    ("loader", "加载器版本"),
    ("pack", "整合包"),
)

KIND_FILTERS = (("all", "全部"),) + GROUPS

ROW_HEIGHT = 62

_BADGE_NAMES = {
    "vanilla": "BadgeVanilla",
    "loader": "BadgeLoader",
    "pack": "BadgePack",
}


class _VersionRow(QWidget):
    """一行版本：左侧色块徽章 + 名称/元信息 + 右侧标签"""

    def __init__(self, version: dict):
        super().__init__()
        # 让点击穿透到 QListWidget，否则选中功能整个失效
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 12, 6)
        layout.setSpacing(12)

        badge = QLabel(self._badge_text(version))
        badge.setObjectName(_BADGE_NAMES.get(version["kind"], "Badge"))
        badge.setFixedSize(38, 38)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
            tag = QLabel("\u26a0 缺少 jar")
            tag.setObjectName("BadgeWarn")
            layout.addWidget(tag)
        elif version["loader_label"]:
            tag = QLabel(version["loader_label"])
            tag.setObjectName("BadgeAccent")
            layout.addWidget(tag)

    @staticmethod
    def _badge_text(version: dict) -> str:
        if version["kind"] == "pack":
            return "包"
        if version["kind"] == "loader":
            return (version["loader"] or "")[:2].upper()
        return "原"

    @staticmethod
    def _meta_text(version: dict) -> str:
        parts = [TYPE_LABELS.get(version["type"], version["type"])]
        if version["java_major"]:
            parts.append(f"Java {version['java_major']}")
        if version["isolated"]:
            parts.append("版本隔离")
        if version["dir_name"] != version["display_name"]:
            parts.append(version["dir_name"])
        return "    ·    ".join(parts)


class VersionsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.scanner = VersionScanner()
        self.versions = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(8)

        title = QLabel("版本管理")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self.subtitle = QLabel()
        self.subtitle.setObjectName("PageSubtitle")
        layout.addWidget(self.subtitle)

        layout.addSpacing(14)

        # ---------- 工具条 ----------
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索版本名…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 1)

        self.kind_filter = QComboBox()
        for key, text in KIND_FILTERS:
            self.kind_filter.addItem(text, key)
        self.kind_filter.setMinimumWidth(130)
        self.kind_filter.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.kind_filter)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.reload_versions)
        bar.addWidget(self.refresh_btn)

        layout.addLayout(bar)

        # ---------- 卡片列表 ----------
        self.list = QListWidget()
        self.list.setObjectName("VersionList")
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list, 1)

        self.footer = QLabel()
        self.footer.setObjectName("HintText")
        layout.addWidget(self.footer)

        self.reload_versions()

    # ---------- 数据 ----------

    def reload_versions(self):
        self.versions = self.scanner.scan()
        self._apply_filter()

    def reset_scanner(self):
        """游戏目录变了以后重建 scanner 并重扫"""
        self.scanner = VersionScanner()
        self.reload_versions()

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
            self.subtitle.setText(f"没有扫描到版本 —— 当前游戏目录：{self.scanner.mc_dir}")
        elif len(rows) == total:
            self.subtitle.setText(f"共 {total} 个本地版本")
        else:
            self.subtitle.setText(f"共 {total} 个本地版本，符合筛选的 {len(rows)} 个")

    # ---------- 列表构建 ----------

    def _fill_list(self, rows):
        self.list.clear()
        first_version_row = None

        for kind, title in GROUPS:
            group = [v for v in rows if v["kind"] == kind]
            if not group:
                continue
            self._add_group_header(title, len(group))
            for version in group:
                self._add_version_item(version)
                if first_version_row is None:
                    first_version_row = self.list.count() - 1

        if first_version_row is not None:
            self.list.setCurrentRow(first_version_row)
        self.footer.setText("")

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

    def _on_selection_changed(self, current, _previous):
        version = current.data(Qt.ItemDataRole.UserRole) if current else None
        if isinstance(version, dict):
            self.footer.setText(f"位置：{version['path']}")
        else:
            self.footer.setText("")
