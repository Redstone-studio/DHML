"""
主窗口
- QStackedWidget 管理"搜索页 / 详情页"
- 切换时播放左右滑动动画
"""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QStackedWidget
)
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QPoint
)
from ui.search_page import SearchPage
from ui.detail_page import DetailPage
from ui.dialogs.appearance_dialog import AppearanceSettingsDialog
from ui.widgets import background_canvas


class MainWindow(QMainWindow):
    # 页面切换动画：两个方向的时长
    SLIDE_IN_MS = 280      # 进详情（从右滑入）
    SLIDE_OUT_MS = 240     # 返回（向右滑出）
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mosslight Launcher")
        self.resize(1000, 800)
        self.setStyleSheet("""
            QMainWindow { background-color: #1b1c1f; }
            /* ⚠️ 这两条是"自定义背景能不能看见"的关键：
               QStackedWidget 和各个页面默认会用调色板的 window 色**不透明地**
               填满自己，把下面的背景图整个盖掉（实测设红色图片只显示出深灰）。
               让它们透明，背景才透得上来。页面本身没设过背景色，所以是安全的。 */
            QStackedWidget { background: transparent; }
            QStackedWidget > QWidget { background: transparent; }
            QLabel { color: #e9e9ec; }
            QLineEdit, QComboBox {
                background-color: #2a2c30; color: #e9e9ec;
                border: 1px solid #3a3c42; border-radius: 6px;
                padding: 6px 10px; font-size: 12px;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                background-color: #2a2c30;
                color: #e9e9ec;
                selection-background-color: #3f8f4b;
            }
        """)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # 背景画布：把整个界面装进一个会画背景图的容器里。
        # 必须在 setCentralWidget(stack) 之后做 —— attach() 会把当前的中央控件
        # 摘下来挪进画布，再让画布当中央控件。
        self.canvas = background_canvas.attach(self)

        self.search_page = SearchPage()
        self.detail_page = DetailPage()

        self.stack.addWidget(self.search_page)   # index 0
        self.stack.addWidget(self.detail_page)   # index 1

        self.search_page.open_detail.connect(self.open_detail)
        self.search_page.open_background.connect(self.open_background_settings)
        self.detail_page.back_requested.connect(self.go_back)

        self._anim = None

    def open_background_settings(self):
        """打开背景设置（改完立刻生效，画布会自己重画）"""
        AppearanceSettingsDialog(self, canvas=self.canvas).exec()

    def refresh_card_style(self):
        """把两页里已经建出来的卡片按当前透明度重新套一遍样式

        设置界面改了透明度会调它 —— 卡片是各自 setStyleSheet 的，
        不挨个刷新的话只有新加载出来的卡片才是新透明度。
        """
        self.search_page.refresh_card_style()
        self.detail_page.refresh_card_style()

    def open_detail(self, hit: dict):
        """搜索页 → 详情页（详情从右滑入）

        ⚠️ 先用搜索结果预填卡片再启动动画：详情接口要 1~3 秒，不预填的话
        页面滑进来时还挂着**上一个 mod** 的内容，看着就像"显示了上一个页面"。
        """
        slug = hit.get("slug") or hit.get("project_id") or hit.get("title")
        self.detail_page.prefetch(hit)
        self.detail_page.load(slug)

        w = self.width()
        self.detail_page.move(w, 0)
        self.stack.setCurrentWidget(self.detail_page)
        self._slide_page(w, 0, self.SLIDE_IN_MS, QEasingCurve.Type.OutCubic)

    def go_back(self):
        """详情页 → 搜索页（详情向右滑出）"""
        w = self.width()
        self._slide_page(0, w, self.SLIDE_OUT_MS, QEasingCurve.Type.InCubic,
                         on_done=self._after_back)

    def _slide_page(self, x_from: int, x_to: int, duration: int, curve,
                    on_done=None):
        """整页横向滑动的统一入口

        ⚠️ 动画期间**关掉重绘**（`setUpdatesEnabled(False)`）：
        整页移动是"每帧重画整个页面"的场景，最吃渲染。
        关掉之后 Qt 只在结束时画一次，掉帧明显减少（之前实测帧间隔被拉到 40~70ms，
        看着就是抖）。结束时一定要打开，否则页面就不再刷新了。

        顺带把"上一条动画没播完就又切页"处理掉了：先 stop 再建新的。
        """
        if self._anim is not None:
            self._anim.stop()
            try:
                self._anim.finished.disconnect()
            except TypeError:
                pass

        self.detail_page.setUpdatesEnabled(False)
        self._anim = QPropertyAnimation(self.detail_page, b"pos")
        self._anim.setDuration(duration)
        self._anim.setStartValue(QPoint(x_from, 0))
        self._anim.setEndValue(QPoint(x_to, 0))
        self._anim.setEasingCurve(curve)
        self._anim.finished.connect(lambda: self._finish_slide(on_done))
        self._anim.start()

    def _finish_slide(self, on_done):
        # 先把位置钉死在终点（动画最后一步偶尔差几像素），再恢复重绘
        self.detail_page.setUpdatesEnabled(True)
        self.detail_page.update()
        if on_done is not None:
            on_done()

    def _after_back(self):
        self.stack.setCurrentWidget(self.search_page)

    def closeEvent(self, event):
        """关窗口时叫停还没播完的卡片入场动画

        动画/定时器持有的是那些卡的引用，让它们比窗口活得久没意义，
        而且在解释器退出阶段容易打出没用的告警。
        """
        self.search_page.stagger.clear()
        self.detail_page.stagger.clear()
        super().closeEvent(event)
