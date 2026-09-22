from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QStackedWidget
)
from ui.widgets.sidebar import Sidebar
from ui.widgets.account_list import AccountList
from ui.pages.home_page import HomePage
from ui.pages.versions_page import VersionsPage
from ui.pages.settings_page import SettingsPage
from ui.dialogs.new_account_dialog import NewAccountDialog
from core.accounts import AccountManager


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MC Launcher")
        self.resize(1000, 640)
        self.setMinimumSize(800, 520)

        # ---------- 1. 中央部件 & 主布局 ----------
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---------- 2. 侧边栏 ----------
        self.sidebar = Sidebar()

        # ---------- 3. 账户管理 ----------
        self.account_manager = AccountManager()
        self.account_list = AccountList(self.account_manager)
        self.account_list.add_requested.connect(self.open_new_account_dialog)
        self.account_list.account_selected.connect(self.on_account_changed)

       # ---------- 4. 页面堆栈 ----------
        self.stack = QStackedWidget()
        self.pages = {
            "home": HomePage(),
            "versions": VersionsPage(),
            "settings": SettingsPage(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        # ---------- 4b. 设置页改了配置 → 首页刷新 ----------
        self.pages["settings"].config_changed.connect(self._on_config_changed)

        # ---------- 5. 加入布局（顺序：侧边栏 | 账户列表 | 内容） ----------
        layout.addWidget(self.sidebar)
        layout.addWidget(self.account_list)
        layout.addWidget(self.stack, 1)

        # ---------- 6. 信号连接 ----------
        self.sidebar.page_changed.connect(self.switch_page)

        # ---------- 7. 加载样式 ----------
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

    def open_new_account_dialog(self):
        dlg = NewAccountDialog(self)
        if dlg.exec():
            if dlg.result_account:
                typ, name = dlg.result_account
                self.account_manager.add_offline(name)
                self.account_list.refresh()
                self.on_account_changed(name)

    def on_account_changed(self, name: str):
        print(f"[UI] 切换账户: {name}")
        home = self.pages["home"]
        if hasattr(home, "username_input"):
            home.username_input.setText(name)
