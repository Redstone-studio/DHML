"""
运行日志页

## 为什么要单独一页

这个项目里所有难查的问题都是同一类：**点下去没反应、报了个看不懂的错**。
根因通常在 HTTP 层 —— 打到哪个端点、请求体是什么、服务端**原样**回了什么。

控制台其实都打了，但要一边开终端一边用界面、还得往上翻，
实际用起来没人会这么做。所以收进内存、单独一页实时显示。

## ⚠️ 两条

**令牌不会出现在这里。** 所有进日志的东西都先过
`core/logbook.redact()`（按 key 递归 + URL 查询串），
`access_token` / `refresh_token` / `password` / `device_code` /
`Authorization` 头一律变成 `***`。界面上也写了这句，免得用户不敢复制给别人看。

**一定要退订。** 页面销毁后回调还在的话，日志一来就会往一个已经没了的控件上写
—— 轻则白干活，重则崩。
"""
from html import escape

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
    QWidget
)

from core import logbook, theme

#: 级别 → (过滤按钮的 objectName, 正文颜色取调色板里的哪个键)
LEVEL_STYLE = {
    "info": ("LogCountInfo", "text_dim"),
    "ok": ("LogCountInfo", "accent"),
    "warn": ("LogCountWarn", "warn"),
    "error": ("LogCountError", "danger"),
}

LEVEL_LABEL = {"info": "信息", "ok": "成功", "warn": "警告", "error": "错误"}


class LogPage(QWidget):
    #: ⚠️ **必须走信号，不能直接在回调里写控件。**
    #: `logbook` 是从**后台线程**（验证用的 QThread、头像线程池）回调的，
    #: 而 Qt 控件只能在主线程碰。这个信号是跨线程的，Qt 会自动排队到主线程执行
    #: （因为 LogPage 活在主线程里）。直接在回调里 appendHtml 的话，
    #: 表现是时好时坏、偶尔崩 —— 最难查的那种。
    entry_added = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._unsubscribe = None
        self._palette = theme.palette("dark")
        self._auto_scroll = True
        self.entry_added.connect(self._on_entry)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(10)

        title = QLabel("运行日志")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        sub = QLabel("每个请求和响应原样记下来 —— 排查「为什么没成」的时候看这里。\n"
                     "⚠️ 令牌、密码、设备码都已经替换成 ***，可以直接复制给别人看。")
        sub.setObjectName("PageSubtitle")
        sub.setWordWrap(True)
        root.addWidget(sub)

        root.addSpacing(4)

        # ---------- 工具栏 ----------
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self.level_buttons = {}
        for level in ("info", "ok", "warn", "error"):
            btn = QPushButton(f"{LEVEL_LABEL[level]} 0")
            btn.setObjectName(LEVEL_STYLE[level][0])
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(self._rebuild)
            bar.addWidget(btn)
            self.level_buttons[level] = btn

        bar.addStretch()

        self.autoscroll_check = QCheckBox("自动滚到底")
        self.autoscroll_check.setChecked(True)
        self.autoscroll_check.toggled.connect(self._on_autoscroll)
        bar.addWidget(self.autoscroll_check)

        copy_btn = QPushButton("复制全部")
        copy_btn.clicked.connect(self._copy_all)
        bar.addWidget(copy_btn)

        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("DangerButton")
        clear_btn.clicked.connect(self._clear)
        bar.addWidget(clear_btn)

        root.addLayout(bar)

        # ---------- 日志正文 ----------
        self.view = QPlainTextEdit()
        self.view.setObjectName("GameLog")          # 样式来自主项目那份
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setMaximumBlockCount(logbook.MAX_ENTRIES * 12)
        root.addWidget(self.view, 1)

        self.empty_hint = QLabel("还没有日志。去「正版验证」页跑一次就有了。")
        self.empty_hint.setObjectName("EmptyState")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty_hint)

        self._rebuild()

    # ---------- 显示 / 隐藏 ----------

    def showEvent(self, event):
        """页面显示出来才订阅 —— 不在前台时没必要刷界面"""
        super().showEvent(event)
        self._subscribe()

    def hideEvent(self, event):
        self._unsubscribe_now()
        super().hideEvent(event)

    def _subscribe(self):
        if self._unsubscribe is None:
            # 订阅的是**信号的 emit**，不是 _on_entry —— 见类开头的说明
            self._unsubscribe = logbook.subscribe(self.entry_added.emit)

    def _unsubscribe_now(self):
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception as e:
                print(f"[Log] 退订失败：{type(e).__name__}: {e}")
            self._unsubscribe = None

    def stop_workers(self):
        """窗口要关了 —— 把订阅摘掉，别让日志往已经销毁的控件上写"""
        self._unsubscribe_now()

    # ---------- 渲染 ----------

    def _on_entry(self, entry):
        self._bump_counts()
        if not self.level_buttons[entry.level].isChecked():
            # 被筛掉的日志也要重算提示 —— 否则"日志来了但提示还挂着"
            self._refresh_hint()
            return
        self._append(entry)
        self.empty_hint.setVisible(False)

    def _append(self, entry):
        color = self._palette.get(LEVEL_STYLE[entry.level][1], "#e9e9ec")
        head = (f"<span style='color:{color}'>"
                f"[{entry.clock}] {entry.level.upper():<5} "
                f"{escape(entry.step)} — {escape(entry.title)}</span>")
        self.view.appendHtml(head)
        for extra in (entry.detail, entry.request, entry.response):
            if extra:
                self.view.appendPlainText(extra)
        if self._auto_scroll:
            self.view.verticalScrollBar().setValue(
                self.view.verticalScrollBar().maximum())

    def _rebuild(self):
        """按当前的级别过滤重画全部"""
        self.view.clear()
        shown = [e for e in logbook.entries()
                 if self.level_buttons[e.level].isChecked()]
        for entry in shown:
            self._append(entry)
        self._bump_counts()
        self._refresh_hint()
        if self._auto_scroll:
            self.view.verticalScrollBar().setValue(
                self.view.verticalScrollBar().maximum())

    def _bump_counts(self):
        counts = {level: 0 for level in self.level_buttons}
        for entry in logbook.entries():
            if entry.level in counts:
                counts[entry.level] += 1
        for level, btn in self.level_buttons.items():
            btn.setText(f"{LEVEL_LABEL[level]} {counts[level]}")

    def _refresh_hint(self):
        """空提示要分**两种**情况

        ⚠️ 只判断"有没有日志"是不够的：把级别全点掉之后，
        日志明明在、屏幕却是空的 —— 这时候显示"还没有日志"是假话，
        用户会以为程序把日志弄丢了。
        """
        entries = logbook.entries()
        if not entries:
            self.empty_hint.setText("还没有日志。去「正版验证」页跑一次就有了。")
            self.empty_hint.setVisible(True)
            return
        shown = sum(1 for e in entries if self.level_buttons[e.level].isChecked())
        if shown == 0:
            self.empty_hint.setText(
                "当前筛选下没有日志 —— 点上面那几个级别按钮可以切换显示。")
            self.empty_hint.setVisible(True)
            return
        self.empty_hint.setVisible(False)

    def _on_autoscroll(self, checked: bool):
        self._auto_scroll = bool(checked)

    # ---------- 动作 ----------

    def _copy_all(self):
        text = logbook.as_text()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        # 光标移到末尾，让人知道复制的是最新的
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    def _clear(self):
        logbook.clear()
        self._rebuild()
