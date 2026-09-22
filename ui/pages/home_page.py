from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QComboBox
)
from PyQt6.QtCore import Qt


class HomePage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        title = QLabel("启动游戏")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        # 版本选择
        row = QWidget()
        from PyQt6.QtWidgets import QHBoxLayout
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(QLabel("游戏版本:"))
        self.version_combo = QComboBox()
        self.version_combo.addItems(["1.20.4", "1.20.1", "1.19.4"])
        self.version_combo.setMinimumWidth(200)
        row_layout.addWidget(self.version_combo)
        row_layout.addStretch()
        layout.addWidget(row)

        # 用户名（离线模式占位）
        user_row = QWidget()
        user_layout = QHBoxLayout(user_row)
        user_layout.setContentsMargins(0, 0, 0, 0)
        user_layout.addWidget(QLabel("玩家名:"))
        from PyQt6.QtWidgets import QLineEdit
        self.username_input = QLineEdit("Steve")
        self.username_input.setMinimumWidth(200)
        user_layout.addWidget(self.username_input)
        user_layout.addStretch()
        layout.addWidget(user_row)

        layout.addSpacing(20)

        # 启动按钮
        self.launch_btn = QPushButton("▶  启动游戏")
        self.launch_btn.setObjectName("LaunchButton")
        self.launch_btn.setFixedHeight(52)
        self.launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch_btn.clicked.connect(self._on_launch)
        layout.addWidget(self.launch_btn)

        layout.addStretch()

    def _on_launch(self):
        print(f"[TODO] 启动 {self.version_combo.currentText()} "
              f"玩家 {self.username_input.text()}")
