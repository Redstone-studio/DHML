"""主窗口：侧边栏 + 页面堆栈"""

from PyQt6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QWidget

from core.accounts import AccountManager
from core.app_info import APP_NAME
from core.resources import resource_path, stylesheet_path
from ui.dialogs.new_account_dialog import NewAccountDialog
from ui.pages.accounts_page import AccountsPage
from ui.pages.home_page import HomePage
from ui.pages.settings_page import SettingsPage
from ui.pages.versions_page import VersionsPage
from ui.widgets.sidebar import Sidebar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1080, 700)
        self.setMinimumSize(900, 580)

        self.account_manager = AccountManager()

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar()
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        layout.addWidget(self.stack, 1)

        self.pages = {
            "home": HomePage(self.account_manager),
            "versions": VersionsPage(),
            "accounts": AccountsPage(self.account_manager),
            "settings": SettingsPage(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        # ---------- 信号 ----------
        self.sidebar.page_changed.connect(self.switch_page)
        self.sidebar.account_clicked.connect(lambda: self.switch_page("accounts"))

        accounts_page = self.pages["accounts"]
        accounts_page.accounts_changed.connect(self._on_accounts_changed)
        accounts_page.add_requested.connect(self.open_new_account_dialog)

        self.pages["settings"].config_changed.connect(self._on_config_changed)

        # ---------- 初始状态 ----------
        self.sidebar.set_account(self.account_manager.get_current())
        self.pages["home"].set_account(self.account_manager.get_current())
        self.switch_page("home")

        self.load_styles()

    # ---------- 导航 ----------

    def switch_page(self, key: str):
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(key)
        # 切到版本页时刷新一下（那里显示的是完整清单）
        if key == "versions":
            page.reload_versions()

    # ---------- 样式 ----------

    def load_styles(self):
        """加载 QSS

        路径交给 core/resources.py 统一解析（打包后是 <exe>/_internal/assets/...）。
        绝对不要写 open("assets/styles/dark.qss") —— 那是 0.0.x 系列里
        样式一直不生效的根因，而且异常被静默吞掉之后极难排查。
        """
        path = stylesheet_path()
        try:
            qss = path.read_text(encoding="utf-8")
        except OSError as e:
            # 不静默吞掉：样式丢了是肉眼可见的问题，至少留条线索
            print(f"[UI] 样式加载失败: {path} ({e})")
            return

        # QSS 里的 @ICONS@ 要换成图标目录的绝对路径。
        # Qt 的 url() 相对路径是按「当前工作目录」解析的，不是按 qss 文件位置，
        # 打包成 exe 之后工作目录一变图标就全丢了。
        qss = qss.replace("@ICONS@", resource_path("assets", "icons").as_posix())
        self.setStyleSheet(qss)

    # ---------- 账户 ----------

    def open_new_account_dialog(self):
        dlg = NewAccountDialog(self)
        if dlg.exec():
            if dlg.result_account:
                _type, name = dlg.result_account
                self.account_manager.add_offline(name)
                self._on_accounts_changed()

    def _on_accounts_changed(self):
        """账户列表变了：账户页、侧边栏卡片、首页的档案显示都要跟着更新"""
        current = self.account_manager.get_current()
        self.pages["accounts"].reload()
        self.sidebar.set_account(current)
        self.pages["home"].set_account(current)

    # ---------- 配置 ----------

    def _on_config_changed(self):
        """设置页改了游戏目录：所有跟版本相关的页面都要重新扫描"""
        self.pages["home"].reset_scanner()
        self.pages["versions"].reset_scanner()
