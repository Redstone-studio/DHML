"""空页面占位

侧边栏那几个还没做的页面用它顶着 —— 比"点了没反应"清楚，
也比留一个空白页强（用户会以为是坏了）。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PlaceholderPage(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")
        layout.addWidget(title_label)

        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("PageSubtitle")
            sub.setWordWrap(True)
            layout.addWidget(sub)

        layout.addSpacing(20)

        empty = QLabel("这个页面还没做")
        empty.setObjectName("EmptyState")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(empty, 1)
