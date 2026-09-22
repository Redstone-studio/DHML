from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QCheckBox,
    QFileDialog, QMessageBox, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal

from core.config import config
from core.versions import default_minecraft_dir


class SettingsPage(QWidget):
    """设置页：MC 目录、Java、内存等"""

    # 通知主窗口：配置变了
    config_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        title = QLabel("设置")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        # ---------- MC 目录 ----------
        layout.addWidget(self._make_section_title("游戏目录"))
        layout.addWidget(self._make_mc_dir_row())

        # ---------- Java ----------
        layout.addWidget(self._make_section_title("Java"))
        layout.addWidget(self._make_java_row())

        # ---------- 内存 ----------
        layout.addWidget(self._make_section_title("内存"))
        layout.addWidget(self._make_memory_row())

        # ---------- 其他 ----------
        layout.addWidget(self._make_section_title("其他"))
        self.close_checkbox = QCheckBox("启动游戏后关闭启动器")
        self.close_checkbox.setChecked(bool(config.get("close_on_launch")))
        self.close_checkbox.stateChanged.connect(
            lambda s: config.set("close_on_launch", bool(s))
        )
        layout.addWidget(self.close_checkbox)

        layout.addStretch()

    # ---------- 各区块 ----------

    def _make_section_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionTitle")
        return lbl

    def _make_mc_dir_row(self) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)

        self.mc_dir_input = QLineEdit()
        self.mc_dir_input.setPlaceholderText(f"留空使用默认：{default_minecraft_dir()}")
        self.mc_dir_input.setText(config.get("minecraft_dir", ""))
        h.addWidget(self.mc_dir_input, 1)

        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse_mc_dir)
        h.addWidget(browse_btn)

        save_btn = QPushButton("应用")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self._save_mc_dir)
        h.addWidget(save_btn)

        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(self._reset_mc_dir)
        h.addWidget(reset_btn)

        return row

    def _make_java_row(self) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)

        self.java_input = QLineEdit()
        self.java_input.setPlaceholderText("留空自动查找 java（暂未实现）")
        self.java_input.setText(config.get("java_path", ""))
        self.java_input.setEnabled(False)   # 暂时禁用
        h.addWidget(self.java_input, 1)

        hint = QLabel("（暂未实现）")
        hint.setObjectName("PageHint")
        h.addWidget(hint)

        return row

    def _make_memory_row(self) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)

        h.addWidget(QLabel("最小:"))
        self.min_mem = QSpinBox()
        self.min_mem.setRange(256, 32768)
        self.min_mem.setSuffix(" MB")
        self.min_mem.setValue(int(config.get("min_memory", 512)))
        self.min_mem.valueChanged.connect(
            lambda v: config.set("min_memory", v)
        )
        h.addWidget(self.min_mem)

        h.addSpacing(20)
        h.addWidget(QLabel("最大:"))
        self.max_mem = QSpinBox()
        self.max_mem.setRange(512, 65536)
        self.max_mem.setSuffix(" MB")
        self.max_mem.setValue(int(config.get("max_memory", 2048)))
        self.max_mem.valueChanged.connect(
            lambda v: config.set("max_memory", v)
        )
        h.addWidget(self.max_mem)

        h.addStretch()
        return row

    # ---------- 事件 ----------

    def _browse_mc_dir(self):
        start = self.mc_dir_input.text().strip() or str(default_minecraft_dir())
        chosen = QFileDialog.getExistingDirectory(
            self, "选择 .minecraft 目录", start
        )
        if chosen:
            self.mc_dir_input.setText(chosen)

    def _save_mc_dir(self):
        path_str = self.mc_dir_input.text().strip()

        # 校验
        if path_str:
            from pathlib import Path
            p = Path(path_str)
            if not p.is_dir():
                QMessageBox.warning(self, "路径无效", f"目录不存在：\n{p}")
                return
            if not (p / "versions").is_dir():
                reply = QMessageBox.question(
                    self, "目录可能不对",
                    f"选中的目录里没有 versions 文件夹：\n{p}\n\n仍要使用吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        config.set("minecraft_dir", path_str)
        QMessageBox.information(self, "已保存", "MC 目录已更新，首页版本列表将刷新。")
        self.config_changed.emit()   # 通知主窗口

    def _reset_mc_dir(self):
        self.mc_dir_input.setText("")
        config.set("minecraft_dir", "")
        QMessageBox.information(self, "已重置", f"已恢复默认目录：\n{default_minecraft_dir()}")
        self.config_changed.emit()
