"""启动页

结构调整：玩家名不再是一个可编辑的输入框。
旧代码里 main_window 会把当前账户名回填到 QLineEdit，但启动时读的又是输入框
里的文本 —— 用户可以随便改成非法名字，绕过 NewAccountDialog 的正则校验。
v0.2.0 拼 --username / --uuid / --accessToken 时必须来自 AccountManager，
所以这里把"当前档案"做成只读展示，从源头拆掉这个雷。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget
)

from core.accounts import TYPE_LABELS
from core.versions import TYPE_LABELS as VERSION_TYPE_LABELS, VersionScanner

# 下拉框里的分段顺序
GROUP_TITLES = (
    ("vanilla", "官方版本"),
    ("loader", "加载器版本"),
    ("pack", "整合包"),
)


class HomePage(QWidget):
    def __init__(self, account_manager):
        super().__init__()
        self.account_manager = account_manager
        self.scanner = VersionScanner()
        self.versions = []
        self._last_selected_id = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(6)

        title = QLabel("启动游戏")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        subtitle = QLabel("选择版本和档案，然后启动")
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(subtitle)

        layout.addSpacing(16)

        # 扫描告警条（没有问题时隐藏）
        self.banner = QLabel()
        self.banner.setObjectName("WarningBanner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        layout.addWidget(self.banner)
        layout.addSpacing(6)

        # ---------- 主卡片 ----------
        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 20, 22, 20)
        card_layout.setSpacing(14)

        # 版本选择
        version_row = QHBoxLayout()
        version_row.setSpacing(10)
        version_label = QLabel("游戏版本")
        version_label.setObjectName("FieldLabel")
        version_label.setFixedWidth(64)
        version_row.addWidget(version_label)

        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(320)
        self.version_combo.currentIndexChanged.connect(self._on_version_changed)
        version_row.addWidget(self.version_combo, 1)

        self.refresh_btn = QPushButton("\u21bb")
        self.refresh_btn.setObjectName("IconButton")
        self.refresh_btn.setFixedSize(34, 34)
        self.refresh_btn.setToolTip("重新扫描本地版本")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.reload_versions)
        version_row.addWidget(self.refresh_btn)
        card_layout.addLayout(version_row)

        # 选中版本的详情徽章
        self.badge_row = QHBoxLayout()
        self.badge_row.setSpacing(8)
        self.badge_row.addStretch()
        card_layout.addLayout(self.badge_row)

        # 当前档案（只读）
        account_row = QHBoxLayout()
        account_row.setSpacing(10)
        account_label = QLabel("当前档案")
        account_label.setObjectName("FieldLabel")
        account_label.setFixedWidth(64)
        account_row.addWidget(account_label)
        self.account_value = QLabel("未选择档案")
        account_row.addWidget(self.account_value)
        account_row.addStretch()
        card_layout.addLayout(account_row)

        card_layout.addSpacing(6)

        self.launch_btn = QPushButton("\u25b6    启动游戏")
        self.launch_btn.setObjectName("LaunchButton")
        self.launch_btn.setFixedHeight(54)
        self.launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch_btn.clicked.connect(self._on_launch)
        card_layout.addWidget(self.launch_btn)

        layout.addWidget(card)
        layout.addStretch()

        self.reload_versions()

    # ---------- 版本列表 ----------

    def reload_versions(self):
        self.versions = self.scanner.scan()
        self._fill_combo()
        self._update_banner()

    def reset_scanner(self):
        """配置变更（游戏目录换了）之后重建 scanner 并重扫"""
        self.scanner = VersionScanner()
        self.reload_versions()

    def _fill_combo(self):
        # 记住当前选中的版本，刷新后按 id 恢复，不要无脑跳回第一项
        previous_id = self._last_selected_id

        self.version_combo.blockSignals(True)
        self.version_combo.clear()

        if not self.versions:
            self.version_combo.addItem("未找到本地版本")
            self.version_combo.setEnabled(False)
            self.launch_btn.setEnabled(False)
            self.version_combo.blockSignals(False)
            self._set_badges([])
            return

        self.version_combo.setEnabled(True)
        self.launch_btn.setEnabled(True)

        restore_index = None
        for kind, _title in GROUP_TITLES:
            items = [v for v in self.versions if v["kind"] == kind]
            if not items:
                continue
            if self.version_combo.count():
                self.version_combo.insertSeparator(self.version_combo.count())
            for version in items:
                self.version_combo.addItem(self._label_for(version), version)
                index = self.version_combo.count() - 1
                if restore_index is None and version["id"] == previous_id:
                    restore_index = index

        if restore_index is not None:
            self.version_combo.setCurrentIndex(restore_index)
        else:
            self.version_combo.setCurrentIndex(0)

        self.version_combo.blockSignals(False)
        self._on_version_changed()

    @staticmethod
    def _label_for(version) -> str:
        marks = []
        if version["loader_label"]:
            marks.append(version["loader_label"])
        if version["kind"] == "pack":
            marks.append("整合包")
        if not version["complete"]:
            marks.append("\u26a0 不完整")
        name = version["display_name"]
        return f"{name}    · {', '.join(marks)}" if marks else name

    def _on_version_changed(self):
        version = self.version_combo.currentData()
        if not isinstance(version, dict):
            self._set_badges([])
            return

        self._last_selected_id = version["id"]

        entries = [(VERSION_TYPE_LABELS.get(version["type"], version["type"]), "Badge")]
        if version["loader_label"]:
            entries.append((version["loader_label"], "BadgeAccent"))
        entries.append(
            (f"Java {version['java_major']}" if version["java_major"] else "Java 未知", "Badge")
        )
        if version["isolated"]:
            entries.append(("版本隔离", "Badge"))
        if version["complete"]:
            entries.append(("可启动", "BadgeAccent"))
        else:
            entries.append(("\u26a0 版本不完整", "BadgeWarn"))

        self._set_badges(entries)

    def _set_badges(self, entries):
        """重建徽章行。entries: list[(文字, objectName)]"""
        while self.badge_row.count():
            item = self.badge_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for text, object_name in entries:
            label = QLabel(text)
            label.setObjectName(object_name)
            self.badge_row.addWidget(label)
        self.badge_row.addStretch()

    def _update_banner(self):
        errors = self.scanner.errors
        if not errors:
            self.banner.hide()
            return
        extra = f"（另有 {len(errors) - 1} 条）" if len(errors) > 1 else ""
        self.banner.setText(f"\u26a0  {len(errors)} 个版本读不了：{errors[0]}{extra}")
        self.banner.show()

    # ---------- 档案 ----------

    def set_account(self, account):
        if not account:
            self.account_value.setText("未选择档案 —— 去「账户」页新建一个")
            return
        type_label = TYPE_LABELS.get(account.get("type"), "未知")
        self.account_value.setText(f"{account.get('name', '?')}    ·    {type_label}")

    # ---------- 启动 ----------

    def _on_launch(self):
        version = self.version_combo.currentData()
        if not isinstance(version, dict):
            return

        account = self.account_manager.get_current()
        if not account:
            QMessageBox.warning(self, "还没有档案", "请先在「账户」页新建一个档案。")
            return

        if not version["complete"]:
            QMessageBox.warning(
                self, "版本不完整",
                f"「{version['display_name']}」缺少客户端 jar，无法启动。\n\n"
                f"请用 PCL / HMCL 重新安装该版本。"
            )
            return

        # v0.2.0 在这里拼 Java 命令并拉起进程。
        # 下面这些字段就是拼命令要用的东西。
        print("[TODO] 启动游戏")
        print(f"  版本 id : {version['id']}")
        print(f"  版本目录: {version['path']}")
        print(f"  客户端  : {version['jar']}")
        print(f"  游戏目录: {version['game_dir']}")
        print(f"  需要 Java: {version['java_major']}")
        print(f"  档案    : {account['name']} / uuid={account.get('uuid')}")
