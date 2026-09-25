"""
账户页：本地存下来的账号

用途很直接 —— **看得见**。登录完之后：
  · 有哪些账号
  · 哪个是当前在用的
  · 令牌过期了没（过期不代表账号没用，能刷新）
  · 头像长什么样
  · 不想要了怎么删

⚠️ **令牌一个字都不显示。** `refresh_token` 等价于长期的账号访问权，
放在界面上（截图、录屏、共享屏幕）就等于泄露。这里只给"有效 / 过期"这种结论。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
)

from core import accounts as acc
from ui.avatar_remote import AvatarPool, cache_key
from ui.icons import screen_dpr

AVATAR_SIZE = 40


class AccountRow(QFrame):
    """一个账号"""

    switched = pyqtSignal(str)
    removed = pyqtSignal(str)

    def __init__(self, account: dict, is_current: bool, parent=None):
        super().__init__(parent)
        self.account = account
        self.setObjectName("PlayerCardMe" if is_current else "PlayerCard")

        name = str(account.get("name") or "?")
        uid = str(account.get("uuid") or "")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        self.avatar = QLabel(name[:1].upper())
        self.avatar.setObjectName("PlayerAvatar")
        self.avatar.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.avatar)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_label = QLabel(name)
        name_label.setObjectName("PlayerName")
        name_row.addWidget(name_label)

        if is_current:
            badge = QLabel("当前使用")
            badge.setObjectName("BadgeAccent")
            name_row.addWidget(badge)

        # 令牌状态：**只说结论，不说内容**
        state = QLabel("令牌已过期（下次用时自动刷新）" if acc.is_expired(account)
                       else "令牌有效")
        state.setObjectName("BadgeWarn" if acc.is_expired(account) else "Badge")
        name_row.addWidget(state)
        name_row.addStretch()
        text_box.addLayout(name_row)

        meta = QLabel(f"{account.get('type', '?')}  ·  {uid}")
        meta.setObjectName("PlayerMeta")
        text_box.addWidget(meta)

        layout.addLayout(text_box, 1)

        if not is_current:
            use_btn = QPushButton("设为当前")
            use_btn.clicked.connect(lambda: self.switched.emit(uid))
            layout.addWidget(use_btn)

        del_btn = QPushButton("删除")
        del_btn.setObjectName("DangerButton")
        del_btn.clicked.connect(lambda: self.removed.emit(uid))
        layout.addWidget(del_btn)

        self._request_avatar(name, uid, account.get("skin_url") or "")

    # ---------- 头像 ----------

    def _request_avatar(self, name: str, uid: str, skin_url: str):
        pool = AvatarPool.instance()
        if skin_url:
            self._skin_key = pool.request_skin(skin_url)
            if self._skin_key:
                pool.skin_ready.connect(self._on_skin)
            return

        # 微软账号一定有皮肤地址；走到这儿说明是别的来源。
        # 本地默认皮肤（按 UUID 挑）即时可用，不用等网络。
        try:
            from ui.avatar import account_avatar
            pixmap = account_avatar(self.account, AVATAR_SIZE, screen_dpr(self))
            if pixmap is not None and not pixmap.isNull():
                self.avatar.setText("")
                self.avatar.setPixmap(pixmap)
        except Exception as e:
            print(f"[Accounts] 本地头像取不到：{type(e).__name__}: {e}")

    def _on_skin(self, key: str, path: str):
        if key != getattr(self, "_skin_key", None):
            return
        try:
            from ui.avatar import face_pixmap
            pixmap = face_pixmap(path, AVATAR_SIZE, screen_dpr(self))
            if pixmap is not None and not pixmap.isNull():
                self.avatar.setText("")
                self.avatar.setPixmap(pixmap)
        finally:
            try:
                AvatarPool.instance().skin_ready.disconnect(self._on_skin)
            except TypeError:
                pass


class AccountsPage(QWidget):
    """账户列表"""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(10)

        title = QLabel("账户")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        sub = QLabel("登录过的账号存在本地，重启之后还在。"
                     "令牌不会显示出来（它等价于账号的长期访问权）。")
        sub.setObjectName("PageSubtitle")
        sub.setWordWrap(True)
        root.addWidget(sub)

        root.addSpacing(6)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        host = QWidget()
        self.rows_layout = QVBoxLayout(host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)
        # ⚠️ 末尾常驻撑开项，行一律插在它前面（见 player_card 那边的说明）
        self.rows_layout.addStretch()
        self.scroll.setWidget(host)
        root.addWidget(self.scroll, 1)

        self.empty_label = QLabel("还没有账号。\n去「正版验证」页用微软登录或者外置登录加一个。")
        self.empty_label.setObjectName("EmptyState")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rows_layout.insertWidget(0, self.empty_label)

        footer = QLabel(f"账户文件：{acc.accounts_path()}")
        footer.setObjectName("ResultDetail")
        footer.setWordWrap(True)
        footer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(footer)

        self.reload()

    def reload(self):
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []

        accounts = acc.all_accounts()
        current = acc.get_current() or {}
        current_id = str(current.get("uuid") or "")

        self.empty_label.setVisible(not accounts)
        for account in accounts:
            is_current = str(account.get("uuid") or "") == current_id
            row = AccountRow(account, is_current)
            row.switched.connect(self._on_switch)
            row.removed.connect(self._on_remove)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
            self._rows.append(row)

    def _on_switch(self, uid: str):
        acc.set_current(uid)
        self.reload()
        self.changed.emit()

    def _on_remove(self, uid: str):
        from PyQt6.QtWidgets import QMessageBox
        name = (acc.find(uid) or {}).get("name") or uid
        reply = QMessageBox.question(
            self, "删除账户",
            f"要把「{name}」从本地删掉吗？\n"
            f"（只删本地的记录，微软那边的账号不受影响，重新登录即可找回）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        acc.remove(uid)
        self.reload()
        self.changed.emit()
