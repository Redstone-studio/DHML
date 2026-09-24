"""关于对话框

从设置页的「关于」卡片里弹出来。用对话框而不是页面：
这类内容一辈子看一次，没必要占一个常驻导航项。

对话框是模态的、而且每次都是新建的，所以不需要 retranslate()
（用户不可能在它开着的时候切语言）—— 和新档案对话框一个思路。
"""

from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout
)

from core.i18n import tr
from ui.widgets.about_content import AboutContent


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AboutDialog")
        self.setWindowTitle(tr("关于本软件"))
        # 640 宽时说明文字折得太碎，几乎一屏只能看一段；加宽到 760，
        # 中英日俄四种语言里最长的俄文也不用来回滚
        self.resize(760, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(0)

        # 内容比窗口高，必须能滚 —— 四种语言里文本长度差很多
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.content = AboutContent()
        scroll.setWidget(self.content)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(24, 0, 24, 0)
        buttons.addStretch()

        close_btn = QPushButton(tr("关闭"))
        close_btn.setObjectName("PrimaryButton")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)

        layout.addLayout(buttons)
