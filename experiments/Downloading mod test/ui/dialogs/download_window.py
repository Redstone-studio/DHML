"""
下载进度窗口（独立窗口）

点「下载」后弹出来，单独一个窗口 —— 这样用户可以一边下一边继续翻版本列表。

布局参考 PCL2 的「任务管理」和 HMCL 的下载页，但**没有照搬**：
左边一列读数（总进度 / 速度 / 剩余文件），右边是文件列表，底部右下角取消。

## 为什么刷新走定时器，而不是每个信号刷一次

下载线程每收 64KB 就发一次信号 —— 一个 10MB 的文件就是 160 次，
再乘上并发数，每次都要重建一堆 QLabel 文本，纯属浪费。
这里用一个 150ms 的定时器统一刷新（人眼也看不出差别），
线程那边只管累加字节数。
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QProgressBar, QScrollArea, QFrame, QWidget
)

from core import download as dl
from ui.widgets import appearance

# 统一刷新间隔（毫秒）
REFRESH_MS = 150


def _readout(title: str) -> tuple:
    """左边一列里的一个小读数：标题 + 大数字"""
    box = QVBoxLayout()
    box.setSpacing(2)
    cap = QLabel(title)
    cap.setStyleSheet("color: #8a8f98; font-size: 11px;")
    value = QLabel("—")
    value.setStyleSheet("color: #e9e9ec; font-size: 17px; font-weight: bold;")
    box.addWidget(cap)
    box.addWidget(value)
    return box, value


class _TaskRow(QFrame):
    """文件列表里的一行：名字 + 进度条 + 状态"""

    def __init__(self, task: dl.DownloadTask):
        super().__init__()
        self.task = task
        self.setObjectName("TaskRow")
        self.setStyleSheet("""
            #TaskRow {
                background-color: #232428;
                border: 1px solid #2e3034;
                border-radius: 8px;
            }
            #TaskRow QLabel { background: transparent; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.name = QLabel(f"[{task.kind}] {task.title}")
        self.name.setStyleSheet("color: #e9e9ec; font-size: 12px;")
        self.name.setWordWrap(False)
        top.addWidget(self.name, 1)
        self.state = QLabel("等待中")
        self.state.setStyleSheet("color: #8a8f98; font-size: 11px;")
        top.addWidget(self.state)
        layout.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 100)
        self.bar.setStyleSheet("""
            QProgressBar {
                background-color: #2a2c30; border: none; border-radius: 3px;
            }
            QProgressBar::chunk { background-color: #5ec269; border-radius: 3px; }
        """)
        layout.addWidget(self.bar)

        # 上一次刷进去的值（None 哨兵：保证第一遍一定刷）
        self._last = None

    def refresh(self):
        t = self.task
        pct = t.percent

        # ⚠️ 值没变就直接返回。整合包动辄几百个文件，每 150ms 无脑刷一遍
        # 所有行的 QLabel + QProgressBar 是纯浪费 —— 控件没变也会走一次
        # setText/setValue + 重排。以后要下 500 个文件时这条是必须的。
        if self._last == (t.state, pct):
            return
        self._last = (t.state, pct)

        if t.state == dl.DONE:
            self.bar.setRange(0, 100)
            self.bar.setValue(100)
        elif pct is None:
            # 总大小未知：进度条空着但别显示假的 100%
            self.bar.setRange(0, 100)
            self.bar.setValue(0)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(pct))

        self.state.setText(t.status_text())
        color = {
            dl.DONE: "#5ec269",
            dl.FAILED: "#e0575f",
            dl.CANCELLED: "#8a8f98",
        }.get(t.state, "#8a8f98")
        self.state.setStyleSheet(f"color: {color}; font-size: 11px;")


class DownloadWindow(QDialog):
    """下载进度窗口

    用法：
        win = DownloadWindow(parent)
        win.enqueue([DownloadTask(...), ...])
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("下载任务")
        self.setModal(False)              # 非模态：下着的时候还能翻列表
        self.resize(760, 460)
        self.setStyleSheet("""
            QDialog { background-color: #1b1c1f; }
            QLabel { color: #e9e9ec; }
            /* ⚠️ 滚动区必须显式设透明：不设的话它和 viewport 会露出系统默认的
               浅灰底色，在深色窗口里就是一大块刺眼的白（实测）。 */
            QScrollArea, QScrollArea > QWidget > QWidget {
                border: none;
                background: transparent;
            }
        """)

        self.manager = dl.DownloadManager(parent=self, on_update=None)
        self.manager.set_max_workers(appearance.effective_threads())
        self._rows = []

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        # ---------- 标题 ----------
        head = QVBoxLayout()
        head.setSpacing(2)
        title = QLabel("下载任务")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        head.addWidget(title)
        self.subtitle = QLabel("准备中…")
        self.subtitle.setStyleSheet("color: #8a8f98; font-size: 11px;")
        head.addWidget(self.subtitle)
        root.addLayout(head)

        # ---------- 总进度条 ----------
        self.total_bar = QProgressBar()
        self.total_bar.setTextVisible(False)
        self.total_bar.setFixedHeight(8)
        self.total_bar.setRange(0, 100)
        self.total_bar.setStyleSheet("""
            QProgressBar {
                background-color: #2a2c30; border: none; border-radius: 4px;
            }
            QProgressBar::chunk { background-color: #3f8f4b; border-radius: 4px; }
        """)
        root.addWidget(self.total_bar)

        # ---------- 中间：左边读数 + 右边列表 ----------
        middle = QHBoxLayout()
        middle.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(14)
        box1, self.v_total = _readout("总进度")
        box2, self.v_speed = _readout("下载速度")
        box3, self.v_left = _readout("剩余文件")
        for b in (box1, box2, box3):
            left.addLayout(b)
        left.addStretch()
        left_box = QWidget()
        left_box.setFixedWidth(132)
        left_box.setLayout(left)
        middle.addWidget(left_box)

        # 文件列表
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.list_host = QWidget()
        self.list_host.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_host)
        middle.addWidget(self.scroll, 1)

        root.addLayout(middle, 1)

        # ---------- 底部 ----------
        bottom = QHBoxLayout()
        self.footer = QLabel("")
        self.footer.setStyleSheet("color: #8a8f98; font-size: 11px;")
        bottom.addWidget(self.footer, 1)

        self.cancel_btn = QPushButton("取消下载")
        self.cancel_btn.setFixedHeight(30)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2c30; color: #e9e9ec;
                border: 1px solid #3a3c42; border-radius: 6px;
                padding: 0 16px; font-size: 12px;
            }
            QPushButton:hover:enabled { border-color: #e0575f; color: #ff8f95; }
            QPushButton:disabled { color: #6e7076; border-color: #2b2d31; }
        """)
        self.cancel_btn.clicked.connect(self.cancel_all)
        bottom.addWidget(self.cancel_btn)

        self.close_btn = QPushButton("关闭")
        self.close_btn.setFixedHeight(30)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #3f8f4b; color: white;
                border: none; border-radius: 6px;
                padding: 0 18px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #4da25a; }
        """)
        self.close_btn.clicked.connect(self.close)
        bottom.addWidget(self.close_btn)
        root.addLayout(bottom)

        # 定时刷新（见文件头说明）
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)

    # ---------- 对外 ----------

    def enqueue(self, tasks: list):
        """把一批任务交进来，马上开跑"""
        for task in tasks:
            self.manager.add(task)
            row = _TaskRow(task)
            # 插到 stretch 前面
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self._rows.append(row)
        self.manager.start()
        self._timer.start()
        self._refresh()

    def cancel_all(self):
        self.manager.cancel_all()
        self._refresh()

    # ---------- 刷新 ----------

    def _refresh(self):
        got, total, percent, speed = self.manager.progress()
        active = self.manager.active_count()
        done = self.manager.finished_count()
        count = len(self.manager.tasks())

        if percent is None:
            self.v_total.setText(dl.human_size(got) if got else "—")
            self.total_bar.setRange(0, 100)
            self.total_bar.setValue(0)
        else:
            self.v_total.setText(f"{percent:.2f} %")
            self.total_bar.setValue(int(percent))

        self.v_speed.setText(dl.human_speed(speed))
        self.v_left.setText(str(active))

        if active:
            self.subtitle.setText(f"正在下载 {active} 个文件…")
        elif done and done == count:
            failed = sum(1 for t in self.manager.tasks() if t.state == dl.FAILED)
            cancelled = sum(1 for t in self.manager.tasks() if t.state == dl.CANCELLED)
            bits = [f"完成 {done - failed - cancelled} 个"]
            if failed:
                bits.append(f"失败 {failed}")
            if cancelled:
                bits.append(f"已取消 {cancelled}")
            self.subtitle.setText("，".join(bits))
            # ⚠️ **只停定时器，别再调一次 _refresh()** —— 那会无限递归
            # （_refresh → _refresh → … 直到 RecursionError，实测爆栈）。
            # 这次调用本来就会把下面几行走完，速度值自然会清掉。
            self._timer.stop()

        self.cancel_btn.setEnabled(active > 0)

        for row in self._rows:
            row.refresh()

        # 底部提示：正在下的文件放到哪了
        running = [t for t in self.manager.tasks() if t.state == dl.RUNNING]
        if running:
            self.footer.setText(f"保存到：{running[0].path}")
        elif self.manager.tasks():
            self.footer.setText(f"保存到：{self.manager.tasks()[0].path}")

    # ---------- 关闭 ----------

    def closeEvent(self, event):
        if self.manager.active_count() > 0:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "还有下载在进行",
                "关掉窗口会取消正在下载的文件（不会留下半个文件）。要继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.manager.cancel_all()
            # 取消是异步的（worker 要写完手头那块、删掉 .part 才退）。
            # 不等一下就让窗口被销毁的话，QThread 会在运行中被析构。
            self.manager.wait_all()
        self._timer.stop()
        super().closeEvent(event)
