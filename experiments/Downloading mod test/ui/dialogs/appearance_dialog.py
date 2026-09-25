"""
背景设置

三种背景可以混着用：
  · 纯色        —— 不想折腾的时候用
  · 图片        —— 铺满 / 完整显示 / 居中原始大小 / 平铺 四种铺法
  · 压暗程度    —— 亮背景会把卡片和文字冲得看不清，这层黑色能救回来

改完**立刻生效**（主窗口实时重画），不用重启。
"""
from PyQt6.QtCore import Qt
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QSlider, QFrame, QColorDialog, QFileDialog,
    QCheckBox, QWidget
)

from ui.widgets import appearance
from ui.widgets.collapsible_section import CollapsibleSection


class AppearanceSettingsDialog(QDialog):
    """背景设置（模态）。

    canvas 传进来是为了改完立刻让主窗口重画。
    """

    def __init__(self, parent=None, canvas=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setWindowTitle("背景设置")
        self.setModal(True)
        self.setMinimumSize(640, 460)
        self.setStyleSheet("""
            QDialog { background-color: #1b1c1f; }
            QLabel { color: #e9e9ec; }
            QLineEdit {
                background-color: #2a2c30; color: #e9e9ec;
                border: 1px solid #3a3c42; border-radius: 6px;
                padding: 6px 10px; font-size: 12px;
            }
            QComboBox {
                background-color: #2a2c30; color: #e9e9ec;
                border: 1px solid #3a3c42; border-radius: 6px;
                padding: 6px 10px; font-size: 12px;
            }
            QPushButton {
                background-color: #2a2c30; color: #e9e9ec;
                border: 1px solid #3a3c42; border-radius: 6px;
                padding: 6px 14px; font-size: 12px;
            }
            QPushButton:hover { background-color: #34363a; border-color: #5ec269; }
            QSlider::groove:horizontal {
                height: 4px; background: #2e3034; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #5ec269; width: 12px; margin: -5px 0; border-radius: 6px;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("自定义背景")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        root.addWidget(title)

        hint = QLabel("图片随便放哪都行（建议 1920×1080 以上）。改完立刻生效。")
        hint.setStyleSheet("color: #a0a1a7; font-size: 12px;")
        root.addWidget(hint)

        bg = appearance.get_bg()

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        # ---------- 图片 ----------
        form.addWidget(QLabel("背景图"), 0, 0)
        self.path_edit = QLineEdit(bg["image_raw"])
        self.path_edit.setPlaceholderText("留空 = 不用图片")
        self.path_edit.editingFinished.connect(self._on_path_edited)
        form.addWidget(self.path_edit, 0, 1)

        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        form.addWidget(browse, 0, 2)

        # ---------- 铺法 ----------
        form.addWidget(QLabel("铺法"), 1, 0)
        self.mode_combo = QComboBox()
        for key in appearance.MODE_ORDER:
            self.mode_combo.addItem(appearance.MODES[key], key)
            self.mode_combo.setItemData(self.mode_combo.count() - 1,
                                        appearance.MODE_HINTS.get(key, ""),
                                        Qt.ItemDataRole.ToolTipRole)
        idx = self.mode_combo.findData(bg["mode"])
        self.mode_combo.setCurrentIndex(max(0, idx))
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addWidget(self.mode_combo, 1, 1, 1, 2)

        # 当前铺法的说明（跟着选项变）
        self.mode_hint = QLabel(appearance.MODE_HINTS.get(bg["mode"], ""))
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setStyleSheet("color: #6e7076; font-size: 11px;")
        form.addWidget(self.mode_hint, 2, 1, 1, 2)

        # ---------- 纯色 ----------
        form.addWidget(QLabel("纯色背景"), 3, 0)
        self.color_edit = QLineEdit(bg["color"])
        self.color_edit.setPlaceholderText(f"#rrggbb，留空 = {appearance.FALLBACK_COLOR}")
        self.color_edit.editingFinished.connect(self._on_color_edited)
        form.addWidget(self.color_edit, 3, 1)

        pick = QPushButton("选颜色…")
        pick.clicked.connect(self._pick_color)
        form.addWidget(pick, 3, 2)

        # ---------- 压暗 ----------
        form.addWidget(QLabel("压暗"), 4, 0)
        self.dim_slider = QSlider(Qt.Orientation.Horizontal)
        self.dim_slider.setRange(0, 200)
        self.dim_slider.setValue(bg["dim"])
        self.dim_slider.valueChanged.connect(self._on_dim_changed)
        form.addWidget(self.dim_slider, 4, 1)

        self.dim_label = QLabel(f"{bg['dim']}")
        self.dim_label.setStyleSheet("color: #a0a1a7; font-size: 12px;")
        self.dim_label.setFixedWidth(34)
        form.addWidget(self.dim_label, 4, 2)

        # ---------- 卡片透明度 ----------
        form.addWidget(QLabel("卡片透明度"), 5, 0)
        low, high = appearance.CARD_OPACITY_RANGE
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(low, high)
        self.opacity_slider.setValue(appearance.get_card_opacity())
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        form.addWidget(self.opacity_slider, 5, 1)

        self.opacity_label = QLabel(f"{appearance.get_card_opacity()}%")
        self.opacity_label.setStyleSheet("color: #a0a1a7; font-size: 12px;")
        self.opacity_label.setFixedWidth(40)
        form.addWidget(self.opacity_label, 5, 2)

        opacity_hint = QLabel("调小 → 卡片底色透出背景图（100% = 完全不透明）。"
                              "文字和图标不受影响，只有卡片底色变透。")
        opacity_hint.setWordWrap(True)
        opacity_hint.setStyleSheet("color: #6e7076; font-size: 11px;")
        form.addWidget(opacity_hint, 6, 1, 1, 2)

        # ---------- 入场淡入（卡顿排查用） ----------
        form.addWidget(QLabel("入场淡入"), 7, 0)
        self.fade_check = QCheckBox("卡片出现时带淡入 + 位移")
        self.fade_check.setChecked(appearance.get_anim_fade())
        self.fade_check.toggled.connect(self._on_fade_toggled)
        form.addWidget(self.fade_check, 7, 1, 1, 2)

        fade_hint = QLabel("关掉只剩位移。淡入要给每张卡片挂离屏合成，"
                           "**觉得动画卡就关它试试**（下一批卡片生效）。")
        fade_hint.setWordWrap(True)
        fade_hint.setStyleSheet("color: #6e7076; font-size: 11px;")
        form.addWidget(fade_hint, 8, 1, 1, 2)

        root.addLayout(form)

        # ---------- 游戏目录 ----------
        # 下载的落点全靠它：<游戏目录>/versions/<版本文件夹>/mods
        # 目录结构怎么解析见 core/mc_dir.py
        mc_title = QLabel("游戏目录")
        mc_title.setStyleSheet("font-size: 13px; font-weight: bold;")
        root.addWidget(mc_title)

        mc_row = QHBoxLayout()
        mc_row.setSpacing(8)
        self.mc_edit = QLineEdit(str(appearance.get_mc_dir() or ""))
        self.mc_edit.setPlaceholderText("还没找到 —— 点「浏览」选到 .minecraft 那一层")
        self.mc_edit.editingFinished.connect(self._on_mc_edited)
        mc_row.addWidget(self.mc_edit, 1)

        mc_browse = QPushButton("浏览…")
        mc_browse.clicked.connect(self._browse_mc_dir)
        mc_row.addWidget(mc_browse)

        self.mc_auto_btn = QPushButton("自动探测")
        self.mc_auto_btn.clicked.connect(self._auto_mc_dir)
        mc_row.addWidget(self.mc_auto_btn)

        root.addLayout(mc_row)

        self.mc_hint = QLabel()
        self.mc_hint.setWordWrap(True)
        self.mc_hint.setStyleSheet("color: #6e7076; font-size: 11px;")
        root.addWidget(self.mc_hint)
        self._refresh_mc_hint()

        note = QLabel("优先级：**有图片就用图片**，纯色只在没图片时生效"
                      "（所以想用纯色要先把背景图那一栏清空）。\n"
                      "提示：图片 + 压暗 90 左右，卡片和文字最清楚。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #6e7076; font-size: 11px;")
        root.addWidget(note)

        # ---------- 下载与访问（多线程） ----------
        # 开关打开 → 用滑块的值；关掉 → 一律用默认值，滑块收起
        self.multi_check = QCheckBox("多线程下载 / 访问（同时下载多个图标、并发请求）")
        self.multi_check.setChecked(appearance.get_multi_thread())
        self.multi_check.toggled.connect(self._on_multi_toggled)
        root.addWidget(self.multi_check)

        thread_inner = QWidget()
        thread_row = QHBoxLayout(thread_inner)
        thread_row.setContentsMargins(0, 0, 0, 0)
        thread_row.setSpacing(10)

        thread_row.addWidget(QLabel("线程数"))
        low, high = appearance.THREAD_RANGE
        self.thread_slider = QSlider(Qt.Orientation.Horizontal)
        self.thread_slider.setRange(low, high)
        self.thread_slider.setValue(appearance.get_thread_count())
        self.thread_slider.setTickInterval(4)
        self.thread_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.thread_slider.valueChanged.connect(self._on_threads_changed)
        thread_row.addWidget(self.thread_slider, 1)
        self.thread_label = QLabel(f"{self.thread_slider.value()}")
        self.thread_label.setStyleSheet("color: #a0a1a7; font-size: 12px;")
        self.thread_label.setFixedWidth(28)
        thread_row.addWidget(self.thread_label)

        thread_note = QLabel("范围 1~32，默认 8。开太多反而会互相抢带宽、也被服务端限速；"
                             "8~16 一般最舒服。")
        thread_note.setWordWrap(True)
        thread_note.setStyleSheet("color: #6e7076; font-size: 11px;")

        thread_box = QWidget()
        thread_col = QVBoxLayout(thread_box)
        thread_col.setContentsMargins(0, 0, 0, 0)
        thread_col.setSpacing(4)
        thread_col.addWidget(thread_inner)
        thread_col.addWidget(thread_note)

        # 复用收起动画（和版本分组同一套节奏）
        self.thread_section = CollapsibleSection("线程数", thread_box)
        root.addWidget(self.thread_section)
        # 初始状态跟着开关走，不放动画（打开设置不该先自己动一下）
        self._sync_thread_section(animate=False)

        root.addStretch()

        # ---------- 底部 ----------
        bottom = QHBoxLayout()
        reset = QPushButton("恢复默认")
        reset.clicked.connect(self._reset)
        bottom.addWidget(reset)
        bottom.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        root.addLayout(bottom)

    # ---------- 交互 ----------

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选一张背景图", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;所有文件 (*)")
        if not path:
            return
        self.path_edit.setText(path)
        self._save_and_apply()

    def _on_path_edited(self):
        self._save_and_apply()

    def _on_mode_changed(self, _index):
        key = self.mode_combo.currentData()
        self.mode_hint.setText(appearance.MODE_HINTS.get(key, ""))
        self._save_and_apply()

    def _on_color_edited(self):
        self._save_and_apply()

    def _on_dim_changed(self, value):
        self.dim_label.setText(str(value))
        self._save_and_apply()

    def _on_opacity_changed(self, value):
        self.opacity_label.setText(f"{value}%")
        appearance.save_key(appearance.KEY_CARD_OPACITY, value)
        # 已建出来的卡片不会自己变，让主窗口把它们重新套一遍样式
        window = self.parent()
        if window is not None and hasattr(window, "refresh_card_style"):
            window.refresh_card_style()

    def _on_fade_toggled(self, checked):
        appearance.save_key(appearance.KEY_ANIM_FADE, bool(checked))

    # ---------- 游戏目录 ----------

    def _refresh_mc_hint(self):
        """提示当前用的目录是什么情况（几个版本、含哪些加载器、是不是探测来的）"""
        from core import mc_dir as mcd
        current = self.mc_edit.text().strip()
        desc = mcd.describe_mc_dir(current)
        if current and appearance.is_mc_dir_auto():
            desc += "（自动探测的，没写进配置）"
        self.mc_hint.setText(
            f"{desc}\n下载会落到 <游戏目录>/versions/<版本号-加载器>/mods。")

    def _browse_mc_dir(self):
        start = self.mc_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择 .minecraft 目录", start)
        if not path:
            return
        from core import mc_dir as mcd
        # 用户很可能直接点进 versions/ 甚至某个版本文件夹里 —— 往上纠一层
        fixed = mcd.normalize_mc_dir(path)
        self.mc_edit.setText(str(fixed))
        appearance.set_mc_dir(fixed)
        self._refresh_mc_hint()

    def _on_mc_edited(self):
        from core import mc_dir as mcd
        text = self.mc_edit.text().strip()
        if not text:
            appearance.set_mc_dir("")          # 清空 = 回到自动探测
            self.mc_edit.setText(str(appearance.get_mc_dir() or ""))
        else:
            fixed = mcd.normalize_mc_dir(text)
            appearance.set_mc_dir(fixed)
            self.mc_edit.setText(str(fixed))
        self._refresh_mc_hint()

    def _auto_mc_dir(self):
        """清掉手选的值，让探测器重新挑一个"""
        from core import mc_dir as mcd
        mcd.forget_guess()
        appearance.set_mc_dir("")
        self.mc_edit.setText(str(appearance.get_mc_dir() or ""))
        self._refresh_mc_hint()

    # ---------- 多线程 ----------

    def _on_multi_toggled(self, checked):
        """开关一变就落盘，并带动画地收起/展开线程数那一段

        开关**打开**时滑块是展开的；关掉时收起、并且实际线程数回到默认值
        （滑块上填的数字还留着，下次打开能接着用）。
        """
        appearance.save_key(appearance.KEY_MULTI_THREAD, bool(checked))
        self._sync_thread_section(animate=True)

    def _sync_thread_section(self, animate: bool):
        on = self.multi_check.isChecked()
        self.thread_slider.setEnabled(on)
        self.thread_section.set_expanded(on, animate=animate)

    def _on_threads_changed(self, value):
        self.thread_label.setText(str(value))
        appearance.save_key(appearance.KEY_DOWNLOAD_THREADS, int(value))

    def _pick_color(self):
        from PyQt6.QtGui import QColor
        current = QColor(self.color_edit.text().strip() or appearance.FALLBACK_COLOR)
        color = QColorDialog.getColor(current, self, "选背景色")
        if color.isValid():
            self.color_edit.setText(color.name())
            self._save_and_apply()

    def _reset(self):
        for key, value in appearance.DEFAULT.items():
            appearance.save_key(key, value)
        self.path_edit.setText("")
        self.color_edit.setText("")
        self.mode_combo.setCurrentIndex(self.mode_combo.findData("fill"))
        self.dim_slider.setValue(appearance.DEFAULT[appearance.KEY_BG_DIM])
        self.opacity_slider.setValue(appearance.DEFAULT[appearance.KEY_CARD_OPACITY])
        self.fade_check.setChecked(appearance.DEFAULT[appearance.KEY_ANIM_FADE])
        self.multi_check.setChecked(appearance.DEFAULT[appearance.KEY_MULTI_THREAD])
        self.thread_slider.setValue(appearance.DEFAULT[appearance.KEY_DOWNLOAD_THREADS])
        self._sync_thread_section(animate=False)
        # 游戏目录也回到"自动探测"
        from core import mc_dir as mcd
        mcd.forget_guess()
        self.mc_edit.setText(str(appearance.get_mc_dir() or ""))
        self._refresh_mc_hint()
        self._apply()
        window = self.parent()
        if window is not None and hasattr(window, "refresh_card_style"):
            window.refresh_card_style()

    # ---------- 生效 ----------

    def _save_and_apply(self):
        appearance.save_key(appearance.KEY_BG_IMAGE, self.path_edit.text().strip())
        appearance.save_key(appearance.KEY_BG_COLOR, self.color_edit.text().strip())
        appearance.save_key(appearance.KEY_BG_MODE, self.mode_combo.currentData() or "fill")
        appearance.save_key(appearance.KEY_BG_DIM, self.dim_slider.value())
        self._apply()

    def _apply(self):
        if self.canvas is not None:
            self.canvas.reload()
