"""侧边栏：导航按钮 + 底部"当前档案"卡片

结构调整说明：旧版本把账户列表做成了一整列（固定占 ~250px 宽），
为三个账户常年占掉四分之一的窗口。现在账户收进独立页面，
侧边栏底部只留一张显示当前档案的卡片，点一下跳到账户页。

文案：静态文字用 self.label()；导航按钮因为要拼图标前缀，
在 _apply_nav_texts() 里手工处理（见 retranslate）。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
)

from core.accounts import type_label
from core.app_info import APP_NAME, APP_VERSION
from core.i18n import tr
from ui.translatable import TranslatableWidget


class _AccountChip(QFrame):
    """显示当前档案的小卡片，整块可点击"""

    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("AccountChip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._account = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(10)

        self.avatar = QLabel("?")
        self.avatar.setObjectName("AccountAvatar")
        self.avatar.setFixedSize(32, 32)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.avatar)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(0)
        self.name_label = QLabel()
        self.name_label.setObjectName("AccountChipName")
        self.type_label = QLabel()
        self.type_label.setObjectName("AccountChipType")
        text_box.addWidget(self.name_label)
        text_box.addWidget(self.type_label)
        layout.addLayout(text_box, 1)

        self.set_account(None)

    def set_account(self, account):
        """记住当前档案 —— 语言切换时要靠它重建这两行文字"""
        self._account = account
        if not account:
            self.avatar.setText("?")
            self.name_label.setText(tr("未选择档案"))
            self.type_label.setText(tr("点此新建"))
            return
        name = str(account.get("name", "?"))
        self.avatar.setText(name[:1].upper())
        self.name_label.setText(name)
        self.type_label.setText(type_label(account.get("type")))

    def retranslate(self):
        self.set_account(self._account)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class Sidebar(TranslatableWidget):
    page_changed = pyqtSignal(str)
    account_clicked = pyqtSignal()

    # (页面 key, 图标, 文案)
    # 图标全部用几何符号而不是 emoji：emoji 在缺字体的机器上会变豆腐块，
    # 这些符号在 Segoe UI Symbol 里一定有。
    NAV_ITEMS = (
        ("home", "\u25b6", "启动"),
        ("versions", "\u25a4", "版本"),
        ("accounts", "\u25c9", "账户"),
        ("personalize", "\u25d0", "个性化"),
        ("settings", "\u2699", "设置"),
    )  # noqa: i18n  —— 这些中文在 _apply_nav_texts 里过 tr()

    def __init__(self):
        super().__init__()
        self.setObjectName("Sidebar")
        # 关键：纯 QWidget 子类默认不绘制 QSS 里的 background-color / border，
        # 必须打开 WA_StyledBackground，否则 #Sidebar 的背景色和右边框全部无效
        # （表现为侧边栏露出窗口底色，比预期更黑）。
        # 卡片用的是 QFrame，QFrame 自带这个行为，所以一直正常。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(224)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 20, 14, 18)
        layout.setSpacing(4)

        # 产品名不翻译
        brand = QLabel(f"\u26cf  {APP_NAME}")
        brand.setObjectName("SidebarBrand")
        layout.addWidget(brand)
        layout.addSpacing(20)

        self.buttons = {}
        for key, _icon, _text in self.NAV_ITEMS:
            btn = QPushButton()
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked, k=key: self._on_nav(k))
            layout.addWidget(btn)
            self.buttons[key] = btn
        self._apply_nav_texts()

        layout.addStretch()

        layout.addWidget(self.label("当前档案", "SidebarSectionLabel"))

        self.account_chip = _AccountChip()
        self.account_chip.clicked.connect(self.account_clicked.emit)
        layout.addWidget(self.account_chip)

        layout.addSpacing(12)
        # 版本号不是文案
        version = QLabel(APP_VERSION)
        version.setObjectName("SidebarVersion")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

    # ---------- 文案 ----------

    def _apply_nav_texts(self):
        """导航按钮要拼图标前缀，所以没法用 self.label()，手工处理"""
        for key, icon, text in self.NAV_ITEMS:
            self.buttons[key].setText(f"  {icon}    {tr(text)}")

    def retranslate(self):
        super().retranslate()
        self._apply_nav_texts()
        self.account_chip.retranslate()

    # ---------- 对外 ----------

    def set_active(self, key: str):
        """只更新按钮选中状态，不发信号（避免初始化时递归）"""
        for k, btn in self.buttons.items():
            btn.setChecked(k == key)

    def set_account(self, account):
        self.account_chip.set_account(account)

    # ---------- 内部 ----------

    def _on_nav(self, key: str):
        self.set_active(key)
        self.page_changed.emit(key)
