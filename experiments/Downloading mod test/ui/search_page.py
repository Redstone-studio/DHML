"""
搜索页
- 顶部筛选栏
- 结果列表
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QLineEdit, QComboBox, QFrame
)
from PyQt6.QtCore import Qt, QThreadPool, pyqtSignal, QTimer
import time

from ui.workers.search_worker import SearchWorker
from ui.workers.icon_worker import IconTask
from ui.widgets.mod_card import ModResultCard
from ui.widgets.slide_in import StaggerReveal, SlideInRow
from ui.widgets.loading_bar import LoadingBar
from ui.widgets.link_tabs import LinkTabs
from ui.widgets import anim_settings, appearance
from ui.widgets.anim_prefs import preset, scaled, speed_factor
from core import cache


def search_tab_text(label: str) -> str:
    """类型标签的文字："模组" → "搜模组"。只取"模组"两个字，更好扫"""
    short = label[:-1] if label.endswith("包") else label
    return f"搜{short}"


# 每批加载多少个（Modrinth 的 /search 上限是 100）。
# 12 而不是 20：首批要快、要"秒出"，剩下的滑到底再要。
SEARCH_PAGE_SIZE = 12
# 离底部还有这么多像素就开始抓下一批。
# 40 = 基本等于"真贴到底才抓"（只留一点余量，免得贴底那一瞬间出现空白期）。
# 以前是 320：那是**提前**加载，看着像"还没滚到底就开始下东西了"。
LOAD_MORE_THRESHOLD = 40
# "内容不够一屏时自动补几批"的上限。
# 1 = 只补一次：补一批是为了不让首屏空着，但用户明确要"滚到底才加载"，
# 所以不能由着它连补好几批。
MAX_AUTO_FILL = 1
# 布局稳定下来要等多久（毫秒）—— 布局期 scrollbar 的 rangeChanged 会连发几十次，
# 而且那时候视口高度还没定，必须等它稳定再判断
LAYOUT_SETTLE_MS = 120


def current_preset():
    """当前生效的动画参数（预设 + 速度档；设置里改了下一次加载就生效）"""
    return scaled(preset(anim_settings.get_preset_key()),
                  speed_factor(anim_settings.get_speed_key()))


# ============================================================
# 分批建造（治"数据到了先顿一下再出来"）
# ============================================================
#
# 实测：一口气造 24 张卡片（ModResultCard + SlideInRow + 入布局）要 **40ms 左右**，
# 而一帧的预算只有 16.7ms。这 40ms 里主线程在做纯 Python/Qt 的构造工作，
# 界面完全不动 —— 看着就是"卡一下下才出来"。加载条那个收起动画也一起被卡住。
#
# 所以改成按时间预算分批：每帧最多干 FRAME_BUDGET_MS 毫秒，干不完的下一帧接着干
# （用 singleShot(0) 让出去，Qt 就有机会把这一帧画出来）。
# 用户看到的是"卡片一批批冒出来"，而不是"先冻住再全出来"。
FRAME_BUDGET_MS = 12.0
# 每帧最多造几张。光有时间预算不够用：每 addWidget 一次都会触发一次布局重排，
# 而重排成本随已有卡片数增长，所以"下一张要多久"估不准（实测第一帧还是塞了 16 张）。
# 再加个条数硬上限兜底，保证一帧的活是有限的。
FRAME_MAX_ITEMS = 6


MC_VERSIONS = [
    "全部",
    "1.21.4", "1.21.3", "1.21.2", "1.21.1", "1.21",
    "1.20.6", "1.20.4", "1.20.2", "1.20.1", "1.20",
    "1.19.4", "1.19.2", "1.19",
    "1.18.2", "1.18.1",
    "1.17.1",
    "1.16.5", "1.16.4",
    "1.15.2", "1.14.4",
    "1.12.2", "1.7.10",
]

LOADERS = ["全部", "fabric", "forge", "neoforge", "quilt"]

PROJECT_TYPES = [
    ("mod", "模组"),
    ("shader", "光影"),
    ("resourcepack", "资源包"),
    ("datapack", "数据包"),
    ("modpack", "整合包"),
]

CATEGORIES = [
    "全部",
    ("adventure", "冒险"),
    ("decoration", "装饰"),
    ("economy", "经济"),
    ("equipment", "装备"),
    ("food", "食物"),
    ("game-mechanics", "游戏机制"),
    ("library", "支持库"),
    ("magic", "魔法"),
    ("management", "管理"),
    ("minigame", "小游戏"),
    ("mobs", "生物"),
    ("optimization", "性能优化"),
    ("social", "社交"),
    ("storage", "存储"),
    ("technology", "科技"),
    ("transportation", "交通"),
    ("utility", "实用工具"),
    ("worldgen", "世界生成"),
]


class SearchPage(QWidget):
    open_detail = pyqtSignal(dict)   # ← 新增：点卡片 → 通知主窗口
    open_background = pyqtSignal()   # ← 「背景」按钮 → 主窗口去开背景设置

    def __init__(self):
        super().__init__()

        self.icon_pool = QThreadPool()
        # 线程数读设置里的「多线程下载/访问」（appearance.effective_threads()：
        # 开关关着就是默认 8，开着才用滑块的值）
        self.icon_pool.setMaxThreadCount(appearance.effective_threads())

        self.icon_memory_cache = {}
        self.url_to_cards = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ===== 筛选区：一张带标题的卡片 =====
        # 这一整块（标题 + 类型标签 + 输入框 + 筛选 + 按钮）当作"同一件事"收在一起，
        # 比原来散成两行更像一个整体。
        panel = QFrame()
        panel.setObjectName("FilterPanel")
        panel.setStyleSheet("""
            #FilterPanel {
                background-color: rgba(28, 29, 34, 220);
                border: 1px solid #2e3034;
                border-radius: 10px;
            }
            #FilterPanel QLabel { color: #a0a1a7; font-size: 12px; }

            /* ---------- 类型标签（一排互斥按钮） ----------
               选中态靠动态属性 selected，不是靠 :checked ——
               这样互斥逻辑归代码管，外观归 QSS 管，两边不打架。
               注意顺序：:hover 写在 [selected="true"] 前面，
               否则选中的那个悬停时会丢掉绿色。 */
            #FilterPanel QPushButton#LinkTab {
                background-color: #232428;
                color: #c8cad0;
                border: 1px solid #34363a;
                border-radius: 7px;
                padding: 7px 16px;
                font-size: 12.5px;
            }
            #FilterPanel QPushButton#LinkTab:hover {
                background-color: #2c2e33;
                color: #ffffff;
                border-color: #4a4d54;
            }
            #FilterPanel QPushButton#LinkTab[selected="true"] {
                background-color: #3f8f4b;
                color: #ffffff;
                border-color: #5ec269;
                font-weight: bold;
            }
            #FilterPanel QPushButton#LinkTab[selected="true"]:hover {
                background-color: #4da25a;
                border-color: #6fd67c;
            }
        """)
        panel_box = QVBoxLayout(panel)
        panel_box.setContentsMargins(14, 12, 14, 12)
        panel_box.setSpacing(10)

        # 标题
        self.panel_title = QLabel("搜索资源")
        self.panel_title.setStyleSheet(
            "color: #e9e9ec; font-size: 15px; font-weight: bold;")
        panel_box.addWidget(self.panel_title)

        # ---- 类型：标签页（样式模仿 HTML 的 <a> 链接，不是按钮）----
        self.type_tabs = LinkTabs()
        for key, label in PROJECT_TYPES:
            self.type_tabs.add_tab(key, search_tab_text(label))
        self.type_tabs.changed.connect(self._on_ptype_changed)
        panel_box.addWidget(self.type_tabs)

        # ---- 搜索框 ----
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索模组 / 光影 / 资源包...")
        self.search_input.returnPressed.connect(self.do_search)
        panel_box.addWidget(self.search_input)

        # ---- 筛选（版本 / 加载器 / 分类）----
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        row1.addWidget(QLabel("版本:"))
        self.mc_combo = QComboBox()
        self.mc_combo.addItems(MC_VERSIONS)
        self.mc_combo.setFixedWidth(110)
        row1.addWidget(self.mc_combo)

        row1.addWidget(QLabel("加载器:"))
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(LOADERS)
        self.loader_combo.setFixedWidth(100)
        row1.addWidget(self.loader_combo)

        row1.addWidget(QLabel("分类:"))
        self.cat_combo = QComboBox()
        for item in CATEGORIES:
            if isinstance(item, tuple):
                self.cat_combo.addItem(item[1], item[0])
            else:
                self.cat_combo.addItem(item, item)
        self.cat_combo.setFixedWidth(120)
        row1.addWidget(self.cat_combo)

        row1.addStretch()
        panel_box.addLayout(row1)

        # ---- 操作按钮 ----
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        self.search_btn = QPushButton("🔍 搜索模组")
        self.search_btn.setFixedHeight(34)
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background-color: #3f8f4b; color: white;
                border: none; border-radius: 6px;
                padding: 0 22px; font-weight: bold;
            }
            QPushButton:hover { background-color: #4da25a; }
            QPushButton:disabled { background-color: #555; color: #999; }
        """)
        self.search_btn.clicked.connect(self.do_search)
        row2.addWidget(self.search_btn)

        self.reset_btn = QPushButton("重置")
        self.reset_btn.setFixedHeight(34)
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2c30; color: #e9e9ec;
                border: 1px solid #3a3c42; border-radius: 6px;
                padding: 0 16px;
            }
            QPushButton:hover { background-color: #34363a; }
        """)
        self.reset_btn.clicked.connect(self.do_reset)
        row2.addWidget(self.reset_btn)

        self.anim_btn = QPushButton("动效")
        self.anim_btn.setFixedHeight(34)
        self.anim_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.anim_btn.setToolTip("设置卡片入场动画的风格（里面带实时预览）")
        self.anim_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2c30; color: #e9e9ec;
                border: 1px solid #3a3c42; border-radius: 6px;
                padding: 0 16px;
            }
            QPushButton:hover { background-color: #34363a; border-color: #5ec269; }
        """)
        self.anim_btn.clicked.connect(self.open_anim_settings)
        row2.addWidget(self.anim_btn)

        self.bg_btn = QPushButton("背景")
        self.bg_btn.setFixedHeight(34)
        self.bg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bg_btn.setToolTip("自定义背景与卡片透明度")
        self.bg_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2c30; color: #e9e9ec;
                border: 1px solid #3a3c42; border-radius: 6px;
                padding: 0 16px;
            }
            QPushButton:hover { background-color: #34363a; border-color: #5ec269; }
        """)
        self.bg_btn.clicked.connect(self.open_background.emit)
        row2.addWidget(self.bg_btn)

        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        row2.addWidget(self.status_label)
        row2.addStretch()

        panel_box.addLayout(row2)
        layout.addWidget(panel)

        # ===== 结果列表 =====
        # 容器留出 12px 内边距：卡片 hover 时描边会画到边界上，
        # 不留缝的话那条绿边会被滚动区裁掉一半
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.container = QWidget()
        self.container.setStyleSheet("background: transparent;")
        self.results_layout = QVBoxLayout(self.container)
        self.results_layout.setSpacing(8)
        self.results_layout.setContentsMargins(12, 2, 12, 12)
        # 兜底按钮常驻在最后（隐藏状态），内容不够一屏时才显示
        self._more_btn = None
        self.results_layout.addStretch()
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, 1)

        # 结果卡片的入场动画调度器（见 ui/widgets/slide_in.py）。
        # 用哪一档是用户在「动效」里选的（预设定义见 ui/widgets/anim_prefs.py）
        _p = current_preset()
        self.stagger = StaggerReveal(
            self,
            interval=_p.interval,
            initial_offset=_p.offset,
            direction=_p.direction,
            style=_p.style,
            overshoot=_p.overshoot,
            bounce_ratio=_p.bounce_ratio,
        )

        # 分页状态
        self._query = ""            # 这一轮搜索的关键词（翻页时要原样带上）
        self._offset = 0            # 已经请求到第几条
        self._total = 0             # 服务端说一共有多少条
        self._loading = False       # 有没有请求在路上（防止重复触发）
        self._first_loaded = False  # 首页数据到了没（决定要不要追加）
        self._auto_fills = 0        # 连续自动补了几批（见 _on_range_changed）
        self._more_btn = None       # 内容不够一屏时出现的兜底按钮
        self._needs_load_more = False

        # 加载条放在滚动区**外面**：滚动的时候它一直可见
        self.loading_bar = LoadingBar()
        layout.addWidget(self.loading_bar)

        # 快滑到底就抓下一批；内容不够一屏时 rangeChanged 会兜住，
        # 免得"滚动条拉不动 → 永远触发不了加载"
        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_load_more)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._on_range_changed)

        # "内容够不够一屏"必须等布局稳定了再看（详见 detail_page.py 里的说明）
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(LAYOUT_SETTLE_MS)
        self._check_timer.timeout.connect(self._check_short_content)

        # 首次 → 加载推荐
        self.do_search()

    def resizeEvent(self, event):
        """窗口变大后重新判一次"要不要补"（可能又能多显示几条了）"""
        super().resizeEvent(event)
        self._auto_fills = 0
        self._check_timer.start()

    def _clear_results(self):
        """清空结果区，并叫停还没播完的入场动画

        ⚠️ 顺序不能反：先停动画再删控件。不然定时器会打到已经被
        deleteLater() 删掉的卡片上。
        """
        self.stagger.clear()
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self.url_to_cards.clear()
        # ⚠️ 兜底按钮现在**真的在布局里**了（见 _show_more_btn）。上面那个循环
        # 已经把它摘下来 deleteLater() 了，引用要一起清掉 ——
        # 否则下一次 _hide_more_btn() 会去碰一个已经被销毁的对象。
        self._more_btn = None
        self._hide_more_btn()

        # ⚠️ **撑开项每次清空后都要补回来**。
        # takeAt(0) 会把 spacer 也一起摘掉（spacer 没有 widget()，上面那个循环
        # 只是不 deleteLater 它，但它确实已经不在布局里了）。少了它之后，
        # 内容不满一屏时 QVBoxLayout 会把多出来的高度**平摊给每一行**：
        # 实测只有 2 条结果时每行被拉到 224px（卡片本身只有 100px），
        # 多出来的部分就表现成"两张卡片之间隔了一大段空白"。
        self.results_layout.addStretch()

        # 分页状态也要一起清，否则新一轮会从上一轮的 offset 接着请求
        self._offset = 0
        self._total = 0
        self._loading = False
        self._first_loaded = False
        self._auto_fills = 0
        self._check_timer.stop()

    def _add_result_widget(self, w):
        """把控件插到**末尾撑开项的前面**

        ⚠️ 不能用 addWidget：布局末尾常驻一个 addStretch()，addWidget 会把它
        追加到撑开项**后面**，卡片就跑到撑开项下面去了。
        （代码里原来那句注释"不加 addStretch()，加了之后卡片会跑到 stretch 下面去"
        说的就是这个现象 —— 但当时的结论是"那就不加 stretch"，结果踩了另一个坑：
        没有撑开项，多出来的高度会被平摊到每一行上。正确的做法是**保留撑开项、
        插在它前面**。）
        """
        self.results_layout.insertWidget(
            max(0, self.results_layout.count() - 1), w)

    # ---------- 类型标签页 ----------

    def _on_ptype_changed(self, key: str):
        """切换类型标签

        切了标签就**直接重搜**：点"搜光影"却什么都不发生会让人以为坏了。
        加载器筛选只对模组有意义（光影/资源包没有加载器概念），顺手禁掉。
        """
        label = dict(PROJECT_TYPES).get(key, key)
        self.panel_title.setText(f"搜索{label}")
        self.search_btn.setText(f"🔍 搜索{label}")
        self.loader_combo.setEnabled(key == "mod")
        self.do_search()

    def refresh_card_style(self):
        """卡片透明度改了以后，把已经建出来的卡片刷新一遍"""
        for row in self._results_rows():
            for card in row.findChildren(ModResultCard):
                card.refresh_style()

    def _results_rows(self):
        rows = []
        for i in range(self.results_layout.count()):
            w = self.results_layout.itemAt(i).widget()
            if w is not None:
                rows.append(w)
        return rows

    def open_anim_settings(self):
        """打开动效设置。选完立刻生效 —— 下一次加载就用新风格"""
        from ui.dialogs.anim_settings_dialog import AnimSettingsDialog
        AnimSettingsDialog(self).exec()
        p = current_preset()
        self.status_label.setText(f"动效已切换：{p.name}")

    def do_search(self):
        """开始一轮新搜索（用户点按钮 / 回车 / 重置都走这里）"""
        self._clear_results()

        query = self.search_input.text().strip()
        mc = self.mc_combo.currentText()
        loader = self.loader_combo.currentText() if self.loader_combo.isEnabled() else "全部"
        cat = self.cat_combo.currentData() or "全部"
        ptype = self.type_tabs.current_key() or "mod"

        # 这一轮的筛选条件记下来，翻页时原样复用 —— 用户翻页途中改了筛选框
        # 也不该影响已经发出去的那一轮
        self._query = query
        self._mc, self._loader = mc, loader
        self._cat, self._ptype = cat, ptype

        if not query:
            self.loading_bar.show_state("正在加载推荐…")
            self.status_label.setText("加载推荐...")
        else:
            self.loading_bar.show_state(f"正在搜索「{query}」…")
            self.status_label.setText(f"搜索: {query} ...")

        self.search_btn.setEnabled(False)
        self._request_page(0)

    # ---------- 取数 ----------

    def _request_page(self, offset: int):
        """请求第 offset 条开始的一批"""
        self._loading = True
        self.worker = SearchWorker(
            self._query, self._mc, self._loader, self._cat, self._ptype,
            limit=SEARCH_PAGE_SIZE, offset=offset,
        )
        self.worker.results.connect(self._on_results)
        self.worker.start()

    def _load_more(self):
        """抓下一批（滚动到底部时自动调）"""
        if self._loading or self._offset >= self._total:
            return
        self.loading_bar.show_state("正在加载更多…")
        self.status_label.setText(f"加载中… 已显示 {self._offset} / {self._total}")
        self._request_page(self._offset)

    def _at_bottom(self) -> bool:
        """是不是已经拖到底了（留 LOAD_MORE_THRESHOLD 像素余量）"""
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - LOAD_MORE_THRESHOLD

    def _maybe_load_more(self, *_):
        """滚动条动了 → **拖到底**才接着抓

        用户自己滚到底 = 明确想看更多，所以不设上限。
        唯一要防的是"内容不够一屏、滚动条拉不动"那种卡死（见 _check_short_content）。
        """
        self._needs_load_more = False
        if self._loading or self._offset >= self._total:
            return
        if self._at_bottom():
            self._auto_fills = 0     # 用户主动滚到底，允许后面重新自动补
            self._load_more()

    def _on_range_changed(self, _min: int, max_value: int):
        """内容高度变了 → 防抖一下再判断（rangeChanged 在布局期会连发很多次，
        而且那时候视口高度还没定，当场判断会误判"内容不够一屏"）"""
        self._check_timer.start()

    def _check_short_content(self):
        """布局稳定后：内容真的不够一屏吗？"""
        if self._loading or self._offset >= self._total:
            return
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() > bar.height():
            return
        if self._auto_fills >= MAX_AUTO_FILL:
            self._show_more_btn()
            return
        self._auto_fills += 1
        self._load_more()

    # ---------- 末尾的「加载更多」兜底按钮 ----------

    def _make_more_btn(self):
        from PyQt6.QtWidgets import QPushButton
        btn = QPushButton("加载更多")
        btn.setFixedHeight(32)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2c30; color: #e9e9ec;
                border: 1px solid #3a3c42; border-radius: 6px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #34363a; border-color: #5ec269; }
        """)
        btn.clicked.connect(self._load_more)
        return btn

    def _show_more_btn(self):
        if self._more_btn is None:
            self._more_btn = self._make_more_btn()
            # ⚠️ **必须真的加进布局**。原来这里只有 setVisible(True)：
            # 按钮既没有父控件、也没进任何布局，于是它会变成一个
            # **独立的顶层窗口**飘在桌面上（内容不够一屏时才会触发，所以不容易撞见）。
            self._add_result_widget(self._more_btn)
            self._needs_load_more = True
        self._more_btn.setVisible(True)

    def _hide_more_btn(self):
        self._needs_load_more = False
        if self._more_btn is not None:
            self._more_btn.setVisible(False)

    def _on_results(self, hits: list, total: int):
        self._loading = False
        self._total = int(total or 0)

        if not self._first_loaded:
            # ---- 首页数据 ----
            self.search_btn.setEnabled(True)
            if not hits:
                self.loading_bar.hide_now()
                lbl = QLabel("没有找到结果")
                lbl.setStyleSheet("color: #888; padding: 40px;")
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._add_result_widget(lbl)
                self.status_label.setText("0 个结果")
                return
            self._first_loaded = True
            # ⚠️ 加载条的收起和状态文字都放到"卡片造完"之后再做：
            # 建造是按帧分批的，现在就收起的话会出现"加载条没了、卡片还没出来"
            # 的空窗期，观感比原来更差。
            self._append_hits(hits, done=self._after_first_page)
            return

        # ---- 后面几页 ----
        if hits:
            self._append_hits(hits, done=self._after_more_page)
        else:
            self.loading_bar.hide_now()
            self._refresh_status()

    # ---------- 卡片造完之后的收尾 ----------

    def _after_first_page(self):
        self.loading_bar.hide_now(f"已加载 {self._offset} 个结果")
        self._refresh_status()

    def _after_more_page(self):
        self.loading_bar.hide_now()
        self._refresh_status()

    def _refresh_status(self):
        if self._total > self._offset:
            self.status_label.setText(f"显示 {self._offset} / 共 {self._total} 个结果")
        else:
            self.status_label.setText(f"✓ 共 {self._offset} 个结果")

    def _append_hits(self, hits: list, done=None):
        """把一批结果插到列表末尾（**按帧预算分批建造**，见 FRAME_BUDGET_MS）

        done 是"全部卡片造完"的回调 —— 状态文字和加载条收起都挂在它上面，
        免得出现"说加载好了但屏幕上还空着"。

        **插在末尾撑开项的前面**（见 _add_result_widget）：布局末尾常驻一个
        addStretch() 用来吸收"内容不满一屏"时多出来的高度，所以不能用 addWidget
        ——那会把卡片塞到撑开项下面去。

        以前是 for 循环一口气造完 —— 24 张要 40ms，界面在这期间是冻住的。
        现在每帧只干 FRAME_BUDGET_MS，干不完的挂到下一帧。
        """
        def build_one(hit):
            card = ModResultCard(hit)
            card.clicked.connect(self._on_card_clicked)
            p = current_preset()
            row = SlideInRow(
                card, duration=p.duration, offset=p.offset,
                direction=p.direction, style=p.style,
                overshoot=p.overshoot, bounce_ratio=p.bounce_ratio,
                ease=p.ease,
            )
            icon_url = hit.get("icon_url", "")
            if icon_url:
                self.url_to_cards.setdefault(icon_url, []).append(card)
            # ⚠️ **必须交给 stagger 排队**。
            # 这里曾经图省事直接 new SlideInRow 就 addWidget 了，结果行没进调度器，
            # stagger.start() 什么也不做 —— 卡片就永远停在"起手位置"
            # （偏移 = offset，被推出可视区），表现成"列表是空的 / 动画不动"。
            # add() 会 setParent(None)，所以要把**它的返回值**加进布局。
            return self.stagger.add(row)

        def iterate(index: int) -> bool:
            """返回 True 表示还没做完（已挂到下一帧）

            ⚠️ **建造和入布局要放在同一个循环里**：只把"造控件"分批、
            最后再一次性 addWidget 的话，那一次插入仍会占用一帧（实测 25ms），
            卡顿只是从"造的时候"挪到"插的时候"，等于没修。
            """
            start = time.monotonic()
            made = 0
            while index < len(hits) and made < FRAME_MAX_ITEMS:
                row = build_one(hits[index])
                self._add_result_widget(row)
                index += 1
                made += 1
                # 到预算就交还控制权，让 Qt 先把这一帧画出来
                if (time.monotonic() - start) * 1000 >= FRAME_BUDGET_MS:
                    break
            if index < len(hits):
                QTimer.singleShot(0, lambda: iterate(index))
                return True
            self._offset += len(hits)
            if self._offset >= self._total:
                self._hide_more_btn()   # 没有更多了，按钮收起来
            self.stagger.start()
            self._load_icons_async()
            if done is not None:
                done()
            return False

        iterate(0)

    def _load_icons_async(self):
        for url in list(self.url_to_cards.keys()):
            if url in self.icon_memory_cache:
                data = self.icon_memory_cache[url]
                for card in self.url_to_cards[url]:
                    card.set_icon(data)
                continue

            cached = cache.load(url)
            if cached:
                self.icon_memory_cache[url] = cached
                for card in self.url_to_cards[url]:
                    card.set_icon(cached)
                continue

            task = IconTask(url)
            task.signals.finished.connect(self._on_icon_ready)
            task.signals.failed.connect(self._on_icon_failed)
            self.icon_pool.start(task)

    def _on_icon_ready(self, url: str, data: bytes):
        self.icon_memory_cache[url] = data
        for card in self.url_to_cards.get(url, []):
            card.set_icon(data)

    def _on_icon_failed(self, url: str):
        pass

    def _on_card_clicked(self, hit: dict):
        # 通知主窗口：切到详情页
        self.open_detail.emit(hit)

    def do_reset(self):
        self.search_input.clear()
        self.mc_combo.setCurrentIndex(0)
        self.loader_combo.setCurrentIndex(0)
        self.cat_combo.setCurrentIndex(0)
        # 类型标签回到第一个（模组）。notify=False 是为了避免它自己触发一次搜索，
        # 下面统一再搜一次就够
        self.type_tabs.set_current(self.type_tabs.keys()[0], notify=False)
        self._on_ptype_changed(self.type_tabs.current_key())
