"""开关（Switch）

Qt 没有原生的开关控件，QCheckBox 又是个方框。这里自己画一个：
圆角轨道 + 会滑动的圆钮，切换时有 120ms 的小动画。

⚠️ 因为是自己画的，**QSS 管不到它** —— 颜色得从 ui/theme_state.py 现取。
所以主题/强调色变了之后，要让界面重建这些控件（或者调 refresh()）。
"""

from PyQt6.QtCore import (QEasingCurve, QPropertyAnimation, QRectF, QSize,
                          Qt, pyqtProperty)
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QAbstractButton

from ui import theme_state

TRACK_WIDTH = 40
TRACK_HEIGHT = 22
MARGIN = 2


class Switch(QAbstractButton):
    """拨动开关。用法和 QCheckBox 一样：setChecked() / isChecked() / toggled"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(TRACK_WIDTH, TRACK_HEIGHT)

        self._offset = 0.0          # 0 = 关到底，1 = 开到底
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    # ---------- 动画属性 ----------

    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float):
        self._offset = value
        self.update()

    offset = pyqtProperty(float, get_offset, set_offset)

    def _animate(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool):        # noqa: N802  （Qt 的命名）
        super().setChecked(checked)
        # 程序设值时不该有动画（比如加载配置），直接到位
        self._anim.stop()
        self.set_offset(1.0 if checked else 0.0)

    def refresh(self):
        """主题或强调色变了之后调一下，重取颜色"""
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(TRACK_WIDTH, TRACK_HEIGHT)

    # ---------- 画 ----------

    def paintEvent(self, _event):
        colors = theme_state.palette()
        on = self.isChecked()

        track_on = QColor(colors["accent_fill"])
        track_off = QColor(colors["border_control"])
        knob = QColor(colors["on_accent"] if on else colors["text_dim"])

        if not self.isEnabled():
            track_on.setAlpha(120)
            track_off.setAlpha(120)
            knob.setAlpha(140)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 轨道
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_on if on else track_off)
        painter.drawRoundedRect(
            QRectF(0, 0, TRACK_WIDTH, TRACK_HEIGHT),
            TRACK_HEIGHT / 2, TRACK_HEIGHT / 2,
        )

        # 圆钮：位置按 _offset 插值
        diameter = TRACK_HEIGHT - MARGIN * 2
        travel = TRACK_WIDTH - diameter - MARGIN * 2
        x = MARGIN + travel * self._offset
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(x, MARGIN, diameter, diameter))
