"""版本页：本地版本清单

旧版本这个页面只有一个"（待实现）"标签，现在做成真正的清单 + 搜索 + 筛选。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from core.versions import TYPE_LABELS, VersionScanner

KIND_FILTERS = (
    ("all", "全部"),
    ("vanilla", "官方版本"),
    ("loader", "加载器版本"),
    ("pack", "整合包"),
)

KIND_LABELS = {
    "vanilla": "官方",
    "loader": "加载器",
    "pack": "整合包",
}

COLUMNS = ("名称", "类型", "加载器", "Java", "状态")

_COLOR_OK = QColor("#8fd49a")
_COLOR_WARN = QColor("#e0b341")
_COLOR_DIM = QColor("#6e7076")


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
        self.kind_filter.setMinimumWidth(120)
        self.kind_filter.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.kind_filter)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.reload_versions)
        bar.addWidget(self.refresh_btn)

        layout.addLayout(bar)
        layout.addSpacing(6)

        # ---------- 表格 ----------
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        self.empty_label = QLabel("没有找到本地版本")
        self.empty_label.setObjectName("EmptyState")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        layout.addWidget(self.empty_label)

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

        self._fill_table(rows)

        total = len(self.versions)
        if not total:
            self.subtitle.setText(f"没有扫描到版本 —— 当前游戏目录：{self.scanner.mc_dir}")
        elif len(rows) == total:
            self.subtitle.setText(f"共 {total} 个本地版本")
        else:
            self.subtitle.setText(f"共 {total} 个本地版本，符合筛选的 {len(rows)} 个")

    def _fill_table(self, rows):
        self.table.setRowCount(0)

        for version in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            name_item = QTableWidgetItem(version["display_name"])
            name_item.setToolTip(str(version["path"]))
            self.table.setItem(row, 0, name_item)

            type_text = TYPE_LABELS.get(version["type"], version["type"])
            kind_text = KIND_LABELS.get(version["kind"], "")
            type_item = QTableWidgetItem(f"{type_text} · {kind_text}" if kind_text else type_text)
            type_item.setForeground(_COLOR_DIM)
            self.table.setItem(row, 1, type_item)

            loader_item = QTableWidgetItem(version["loader_label"] or "—")
            loader_item.setForeground(_COLOR_OK if version["loader_label"] else _COLOR_DIM)
            self.table.setItem(row, 2, loader_item)

            java_item = QTableWidgetItem(
                str(version["java_major"]) if version["java_major"] else "—"
            )
            java_item.setForeground(_COLOR_DIM)
            self.table.setItem(row, 3, java_item)

            if version["complete"]:
                status_item = QTableWidgetItem("可启动")
                status_item.setForeground(_COLOR_OK)
            else:
                status_item = QTableWidgetItem("\u26a0 缺少 jar")
                status_item.setForeground(_COLOR_WARN)
                status_item.setToolTip("客户端 jar 不在，无法启动；请用 PCL / HMCL 重新安装")
            self.table.setItem(row, 4, status_item)

        self.table.setVisible(bool(rows))
        self.empty_label.setVisible(not rows)
        if not rows:
            self.empty_label.setText(
                "没有符合筛选条件的版本" if self.versions else "没有扫描到本地版本"
            )
