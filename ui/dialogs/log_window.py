"""独立的游戏日志窗口

为什么用顶层 QWidget（Qt.WindowType.Window）而不是 QDialog：
对话框会一直挂在主窗口前面，而这个窗口要能自由摆放、独立最小化 ——
玩家常常一边看日志一边翻别的窗口。

窗口里那排「0 错误 / 1 警告 / …」是照 HMCL 的做法：日志几千行，直接翻很痛苦，
先把各级别的条数摆在眼前，一眼就知道有没有出事。
**点一下**计数就把那一级藏起来（数字压暗），再点一下放出来 ——
调试时最烦的就是被 INFO 刷屏，只想看 WARN 和 ERROR。

日志**按级别上色**（错误红、警告黄、调试灰），这样出错那几行自己会跳出来。
颜色不走 QSS —— QSS 只能给整个 QPlainTextEdit 一个颜色，管不到单独几行，
所以是插文本时一段一段带上 QTextCharFormat。因此主题换了要主动 refresh_theme()。

日志原文存在 self._buffer 里（级别 + 原文），view 是它按当前过滤条件渲染出来的结果。
不直接往 view 里塞、过滤时去删行，是因为 QPlainTextEdit 删中间的块很慢，
而且堆栈续行得跟着它上一条一起藏（见 _is_continuation）。

⚠️ 文案写法：这里**不用** bind/dict 那套，直接在 retranslate() 里写
self.xxx.setText(tr("中文"))。因为提取工具只认"字面量直接出现在 tr() 里"，
套一层变量它就当成没走文案系统 —— 而且英文字面量（比如 "errors"）它根本不抓，
那样这些词就永远翻译不了。所以量词也用中文当 key。
"""

from collections import Counter

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget
)

from core.i18n import tr
from ui.theme_state import palette

# (日志里的级别, QSS 里的 objectName)。显示用的词在 retranslate() 里查表，
# 不放在这儿 —— 常量里的中文字面量提取工具不认，翻译不进 JSON。
LEVELS = (
    ("FATAL", "LogCountFatal"),
    ("ERROR", "LogCountError"),
    ("WARN", "LogCountWarn"),
    ("INFO", "LogCountInfo"),
    ("DEBUG", "LogCountDebug"),
)

# 显示行数的档位（0 = 不限）
LINE_LIMITS = (2000, 5000, 20000, 0)

# 正文颜色：级别 → (调色板里的 token, 要不要加粗)。
# 空字符串 = 没有级别的行（启动器自己的消息等），用正文色。
_LEVEL_COLOR = {
    "FATAL": ("danger", True),
    "ERROR": ("danger", False),
    "WARN": ("warn", False),
    "INFO": ("text_dim", False),
    "DEBUG": ("text_disabled", False),
    "": ("text", False),
}

# 续行（堆栈）的开头。Java 的堆栈是 "\tat com.foo…"，Python 的是缩进的
# "  File …"，log4j 还常有 "Caused by:" / "Suppressed:" / "..."。
# 这些行自己没有级别标记，过滤和上色时都得跟着上一条走。
_CONTINUATION_PREFIXES = ("at ", "Caused by:", "Suppressed:", "...")


def _detect_level(line: str) -> str:
    """这条日志是哪一级的

    按 MC / log4j 常见的几种写法匹配，而不是简单找子串 ——
    不然 "no errors found" 也会被算成 ERROR。
    """
    for level, _obj in LEVELS:
        if (f"/{level}]" in line or f'level="{level}"' in line
                or f"{level}:" in line or f"[{level}]" in line):
            return level
    return ""


def _is_continuation(line: str) -> bool:
    """这行是上一条日志的续行（堆栈），不是新的一条

    启动器自己加的行（"[启动器] …"）不缩进、也不以堆栈前缀开头，
    所以不会被算成续行 —— 藏了 INFO 也照样能看到「游戏已退出」这类消息。
    """
    if not line:
        return False
    if line[0] in " \t":
        # 只有空白字符的行不算续行，免得空行把上一条的级别继承下去
        return bool(line.strip())
    return line.startswith(_CONTINUATION_PREFIXES)


class LogWindow(QWidget):
    """游戏日志窗口。内容由外面 push 进来（append / clear）"""

    stop_game = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("LogWindow")
        self.resize(940, 580)

        self._counts = Counter()
        self._words = {}
        # (归属级别, 原文)。归属级别为空 = 不属于任何一级，永远显示
        self._buffer: "list[tuple[str, str]]" = []
        self._last_level = ""
        self._formats = {}          # 级别 -> QTextCharFormat（主题变了要清）

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ---------- 工具栏 ----------
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.limit_label = QLabel()
        bar.addWidget(self.limit_label)

        self.limit_combo = QComboBox()
        for n in LINE_LIMITS:
            self.limit_combo.addItem(tr("全部") if n == 0 else str(n), n)
        self.limit_combo.setCurrentIndex(0)
        self.limit_combo.currentIndexChanged.connect(self._on_limit_changed)
        bar.addWidget(self.limit_combo)

        bar.addSpacing(12)

        # 级别计数：既是计数也是过滤开关（点一下藏起来）
        self.level_buttons = {}
        for level, obj in LEVELS:
            btn = QPushButton()
            btn.setObjectName(obj)
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.level_buttons[level] = btn
            bar.addWidget(btn)

        bar.addStretch()

        self.autoscroll = QCheckBox()
        self.autoscroll.setChecked(True)
        bar.addWidget(self.autoscroll)

        self.export_btn = QPushButton()
        self.export_btn.clicked.connect(self.export)
        bar.addWidget(self.export_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setObjectName("DangerButton")
        self.stop_btn.clicked.connect(self.stop_game.emit)
        bar.addWidget(self.stop_btn)

        self.clear_btn = QPushButton()
        self.clear_btn.clicked.connect(self.clear)
        bar.addWidget(self.clear_btn)

        layout.addLayout(bar)

        # ---------- 日志本体 ----------
        self.view = QPlainTextEdit()
        self.view.setObjectName("GameLog")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(LINE_LIMITS[0])
        layout.addWidget(self.view)

        # 放在 view 建好之后再接，免得构造期就触发一次 _rebuild
        for btn in self.level_buttons.values():
            btn.toggled.connect(self._on_filter_changed)

        self.retranslate()

    # ---------- 文案 ----------

    def retranslate(self):
        self.setWindowTitle(tr("游戏日志"))
        self.limit_label.setText(tr("显示行数"))
        self.autoscroll.setText(tr("自动滚动"))
        self.export_btn.setText(tr("导出"))
        self.stop_btn.setText(tr("结束游戏进程"))
        self.clear_btn.setText(tr("清空"))

        # 各级别显示用的词。字面量写死在 tr() 里，提取工具才认得出
        self._words = {
            "FATAL": tr("致命"),
            "ERROR": tr("错误"),
            "WARN": tr("警告"),
            "INFO": tr("信息"),
            "DEBUG": tr("调试"),
        }
        tip = tr("点一下隐藏这一级，再点一下显示")
        for btn in self.level_buttons.values():
            btn.setToolTip(tip)

        # 行数下拉框里除了"全部"都是数字，只要换掉那一项
        index = self.limit_combo.findData(0)
        if index >= 0:
            self.limit_combo.setItemText(index, tr("全部"))
        self._refresh_counts()

    def _refresh_counts(self):
        for level, _obj in LEVELS:
            self.level_buttons[level].setText(
                f"{self._counts[level]} {self._words.get(level, level)}")

    # ---------- 上色 ----------

    def _format_for(self, owner: str) -> QTextCharFormat:
        """某个级别用的文字格式（带缓存的，一行一行建格式太浪费）"""
        fmt = self._formats.get(owner)
        if fmt is not None:
            return fmt

        colors = palette()
        token, bold = _LEVEL_COLOR.get(owner, _LEVEL_COLOR[""])
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(colors.get(token, colors.get("text", "#d8d8dc"))))
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        self._formats[owner] = fmt
        return fmt

    def refresh_theme(self):
        """主题 / 强调色变了要调一下

        QSS 管不到单独几行的颜色（那是 QTextCharFormat 写死的），
        所以主窗口换主题时得把缓存的格式扔掉、重新渲染一遍。
        """
        self._formats.clear()
        self._rebuild()

    # ---------- 过滤 ----------

    def _is_visible(self, owner: str) -> bool:
        """归属级别为空的行没有开关可点，永远显示"""
        return not owner or self.level_buttons[owner].isChecked()

    def _on_filter_changed(self, _checked: bool):
        self._rebuild()

    def _rebuild(self):
        """按当前过滤条件 + 当前主题重新渲染一遍

        同一级别的连续行合并成一次 insertText —— 日志通常是一串一串来的，
        这样几千行也就几毫秒。点一下过滤开关就整篇重建，省得维护增量状态。
        """
        bar = self.view.verticalScrollBar()
        keep = bar.value()
        self.view.setUpdatesEnabled(False)
        try:
            self.view.clear()
            cursor = self.view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            first = True
            run_owner = None
            run_texts = []

            def flush():
                nonlocal first
                if not run_texts:
                    return
                if not first:
                    cursor.insertText("\n")
                cursor.insertText("\n".join(run_texts), self._format_for(run_owner))
                run_texts.clear()
                first = False

            for owner, text in self._buffer:
                if not self._is_visible(owner):
                    continue
                if owner != run_owner:
                    flush()
                    run_owner = owner
                run_texts.append(text)
            flush()
        finally:
            self.view.setUpdatesEnabled(True)

        if self.autoscroll.isChecked():
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(keep, bar.maximum()))

    # ---------- 内容 ----------

    def _trim(self):
        """_buffer 和 view 用同一个行数上限，超了从最老的开始丢"""
        limit = self.limit_combo.currentData() or 0
        if limit and len(self._buffer) > limit:
            del self._buffer[:-limit]

    def _push(self, text: str) -> str:
        """记一条日志，返回它的归属级别（过滤和上色都用它）"""
        level = _detect_level(text)
        if level:
            self._counts[level] += 1
            self._last_level = level
            self.level_buttons[level].setText(
                f"{self._counts[level]} {self._words.get(level, level)}")
            owner = level
        elif _is_continuation(text):
            owner = self._last_level          # 堆栈续行跟着上一条一起显示/隐藏
        else:
            owner = ""                        # 不属于任何一级，永远显示
            self._last_level = ""

        self._buffer.append((owner, text))
        self._trim()
        return owner

    def append(self, text: str):
        owner = self._push(text)
        if not self._is_visible(owner):
            return

        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.view.document().isEmpty():
            cursor.insertText("\n")
        cursor.insertText(text, self._format_for(owner))

        if self.autoscroll.isChecked():
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def clear(self):
        self._buffer.clear()
        self._last_level = ""
        self._counts.clear()
        self.view.clear()
        self._refresh_counts()

    def _on_limit_changed(self, _index: int):
        limit = self.limit_combo.currentData() or 0
        self.view.setMaximumBlockCount(limit)     # 0 = 不限
        self._trim()
        self._rebuild()

    def export(self):
        """导出的是**全部**日志，不管当前藏了哪几级 ——
        发问题报告时漏掉被藏起来的 DEBUG，往往就查不出来了"""
        path, _ = QFileDialog.getSaveFileName(
            self, tr("导出日志"), "MCLuncher-log.txt", "Text (*.txt);;All (*)"
        )
        if not path:
            return
        body = "\n".join(text for _owner, text in self._buffer)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(body + "\n" if body else "")
        except OSError as e:
            QMessageBox.warning(self, tr("导出失败"), str(e))
