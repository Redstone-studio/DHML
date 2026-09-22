from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QLineEdit
)
from PyQt6.QtCore import Qt

from core.versions import VersionScanner


class HomePage(QWidget):
    def __init__(self):
        super().__init__()
        self.scanner = VersionScanner()
        self.versions = []   # 缓存扫描结果

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        title = QLabel("启动游戏")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        # ---------- 版本选择 ----------
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(QLabel("游戏版本:"))

        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(280)
        row_layout.addWidget(self.version_combo)

        # 刷新按钮
        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setObjectName("RefreshButton")
        self.refresh_btn.setFixedSize(32, 32)
        self.refresh_btn.setToolTip("重新扫描本地版本")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.reload_versions)
        row_layout.addWidget(self.refresh_btn)

        row_layout.addStretch()
        layout.addWidget(row)

        # ---------- 玩家名 ----------
        user_row = QWidget()
        user_layout = QHBoxLayout(user_row)
        user_layout.setContentsMargins(0, 0, 0, 0)
        user_layout.addWidget(QLabel("玩家名:"))
        self.username_input = QLineEdit("Steve")
        self.username_input.setMinimumWidth(200)
        user_layout.addWidget(self.username_input)
        user_layout.addStretch()
        layout.addWidget(user_row)

        layout.addSpacing(20)

        # ---------- 启动按钮 ----------
        self.launch_btn = QPushButton("▶  启动游戏")
        self.launch_btn.setObjectName("LaunchButton")
        self.launch_btn.setFixedHeight(52)
        self.launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch_btn.clicked.connect(self._on_launch)
        layout.addWidget(self.launch_btn)

        layout.addStretch()

        # 首次加载
        self.reload_versions()

    # ---------- 版本列表 ----------

    def reload_versions(self):
        """重新扫描本地版本，刷新下拉框"""
        self.version_combo.clear()
        self.versions = self.scanner.scan()

        if not self.versions:
            self.version_combo.addItem("未找到本地版本")
            self.version_combo.setEnabled(False)
            self.launch_btn.setEnabled(False)
            # 提示信息
            if self.scanner.errors:
                print("[版本扫描] 警告:")
                for e in self.scanner.errors:
                    print(f"  - {e}")
            return

        self.version_combo.setEnabled(True)
        self.launch_btn.setEnabled(True)

        for v in self.versions:
            # 显示名 + 类型标记
            label = self._format_label(v)
            self.version_combo.addItem(label, userData=v)

        # 默认选最新正式版（列表已排序，第一个就是）
        self.version_combo.setCurrentIndex(0)

        def reset_scanner(self):
        """配置变更后重建 scanner"""
        from core.versions import VersionScanner
        self.scanner = VersionScanner()
        self.reload_versions()
        
    @staticmethod
    def _format_label(v: dict) -> str:
        """把版本信息格式化成下拉框里的文字"""
        name = v["display_name"]
        marks = []
        if v["loader"]:
            # loader 字段是小写 key，展示用大写
            marks.append(v["loader"].capitalize())
        if v["kind"] == "pack":
            marks.append("整合包")
        if not v["complete"]:
            marks.append("⚠ 不完整")

        if marks:
            return f"{name}  [{', '.join(marks)}]"
        return name

    # ---------- 启动（暂未实现） ----------

    def _on_launch(self):
        v = self.version_combo.currentData()
        if v is None:
            print("[启动] 未选择版本")
            return
        print(f"[TODO] 启动游戏")
        print(f"  版本: {v['id']}")
        print(f"  类型: {v['type']} / {v['kind']}")
        print(f"  加载器: {v['loader']}")
        print(f"  游戏目录: {v['game_dir']}")
        print(f"  是否隔离: {v['isolated']}")
        print(f"  Java 要求: {v['java_major']}")
        print(f"  玩家名: {self.username_input.text()}")
