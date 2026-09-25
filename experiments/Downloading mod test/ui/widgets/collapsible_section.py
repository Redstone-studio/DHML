"""
可收起的区块（带高度动画）

从 `CollapsibleGroup` 里抽出来的通用版：给一个标题和一块内容，
点标题就展开/收起，带高度动画。

## 为什么单独抽一个

设置页里有好几处是"开关打开才显示更多选项"的结构（比如多线程下载的线程数滑块）。
每处都手抄一遍高度动画迟早会漂，所以统一走这里。

## 高度怎么算

⚠️ **不能用 `content.sizeHint()`**：内容里可能包含"轮到才显示"的控件
（`SlideInRow` 那种），隐藏的子控件不计入 sizeHint，量出来会小一大截。
所以这里自己把每一行的高度加起来（`_measure`）。

## 用法

    sec = CollapsibleSection("下载设置", inner_widget)
    sec.set_expanded(True)      # 带动画地展开
    sec.expanded_changed.connect(handler)
"""
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QAbstractAnimation, pyqtSignal,
)
from PyQt6.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget

# 和 CollapsibleGroup 用同一套节奏，视觉上才统一
EXPAND_MS = 220
EXPAND_CURVE = QEasingCurve.Type.InOutCubic
# 标题栏和内容之间的间距（自己算高度时要用到）
_SECTION_SPACING = 6


class CollapsibleSection(QWidget):
    """可收起区块。标题栏是个带箭头的按钮，下面挂内容"""

    expanded_changed = pyqtSignal(bool)

    def __init__(self, title: str, content: QWidget, parent=None,
                 expanded: bool = False):
        super().__init__(parent)
        self._content = content
        # 自己接管高度：外部布局别想把我拉高或压扁
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.toggle_btn = QToolButton()
        self.toggle_btn.setText(title)
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(False)
        self.toggle_btn.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Fixed)
        self.toggle_btn.setStyleSheet("""
            QToolButton {
                background-color: #232428;
                color: #e9e9ec;
                border: 1px solid #2e3034;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 13px;
                text-align: left;
            }
            QToolButton:hover { background-color: #2a2c30; border-color: #5ec269; }
            QToolButton:checked { background-color: #26282d; border-color: #3a3c42; }
        """)
        self.toggle_btn.clicked.connect(self._on_toggle)
        layout.addWidget(self.toggle_btn)

        # 外层的 box 负责被"压扁"；真正的内容是它的子控件，不被改造成影响布局
        self.box = QWidget()
        self.box_layout = QVBoxLayout(self.box)
        self.box_layout.setContentsMargins(16, 6, 0, 6)
        self.box_layout.setSpacing(6)
        self.box_layout.addWidget(content)
        self.box.setMaximumHeight(0)
        self.box.setVisible(False)
        layout.addWidget(self.box)

        self.anim = QPropertyAnimation(self.box, b"maximumHeight", self)
        self.anim.setDuration(EXPAND_MS)
        self.anim.setEasingCurve(EXPAND_CURVE)
        self.anim.valueChanged.connect(self._on_anim_value)
        self.anim.finished.connect(self._after_anim)

        if expanded:
            # 初始就是展开的：直接把高度定死，不放动画（省得打开就动一下）
            self._set_state(True, animate=False)
        self._sync_own_height()

    # ---------- 自身高度 ----------

    def _header_height(self) -> int:
        return self.toggle_btn.sizeHint().height()

    def _sync_own_height(self):
        """按当前状态把**自己**的高度定死

        ⚠️ 这一步是必须的，不能只设 box 的 maximumHeight。
        实测：本组件放进紧凑布局（比如设置对话框）后，它会和别的控件抢空间，
        box 被压成 47px（量出来 73px），展开动画看着"没展开完"。
        原因是 QWidget 默认的竖向尺寸策略是 Preferred，会跟着被拉伸/压缩。

        ⚠️ 展开/收起**过程中**也要跟着 box 的实时高度走：否则自身高度还是旧的，
        内容会被自己的边界裁掉（动画看着像被"切"）。
        """
        header = self._header_height()
        if self.anim.state() == QAbstractAnimation.State.Running:
            inner = self.box.height()
        elif self.is_expanded():
            inner = self._measure()
        else:
            inner = 0
        self.setFixedHeight(header + _SECTION_SPACING + inner)

    def _on_anim_value(self, _value):
        """动画每一帧都同步一次自身高度"""
        self._sync_own_height()

    # ---------- 对外 ----------

    def is_expanded(self) -> bool:
        return self.toggle_btn.isChecked()

    def set_expanded(self, expanded: bool, animate: bool = True):
        """代码里控制展开/收起（也会同步按钮状态）"""
        if self.is_expanded() == bool(expanded):
            return
        self._set_state(bool(expanded), animate=animate)

    # ---------- 内部 ----------

    def _on_toggle(self):
        self._set_state(self.toggle_btn.isChecked(), animate=True)

    def _set_state(self, expanded: bool, animate: bool):
        self.toggle_btn.setChecked(expanded)
        self.toggle_btn.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

        if expanded:
            self.box.setVisible(True)
            target = self._measure()
            if not animate:
                self.anim.stop()
                self.box.setMaximumHeight(target)
            else:
                self.box.setMaximumHeight(0)
                self.anim.stop()
                self.anim.setStartValue(0)
                self.anim.setEndValue(target)
                self.anim.start()
        else:
            current = self.box.height()
            self.anim.stop()
            if not animate:
                self.box.setMaximumHeight(0)
                self.box.setVisible(False)
            else:
                self.anim.setStartValue(current)
                self.anim.setEndValue(0)
                self.anim.start()

        if not animate:
            # 不放动画的路径（初始化 / 恢复默认）要立刻把高度定下来，
            # 不然会停在旧高度上
            self._sync_own_height()
        self.expanded_changed.emit(expanded)

    def _measure(self) -> int:
        """展开后该有多高：逐行累加，不依赖 sizeHint 对隐藏控件的态度"""
        margins = self.box_layout.contentsMargins()
        rows = [self.box_layout.itemAt(i).widget()
                for i in range(self.box_layout.count())]
        rows = [w for w in rows if w is not None]
        if not rows:
            return 0
        spacing = self.box_layout.spacing() * max(0, len(rows) - 1)
        return (sum(w.sizeHint().height() for w in rows) + spacing
                + margins.top() + margins.bottom())

    def _after_anim(self):
        if not self.is_expanded():
            self.box.setVisible(False)
        else:
            # ⚠️ 这里**不能**把高度上限放开成无限大：放开之后外层布局会把内容
            # 拉伸到剩余空间，实测展开高度从 132 变成 331，动画最后一下会"弹"。
            # 定死成量出来的高度才对（内容宽度变了会在 resizeEvent 里重算）。
            self.box.setMaximumHeight(self._measure())
        self._sync_own_height()

    def resizeEvent(self, event):
        """宽度变了要重算高度 —— 里面的说明文字会换行，行数变了高度就变了"""
        super().resizeEvent(event)
        if self.is_expanded() and self.anim.state() != QAbstractAnimation.State.Running:
            self.box.setMaximumHeight(self._measure())
            self._sync_own_height()
