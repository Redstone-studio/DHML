"""主窗口：侧边栏 + 页面堆栈"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QWidget

from core import i18n, theme
from core.accounts import AccountManager
from core.app_info import APP_DISPLAY_NAME
from core.config import config
from core.resources import resource_path, stylesheet_path
from ui.dialogs.new_account_dialog import NewAccountDialog
from ui.icons import app_icon
from ui.pages.accounts_page import AccountsPage
from ui.pages.home_page import HomePage
from ui.pages.personalize_page import PersonalizePage
from ui.pages.settings_page import SettingsPage
from ui.pages.versions_page import VersionsPage
from ui.widgets.sidebar import Sidebar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 窗口标题用**完整名**：Windows 的任务栏、Alt+Tab 显示的就是它，
        # 界面上自己叫的名字（侧边栏左上角）短一点，是 APP_NAME
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1280, 820)
        self.setMinimumSize(1040, 620)

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
            "personalize": PersonalizePage(),
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
        # 版本页也能换游戏目录（设置页那张卡片已经搬过去了）
        versions_page = self.pages["versions"]
        versions_page.config_changed.connect(self._on_config_changed)
        # 版本页选中的版本 = 启动页面板上的版本（两个页面共用一个"当前版本"）
        versions_page.version_selected.connect(self.pages["home"].set_version)
        # 启动页点「选择版本」→ 切到版本页
        self.pages["home"].versions_requested.connect(
            lambda: self.switch_page("versions"))

        # 主题和语言改到"个性化"页了
        personalize = self.pages["personalize"]
        personalize.language_changed.connect(self.set_language)
        personalize.theme_changed.connect(self.load_styles)

        # 系统深浅色变了就重刷（只有"跟随系统"模式才真的会动样式）
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(
                self._on_system_color_scheme_changed
            )
        except AttributeError:
            pass    # 老版本 Qt 没这个 API，那就只能跟随不了系统

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
        # 切到版本页 / 启动页时刷新一下（版本列表和面板上的信息都来自扫描）
        if key in ("versions", "home"):
            page.reload_versions()

    # ---------- 语言 ----------

    def set_language(self, lang: str):
        """切换界面语言（设置页调用）

        做法是"逐页重设文字"，**不是**重建页面：

        - 不重建控件 → 不会丢状态（当前页、选中的版本、搜索词都还在）
        - 不会打断以后 v0.2 启动 / v0.4 下载这类长任务

        代价是每个页面自己实现 retranslate()，其中动态生成的文字
        （列表项、下拉项、带变量的文案）要自己重建。
        静态文字只要是 self.label() / self.button() 建的，基类会自动重设。
        """
        if lang == i18n.current_language():
            return
        i18n.set_language(lang)
        config.set("language", lang)

        self.sidebar.retranslate()
        for page in self.pages.values():
            page.retranslate()

    # ---------- 样式 / 主题 ----------

    def _system_is_dark(self) -> bool:
        """系统用的是深色还是浅色（Qt 6.5+ 才有 colorScheme）"""
        try:
            return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except AttributeError:
            return True     # 拿不到就默认深色

    def current_theme_mode(self) -> str:
        """把配置里的 system/dark/light 落到实际的 dark/light"""
        return theme.effective_mode(config.get("theme", "system"), self._system_is_dark())

    def closeEvent(self, event):
        """关窗口时收拾后台线程

        ⚠️ Qt 的规矩：**销毁一个还在运行的 QThread 会让 Qt 直接终止进程**
        （连 Python 的异常都来不及抛，stdout 缓冲也全丢）。所以：
          - 扫描 Java 这种"可等"的任务：等一下，通常几百毫秒就完
          - 启动游戏的任务：**不能等**（关了启动器游戏还要继续跑），
            所以把它从父对象上摘下来，让 Qt 别去销毁它
        """
        for page in getattr(self, "pages", {}).values():
            scan = getattr(page, "_java_scan", None)
            if scan is not None and scan.isRunning():
                scan.wait(3000)

            launch = getattr(page, "_task", None)
            if launch is not None and launch.isRunning():
                launch.setParent(None)      # 摘出去：进程退出时别带着它一起销毁

            repair = getattr(page, "_repair_task", None)
            if repair is not None and repair.isRunning():
                repair.wait(3000)

        super().closeEvent(event)

    def load_styles(self):
        """加载样式表

        两步替换：
          1. @ICONS@ → 图标目录的绝对路径（Qt 的 url() 相对路径是按工作目录解析的，
                       打包后工作目录一变就找不到图）
          2. @变量@  → core/theme.py 里对应主题的实际颜色
        最后检查有没有漏网的变量 —— 写错名字的话 Qt 会静默忽略那条规则，
        界面会悄悄少个样式，很难查。
        """
        path = stylesheet_path()
        try:
            qss = path.read_text(encoding="utf-8")
        except OSError as e:
            # 不静默吞掉：样式丢了是肉眼可见的问题，至少留条线索
            print(f"[UI] 样式加载失败: {path} ({e})")
            return

        qss = qss.replace("@ICONS@", resource_path("assets", "icons").as_posix())

        mode = self.current_theme_mode()
        qss = theme.resolve(qss, theme.palette(mode, config.get("accent_color", "")))

        left = theme.unresolved(qss)
        if left:
            print(f"[UI] app.qss 里有没被替换的变量（写错名字了？）: {left}")

        self.setStyleSheet(qss)

        # 自绘 / 手写格式的控件不吃 QSS（开关、日志行的级别颜色），
        # 得让它们自己重刷一遍。用鸭子类型而不是写死类型，页面按需实现即可。
        for page in self.pages.values():
            refresh = getattr(page, "refresh_theme", None)
            if callable(refresh):
                refresh()

    def _on_system_color_scheme_changed(self, *_args):
        """系统深浅色变了。只有"跟随系统"时才有必要重刷"""
        if config.get("theme", "system") == "system":
            self.load_styles()

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
