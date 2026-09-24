"""单个版本的启动设置

每一项都是**三态**：勾上 = 这个版本自己定（写进 versions.json），
不勾 = 跟随「设置」页里的启动器默认（那一项在文件里直接不存在）。

为什么不"把默认值抄一份进这个版本"：那样以后改了启动器默认，
所有动过一次的版本都不会跟着变，用户会莫名其妙。
（PCL2 也是这么做的：没动过的项跟着全局走。）

对话框是模态的、每次新建，所以不需要 retranslate()（用户不可能在它开着的时候
切语言）—— 和新档案对话框、关于对话框一个思路。
"""

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QVBoxLayout
)

from core.config import config
from core.i18n import tr
from core.java import scan_minecraft_dirs
from core.memory import jvm_overhead_mb, system_memory
from core.version_settings import version_settings
from ui.tasks import QuickJavaScanTask
from ui.widgets.memory_bar import MemoryBar

# 内存范围跟「设置」页保持一致（那边也是这两个数）
MIN_MEMORY_RANGE = (512, 32768)
MAX_MEMORY_RANGE = (512, 65536)
MEMORY_STEP = 512


def _gb(megabytes: int) -> str:
    """MB → "12.4"（配合文案里的 GB 用）"""
    return f"{max(0, int(megabytes)) / 1024:.1f}"


class VersionSettingsDialog(QDialog):
    def __init__(self, version: dict, javas=None, parent=None):
        super().__init__(parent)
        self.version = version
        self.version_id = version.get("id", "")
        self.saved = False          # 点没点保存（调用方据此决定要不要刷新）
        self._javas = list(javas or [])
        self._scan = None
        self._loading = True

        saved_overrides = version_settings.get(self.version_id)

        self.setObjectName("VersionSettingsDialog")
        self.setWindowTitle(tr("版本设置"))
        self.resize(660, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 16)
        layout.setSpacing(12)

        self.name_label = QLabel(version.get("display_name", ""))
        self.name_label.setObjectName("VersionRowName")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label)

        self.path_label = QLabel(str(version.get("path", "")))
        self.path_label.setObjectName("HintText")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        layout.addWidget(self.hint_label(tr(
            "这里只影响这一个版本。没勾的项跟着「设置」页里的默认值走。")))

        layout.addSpacing(4)

        # ---------- Java ----------
        self.java_row, self.java_check = self._row(tr("使用指定的 Java"))
        self.java_combo = QComboBox()
        self.java_combo.setMinimumWidth(360)
        self.java_row.addWidget(self.java_combo, 1)
        self.java_rescan_btn = QPushButton(tr("重新扫描"))
        self.java_rescan_btn.clicked.connect(self._rescan_java)
        self.java_row.addWidget(self.java_rescan_btn)
        layout.addLayout(self.java_row)

        # ---------- 内存 ----------
        self.mem_row, self.mem_check = self._row(tr("单独设置内存"))
        self.min_mem = self._mem_spin(MIN_MEMORY_RANGE)
        self.max_mem = self._mem_spin(MAX_MEMORY_RANGE)
        self.mem_row.addWidget(QLabel(tr("最小")))
        self.mem_row.addWidget(self.min_mem)
        self.mem_row.addWidget(QLabel(tr("最大")))
        self.mem_row.addWidget(self.max_mem)
        self.mem_row.addStretch()
        layout.addLayout(self.mem_row)

        self.mem_hint = self.hint_label(tr("最小堆不能大于最大堆 —— 两边会自动联动。"))
        layout.addWidget(self.mem_hint)

        # 系统内存 + 这个版本会占多少（跟设置页那条是同一个控件）
        self.memory_bar = MemoryBar()
        layout.addWidget(self.memory_bar)

        self.memory_legend = QLabel()
        self.memory_legend.setObjectName("HintText")
        self.memory_legend.setWordWrap(True)
        layout.addWidget(self.memory_legend)

        # ---------- 额外 JVM 参数 ----------
        self.jvm_row, self.jvm_check = self._row(tr("追加 JVM 参数"))
        self.jvm_edit = QLineEdit()
        self.jvm_edit.setPlaceholderText("-XX:+UseG1GC")
        self.jvm_row.addWidget(self.jvm_edit, 1)
        layout.addLayout(self.jvm_row)

        self.jvm_hint = self.hint_label(tr(
            "只有确实知道在填什么再加。填错了 JVM 会拒绝启动，游戏就起不来了。"))
        layout.addWidget(self.jvm_hint)

        layout.addStretch()

        # ---------- 底部按钮 ----------
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_btn = QPushButton(tr("取消"))
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        self.save_btn = QPushButton(tr("保存"))
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.clicked.connect(self._on_save)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)

        # ---------- 填值 ----------
        self._fill_java_combo()
        self.java_combo.setCurrentIndex(
            max(0, self.java_combo.findData(saved_overrides.get("java_path", ""))))

        self.min_mem.setValue(int(saved_overrides.get(
            "min_memory", config.get("min_memory", 512))))
        self.max_mem.setValue(int(saved_overrides.get(
            "max_memory", config.get("max_memory", 2048))))
        self.jvm_edit.setText(saved_overrides.get("extra_jvm_args", ""))

        self.java_check.setChecked("java_path" in saved_overrides)
        self.mem_check.setChecked(
            "min_memory" in saved_overrides or "max_memory" in saved_overrides)
        self.jvm_check.setChecked("extra_jvm_args" in saved_overrides)

        for check, widgets in self._groups():
            check.toggled.connect(self._sync_enabled)
            self._sync_enabled()
        self.min_mem.valueChanged.connect(self._on_min_changed)
        self.max_mem.valueChanged.connect(self._on_max_changed)

        self._loading = False
        self._refresh_memory_bar()
        if not self._javas:
            self._rescan_java()

    # ---------- 搭控件的小工具 ----------
    #
    # ⚠️ 这里的 text 参数**要求调用方自己过 tr()** —— 字面量必须直接写在 tr() 里，
    # 提取工具才认（套一层 helper 变量它就会报"没走文案系统"）。

    @staticmethod
    def hint_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("HintText")
        label.setWordWrap(True)
        return label

    def _row(self, text: str):
        row = QHBoxLayout()
        row.setSpacing(10)
        check = QCheckBox(text)
        # 三个勾选框要左对齐成一条竖线，所以给个统一的宽度；
        # 太窄的话"追加 JVM 参数"这种长一点的标签会被切掉
        check.setMinimumWidth(165)
        row.addWidget(check)
        return row, check

    @staticmethod
    def _mem_spin(value_range):
        spin = QSpinBox()
        spin.setRange(*value_range)
        spin.setSingleStep(MEMORY_STEP)
        spin.setSuffix(" MB")
        return spin

    def _groups(self):
        """(勾选框, 勾上以后才可用的那些控件)"""
        return (
            (self.java_check, (self.java_combo, self.java_rescan_btn)),
            (self.mem_check, (self.min_mem, self.max_mem)),
            (self.jvm_check, (self.jvm_edit,)),
        )

    def _sync_enabled(self, *_args):
        """没勾的项灰掉 —— 顺便让用户看见"现在用的是默认值"（数字就在那儿）"""
        for check, widgets in self._groups():
            on = check.isChecked()
            for widget in widgets:
                widget.setEnabled(on)

    # ---------- Java ----------

    def _fill_java_combo(self):
        saved = version_settings.get(self.version_id).get("java_path", "")
        self.java_combo.blockSignals(True)
        self.java_combo.clear()
        self.java_combo.addItem(tr("自动选择（推荐）"), "")
        for info in self._javas:
            self.java_combo.addItem(f"Java {info.major} · {info.path}", info.path)
        # 存的那个 Java 现在扫不到了（卸载了/挪走了）：也得让它出现在下拉里，
        # 不然一打开对话框就会被悄悄改成"自动选择"
        if saved and self.java_combo.findData(saved) < 0:
            self.java_combo.addItem(tr("手动指定：{path}", path=saved), saved)
        self.java_combo.setCurrentIndex(max(0, self.java_combo.findData(saved)))
        self.java_combo.blockSignals(False)

    def _rescan_java(self):
        if self._scan is not None and self._scan.isRunning():
            return
        self.java_rescan_btn.setEnabled(False)
        self.java_rescan_btn.setText(tr("扫描中…"))
        self._scan = QuickJavaScanTask(scan_minecraft_dirs(), self)
        self._scan.done.connect(self._on_java_scan_done)
        self._scan.start()

    def _on_java_scan_done(self, javas):
        self._javas = javas
        self.java_rescan_btn.setEnabled(True)
        self.java_rescan_btn.setText(tr("重新扫描"))
        self._fill_java_combo()
        self._sync_enabled()

    # ---------- 内存联动 ----------

    def _refresh_memory_bar(self):
        """画的是**最终会生效**的值（勾上就是自定义的，没勾就是默认的）"""
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
        else:
            self.memory_legend.setText(tr("读不出系统内存，这条只按你填的值画。"))

    def _on_min_changed(self, value: int):
        if self._loading:
            return
        if value > self.max_mem.value():
            self.max_mem.blockSignals(True)
            self.max_mem.setValue(value)
            self.max_mem.blockSignals(False)
        self._refresh_memory_bar()

    def _on_max_changed(self, value: int):
        if self._loading:
            return
        if value < self.min_mem.value():
            self.min_mem.blockSignals(True)
            self.min_mem.setValue(value)
            self.min_mem.blockSignals(False)
        self._refresh_memory_bar()

    # ---------- 保存 ----------

    def _on_save(self):
        overrides = {}
        if self.java_check.isChecked():
            overrides["java_path"] = self.java_combo.currentData() or ""
        if self.mem_check.isChecked():
            overrides["min_memory"] = self.min_mem.value()
            overrides["max_memory"] = self.max_mem.value()
        if self.jvm_check.isChecked():
            overrides["extra_jvm_args"] = self.jvm_edit.text().strip()

        # 空值会被 version_settings 丢掉（等于"跟随默认"），所以全空时
        # 这个版本在文件里就消失了 —— 正是我们要的语义
        version_settings.update(self.version_id, overrides)
        self.saved = True
        self.accept()
