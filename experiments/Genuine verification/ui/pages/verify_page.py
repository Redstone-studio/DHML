"""
正版验证页

左边那排"验证方式"是从 `core/verify` 的注册表来的 —— 加一种新方式不用改这个文件。

## 这个页面刻意做的事

**把"凭什么"写出来。** 验证结果只给一个"通过/失败"是没用的：
  · 通过了 → 要说清是"服务端已经用正版账号把你认下来了"，不是"我们查了你"
  · 只有抽样名单没抽到你 → 明确说**不能判定**，而不是算作失败
  · 连不上 → 要给出**实际连的**地址（SRV 解析之后那个），否则没法排查

**头像异步加载。** 一次列表十几个玩家，同步取图就是十几个 RTT 的卡死。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget
)

from core import config
from core.verify import (
    STATE_ABSENT, STATE_ERROR, STATE_INCONCLUSIVE, STATE_VERIFIED,
    all_verifiers, get_verifier,
)
from ui.avatar_remote import AvatarPool
from ui.widgets.player_card import PlayerCard

#: 结果状态 → 横幅的 objectName（样式见 assets/styles/parts/80-verify.qss）
BANNER_STYLE = {
    STATE_VERIFIED: "VerifyBannerOk",
    STATE_INCONCLUSIVE: "VerifyBannerWarn",
    STATE_ABSENT: "VerifyBannerBad",
    STATE_ERROR: "VerifyBannerBad",
}

TIMEOUT_CHOICES = (("3 秒", 3.0), ("5 秒", 5.0), ("8 秒", 8.0), ("15 秒", 15.0))


class VerifyPage(QWidget):
    #: 账户表变了（登录成功 / 切换 / 删除）—— 外面据此刷新账户页
    account_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._token = 0
        self._worker = None
        self._cards = []
        self._verifier = get_verifier(config.get("verifier"))

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(10)

        # ---------- 标题 ----------
        title = QLabel("正版验证")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        self.subtitle = QLabel()
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setWordWrap(True)
        root.addWidget(self.subtitle)

        root.addSpacing(4)

        # ---------- 验证方式 ----------
        self.method_row = QHBoxLayout()
        self.method_row.setSpacing(8)
        self.method_buttons = {}
        for v in all_verifiers():
            btn = QPushButton(v.title)
            btn.setObjectName("MethodTab")
            btn.setCheckable(True)
            btn.setEnabled(v.implemented)
            btn.setCursor(Qt.CursorShape.PointingHandCursor if v.implemented
                          else Qt.CursorShape.ForbiddenCursor)
            if not v.implemented:
                btn.setToolTip("还没实现 —— 现在只有「玩家列表」能用")
            btn.clicked.connect(lambda _c, k=v.key: self._on_method(k))
            self.method_row.addWidget(btn)
            self.method_buttons[v.key] = btn
        self.method_row.addStretch()
        root.addLayout(self.method_row)

        # ---------- 表单 ----------
        form_card = QFrame()
        form_card.setObjectName("Card")
        form = QVBoxLayout(form_card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)

        form.addWidget(self._field_label("验证服务器"))
        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("mc.example.com 或 mc.example.com:25565")
        form.addWidget(self.address_edit)

        # 地址框的说明随验证方式变（外置登录要讲清两种写法都能吃）
        self.address_hint = self._hint("")
        form.addWidget(self.address_hint)

        self.name_label = self._field_label("玩家名（要验证的那个账号）")
        form.addWidget(self.name_label)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("游戏里的名字，例如 Notch")
        form.addWidget(self.name_edit)

        # 密码：只有选了"外置登录"才显示。
        # ⚠️ **绝不落盘、绝不进日志** —— 它只在这一次验证里活一下。
        self.pass_label = self._field_label("密码（可留空：留空就只查角色存不存在）")
        form.addWidget(self.pass_label)
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_edit.setPlaceholderText("填了才会真的登录一次，用来证明这个角色是你的")
        form.addWidget(self.pass_edit)

        options = QHBoxLayout()
        options.setSpacing(12)
        self.query_check = QCheckBox("优先用 Query 取完整名单")
        self.query_check.setToolTip(
            "Query 能拿到完整在线名单；服务器没开 enable-query 时会自动退回 SLP。\n"
            "SLP 只给最多 12 个抽样名字 —— 区别很重要，见页脚的说明。")
        options.addWidget(self.query_check)
        options.addWidget(self._field_label("超时"))
        self.timeout_combo = QComboBox()
        for label, value in TIMEOUT_CHOICES:
            self.timeout_combo.addItem(label, value)
        self.timeout_combo.setFixedWidth(96)
        options.addWidget(self.timeout_combo)
        options.addStretch()
        form.addLayout(options)

        form.addWidget(self._hint(
            "⚠️ 只对 online-mode=true 的**正版服**有效。离线服不校验账号，"
            "谁都能用你的名字进去，那时这个结果什么都证明不了。"))

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        self.run_btn = QPushButton("开始验证")
        self.run_btn.setObjectName("PrimaryButton")
        self.run_btn.clicked.connect(self.run_verify)
        run_row.addWidget(self.run_btn)
        self.clear_btn = QPushButton("清空结果")
        self.clear_btn.clicked.connect(self.clear_results)
        run_row.addWidget(self.clear_btn)
        run_row.addStretch()
        form.addLayout(run_row)

        root.addWidget(form_card)
        self.form_card = form_card

        # ---------- 微软登录面板（交互式，只有选了微软才出现）----------
        # ⚠️ 延迟创建：它里面会建 QThread，另外两种方式根本用不到
        self.login_panel = None

        # ---------- 结果横幅 ----------
        self.banner = QLabel("还没开始验证。")
        self.banner.setObjectName("VerifyBanner")
        self.banner.setWordWrap(True)
        root.addWidget(self.banner)

        self.detail = QLabel("")
        self.detail.setObjectName("ResultDetail")
        self.detail.setWordWrap(True)
        root.addWidget(self.detail)

        # ---------- 在线玩家 ----------
        players_head = QHBoxLayout()
        players_head.setSpacing(8)
        self.players_title = QLabel("在线玩家")
        self.players_title.setObjectName("SectionTitle")
        players_head.addWidget(self.players_title)
        players_head.addStretch()
        root.addLayout(players_head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        host = QWidget()
        self.players_layout = QVBoxLayout(host)
        self.players_layout.setContentsMargins(0, 0, 0, 0)
        self.players_layout.setSpacing(6)
        # ⚠️ 末尾常驻一个撑开项，卡片一律**插在它前面**。
        # 不加的话内容不满一屏时，QVBoxLayout 会把多出来的高度平摊给每一行
        # （另一个实验项目踩过：2 条结果时每行被拉到 224px）
        self.players_layout.addStretch()
        self.scroll.setWidget(host)
        root.addWidget(self.scroll, 1)

        self.empty_label = QLabel("还没有结果")
        self.empty_label.setObjectName("EmptyState")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.players_layout.insertWidget(0, self.empty_label)

        self._load_config()
        self._sync_method_buttons()

    # ---------- 小工具 ----------

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    def _hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("HintText")
        label.setWordWrap(True)
        return label

    # ---------- 配置 ----------

    def _load_config(self):
        self.address_edit.setText(str(config.get("server_address") or ""))
        self.name_edit.setText(str(config.get("player_name") or ""))
        self.query_check.setChecked(bool(config.get("prefer_query", True)))
        timeout = float(config.get("timeout") or 5.0)
        index = self.timeout_combo.findData(timeout)
        self.timeout_combo.setCurrentIndex(index if index >= 0 else 1)

    def _save_config(self):
        config.set_key("server_address", self.address_edit.text().strip())
        config.set_key("player_name", self.name_edit.text().strip())
        config.set_key("prefer_query", self.query_check.isChecked())
        config.set_key("timeout", float(self.timeout_combo.currentData() or 5.0))
        # ⚠️ **密码一个字都不存。** 不写配置、不进日志、不留在界面上。

    # ---------- 验证方式 ----------

    def _ensure_login_panel(self):
        """第一次选中微软登录时才建面板"""
        if self.login_panel is not None:
            return
        from ui.widgets.device_login import DeviceLoginPanel
        self.login_panel = DeviceLoginPanel(self._verifier)
        self.login_panel.logged_in.connect(self._on_logged_in)
        # 插在表单卡片后面（表单卡片 index 1：标题、副标题、方法行之后）
        self.layout().insertWidget(3, self.login_panel)

    def _on_logged_in(self, account: dict):
        """登录成功：面板已经存过盘了，这里只负责刷界面"""
        if not account:
            return
        self.account_changed.emit()

    def stop_workers(self):
        """窗口要关了：把还在轮询的线程停掉（不停的话 Qt 会崩）"""
        if self.login_panel is not None:
            self.login_panel.stop()

    def _on_method(self, key: str):
        self._verifier = get_verifier(key)
        config.set_key("verifier", key)
        self._sync_method_buttons()
        self._cards_clear()
        self.banner.setObjectName("VerifyBanner")
        self.banner.setText("还没开始验证。")
        self._repolish(self.banner)
        self.detail.setText("")
        # 换了方式，地址/名字的含义完全变了（服务器地址 vs 验证站地址），
        # 留着上一次的值只会让人点下去得到莫名其妙的错。
        # 交互式的那种（微软）根本没有这两个框，跳过。
        if not getattr(self._verifier, "interactive", False):
            self.address_edit.clear()
            self.name_edit.clear()

    def _sync_method_buttons(self):
        for key, btn in self.method_buttons.items():
            btn.setChecked(key == self._verifier.key)
        self.subtitle.setText(f"{self._verifier.title} —— {self._verifier.hint}")

        # 交互式的那一种（微软登录）不用这套"填表 + 查询"的表单，
        # 换成专门的设备码面板
        interactive = bool(getattr(self._verifier, "interactive", False))
        self.form_card.setVisible(not interactive)
        if interactive:
            self._ensure_login_panel()
        if self.login_panel is not None:
            self.login_panel.setVisible(interactive)

        needs_server = self._verifier.needs_server
        self.address_edit.setEnabled(needs_server)

        # 密码框只在外置登录时出现。切换方式时**顺手清掉** ——
        # 免得它留在内存里，或者用户以为填过一次就通用
        show_pass = bool(getattr(self._verifier, "needs_password", False))
        self.pass_label.setVisible(show_pass)
        self.pass_edit.setVisible(show_pass)
        if not show_pass:
            self.pass_edit.clear()

        # Query 那个选项只对"玩家列表"有意义
        is_player_list = self._verifier.key == "player_list"
        self.query_check.setVisible(is_player_list)

        if is_player_list:
            self.address_edit.setPlaceholderText("mc.example.com 或 mc.example.com:25565")
            self.name_edit.setPlaceholderText("游戏里的名字，例如 Notch")
            self.address_hint.setText("要查的是**游戏服务器**的地址。")
        else:
            # 站点上那两个按钮复制的值**两种都能直接粘进来** ——
            # 「复制地址（推荐）」给的是站点根，「复制完整 API」给的是 API 根。
            # 自动识别会把前者补全成后者（读响应头，见 core/verify/yggdrasil.py）
            self.address_edit.setPlaceholderText(
                "demo.lunch.ink （或完整 API：https://demo.lunch.ink/api/yggdrasil）")
            self.name_edit.setPlaceholderText("验证站上的角色名，例如 kongxia_114")
            self.address_hint.setText(
                "站点上「复制地址」和「复制完整 API」**两种都能直接粘进来**。\n"
                "粘站点地址时会自动识别 API 根（读响应头 X-Authlib-Injector-API-Location）。\n"
                "密码可以留空 —— 留空就只查角色在不在，不验证归属。")

    # ---------- 跑验证 ----------

    def run_verify(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()

        self._save_config()
        self._token += 1
        token = self._token

        ctx = {
            "server_address": self.address_edit.text().strip(),
            "player_name": self.name_edit.text().strip(),
            "prefer_query": self.query_check.isChecked(),
            "timeout": float(self.timeout_combo.currentData() or 5.0),
            # 密码只进这一次请求。**不进配置、不进日志**
            "password": self.pass_edit.text() if self.pass_edit.isVisible() else "",
        }

        self.run_btn.setEnabled(False)
        self.run_btn.setText("查询中…")
        self.banner.setObjectName("VerifyBanner")
        self.banner.setText("正在查询服务器…")
        self._repolish(self.banner)
        self.detail.setText("")

        from ui.workers.verify_worker import VerifyWorker
        self._worker = VerifyWorker(self._verifier, ctx, token, parent=self)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, result):
        # ⚠️ 令牌校验：用户连点两次时，先发出去的那次可能后回来。
        # 不校验的话界面会被过期结果覆盖（另一个实验项目踩过同样的坑）
        if getattr(result, "token", None) != self._token:
            return

        self.run_btn.setEnabled(True)
        self.run_btn.setText("开始验证")
        self._render(result)

    # ---------- 渲染结果 ----------

    def _render(self, result):
        self.banner.setObjectName(BANNER_STYLE.get(result.state, "VerifyBanner"))
        self.banner.setText(f"【{result.label}】{result.message}")
        self._repolish(self.banner)

        bits = []
        if result.host:
            # ⚠️ host 有两种形态：玩家列表那条路是**主机名**（要拼 :端口），
            # 外置登录那条路是**完整的 API 根 URL**（拼上去就变成
            # `.../api/yggdrasil:0` 这种鬼东西 —— 实测踩过）。
            if str(result.host).startswith("http"):
                bits.append(f"API 根 {result.host}")
            elif result.port:
                bits.append(f"实际连接 {result.host}:{result.port}")
            else:
                bits.append(f"实际连接 {result.host}")
        if result.elapsed:
            bits.append(f"耗时 {result.elapsed:.2f}s")
        status = result.server
        if status is not None:
            if status.version:
                bits.append(f"版本 {status.version}")
            if status.online or status.max:
                bits.append(f"在线 {status.online}/{status.max}")
            bits.append("Query 通" if status.query_ok else "Query 不通（只有 SLP 抽样）")
            if status.motd:
                bits.append(f"标语 {status.motd}")
        self.detail.setText("  ·  ".join(bits))

        self._show_players(result)

    def _show_players(self, result):
        self._cards_clear()
        players = list(result.players or [])
        wanted = (self.name_edit.text() or "").strip().lower()

        self.players_title.setText(f"在线玩家（{len(players)}）")
        self.empty_label.setVisible(not players)
        if not players:
            self.empty_label.setText(
                "这台服务器没有给出任何玩家名字。\n"
                "很多服务器会把 SLP 的抽样位当广告位，Query 又没开 —— 这时拿不到名单。")

        pool = AvatarPool.instance()
        for player in players:
            is_me = bool(wanted) and (player.get("name") or "").lower() == wanted
            card = PlayerCard(player, highlight=is_me)
            # ⚠️ 插在撑开项前面（见 __init__ 里的说明）
            self.players_layout.insertWidget(self.players_layout.count() - 1, card)
            self._cards.append(card)
        _ = pool

    def _cards_clear(self):
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards = []

    def clear_results(self):
        self._cards_clear()
        self.empty_label.setVisible(True)
        self.empty_label.setText("还没有结果")
        self.players_title.setText("在线玩家")
        self.banner.setObjectName("VerifyBanner")
        self.banner.setText("结果已清空。")
        self._repolish(self.banner)
        self.detail.setText("")

    # ---------- 主题 ----------

    def _repolish(self, widget):
        """改了 objectName 之后必须 unpolish + polish，否则 QSS 不会重新匹配

        ⚠️ 只 setObjectName 是**没用的** —— Qt 不会因为名字变了就重新算样式，
        表现成"结果变了但颜色没变"。
        """
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def refresh_theme(self):
        for card in self._cards:
            card.refresh_theme()
