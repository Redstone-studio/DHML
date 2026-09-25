"""
可折叠分组（带展开动画）
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QToolButton, QSizePolicy
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer, QAbstractAnimation,
)
from ui.widgets.version_card import VersionCard
from ui.widgets.slide_in import SlideInRow, StaggerReveal
from ui.widgets.clip_box import ClipBox
from ui.widgets import anim_settings
from ui.widgets.anim_prefs import preset_for_versions, scaled, speed_factor

# 展开动画（220ms）跑完之后，隔这么久再开始放组内卡片
REVEAL_DELAY_MS = 240


class CollapsibleGroup(QWidget):
    # 和 VersionCard 一样带 (版本, 游戏版本, 加载器) —— 直接透传
    download_requested = pyqtSignal(dict, str, str)

    def __init__(self, title: str, versions: list,
                 slide_duration: int = None, slide_offset: int = None,
                 slide_interval: int = None, slide_style: str = None,
                 slide_direction: str = None, slide_overshoot: int = None,
                 slide_bounce_ratio: float = None, slide_ease: str = None,
                 game_version: str = "", loader: str = ""):
        """slide_* 这一串是给组内卡片用的动画参数。

        调用方（详情页）会把「动效」设置里选中的那一档传进来；
        不传就按当前设置在构造时取一次 —— 这样单独 new 一个分组出来也不会用错风格。

        `game_version` / `loader` 是这个分组对应的组合（分组标题就是它俩拼的），
        下载时要靠它们定位版本文件夹。
        """
        super().__init__()
        self.versions = versions
        self.game_version = game_version
        self.loader = loader
        self._loaded = False

        _p = scaled(preset_for_versions(anim_settings.get_preset_key()),
                    speed_factor(anim_settings.get_speed_key()))
        slide_duration = _p.duration if slide_duration is None else slide_duration
        slide_offset = _p.offset if slide_offset is None else slide_offset
        slide_interval = _p.interval if slide_interval is None else slide_interval
        slide_style = _p.style if slide_style is None else slide_style
        slide_direction = _p.direction if slide_direction is None else slide_direction
        slide_overshoot = _p.overshoot if slide_overshoot is None else slide_overshoot
        slide_bounce_ratio = (_p.bounce_ratio if slide_bounce_ratio is None
                              else slide_bounce_ratio)
        slide_ease = _p.ease if slide_ease is None else slide_ease

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toggle_btn = QToolButton()
        self.toggle_btn.setText(title)
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(False)
        self.toggle_btn.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.refresh_style()
        self.toggle_btn.clicked.connect(self._on_toggle)
        layout.addWidget(self.toggle_btn)

        # 内容放在一个"裁剪盒"里：
        #   收起动画改的是 box 的 maximumHeight（外层布局跟着收），
        #   而里面的 content 高度**固定不动** —— 这样组内卡片一个像素都不会被
        #   重新布局，收起时就不会出现"标签和字闪一下"（用户报的就是这个）。
        #   原来的写法是直接把 content 的 maximumHeight 压到 0，等于每帧让
        #   卡片重新排一遍，所以会闪。
        self.box = ClipBox()
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(20, 6, 0, 6)
        self.content_layout.setSpacing(6)
        self.box.set_content(self.content, 1)     # 真实高度等加载内容后才知道
        self.box.setVisible(False)
        layout.addWidget(self.box)

        self.anim = QPropertyAnimation(self.box, b"maximumHeight")
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.anim.valueChanged.connect(self._on_anim_value)
        self.anim.finished.connect(self._after_anim)

        # 版本卡片的入场动画（展开时才播，见 _on_toggle）。
        # 参数来自「动效」设置里选中的那一档（由详情页传进来）
        self.slide_params = dict(
            duration=slide_duration, offset=slide_offset,
            style=slide_style, direction=slide_direction,
            overshoot=slide_overshoot, bounce_ratio=slide_bounce_ratio,
            ease=slide_ease,
        )
        self.stagger = StaggerReveal(
            self, interval=slide_interval, initial_offset=slide_offset,
            direction=slide_direction, style=slide_style,
            overshoot=slide_overshoot, bounce_ratio=slide_bounce_ratio,
            ease=slide_ease,
        )
        self._rows = []
        # 展开动画结束后隔一小会儿再放卡片（见 _on_toggle 里的说明）
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self.stagger.start)

    def refresh_style(self):
        """按当前"卡片透明度"重建样式，并顺带刷新组内已建出来的版本卡片

        ⚠️ 这个方法在 __init__ 里（self._rows 还没建的时候）就会被调一次，
        所以取 _rows 要用 getattr 兜底，不能直接点属性。
        """
        from ui.widgets import card_style
        self.toggle_btn.setStyleSheet(card_style.group_button_qss())
        for row in getattr(self, "_rows", []):
            for card in row.findChildren(VersionCard):
                card.refresh_style()

    def _on_toggle(self):
        checked = self.toggle_btn.isChecked()

        if checked:
            self.toggle_btn.setArrowType(Qt.ArrowType.DownArrow)
            if not self._loaded:
                self._load_content()
            # 按**准确的**完整高度摆好内容（逐行累加算的，不用 sizeHint ——
            # 卡片还没滑入时 sizeHint 会偏小，导致最后一行被裁掉 1px）
            self._full_height = self._expanded_height()
            self.box.set_full_height(self._full_height)
            self.box.show_full()
            self.box.set_visible_height(0)
            self.box.setVisible(True)
            self.anim.stop()
            self.anim.setStartValue(0)
            self.anim.setEndValue(self._full_height)
            self.anim.start()
            # ⚠️ 卡片滑入要**等容器展开完**再开始：
            # 容器高度是 0 → target 动过去的，中间一直夹着内容。
            # 如果这时候卡片也在滑，前几张会被夹掉大半，看着就是"哗一下闪出来"，
            # 而不是一张张滑进来。所以先展开、再放卡片。
            self._reveal_timer.start()
        else:
            self.toggle_btn.setArrowType(Qt.ArrowType.RightArrow)
            # 收起来时叫停：不然动画会打在高度为 0 的内容里，白跑一遍
            self._reveal_timer.stop()
            self.stagger.clear()
            current = self.box.height()
            self.anim.stop()
            self.anim.setStartValue(current)
            self.anim.setEndValue(0)
            self.anim.start()

    def _on_anim_value(self, value):
        """动画每帧：只改"能看到多少"，内容纹丝不动（所以不会闪）"""
        self.box.set_visible_height(value)

    def _expanded_height(self) -> int:
        """展开后内容该有多高

        ⚠️ **不能用 content.sizeHint()**：组里的卡片是被包进 SlideInRow 的，
        而 SlideInRow 在轮到它之前是隐藏的（要靠滑入动画才出现），隐藏的子控件
        不计入 sizeHint —— 结果就是展开高度算成只剩边距的十几像素，
        表现成"点了一下没反应，看不到下载按钮"（这个 bug 踩过）。

        所以自己把每一行的高度加起来算，不依赖布局对隐藏控件的态度。
        """
        margins = self.content_layout.contentsMargins()
        rows = [self.content_layout.itemAt(i).widget()
                for i in range(self.content_layout.count())]
        rows = [w for w in rows if w is not None]
        if not rows:
            return 0
        spacing = self.content_layout.spacing() * max(0, len(rows) - 1)
        return (sum(w.sizeHint().height() for w in rows) + spacing
                + margins.top() + margins.bottom())

    def _after_anim(self):
        if not self.toggle_btn.isChecked():
            # 收完了：彻底隐藏（不占布局空间）
            self.box.set_visible_height(0)
            self.box.setVisible(False)
        else:
            # 展开完：裁剪放开，高度咬死在内容高度上
            self.box.show_full()

    def resizeEvent(self, event):
        """宽度变了要重算展开高度

        卡片里的文字会换行、标签会重排，行数变了高度就变了。
        只在**展开且没在动画中**时重算 —— 不做的话窗口变宽后内容会被裁掉一截。
        """
        super().resizeEvent(event)
        if (self.toggle_btn.isChecked() and self._loaded
                and self.anim.state() != QAbstractAnimation.State.Running):
            self._full_height = self._expanded_height()
            self.box.set_full_height(self._full_height)
            self.box.show_full()

    def _load_content(self):
        for v in self.versions:
            card = VersionCard(v, self.game_version, self.loader)
            card.download_requested.connect(self.download_requested.emit)
            # 包一层 SlideInRow，但**先不播** —— 展开的时候才播（见 _on_toggle）
            row = SlideInRow(card, **self.slide_params)
            self.stagger.add(row)
            self.content_layout.addWidget(row)
            self._rows.append(row)
        self._loaded = True

    def closeEvent(self, event):
        self.stagger.clear()
        super().closeEvent(event)
