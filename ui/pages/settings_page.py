"""设置页：游戏目录 / Java / 内存 / 其他

修掉的两个问题：
1. 内存的"最小"和"最大"以前各管各的取值范围，可以设成 min > max，
   于是 v0.2.0 会拼出 -Xms4096M -Xmx2048M，JVM 直接拒绝启动。
   现在两边联动：顶到边界时自动把另一边推上去。
2. 以前 QSpinBox 每动一格就 config.set() → save()，按住上下箭头连点会写
   几十次 config.json（你配置文件里那个 min_memory: 514 就是这么来的）。
   现在加 400ms 防抖，而且一次 save() 写完两个值。
"""

from pathlib import Path

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget
)

from core.config import config
from core.versions import default_minecraft_dir


class SettingsPage(QWidget):
    config_changed = pyqtSignal()

    SAVE_DELAY_MS = 400

    def __init__(self):
        super().__init__()
        # 初始化期间不要触发任何写盘
        self._loading = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(8)

        title = QLabel("设置")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        subtitle = QLabel("改动会立即生效并写入 %APPDATA%/MCLuncher/config.json")
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(subtitle)

        layout.addSpacing(14)

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

    # ---------- 卡片工厂 ----------

    @staticmethod
    def _card(title: str):
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 16, 20, 18)
        box.setSpacing(10)
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        box.addWidget(label)
        return card, box

    # ---------- 游戏目录 ----------

    def _make_mc_dir_card(self):
        card, box = self._card("游戏目录")

        row = QHBoxLayout()
        row.setSpacing(10)

        self.mc_dir_input = QLineEdit()
        self.mc_dir_input.setPlaceholderText("留空 = 使用默认路径")
        self.mc_dir_input.setText(config.get("minecraft_dir", ""))
        row.addWidget(self.mc_dir_input, 1)

        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse_mc_dir)
        row.addWidget(browse_btn)

        apply_btn = QPushButton("应用")
        apply_btn.setObjectName("PrimaryButton")
        apply_btn.clicked.connect(self._save_mc_dir)
        row.addWidget(apply_btn)

        reset_btn = QPushButton("恢复默认")
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
        suffix = "" if exists else "    \u26a0 该目录不存在"
        self.mc_dir_hint.setText(f"当前生效：{effective}{suffix}")

    def _browse_mc_dir(self):
        start = self.mc_dir_input.text().strip() or str(default_minecraft_dir())
        chosen = QFileDialog.getExistingDirectory(self, "选择 .minecraft 目录", start)
        if chosen:
            self.mc_dir_input.setText(chosen)

    def _save_mc_dir(self):
        path_text = self.mc_dir_input.text().strip()

        if path_text:
            path = Path(path_text)
            if not path.is_dir():
                QMessageBox.warning(self, "路径无效", f"目录不存在：\n{path}")
                return
            if not (path / "versions").is_dir():
                reply = QMessageBox.question(
                    self, "目录可能不对",
                    f"选中的目录里没有 versions 文件夹：\n{path}\n\n仍要使用吗？",
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
        self.java_input.setPlaceholderText("自动查找")
        self.java_input.setText(config.get("java_path", ""))
        self.java_input.setEnabled(False)
        row.addWidget(self.java_input, 1)

        box.addLayout(row)
        # 旧版本这里 placeholder 写了"（暂未实现）"，右边又挂了一个"（暂未实现）"标签，
        # 一句话说了两遍。现在只留这一行说明。
        hint = QLabel("暂未实现 —— 等 v0.2.0 启动功能落地时一起做（需要按版本挑 Java 8 / 17 / 21）。")
        hint.setObjectName("HintText")
        hint.setWordWrap(True)
        box.addWidget(hint)

        return card

    # ---------- 内存 ----------

    def _make_memory_card(self):
        card, box = self._card("内存")

        row = QHBoxLayout()
        row.setSpacing(10)

        row.addWidget(self._labeled("最小"))
        self.min_mem = QSpinBox()
        self.min_mem.setRange(512, 32768)
        self.min_mem.setSingleStep(512)
        self.min_mem.setSuffix(" MB")
        self.min_mem.setValue(int(config.get("min_memory", 512)))
        self.min_mem.valueChanged.connect(self._on_min_memory_changed)
        row.addWidget(self.min_mem)

        row.addSpacing(18)
        row.addWidget(self._labeled("最大"))
        self.max_mem = QSpinBox()
        self.max_mem.setRange(512, 65536)
        self.max_mem.setSingleStep(512)
        self.max_mem.setSuffix(" MB")
        self.max_mem.setValue(int(config.get("max_memory", 2048)))
        self.max_mem.valueChanged.connect(self._on_max_memory_changed)
        row.addWidget(self.max_mem)

        row.addStretch()
        box.addLayout(row)

        hint = QLabel("最小堆不能大于最大堆 —— 两边会自动联动。")
        hint.setObjectName("HintText")
        box.addWidget(hint)

        return card

    @staticmethod
    def _labeled(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

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

        self.close_checkbox = QCheckBox("启动游戏后关闭启动器")
        self.close_checkbox.setChecked(bool(config.get("close_on_launch")))
        self.close_checkbox.stateChanged.connect(self._on_close_on_launch_changed)
        box.addWidget(self.close_checkbox)

        return card

    def _on_close_on_launch_changed(self, state: int):
        if self._loading:
            return
        config.set("close_on_launch", bool(state))
