from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QStackedWidget
)
from PyQt6.QtCore import Qt
from ui.widgets.sidebar import Sidebar
from ui.pages.home_page import HomePage
from ui.pages.versions_page import VersionsPage
from ui.pages.settings_page import SettingsPage


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MC Launcher")
        self.resize(1000, 640)
        self.setMinimumSize(800, 520)

        # 中央部件
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 侧边栏
        self.sidebar = Sidebar()

        # 页面堆栈
        self.stack = QStackedWidget()
        self.pages = {
            "home": HomePage(),
            "versions": VersionsPage(),
            "settings": SettingsPage(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack, 1)

        # 侧边栏切换页面
        self.sidebar.page_changed.connect(self.switch_page)

        self.load_styles()

    def switch_page(self, key: str):
        if key in self.pages:
            self.stack.setCurrentWidget(self.pages[key])

    def load_styles(self):
        try:
            with open("assets/styles/dark.qss", "r", encoding="utf-8") as f:
                self.setStyleSheet(f.read())
        except FileNotFoundError:
            pass
