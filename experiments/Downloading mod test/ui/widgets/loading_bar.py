"""
加载状态条

界面里凡是"要等网络"的地方都该有个东西告诉用户"在跑，别急"：
搜索要等 Modrinth 的搜索接口，点进详情要等项目 + 版本两个接口。

## 为什么最短显示 180ms

网络快的时候（命中缓存、或者本地网络很好）请求可能 40ms 就回来了。
如果一来一回就立刻隐藏，加载条会**闪一下**再消失，比不显示还难看。
所以 hide_now() 会保证至少显示 MIN_VISIBLE_MS 再收起来。

## 为什么用 setMaximumHeight 做收起动画

和项目里别的动画一个路子（见 collapsible_group.py）：直接 show()/hide()
是硬切，高度从 0 动到尺寸提示值，视觉上顺一点。
"""
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar

# 至少显示这么久再允许隐藏（毫秒）
MIN_VISIBLE_MS = 180
# 收起动画时长
HIDE_ANIM_MS = 140

STYLE = """
#LoadingBar {
    background-color: #232428;
    border: 1px solid #2e3034;
    border-radius: 10px;
}
#LoadingBar QLabel {
    color: #d8d8dc;
    font-size: 12px;
}
#LoadingBar QProgressBar {
    background-color: #2a2c30;
    border: none;
    border-radius: 3px;
    min-height: 6px;
    max-height: 6px;
}
#LoadingBar QProgressBar::chunk {
    background-color: #5ec269;
    border-radius: 3px;
}
"""


class LoadingBar(QFrame):
    """一行"正在加载…"的提示条

    只负责显示和隐藏，不负责取数 —— 取数在各自的 worker 里。

    min_visible_ms 是**至少显示多久**：网络快的时候（本地缓存、镜像给力）
    请求可能几十毫秒就回来了，不兜一下的话加载条会闪一下就没，
    用户根本看不到"它在加载"（详情页尤其明显，所以那边给了更长的值）。
    """

    def __init__(self, parent=None, min_visible_ms: int = MIN_VISIBLE_MS):
        super().__init__(parent)
        self._min_visible_ms = max(0, int(min_visible_ms))
        self.setObjectName("LoadingBar")
        self.setStyleSheet(STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self.label = QLabel("正在加载…")
        layout.addWidget(self.label)

        self.bar = QProgressBar()
        # 0/0 + setRange(0, 0) 就是 Qt 的"不确定进度"模式（来回跑）
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(180)
        layout.addWidget(self.bar, 1)

        self._shown_at = 0.0
        self._hiding = False

        # 收起动画：高度 22 → 0
        self._anim = QPropertyAnimation(self, b"maximumHeight", self)
        self._anim.setDuration(HIDE_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.finished.connect(self._after_hide)

        self.setMaximumHeight(0)
        self.setVisible(False)

    # ---------- 对外 ----------

    def show_state(self, text: str = None):
        """显示（已经在显示就只换文字）"""
        if text:
            self.label.setText(text)
        self._hiding = False
        self._anim.stop()
        self.setVisible(True)
        self.setMaximumHeight(16777215)     # Qt 的 QWIDGETSIZE_MAX：不限高
        self._shown_at = self._now()

    def hide_now(self, text: str = None):
        """收起。显示时间不足 min_visible_ms 的话延迟收"""
        if text:
            self.label.setText(text)
        if not self.isVisible():
            return

        elapsed = (self._now() - self._shown_at) * 1000
        if elapsed < self._min_visible_ms:
            QTimer.singleShot(int(self._min_visible_ms - elapsed) + 10,
                              self._begin_hide)
        else:
            self._begin_hide()

    # ---------- 内部 ----------

    @staticmethod
    def _now() -> float:
        import time
        return time.monotonic()

    def _begin_hide(self):
        if self._hiding or not self.isVisible():
            return
        self._hiding = True
        self._anim.stop()
        self._anim.setStartValue(max(1, self.height()))
        self._anim.setEndValue(0)
        self._anim.start()

    def _after_hide(self):
        if not self._hiding:
            return
        self._hiding = False
        self.setVisible(False)
