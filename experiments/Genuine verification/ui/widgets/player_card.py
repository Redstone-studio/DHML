"""
一个玩家（在线名单里的一行）

头像 + 名字 + 来源 + "就是你"标记。

⚠️ 头像是**异步**来的：`set_avatar` 之前先显示首字母占位，
网络回来了再换图。不要在这里同步等 —— 一次列表十几个玩家，
同步等就是十几个 RTT。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ui.avatar_remote import AvatarPool, cache_key, pixmap_from_bytes
from ui.icons import screen_dpr

AVATAR_SIZE = 40


class PlayerCard(QFrame):
    """在线玩家卡片"""

    def __init__(self, player: dict, highlight: bool = False, parent=None):
        super().__init__(parent)
        self.player = player
        self.highlight = highlight
        self.setObjectName("PlayerCard")
        # QFrame 自带 WA_StyledBackground 的行为，不用手动开

        name = str(player.get("name") or "?")
        uuid = str(player.get("uuid") or "")
        source = player.get("source") or ""
        self._name = name
        self._uuid = uuid

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        self.avatar = QLabel()
        self.avatar.setObjectName("PlayerAvatar")
        self.avatar.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setText(name[:1].upper())
        layout.addWidget(self.avatar)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_label = QLabel(name)
        name_label.setObjectName("PlayerName")
        name_row.addWidget(name_label)

        if highlight:
            badge = QLabel("就是你")
            badge.setObjectName("BadgeAccent")
            name_row.addWidget(badge)

        name_row.addStretch()
        text_box.addLayout(name_row)

        meta = []
        if source == "query":
            meta.append("Query 完整名单")
        elif source == "slp":
            meta.append("SLP 抽样名单")
        elif source == "ali":
            meta.append("外置登录站")
        if uuid:
            meta.append(uuid)
        else:
            meta.append("服务器没给 UUID（按名字取头像）")
        meta_label = QLabel("  ·  ".join(meta))
        meta_label.setObjectName("PlayerMeta")
        text_box.addWidget(meta_label)

        layout.addLayout(text_box, 1)

        #: 皮肤地址（ALI 站点给的）优先 —— 那是**真皮肤**
        self._skin_key = ""
        skin_url = str(player.get("skin_url") or "")
        if skin_url:
            self._request_skin(skin_url)
        elif source == "ali":
            # ⚠️ 外置登录站的角色**没有皮肤**时，别去问 mc-heads ——
            # 它只认 Mojang 账号，第三方 UUID 它一律不知道，白跑一次网络。
            # 直接用本地那 9 张默认皮肤（按 UUID 挑，和游戏里一致）。
            self._use_local_default_skin()
            self._key = ""
        else:
            self._key = cache_key(name, uuid)
            self._request_avatar()

    def _use_local_default_skin(self):
        """用按 UUID 挑的本地默认皮肤当头像（不联网）"""
        try:
            from ui.avatar import account_avatar
            pixmap = account_avatar({"name": self._name, "uuid": self._uuid,
                                     "type": "ali"}, AVATAR_SIZE, screen_dpr(self))
        except Exception as e:
            print(f"[PlayerCard] 本地默认皮肤取不到：{type(e).__name__}: {e}")
            return
        if pixmap is not None and not pixmap.isNull():
            self.avatar.setText("")
            self.avatar.setPixmap(pixmap)

    # ---------- 头像 ----------

    def _request_avatar(self):
        pool = AvatarPool.instance()
        key = self._key
        if not key:
            return

        # 先在内存缓存里找：命中就不用走信号，也就不会有一次多余的 connect
        data = pool.memory(key)
        if data:
            self.set_avatar(data)
            return

        # ⚠️ 每次 connect 都要能被断开：卡片会被销毁重建（重新查询），
        # 不登出的话旧卡片的槽函数会被反复调到 —— 对象已经 deleteLater
        # 之后还会被调到，轻则白干活，重则 RuntimeError。
        pool.ready.connect(self._on_avatar)
        # ⚠️ **必须真的发请求**。第一版这里只 connect 没 request，
        # 表现成"头像永远是首字母占位"，而且不报错 ——
        # 而 e2e 断言当时写成"断网时允许没有头像"，正好把它放过去了。
        pool.request(self._name, self._uuid, AVATAR_SIZE)

    # ---------- 皮肤（ALI 外置登录那种：给的是 64×64 原图）----------

    def _request_skin(self, url: str):
        pool = AvatarPool.instance()
        self._skin_key = pool.request_skin(url)
        if not self._skin_key:
            return
        pool.skin_ready.connect(self._on_skin)

    def _on_skin(self, key: str, path: str):
        if key != getattr(self, "_skin_key", None):
            return
        try:
            # 抠脸交给主线程做：8×8 → 40×40 是微秒级，
            # 而且 QPixmap 本来就不能在非 GUI 线程里用
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

    def _on_avatar(self, key: str, data: bytes):
        if key != self._key:
            return
        try:
            self.set_avatar(data)
        finally:
            try:
                AvatarPool.instance().ready.disconnect(self._on_avatar)
            except TypeError:
                pass

    def set_avatar(self, data: bytes):
        pixmap = pixmap_from_bytes(data, AVATAR_SIZE, screen_dpr(self))
        if pixmap is None or pixmap.isNull():
            return
        self.avatar.setText("")
        self.avatar.setPixmap(pixmap)

    def clear_avatar(self):
        self.avatar.setPixmap(QPixmap())
        self.avatar.setText(str(self.player.get("name") or "?")[:1].upper())

    def refresh_theme(self):
        """主题换了不用重取头像，QSS 会自己重画"""
