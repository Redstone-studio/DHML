"""个性化页：主题 / 主题色 / 界面语言

从设置页拆出来的。"看着舒服"的选项（颜色、语言）和"游戏怎么跑"的选项
（游戏目录、Java、内存）不是一类东西，混在一页里越长越乱。
PCL2 也是这么分的 —— 设置里挂一个"个性化"分类。

主题色的做法参考 PCL2：一排预设圆形色块 + 自定义 RGB 滑块。
拖动滑块就即时生效（有 120ms 防抖，不然拖一次能刷上百遍样式表）。
"""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
    QVBoxLayout, QWidget
)

from core import i18n, theme
from core.config import config
from core.i18n import tr
from ui.translatable import TranslatableWidget

# 深浅模式。(配置里存的值, 界面文案)
THEME_MODES = (
    ("system", "跟随系统"),
    ("light", "浅色"),
    ("dark", "深色"),
)  # noqa: i18n  —— 这些中文在 _fill_theme_combo 里过 tr()

# 预设主题色。第一个是空串 = 用主题自带的那套手挑颜色。
# 名字用的是游戏里的材料名 —— 既有辨识度，又不用去借别的启动器的命名。
PRESET_COLORS = (
    ("", "默认"),
    ("#3b6ea5", "青金石"),
    ("#3fbfb0", "钻石"),
    ("#4caf50", "仙人掌"),
    ("#d9a03a", "金锭"),
    ("#96603a", "泥土"),
    ("#c94f4f", "红石"),
)  # noqa: i18n

SWATCH_SIZE = 26
SLIDER_DEBOUNCE_MS = 120


class _ColorSwatch(QPushButton):
    """圆形色块按钮，选中时加一圈描边

    color 为空串表示"用主题自带的强调色"，所以画什么得由外面告诉它。
    提示文字由外面用 bind(..., "toolTip") 登记，切语言时会自动跟着变。
    """

    def __init__(self, color: str):
        super().__init__()
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def refresh(self, fallback: str, ring: str):
        shown = self.color or fallback
        border = ring if self.isChecked() else "transparent"
        self.setStyleSheet(
            f"QPushButton {{ background-color: {shown};"
            f" border: 2px solid {border};"
            f" border-radius: {SWATCH_SIZE // 2}px; }}"
        )


class PersonalizePage(TranslatableWidget):
    theme_changed = pyqtSignal()
    language_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._loading = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(8)

        layout.addWidget(self.label("个性化", "PageTitle"))
        layout.addWidget(self.label("外观相关的设置，改完立刻生效，不用重启", "PageSubtitle"))

        layout.addSpacing(14)

        layout.addWidget(self._make_theme_card())
        layout.addWidget(self._make_accent_card())
        layout.addWidget(self._make_language_card())
        layout.addStretch()

        # 滑块防抖：拖动时不能每动一格就重刷整套样式表
        self._accent_timer = QTimer(self)
        self._accent_timer.setSingleShot(True)
        self._accent_timer.setInterval(SLIDER_DEBOUNCE_MS)
        self._accent_timer.timeout.connect(self._flush_accent)

        self._refresh_all()
        self._loading = False

    # ---------- 语言切换 ----------

    def retranslate(self):
        super().retranslate()
        self._fill_theme_combo()
        self._fill_language_combo()
        self._refresh_all()

    # ---------- 卡片 ----------

    def _card(self, title: str):
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 16, 20, 18)
        box.setSpacing(10)
        box.addWidget(self.label(title, "SectionTitle"))
        return card, box

    def _make_theme_card(self):
        card, box = self._card("主题")

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.label("深浅模式", "FieldLabel"))

        self.theme_combo = QComboBox()
        self.theme_combo.setFixedWidth(200)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_selected)
        row.addWidget(self.theme_combo)

        row.addStretch()
        box.addLayout(row)
        return card

    def _make_accent_card(self):
        card, box = self._card("主题色")

        # 预设色板
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        self.swatches = []
        for index, (value, name) in enumerate(PRESET_COLORS):
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(7)

            # 注意：这两个都要走 self.bind() —— 它会把文案登记进基类，
            # 语言切换时自动重设。直接 QLabel(tr(name)) 的话只在构造时取一次，
            # 切语言后文字不会变（这个坑真犯过）。
            swatch = _ColorSwatch(value)
            swatch.clicked.connect(lambda _checked, v=value: self._set_accent(v))
            self.bind(swatch, name, "toolTip")
            self.swatches.append(swatch)
            cell_layout.addWidget(swatch)

            label = QLabel()
            label.setObjectName("FieldLabel")
            self.bind(label, name)
            cell_layout.addWidget(label)

            grid.addWidget(cell, index // 4, index % 4)
        box.addLayout(grid)

        box.addSpacing(4)

        # 自定义 RGB
        custom_row = QHBoxLayout()
        custom_row.setSpacing(10)
        custom_row.addWidget(self.label("自定义", "FieldLabel"))

        self.custom_preview = QLabel()
        self.custom_preview.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)
        custom_row.addWidget(self.custom_preview)

        custom_row.addStretch()
        box.addLayout(custom_row)

        self.sliders = []
        for channel in ("R", "G", "B"):
            line = QHBoxLayout()
            line.setSpacing(10)
            tag = QLabel(channel)
            tag.setObjectName("FieldLabel")
            tag.setFixedWidth(12)
            line.addWidget(tag)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 255)
            slider.setFixedWidth(240)
            slider.valueChanged.connect(self._on_rgb_changed)
            line.addWidget(slider)
            self.sliders.append(slider)

            value_label = QLabel("0")
            value_label.setObjectName("HintText")
            value_label.setFixedWidth(30)
            line.addWidget(value_label)
            slider.valueChanged.connect(
                lambda v, lab=value_label: lab.setText(str(v))
            )

            line.addStretch()
            box.addLayout(line)

        box.addWidget(self.label("拖动滑块会直接生效，并取消上面预设色的选中。", "HintText"))
        return card

    def _make_language_card(self):
        card, box = self._card("界面语言")

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.label("语言", "FieldLabel"))

        self.language_combo = QComboBox()
        self.language_combo.setFixedWidth(200)
        self.language_combo.currentIndexChanged.connect(self._on_language_selected)
        row.addWidget(self.language_combo)

        row.addStretch()
        box.addLayout(row)

        box.addWidget(self.label("切换后立刻生效，不需要重启。", "HintText"))
        return card

    # ---------- 数据 ----------

    def _fill_theme_combo(self):
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for key, text in THEME_MODES:
            self.theme_combo.addItem(tr(text), key)
        index = self.theme_combo.findData(config.get("theme", "system"))
        self.theme_combo.setCurrentIndex(index if index >= 0 else 0)
        self.theme_combo.blockSignals(False)

    def _fill_language_combo(self):
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        for code in i18n.available_languages():
            self.language_combo.addItem(i18n.language_name(code), code)
        index = self.language_combo.findData(i18n.current_language())
        self.language_combo.setCurrentIndex(index if index >= 0 else 0)
        self.language_combo.blockSignals(False)

    def _preview_mode(self) -> str:
        """取"当前生效主题"的默认强调色用哪套

        "跟随系统"时界面层才知道实际是哪套，这里近似按深色算 ——
        只影响那个小圆点显示什么颜色，无所谓。
        """
        return "light" if config.get("theme", "system") == "light" else "dark"

    def _refresh_all(self):
        """把界面同步成配置里的样子"""
        self._fill_theme_combo()
        self._fill_language_combo()

        accent = config.get("accent_color", "")
        pal = theme.palette(self._preview_mode())
        ring = pal["text_dim"]

        for swatch, (value, _name) in zip(self.swatches, PRESET_COLORS):
            swatch.setChecked(value == accent)
            swatch.refresh(pal["accent"], ring)

        # 自定义滑块：有自定义色就用它，否则显示当前主题的默认强调色
        base = QColor(accent or pal["accent"])
        self._loading = True
        for slider, value in zip(self.sliders, (base.red(), base.green(), base.blue())):
            slider.setValue(value)
        self._loading = False
        self._refresh_custom_preview(base.name())

    def _refresh_custom_preview(self, color: str):
        self.custom_preview.setStyleSheet(
            f"background-color: {color};"
            f" border: 1px solid {theme.palette(self._preview_mode())['border_hover']};"
            f" border-radius: {SWATCH_SIZE // 2}px;"
        )

    # ---------- 事件 ----------

    def _set_accent(self, value: str):
        if value == config.get("accent_color", ""):
            return
        config.set("accent_color", value)
        self._refresh_all()
        self.theme_changed.emit()

    def _on_theme_selected(self, _index: int):
        if self._loading:
            return
        mode = self.theme_combo.currentData()
        if mode and mode != config.get("theme", "system"):
            config.set("theme", mode)
            self._refresh_all()
            self.theme_changed.emit()

    def _on_language_selected(self, _index: int):
        if self._loading:
            return
        code = self.language_combo.currentData()
        if code and code != i18n.current_language():
            # 真正的切换交给主窗口做（它负责通知所有页面重设文字）
            self.language_changed.emit(code)

    def _on_rgb_changed(self, _value: int):
        if self._loading:
            return
        color = QColor(self.sliders[0].value(), self.sliders[1].value(), self.sliders[2].value())
        self._refresh_custom_preview(color.name())
        # 拖动时先取消预设的选中，手感上更符合直觉
        for swatch in self.swatches:
            swatch.setChecked(False)
            swatch.refresh(theme.palette(self._preview_mode())["accent"],
                           theme.palette(self._preview_mode())["text_dim"])
        self._accent_timer.start()

    def _flush_accent(self):
        color = QColor(self.sliders[0].value(), self.sliders[1].value(), self.sliders[2].value())
        if color.name() != config.get("accent_color", ""):
            config.set("accent_color", color.name())
            self.theme_changed.emit()
