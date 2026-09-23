"""版本页：游戏目录 + 本地版本清单 + 选中版本的详情和操作

**页面职责（2026-09 调整）**：版本列表从「启动」页搬到了这里 ——
同一份列表在两边各摆一个，跟"主界面和日志窗口各显示一份日志"是同一个毛病。
现在启动页只有启动面板，选版本、换游戏目录、看缺哪些库都在这页。

游戏目录的选择也搬来了（原来在设置页）。放在这里更顺手：换目录就是为了看
别的目录下的版本，而设置页那张卡片跟"版本"这件事隔了两层。
搬过来以后设置页那张卡片就删掉了 —— 同一件事不留两个入口。

**故意不做"删除版本"**：这个用户的存档和 mod 都在版本目录里（版本隔离），
删版本等于连存档一起删。等有明确的需求和足够的确认流程再说。
"""

from pathlib import Path

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout
)

from core.config import config
from core.i18n import tr
from core.launch import load_version
from core.repair import missing_libraries
from core.resources import app_dir
from core.versions import default_minecraft_dir
from core.versions import type_label as version_type_label
from ui.dialogs.version_settings_dialog import VersionSettingsDialog
from ui.tasks import RepairTask
from ui.translatable import TranslatableWidget
from ui.widgets.version_list import VersionList

DETAIL_WIDTH = 320


def _set_tone(widget, object_name: str):
    """换 objectName 必须重新 polish，否则 QSS 不重算"""
    if widget.objectName() == object_name:
        return
    widget.setObjectName(object_name)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class VersionsPage(TranslatableWidget):
    # 游戏目录变了 —— 主窗口拿它去让别的页面重扫
    config_changed = pyqtSignal()
    # 选中的版本变了 —— 启动页的面板跟着走
    version_selected = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._version = None
        self._merged = None         # 选中版本的合并后 JSON（补库要用）
        self._missing = []
        self._repair_task = None
        self._loading = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(8)

        layout.addWidget(self.label("版本管理", "PageTitle"))
        layout.addWidget(self.label("选一个版本，或者换一个游戏目录", "PageSubtitle"))
        layout.addSpacing(12)

        layout.addWidget(self._make_dir_card())

        body = QHBoxLayout()
        body.setSpacing(16)

        list_card = QFrame()
        list_card.setObjectName("Card")
        list_box = QVBoxLayout(list_card)
        list_box.setContentsMargins(18, 16, 18, 16)
        list_box.setSpacing(10)

        self.version_list = VersionList()
        self.version_list.selection_changed.connect(self._on_selected)
        list_box.addWidget(self.version_list)
        body.addWidget(list_card, 1)

        body.addWidget(self._make_detail_card())
        layout.addLayout(body, 1)

        self._refresh_dir_hint()
        self.version_list.select_id(config.get("last_version", ""))
        self._loading = False

    # ---------- 游戏目录 ----------

    def _make_dir_card(self):
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 16, 20, 16)
        box.setSpacing(10)

        box.addWidget(self.label("游戏目录", "SectionTitle"))

        row = QHBoxLayout()
        row.setSpacing(10)

        self.dir_combo = QComboBox()
        self.dir_combo.setMinimumWidth(420)
        self.dir_combo.currentIndexChanged.connect(self._on_dir_selected)
        row.addWidget(self.dir_combo, 1)

        self.browse_btn = self.button("浏览…")
        self.browse_btn.clicked.connect(self._browse_dir)
        row.addWidget(self.browse_btn)

        # 目录不存在时才有用 —— 灰色的时候一眼知道"现在不用点"
        self.create_btn = self.button("创建")
        self.create_btn.clicked.connect(self._create_dir)
        row.addWidget(self.create_btn)

        row.addStretch()
        box.addLayout(row)

        self.dir_hint = QLabel()
        self.dir_hint.setObjectName("HintText")
        self.dir_hint.setWordWrap(True)
        box.addWidget(self.dir_hint)

        self._fill_dir_combo()
        return card

    @staticmethod
    def _presets():
        # 文案直接写在 tr() 里，提取工具才认得出
        return (
            (tr("官方启动器目录"), default_minecraft_dir()),
            (tr("启动器目录旁边"), app_dir() / ".minecraft"),
        )

    def _fill_dir_combo(self):
        current = str(config.get_minecraft_dir())
        self.dir_combo.blockSignals(True)
        self.dir_combo.clear()
        for label, path in self._presets():
            self.dir_combo.addItem(f"{label} · {path}", str(path))
        # 现在用的不是预设里的（用户自己挑过）→ 补一项，否则一打开就被悄悄改掉
        if self.dir_combo.findData(current) < 0:
            self.dir_combo.addItem(tr("自定义：{path}", path=current), current)
        self.dir_combo.setCurrentIndex(max(0, self.dir_combo.findData(current)))
        self.dir_combo.blockSignals(False)

    def _refresh_dir_hint(self):
        path = config.get_minecraft_dir()
        try:
            exists = path.is_dir()
        except OSError:
            exists = False
        self.create_btn.setEnabled(not exists)
        if exists:
            self.dir_hint.setText(tr("当前游戏目录：{path}", path=path))
        else:
            self.dir_hint.setText(
                tr("⚠ {path} 不存在，点「创建」新建一个 .minecraft", path=path))

    def _on_dir_selected(self, _index: int):
        if self._loading:
            return
        path = self.dir_combo.currentData()
        if path:
            self._apply_dir(path)

    def _apply_dir(self, path_text: str):
        config.set("minecraft_dir", path_text)
        self._refresh_dir_hint()
        self.version_list.reset_scanner()
        self.config_changed.emit()

    def _browse_dir(self):
        """挑一个 .minecraft 目录

        ⚠️ 起始目录**必须是个真实存在的目录**：原来直接拿当前游戏目录当起点，
        而"官方启动器目录"（%APPDATA%\\.minecraft）在没装过官方启动器的机器上
        根本不存在 —— 那种情况下 Windows 的文件对话框会当场退出来，
        表现就是"点了浏览…没反应"（用户报过这个）。
        """
        current = config.get_minecraft_dir()
        try:
            start = current if current.is_dir() else Path.home()
        except OSError:
            start = Path.home()
        chosen = QFileDialog.getExistingDirectory(
            self.window(), tr("选择 .minecraft 目录"), str(start))
        if not chosen:
            return
        # 选错目录（比如指到了桌面上）会让"扫不到版本"变成一个谜，
        # 所以这里先问一句 —— 但只问浏览来的，预设是可信的
        if not (Path(chosen) / "versions").is_dir():
            reply = QMessageBox.question(
                self, tr("目录可能不对"),
                tr("选中的目录里没有 versions 文件夹：\n{path}\n\n仍要使用吗？", path=chosen),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._apply_dir(chosen)
        self._fill_dir_combo()

    def _create_dir(self):
        path = config.get_minecraft_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
            (path / "versions").mkdir(exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, tr("创建失败"), str(e))
            return
        self._refresh_dir_hint()
        self.version_list.reset_scanner()
        self.config_changed.emit()

    # ---------- 右侧详情 ----------

    def _make_detail_card(self):
        card = QFrame()
        card.setObjectName("Card")
        card.setFixedWidth(DETAIL_WIDTH)
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 20, 20, 18)
        box.setSpacing(10)

        box.addWidget(self.label("版本详情", "SectionTitle"))

        self.detail_name = QLabel()
        self.detail_name.setObjectName("VersionRowName")
        self.detail_name.setWordWrap(True)
        box.addWidget(self.detail_name)

        self.detail_meta = QLabel()
        self.detail_meta.setObjectName("VersionRowMeta")
        self.detail_meta.setWordWrap(True)
        box.addWidget(self.detail_meta)

        self.detail_path = QLabel()
        self.detail_path.setObjectName("HintText")
        self.detail_path.setWordWrap(True)
        box.addWidget(self.detail_path)

        self.detail_libs = QLabel()
        self.detail_libs.setObjectName("HintText")
        self.detail_libs.setWordWrap(True)
        box.addWidget(self.detail_libs)

        self.progress = QLabel()
        self.progress.setObjectName("HintText")
        self.progress.setWordWrap(True)
        box.addWidget(self.progress)

        box.addStretch()

        self.settings_btn = self.button("版本设置")
        self.settings_btn.clicked.connect(self._open_settings)
        box.addWidget(self.settings_btn)

        self.folder_btn = self.button("打开文件夹")
        self.folder_btn.clicked.connect(self._open_folder)
        box.addWidget(self.folder_btn)

        self.repair_btn = self.button("补全缺失的库")
        self.repair_btn.clicked.connect(self._start_repair)
        box.addWidget(self.repair_btn)

        return card

    def _on_selected(self, version):
        self._version = version
        self._merged = None
        self._missing = []
        self.progress.setText("")

        if version:
            # 记住它：启动页下次打开直接用它
            config.set("last_version", version["id"])

        # 启动页的面板跟着走（两个页面共用一个"当前版本"）
        self.version_selected.emit(version)

        if not version:
            self.detail_name.setText(tr("未找到本地版本"))
            self.detail_meta.setText("")
            self.detail_path.setText("")
            self.detail_libs.setText("")
            self.folder_btn.setEnabled(False)
            self.settings_btn.setEnabled(False)
            self.repair_btn.setEnabled(False)
            return

        self.folder_btn.setEnabled(True)
        self.settings_btn.setEnabled(True)
        self.detail_name.setText(version["display_name"])
        self.detail_meta.setText(self._meta_text(version))
        self.detail_path.setText(str(version["path"]))
        self._refresh_libraries()

    def _meta_text(self, version) -> str:
        parts = [version_type_label(version["type"])]
        if version["loader_label"]:
            parts.append(version["loader_label"])
        if version["java_major"]:
            parts.append(f"Java {version['java_major']}")
        if version["isolated"]:
            parts.append(tr("版本隔离"))
        return "    ·    ".join(parts)

    def _refresh_libraries(self):
        version = self._version
        if not version["complete"]:
            self.detail_libs.setText(tr("缺少客户端 jar，这个版本启动不了"))
            _set_tone(self.detail_libs, "BadgeWarn")
            self.repair_btn.setEnabled(False)
            return

        mc_dir = Path(version["path"]).parent.parent
        try:
            self._merged = load_version(mc_dir, version["id"])
            self._missing = missing_libraries(mc_dir, self._merged)
        except Exception as e:
            self._merged = None
            self._missing = []
            self.detail_libs.setText(tr("读不了版本 JSON：{err}", err=e))
            _set_tone(self.detail_libs, "BadgeWarn")
            self.repair_btn.setEnabled(False)
            return

        if self._missing:
            self.detail_libs.setText(tr("缺 {n} 个库", n=len(self._missing)))
            _set_tone(self.detail_libs, "BadgeWarn")
        else:
            self.detail_libs.setText(tr("库都齐了"))
            _set_tone(self.detail_libs, "HintText")
        self.repair_btn.setEnabled(bool(self._missing))

    # ---------- 操作 ----------

    def _open_folder(self):
        if not self._version:
            return
        path = Path(self._version["path"])
        target = path if path.is_dir() else path.parent
        if target.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _open_settings(self):
        """改这个版本自己的启动设置（Java / 内存 / 额外 JVM 参数）"""
        if not self._version:
            return
        # 这个页面没有 Java 扫描结果，对话框会自己扫一遍
        dialog = VersionSettingsDialog(self._version, parent=self)
        dialog.exec()
        if dialog.saved:
            self._on_selected(self._version)

    def _start_repair(self):
        if not self._version or not self._merged or not self._missing:
            return
        self.repair_btn.setEnabled(False)
        self.version_list.setEnabled(False)
        self.progress.setText(tr("开始补全 {n} 个库…", n=len(self._missing)))

        mc_dir = Path(self._version["path"]).parent.parent
        task = RepairTask(mc_dir, self._merged, self)
        task.log.connect(self.progress.setText)
        task.done.connect(self._on_repair_done)
        self._repair_task = task
        task.start()

    def _on_repair_done(self, fixed: int, failed: list):
        self._repair_task = None
        self.version_list.setEnabled(True)
        if failed:
            self.progress.setText(tr("有 {n} 个库没补上", n=len(failed)))
        elif fixed:
            self.progress.setText(tr("补全完成，修好了 {n} 个库", n=fixed))
        else:
            self.progress.setText(tr("没有补上任何库，检查一下网络？"))
        self._refresh_libraries()

    # ---------- 外部接口（main_window 调） ----------

    def reload_versions(self):
        self.version_list.reload()

    def reset_scanner(self):
        """游戏目录换了以后重建 scanner 并重扫"""
        self.version_list.reset_scanner()
        self._refresh_dir_hint()

    def selected_version(self):
        return self._version

    # ---------- 语言切换 ----------

    def retranslate(self):
        super().retranslate()
        # 列表里的分组标题、行、脚注都是生成的，重建一遍（不用重扫）
        self.version_list.retranslate()
        self._fill_dir_combo()
        self._refresh_dir_hint()
