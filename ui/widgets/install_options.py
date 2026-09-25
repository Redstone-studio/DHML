"""安装选项（下载页第二页的内容）

    [←]  ▣ [ 26.3                    ]        ← 版本名可改（默认 = 版本 id）
    ──────────────────────────────────────
    ▸ Forge      最新版 66.0.3                ← core/loaders.loader_rows()
    ▸ NeoForge   还没做
    ▸ Fabric     最新版 0.19.5
    ▸ OptiFine   还没做
    ──────────────────────────────────────
            [ ⤓ 开始下载 ]

设计要点：

- **只做控件本身**，由页面决定什么时候显示它（这样它能脱离页面单独测）
- 加载器那几行复用 `CollapsibleGroup`：标题是加载器名，展开后是版本列表，
  行图标由 `version_pixmap` 按 `loader` 字段自动挑（forge.png / fabric.png…）
- 三种状态照实说（见 core/loaders.py）：能装 → 最新版 + 版本列表；
  我们没做 → 「还没做」；拉不到 → 「拉不到」+ 原因。**后两者绝不混**
- 「开始下载」发 `install_requested`，参数里带上**用户改过的名字**；
  真正安装是下一步（E）的事

加载器互斥（用户 2026-09 定的，跟 PCL 一个手感）：

- 同时**只能选一个**加载器。点某一行 = 选中它，其他几组**灰掉 + 收起来**，
  副标题写「与 Fabric 不兼容」
- 但**锁死 ≠ 死路**：点灰掉那组的标题 = 改选它（取它的最新版），
  不用退回上一页重来
- 选中的那组描边亮起、副标题写「已选 0.19.5（共 253 个）」、里面那一行标出来
- 版本名自动填成 `26.3-Fabric 0.19.5`（`core/loaders.version_folder_name()`）；
  **用户手改过就不再自动填**（别冲掉他写的）
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
        pix = version_pixmap(self._entry, ICON_SIZE, self._dpr)
        self.icon.setPixmap(pix) if pix is not None else self.icon.clear()
        self.set_rows([])                    # 换版本先把旧的加载器清掉

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

        # 一进来只展开**第一个**能装的加载器（用户多半就是来装它的）。
        # ⚠️ 别把每个能装的都展开：五家都"能装"的时候全展开 = 一上来就
        # 5×50 行版本（几百个控件 + 几百张图标），页面又长又慢，
        # 而且跟"同时只能选一个"这件事自相矛盾（见 `_apply_selection`）。
        first_ok = next((r.get("key") for r in self._rows
                         if r.get("state") == "ok" and r.get("versions")), "")

        for row in self._rows:
            # 一行 = **一个带框的折叠组**，状态那句话说在框里面（副标题）。
            # ⚠️ 以前是"框外一行状态文字 + 框"两行，看着散（用户 2026-09 提的）；
            # 现在状态进框、右对齐，一组就是一个卡片。
            key = row.get("key", "")
            group = CollapsibleGroup(row.get("name", key))
            group.set_note(self._status_text(row))
            group.set_items([self._version_entry(row, v)
                             for v in row.get("versions", [])])
            if key == first_ok:
                group.set_expanded(True)
            # 用户点里面某个版本 → 就算"选了这个加载器"，其他组要锁掉
            group.item_activated.connect(
                lambda entry, idx, k=key: self.on_item_clicked(k, entry, idx))
            # 点灰掉那组的标题 = 改选它（锁死 ≠ 死路，用户 2026-09 定的）
            group.header_clicked.connect(
                lambda k=key: self.switch_to(k))
            # 给测试/以后取用：状态文字现在在组自己的副标题上
            group.status_label = group.header_note
            self._groups[key] = group
            self._loader_box.insertWidget(self._loader_box.count() - 1, group)

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

        ⚠️ 这个方法名是**故意的**（"on_" 开头）—— 页面那边会扫
        `dir(page)` 找 `on_*` 方法来接信号。别改名。
        ⚠️ 已经选过别的加载器之后，点**别的组里的行**会**改选过去**
        （`_apply_selection` 会把原来那组锁上）—— 同时只能选一个，
        但永远有退路；改选那条路平时走的是 `switch_to()`（灰掉那组
        根本展不开，所以它的行也点不到）。
        """
        if not key or not isinstance(entry, dict):
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

    def switch_to(self, key: str):
        """改选另一个加载器（点灰掉那组的标题时走这儿）

        选它的**最新版**（`versions[0]`，列表本来就是新的在前），
        版本号具体的挑法跟第一次点一行是一回事。
        """
        row = next((r for r in self._rows if r.get("key") == key), None)
        if row is None or row.get("state") != "ok" or not row.get("versions"):
            # 没版本 / 没做 / 拉不到的那几组：灰着就是灰着，不给改选
            return
        self.on_item_clicked(key, {"id": row["versions"][0].get("version", "")}, 0)

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

        - 选中那组：解锁、展开、描边亮起来、里面选中的那行标出来
        - 其他组：`set_locked(True)`（灰掉、收起来、副标题写"与 X 不兼容"、
          提示"点一下改成它"），但**点标题仍然能改选**（见 `switch_to`）
        """
        picked_key = self._picked[0] if self._picked else ""
        for key, group in self._groups.items():
            picked_here = key == picked_key
            row = next((r for r in self._rows if r.get("key") == key), None)
            name = (row or {}).get("name", key)
            group.set_locked(bool(picked_key) and not picked_here,
                             note=self._note_for(key),
                             tooltip=(tr("点一下改成 {name}", name=name)
                                      if picked_key and not picked_here else ""))
            group.set_selected(picked_here)
            if picked_here:
                # ⚠️ 顺序不能反：折叠着的组还没建行，得先展开再标"已选"
                # （反过来的话 `set_picked_index()` 找不到任何行，标记丢失）
                group.set_expanded(True)
            # ⚠️ 只标记**这一组**里的行：`_picked_index` 是"这一组的第几个"，
            # 不是全局的，标到别的组上会点亮不相干的一行
            group.set_picked_index(self._picked_index if picked_here else -1)

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

    # ---------- 动作 ----------

    def version_name(self) -> str:
        """用户填的版本名（空的话退回版本 id —— 不该出现空目录名）"""
        text = self.name_edit.text().strip()
        return text or (self._entry or {}).get("id", "")

    def selected_loader(self):
        """**兜底**用哪个加载器：第一个能装的 + 它的最新版

        ⚠️ 用户手点过的话以 `picked_loader()` 为准（见 `_on_install`）——
        这个方法只回答"他什么都没点的时候默认装什么"。
        """
        for row in self._rows:
            if row.get("state") == "ok" and row.get("versions"):
                return row.get("key"), row["versions"][0].get("version", "")
        return None, ""

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
        # 用户点过就用他点的那个版本；没点过才退回"第一个能装的 + 最新版"
        loader_key, loader_version = self.picked_loader()
        if not loader_key:
            loader_key, loader_version = self.selected_loader()
        self.install_requested.emit({
            "version_id": entry.get("id", ""),
            "name": self.version_name(),
            "loader_key": loader_key,
            "loader_version": loader_version,
        })

    # ---------- 语言 ----------

    def retranslate(self):
        super().retranslate()
        # 状态那几句是生成的，得重铺一遍
        if self._rows:
            self.set_rows(self._rows)
