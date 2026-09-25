"""下载进度窗口（独立窗口，跟日志窗口一个模式）

布局对齐 `experiments/Downloading mod test` 里那版（用户点名要这个风格）：
左边一列大读数（总进度 / 速度 / 剩余文件），右边是文件卡片，底部一行保存路径，
右下角「取消下载 / 关闭」。

    ┌ 正在安装 1.20.4 ─────────────────────────────────┐
    │ 正在下载 12 个文件…                               │
    │ ████████████░░░░░░░░░░░░░░░░░░                    │
    ├──────────┬───────────────────────────────────────┤
    │ 总进度    │ [client.jar]            100%  25.0 MB │
    │ 18.42 %  │ ██████████████████████████████        │
    │ 下载速度  │ [alpha-1.0.jar]          42%  1.2 MB/s│
    │ 3.4 MB/s │ ████████░░░░░░░░░░░░░░░░░░░░░░        │
    │ 剩余文件  │                                       │
    │ 12       │                                       │
    ├──────────┴───────────────────────────────────────┤
    │ 保存到：…/versions/1.20.4/1.20.4.jar  [取消下载][关闭]│
    └──────────────────────────────────────────────────┘

⚠️ **样式一律走 assets/styles/parts 的 QSS（@token@），不在代码里写死颜色。**
   实验那版是写死 #1b1c1f / #5ec269 的 —— 它只跑深色所以没事，
   我们两套主题，写死了浅色主题下就是一块瞎。状态色也是用
   `kind` 属性交给 QSS 选，不是 setStyleSheet。

## 已经有的文件不显示 + 下完的行自己消失

- 引擎在真正下载前会先校验本地文件，通过就标 `skipped` → **这类任务从头到尾不列**
  （重新点一次安装时最明显：一开窗就列一堆"已存在"纯属噪音）
- **完成的行留 HIDE_DONE_S 秒再撤掉**：749 个资源文件是一批批下完的，
  下完立刻撤会让列表一阵一阵地空，看着像界面坏了
- **失败 / 取消的行一直留着**：下失败了反而从列表里消失，等于把问题藏起来，
  而那正是最需要看见的东西

这两件事都靠 `_sync_rows` 的**增量增删**实现（只动该动的那几行）。
别改回"整体重建"：配上"下完就撤"会变成每 200ms 重建一次列表 —— 会闪、
滚动位置乱跳。

## 为什么是 QWidget 而不是 QDialog

- 不 modal：下载时用户还能切页面看别的（而且 modal 会让离屏测试卡住）
- 跟 `ui/dialogs/log_window.py` 一个模式（那也是独立 QWidget 窗口）

## 为什么用 QTimer 轮询而不是信号

下载引擎用的是**普通 Python 线程**（见 core/download.py 的说明：QThread 的生死
太难管，我们被 `QThread: Destroyed while thread is still running` 坑过）。
跨线程只读一份 `snapshot()` 是最稳的：不用管信号排队，也不怕线程比窗口先死。
"""

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget
)

from core import download as dl
from core.download import human_size, human_speed
from core.i18n import tr

POLL_MS = 200
MAX_ROWS = 12          # 只显示这么多行（几百个库全铺出来没意义，也卡）
LEFT_WIDTH = 132       # 左边读数那一列的宽度（跟实验版一致）
# 下完的行再留这么久才从列表里撤掉（秒）。
# 为什么要留：749 个资源文件是一批批下完的，下完立刻撤会让列表"一阵一阵地空"，
# 看着像界面坏了；留一下能看见"这个刚完成"。
HIDE_DONE_S = 0.8


def _readout(caption: str):
    """左边一列里的一个小读数：小标题 + 大数字"""
    box = QVBoxLayout()
    box.setSpacing(1)
    cap = QLabel(caption)
    cap.setObjectName("DownloadCaption")
    value = QLabel("—")
    value.setObjectName("DownloadValue")
    box.addWidget(cap)
    box.addWidget(value)
    return box, value


class _Row(QFrame):
    """一行：文件名 + 状态 + 进度条（卡片样式）"""

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        self.setObjectName("DownloadRow")
        self._last = None
        self._kind = None

        box = QVBoxLayout(self)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(8)
        label = task.label or task.dest.name
        if task.kind:
            # `[库] xxx.jar`：跟实验里的 `[Mod] xxx` 一个路子
            label = "[%s] %s" % (task.kind, label)
        self.name = QLabel(label)
        self.name.setObjectName("DownloadRowName")
        self.name.setToolTip(str(task.dest))     # 名字太长时能看到完整路径
        top.addWidget(self.name, 1)
        self.state = QLabel()
        self.state.setObjectName("DownloadRowState")
        top.addWidget(self.state)
        box.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setObjectName("DownloadBar")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        box.addWidget(self.bar)

    def refresh(self):
        task = self.task
        pct = task.percent
        text = self._state_text(task)
        # 值没变就别动控件：几百个文件每 200ms 无脑刷一遍是纯浪费
        # （控件没变也会走一次 setText/setValue + 重排）
        if self._last == (text, pct):
            return
        self._last = (text, pct)
        if pct is None:
            # 总大小还不知道（服务器没给 Content-Length）→ 不确定进度条
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(pct * 1000))
        self.state.setText(text)
        self._apply_kind()

    @staticmethod
    def _state_text(task) -> str:
        """右侧那句话

        对齐实验那版：显示「已下 / 总共」（`0 B / 2.6 MB`），而不是"完成/等待"。
        唯一的例外是出错 —— 实验那边出错也只显示字节数，等于把失败藏起来了，
        这里改成直接显示原因（并且上红色），不然用户根本不知道为什么少文件。
        """
        if task.error or task.state == dl.STATE_FAILED:
            return task.error or tr("失败")
        return "%s / %s" % (human_size(task.done_bytes), human_size(task.total))

    def _apply_kind(self):
        """按任务状态给状态文字换色（属性 + QSS，不写死颜色）"""
        state = self.task.state
        if self.task.error or state == dl.STATE_FAILED:
            kind = "err"
        elif state == dl.STATE_DONE:
            kind = "ok"
        elif state == dl.STATE_CANCELLED:
            kind = "dim"
        else:
            kind = ""
        if kind == self._kind:
            return
        self._kind = kind
        self.state.setProperty("kind", kind)
        # ⚠️ 改了 property 必须让 QSS 重算一遍，否则样式不会跟着变
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)


class DownloadWindow(QWidget):
    """下载/安装进度窗口

    用法：
        win = DownloadWindow(manager, title="正在安装 1.20.4", parent=self)
        win.show()
        manager.start()          # ⚠️ 先摆窗口再 start，否则前几百毫秒的进度会丢

    ⚠️ `parent` **别省**：QSS 是从父控件继承的，不给 parent 的顶层窗口
    拿不到主窗口的样式表，会整个退化成 Qt 原生外观（白底 + 系统灰按钮）。
    """

    def __init__(self, manager, title: str = "", parent=None):
        # ⚠️ 必须这么写（跟 ui/dialogs/log_window.py 一致）：
        #   1. **传 parent**：QSS 是从父控件往下继承的，主窗口的样式表
        #      不会自动传给"没有父控件的顶层窗口"。不带 parent 的话，
        #      这个窗口会整个退化成 Qt 原生外观（白底灰按钮 + 系统进度条），
        #      而且主窗口那边一点报错都没有 —— 真机上就是这么翻车的。
        #   2. **加 Qt.Window 标志**：带了 parent 但不加这个标志的话，
        #      它会变成"嵌在页面里的子控件"而不是独立窗口。
        super().__init__(parent, Qt.WindowType.Window)
        self.manager = manager
        self._rows = []
        self._done_at = {}          # id(任务) → 第一次看到它"下完"的时刻（缓冲用）
        self._retry_note = 0        # 点了重试、还没开始下的那个提示（见 _refresh_subtitle）
        self._more_label = None

        self.setObjectName("DownloadWindow")
        self.setWindowTitle(title or tr("下载中"))
        self.resize(780, 480)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # ---------- 标题 ----------
        self.title = QLabel(title or tr("下载中"))
        self.title.setObjectName("DownloadTitle")
        outer.addWidget(self.title)

        self.subtitle = QLabel(tr("准备中…"))
        self.subtitle.setObjectName("DownloadSubtitle")
        outer.addWidget(self.subtitle)

        # ---------- 总进度条 ----------
        self.total_bar = QProgressBar()
        # 总进度条单独一个名字：行内的小条 6px、总条 10px，
        # 共用一个 objectName 的话 QSS 里没法区分（两个 ID 选择器谁赢不好算）
        self.total_bar.setObjectName("DownloadBarTotal")
        self.total_bar.setTextVisible(False)
        self.total_bar.setRange(0, 1000)
        outer.addWidget(self.total_bar)

        # ---------- 中间：左边读数 + 右边文件列表 ----------
        middle = QHBoxLayout()
        middle.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(14)
        box1, self.v_total = _readout(tr("总进度"))
        box2, self.v_speed = _readout(tr("下载速度"))
        box3, self.v_left = _readout(tr("剩余文件"))
        for b in (box1, box2, box3):
            left.addLayout(b)
        left.addStretch()
        left_box = QWidget()
        left_box.setFixedWidth(LEFT_WIDTH)
        left_box.setLayout(left)
        middle.addWidget(left_box)

        # ⚠️ 别把 QWidget 和它的 layout 用同一个变量名：那个 QWidget 一旦
        # 没被引用就会被 Qt 回收，后面用到就是 "wrapped C/C++ object deleted"。
        self.scroll = QScrollArea()
        self.scroll.setObjectName("DownloadScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        list_host = QWidget()
        list_host.setObjectName("DownloadListHost")
        self._rows_box = QVBoxLayout(list_host)
        self._rows_box.setContentsMargins(0, 0, 6, 0)
        self._rows_box.setSpacing(6)
        self._rows_box.addStretch()
        self.scroll.setWidget(list_host)
        middle.addWidget(self.scroll, 1)

        outer.addLayout(middle, 1)

        # ---------- 底部：保存路径 + 按钮 ----------
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.footer = QLabel("")
        self.footer.setObjectName("DownloadFooter")
        bottom.addWidget(self.footer, 1)

        self.cancel_btn = QPushButton(tr("取消下载"))
        # 跟实验一致：平时是中性按钮，**悬停才变红**。
        # 直接挂 #DangerButton 的话一上来就是红的，太吵了。
        self.cancel_btn.setObjectName("DownloadCancelButton")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        bottom.addWidget(self.cancel_btn)

        # 有失败的任务时才出现（见 _refresh_retry_btn）
        self.retry_btn = QPushButton(tr("重试失败的文件"))
        self.retry_btn.setObjectName("PrimaryButton")
        self.retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry_btn.clicked.connect(self._on_retry)
        self.retry_btn.setVisible(False)
        bottom.addWidget(self.retry_btn)

        self.close_btn = QPushButton(tr("关闭"))
        self.close_btn.setObjectName("PrimaryButton")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # 关窗口**不会**停下载：管理器还在后台跑（页面那边会收到结果）。
        # 这里把话说清楚，免得用户以为关掉就取消了。
        self.close_btn.setToolTip(tr("关掉这个窗口不会停止下载，进度会继续"))
        self.close_btn.clicked.connect(self.close)
        bottom.addWidget(self.close_btn)
        outer.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    # ---------- 刷新 ----------

    def refresh(self):
        snap = self.manager.snapshot()
        self._sync_rows(snap)
        for row in self._rows:
            row.refresh()
        self._refresh_stats(snap)
        self._refresh_subtitle(snap)
        self._refresh_footer(snap)
        self._refresh_retry_btn(snap)
        if snap["finished"] and not self._in_grace():
            # 全下完、而且"刚完成"那几行也撤干净了，才停定时器。
            # ⚠️ 不能只判 finished：那会儿完成的行还在缓冲期里，
            # 一停定时器它们就再也没人负责撤掉，会永远挂在列表上。
            self._timer.stop()

    def _sync_rows(self, snap):
        """决定哪些任务该出现在列表里，然后**增量**增删

        规则：
          - 已存在（skipped）→ 不列（从来没真的下过）
          - 完成 → 再留 HIDE_DONE_S 秒，然后撤掉（让用户看见"刚完成"）
          - 失败 / 取消 → **一直留着** —— 下失败了反而消失，等于把问题藏起来
          - 正在下 / 等着下 → 留着

        ⚠️ 这里刻意**不做整体重建**。原来那种"可见集合一变就全删了重建"的写法，
        配上"下完就撤"会变成每 200ms 重建一次列表：闪、滚动位置乱跳、
        刚建出来的行还会短暂显示空进度。所以只动该动的那几行。
        """
        now = time.monotonic()
        tasks = snap["tasks"]

        eligible = []
        for task in tasks:
            if task.skipped:
                continue
            if task.state == dl.STATE_DONE:
                first = self._done_at.setdefault(id(task), now)
                if now - first < HIDE_DONE_S:
                    eligible.append(task)       # 缓冲期内还留着
                continue
            eligible.append(task)               # 失败/取消/下载中/等待

        want = eligible[:MAX_ROWS]
        want_ids = {id(t) for t in want}

        # 该撤的行撤掉
        for row in list(self._rows):
            if id(row.task) not in want_ids:
                self._rows.remove(row)
                self._rows_box.removeWidget(row)
                row.setParent(None)
                row.deleteLater()

        # 该补的行补上（撤掉一行之后，把后面等着的那行顶上来）。
        # 新任务在任务表里一定排在现有的后面，所以直接追加就能保持顺序。
        have = {id(r.task) for r in self._rows}
        for task in want:
            if id(task) in have:
                continue
            row = _Row(task)
            self._rows_box.insertWidget(self._rows_box.count() - 1, row)
            self._rows.append(row)

        self._sync_more_label(max(0, len(eligible) - len(self._rows)))

    def _in_grace(self) -> bool:
        """还有没有"刚完成、正留着"的行（决定定时器能不能停）"""
        now = time.monotonic()
        return any(now - at < HIDE_DONE_S for at in self._done_at.values())

    def _sync_more_label(self, need: int):
        if need <= 0:
            if self._more_label is not None:
                self._more_label.hide()
            return
        if self._more_label is None:
            self._more_label = QLabel()
            self._more_label.setObjectName("DownloadFooter")
            self._rows_box.insertWidget(self._rows_box.count() - 1,
                                        self._more_label)
        self._more_label.setText(tr("还有 {n} 个文件没列出来", n=need))
        self._more_label.show()

    def _refresh_stats(self, snap):
        pct = snap["percent"]
        if pct is None:
            # 总大小还不知道就把已下的字节数亮出来，别显示一个假的 0%
            self.v_total.setText(human_size(snap["bytes"]) if snap["bytes"] else "—")
            self.total_bar.setRange(0, 0)
        else:
            self.v_total.setText("%.2f %%" % (pct * 100))
            self.total_bar.setRange(0, 1000)
            self.total_bar.setValue(int(pct * 1000))
        self.v_speed.setText(human_speed(snap["speed"]))
        # 「剩余文件」= 还没收尾的任务数（已存在的算完成，所以不计入）
        self.v_left.setText(str(max(0, snap["count"] - snap["done"])))

    def _refresh_subtitle(self, snap):
        if snap["active"]:
            self._retry_note = 0        # 真的开始下了，重试那句话可以收了
            text = tr("正在下载 {n} 个文件…", n=snap["active"])
            # 镜像在熔断冷却中（BMCLAPI 抽风时）就说一句：
            # 不说的话进度看着像卡住，用户会以为程序死了
            if snap.get("mirror_off"):
                text = "%s　%s" % (text, tr("镜像源不通，已改用官方源继续下载…"))
            self.subtitle.setText(text)
            return
        if snap["finished"]:
            self._retry_note = 0
            if snap["failed"]:
                self.subtitle.setText(tr("下载结束，但有 {n} 个文件失败",
                                         n=snap["failed"]))
            else:
                self.subtitle.setText(tr("全部完成"))
            return
        if self._retry_note:
            # 点了重试、但字节还没开始流：这句留着，否则会立刻被"准备中…"盖掉，
            # 用户以为点了没反应
            self.subtitle.setText(tr("正在重试 {n} 个失败的文件…",
                                     n=self._retry_note))
            return
        self.subtitle.setText(tr("准备中…"))

    def _refresh_footer(self, snap):
        tasks = snap["tasks"]
        pick = None
        for task in tasks:
            if task.active:
                pick = task
                break
        if pick is None:
            for task in tasks:
                if not task.finished:
                    pick = task
                    break
        self.footer.setText(tr("保存到：{path}", path=pick.dest) if pick else "")

    def _on_cancel(self):
        self.manager.cancel_all()
        self.cancel_btn.setEnabled(False)
        self.subtitle.setText(tr("正在取消…（会把没下完的临时文件删掉）"))

    def _on_retry(self):
        """把失败的那些文件再下一遍

        ⚠️ 必须**重启定时器**：上一次跑完之后它已经被停掉了（见 refresh），
        不重启的话点了重试就再没人刷新界面，看起来像没反应。
        """
        self.retry_btn.setVisible(False)
        self.cancel_btn.setEnabled(True)
        count = self.manager.retry_failed()
        if count:
            # 缓冲期的记录清掉，否则重试成功的行会立刻被当成"早就完成了"撤掉
            self._done_at.clear()
            self._retry_note = count
            self._timer.start()
        self.refresh()

    def _refresh_retry_btn(self, snap):
        """只有"跑完了 + 确实有失败"才给这个按钮，其它时候收起来"""
        if snap["finished"] and snap["failed"]:
            self.retry_btn.setText(tr("重试失败的文件（{n}）", n=snap["failed"]))
            self.retry_btn.setVisible(True)
        else:
            self.retry_btn.setVisible(False)

    # ---------- 收尾 ----------

    def stop(self):
        """外部要收尾时调一下：停掉定时器，别让它在窗口销毁后还往回调

        顺手把还在缓冲期里的"完成"行撤掉。调用方（下载页）一般是在整个安装
        刚结束时就调它，那会儿最后几个文件可能还在 0.8 秒缓冲里 ——
        定时器一停，那些行就再也没人负责撤，会一直挂在列表上。
        """
        self._timer.stop()
        if self._done_at:
            for key in self._done_at:
                self._done_at[key] = 0.0        # 让缓冲立即过期
            self._sync_rows(self.manager.snapshot())

    def closeEvent(self, event):
        # 只停定时器：下载继续（管理器不归窗口管），页面那边会收到结果
        self._timer.stop()
        super().closeEvent(event)
