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
    QListWidgetItem, QFrame, QRadioButton, QButtonGroup, QCheckBox
)

from core import appearance as anim_settings
from core.anim_prefs import (
    PRESET_ORDER, SPEED_ORDER, preset, scaled, speed_factor, speed_text,
    direction_text, ANIM_BOUNCE, DIR_VERTICAL,
)
from core.i18n import tr
from ui import theme_state
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


# 列表左边那个小色块取哪个主题色 —— 纯装饰，让几档风格一眼能分开。
# ⚠️ 色值**不写死**：一律从当前主题的调色板里取（见 ui/theme_state.py），
# 所以浅色主题和用户自定义的强调色都会跟着走。每个预设挑一个主题 token，
# 名字见 core/theme.py 的 DARK / LIGHT。
PRESET_COLOR_TOKENS = {
    "slide_left": "accent",
    "slide_up": "pack_text",
    "slide_left_bounce": "warn",
    "pcl_like": "text_dim",
    "slide_left_back": "accent_text",
    "slide_up_elastic": "danger",
    "pcl_recipe": "pack_border",
}


def _preset_color(key: str) -> str:
    """某一档预设的装饰色（从当前主题调色板里取，见上面的 token 表）"""
    palette = theme_state.palette()
    return palette.get(PRESET_COLOR_TOKENS.get(key, "accent")) or palette["accent"]


class AnimSettingsDialog(QDialog):
    """动效设置（模态）"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(tr("动效设置"))
        self.setModal(True)
        self.setMinimumSize(760, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel(tr("入场动画"))
        title.setObjectName("DialogTitle")
        root.addWidget(title)

        hint = QLabel(tr("列表里的卡片出现时怎么滑进来。选哪一档，右边立刻演一遍。"
                         "嫌快就调下面的速度。"))
        hint.setObjectName("HintText")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # ---------- 总开关 ----------
        # 放在最上面：它是"要不要动效"这个更上位的问题，下面的风格 / 速度
        # 都只是它的细节（关掉之后那一堆选项就没意义了，所以一起置灰）。
        self.enable_check = QCheckBox(tr("关闭动效（省性能）"))
        self.enable_check.setObjectName("AnimEnableCheck")
        self.enable_check.setChecked(not anim_settings.get_anim_enabled())
        self.enable_check.toggled.connect(self._on_enable_toggled)
        root.addWidget(self.enable_check)

        self.enable_hint = QLabel()
        self.enable_hint.setObjectName("HintText")
        self.enable_hint.setWordWrap(True)
        root.addWidget(self.enable_hint)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ---------- 左：预设列表 ----------
        self.list = QListWidget()
        self.list.setObjectName("AnimPresetList")
        self.list.setFixedWidth(240)
        for key in PRESET_ORDER:
            p = preset(key)
            item = QListWidgetItem(p.name)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setIcon(_swatch(_preset_color(key)))
            item.setToolTip(p.description)
            self.list.addItem(item)
        self.list.currentItemChanged.connect(self._on_preset_changed)
        body.addWidget(self.list)

        # ---------- 右：说明 + 预览 ----------
        right = QVBoxLayout()
        right.setSpacing(10)

        self.detail = QLabel()
        self.detail.setObjectName("AnimDetail")
        self.detail.setWordWrap(True)
        right.addWidget(self.detail)

        self.preview = QFrame()
        self.preview.setObjectName("PreviewBox")
        self.preview_layout = QVBoxLayout(self.preview)
        self.preview_layout.setContentsMargins(16, 16, 16, 16)
        self.preview_layout.setSpacing(8)
        right.addWidget(self.preview, 1)

        self.playing_label = QLabel(tr("正在演示：-"))
        self.playing_label.setObjectName("HintText")
        right.addWidget(self.playing_label)

        # 速度档位
        # 速度档（五档，竖着排才放得下）
        speed_row = QHBoxLayout()
        speed_row.setSpacing(10)
        speed_label = QLabel(tr("速度"))
        speed_label.setObjectName("HintText")
        speed_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        speed_row.addWidget(speed_label)

        speed_col = QVBoxLayout()
        speed_col.setSpacing(0)
        self.speed_group = QButtonGroup(self)
        for key in SPEED_ORDER:
            rb = QRadioButton(speed_text(key))
            rb.setObjectName("SpeedOption")
            rb.setProperty("speed_key", key)
            if key == anim_settings.get_speed_key():
                rb.setChecked(True)
            rb.toggled.connect(self._on_speed_changed)
            self.speed_group.addButton(rb)
            speed_col.addWidget(rb)
        speed_row.addLayout(speed_col)
        speed_row.addStretch()

        self.replay_btn = QPushButton(tr("再演一遍"))
        self.replay_btn.clicked.connect(self._preview)
        speed_row.addWidget(self.replay_btn, 0, Qt.AlignmentFlag.AlignTop)
        right.addLayout(speed_row)

        body.addLayout(right, 1)
        root.addLayout(body, 1)

        # ---------- 底部按钮 ----------
        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton(tr("关闭"))
        close_btn.setObjectName("PrimaryButton")
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
        self._sync_enabled_state()
        # 打开就先演一遍当前这一档
        self._preview_timer.start(PREVIEW_DELAY_MS)

    # ---------- 总开关 ----------

    def _on_enable_toggled(self, checked):
        """「关闭动效」勾上/取消：落盘 + 把下面的选项一起置灰

        ⚠️ 勾选框的语义是"**关闭**"，配置里的键是"**启用**"，这里别接反
        （接反了表现就是"勾上反而开始动"，很难一眼看出来）。
        """
        anim_settings.set_anim_enabled(not checked)
        self._sync_enabled_state()
        self._preview_timer.start(PREVIEW_DELAY_MS)

    def _sync_enabled_state(self):
        """按总开关把风格 / 速度 / 预览按钮置灰，并写明当前是什么状态"""
        on = anim_settings.get_anim_enabled()
        for w in (self.list, self.replay_btn):
            w.setEnabled(on)
        for rb in self.speed_group.buttons():
            rb.setEnabled(on)
        self.enable_hint.setText(
            tr("关掉可以省性能：列表直接出现，不再滑入 / 淡入。"
               "这里选过的风格和速度都留着，随时可以再打开。")
            if on else
            tr("动效已关闭：列表直接出现，不滑入也不淡入 —— 省掉每张卡片的"
               "动画和离屏合成。取消勾选就恢复。"))
        self._update_detail(anim_settings.get_preset_key())

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
        if not anim_settings.get_anim_enabled():
            # 关着的时候别把"方向 / 时长 / 位移"那些数字摆出来 —— 它现在不生效
            self.detail.setText(
                tr("<b>动效已关闭</b><br>列表直接出现，不滑入也不淡入。"))
            return
        base = preset(key)
        p = self._current_preset()
        bounce = tr("有回弹") if base.style == ANIM_BOUNCE else tr("无回弹")
        detail = tr("方向：{direction}　时长：{duration}ms（原始 {raw}ms）　"
                    "错峰：{interval}ms<br>位移：{offset}px　{bounce}",
                    direction=direction_text(base.direction),
                    duration=p.duration, raw=base.duration,
                    interval=p.interval, offset=p.offset, bounce=bounce)
        self.detail.setText(
            f"<b>{base.name}</b><br>{base.description}<br><br>{detail}"
        )

    # ---------- 预览 ----------

    def _make_preview_card(self, index: int) -> QFrame:
        """一张示意卡片：左边一条强调色，滑进来时更容易看清位移"""
        palette = theme_state.palette()
        accent = _preset_color(anim_settings.get_preset_key())
        card = QFrame()
        card.setFixedHeight(44)
        # 这张卡片是**自绘**的（叠在预览框上，走不了 #Card 那层全局样式），
        # 所以底色/边框也从调色板里取，别写死 —— 见 ui/theme_state.py
        card.setStyleSheet(
            f"background-color: {palette['bg_card']};"
            f" border: 1px solid {palette['border']};"
            f" border-left: 3px solid {accent};"
            f" border-radius: 8px;"
        )
        inner = QHBoxLayout(card)
        inner.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel(tr("示意卡片 {index}", index=index + 1))
        lbl.setStyleSheet(
            f"color: {palette['text_secondary']}; font-size: 12px; border: none;")
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
        on = anim_settings.get_anim_enabled()
        self.playing_label.setText(
            tr("正在演示：{name}", name=p.name) if on
            else tr("动效已关闭 —— 示意卡片直接显示，不滑不淡"))
        # animate 显式传下去：关掉动效时预览要**静态**（三张卡片一次全出来），
        # 不能还走"每隔 X 毫秒点名一张"那条路 —— 那看着像卡片一个个蹦出来
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
            animate=on,
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
