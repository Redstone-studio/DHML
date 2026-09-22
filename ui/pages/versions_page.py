from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt


class VersionsPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)

        title = QLabel("版本管理")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        hint = QLabel("（待实现：下载、删除、导入版本）")
        hint.setObjectName("PageHint")
        layout.addWidget(hint)

        layout.addStretch()
