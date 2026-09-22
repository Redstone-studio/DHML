from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel, QFrame
)
from PyQt6.QtCore import pyqtSignal, Qt


class Sidebar(QWidget):
    page_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 24, 16, 24)
        layout.setSpacing(8)

        # Logo / 标题
        title = QLabel("⛏ MC Launcher")
        title.setObjectName("SidebarTitle")
        layout.addWidget(title)

        layout.addSpacing(24)

        # 导航按钮
        self.buttons = {}
        nav_items = [
            ("home", "🏠  启动"),
            ("versions", "📦  版本"),
            ("settings", "⚙️  设置"),
        ]
        for key, text in nav_items:
            btn = QPushButton(text)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._on_click(k))
            layout.addWidget(btn)
            self.buttons[key] = btn

        layout.addStretch()

        # 版本号
        version = QLabel("v0.1.0-dev")
        version.setObjectName("SidebarVersion")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

        # 默认选中首页
        self._on_click("home")

    def _on_click(self, key: str):
        for k, btn in self.buttons.items():
            btn.setChecked(k == key)
        self.page_changed.emit(key)
