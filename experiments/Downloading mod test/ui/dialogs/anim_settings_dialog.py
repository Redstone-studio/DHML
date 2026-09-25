"""
动效设置

点工具栏上的「动效」按钮弹出：左边选风格，右边是**实时预览** ——
选哪一档，右边三张示意卡片就用哪一档演一遍，不用真的去搜一次才知道效果。
下面还有速度档位（觉得"太快了看不清"就调它）。

预览用的就是产品代码里那个 SlideInRow / StaggerReveal，
所以这里看到的和实际列表里的效果是同一个东西，不会"预览好看、实际不一样"。

## 一个踩过的坑

`StaggerReveal.add()` 返回的 SlideInRow **已经重新认了父控件**（为了不让它
在轮到之前露出来，内部会 setParent(None)）。所以示意卡片不能先 addWidget 进
布局、再交给 add() —— 那样卡片会被从布局里摘走，预览区变成一片空白
（表现就是"预览不演动画"）。正确顺序是：先 add() 包好，再 addWidget 那个返回值。
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QFrame, QRadioButton, QButtonGroup
)

from ui.widgets import anim_settings
from ui.widgets.anim_prefs import (
    PRESET_ORDER, SPEED_ORDER, preset, scaled, speed_factor, speed_text,
    direction_text, ANIM_BOUNCE, DIR_VERTICAL,
)
from ui.widgets.slide_in import SlideInRow, StaggerReveal

# 预览区里放几张示意卡片
PREVIEW_COUNT = 3
PREVIEW_DELAY_MS = 220      # 切换设置后稍等一下再播，避免连点造成动画打架


def _swatch(color: str, size: int = 14) -> QIcon:
    """列表左边的小色块（纯装饰，让几档风格一眼能分开）"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, size, size, 4, 4)
    painter.end()
    return QIcon(pm)


PRESET_COLORS = {
    "slide_left": "#5ec269",
    "slide_up": "#4f9de0",
    "slide_left_bounce": "#e0b341",
    "pcl_like": "#a0a1a7",
    "slide_left_back": "#c77dff",
    "slide_up_elastic": "#ff8fab",
    "pcl_recipe": "#7fd1ff",
}


class AnimSettingsDialog(QDialog):
    """动效设置（模态）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("动效设置")
        self.setModal(True)
        self.setMinimumSize(760, 520)
        self.setStyleSheet("""
            QDialog { background-color: #1b1c1f; }
            QLabel { color: #e9e9ec; }
            QListWidget {
                background-color: #232428; color: #e9e9ec;
                border: 1px solid #2e3034; border-radius: 8px;
                padding: 4px; font-size: 13px;
            }
            QListWidget::item { padding: 10px 8px; border-radius: 6px; }
            QListWidget::item:selected { background-color: #2f5d3a; color: #ffffff; }
            QPushButton {
                background-color: #2a2c30; color: #e9e9ec;
                border: 1px solid #3a3c42; border-radius: 6px;
                padding: 6px 16px; font-size: 12px;
            }
            QPushButton:hover { background-color: #34363a; border-color: #5ec269; }
            QRadioButton { color: #d8d8dc; font-size: 12px; padding: 2px 4px; }
            QRadioButton::indicator { width: 13px; height: 13px; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("入场动画")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        root.addWidget(title)

        hint = QLabel("列表里的卡片出现时怎么滑进来。选哪一档，右边立刻演一遍。"
                      "嫌快就调下面的速度。")
        hint.setStyleSheet("color: #a0a1a7; font-size: 12px;")
        root.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ---------- 左：预设列表 ----------
        self.list = QListWidget()
        self.list.setFixedWidth(240)
        for key in PRESET_ORDER:
            p = preset(key)
            item = QListWidgetItem(p.name)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setIcon(_swatch(PRESET_COLORS.get(key, "#5ec269")))
            item.setToolTip(p.description)
            self.list.addItem(item)
        self.list.currentItemChanged.connect(self._on_preset_changed)
        body.addWidget(self.list)

        # ---------- 右：说明 + 预览 ----------
        right = QVBoxLayout()
        right.setSpacing(10)

        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color: #d8d8dc; font-size: 12px;")
        right.addWidget(self.detail)

        self.preview = QFrame()
        self.preview.setObjectName("PreviewBox")
        self.preview.setStyleSheet(
            "#PreviewBox { background-color: #16171a; border: 1px solid #2e3034;"
            " border-radius: 10px; }"
        )
        self.preview_layout = QVBoxLayout(self.preview)
        self.preview_layout.setContentsMargins(16, 16, 16, 16)
        self.preview_layout.setSpacing(8)
        right.addWidget(self.preview, 1)

        self.playing_label = QLabel("正在演示：-")
        self.playing_label.setStyleSheet("color: #6e7076; font-size: 11px;")
        right.addWidget(self.playing_label)

        # 速度档位
        # 速度档（五档，竖着排才放得下）
        speed_row = QHBoxLayout()
        speed_row.setSpacing(10)
        speed_label = QLabel("速度")
        speed_label.setStyleSheet("color: #a0a1a7; font-size: 12px;")
        speed_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        speed_row.addWidget(speed_label)

        speed_col = QVBoxLayout()
        speed_col.setSpacing(0)
        self.speed_group = QButtonGroup(self)
        for key in SPEED_ORDER:
            rb = QRadioButton(speed_text(key))
            rb.setProperty("speed_key", key)
            if key == anim_settings.get_speed_key():
                rb.setChecked(True)
            rb.toggled.connect(self._on_speed_changed)
            self.speed_group.addButton(rb)
            speed_col.addWidget(rb)
        speed_row.addLayout(speed_col)
        speed_row.addStretch()

        self.replay_btn = QPushButton("再演一遍")
        self.replay_btn.clicked.connect(self._preview)
        speed_row.addWidget(self.replay_btn, 0, Qt.AlignmentFlag.AlignTop)
        right.addLayout(speed_row)

        body.addLayout(right, 1)
        root.addLayout(body, 1)

        # ---------- 底部按钮 ----------
        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        # 预览调度器（每次重播都换一套参数，所以这里先建一个占位的）
        self._stagger = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._preview)

        self._select_current()
        self._update_detail(anim_settings.get_preset_key())
        # 打开就先演一遍当前这一档
        self._preview_timer.start(PREVIEW_DELAY_MS)

    # ---------- 列表 / 速度 ----------

    def _select_current(self):
        current = anim_settings.get_preset_key()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == current:
                self.list.setCurrentRow(i)
                return
        self.list.setCurrentRow(0)

    def _current_preset(self):
        """选中的风格 + 速度档，合成实际生效的参数"""
        key = anim_settings.get_preset_key()
        return scaled(preset(key), speed_factor(anim_settings.get_speed_key()))

    def _on_preset_changed(self, item, _previous=None):
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        # 选中即生效并落盘（不搞"确定/取消"那套：好不好得看了才知道）
        anim_settings.set_preset_key(key)
        self._update_detail(key)
        self._preview_timer.start(PREVIEW_DELAY_MS)

    def _on_speed_changed(self, checked):
        if not checked:
            return
        rb = self.sender()
        key = rb.property("speed_key")
        anim_settings.set_speed_key(key)
        self._update_detail(anim_settings.get_preset_key())
        self._preview_timer.start(PREVIEW_DELAY_MS)

    def _update_detail(self, key: str):
        base = preset(key)
        p = self._current_preset()
        bounce = "有回弹" if base.style == ANIM_BOUNCE else "无回弹"
        self.detail.setText(
            f"<b>{base.name}</b><br>{base.description}<br><br>"
            f"方向：{direction_text(base.direction)}　"
            f"时长：{p.duration}ms（原始 {base.duration}ms）　"
            f"错峰：{p.interval}ms<br>"
            f"位移：{p.offset}px　{bounce}"
        )

    # ---------- 预览 ----------

    def _make_preview_card(self, index: int) -> QFrame:
        """一张示意卡片：左边一条强调色，滑进来时更容易看清位移"""
        accent = PRESET_COLORS.get(anim_settings.get_preset_key(), "#5ec269")
        card = QFrame()
        card.setFixedHeight(44)
        card.setStyleSheet(
            f"background-color: #232428;"
            f" border: 1px solid #2e3034; border-left: 3px solid {accent};"
            f" border-radius: 8px;"
        )
        inner = QHBoxLayout(card)
        inner.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel(f"示意卡片 {index + 1}")
        lbl.setStyleSheet("color: #d8d8dc; font-size: 12px; border: none;")
        inner.addWidget(lbl)
        inner.addStretch()
        return card

    def _clear_preview(self):
        """把预览区清空（连队里没播的一起丢掉）"""
        if self._stagger is not None:
            self._stagger.clear()
        while self.preview_layout.count():
            item = self.preview_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._rows = []

    def _preview(self):
        """按当前设置（风格 + 速度）重播一遍预览

        ⚠️ 顺序很关键：**先 add() 包成行，再 addWidget**。
        反过来的话 add() 会把卡片从布局里摘走（它内部要 setParent(None) 藏起来），
        预览区就变成空白 —— 这个坑踩过。
        """
        self._clear_preview()

        p = self._current_preset()
        self.playing_label.setText(f"正在演示：{p.name}")
        self._stagger = StaggerReveal(
            self.preview,
            interval=p.interval,
            initial_offset=p.offset,
            direction=p.direction,
            style=p.style,
            duration=p.duration,
            overshoot=p.overshoot,
            bounce_ratio=p.bounce_ratio,
            ease=p.ease,
        )

        self._rows = []
        for i in range(PREVIEW_COUNT):
            # duration 在构造 StaggerReveal 时就传下去了，这里不用再设
            row = self._stagger.add(self._make_preview_card(i))
            self.preview_layout.addWidget(row)
            self._rows.append(row)
        self.preview_layout.addStretch()

        self._stagger.start()

    def closeEvent(self, event):
        self._preview_timer.stop()
        if self._stagger is not None:
            self._stagger.clear()
        super().closeEvent(event)
