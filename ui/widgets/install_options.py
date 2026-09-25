"""安装选项（下载页第二页的内容）

    [←]  ▣ [ 26.3                    ]        ← 版本名可改（默认 = 版本 id）
    ──────────────────────────────────────
    ▸ Forge      最新版 66.0.3                ← core/loaders.loader_rows()
    ▸ NeoForge   最新版 21.1.72                  **一进来全是收起的**
    ▸ Fabric     最新版 0.19.5
    ▸ OptiFine   最新版 HD_U_I5
    ──────────────────────────────────────
        还没选加载器 —— 直接点「开始下载」只会装原版
            [ ⤓ 开始下载 ]

设计要点：

- **只做控件本身**，由页面决定什么时候显示它（这样它能脱离页面单独测）
- 加载器那几行复用 `CollapsibleGroup`：标题是加载器名，展开后是版本列表，
  行图标由 `version_pixmap` 按 `loader` 字段自动挑（forge.png / fabric.png…）
- 三种状态照实说（见 core/loaders.py）：能装 → 最新版 + 版本列表；
  我们没做 → 「还没做」；拉不到 → 「拉不到」+ 原因。**后两者绝不混**
- 「开始下载」发 `install_requested`，参数里带上**用户改过的名字**、
  选中的加载器、以及**安装器要的额外数据**（`loader_extra`：OptiFine 的
  type/patch，见 `loader_extra()`）；真正安装是 `DownloadPage._install_worker`
  的事 —— Forge / NeoForge / OptiFine 那几家下完还要**跑官方安装器**
  （`core/loader_setup.py`），跑的时候进度走下载窗口下半截那个控制台

加载器是**可选的、而且不预选**（用户 2026-09 定的："让用户自己选择，
不展开任何选项"）：

- 一进来五组**全是收起的**，也没有任何一组被"默认选中"
- **一个都不点 = 只装原版**（`selected_loader()` 返回 `("", "")`）。
  以前是"什么都不点就装第一个能装的（Forge）"—— 那是替用户做主，
  而且界面上看不出来。现在按钮上方会明说
  「还没选加载器 —— 直接点「开始下载」只会装原版」（`_refresh_hint()`）
- 想装加载器就自己展开一家、点里面一个版本

加载器互斥（用户 2026-09 定的，跟 PCL 一个手感）：

- 同时**只能选一个**加载器。点某一行 = 选中它，其他几组**灰掉 + 收起来**、
  标题**点不动**、副标题写「与 Fabric 不兼容」
- 要换只能先点选中那组右边的「**取消选择**」（`clear_selection()`）——
  这是刻意的（用户 2026-09 明确要求"选了之后就点不动其他的了"）。
  ⚠️ 别把退路做在灰卡片上：那样用户想"展开看看"就会**静悄悄把加载器换掉**
- 选中的那组描边亮起、副标题写「已选 0.19.5（共 253 个）」、里面那一行标出来，
  **顶部的图标也从草方块换成对应加载器的图**
- 版本名自动填成 `26.3-Fabric 0.19.5`（`core/loaders.version_folder_name()`）；
  **用户手改过就不再自动填**（别冲掉他写的）
- **选了 Fabric** 才会多出一组「Fabric API」（Modrinth 上的那个 mod）：
  默认装匹配当前游戏版本的最新版，可以改选别版、也可以点「不装」。
  它跟加载器**不是互斥关系**，所以它不在 `self._groups` 里
  （进去的话选中它会把 Fabric 锁掉）。装的时候走**版本隔离**的
  `versions/<版本名>/mods/`
"""

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget
)

from core.i18n import tr
from core.loaders import LOADER_NAMES, version_folder_name
from ui.icons import screen_dpr, version_pixmap
from ui.translatable import TranslatableWidget
from ui.widgets.collapsible_group import CollapsibleGroup
from ui.widgets.loading_bar import LoadingBar

ICON_SIZE = 32
NAME_MAX = 64


class InstallOptions(TranslatableWidget):
    """选好远程版本之后的那一页

    信号：
        back_requested()        点了「← 返回」
        install_requested(dict) 点了「开始下载」
                                {version_id, name, loader_key, loader_version}
    """

    back_requested = pyqtSignal()
    install_requested = pyqtSignal(dict)
    # 选了 Fabric、但手上还没有 Fabric API 的候选版本 → 页面据此去拉
    # （参数是游戏版本）。**不在这里自己联网**：这一层是纯界面，
    # 而且"只有选了 Fabric 才值得拉"这件事得让页面知道
    api_wanted = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._entry = None
        self._rows = []
        self._dpr = screen_dpr(self)
        self._groups = {}           # key → CollapsibleGroup（互斥锁定要用）
        # 用户**显式选过**的加载器（`(key, version)`）；没选过是 None。
        # ⚠️ 跟 `selected_loader()` 的区别：那个问的是"当前该用哪个"（永远有答案），
        # 这个问的是"用户点过没有"（决定要不要锁其他组、要不要自动填名字）。
        self._picked = None
        # 用户选的是那个加载器版本列表里的**第几个**（标"已选"那一行要用）
        self._picked_index = -1
        # 用户手改过名字没有 —— 改过就**不再自动填**（别冲掉他写的）
        self.name_edited = False

        # Fabric API（Modrinth 上的那个 mod）。**只有选了 Fabric 才有意义** ——
        # 别的加载器装它纯属添乱（见 `_refresh_api_group`）
        self._api_versions = []      # `core/loader_install.fabric_api_versions()` 的结果
        self._api_group = None       # 那个折叠组（换一批加载器行时重建）
        self._api_picked = None      # 选中的是第几个（None = 还没定 → 用最新的）
        self._api_off = False        # 用户点了「不装 Fabric API」
        self._api_asked_mc = ""      # 已经为哪个游戏版本要过数据了（别重复要）

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        outer.addWidget(self._make_top())
        # ⚠️ 接在 `_make_top()` **之后**：`name_edit` 是在那里面建的。
        # 用户手改过名字就标记一下，之后不再自动填（别冲掉他写的）。
        self.name_edit.editingFinished.connect(self._on_name_edited)
        # 「将安装到：…」—— 让用户在点「开始下载」**之前**就能看见目标目录
        self.target_hint = self._make_target_hint()
        outer.addWidget(self.target_hint)
        # ⚠️ 加载条必须挂在 **_loader_box 外面**：_clear_rows() 会把 box 里的
        # 每个控件都 deleteLater 掉，放进去的话每次 set_rows() 都会把它删掉，
        # 之后再用就是 "wrapped C/C++ object has been deleted"。
        self.loading = LoadingBar()
        outer.addWidget(self.loading)
        outer.addWidget(self._make_loader_area(), 1)

        # 「开始下载」按了之后在这儿回话（安装编排还没做时就说清楚）
        self.status = QLabel()
        self.status.setObjectName("HintText")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.hide()
        outer.addWidget(self.status)

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.install_btn = self.button("开始下载", "PrimaryButton")
        self.install_btn.setMinimumWidth(200)
        self.install_btn.clicked.connect(self._on_install)
        bottom.addWidget(self.install_btn)
        bottom.addStretch()
        outer.addLayout(bottom)

    # ---------- 顶部：返回 + 图标 + 可改的名字 ----------

    def _make_top(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        box = QHBoxLayout(card)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(10)

        self.back_btn = self.button("←", "LinkButton")
        self.back_btn.setFixedWidth(40)
        self.back_btn.clicked.connect(self.back_requested.emit)
        box.addWidget(self.back_btn)

        self.icon = QLabel()
        self.icon.setObjectName("BadgeIcon")
        self.icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        box.addWidget(self.icon)

        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(NAME_MAX)
        self.name_edit.setPlaceholderText(tr("版本名（可以自己改）"))
        box.addWidget(self.name_edit, 1)
        return card

    def _make_target_hint(self) -> QLabel:
        """「将安装到：…」那一行（见 set_target_dir 的说明）"""
        hint = QLabel()
        hint.setObjectName("HintText")
        hint.setWordWrap(True)
        hint.setVisible(False)
        return hint

    # ---------- 加载器那几行 ----------

    def _make_loader_area(self) -> QScrollArea:
        holder = QWidget()
        self._loader_box = QVBoxLayout(holder)
        self._loader_box.setContentsMargins(0, 0, 8, 0)
        self._loader_box.setSpacing(8)
        self._loader_box.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(holder)
        return scroll

    # ---------- 数据 ----------

    def set_version(self, entry: dict):
        """换一个远程版本（页面点了一行之后调它）"""
        self._entry = dict(entry or {})
        vid = self._entry.get("id", "")
        self.name_edit.setText(vid)          # 默认就是版本 id，用户可以改
        # 换了远程版本 → 之前"手改过名字"作废：新版本的默认名还是版本 id，
        # 用户选加载器时该自动填（不重置的话他会拿到上一个版本的旧名字）
        self.name_edited = False
        # 换了远程版本 → "为哪个游戏版本要过 Fabric API 数据"也要跟着换，
        # ⚠️ 手上的**旧数据也要扔掉**：它是按上一个游戏版本筛出来的，
        # 留着的话换个游戏版本再选 Fabric 会装上一个不匹配的 Fabric API
        self._api_asked_mc = ""
        self._api_versions = []
        self._api_picked = None
        self._api_off = False
        self.set_rows([])                    # 换版本先把旧的加载器清掉
        # ⚠️ 图标要放在 `set_rows([])` **之后**刷：那一步会把 `_picked` 清掉，
        # 顺序反了会用上一个版本选中的加载器画图标
        self._refresh_icon()

    def set_rows(self, rows):
        """铺加载器那几行（`core/loaders.loader_rows()` 的结果）

        空列表 = 还在拉 → 显示加载条（不确定进度条来回跑），
        拉到东西 = 收起来。失败那种情况由 set_status() 负责收
        （页面是先 set_rows([]) 再 set_status(错误)）。
        """
        self._rows = list(rows or [])
        self._clear_rows()
        # ⚠️ 清"选中"这件事必须放在**空列表早退之前**：换远程版本时走的是
        # `set_rows([])` 那条路，早退之后再清就等于没清 —— 表现是新版本上
        # 还锁着上一个版本的加载器选择（测试抓到的：换版本后 picked 还是
        # ('fabric', '0.19.5')）。
        self._picked = None
        self._picked_index = -1

        if not self._rows:
            self.loading.show_state(tr("正在获取模组加载器…"))
            return

        self.loading.hide_now()

        # ⚠️ **一进来一组都不展开**（用户 2026-09 定的）。
        # 原来是把第一个能装的（Forge）自动展开 —— 那等于替用户做主：
        # 他什么都没点就按「开始下载」，装出来的是 Forge。现在全都收着，
        # 想让用户自己点一个；一个都不点就装纯原版（见 `selected_loader`）。
        for row in self._rows:
            # 一行 = **一个带框的折叠组**，状态那句话说在框里面（副标题）。
            # ⚠️ 以前是"框外一行状态文字 + 框"两行，看着散（用户 2026-09 提的）；
            # 现在状态进框、右对齐，一组就是一个卡片。
            key = row.get("key", "")
            group = CollapsibleGroup(row.get("name", key))
            group.set_note(self._status_text(row))
            group.set_items([self._version_entry(row, v)
                             for v in row.get("versions", [])])
            # 用户点里面某个版本 → 就算"选了这个加载器"，其他组要锁掉
            group.item_activated.connect(
                lambda entry, idx, k=key: self.on_item_clicked(k, entry, idx))
            # 选中那组标题右边的「取消选择」（锁死之后唯一"想换一个"的入口）
            group.action_triggered.connect(self.clear_selection)
            # 给测试/以后取用：状态文字现在在组自己的副标题上
            group.status_label = group.header_note
            self._groups[key] = group
            self._loader_box.insertWidget(self._loader_box.count() - 1, group)

        # Fabric API 那一组（只有选了 Fabric 才会显示出来）
        self._make_api_group()
        # 铺完一遍：按当前"选中"状态刷一次锁/副标题/标记
        self._apply_selection()

    @staticmethod
    def _version_entry(row: dict, version: dict) -> dict:
        """把加载器的一个版本包成 `version_pixmap` 认识的样子

        带上 `loader` 字段，图标就会自动挑 forge.png / fabric.png（见 version_icon_name）
        """
        return {
            "id": version.get("version", ""),
            "display_name": version.get("version", ""),
            "type": "release",
            "loader": row.get("key", ""),
            "release_time": version.get("modified", ""),
        }

    def _status_text(self, row: dict) -> str:
        """这一行右侧那句状态（四种情况都要说清楚）

        ⚠️ 顺序很重要：**"已经选了别的加载器"要盖过"有几个版本"** ——
        否则选中 Forge 之后，NeoForge 那行还写着"最新版 47.1.84（共 60 个）"，
        看着像还能选（用户 2026-09 要的就是这个效果：不可选要说出来）。
        """
        key = row.get("key", "")
        if self._picked and self._picked[0] != key:
            picked_name = LOADER_NAMES.get(self._picked[0], self._picked[0])
            return tr("与 {name} 不兼容", name=picked_name)

        state = row.get("state")
        if state == "ok":
            if row.get("count"):
                return tr("最新版 {v}（共 {n} 个）",
                          v=row.get("latest", ""), n=row.get("count", 0))
            return tr("这个版本没有可用的 {name}", name=row.get("name", ""))
        if state == "error":
            # ⚠️ 拉不到 ≠ 还没做：把原因带上，用户知道重试可能就好了
            return tr("拉不到版本列表（网络问题），稍后重试")
        return tr("还没做")

    # ---------- 加载器互斥（选了一个，其他锁死）----------

    def on_item_clicked(self, key: str, entry, index: int = -1):
        """用户点了某个加载器里的一个版本 → 选中它、锁掉其他

        ⚠️ 这个方法名是**习惯**（"on_" 开头，跟页面里其它回调一致）——
        信号其实是在 `set_rows()` 里显式 connect 的，没有谁在扫 `dir(page)`。
        ⚠️ 锁掉之后另外几组是**真的点不动**（标题禁用 + 组收起来 + 这里再挡一层），
        想换只能先 `clear_selection()`。
        """
        if not key or not isinstance(entry, dict):
            return
        # ⚠️ 锁着的那几组：它们的行**还在**（只是折叠起来看不见）。界面本来
        # 就点不到（标题按钮禁用了、组也收起来了），这一句是兜底的不变式 ——
        # 灰着的组永远改不了当前选择，想换只能先 `clear_selection()`。
        if self._picked and self._picked[0] != key:
            return
        version = str(entry.get("id") or entry.get("display_name") or "")
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = -1
        if self._picked == (key, version) and self._picked_index == index:
            return
        self._picked = (key, version)
        self._picked_index = index
        self._apply_selection()
        self._maybe_autofill_name()
        self._refresh_icon()

    def clear_selection(self):
        """取消当前选择（选中那组右边的「取消选择」按钮）

        ⚠️ **这是锁死之后唯一的退路**（用户 2026-09 定的）：灰掉的那几组标题
        是禁用的、点不动，"想换一个"必须走这里。别把这个入口去掉，不然
        选错一次只能返回上一页重进。
        名字是我们自动填的话就退回版本 id（用户手改过的不动）。
        """
        if not self._picked:
            return
        self._picked = None
        self._picked_index = -1
        # ⚠️ Fabric API 的选择是"选了 Fabric"的下级选择：加载器都取消了，
        # 它也该回到默认（下次再选 Fabric 时重新取最新那版），
        # 不然用户会莫名其妙发现"怎么还是上次那个旧 API"
        self._api_picked = None
        self._api_off = False
        self._apply_selection()
        if not self.name_edited:
            self.name_edit.setText(str((self._entry or {}).get("id") or ""))
        self._refresh_icon()

    def _refresh_icon(self):
        """顶部那个图标跟着**选中的加载器**变（没选就是游戏版本自己那张图）

        ⚠️ 用户 2026-09 提的：选了 Fabric 之后名字已经写成
        `1.21.4-Fabric 0.19.5`、目标目录也跟着变了，唯独左上角还画着草方块 ——
        一眼扫过去最先看到的那个图标跟后面全对不上。
        （真装完之后的版本列表那边是靠**扫描目录自动识别**的，所以这里只是
        "预览"，不落盘、也不影响安装结果。）
        """
        if self._picked and self._picked[0]:
            key, version = self._picked
            # 带 `loader` 字段，`version_icon_name()` 就会挑 fabric.png / forge.png
            entry = {"id": version, "display_name": version,
                     "type": "release", "loader": key}
        else:
            entry = self._entry or {}
        pix = version_pixmap(entry, ICON_SIZE, self._dpr)
        if pix is not None:
            self.icon.setPixmap(pix)
        else:
            self.icon.clear()

    def picked_loader(self):
        """用户**显式点过**的加载器 `(key, version)`；没点过就是 `(None, "")`

        ⚠️ 跟 `selected_loader()` 的区别：那个是"没选的时候该用哪个"（兜底），
        这个是"用户自己挑的"。`_on_install()` 优先用它。
        """
        if not self._picked:
            return None, ""
        return self._picked

    def _apply_selection(self):
        """按 `self._picked` 把各组锁/解锁、标出选中的那一行、刷新状态文字

        - 选中那组：解锁、展开、描边亮起来、里面选中的那行标出来，
          标题右边多一个「取消选择」（**唯一**的改选入口）
        - 其他组：`set_locked(True)`（灰掉、收起来、标题点不动、
          副标题写"与 X 不兼容"、tooltip 说清要换该怎么办）
        """
        picked_key = self._picked[0] if self._picked else ""
        picked_name = LOADER_NAMES.get(picked_key, picked_key) if picked_key else ""
        for key, group in self._groups.items():
            picked_here = key == picked_key
            locked = bool(picked_key) and not picked_here
            group.set_locked(locked, note=self._note_for(key),
                             tooltip=(tr("已经选了 {name}，要换就点选中那组的「取消选择」", name=picked_name)
                                      if locked else ""))
            group.set_selected(picked_here)
            group.set_action(tr("取消选择") if picked_here else "")
            if picked_here:
                # ⚠️ 顺序不能反：折叠着的组还没建行，得先展开再标"已选"
                # （反过来的话 `set_picked_index()` 找不到任何行，标记丢失）
                group.set_expanded(True)
            # ⚠️ 只标记**这一组**里的行：`_picked_index` 是"这一组的第几个"，
            # 不是全局的，标到别的组上会点亮不相干的一行
            group.set_picked_index(self._picked_index if picked_here else -1)
        # Fabric API 那一组的显隐跟着"选的是不是 Fabric"走
        self._maybe_want_api()
        self._refresh_api_group()
        self._refresh_hint()

    def _refresh_hint(self):
        """按钮上方那行提示：**没选加载器时说清楚"这次只装原版"**

        ⚠️ 为什么要有它：一组都不默认展开、也不给默认加载器之后，"什么都不点"
        会装出**纯原版** —— 不说的话用户点完才发现没装加载器（反过来，以前
        什么都不点会悄悄装 Forge，同样说不清）。选了加载器就收起来：
        那组的副标题已经写着「已选 0.19.5（共 253 个）」了。
        """
        if self._picked or not self._groups:
            self.set_status("")
            return
        self.set_status(tr("还没选加载器 —— 直接点「开始下载」只会装原版"))

    def _maybe_want_api(self):
        """选了 Fabric、但手上还没有 Fabric API 的数据 → 向页面要一次

        ⚠️ 这件事**不能**放在 `_refresh_api_group()` 里：那一组是"有数据才建"的，
        没有数据时它连着 return 两次，请求根本发不出去（测试抓到过）。
        """
        if not (self._picked and self._picked[0] == "fabric"):
            return
        if self._api_versions:
            return
        mc = str((self._entry or {}).get("id") or "")
        if mc and self._api_asked_mc != mc:
            self._api_asked_mc = mc
            self.api_wanted.emit(mc)

    def _note_for(self, key: str) -> str:
        """某个组现在该显示的状态文字"""
        row = next((r for r in self._rows if r.get("key") == key), None)
        if row is None:
            return ""
        if self._picked and self._picked[0] == key and self._picked[1]:
            count = row.get("count", 0)
            if count:
                return tr("已选 {v}（共 {n} 个）", v=self._picked[1], n=count)
            return tr("已选 {v}", v=self._picked[1])
        return self._status_text(row)

    # ---------- Fabric API（选了 Fabric 才出现）----------

    def set_api_versions(self, versions):
        """页面上层把 Fabric API 的候选版本递进来（Modrinth，已按游戏版本筛过）

        空列表 = 没有 / 拉不到 → 那一组**不出现**（不占位也不说废话）。
        """
        self._api_versions = [v for v in (versions or []) if isinstance(v, dict)]
        self._api_picked = None
        self._api_off = False
        self._make_api_group()
        self._refresh_api_group()

    def _make_api_group(self):
        """建「Fabric API」那一组（**不在** `self._groups` 里）

        ⚠️ 它不能进 `self._groups`：那个表是"加载器互斥"用的，进去的话
        选中 Fabric API 会把 Fabric 自己锁掉 —— 它俩不是互斥关系，
        是"配套"。
        """
        if self._api_group is not None or not self._api_versions:
            return
        group = CollapsibleGroup(tr("Fabric API"))
        group.set_items([self._api_entry(v) for v in self._api_versions])
        group.item_activated.connect(
            lambda _e, idx: self.on_api_clicked(idx))
        group.action_triggered.connect(self.toggle_api)
        group.set_expanded(True)          # 就一两条，展开着让用户看见装的是哪版
        self._api_group = group
        self._loader_box.insertWidget(self._loader_box.count() - 1, group)

    @staticmethod
    def _api_entry(version: dict) -> dict:
        """Modrinth 的一个版本 → `version_pixmap` 认识的样子

        ⚠️ **不要**给 `loader` 字段：给了就会被认成"加载器版本行"，
        跟上面那几组画一样的图标，看不出它是 mod。用 `kind="pack"` 走箱子图标。
        """
        num = version.get("version_number", "")
        return {
            "id": num, "display_name": num, "type": "release",
            "kind": "pack", "loader": "",
            "release_time": version.get("date_published", ""),
        }

    def on_api_clicked(self, index: int):
        """点了 Fabric API 里的一个版本 → 改成装那一个"""
        try:
            index = int(index)
        except (TypeError, ValueError):
            return
        if not (0 <= index < len(self._api_versions)):
            return
        if self._api_picked == index and not self._api_off:
            return
        self._api_picked = index
        self._api_off = False
        self._refresh_api_group()

    def toggle_api(self):
        """「不装 / 装上」Fabric API（那个小按钮）"""
        if not self._api_versions:
            return
        self._api_off = not self._api_off
        self._refresh_api_group()

    def _refresh_api_group(self):
        """Fabric API 那一组的显隐 + 副标题 + 选中行

        ⚠️ 只有**选了 Fabric** 才显示：别的加载器装 Fabric API 没有任何意义
        （它是 Fabric 的 API 实现），显示了只会让人以为"装什么都行"。
        """
        group = self._api_group
        if group is None:
            return
        fabric = bool(self._picked) and self._picked[0] == "fabric"
        if not fabric or not self._api_versions:
            group.setVisible(False)
            if not fabric:
                # 换了别的加载器 → 之前"不装"的选择作废（下次选回 Fabric 要重新生效）
                self._api_off = False
            return

        if self._api_off:
            group.set_note(tr("不装"))
            group.set_action(tr("装上"), tooltip=tr("把这个也一起装上"))
            group.set_picked_index(-1)
        else:
            if self._api_picked is None:
                self._api_picked = 0          # 默认最新那个
            version = self._api_versions[self._api_picked]
            group.set_note(tr("会一起装 {v}",
                              v=version.get("version_number", "")))
            group.set_action(tr("不装"), tooltip=tr("这次不装 Fabric API"))
            group.set_picked_index(self._api_picked)
        group.set_selected(not self._api_off)
        group.set_locked(False)
        group.set_expanded(True)
        group.setVisible(True)

    def api_version(self):
        """当前要一起装的 Fabric API 版本（dict）；不装 / 选了别的加载器 → None"""
        if self._api_off or not self._api_versions:
            return None
        if not (self._picked and self._picked[0] == "fabric"):
            return None
        index = 0 if self._api_picked is None else self._api_picked
        if not (0 <= index < len(self._api_versions)):
            return None
        return self._api_versions[index]

    # ---------- 文件名自动填 ----------

    def _on_name_edited(self):
        """用户手改过名字 → 以后不再自动填（别冲掉他写的）"""
        if self.name_edit.text().strip():
            self.name_edited = True

    def _maybe_autofill_name(self):
        """按选中的加载器自动填版本名：`26.3-Fabric 0.19.5`

        ⚠️ **用户改过就不碰**（`name_edited`）—— 他手打的名字比我们猜的准。
        ⚠️ 格式由 `core/loaders.version_folder_name()` 定，**必须跟
        `core/mc_dir.py` 的 `match_version()` 对得上**：这个名字就是安装目录名，
        也是那边反查"mod 该装到哪个版本"的依据。
        """
        if not self._picked or self.name_edited:
            return
        mc = str((self._entry or {}).get("id") or "")
        key, version = self._picked
        name = version_folder_name(mc, key, version)
        if name:
            self.name_edit.setText(name)

    def _clear_rows(self):
        for i in reversed(range(self._loader_box.count())):
            widget = self._loader_box.itemAt(i).widget()
            if widget is not None:
                self._loader_box.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
        # ⚠️ 分组表也要清：留着的话 `_apply_selection()` 会去碰已经
        # deleteLater 掉的控件（"wrapped C/C++ object has been deleted"）
        self._groups = {}
        self._api_group = None

    # ---------- 动作 ----------

    def version_name(self) -> str:
        """用户填的版本名（空的话退回版本 id —— 不该出现空目录名）"""
        text = self.name_edit.text().strip()
        return text or (self._entry or {}).get("id", "")

    def selected_loader(self):
        """**兜底**用哪个加载器 —— 现在是「**不装**」，返回 `("", "")`

        ⚠️ 这里原来返回的是"第一个能装的那家 + 它的最新版"（也就是 Forge）。
        那等于**替用户做主**：他什么都没点、直接按「开始下载」，装出来的是
        Forge —— 而界面上没有任何地方说这件事（用户 2026-09 定的：
        "让用户自己选择，不展开任何选项"）。

        所以现在：**一个都不点 = 只装原版**。想装加载器就自己展开一家、点一个
        版本（`picked_loader()` 会拿到）。按钮上方那行提示也会明说
        「还没选加载器 —— 直接点「开始下载」只会装原版」（见 `_refresh_hint`）。
        """
        return "", ""

    def loader_extra(self, key: str, version: str) -> dict:
        """传给安装器的额外数据（现在只有 OptiFine 用得上：`type` / `patch`）

        OptiFine 的安装器下载地址是 `/optifine/<mc>/<type>/<patch>`，
        这两段只能从版本表的原始数据里拿（见 `core/loader_setup.installer_url`）。

        ⚠️ 别把整个版本 dict 塞进去：那里面还有 `filename`、`forge` 这些
        跟安装器没关系的东西，将来谁往里加字段就可能悄悄混进请求参数里。
        """
        extra = {}
        if (key or "").lower() != "optifine":
            return extra
        for row in self._rows:
            if row.get("key") != key:
                continue
            for item in row.get("versions") or []:
                if (isinstance(item, dict)
                        and str(item.get("version") or "") == str(version or "")):
                    extra["type"] = str(item.get("type") or "")
                    extra["patch"] = str(item.get("patch") or "")
                    return extra
        return extra

    def set_target_dir(self, path):
        """告诉用户"点了开始下载，文件会装到哪"

        ⚠️ 这个必须**提前**显示，不能等下载窗口里那个"保存到"（那是开始之后）。
        用户 2026-09 报过"怎么装到启动器旁边那个目录去了" ——
        当时界面上没有任何地方能在点之前看出目标目录，只能靠事后翻配置。
        目录不存在时也照实说（引擎会把它建出来）。
        """
        if not path:
            self.target_hint.setText("")
            self.target_hint.setVisible(False)
            return
        p = Path(path)
        try:
            exists = p.is_dir()
        except OSError:
            exists = False
        if exists:
            self.target_hint.setText(tr("将安装到：{path}", path=p))
        else:
            self.target_hint.setText(tr(
                "将安装到：{path}（这个目录还不存在，会新建）", path=p))
        self.target_hint.setVisible(True)

    def set_status(self, text: str):
        """在按钮上方回一句话（空字符串 = 收起来）"""
        self.status.setText(text or "")
        self.status.setVisible(bool(text))
        # 只要有话说，就说明"等加载"这件事已经结束了（拉失败、或者出结果了），
        # 加载条该收起来 —— 不收的话会出现"转着圈 + 下面写着失败"的怪状态
        if text:
            self.loading.hide_now()

    def _on_install(self):
        entry = self._entry or {}
        # 用户点过就用他点的那个版本；**没点过就是不装加载器**（纯原版）——
        # 兜底不再替用户挑一家（见 `selected_loader`）
        loader_key, loader_version = self.picked_loader()
        if not loader_key:
            loader_key, loader_version = self.selected_loader()
        self.install_requested.emit({
            "version_id": entry.get("id", ""),
            "name": self.version_name(),
            "loader_key": loader_key,
            "loader_version": loader_version,
            # 安装器要的额外数据（OptiFine 的 type/patch；别家是空 dict）
            "loader_extra": self.loader_extra(loader_key, loader_version),
            # 要一起装的 Fabric API（没选 Fabric / 点了「不装」就是 None）
            "api_version": self.api_version(),
        })

    # ---------- 语言 ----------

    def retranslate(self):
        super().retranslate()
        # 状态那几句是生成的，得重铺一遍
        if self._rows:
            self.set_rows(self._rows)
