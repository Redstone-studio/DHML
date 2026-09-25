"""
主窗口（空壳）

样式**完全走主项目那套**：`assets/styles/app.qss` + `parts/*.qss`，
由 `core/resources.py` 拼起来，再用 `core/theme.py` 把 `@变量@` 换成实际颜色。
这三件事和主项目是同一份代码（直接复制过来的），所以观感一致。

改样式的正确姿势：
  · 加规则/改规则 → 改 `assets/styles/parts/*.qss`，**颜色一律用 @变量@**
  · 换配色       → 改 `core/theme.py` 的 DARK / LIGHT
  · 不要在这里 setStyleSheet 里写死颜色
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout, QMainWindow, QStackedWidget, QWidget
)

from core import theme
from core.app_info import APP_DISPLAY_NAME
from core.resources import load_stylesheet, resource_path
from ui.pages.accounts_page import AccountsPage
from ui.pages.log_page import LogPage
from ui.pages.placeholder_page import PlaceholderPage
from ui.pages.verify_page import VerifyPage
from ui.widgets.sidebar import Sidebar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1080, 720)

        root = QWidget()
        root.setObjectName("Root")          # app.qss 里 #Root 管窗口底色
        self.setCentralWidget(root)

        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.page_changed.connect(self.switch_page)
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.pages = {
            "verify": VerifyPage(),
            "accounts": AccountsPage(),
            "log": LogPage(),
            "settings": PlaceholderPage(
                "设置",
                "配置目录、超时、皮肤站选择这些。现在是空的。"),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        # 登录成功之后账户页要立刻能看到新账号
        self.pages["verify"].account_changed.connect(self.pages["accounts"].reload)

        self.sidebar.set_active("verify")
        self.stack.setCurrentWidget(self.pages["verify"])

        self.load_styles()

    # ---------- 页面 ----------

    def switch_page(self, key: str):
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)

    # ---------- 样式 ----------

    def load_styles(self):
        """读样式 → 替换变量 → setStyleSheet

        三步替换（和主项目一致）：
          1. `@ICONS@` → 图标目录的**绝对路径**
             （Qt 的 `url()` 相对路径是按工作目录解析的，换个目录启动就找不到图）
          2. `@变量@`  → core/theme.py 里对应主题的实际颜色
          3. 检查有没有漏网的变量
        """
        qss = load_stylesheet()
        if not qss.strip():
            # 一份样式都没读到：这时**别把空样式设进去**，
            # 否则是"全白"而不是"没样式"，更难看也更难查
            print("[UI] 没读到任何样式片段，检查 assets/styles/")
            return

        qss = qss.replace("@ICONS@", resource_path("assets", "icons").as_posix())
        qss = theme.resolve(qss, theme.palette(self.current_theme_mode()))

        left = theme.unresolved(qss)
        if left:
            # 写错变量名的话 Qt 会**静默忽略**那条规则，界面悄悄少个样式
            print(f"[UI] 有没被替换的变量（名字写错了？）: {left}")

        self.setStyleSheet(qss)

        for page in self.pages.values():
            refresh = getattr(page, "refresh_theme", None)
            if callable(refresh):
                refresh()

    def current_theme_mode(self) -> str:
        """现在用深色还是浅色

        这个实验先写死深色 —— 主项目那套"跟随系统"要读 Qt 的 colorScheme
        并监听变化，等真需要了再把那段搬过来（`theme.effective_mode` 已经在了）。
        """
        return "dark"

    # ---------- 关闭 ----------

    def closeEvent(self, event):
        """关窗口前把还在跑的线程停掉

        ⚠️ 微软登录的轮询线程可能还在转。不停的话它会在窗口销毁之后
        继续往界面发信号 —— Qt 会直接崩（不是抛异常，是进程没了）。
        """
        for page in self.pages.values():
            stop = getattr(page, "stop_workers", None)
            if callable(stop):
                try:
                    stop()
                except Exception as e:
                    print(f"[UI] 停线程失败：{type(e).__name__}: {e}")
        super().closeEvent(event)
