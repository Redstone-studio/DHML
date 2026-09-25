"""侧边栏：导航按钮 + 底部版本号

⚠️ 样式全靠**主项目那套 QSS 的 objectName**（`#Sidebar` / `#NavButton` /
`#SidebarBrand` / `#SidebarVersion`）。改动 objectName 就等于脱离主项目风格，
所以名字一个都别改。

⚠️ 纯 QWidget 子类**默认不画 QSS 的 background-color / border**，
必须打开 `WA_StyledBackground`，否则 `#Sidebar` 的底色和右边框全部无效
（表现成侧边栏露出窗口底色）。主项目踩过这个坑，这里照抄处理。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel, QPushButton, QVBoxLayout, QWidget
)

from core.app_info import APP_NAME, APP_VERSION


class Sidebar(QWidget):
    page_changed = pyqtSignal(str)

    # (页面 key, 图标, 文案)
    # ⚠️ 图标用**几何符号**而不是 emoji：emoji 在缺字体的机器上会变豆腐块，
    # 这些符号在 Segoe UI Symbol 里一定有（主项目的规矩）
    NAV_ITEMS = (
        ("verify", "\u2714", "正版验证"),
        ("accounts", "\u263a", "账户"),
        ("log", "\u2261", "运行日志"),
        ("settings", "\u2699", "设置"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(224)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 20, 14, 18)
        layout.setSpacing(4)

        brand = QLabel(f"\u26cf  {APP_NAME}")
        brand.setObjectName("SidebarBrand")
        layout.addWidget(brand)
        layout.addSpacing(20)

        self.buttons = {}
        for key, icon, text in self.NAV_ITEMS:
            btn = QPushButton(f"  {icon}    {text}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked, k=key: self._on_nav(k))
            layout.addWidget(btn)
            self.buttons[key] = btn

        layout.addStretch()

        hint = QLabel("实验项目 · 不含主程序功能")
        hint.setObjectName("SidebarSectionLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(12)
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("SidebarVersion")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

    def set_active(self, key: str):
        """只改选中状态，不发信号（初始化时用，避免递归）"""
        for k, btn in self.buttons.items():
            btn.setChecked(k == key)

    def _on_nav(self, key: str):
        self.set_active(key)
        self.page_changed.emit(key)
