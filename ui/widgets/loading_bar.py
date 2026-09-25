"""加载状态条：一行「正在加载…」+ 来回跑的不确定进度条

界面里凡是"要等网络"的地方都该有个东西告诉用户"在跑，别急"：
拉模组加载器列表、拉版本列表、搜 Mod 都要等接口。

    ┌──────────────────────────────────────────────┐
    │ 正在获取模组加载器…      ▓▓▓▓░░░░░░▓▓▓▓░░░░    │
    └──────────────────────────────────────────────┘

移植自 `experiments/Downloading mod test/ui/widgets/loading_bar.py`，
行为一样，但**样式搬到了 assets/styles/parts 的 QSS 里**：
实验那版是 `setStyleSheet(STYLE)` 写死颜色的，主题一换就不跟着变
（我们两套主题，见 theme.py）。

## 为什么最短显示 180ms

网络快的时候（命中缓存、或者本地网络很好）请求可能 40ms 就回来了。
如果一来一回就立刻隐藏，加载条会**闪一下**再消失，比不显示还难看。
所以 hide_now() 会保证至少显示 MIN_VISIBLE_MS 再收起来。

## 为什么用 setMaximumHeight 做收起动画

和项目里别的动画一个路子（见 collapsible_group.py）：直接 show()/hide()
是硬切，高度从 0 动到尺寸提示值，视觉上顺一点。
"""

import time

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar

from core.i18n import tr

# 至少显示这么久再允许隐藏（毫秒）
MIN_VISIBLE_MS = 180
# 收起动画时长
HIDE_ANIM_MS = 140
# Qt 的 QWIDGETSIZE_MAX：不限高
_NO_LIMIT = 16777215


class LoadingBar(QFrame):
    """一行"正在加载…"的提示条

    只负责显示和隐藏，不负责取数 —— 取数在各自的 worker 里。

    min_visible_ms 是**至少显示多久**：网络快的时候请求可能几十毫秒就回来，
    不兜一下的话加载条会闪一下就没，用户根本看不到"它在加载"。
    """

    def __init__(self, parent=None, min_visible_ms: int = MIN_VISIBLE_MS):
        super().__init__(parent)
        self._min_visible_ms = max(0, int(min_visible_ms))
        self.setObjectName("LoadingBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self.label = QLabel(tr("正在加载…"))
        self.label.setObjectName("LoadingBarText")
        layout.addWidget(self.label)

        self.bar = QProgressBar()
        # ⚠️ setRange(0, 0) 就是 Qt 的"不确定进度"模式（chunk 来回跑）。
        # 千万别写成 (0, 100) 再 setValue(0) —— 那是一条永远不动的空条。
        self.bar.setObjectName("LoadingBarTrack")
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(180)
        layout.addWidget(self.bar, 1)

        self._shown_at = 0.0
        self._hiding = False
        # ⚠️ **自己记"该不该显示"，不要靠 `isVisible()` 判**：父页面还没被切过来
        # （或者窗口还没 show）时子控件的 `isVisible()` 永远是 False，于是
        # `hide_now()` 会以为"本来就没显示"直接 return —— 显示标记和限高都留着，
        # 等用户真翻到这一页，看到的就是一条**永远转下去的加载条**。
        # （截图脚本里就是这么发现的：数据早就到了，条还挂在页面上。）
        self._shown = False

        # 收起动画：高度 → 0
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
        self._shown = True
        self._anim.stop()
        self.setVisible(True)
        self.setMaximumHeight(_NO_LIMIT)
        self._shown_at = time.monotonic()

    def hide_now(self, text: str = None):
        """收起。显示时间不足 min_visible_ms 的话延迟收

        `text` 是"收起来的时候顺手把最后一句状态留下"用的
        （比如「加载失败」「共 12 个版本」）—— 收完还能看见。
        """
        if text:
            self.label.setText(text)
        if not self._shown:
            return

        elapsed = (time.monotonic() - self._shown_at) * 1000
        if elapsed < self._min_visible_ms:
            QTimer.singleShot(int(self._min_visible_ms - elapsed) + 10,
                              self._begin_hide)
        else:
            self._begin_hide()

    @property
    def is_loading(self) -> bool:
        """正在显示加载态（测试和调用方用，省得自己判 isVisible）

        ⚠️ 也是看 `_shown` 而不是 `isVisible()`：父页面没显示时 `isVisible()`
        恒为 False，那样这个属性会撒谎（明明在等却报 False）。
        """
        return self._shown and not self._hiding

    # ---------- 内部 ----------

    def _begin_hide(self):
        if self._hiding or not self._shown:
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
        self._shown = False
        self.setVisible(False)
