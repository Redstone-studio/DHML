"""
带裁剪的容器（收起/展开动画用）

## 它解决什么

原来收起动画是把内容的 `maximumHeight` 从 306 压到 0。副作用：内容自己的高度每帧
都在变 → 里面每个子控件（版本卡片、标签、文字）每帧都被重新摆一遍 →
**看着就是标签和文字在抖/闪**（用户报的就是这个）。

现在改成本控件负责"能看到多少"：

    本控件高度   ---------> 0     （外层布局看到它在收，跟着收）
    内容高度     一直 = 306        （子控件一动不动，不重排）

内容**一个像素都不会被重新布局**，所以不闪。

## 两个容易踩的地方

1. **裁剪用 Qt 原生的**：父控件在 `paintEvent` 里设的 clip 区域会作用到子控件
   （Qt 只在父控件的绘制流程里画子控件）。不需要手动 render 每个子控件 ——
   早期版本那么写过，又慢又容易出错。

2. **本控件的高度必须自己定死**：`QWidget` 默认竖向策略是 Preferred，
   挂进布局后会被拉伸（实测内容 306 被拉到 737）。所以每次显示/收起都用
   `setFixedHeight` 咬住。
"""
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QWidget


class ClipBox(QWidget):
    """只显示前 `visible_height` 像素的容器

    用法：
        box = ClipBox()
        box.set_content(widget, full_height)   # 内容按这个高度定死
        box.show_full()                        # 完整显示
        box.set_visible_height(h)              # 动画中每帧调它
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._visible = 0
        self._content = None
        self._full = 0

    # ---------- 内容 ----------

    def set_content(self, widget: QWidget, full_height: int = None):
        """挂内容，并按 `full_height` 把它的高度**固定**下来"""
        self._content = widget
        widget.setParent(self)
        self._full = max(1, int(full_height)) if full_height else 1
        self._sync_content()

    def content(self):
        return self._content

    def content_height(self) -> int:
        return self._full

    def set_full_height(self, height: int):
        """内容该有多高（由调用方逐行累加算出来，比 sizeHint 可靠）"""
        self._full = max(1, int(height))
        self._sync_content()

    def _sync_content(self):
        if self._content is None:
            return
        self._content.setFixedHeight(self._full)
        self._content.setGeometry(0, 0, self.width(), self._full)

    # ---------- 显示状态 ----------

    def show_full(self):
        """完整显示：裁剪放开 + 高度咬死成内容高度（不被外层布局拉伸）"""
        self._visible = self._full
        if self._content is not None:
            self._sync_content()
        self.setFixedHeight(self._full)
        self.update()

    def set_visible_height(self, value: int):
        """动画中每帧调：只改"能看到多少"，并把自身高度咬到同一个值"""
        value = max(0, int(value))
        self._visible = value
        # 高度咬死成可见高度 —— 外层布局才会跟着收缩
        self.setFixedHeight(max(0, value))
        if self._content is not None:
            # 内容**不动**：始终保持完整高度、从顶部开始，只是被画出来的部分变少
            self._content.setGeometry(0, 0, self.width(), self._full)
        self.update()

    def visible_height(self) -> int:
        return self._visible

    def resizeEvent(self, event):
        """宽度变了：内容跟着变宽（高度仍然不动）"""
        if self._content is not None:
            self._content.setGeometry(0, 0, self.width(), self._full)
        super().resizeEvent(event)

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        painter = QPainter(self)
        # 只画前 _visible 像素。父控件的 clip 会作用到子控件，
        # 所以超出的部分（包括里面的卡片）都不会被画出来。
        painter.setClipRect(QRect(0, 0, self.width(), max(0, self._visible)))
        painter.end()
