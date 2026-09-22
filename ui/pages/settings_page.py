"""设置页：语言 / 游戏目录 / Java / 内存 / 其他

修掉的历史问题：
1. 内存的"最小"和"最大"以前各管各的取值范围，可以设成 min > max，
   于是 v0.2.0 会拼出 -Xms4096M -Xmx2048M，JVM 直接拒绝启动。
   现在两边联动：顶到边界时自动把另一边推上去。
2. 以前 QSpinBox 每动一格就 config.set() → save()，按住上下箭头连点会写
   几十次 config.json。现在加 400ms 防抖，而且一次 save() 写完两个值。
3. Java 那行 placeholder 写了"（暂未实现）"、右边又挂一个标签，一句话说了两遍。

文案：静态文字走 self.label()/self.button()/self.bind()；
目录提示是生成的，在 retranslate() 里重建。
"""

from pathlib import Path

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout
)

from core import i18n
from core.config import config
from core.i18n import tr
from core.versions import default_minecraft_dir
from ui.translatable import TranslatableWidget


class SettingsPage(TranslatableWidget):
    config_changed = pyqtSignal()
    language_changed = pyqtSignal(str)

    SAVE_DELAY_MS = 400

    def __init__(self):
        super().__init__()
        # 初始化期间不要触发任何写盘
        self._loading = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(8)

        layout.addWidget(self.label("设置", "PageTitle"))
        layout.addWidget(self.label(
            "改动会立即生效并写入 %APPDATA%/MCLuncher/config.json", "PageSubtitle"
        ))

        layout.addSpacing(14)

        layout.addWidget(self._make_language_card())
        layout.addWidget(self._make_mc_dir_card())
        layout.addWidget(self._make_java_card())
        layout.addWidget(self._make_memory_card())
        layout.addWidget(self._make_misc_card())
        layout.addStretch()

        # 内存数值的防抖保存
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._flush_memory)

        self._refresh_mc_dir_hint()
        self._loading = False

    # ---------- 语言切换 ----------

    def retranslate(self):
        super().retranslate()
        self._fill_language_combo()
        self._refresh_mc_dir_hint()

    # ---------- 卡片工厂 ----------

    def _card(self, title: str):
        """建一张卡片，返回 (卡片, 内容布局)。卡片标题也走文案系统"""
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 16, 20, 18)
        box.setSpacing(10)
        box.addWidget(self.label(title, "SectionTitle"))
        return card, box

    # ---------- 界面语言 ----------

    def _make_language_card(self):
        card, box = self._card("界面语言")

        row = QHBoxLayout()
        row.setSpacing(10)

        field = self.label("语言", "FieldLabel")
        row.addWidget(field)

        self.language_combo = QComboBox()
        self.language_combo.setMinimumWidth(180)
        self.language_combo.currentIndexChanged.connect(self._on_language_selected)
        row.addWidget(self.language_combo)
        row.addStretch()
        box.addLayout(row)

        box.addWidget(self.label("切换后立刻生效，不需要重启。", "HintText"))

        self._fill_language_combo()
        return card

    def _fill_language_combo(self):
        """语言名用自己的语言显示（简体中文 / English），这是通行做法"""
        current = i18n.current_language()
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        for code in i18n.available_languages():
            self.language_combo.addItem(i18n.language_name(code), code)
        index = self.language_combo.findData(current)
        self.language_combo.setCurrentIndex(index if index >= 0 else 0)
        self.language_combo.blockSignals(False)

    def _on_language_selected(self, _index: int):
        if self._loading:
            return
        code = self.language_combo.currentData()
        if code and code != i18n.current_language():
            # 真正的切换交给主窗口做（它负责通知所有页面重设文字）
            self.language_changed.emit(code)

    # ---------- 游戏目录 ----------

    def _make_mc_dir_card(self):
        card, box = self._card("游戏目录")

        row = QHBoxLayout()
        row.setSpacing(10)

        self.mc_dir_input = QLineEdit()
        self.mc_dir_input.setText(config.get("minecraft_dir", ""))
        self.bind(self.mc_dir_input, "留空 = 使用默认路径", "placeholderText")
        row.addWidget(self.mc_dir_input, 1)

        browse_btn = self.button("浏览…")
        browse_btn.clicked.connect(self._browse_mc_dir)
        row.addWidget(browse_btn)

        apply_btn = self.button("应用", "PrimaryButton")
        apply_btn.clicked.connect(self._save_mc_dir)
        row.addWidget(apply_btn)

        reset_btn = self.button("恢复默认")
        reset_btn.clicked.connect(self._reset_mc_dir)
        row.addWidget(reset_btn)

        box.addLayout(row)

        self.mc_dir_hint = QLabel()
        self.mc_dir_hint.setObjectName("HintText")
        self.mc_dir_hint.setWordWrap(True)
        box.addWidget(self.mc_dir_hint)

        return card

    def _refresh_mc_dir_hint(self):
        effective = default_minecraft_dir()
        # 用 try 包一下：权限有问题的目录连 is_dir() 都可能抛异常
        try:
            exists = effective.is_dir()
        except OSError:
            exists = False
        suffix = "" if exists else "    " + tr("⚠ 该目录不存在")
        self.mc_dir_hint.setText(tr("当前生效：{path}{suffix}", path=effective, suffix=suffix))

    def _browse_mc_dir(self):
        start = self.mc_dir_input.text().strip() or str(default_minecraft_dir())
        chosen = QFileDialog.getExistingDirectory(self, tr("选择 .minecraft 目录"), start)
        if chosen:
            self.mc_dir_input.setText(chosen)

    def _save_mc_dir(self):
        path_text = self.mc_dir_input.text().strip()

        if path_text:
            path = Path(path_text)
            if not path.is_dir():
                QMessageBox.warning(self, tr("路径无效"), tr("目录不存在：\n{path}", path=path))
                return
            if not (path / "versions").is_dir():
                reply = QMessageBox.question(
                    self, tr("目录可能不对"),
                    tr("选中的目录里没有 versions 文件夹：\n{path}\n\n仍要使用吗？", path=path),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        config.set("minecraft_dir", path_text)
        self._refresh_mc_dir_hint()
        self.config_changed.emit()

    def _reset_mc_dir(self):
        self.mc_dir_input.setText("")
        config.set("minecraft_dir", "")
        self._refresh_mc_dir_hint()
        self.config_changed.emit()

    # ---------- Java ----------

    def _make_java_card(self):
        card, box = self._card("Java")

        row = QHBoxLayout()
        row.setSpacing(10)

        self.java_input = QLineEdit()
        self.java_input.setText(config.get("java_path", ""))
        self.java_input.setEnabled(False)
        self.bind(self.java_input, "自动查找", "placeholderText")
        row.addWidget(self.java_input, 1)

        box.addLayout(row)
        box.addWidget(self.label(
            "暂未实现 —— 等 v0.2.0 启动功能落地时一起做（需要按版本挑 Java 8 / 17 / 21）。",
            "HintText"
        ))

        return card

    # ---------- 内存 ----------

    def _make_memory_card(self):
        card, box = self._card("内存")

        row = QHBoxLayout()
        row.setSpacing(10)

        row.addWidget(self.label("最小", "FieldLabel"))
        self.min_mem = QSpinBox()
        self.min_mem.setRange(512, 32768)
        self.min_mem.setSingleStep(512)
        self.min_mem.setSuffix(" MB")
        self.min_mem.setValue(int(config.get("min_memory", 512)))
        self.min_mem.valueChanged.connect(self._on_min_memory_changed)
        row.addWidget(self.min_mem)

        row.addSpacing(18)
        row.addWidget(self.label("最大", "FieldLabel"))
        self.max_mem = QSpinBox()
        self.max_mem.setRange(512, 65536)
        self.max_mem.setSingleStep(512)
        self.max_mem.setSuffix(" MB")
        self.max_mem.setValue(int(config.get("max_memory", 2048)))
        self.max_mem.valueChanged.connect(self._on_max_memory_changed)
        row.addWidget(self.max_mem)

        row.addStretch()
        box.addLayout(row)

        box.addWidget(self.label("最小堆不能大于最大堆 —— 两边会自动联动。", "HintText"))

        return card

    def _on_min_memory_changed(self, value: int):
        if self._loading:
            return
        # 最小超过最大时，把最大一起顶上去，而不是弹窗骂用户
        if value > self.max_mem.value():
            self.max_mem.blockSignals(True)
            self.max_mem.setValue(value)
            self.max_mem.blockSignals(False)
        self._save_timer.start()

    def _on_max_memory_changed(self, value: int):
        if self._loading:
            return
        if value < self.min_mem.value():
            self.min_mem.blockSignals(True)
            self.min_mem.setValue(value)
            self.min_mem.blockSignals(False)
        self._save_timer.start()

    def _flush_memory(self):
        """防抖之后一次写完两个值

        这里直接改 config.data 再调一次 save()，而不是调两次 config.set()，
        就是为了避免一次拖动写出两份 config.json。
        """
        config.data["min_memory"] = self.min_mem.value()
        config.data["max_memory"] = self.max_mem.value()
        config.save()

    # ---------- 其他 ----------

    def _make_misc_card(self):
        card, box = self._card("其他")

        self.close_checkbox = QCheckBox()
        self.close_checkbox.setChecked(bool(config.get("close_on_launch")))
        self.close_checkbox.stateChanged.connect(self._on_close_on_launch_changed)
        self.bind(self.close_checkbox, "启动游戏后关闭启动器")
        box.addWidget(self.close_checkbox)

        return card

    def _on_close_on_launch_changed(self, state: int):
        if self._loading:
            return
        config.set("close_on_launch", bool(state))
