"""
类型标签栏

一排互斥的按钮，用来切换"搜什么"：搜模组 / 搜光影 / 搜资源包 / 搜数据包 / 搜整合包。

## 样式为什么走 QSS + 动态属性

选中态不是靠 Python 里换 stylesheet，而是给每个按钮一个 `selected` 动态属性，
然后在 QSS 里写 `QPushButton[selected="true"]`。

这样做的好处：
  · 所有外观集中在一处（SearchPage 的 FilterPanel 样式表里），想换配色不用翻代码
  · 不用每次切换都 setStyleSheet（那样每个按钮会各留一份样式表，也更容易漂）
  · 属性变了记得 unpolish/polish 一下，Qt 才会重新匹配选择器（见 _repolish）

## 用法

    tabs = LinkTabs()
    tabs.add_tab("mod", "搜模组")
    tabs.add_tab("shader", "搜光影")
    tabs.changed.connect(handler)      # 参数是 key
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QWidget

# 每个标签按钮的最小宽度（像素）。够放下"搜资源包"这种最长的标签，
# 同时让整排宽窄一致 —— 长短不一的按钮排在一起很毛躁。
TAB_MIN_WIDTH = 92


class LinkTabs(QWidget):
    """一排互斥按钮（名字保留 LinkTabs，第一版是"下划线链接"那套）"""

    changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LinkTabs")

        self._buttons = {}       # key -> QPushButton
        self._order = []
        self._current = None

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        # ⚠️ 末尾这个 stretch 不能省：给 QPushButton 套了样式表之后，
        # 它的尺寸策略会被当成"乐意占满宽度"，一排标签就会被横向拉散
        # （实测 5 个标签铺满整个面板宽度）。加个弹簧把它们顶到左边。
        self._layout.addStretch(1)

    # ---------- 组装 ----------

    def add_tab(self, key: str, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("LinkTab")
        btn.setCheckable(True)
        btn.setAutoExclusive(False)      # 互斥由 set_current 自己管，行为更可控
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # 点完别留焦点框
        # 尺寸写死：套了 QSS 的 QPushButton 会随可用宽度乱伸缩，
        # 一排标签宽窄不一很难看。取"最长那个标签"的宽度，整排对齐
        btn.setMinimumWidth(TAB_MIN_WIDTH)
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        btn.setProperty("selected", False)
        btn.clicked.connect(lambda _=False, k=key: self.set_current(k))

        self._buttons[key] = btn
        self._order.append(key)
        # 插到弹簧前面，保证按钮都在左边
        self._layout.insertWidget(self._layout.count() - 1, btn)

        if self._current is None:
            self.set_current(key, notify=False)
        return btn

    # ---------- 对外 ----------

    def keys(self) -> list:
        return list(self._order)

    def current_key(self) -> str:
        return self._current

    def set_current(self, key: str, notify: bool = True):
        """切到某一项。notify=False 用于初始化（别在信号还没接好时就发）"""
        if key not in self._buttons:
            return
        changed = key != self._current
        self._current = key
        for k, btn in self._buttons.items():
            selected = (k == key)
            if btn.property("selected") != selected:
                btn.setProperty("selected", selected)
                _repolish(btn)
        if changed and notify:
            self.changed.emit(key)

    # ---------- 键盘可达性 ----------

    def keyPressEvent(self, event):
        """左右方向键在标签之间移动（纯鼠标的话手不离键盘也能切）"""
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            if self._current in self._order:
                i = self._order.index(self._current)
                step = -1 if event.key() == Qt.Key.Key_Left else 1
                self.set_current(self._order[(i + step) % len(self._order)])
                return
        super().keyPressEvent(event)


def _repolish(widget):
    """属性改了以后要重走一遍样式匹配，否则 QSS 里的 [selected="true"] 不生效"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
