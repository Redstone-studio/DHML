from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)

        title = QLabel("设置")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        hint = QLabel("（待实现：Java 路径、内存分配、账户管理）")
        hint.setObjectName("PageHint")
        layout.addWidget(hint)

        layout.addStretch()
