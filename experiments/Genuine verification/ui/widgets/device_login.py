"""
微软登录面板（设备码流程）

## 为什么单独一个控件

另外两种验证方式是"填完点一下、出结果"，一个按钮就够。
微软登录是**交互式**的，要展示的东西也多：

    ① 点「开始登录」        → 后台申请设备码
    ② 界面显示 K7Q9-XYZ     → 用户去 microsoft.com/link 输码
    ③ 后台按 interval 轮询   → "还没输完"是正常状态，不能报错
    ④ 授权完 → 走完后面五步 → 存账户、出结果

而且第 ③ 步的等待时间**由用户决定**（可能半分钟，也可能去泡杯茶）。
所以状态机要能停在"等你"这个状态上，而不是一个阻塞调用。

## ⚠️ 两条容易写错的地方

**轮询里的"还没授权"不是错误。** `AuthorizationPending` 是设备码流程的
正常中间状态，当成失败的话用户一点开始就立刻看到"登录失败"。

**取消要能真的停下来。** 用户点了取消又点开始，上一次的轮询线程必须停 ——
不然两个线程会抢同一个 UI（另一个实验项目里踩过"过期结果覆盖新结果"）。
"""
from PyQt6.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget
)

from core import config
from core.verify import microsoft as ms

#: 设备码到期前多久提示（秒）
EXPIRE_WARN = 60


class LoginWorker(QThread):
    """后台跑"申请设备码 → 轮询 → 走完六步"

    ⚠️ 全程不能碰界面，只能发信号。
    """

    code_ready = pyqtSignal(dict)          # 设备码信息（给用户看的）
    waiting = pyqtSignal(int)              # 已经等了多久（秒）
    done = pyqtSignal(object, object)      # (账户, VerifyResult)
    failed = pyqtSignal(str, str, str)     # (步骤, 消息, 提示)

    def __init__(self, verifier, client_id: str, parent=None):
        super().__init__(parent)
        self.verifier = verifier
        self.client_id = client_id
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            device = self.verifier.begin(self.client_id)
        except ms.MicrosoftAuthError as e:
            self.failed.emit(e.step, e.message, e.hint)
            return
        except Exception as e:
            self.failed.emit("申请设备码", f"{type(e).__name__}: {e}", "")
            return

        if self._cancelled:
            return
        self.code_ready.emit(device)

        interval = max(1, int(device.get("interval") or 5))
        expires_in = int(device.get("expires_in") or 900)
        waited = 0

        while not self._cancelled and waited < expires_in:
            self.msleep(interval * 1000)
            waited += interval
            if self._cancelled:
                return
            self.waiting.emit(waited)
            try:
                account, result = self.verifier.poll_once(
                    self.client_id, device["device_code"])
            except ms.AuthorizationPending:
                continue                    # 正常：用户还没输完
            except ms.MicrosoftAuthError as e:
                self.failed.emit(e.step, e.message, e.hint)
                return
            except Exception as e:
                self.failed.emit("登录", f"{type(e).__name__}: {e}", "")
                return
            if not self._cancelled:
                self.done.emit(account, result)
            return

        if not self._cancelled:
            self.failed.emit("申请设备码", "设备码过期了（等太久）", "重新点一次「开始登录」")


class DeviceLoginPanel(QFrame):
    """设备码登录面板：填 client_id → 显示码 → 等授权 → 出结果"""

    #: 登录成功（账户已落盘）—— 外面据此刷新账户列表
    logged_in = pyqtSignal(dict)

    def __init__(self, verifier, parent=None):
        super().__init__(parent)
        self.verifier = verifier
        self._worker = None

        self.setObjectName("Card")
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)

        # ---------- 客户端 ID ----------
        label = QLabel("Azure 应用（客户端）ID")
        label.setObjectName("FieldLabel")
        box.addWidget(label)

        self.client_edit = QLineEdit(str(config.get("ms_client_id") or ""))
        self.client_edit.setPlaceholderText("00000000-0000-0000-0000-000000000000")
        self.client_edit.textChanged.connect(self._on_client_changed)
        box.addWidget(self.client_edit)

        hint = QLabel(
            "这个 ID 得是你**自己**在 Azure 门户注册的应用 —— 用别人的等于冒用别人的应用。\n"
            "而且新应用还要申请 Minecraft API 权限，否则第一步之后会 403。"
            "详见 README 的「微软登录怎么开通」。")
        hint.setObjectName("HintText")
        hint.setWordWrap(True)
        box.addWidget(hint)

        # ---------- 设备码区 ----------
        self.code_box = QWidget()
        code_layout = QVBoxLayout(self.code_box)
        code_layout.setContentsMargins(0, 6, 0, 0)
        code_layout.setSpacing(6)

        step = QLabel("去这个网址，输入下面这串码：")
        step.setObjectName("FieldLabel")
        code_layout.addWidget(step)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_label = QLabel("https://microsoft.com/link")
        self.url_label.setObjectName("DeviceUrl")
        self.url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        url_row.addWidget(self.url_label, 1)
        self.open_btn = QPushButton("打开浏览器")
        self.open_btn.setObjectName("PrimaryButton")
        self.open_btn.clicked.connect(self._open_browser)
        url_row.addWidget(self.open_btn)
        code_layout.addLayout(url_row)

        code_row = QHBoxLayout()
        code_row.setSpacing(8)
        self.code_label = QLabel("——————")
        self.code_label.setObjectName("DeviceCode")
        self.code_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        code_row.addWidget(self.code_label)
        self.copy_btn = QPushButton("复制验证码")
        self.copy_btn.clicked.connect(self._copy_code)
        code_row.addWidget(self.copy_btn)
        code_row.addStretch()
        code_layout.addLayout(code_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("HintText")
        self.status_label.setWordWrap(True)
        code_layout.addWidget(self.status_label)

        self.code_box.setVisible(False)
        box.addWidget(self.code_box)

        # ---------- 按钮 ----------
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.start_btn = QPushButton("开始登录")
        self.start_btn.setObjectName("PrimaryButton")
        self.start_btn.clicked.connect(self.start)
        buttons.addWidget(self.start_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        buttons.addWidget(self.cancel_btn)
        buttons.addStretch()
        box.addLayout(buttons)

        # 每秒更新一次"已经等了多久"，让用户知道程序还活着
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._elapsed = 0

        # 一开始就给一句"接下来会发生什么" —— 空白的状态栏会让人以为坏了
        self.status_label.setText(
            "填好上面的客户端 ID，点「开始登录」。\n"
            "接着这里会显示一串验证码，浏览器会自动打开，去那边输码就行。")
        self._sync_client_state()

    # ---------- 状态 ----------

    def _on_client_changed(self, text: str):
        config.set_key("ms_client_id", text.strip())
        self._sync_client_state()

    def _sync_client_state(self):
        """client_id 变了：更新提示，但**不禁用按钮**

        见 `_set_busy` 的说明 —— 禁用按钮 = 用户点了没反应还不知道为什么。
        """
        has_id = bool(self.client_edit.text().strip())
        if not self.cancel_btn.isEnabled():
            self.start_btn.setEnabled(True)
            if has_id:
                self.start_btn.setToolTip("")
            else:
                self.start_btn.setToolTip("没填客户端 ID —— 点了会告诉你缺什么")

    def start(self):
        client_id = self.client_edit.text().strip()
        if not client_id:
            # 不用弹窗，状态栏说清楚就行
            self.status_label.setText(
                "先填上面那个 **Azure 客户端 ID** 才能开始。\n"
                "它得是你自己在 Azure 门户注册的应用 ——— 见 README 的"
                "「微软登录怎么开通」。")
            return
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()

        self.code_box.setVisible(False)
        self._set_busy(True)
        self.status_label.setText("正在申请设备码…")
        self._elapsed = 0
        self._tick.start()

        self._worker = LoginWorker(self.verifier, client_id, parent=self)
        self._worker.code_ready.connect(self._on_code)
        self._worker.waiting.connect(self._on_waiting)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        # ⚠️ 保险丝：线程真的结束了就恢复可点状态。
        # 只靠 done/failed 那两条信号不够 —— 它们是在 run() 返回**前后**
        # 那一刻发的，主线程处理时线程可能还没完全结束（实测：
        # 失败之后按钮一直是灰的，再也点不动）。
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def cancel(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
        self._set_busy(False)
        self.status_label.setText("已取消。")
        self.code_box.setVisible(False)
        self.logged_in.emit({})

    def _set_busy(self, busy: bool):
        """统一的"正在忙"状态开关

        ⚠️ 开始按钮**永远不禁用**（除了正在忙的时候）。
        第一版是"没填 client_id 就禁用 + 只给 tooltip"，
        结果用户点了没反应也看不到原因 —— Windows 上**禁用按钮不弹 tooltip**。
        现在改成：按钮一直能点，缺东西就在状态栏里说清楚缺什么。
        """
        self.cancel_btn.setEnabled(busy)
        self.start_btn.setEnabled(not busy)
        self.client_edit.setEnabled(not busy)

    def _on_worker_finished(self):
        """线程结束 —— 无论如何都把界面恢复成"可以再来一次\""""
        if not self.cancel_btn.isEnabled():
            self._set_busy(False)

    # ---------- 后台回来的信号 ----------

    def _on_code(self, device: dict):
        # ⚠️ 优先用 verification_uri_complete —— 它已经把码带进链接了，
        # 用户点开就只剩"确认"，不用手输（少一步出错的机会）
        url = device.get("verification_uri_complete") or device.get("verification_uri") or ""
        self.url_label.setText(url or "https://microsoft.com/link")
        self._url = url
        self.code_label.setText(str(device.get("user_code") or "——————"))
        self._code = str(device.get("user_code") or "")
        self.code_box.setVisible(True)
        self.status_label.setText(
            f"把上面那串码输进去并完成登录。\n"
            f"输完之前这里会一直等 —— 现在等了 {self._elapsed} 秒。")
        # 直接开浏览器：用户点的就是"登录"，多问一次没意义
        self._open_browser()

    def _on_waiting(self, seconds: int):
        self._elapsed = seconds

    def _on_tick(self):
        self._elapsed += 1
        if self.cancel_btn.isEnabled() and self.code_box.isVisible():
            self.status_label.setText(
                f"等你在浏览器里完成授权…（已等 {self._elapsed} 秒）\n"
                f"授权页面没关的话，这里会自动继续，不用重新开始。")

    def _on_done(self, account, result):
        self._tick.stop()
        self.code_box.setVisible(False)
        self._set_busy(False)
        self.status_label.setText("登录成功。")
        self.logged_in.emit(account or {})

    def _on_failed(self, step: str, message: str, hint: str):
        self._tick.stop()
        self.code_box.setVisible(False)
        self._set_busy(False)
        text = f"【失败】{step}：{message}"
        if hint:
            text += f"\n{hint}"
        self.status_label.setText(text)

    # ---------- 小动作 ----------

    def _open_browser(self):
        url = getattr(self, "_url", "") or "https://microsoft.com/link"
        QDesktopServices.openUrl(QUrl(url))

    def _copy_code(self):
        code = getattr(self, "_code", "")
        if code:
            QGuiApplication.clipboard().setText(code)

    def stop(self):
        """窗口要关了：把轮询线程停掉

        ⚠️ 不停的话线程会在窗口销毁之后还往它发信号 —— Qt 会直接崩。
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
