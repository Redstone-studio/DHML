"""主窗口：侧边栏 + 页面堆栈"""

from PyQt6.QtCore import Qt, QPropertyAnimation, QPoint, QEasingCurve
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QWidget

from core import i18n, theme
from core.accounts import AccountManager
from core.app_info import APP_DISPLAY_NAME
from core.config import config
from core.resources import load_stylesheet, resource_path
from ui.dialogs.new_account_dialog import NewAccountDialog
from ui.icons import app_icon
from ui.pages.accounts_page import AccountsPage
from ui.pages.download_page import DownloadPage
from ui.pages.home_page import HomePage
from ui.pages.personalize_page import PersonalizePage
from ui.pages.settings_page import SettingsPage
from ui.pages.version_settings_page import VersionSettingsPage
from ui.pages.versions_page import VersionsPage
from ui.widgets import background_canvas, card_style
from ui.widgets.sidebar import Sidebar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 窗口标题用**完整名**：Windows 的任务栏、Alt+Tab 显示的就是它，
        # 界面上自己叫的名字（侧边栏左上角）短一点，是 APP_NAME
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1360, 860)
        self.setMinimumSize(1160, 660)

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

        # ---------- 背景画布（移植自 experiments/Downloading mod test）----------
        # 把整个界面装进一个会自己画背景图/背景色的容器里（见
        # `ui/widgets/background_canvas.py`）。⚠️ 必须在 setCentralWidget(root)
        # 之后做 —— `attach()` 会把当前的中央控件摘下来挪进画布，再让画布当中央控件。
        # ⚠️ 还得让页面透明（app.qss 里 QWidget 不再填页面色），不然背景图会被
        # 页面自己的底色整个盖掉 —— 实验项目踩过这个坑（第 6 条）。
        self.canvas = background_canvas.attach(self)

        self.pages = {
            "home": HomePage(self.account_manager),
            "download": DownloadPage(),
            "versions": VersionsPage(),
            # 版本设置是**整页**不是弹窗（2026-09 改）：从启动面板或版本页进来，
            # 侧边栏不给它入口 —— 它属于"某一个版本"，不是第四个导航项
            "version_settings": VersionSettingsPage(),
            "accounts": AccountsPage(self.account_manager),
            "personalize": PersonalizePage(),
            "settings": SettingsPage(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        # 「版本设置」页的「返回」回到**进来的那一页**
        self._settings_return = "versions"
        # 页面切换动画（见 _slide_page）；必须在第一次 switch_page 之前建好
        self._page_anim = None

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
        # 在版本页双击一个版本 → 直接回启动页（选中已经跟着走了）
        versions_page.version_activated.connect(self._on_version_activated)
        # 启动页点「选择版本」→ 切到版本页
        self.pages["home"].versions_requested.connect(
            lambda: self.switch_page("versions"))

        # 「版本设置」：两个入口都导航到那一页（不再是弹窗）
        version_settings_page = self.pages["version_settings"]
        self.pages["home"].version_settings_requested.connect(
            self.open_version_settings)
        versions_page.version_settings_requested.connect(self.open_version_settings)
        version_settings_page.back_requested.connect(
            lambda: self.switch_page(self._settings_return))
        version_settings_page.version_pick_requested.connect(
            lambda: self.switch_page("versions"))
        version_settings_page.accounts_requested.connect(
            lambda: self.switch_page("accounts"))
        # 那边改了 Java / 内存：启动面板上那行元信息要跟着刷新
        version_settings_page.saved.connect(self.pages["home"].refresh_panel)
        # 下载页装完一个版本 → 版本列表和启动面板都要重扫
        download_page = self.pages["download"]
        download_page.installed.connect(self._on_installed)

        # 主题和语言改到"个性化"页了
        personalize = self.pages["personalize"]
        personalize.language_changed.connect(self.set_language)
        personalize.theme_changed.connect(self.load_styles)
        # 个性化页里的「背景设置」改游戏目录时也要重扫（跟设置页 / 版本页同一条路）
        personalize.config_changed.connect(self._on_config_changed)

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
        self.pages["version_settings"].set_account(self.account_manager.get_current())
        self.switch_page("home")

        self.load_styles()

    # ---------- 导航 ----------

    # 页面切换动画：新页面从右边滑进来（移植自实验项目的 main_window）
    SLIDE_MS = 240

    def switch_page(self, key: str):
        page = self.pages.get(key)
        if page is None:
            return
        previous = self.stack.currentWidget()
        first_show = previous is None
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(key)
        # 切到版本页 / 启动页时刷新一下（版本列表和面板上的信息都来自扫描）
        if key in ("versions", "home"):
            page.reload_versions()
        # 新页面横向滑进来（第一次显示不滑 —— 开机那一下整页飞进来很怪；
        # 关了动效也不滑 —— 整页位移动画是最吃渲染的一处）
        if not first_show and previous is not page and self._anim_enabled():
            self._slide_page(page)

    def _anim_enabled(self) -> bool:
        """动效总开关（"关闭动效省性能"）。关掉时切页直接摆到位，不建动画"""
        from core import appearance
        return appearance.get_anim_enabled()

    def _slide_page(self, page):
        """让 `page` 从右边（窗口外）滑到位

        ⚠️ 三件事都不能省（实验项目里逐条试出来的，见它的使用说明第 8 节）：
          1. 整页横向移动是"每帧重画整页"，最吃渲染 —— 动画期间
             `setUpdatesEnabled(False)`，Qt 就只在结束时画一次，掉帧明显少
          2. 动画**必须先 stop 掉上一条**：上一条没播完就切页，两条会打架
          3. 结束时把位置钳在 (0, 0) 并把重绘打开 —— 漏了就会"页面再也不刷新"
        """
        if self._page_anim is not None:
            self._page_anim.stop()
            self._page_anim = None
        page.setUpdatesEnabled(False)
        anim = QPropertyAnimation(page, b"pos", self)
        anim.setDuration(self.SLIDE_MS)
        anim.setStartValue(QPoint(page.width() or self.width(), 0))
        anim.setEndValue(QPoint(0, 0))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: self._finish_slide(page))
        self._page_anim = anim
        anim.start()

    def _finish_slide(self, page):
        page.move(0, 0)
        page.setUpdatesEnabled(True)
        page.update()
        self._page_anim = None

    def apply_appearance(self):
        """外观设置改完调它：背景重画 + 卡片透明度按新值重套

        ⚠️ 卡片是各自 setStyleSheet 的（透明度写在那份样式里），不挨个刷新的话
        只有**新加载出来**的卡片是新透明度 —— 用户会以为设置没生效。
        """
        if getattr(self, "canvas", None) is not None:
            self.canvas.reload()
        for page in self.pages.values():
            refresh = getattr(page, "refresh_card_style", None)
            if callable(refresh):
                refresh()
        self.load_styles()          # 卡片透明度也可能写在全局样式里

    def _on_installed(self):
        """装完一个新版本：跟"改了游戏目录"一样，重扫所有跟版本相关的页面"""
        self._on_config_changed()

    def _on_version_activated(self, version: dict):
        """版本页里双击（或回车）了一个版本：回启动页

        `switch_page("home")` 会重扫一遍并按 config 里的 last_version 选中 ——
        而双击那一下已经把 last_version 写好了（versions_page._on_selected），
        所以这里什么都不用再设，直接切过去，面板上就是刚双击的那个版本。
        """
        if not version:
            return
        self.switch_page("home")

    def open_version_settings(self, version: dict):
        """打开某个版本的设置页

        「返回」回到进来的那一页（启动面板和版本页各有一个入口），
        侧边栏的高亮也留在那一页 —— 这是个子页，不是第四个导航项。
        """
        if not version:
            return
        page = self.pages["version_settings"]
        current = self.stack.currentWidget()
        for key, candidate in self.pages.items():
            if candidate is current and key != "version_settings":
                self._settings_return = key
                break
        # 首页已经扫过 Java 了，白给它用，省一次全盘扫描
        page.set_javas(self.pages["home"].javas())
        page.set_version(version)
        page.set_tab(0)
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(self._settings_return)

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

        样式分在 `assets/styles/app.qss` + `assets/styles/parts/*.qss` 里，
        由 core/resources.py 的 load_stylesheet() 按顺序拼起来（QSS 没有 @import）。
        然后三步替换：
          1. @ICONS@ → 图标目录的绝对路径（Qt 的 url() 相对路径是按工作目录解析的，
                       打包后工作目录一变就找不到图）
          2. @ICONS_SUFFIX@ → 主题相关的图标后缀（深色主题是空串、浅色是 "-light"）
                       ⚠️ Qt 的 SVG **不支持 currentColor**，颜色只能写死在文件里，
                       所以每个要在 QSS 里用的图标都得有 `-dark` / `-light` 两份
                       （见 ui/icons.py 里同一套约定）
          3. @变量@  → core/theme.py 里对应主题的实际颜色
        最后检查有没有漏网的变量 —— 写错名字的话 Qt 会静默忽略那条规则，
        界面会悄悄少个样式，很难查。

        ⚠️ 这个函数在**主题变化时会被重新调用**（见 _on_system_color_scheme_changed
        和设置页），所以替换出来的后缀会跟着更新，不用担心换成浅色后还指着深色图标。
        """
        qss = load_stylesheet()
        if not qss.strip():
            # 一份样式都没读到：这次别把空样式表设进去，不然是"全白"而不是"没样式"
            print("[UI] 没读到任何样式片段，检查 assets/styles/")
            return

        mode = self.current_theme_mode()
        # ⚠️ 顺序：先 @ICONS@（它只是换路径），再 @ICONS_SUFFIX@。
        # 反过来的话 `@ICONS_SUFFIX@` 会被先换掉，结果一样，但读起来更容易搞混。
        qss = qss.replace("@ICONS@", resource_path("assets", "icons").as_posix())
        # 深色主题的图标就是原名（不带后缀），浅色主题带 -light —— 见上面第 2 条
        qss = qss.replace("@ICONS_SUFFIX@", "" if mode == "dark" else "-light")
        qss = theme.resolve(qss, theme.palette(mode, config.get("accent_color", "")))

        left = theme.unresolved(qss)
        if left:
            print(f"[UI] app.qss 里有没被替换的变量（写错名字了？）: {left}")

        # 卡片透明度：追加一段覆盖块（排在最后才压得住原来的 @bg_card@）。
        # 不透明度 100% 时它是空串 —— 别白写一段样式。
        qss += card_style.opacity_stylesheet(
            palette=theme.palette(mode, config.get("accent_color", "")))
        # 外壳（左侧竖栏 / 分类栏）半透明：只在设了背景图时有内容。
        # ⚠️ 排在卡片那段**后面**：外壳有自己固定的不透明度，不该被
        # "卡片透明度"那一档带走。
        qss += card_style.chrome_stylesheet(
            palette=theme.palette(mode, config.get("accent_color", "")))

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
        """账户列表变了：账户页、侧边栏卡片、首页和版本设置页都要跟着更新"""
        current = self.account_manager.get_current()
        self.pages["accounts"].reload()
        self.sidebar.set_account(current)
        self.pages["home"].set_account(current)
        self.pages["version_settings"].set_account(current)

    # ---------- 配置 ----------

    def _on_config_changed(self):
        """设置页改了游戏目录：所有跟版本相关的页面都要重新扫描"""
        self.pages["home"].reset_scanner()
        self.pages["versions"].reset_scanner()
        # 版本设置页上"跟随全局设置"的项要立刻反映新的全局值
        self.pages["version_settings"].refresh_from_global()
