"""支持语言切换的控件基类

写新界面时请照这三条来：

1. **所有面向用户的文字都要走文案系统**，不要直接写中文字面量。
   简单场合用 tr("中文")，建控件用本模块的 self.label() / self.button()。

2. **静态文字用 self.label() / self.button() / self.bind() 建**
   —— 它们会自动登记，语言切换时自动重设，你不用管。

3. **动态生成的文字要在子类覆写的 retranslate() 里重建**
   （列表项、下拉项、带变量的文案，比如 f"共 {n} 个版本"），
   并且**务必先调 super().retranslate()**。

为什么要有这个基类：如果只靠"记得在每个页面的 retranslate() 里补一行"，
两个 AI 协作时迟早会漏。用 self.label() 建控件的话，
"加一个标签"这件事本身就自带了翻译支持，不需要额外记忆。
"""

from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from core.i18n import tr

# 属性名 → setter 方法名
_SETTERS = {
    "text": "setText",
    "toolTip": "setToolTip",
    "placeholderText": "setPlaceholderText",
    "windowTitle": "setWindowTitle",
    "suffix": "setSuffix",
    "prefix": "setPrefix",
}


def _apply(widget, key: str, prop: str):
    setter = getattr(widget, _SETTERS[prop], None)
    if setter is not None:
        setter(tr(key))


class TranslatableWidget(QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tr_bindings = []      # [(widget, key, prop)]

    # ---------- 建控件（自动登记文案） ----------

    def label(self, key: str, object_name: str = None) -> QLabel:
        label = QLabel()
        if object_name:
            label.setObjectName(object_name)
        return self.bind(label, key)

    def button(self, key: str, object_name: str = None) -> QPushButton:
        button = QPushButton()
        if object_name:
            button.setObjectName(object_name)
        return self.bind(button, key)

    def bind(self, widget, key: str, prop: str = "text"):
        """把控件的某个属性绑到文案上，返回 widget（方便链式写）

        prop 支持 text / toolTip / placeholderText / windowTitle / suffix / prefix
        """
        if prop not in _SETTERS:
            raise ValueError(f"unsupported prop: {prop} (expected one of {sorted(_SETTERS)})")
        self._tr_bindings.append((widget, key, prop))
        _apply(widget, key, prop)
        return widget

    # ---------- 语言切换 ----------

    def retranslate(self):
        """语言变了，重设所有登记过的文字

        子类覆写时**必须先调用 super().retranslate()**，否则静态文字不会更新。
        """
        alive = []
        for widget, key, prop in self._tr_bindings:
            try:
                _apply(widget, key, prop)
            except RuntimeError:
                continue        # 控件已经被销毁（比如重建过列表），丢掉绑定
            alive.append((widget, key, prop))
        self._tr_bindings = alive
