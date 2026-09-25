"""版本页：游戏目录 + 本地版本清单 + 选中版本的详情和操作

**页面职责（2026-09 调整）**：版本列表从「启动」页搬到了这里 ——
同一份列表在两边各摆一个，跟"主界面和日志窗口各显示一份日志"是同一个毛病。
现在启动页只有启动面板，选版本、换游戏目录、看缺哪些库都在这页。

游戏目录的选择也搬来了（原来在设置页）。放在这里更顺手：换目录就是为了看
别的目录下的版本，而设置页那张卡片跟"版本"这件事隔了两层。
搬过来以后设置页那张卡片就删掉了 —— 同一件事不留两个入口。

**故意不做"删除版本"**：这个用户的存档和 mod 都在版本目录里（版本隔离），
删版本等于连存档一起删。等有明确的需求和足够的确认流程再说。
"""

from pathlib import Path
import threading

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget
)

from core import install as install_mod
from core.config import config, effective_threads
from core.download import DownloadManager
from core.i18n import tr
from core.launch import load_version, rules_allow
from core.repair import missing_libraries
from core.resources import app_dir
from core.versions import official_minecraft_dir
from core.versions import type_label as version_type_label
from ui.icons import action_icon
from ui.dialogs.download_window import DownloadWindow
from ui.translatable import TranslatableWidget
from ui.widgets.version_list import VersionList

DIR_WIDTH = 300      # 游戏目录那一列（右边的窄列）—— 300 是路径不折行的最小宽度


def _set_tone(widget, object_name: str):
    """换 objectName 必须重新 polish，否则 QSS 不重算"""
    if widget.objectName() == object_name:
        return
    widget.setObjectName(object_name)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class VersionsPage(TranslatableWidget):
    # 游戏目录变了 —— 主窗口拿它去让别的页面重扫
    config_changed = pyqtSignal()
    # 选中的版本变了 —— 启动页的面板跟着走
    version_selected = pyqtSignal(object)
    # 双击 / 回车了一个版本 —— 主窗口据此切回启动页
    version_activated = pyqtSignal(object)
    # 点了「版本设置」—— 主窗口导航到版本设置页
    version_settings_requested = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self._version = None
        self._merged = None         # 选中版本的合并后 JSON（补全要用）
        self._missing = []
        # 补全（缺文件）那一条线的状态：后台线程只写结果，主线程定时器来收
        self._repairing = False
        self._repair_win = None
        self._repair_manager = None
        self._repair_done = False
        self._repair_ok = False
        self._repair_result = ""
        self._repair_note = ""      # 重建 JSON 之类要额外说一句的话
        self._loading = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(8)

        layout.addWidget(self.label("版本管理", "PageTitle"))
        layout.addWidget(self.label("选一个版本，或者换一个游戏目录", "PageSubtitle"))
        layout.addSpacing(12)

        # 上：版本详情（整条横着放，见 _make_detail_card 的注释）
        detail_card = self._make_detail_card()
        layout.addWidget(detail_card)

        # 中 + 右：版本列表（原位不变）+ 游戏目录（原来是页面顶部一条）
        body = QHBoxLayout()
        body.setSpacing(16)

        list_card = QFrame()
        list_card.setObjectName("Card")
        list_box = QVBoxLayout(list_card)
        list_box.setContentsMargins(18, 16, 18, 16)
        list_box.setSpacing(10)

        self.version_list = VersionList()
        self.version_list.selection_changed.connect(self._on_selected)
        # 双击 / 回车一个版本 = 选中它 + 回启动页（用户 2026-09 要求）
        self.version_list.activated.connect(self._on_activated)
        list_box.addWidget(self.version_list)
        # 双击这个动作没有任何视觉提示，写在列表下面明说一句
        list_box.addWidget(self.label("双击一个版本可以直接回到启动页。", "HintText"))
        body.addWidget(list_card, 1)

        body.addWidget(self._make_dir_card())
        layout.addLayout(body, 1)

        self._refresh_dir_hint()
        self.version_list.select_id(config.get("last_version", ""))
        self._repair_timer = QTimer(self)
        self._repair_timer.setInterval(200)
        self._repair_timer.timeout.connect(self._poll_repair)
        self._loading = False

    # ---------- 游戏目录 ----------

    def _make_dir_card(self):
        card = QFrame()
        card.setObjectName("Card")
        card.setFixedWidth(DIR_WIDTH)      # 右上角那一列，跟左边列表并排
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 16, 18, 16)
        box.setSpacing(10)

        box.addWidget(self.label("游戏目录", "SectionTitle"))

        # 竖着排：这一列只有 260 px，横排会让路径和按钮互相挤
        # 用**列表**而不是下拉框：路径太长，下拉框里只能挤成一行、也看不出
        # 有哪几个目录是"认识的"。列表每项两行（名字 + 路径）跟版本列表一个样。
        self.dir_list = QListWidget()
        self.dir_list.setObjectName("VersionList")     # 借版本列表的卡片行样式
        self.dir_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.dir_list.currentItemChanged.connect(self._on_dir_selected)
        box.addWidget(self.dir_list)

        self.dir_hint = QLabel()
        self.dir_hint.setObjectName("HintText")
        self.dir_hint.setWordWrap(True)
        box.addWidget(self.dir_hint)

        box.addSpacing(4)
        box.addWidget(self.label("添加或导入", "SidebarSectionLabel"))

        # 图标是 svg（Qt 不支持 currentColor，所以深/浅各一份，见 ui/icons.action_icon）
        self.new_btn = self.button("新建 .minecraft 文件夹")
        self.new_btn.setIcon(action_icon("folder"))
        self.new_btn.clicked.connect(self._create_dir)
        box.addWidget(self.new_btn)

        self.add_btn = self.button("添加已有文件夹")
        self.add_btn.setIcon(action_icon("plus-circle"))
        self.add_btn.clicked.connect(self._browse_dir)
        box.addWidget(self.add_btn)

        # 预留：整合包导入要联网下载解压，属于 v0.4 那批。先灰着并说明 ——
        # 不留一个点了没反应的按钮。
        self.import_btn = self.button("导入整合包")
        self.import_btn.setIcon(action_icon("tool"))
        self.import_btn.setEnabled(False)
        self.import_btn.setToolTip(tr("整合包导入还没做（计划在 v0.4）"))
        box.addWidget(self.import_btn)

        box.addStretch()
        self._fill_dir_combo()
        return card

    @staticmethod
    def _presets():
        """固定两项：官方启动器文件夹 + 启动器目录旁边

        两个都是**算出来的**（不存配置），所以换台机器/换个位置自动就对 ——
        这就是"自适应检测"。"启动器目录旁边"那个不存在时不用慌：
        点「新建 .minecraft 文件夹」就地建一个。
        """
        # 文案直接写在 tr() 里，提取工具才认得出
        return (
            (tr("官方启动器文件夹"), official_minecraft_dir()),
            (tr("启动器目录旁边"), app_dir() / ".minecraft"),
        )

    @staticmethod
    def _norm_dir(path_text: str) -> str:
        r"""把目录路径统一成原生写法 + 小写，用来**比较**

        ⚠️ 为什么必须统一：Qt 的文件对话框返回的是**正斜杠**（`D:/MC/foo`），
        而配置里、预设里都是反斜杠（`D:\MC\foo`）。不统一的话两边对不上，
        代码会以为"当前用的目录不在列表里"，于是**多冒出一项「当前使用」**
        （用户 2026-09 报过）。大小写同理（Windows 不敏感）。
        """
        try:
            return str(Path(path_text)).lower()
        except (OSError, ValueError, TypeError):
            return str(path_text).lower()
    @staticmethod
    def _dir_label(path_text: str) -> str:
        """列表里那一项的标题

        `.minecraft` 结尾的取**上一级**目录名（`D:\\DHML\\.minecraft` → `DHML`），
        不然列表里会是一串一模一样的 ".minecraft"，谁也分不清哪个是哪个。
        """
        p = Path(path_text)
        if p.name.lower() == ".minecraft":
            return p.parent.name or str(p.parent)
        return p.name or path_text

    def _make_dir_row(self, label: str, path_text: str):
        """列表里的一行：名字（大）+ 路径（**更小的字**）

        为什么不用 `QListWidgetItem("名字\n路径")` 那种两行文本：
        一个 item 只有一个字体，两行只能一样大；路径通常很长，跟名字一样大
        会把名字压下去。所以用自定义控件（跟版本列表那些行一个做法），
        路径用 `#DirRowPath` 那个 11px 的小字。
        """
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, path_text)

        row = QWidget()
        # ⚠️ 这层控件会盖在 item 的选中底色上面：它自己必须**透明**，
        # 否则选中高亮就被糊掉了（QSS 里 #DirRow 那一条就是干这个的）
        row.setObjectName("DirRow")
        box = QVBoxLayout(row)
        box.setContentsMargins(10, 6, 10, 6)
        box.setSpacing(1)

        name = QLabel(label)
        name.setObjectName("DirRowName")
        box.addWidget(name)

        path = QLabel(path_text)
        path.setObjectName("DirRowPath")
        path.setToolTip(path_text)
        box.addWidget(path)

        item.setSizeHint(row.sizeHint())
        # ⚠️ 这里**不能** setItemWidget：item 还没 addItem 进列表，Qt 认为
        # 那个控件没有归属，会把它销毁 —— 表现就是"行是空的，只有选中高亮"。
        # 挂控件必须在 addItem 之后（见 _fill_dir_combo）。
        return item, row
    def _fill_dir_combo(self):
        """把"认识的目录"填进列表：固定两项 + 记住用过的 + 当前在用的

        ⚠️ 名字里的 combo 是历史（以前是 QComboBox）—— 改名的代价是
        retranslate() 等三处调用点都要跟着改，不值得，就这样留着。
        """
        current = str(config.get_minecraft_dir())
        entries = list(self._presets())
        seen = {self._norm_dir(str(path)) for _label, path in entries}
        for text in config.get("known_minecraft_dirs", []):
            if self._norm_dir(text) not in seen:
                entries.append((self._dir_label(text), Path(text)))
                seen.add(self._norm_dir(text))
        if self._norm_dir(current) not in seen:
            entries.append((tr("当前使用"), Path(current)))

        self.dir_list.blockSignals(True)
        self.dir_list.clear()
        for label, path in entries:
            item, row = self._make_dir_row(label, str(path))
            self.dir_list.addItem(item)               # ★ 先加进列表
            self.dir_list.setItemWidget(item, row)    # ★ 再挂控件（顺序不能反）
            if self._norm_dir(str(path)) == self._norm_dir(current):
                self.dir_list.setCurrentItem(item)
        self.dir_list.blockSignals(False)

    def _refresh_dir_hint(self):
        path = config.get_minecraft_dir()
        try:
            exists = path.is_dir()
        except OSError:
            exists = False

        # 「新建 .minecraft 文件夹」**只在"现在这个游戏目录根本不存在"时才出现**。
        #
        # ⚠️ 改过一次（2026-09 用户报的）：原来是看"启动器旁边那个目录在不在",
        # 结果用户选好了官方目录、游戏能跑，只要启动器旁边没建过，
        # 这个按钮就一直杵在那儿 —— 既多余，又像在催他"再建一个"，
        # 点下去还会把游戏目录切到启动器旁边那个去（用户就是这么中招的）。
        # 判据应该是"当前这个目录能不能用"，跟启动器旁边没关系。
        self.new_btn.setVisible(not exists)

        if exists:
            self.dir_hint.setText(tr("当前游戏目录：{path}", path=path))
        else:
            self.dir_hint.setText(tr(
                "⚠ {path} 不存在 —— 点「新建 .minecraft 文件夹」就地建一个。",
                path=path))

    def _on_dir_selected(self, current, previous=None):
        if self._loading or current is None:
            return
        path = current.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        target = Path(path)
        try:
            exists = target.is_dir()
        except OSError:
            exists = False

        if not exists:
            # ⚠️ 这里必须问一句，不能"选了就认"。
            # 列表里有「启动器目录旁边」这种**算出来的、可能还不存在**的项，
            # 原来点它会悄悄变成"当前游戏目录" —— 于是下载就装到
            # 启动器旁边那个目录去了，用户还以为自己选的是官方目录
            # （2026-09 用户报的就是这个：界面高亮和实际在用的目录不一致）。
            reply = QMessageBox.question(
                self, tr("目录不存在"),
                tr("{path}\n\n这个目录还不存在，现在建一个吗？", path=path),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if reply != QMessageBox.StandardButton.Yes:
                # 不建就**别切**：把列表的选中项退回真正在用的那个，
                # 不然高亮和实际用的不一致，等于骗人
                self._fill_dir_combo()
                return
            try:
                target.mkdir(parents=True, exist_ok=True)
                (target / "versions").mkdir(exist_ok=True)
            except OSError as e:
                QMessageBox.warning(self, tr("创建失败"), str(e))
                self._fill_dir_combo()
                return

        self._apply_dir(path)

    def _apply_dir(self, path_text: str):
        # 归一化：浏览框给的是正斜杠，存进配置前统一成原生写法
        path_text = str(Path(path_text))
        config.set("minecraft_dir", path_text)
        # 记进"用过的列表"：下次打开还在列表里，不用重新浏览一遍
        config.remember_minecraft_dir(path_text)
        self._refresh_dir_hint()
        self.version_list.reset_scanner()
        self.config_changed.emit()

    def _browse_dir(self):
        """挑一个 .minecraft 目录

        ⚠️ 起始目录**必须是个真实存在的目录**：原来直接拿当前游戏目录当起点，
        而"官方启动器目录"（%APPDATA%\\.minecraft）在没装过官方启动器的机器上
        根本不存在 —— 那种情况下 Windows 的文件对话框会当场退出来，
        表现就是"点了浏览…没反应"（用户报过这个）。
        """
        current = config.get_minecraft_dir()
        try:
            start = current if current.is_dir() else Path.home()
        except OSError:
            start = Path.home()
        chosen = QFileDialog.getExistingDirectory(
            self.window(), tr("选择 .minecraft 目录"), str(start))
        if not chosen:
            return
        # 选错目录（比如指到了桌面上）会让"扫不到版本"变成一个谜，
        # 所以这里先问一句 —— 但只问浏览来的，预设是可信的
        if not (Path(chosen) / "versions").is_dir():
            reply = QMessageBox.question(
                self, tr("目录可能不对"),
                tr("选中的目录里没有 versions 文件夹：\n{path}\n\n仍要使用吗？", path=chosen),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._apply_dir(chosen)
        self._fill_dir_combo()

    def _create_dir(self):
        """把**当前这个**游戏目录建出来并切过去

        ⚠️ 目标原来是写死"启动器旁边那个"。但按钮的显示判据、提示语
        （"点「新建 .minecraft 文件夹」就地建一个"）说的都是**当前这个**目录 ——
        目标写死的话，用户选着别的路径点下去，建出来的却是启动器旁边那个，
        提示和结果对不上，而且游戏就装到不是他想要的地方去了。
        现在建的、并切过去的，就是当前选中的那个目录。
        （选中列表里"启动器目录旁边"那一项时，它就等于启动器旁边那个，
        所以老用法一点没变。）
        """
        path = config.get_minecraft_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
            (path / "versions").mkdir(exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, tr("创建失败"), str(e))
            return
        self._apply_dir(str(path))      # 里面会记住它 + 重扫 + 通知别的页
        self._fill_dir_combo()

    # ---------- 顶部详情（2026-09 改：原来是右边一列）----------
    #
    # 为什么挪到顶部整条：右边那列只有 320 px，名字长的版本会折成三四行，
    # 三个按钮也只能竖着堆；而它左边紧挨着应用自己的侧边栏，看起来像"两个导航栏"。
    # 变成顶部横条之后按钮能横着排，版本列表的宽度也回来了。

    def _make_detail_card(self):
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 16, 20, 16)
        box.setSpacing(8)

        # 第一行：标题 + 版本名 + 元信息 + 三个按钮（靠右）
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self.label("版本详情", "SectionTitle"))

        self.detail_name = QLabel()
        self.detail_name.setObjectName("VersionRowName")
        head.addWidget(self.detail_name)

        self.detail_meta = QLabel()
        self.detail_meta.setObjectName("VersionRowMeta")
        head.addWidget(self.detail_meta)

        head.addStretch()

        self.settings_btn = self.button("版本设置")
        self.settings_btn.clicked.connect(self._open_settings)
        head.addWidget(self.settings_btn)

        self.folder_btn = self.button("打开文件夹")
        self.folder_btn.clicked.connect(self._open_folder)
        head.addWidget(self.folder_btn)

        self.repair_btn = self.button("补全缺失的文件")
        self.repair_btn.clicked.connect(self._start_repair)
        head.addWidget(self.repair_btn)
        box.addLayout(head)

        self.detail_path = QLabel()
        self.detail_path.setObjectName("HintText")
        self.detail_path.setWordWrap(True)
        box.addWidget(self.detail_path)

        self.detail_libs = QLabel()
        self.detail_libs.setObjectName("HintText")
        self.detail_libs.setWordWrap(True)
        box.addWidget(self.detail_libs)

        self.progress = QLabel()
        self.progress.setObjectName("HintText")
        self.progress.setWordWrap(True)
        box.addWidget(self.progress)

        return card

    def _on_activated(self, version):
        """双击 / 回车了一个版本：确保它被选中，然后请主窗口切回启动页

        "当前版本"本来就跟着选中走（`selection_changed` → 启动页的面板），
        所以这里不用再自己去 set_version，只要把"回启动页"发出去。

        保险起见先显式选一次：`itemActivated` 不保证之前一定走过一遍选中
        （比如用键盘上下移动后按回车，中间那次选中是走过的，但别指望）。
        """
        if not version:
            return
        self.version_list.select_id(version["id"])
        self.version_activated.emit(version)

    def _on_selected(self, version):
        self._version = version
        self._merged = None
        self._missing = []
        self.progress.setText("")

        if version:
            # 记住它：启动页下次打开直接用它
            config.set("last_version", version["id"])

        # 启动页的面板跟着走（两个页面共用一个"当前版本"）
        self.version_selected.emit(version)

        if not version:
            self.detail_name.setText(tr("未找到本地版本"))
            self.detail_meta.setText("")
            self.detail_path.setText("")
            self.detail_libs.setText("")
            self.folder_btn.setEnabled(False)
            self.settings_btn.setEnabled(False)
            self.repair_btn.setEnabled(False)
            return

        self.folder_btn.setEnabled(True)
        self.settings_btn.setEnabled(True)
        self.detail_name.setText(version["display_name"])
        self.detail_meta.setText(self._meta_text(version))
        self.detail_path.setText(str(version["path"]))
        self._refresh_libraries()

    def _meta_text(self, version) -> str:
        parts = [version_type_label(version["type"])]
        if version["loader_label"]:
            parts.append(version["loader_label"])
        if version["java_major"]:
            parts.append(f"Java {version['java_major']}")
        if version["isolated"]:
            parts.append(tr("版本隔离"))
        return "    ·    ".join(parts)

    def _refresh_libraries(self):
        """更新"缺什么"那两行 + 决定「补全缺失的文件」能不能点

        ⚠️ 按钮的可用性**不能只看库**：补全现在管的是全部文件
        （库 / 资源 / 客户端 jar / natives / 日志配置），所以只要版本 JSON
        读得出来就该能点 —— 哪怕库是齐的，也可能缺资源或缺 jar。
        以前是 `setEnabled(bool(self._missing))`，于是"库齐了但资源没下"
        这种版本根本按不动这个按钮。
        """
        version = self._version
        if not version["complete"]:
            # 缺客户端 jar：以前这里直接把按钮禁掉，等于"缺的东西补不了"。
            # 补全本来就能把 jar 下回来，所以照旧可点。
            self.detail_libs.setText(tr("缺少客户端 jar，这个版本启动不了"))
            _set_tone(self.detail_libs, "BadgeWarn")
            self._load_merged(version)
            return

        self._load_merged(version)

    def _load_merged(self, version):
        """读合并后的版本 JSON + 算缺哪些库，然后决定按钮状态"""
        mc_dir = Path(version["path"]).parent.parent
        try:
            self._merged = load_version(mc_dir, version["id"])
            self._missing = missing_libraries(mc_dir, self._merged)
        except Exception as e:                              # noqa: BLE001
            self._merged = None
            self._missing = []
            self.detail_libs.setText(tr("读不了版本 JSON：{err}", err=e))
            _set_tone(self.detail_libs, "BadgeWarn")
            self.repair_btn.setEnabled(False)
            return

        if version["complete"]:
            if self._missing:
                self.detail_libs.setText(tr("缺 {n} 个库", n=len(self._missing)))
                _set_tone(self.detail_libs, "BadgeWarn")
            else:
                self.detail_libs.setText(tr("库都齐了"))
                _set_tone(self.detail_libs, "HintText")
        self.repair_btn.setEnabled(not self._repairing)

    # ---------- 操作 ----------

    def _open_folder(self):
        if not self._version:
            return
        path = Path(self._version["path"])
        target = path if path.is_dir() else path.parent
        if target.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _open_settings(self):
        """改这个版本自己的启动设置（Java / 内存 / JVM 参数 / 游戏参数）

        以前弹对话框，现在导航到版本设置页（见 main_window 的接线）。
        """
        if not self._version:
            return
        self.version_settings_requested.emit(self._version)

    def _start_repair(self):
        """补全这个版本**缺的所有文件**（库 + 资源 + 客户端 jar + natives + 日志配置）

        以前只管库（`core/repair.py` 只做"把 JSON 里写了 url 但本地没有的库下回来"），
        于是缺 assets / 缺客户端 jar 的时候没有任何东西会补。
        现在直接复用安装引擎：`plan_version()` 本来就会把该有的东西全列出来，
        而引擎的判断是"本地已有且 sha1 对得上就跳过" ——
        所以"补全"就是拿它跑一遍，缺什么补什么；一个都不缺时扫一遍就结束
        （扫描进度在下载窗口里看得见：剩余文件在往下掉）。

        ⚠️ 后台线程只写结果，主线程用定时器收 —— 跟下载页一套做法，
        原因见 core/download.py 里关于 QThread 的说明。
        """
        if self._repairing or not self._version or not self._merged:
            return
        version = self._version
        mc_dir = Path(version["path"]).parent.parent
        jar_name = version["id"]

        self._repairing = True
        self.repair_btn.setEnabled(False)
        # 按钮自己也要变样：否则点下去毫无反馈，用户以为没反应
        self.repair_btn.setText(tr("补全中…"))
        self.version_list.setEnabled(False)
        # 反馈要**贴在按钮附近**（detail_libs 就在这一排按钮下面）。
        # 原来只写列表最底下那行 progress，离按钮太远，等于没有反馈。
        self.detail_libs.setText(tr("正在检查缺哪些文件…"))
        _set_tone(self.detail_libs, "HintText")
        self.progress.setText(tr("正在检查缺哪些文件…"))

        manager = DownloadManager(max_workers=effective_threads())
        # parent 一定要给：不给的话这个顶层窗口拿不到本窗口的样式表
        win = DownloadWindow(manager, title=tr("补全 {name}", name=jar_name),
                             parent=self)
        win.show()
        self._repair_win = win
        self._repair_manager = manager
        self._repair_done = False
        self._repair_ok = False
        self._repair_result = ""
        self._repair_note = ""

        threading.Thread(target=self._repair_worker,
                         args=(mc_dir, self._merged, jar_name, manager,
                               Path(version["path"])),
                         daemon=True).start()
        self._repair_timer.start()

    def _repair_worker(self, mc_dir, vj, jar_name, manager, version_dir):
        """后台线程：**先确认 JSON 对不对** → 算清单 → 下载 → 解 natives

        ⚠️ 这里**只写结果**，不碰界面（Qt 控件只能在主线程动）。
        """
        try:
            # 有 jar 但 JSON 不对（比如从 jar 里拖出来的那个 422 字节的
            # version.json）→ 先从官方清单把它重建回来。
            # 不重建的话后面什么都规划不出来：那份 JSON 里没有
            # downloads / libraries / mainClass，"补全"根本不知道要补什么。
            # 重建只要几十 KB，jar 一个字节都不用重下。
            if not install_mod.looks_like_launcher_json(vj):
                if install_mod.rebuild_version_json(mc_dir, jar_name, version_dir):
                    self._repair_note = tr("版本 JSON 不对，已从官方清单重建")
                    vj = load_version(mc_dir, jar_name)
                else:
                    self._repair_result = tr(
                        "版本 JSON 不对，而清单里也没有 {name} 这个名字", name=jar_name)
                    return

            index = install_mod.load_asset_index(mc_dir, vj,
                                                 fetch=install_mod.http_json)
            plan = install_mod.plan_version(vj, mc_dir, rules_allow=rules_allow,
                                            asset_index=index, jar_name=jar_name)
            install_mod.start_install(plan, manager)
            manager.start()
            finished = manager.wait_all(timeout=3600)

            if finished:
                natives_dir = plan.version_dir / (jar_name + "-natives")
                install_mod.extract_natives(plan, natives_dir)

            snap = manager.snapshot()
            # "补了几个"= 真的下了的（skipped 是本地本来就有、校验通过的）
            fetched = sum(1 for t in snap["tasks"] if not t.skipped)
            if not finished:
                self._repair_result = tr("下载超时（还没下完）")
            elif snap["failed"]:
                self._repair_result = tr("补全结束，但有 {n} 个文件失败",
                                         n=snap["failed"])
            elif snap["count"] == 0:
                self._repair_result = tr("没有可下载的文件（版本 JSON 不对？）")
            elif fetched == 0:
                self._repair_result = tr("检查完了，文件都是齐的")
                self._repair_ok = True
            else:
                self._repair_result = tr("补全完成，补了 {n} 个文件", n=fetched)
                self._repair_ok = True
        except Exception as e:                          # noqa: BLE001
            self._repair_result = tr("补全失败：{err}", err=e)
        finally:
            self._repair_done = True

    def _poll_repair(self):
        """主线程收结果（见 _start_repair 的说明）"""
        if not self._repair_done:
            return
        self._repair_timer.stop()
        self._repair_done = False
        self._repairing = False
        self.progress.setText(self._repair_result or tr("补全结束"))

        if self._repair_win is not None:
            self._repair_win.stop()     # 停掉它的定时器再放掉引用
            self._repair_win = None
        self._repair_manager = None

        self.version_list.setEnabled(True)
        self.repair_btn.setText(tr("补全缺失的文件"))
        self._refresh_libraries()       # 库/缺 jar 那两行要重算
        # 结果贴在按钮下面（跟"缺 N 个库"拼在一起），不然用户根本不知道点完发生了什么
        bits = [self.detail_libs.text()]
        if self._repair_note:
            bits.append(self._repair_note)
        if self._repair_result:
            bits.append(self._repair_result)
        self.detail_libs.setText("　·　".join(b for b in bits if b))
        if self._repair_ok and self._version:
            # 文件补上了 → 让本地列表重扫一遍（新补的版本可能刚变得可用）
            self.version_list.reset_scanner()

    # ---------- 外部接口（main_window 调） ----------

    def reload_versions(self):
        self.version_list.reload()

    def reset_scanner(self):
        """游戏目录换了以后重建 scanner 并重扫"""
        self.version_list.reset_scanner()
        self._refresh_dir_hint()

    def selected_version(self):
        return self._version

    # ---------- 语言切换 ----------

    def retranslate(self):
        super().retranslate()
        # 列表里的分组标题、行、脚注都是生成的，重建一遍（不用重扫）
        self.version_list.retranslate()
        self._fill_dir_combo()
        self._refresh_dir_hint()
