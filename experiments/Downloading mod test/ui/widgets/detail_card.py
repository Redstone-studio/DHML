"""
Mod 详情卡片
"""
import requests
from urllib.parse import quote
from datetime import datetime

from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QMenu
)
from PyQt6.QtGui import QPixmap, QDesktopServices, QGuiApplication
from PyQt6.QtCore import Qt, QByteArray, QUrl, QTimer

from core import mcmod


HEADERS = {"User-Agent": "Mosslight-Launcher/1.0"}

# 图标下载共用一个线程池（搜索页也有一个，两边都读同一个设置）
_ICON_POOL = None


def _icon_pool():
    """按设置里的线程数建/取共享线程池

    ⚠️ 别每次 load 都新建一个 QThreadPool —— 每个池都有自己的线程数上限，
    用户点几个模组就会把线程数放大好几倍，比单线程还糟。
    """
    global _ICON_POOL
    from PyQt6.QtCore import QThreadPool
    from ui.widgets import appearance
    if _ICON_POOL is None:
        _ICON_POOL = QThreadPool()
        _ICON_POOL.setMaxThreadCount(appearance.effective_threads())
    else:
        # 设置里改过线程数的话，下次用的时候顺手同步一下
        _ICON_POOL.setMaxThreadCount(appearance.effective_threads())
    return _ICON_POOL

LOADER_LABEL = {
    "neoforge": "NeoForge",
    "fabric": "Fabric",
    "forge": "Forge",
    "quilt": "Quilt",
}


def format_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def format_date(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return (iso_str or "")[:10]


def search_mcmod_url(name: str) -> str:
    return f"https://search.mcmod.cn/s?key={quote(name)}"


class ModDetailCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            ModDetailCard {
                background-color: #232428;
                border-radius: 12px;
                border: 1px solid #2e3034;
            }
            QLabel { color: #e9e9ec; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(96, 96)
        self.icon_label.setStyleSheet("border-radius: 12px; background-color: #2a2c30;")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setText("?")
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(6)

        name_row = QHBoxLayout()
        self.name_label = QLabel("加载中...")
        self.name_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff;")
        name_row.addWidget(self.name_label)

        self.name_en_label = QLabel("")
        self.name_en_label.setStyleSheet("font-size: 13px; color: #888;")
        name_row.addWidget(self.name_en_label)
        name_row.addStretch()
        right.addLayout(name_row)

        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet("font-size: 12px; color: #a0a1a7;")
        right.addWidget(self.meta_label)

        self.desc_label = QLabel("")
        self.desc_label.setStyleSheet("font-size: 12px; color: #b8b8b8;")
        self.desc_label.setWordWrap(True)
        right.addWidget(self.desc_label)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("font-size: 12px; color: #a0a1a7;")
        right.addWidget(self.stats_label)

        right.addSpacing(6)

        btn_row = QHBoxLayout()
        self.btn_modrinth = QPushButton("转到 Modrinth")
        self.btn_mcmod = QPushButton("转到 MC 百科")
        self.btn_copy = QPushButton("复制名称")

        for btn in (self.btn_modrinth, self.btn_mcmod, self.btn_copy):
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #2a2c30;
                    color: #e9e9ec;
                    border: 1px solid #3a3c42;
                    border-radius: 6px;
                    padding: 0 14px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #34363a;
                    border-color: #5ec269;
                    color: #5ec269;
                }
            """)
            btn_row.addWidget(btn)

        self.btn_modrinth.setStyleSheet("""
            QPushButton {
                background-color: #3f8f4b; color: white;
                border: none; border-radius: 6px;
                padding: 0 14px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #4da25a; }
        """)

        btn_row.addStretch()
        right.addLayout(btn_row)

        layout.addLayout(right, 1)
        self.data = None
        # MC 百科：先给"搜索页"链接（立即可用），后台解析出词条后再升级
        self._mcmod_url = ""
        self._mcmod_search_url = ""
        self._mcmod_entry = ""
        self._mcmod_confidence = None
        self._mcmod_worker = None
        # 图标请求令牌：卡片是复用的，迟到的图标不能盖到新 mod 上
        self._icon_token = 0
        self._icon_url = ""

    def prefetch(self, hit: dict):
        """用**搜索结果**先把卡片填上，别等网络

        搜索结果里已经有名字、图标、描述、下载量这些了（`/search` 接口给的字段
        够用），所以点进去的瞬间就能显示新 mod 的信息 —— 等详情接口回来再补全
        （分类、加载器这些搜索结果里没有）。

        ⚠️ 这解决一个观感问题：详情页是从右边滑进来的，如果滑进来时卡片还挂着
        **上一个 mod** 的内容，看着就像"显示了上一个页面"。
        """
        if not hit:
            return
        self.load({
            "title": hit.get("title", ""),
            "slug": hit.get("slug", ""),
            "description": hit.get("description", ""),
            "categories": [],              # 搜索结果里没有，等详情回来补
            "loaders": [],
            "downloads": hit.get("downloads", 0),
            "updated": hit.get("date_modified", ""),
            "icon_url": hit.get("icon_url", ""),
        })

    def _safe_connect(self, btn, slot):
        try:
            btn.clicked.disconnect()
        except TypeError:
            pass
        btn.clicked.connect(slot)

    def load(self, data: dict):
        self.data = data

        # ⚠️ 图标**必须异步拿**。
        # 这里原来是 `requests.get(icon_url, timeout=10)` 直接跑在主线程上 ——
        # 实测点进详情页会卡住 2.4 秒（网络慢就卡满 10 秒的超时），
        # 表现为"点一下整页冻住，然后所有内容一股脑出来"。
        # 现在和搜索页一样：先看内存/磁盘缓存，没有就丢给线程池。
        self.icon_label.setPixmap(QPixmap())
        self.icon_label.setText("?")
        # ⚠️ 图标这里**每次都重新走一遍**，别做"URL 相同就跳过"的优化：
        # 预填（prefetch）会先发一次请求，而本方法开头把 _icon_token 递增、
        # 把那次的结果作废了；如果这里再因为"URL 没变"而跳过，
        # 就再也没有人去取图标了 —— 表现成"进详情页图标是空的（?）"。
        # 好在 _load_icon_async 第一步就查磁盘缓存，重复调用几乎零成本。
        self._icon_url = ""
        self._load_icon_async(data.get("icon_url", ""))

        self.name_label.setText(data.get("title", ""))
        self.name_en_label.setText(f"|  {data.get('slug', '')}")

        cats = data.get("categories", [])
        loaders = data.get("loaders", [])
        loader_text = " / ".join(LOADER_LABEL.get(l, l.capitalize()) for l in loaders)
        cat_text = ", ".join(cats)
        self.meta_label.setText(f"📂 {cat_text}    ⚙ {loader_text}    🌐 Modrinth")

        self.desc_label.setText(data.get("description", ""))

        downloads = data.get("downloads", 0)
        updated = format_date(data.get("updated", ""))
        self.stats_label.setText(
            f"📥 下载量: {format_number(downloads)}    🕒 上次更新: {updated}"
        )

        slug = data.get("slug", "")
        self._safe_connect(
            self.btn_modrinth,
            lambda: QDesktopServices.openUrl(QUrl(f"https://modrinth.com/mod/{slug}"))
        )

        name = data.get("title", "")

        # MC 百科：先挂"搜索页"链接 —— 用户马上就能点，不用等解析。
        # （Modrinth 和百科没有共享 ID，得靠搜名字反查词条，见 core/mcmod.py）
        self._mcmod_url = mcmod.search_url(name)
        self._mcmod_search_url = self._mcmod_url
        self._mcmod_entry = ""
        self._mcmod_confidence = None
        self._refresh_mcmod_button()
        self._safe_connect(self.btn_mcmod, self._open_mcmod)
        self._resolve_mcmod_async(name)

        self._safe_connect(self.btn_copy, lambda: self._copy(name))

    # ---------- MC 百科直达 ----------
    #
    # 三档行为（置信度来自 core/mcmod.pick_best）：
    #   exact  → 直接跳词条
    #   strong / weak → 弹个小菜单，让用户选"去词条"还是"去搜索页"
    #   没解析到 → 直接开搜索页
    #
    # 为什么要问：百科里名字会撞车（搜 "Fabric API" 会出来 FabricAPI /
    # Forgified Fabric API / Quilt Standard Libraries / Cloth API）。
    # 精确命中可以直接跳，但只靠"包含"或"词重合"挑出来的，选错就白跑一趟。

    def _open_mcmod(self):
        if not self._mcmod_url:
            return
        if self._mcmod_confidence in (mcmod.CONFIDENCE_EXACT, None):
            QDesktopServices.openUrl(QUrl(self._mcmod_url))
            return
        self._popup_mcmod_choice()

    def _popup_mcmod_choice(self):
        """置信度不够时：让用户自己点一下确认"""
        menu = QMenu(self)
        exact_item = menu.addAction(f"① 直接去词条：{self._mcmod_entry}")
        exact_item.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl(self._mcmod_url)))
        menu.addSeparator()
        search_item = menu.addAction("② 去百科搜索页，我自己挑")
        search_item.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl(self._mcmod_search_url)))
        menu.exec(self.btn_mcmod.mapToGlobal(self.btn_mcmod.rect().bottomLeft()))

    def _refresh_mcmod_button(self):
        """按解析结果更新按钮文字和提示（提示里一定写清会跳到哪）"""
        if self._mcmod_entry and self._mcmod_confidence == mcmod.CONFIDENCE_EXACT:
            self.btn_mcmod.setText("转到 MC 百科")
            self.btn_mcmod.setToolTip(
                f"直达词条（名字完全一致）：{self._mcmod_entry}")
        elif self._mcmod_entry:
            # 名字只做到"像" —— 按钮上点明"可能"，点了会先问一句
            self.btn_mcmod.setText("MC 百科（确认）")
            self.btn_mcmod.setToolTip(
                f"最像的词条是「{self._mcmod_entry}」，但名字不是完全一致。\n"
                f"点一下会让你选：去这个词条，还是去搜索页自己挑")
        else:
            self.btn_mcmod.setText("MC 百科（搜索）")
            self.btn_mcmod.setToolTip(
                "没找到明确对应的词条，会打开百科的搜索结果页\n"
                "（Modrinth 和百科没有共享 ID，只能按名字反查）")

    def _resolve_mcmod_async(self, name: str):
        """后台解析百科词条（失败/拿不准就退回搜索页）"""
        if not name:
            return
        # 预填时已经为同一个名字解析过了，别重复发（百科那边有限速，省着点用）
        if name == getattr(self, "_mcmod_resolving_name", None):
            return
        from ui.workers.mcmod_worker import McmodResolveWorker
        # 记下"这次是为哪个名字解析的"，结果回来时要核对（用户可能已经切走了）
        self._mcmod_name = name
        self._mcmod_resolving_name = name
        worker = McmodResolveWorker(name)
        worker.done.connect(self._on_mcmod_resolved)
        self._mcmod_worker = worker
        worker.start()

    def _on_mcmod_resolved(self, cid, title, confidence, search_url):
        # ⚠️ 迟到的结果可能属于上一个 mod（用户点得快），对不上就丢掉
        if getattr(self, "_mcmod_name", None) != (self.data or {}).get("title", ""):
            return
        if search_url:
            self._mcmod_search_url = search_url
        if not cid:
            return
        self._mcmod_url = mcmod.class_url(cid)
        self._mcmod_entry = title or ""
        self._mcmod_confidence = confidence
        self._refresh_mcmod_button()

    def _copy(self, text: str):
        QGuiApplication.clipboard().setText(text)
        original = self.btn_copy.text()
        self.btn_copy.setText("已复制 ✓")
        QTimer.singleShot(1500, lambda: self.btn_copy.setText(original))

    # ---------- 图标（异步，绝不在主线程联网） ----------

    def _load_icon_async(self, url: str):
        """先查缓存，没有就丢给线程池去下

        复用搜索页那套 IconTask + core.cache，所以两个页面的图标缓存是同一份，
        同一个模组在搜索页看过，进详情页就是秒出、不发第二次请求。

        ⚠️ 本卡片是**复用同一个控件**的（换 mod 时只改内容）。所以图标结果回来
        必须核对是不是当前这个 mod 的 —— 原来用 `_mcmod_name` 判归属是错的
        （那是给百科按钮用的字段），迟到的图标会盖到新 mod 上。
        """
        if not url:
            return
        self._icon_url = url
        self._icon_token += 1
        token = self._icon_token

        from core import cache
        cached = cache.load(url)
        if cached:
            self._apply_icon(cached, token)
            return

        from ui.workers.icon_worker import IconTask
        task = IconTask(url)
        task.signals.finished.connect(
            lambda u, data, t=token: self._apply_icon(data, t))
        task.signals.failed.connect(lambda _u, t=token: None)
        _icon_pool().start(task)

    def _apply_icon(self, data: bytes, token=None):
        # 迟到的图标直接丢（见 _load_icon_async 的说明）
        if token is not None and token != self._icon_token:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(QByteArray(data))
        if pixmap.isNull():
            return
        self.icon_label.setText("")
        self.icon_label.setPixmap(pixmap.scaled(
            96, 96,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
