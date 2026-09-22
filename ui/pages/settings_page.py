"""设置页：游戏目录 / Java / 内存 / 其他

主题和界面语言搬到「个性化」页了 —— "看着舒服"（颜色、语言）和"游戏怎么跑"
（目录、Java、内存）不是一类东西，混在一页里只会越长越乱。

修掉的历史问题：
1. 内存的"最小"和"最大"以前各管各的取值范围，可以设成 min > max，
   于是 v0.2.0 会拼出 -Xms4096M -Xmx2048M，JVM 直接拒绝启动。
   现在两边联动：顶到边界时自动把另一边推上去。
2. 以前 QSpinBox 每动一格就 config.set() → save()，按住上下箭头连点会写
   几十次 config.json。现在加 400ms 防抖，而且一次 save() 写完两个值。
3. Java 那行 placeholder 写了"（暂未实现）"、右边又挂一个标签，一句话说了两遍。
"""

from pathlib import Path

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget
)

from core.config import config
from core.i18n import tr
from core.versions import default_minecraft_dir
from ui.dialogs.about_dialog import AboutDialog
from ui.translatable import TranslatableWidget


class SettingsPage(TranslatableWidget):
    config_changed = pyqtSignal()

    SAVE_DELAY_MS = 400

    # 表单控件的统一宽度。
    #
    # 这里可以安心用固定值，因为 QLineEdit 内容太长时会**自己横向滚动**，
    # 不会把文字挤没；下拉框最多让长选项省略号显示。
    # 但 QLabel / QPushButton 那种"文字必须完整放得下"的控件千万不要锁死宽度
    # —— 界面文字会随语言变长（"启动游戏" → "Launch Game"）。
    FIELD_WIDTH = 300

    def __init__(self):
        super().__init__()
        # 初始化期间不要触发任何写盘
        self._loading = True

        # 内容比窗口高时要有滚动条，否则卡片会被压扁、底部被切掉。
        # （窗口最小高度 580，而这个页面几张卡片加起来超过它）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(8)

        layout.addWidget(self.label("设置", "PageTitle"))
        layout.addWidget(self.label(
            "改动会立即生效并写入 %APPDATA%/MCLuncher/config.json", "PageSubtitle"
        ))

        layout.addSpacing(14)

        layout.addWidget(self._make_mc_dir_card())
        layout.addWidget(self._make_java_card())
        layout.addWidget(self._make_memory_card())
        layout.addWidget(self._make_misc_card())
        layout.addWidget(self._make_about_card())
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

    # ---------- 游戏目录 ----------

    def _make_mc_dir_card(self):
        card, box = self._card("游戏目录")

        row = QHBoxLayout()
        row.setSpacing(10)

        self.mc_dir_input = QLineEdit()
        self.mc_dir_input.setText(config.get("minecraft_dir", ""))
        # 这一行右边有三个按钮，而按钮宽度**随语言变**（中文"恢复默认"86px，
        # 英文"Reset"99px，长一点的译文还会更宽）。所以输入框不能固定死，
        # 给它一个范围：宽的时候涨到 300（和别的控件对齐），挤的时候缩到 200。
        self.mc_dir_input.setMinimumWidth(200)
        self.mc_dir_input.setMaximumWidth(self.FIELD_WIDTH)
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

        row.addStretch()
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
        self.java_input.setFixedWidth(self.FIELD_WIDTH)
        self.bind(self.java_input, "自动查找", "placeholderText")
        row.addWidget(self.java_input)
        row.addStretch()

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

    # ---------- 关于 ----------

    def _make_about_card(self):
        """软件信息不占导航项，收在这里弹对话框

        这类内容一辈子看一次，占一个常驻入口不划算（PCL2 也是收在设置里的）。
        """
        card, box = self._card("关于")

        box.addWidget(self.label("版本信息、隐私说明和开源许可", "HintText"))

        row = QHBoxLayout()
        about_btn = self.button("查看软件信息")
        about_btn.clicked.connect(self._open_about)
        row.addWidget(about_btn)
        row.addStretch()
        box.addLayout(row)

        return card

    def _open_about(self):
        AboutDialog(self).exec()

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
