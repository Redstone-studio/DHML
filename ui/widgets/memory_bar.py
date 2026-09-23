"""内存占用条

四段，从左到右：

    [系统已用][游戏最多占用][JVM 开销][剩余]

外加一条**红线刻度**（物理内存的 60%）—— 越过它就容易开始换页，
表现是"越玩越卡"，比崩溃更烦人。

为什么自绘：QSS 只能给一个控件一种底色，画不出按比例分段、还要按当前窗口宽度
重算的条。颜色照旧走 ui/theme_state.py，所以换主题/换强调色会跟着变
（主窗口 load_styles 之后会调 refresh_theme）。
"""

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from core.memory import PAGE_RISK_RATIO
from ui.theme_state import palette

# 每一段用哪个调色板 token
_SEGMENTS = (
    ("used", "border_control"),     # 系统已经用掉的（不是我们的）
    ("game", "accent"),             # 游戏堆上限（-Xmx）
    ("overhead", "warn"),           # JVM 堆之外的开销（估算）
)

BAR_HEIGHT = 22


class MemoryBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MemoryBar")
        self.setFixedHeight(BAR_HEIGHT)
        self._total = 0
        self._used = 0
        self._game = 0
        self._overhead = 0

    def set_values(self, total_mb: int, used_mb: int, heap_mb: int, overhead_mb: int):
        self._total = max(0, int(total_mb))
        self._used = max(0, int(used_mb))
        self._game = max(0, int(heap_mb))
        self._overhead = max(0, int(overhead_mb))
        self.update()

    def values(self):
        """(总量, 系统已用, 游戏上限, JVM 开销)，单位都是 MB"""
        return (self._total, self._used, self._game, self._overhead)

    def refresh_theme(self):
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        colors = palette()

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, QColor(colors.get("bg_elevated", "#26282d")))

        if self._total <= 0:
            return

        # 画到圆角里面去 —— 不裁剪的话方块段会盖住两端的圆角
        painter.setClipPath(path)
        unit = rect.width() / self._total
        values = {"used": self._used, "game": self._game, "overhead": self._overhead}
        x = rect.left()
        for key, token in _SEGMENTS:
            width = values[key] * unit
            if width <= 0:
                continue
            painter.fillRect(QRectF(x, rect.top(), width, rect.height()),
                             QColor(colors.get(token, "#5ec269")))
            x += width

        # 60% 那条红线：游戏最多占用越过去就该考虑调小
        limit_x = rect.left() + rect.width() * PAGE_RISK_RATIO
        if limit_x < rect.right():
            painter.setPen(QPen(QColor(colors.get("danger", "#e0575f")), 2))
            painter.drawLine(QPointF(limit_x, rect.top()),
                             QPointF(limit_x, rect.bottom()))

        painter.end()
