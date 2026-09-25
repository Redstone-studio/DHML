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

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget
)

from core.config import PORTABLE_MARKER, config, get_config_dir, is_portable
from core.java import scan_minecraft_dirs
from core.i18n import tr
from core.memory import (
    MEMORY_MAX, MEMORY_MIN, MEMORY_STEP, jvm_overhead_mb, recommend_memory,
    snap_memory, system_memory
)
from ui.dialogs.about_dialog import AboutDialog
from ui.tasks import QuickJavaScanTask
from ui.widgets.memory_bar import MemoryBar
from ui.widgets.switch import Switch
from ui.translatable import TranslatableWidget


# 内存滑块的档位常量搬到了 core/memory.py（版本设置页也要用同一套档位，
# 两处各写一份迟早会不一致）


def _gb(megabytes: int) -> str:
    """MB → "12.4"（配合文案里的 GB 用）"""
    return f"{max(0, int(megabytes)) / 1024:.1f}"


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
        # 写死路径是不对的：开了便携模式以后配置就不在 %APPDATA% 了。
        # 文案里带路径 → 动态生成（retranslate() 里会重贴）
        self.subtitle = QLabel()
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)
        self._refresh_subtitle()

        layout.addSpacing(14)

        layout.addWidget(self._make_java_card())
        layout.addWidget(self._make_memory_card())
        layout.addWidget(self._make_download_card())
        layout.addWidget(self._make_misc_card())
        layout.addWidget(self._make_about_card())
        layout.addStretch()

        # 内存数值的防抖保存
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._flush_memory)

        self._loading = False

    # ---------- 语言切换 ----------

    def retranslate(self):
        super().retranslate()
        # 内存那条的说明和推荐值按钮里都带数字，是生成的
        self._refresh_memory_bar()
        self._refresh_config_hint()
        self._refresh_subtitle()
        # 下载那条的说明也是生成的（里面有线程数）
        self._refresh_thread_controls()

    def _refresh_subtitle(self):
        self.subtitle.setText(
            tr("改动会立即生效并写入 {path}", path=get_config_dir() / "config.json"))

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

    # ---------- Java ----------

    def _make_java_card(self):
        card, box = self._card("Java")

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.label("使用哪个", "FieldLabel"))

        self.java_combo = QComboBox()
        self.java_combo.setFixedWidth(self.FIELD_WIDTH)
        self.java_combo.currentIndexChanged.connect(self._on_java_selected)
        row.addWidget(self.java_combo)

        self.java_rescan_btn = self.button("重新扫描")
        self.java_rescan_btn.clicked.connect(self._rescan_java)
        row.addWidget(self.java_rescan_btn)

        row.addStretch()
        box.addLayout(row)

        self.java_list_label = QLabel()
        self.java_list_label.setObjectName("HintText")
        self.java_list_label.setWordWrap(True)
        box.addWidget(self.java_list_label)

        box.addWidget(self.label(
            "「自动选择」会按每个版本要求的 Java 挑。手动指定时只有主版本对得上才会用它 —— "
            "对不上就自动挑，并在启动日志里说明。", "HintText"
        ))

        self._java_scan = None
        self._javas = []
        self._fill_java_combo()
        self._rescan_java()
        return card

    def _fill_java_combo(self):
        """下拉框：自动选择 + 扫到的每个 Java"""
        self.java_combo.blockSignals(True)
        self.java_combo.clear()
        self.java_combo.addItem(tr("自动选择（推荐）"), "")
        for info in self._javas:
            if info.usable:
                self.java_combo.addItem(f"Java {info.major} · {info.path}", info.path)

        # 配置里指定的那个要是没扫到也列出来，免得用户以为设置丢了
        saved = config.get("java_path", "")
        if saved and self.java_combo.findData(saved) < 0:
            self.java_combo.addItem(tr("手动指定：{path}", path=saved), saved)

        index = self.java_combo.findData(saved)
        self.java_combo.setCurrentIndex(index if index >= 0 else 0)
        self.java_combo.blockSignals(False)

        usable = [j for j in self._javas if j.usable]
        if usable:
            self.java_list_label.setText(
                tr("已找到 {n} 个 Java：", n=len(usable))
                + "\n" + "\n".join("    " + j.label() for j in usable)
            )
        else:
            self.java_list_label.setText(tr("没找到任何 Java，点「重新扫描」，或者自己装一个。"))

    def _rescan_java(self):
        if self._java_scan is not None and self._java_scan.isRunning():
            return
        self.java_rescan_btn.setEnabled(False)
        self.java_rescan_btn.setText(tr("扫描中…"))
        self._java_scan = QuickJavaScanTask(scan_minecraft_dirs(), self, use_cache=False)
        self._java_scan.done.connect(self._on_java_scan_done)
        self._java_scan.start()

    def _on_java_scan_done(self, javas):
        self._javas = javas
        self.java_rescan_btn.setEnabled(True)
        self.java_rescan_btn.setText(tr("重新扫描"))
        self._fill_java_combo()

    def _on_java_selected(self, _index: int):
        if self._loading:
            return
        path = self.java_combo.currentData() or ""
        if path != config.get("java_path", ""):
            config.set("java_path", path)
            self.config_changed.emit()

    # ---------- 内存 ----------

    def _make_memory_card(self):
        card, box = self._card("内存")

        # 系统内存 + 这个配置会占多少，画成一条 （见 ui/widgets/memory_bar.py）
        self.memory_bar = MemoryBar()
        box.addWidget(self.memory_bar)

        self.memory_legend = QLabel()
        self.memory_legend.setObjectName("HintText")
        self.memory_legend.setWordWrap(True)
        box.addWidget(self.memory_legend)

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

        row.addStretch()

        # 文字里带数字，所以不用 self.button() 绑定（那个只认固定文案），
        # 由 _refresh_memory_bar() 负责设置
        self.recommend_btn = QPushButton()
        self.recommend_btn.clicked.connect(self._apply_recommended_memory)
        row.addWidget(self.recommend_btn)

        box.addLayout(row)
        box.addSpacing(4)

        # ---------- 最大内存：滑块 ----------
        # 用滑块而不是数字框：拖比点小箭头快得多，而且整条的长度本身就是
        # "物理内存有多大"的直观提示（滑块拉到底 = 全部内存，配到一半以上就过了红线）。
        info = system_memory()
        upper = min(MEMORY_MAX, max(4096, info.total_mb)) if info.ok else MEMORY_MAX
        upper = (upper // MEMORY_STEP) * MEMORY_STEP

        slider_row = QHBoxLayout()
        slider_row.setSpacing(10)
        slider_row.addWidget(self.label("最大", "FieldLabel"))

        self.max_mem = QSlider(Qt.Orientation.Horizontal)
        self.max_mem.setObjectName("MemorySlider")
        self.max_mem.setRange(MEMORY_MIN, upper)
        self.max_mem.setSingleStep(MEMORY_STEP)
        self.max_mem.setPageStep(MEMORY_STEP * 4)
        self.max_mem.setTickInterval(MEMORY_STEP * 8)
        self.max_mem.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.max_mem.setValue(int(config.get("max_memory", 2048)))
        # ⚠️ 先 setValue 再 connect：不然初始化那一下会被当成"用户拖了"，
        # 于是去夹最小堆、还会写一次配置
        self.max_mem.valueChanged.connect(self._on_max_memory_changed)
        slider_row.addWidget(self.max_mem, 1)

        # 数字单独一个标签（不是数字框）：固定宽度，拖的时候不会把布局推来推去
        self.max_value = QLabel(f"{self.max_mem.value()} MB")
        self.max_value.setObjectName("FieldLabel")
        self.max_value.setFixedWidth(90)
        self.max_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
        slider_row.addWidget(self.max_value)

        box.addLayout(slider_row)

        box.addWidget(self.label("最小堆不能大于最大堆 —— 两边会自动联动。", "HintText"))
        box.addWidget(self.label(
            "「游戏最多占用」是堆上限，实际用多少看玩法，整合包通常要往上调；"
            "越过红线（物理内存的 60%）容易开始换页，表现是越玩越卡。", "HintText"))

        self._refresh_memory_bar()
        return card

    @staticmethod
    def _snap_memory(value: int) -> int:
        """（转发到 core.memory.snap_memory，留个名字给老代码/测试用）"""
        return snap_memory(value)

    def _refresh_memory_bar(self):
        info = system_memory()
        heap = self.max_mem.value()
        overhead = jvm_overhead_mb(heap)
        self.memory_bar.set_values(info.total_mb, info.used_mb, heap, overhead)

        if info.ok:
            self.memory_legend.setText(tr(
                "物理内存 {total} GB · 系统已用 {used} GB · 游戏最多占用 {heap} GB"
                " · JVM 开销约 {overhead} GB",
                total=_gb(info.total_mb), used=_gb(info.used_mb),
                heap=_gb(heap), overhead=_gb(overhead)))
            self.recommend_btn.setEnabled(True)
            self.recommend_btn.setText(
                tr("用推荐值（{n} MB）", n=recommend_memory(info.total_mb)))
        else:
            # 读不到就老实说 —— 只按用户填的值画，不假装知道系统占用
            self.memory_legend.setText(tr("读不出系统内存，这条只按你填的值画。"))
            self.recommend_btn.setEnabled(False)
            self.recommend_btn.setText(tr("用推荐值"))

    def _apply_recommended_memory(self):
        info = system_memory()
        if not info.ok:
            return
        value = recommend_memory(info.total_mb)
        # 最小跟着走一半，别让 min 卡住 max（联动逻辑在 _on_*_changed 里）
        self.min_mem.setValue(min(value, max(MEMORY_MIN, value // 2)))
        self.max_mem.setValue(value)
        self.max_value.setText(f"{self.max_mem.value()} MB")
        self._refresh_memory_bar()

    def _on_min_memory_changed(self, value: int):
        if self._loading:
            return
        # 最小超过最大时，把最大一起顶上去，而不是弹窗骂用户
        if value > self.max_mem.value():
            self.max_mem.blockSignals(True)
            self.max_mem.setValue(value)
            self.max_mem.blockSignals(False)
            self.max_value.setText(f"{value} MB")
        self._refresh_memory_bar()
        self._save_timer.start()

    def _on_max_memory_changed(self, value: int):
        if self._loading:
            return

        # 滑块拖出来的值不是整数档，先吸附 —— 不吸附的话配置里会出现 3586 这种数，
        # 而滑块本身又画不出那么细的差别
        snapped = self._snap_memory(value)
        if snapped != value:
            self.max_mem.blockSignals(True)
            self.max_mem.setValue(snapped)
            self.max_mem.blockSignals(False)
            value = snapped

        if value < self.min_mem.value():
            self.min_mem.blockSignals(True)
            self.min_mem.setValue(value)
            self.min_mem.blockSignals(False)
        self.max_value.setText(f"{value} MB")
        self._refresh_memory_bar()
        self._save_timer.start()

    def refresh_theme(self):
        """主窗口换主题时会调（自绘控件不吃 QSS）"""
        self.memory_bar.refresh_theme()

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

    def _switch_row(self, box, label, value: bool, on_toggle):
        """一行开关：左边拨动开关，右边说明

        label 由调用方用 self.label(...) 建好传进来 —— 字面量必须直接出现在
        self.label() 里，提取工具才认得出（套一层变量它会当成没走文案系统）。
        """
        row = QHBoxLayout()
        row.setSpacing(10)

        sw = Switch()
        sw.setChecked(bool(value))
        sw.toggled.connect(on_toggle)
        row.addWidget(sw)
        row.addWidget(label)
        row.addStretch()
        box.addLayout(row)
        return sw

    # ---------- 下载 ----------

    def _make_download_card(self):
        """多线程下载：开关 + 线程数（移植自 experiments/Downloading mod test）

        ⚠️ 语义照搬实验那边：开关是"用不用我自己设的线程数"，
        **关着不是单线程**，而是用默认的 DEFAULT_THREADS 个。
        所以关掉的时候滑块置灰 + 底下写清楚现在实际用几个，
        免得用户以为关掉就变成 1 个线程了。
        """
        card, box = self._card("下载")

        self.thread_hint = QLabel()
        self.thread_hint.setObjectName("HintText")
        self.thread_hint.setWordWrap(True)

        self.multi_thread_switch = self._switch_row(
            box, self.label("使用自定义下载线程数", "FieldLabel"),
            config.get("multi_thread", False), self._on_multi_thread_changed)

        from core.config import DEFAULT_THREADS, THREAD_RANGE
        low, high = THREAD_RANGE
        slider_row = QHBoxLayout()
        slider_row.setSpacing(10)
        slider_row.addWidget(self.label("线程数", "FieldLabel"))

        self.thread_slider = QSlider(Qt.Orientation.Horizontal)
        self.thread_slider.setObjectName("MemorySlider")
        self.thread_slider.setRange(low, high)
        self.thread_slider.setSingleStep(1)
        self.thread_slider.setPageStep(4)
        self.thread_slider.setTickInterval(4)
        self.thread_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        # ⚠️ 先 setValue 再 connect：不然初始化那一下会被当成"用户拖了"，写一次配置
        self.thread_slider.setValue(int(config.get("download_threads",
                                                   DEFAULT_THREADS)))
        self.thread_slider.valueChanged.connect(self._on_threads_changed)
        slider_row.addWidget(self.thread_slider, 1)

        self.thread_value = QLabel()
        self.thread_value.setObjectName("FieldLabel")
        self.thread_value.setFixedWidth(70)
        self.thread_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignVCenter)
        slider_row.addWidget(self.thread_value)
        box.addLayout(slider_row)

        box.addWidget(self.thread_hint)
        self._refresh_thread_controls()
        return card

    def _refresh_thread_controls(self):
        """开关状态 + 滑块可用性 + 那句"实际用几个线程"的说明"""
        from core.config import DEFAULT_THREADS, effective_threads
        on = bool(config.get("multi_thread", False))
        self.thread_slider.setEnabled(on)
        self.thread_value.setText(str(self.thread_slider.value()))
        if on:
            self.thread_hint.setText(tr(
                "开得越多下载越快，但太吃网络和磁盘；8~16 一般就够了。"
                "当前用 {n} 个线程。", n=effective_threads()))
        else:
            self.thread_hint.setText(tr(
                "关着的时候用默认的 {d} 个线程（不是单线程）。"
                "当前用 {n} 个线程。", d=DEFAULT_THREADS, n=effective_threads()))

    def _on_multi_thread_changed(self, checked: bool):
        if self._loading:
            return
        config.set("multi_thread", bool(checked))
        self._refresh_thread_controls()

    def _on_threads_changed(self, value: int):
        if self._loading:
            return
        config.set("download_threads", int(value))
        self._refresh_thread_controls()

    def _make_misc_card(self):
        card, box = self._card("其他")

        self.close_switch = self._switch_row(
            box, self.label("启动游戏后关闭启动器", "FieldLabel"),
            config.get("close_on_launch", False), self._on_close_on_launch_changed)

        self.log_switch = self._switch_row(
            box, self.label("启动游戏后自动打开日志窗口", "FieldLabel"),
            config.get("show_log_window", True), self._on_show_log_changed)

        # 配置到底存哪 —— 出问题时要问"你的配置在哪"，写出来省一轮
        self.config_hint = QLabel()
        self.config_hint.setObjectName("HintText")
        self.config_hint.setWordWrap(True)
        box.addWidget(self.config_hint)
        self._refresh_config_hint()

        return card

    def _refresh_config_hint(self):
        path = get_config_dir()
        if is_portable():
            self.config_hint.setText(tr("配置位置：{path}（便携模式）", path=path))
        else:
            # 顺便把"怎么改成便携模式"写在旁边，不然这个功能没人发现得了
            self.config_hint.setText(tr(
                "配置位置：{path}（想改成便携模式，就在启动器目录放一个 {marker}）",
                path=path, marker=PORTABLE_MARKER))

    def _on_close_on_launch_changed(self, checked: bool):
        if self._loading:
            return
        config.set("close_on_launch", bool(checked))

    def _on_show_log_changed(self, checked: bool):
        if self._loading:
            return
        config.set("show_log_window", bool(checked))
