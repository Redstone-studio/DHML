"""单个版本的设置页

**不是弹窗** —— 从「版本」页的卡片和启动面板上的「版本设置」导航进来，
左上角「← 返回」回到来的那一页。分两页：

    设置      布局对着 PCL 的版本设置页抄：启动选项 / 内存分配 / 服务器 / 高级选项
    Mod 管理   这一版是占位：没装加载器 → 「该版本不可使用 Mod」那种提示卡，
               装了加载器 → 「Mod 列表还没做」

改动**即时保存**（和「设置」页一样，没有保存/取消按钮）：页面是常驻的，
再挂一对按钮反而要多记一份"打开时的快照"，还容易漏掉"切走时忘了保存"。

三态语义见 core/version_settings.py：文件里没有这个键 = 跟随启动器默认。
所以"选回跟随全局设置" = 把键删掉，**不是**把当前默认值抄一份进去
（抄进去以后改了全局默认，这个版本就不会跟着变了）。

⚠️ 四个**空壳**（版本隔离 / 游戏窗口标题 / 内存管理 / 启动前执行命令）：
画出来了但没接上，控件灰着、旁边有说明。PCL 里那两个「禁用 Java Launch
Wrapper / 禁用 LWJGL Unsafe Agent」是 PCL 自己往命令行塞的补丁的开关，
我们没塞补丁，**故意不画** —— 画了就是谁也看不懂的死开关。
"""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QRadioButton, QScrollArea, QSlider, QSpinBox, QStackedWidget,
    QVBoxLayout, QWidget
)

from core.i18n import tr
from core.java import scan_minecraft_dirs
from core.memory import (
    MEMORY_MAX, MEMORY_MIN, MEMORY_STEP, jvm_overhead_mb, snap_memory,
    system_memory
)
from core.version_settings import launcher_defaults, version_settings
from ui.tasks import QuickJavaScanTask
from ui.translatable import TranslatableWidget
from ui.widgets.memory_bar import MemoryBar

TAB_SETTINGS = 0
TAB_MODS = 1

# 左边的字段名那一列。用 minimumWidth 而不是固定宽度：界面文字会随语言变长
# （俄语尤其长），锁死宽度会把标签切掉。140 是"够放下最常见的中文标签、
# 又不会让输入框离得太远"的那个数。
LABEL_WIDTH = 140

# 「自定义信息」在游戏里就是 `${version_type}`：1.20.4 的主界面那行是
# `"Minecraft " + 版本名 + (type == "release" ? "" : "/" + type)`，
# 所以它显示在**主界面左下角**（不是 F3 —— F3 显示的是 `--version` 和客户端品牌）。
# 留空时由 `core/branding.launch_brand()` 自动填「启动器名/加载器（N 个模组）」。
# PCL 里这个设置叫「版本信息」/「自定义信息」。
CUSTOM_INFO_HINT_MAX = 32


def _gb(megabytes: int) -> str:
    """MB → "12.4"（配合文案里的 GB 用）"""
    return f"{max(0, int(megabytes)) / 1024:.1f}"


class VersionSettingsPage(TranslatableWidget):
    # 回上一页（哪一页进来的由主窗口记着）
    back_requested = pyqtSignal()
    # Mod 提示卡里的「版本选择」→ 切到版本页
    version_pick_requested = pyqtSignal()
    # 「管理档案」→ 切到账户页
    accounts_requested = pyqtSignal()
    # 存过一次盘（首页面板上的 Java / 内存那行要跟着刷新）
    saved = pyqtSignal()

    SAVE_DELAY_MS = 400
    FIELD_WIDTH = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = True
        self._version = None
        self._account = None
        self._javas = []
        self._java_scan = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 20)
        outer.setSpacing(6)

        outer.addWidget(self._make_header())
        outer.addSpacing(2)
        outer.addWidget(self._make_tabs())
        outer.addSpacing(6)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._make_settings_tab())     # TAB_SETTINGS
        self.stack.addWidget(self._make_mods_tab())         # TAB_MODS
        outer.addWidget(self.stack, 1)

        # 数值的防抖保存：拖滑块会连着发几十次 valueChanged，
        # 每次都 save() 的话 versions.json 会被写几十遍
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._flush)

        self._loading = False
        self.set_tab(TAB_SETTINGS)

    # ============================================================
    # 搭界面
    # ============================================================

    def _make_header(self) -> QWidget:
        """← 返回 + 版本名 + 版本路径"""
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.back_btn = self.button("← 返回", "LinkButton")
        self.back_btn.clicked.connect(self.back_requested.emit)
        row.addWidget(self.back_btn)
        row.addStretch()
        layout.addLayout(row)

        # 版本名/路径是动态的，retranslate() 里重贴
        self.name_label = QLabel()
        self.name_label.setObjectName("PageTitle")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label)

        self.path_label = QLabel()
        self.path_label.setObjectName("PageSubtitle")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        self.scope_hint = self.label("这些设置只对该游戏版本生效，不影响其他版本。",
                                     "HintText")
        self.scope_hint.setWordWrap(True)
        layout.addWidget(self.scope_hint)
        return box

    def _make_tabs(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        # ⚠️ 这里**不能**写成 `for key in ("设置", "Mod 管理")` 的循环：那样字面量
        # 是经由变量进 self.button() 的，文案提取工具认不出来（会报"没走文案系统"）。
        # 逐个写出来它才看得见；绑定的 key 仍然是中文原文，切语言时能重设。
        self.tab_buttons = [
            self.button("设置", "TabButton"),
            self.button("Mod 管理", "TabButton"),
        ]
        for index, button in enumerate(self.tab_buttons):
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, i=index: self.set_tab(i))
            row.addWidget(button)
        row.addStretch()

        sep = QFrame()
        sep.setObjectName("CardSep")
        sep.setFixedHeight(1)
        wrapper = QWidget()
        column = QVBoxLayout(wrapper)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(box)
        column.addWidget(sep)
        return wrapper

    def _scrollable(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _card(self, title: str):
        """一张卡片，返回 (卡片, 内容布局)"""
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 16, 20, 18)
        box.setSpacing(10)
        box.addWidget(self.label(title, "SectionTitle"))
        return card, box

    def _field_row(self, key: str):
        """一行：左边字段名 + 右边控件（返回 (行布局, 已经放好的字段名标签)）"""
        row = QHBoxLayout()
        row.setSpacing(10)
        label = self.label(key, "FieldLabel")
        label.setMinimumWidth(LABEL_WIDTH)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label)
        return row

    def _shell_combo(self, items, current: int = 0) -> QComboBox:
        """空壳下拉：画出来、能看、不能改（旁边会写"还没接上"）"""
        combo = QComboBox()
        combo.addItems(items)
        combo.setCurrentIndex(current)
        combo.setFixedWidth(self.FIELD_WIDTH)
        combo.setEnabled(False)
        return combo

    def _shell_edit(self, placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setFixedWidth(self.FIELD_WIDTH)
        edit.setEnabled(False)
        return edit

    # ---------- 设置页 ----------

    def _make_settings_tab(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 12, 12, 12)
        layout.setSpacing(12)

        layout.addWidget(self._make_launch_card())
        layout.addWidget(self._make_memory_card())
        layout.addWidget(self._make_server_card())
        layout.addWidget(self._make_advanced_card())
        layout.addStretch()
        return self._scrollable(content)

    def _make_launch_card(self) -> QFrame:
        card, box = self._card("启动选项")

        # 版本隔离：显示**当前实际判断结果**，但暂时不让改
        row = self._field_row("版本隔离")
        self.isolate_combo = self._shell_combo([tr("开启"), tr("关闭")])
        row.addWidget(self.isolate_combo)
        row.addStretch()
        box.addLayout(row)
        self.isolate_hint = QLabel()
        self.isolate_hint.setObjectName("HintText")
        self.isolate_hint.setWordWrap(True)
        box.addWidget(self.isolate_hint)

        # 游戏窗口标题（空壳）
        row = self._field_row("游戏窗口标题")
        self.title_edit = self._shell_edit(tr("留空 = 不改"))
        row.addWidget(self.title_edit)
        row.addStretch()
        box.addLayout(row)
        self.title_hint = QLabel()
        self.title_hint.setObjectName("HintText")
        self.title_hint.setWordWrap(True)
        box.addWidget(self.title_hint)

        # 自定义信息（真：它就是 ${version_type}）
        row = self._field_row("自定义信息")
        self.info_edit = QLineEdit()
        self.info_edit.setFixedWidth(self.FIELD_WIDTH)
        self.info_edit.setMaxLength(CUSTOM_INFO_HINT_MAX)
        self.info_edit.textChanged.connect(self._on_changed)
        row.addWidget(self.info_edit)
        row.addStretch()
        box.addLayout(row)
        box.addWidget(self.label(
            "游戏主界面那行字里「斜杠后面」那段（启动参数里的 ${version_type}）。"
            "留空就自动填「启动器名/加载器（N 个模组）」，填 release 就不显示。",
            "HintText"))

        # Java（真）
        row = self._field_row("Java")
        self.java_combo = QComboBox()
        self.java_combo.setMinimumWidth(300)
        self.java_combo.currentIndexChanged.connect(self._on_changed)
        row.addWidget(self.java_combo, 1)
        self.java_rescan_btn = self.button("重新扫描")
        self.java_rescan_btn.clicked.connect(self._rescan_java)
        row.addWidget(self.java_rescan_btn)
        box.addLayout(row)

        self.java_hint = QLabel()
        self.java_hint.setObjectName("HintText")
        self.java_hint.setWordWrap(True)
        box.addWidget(self.java_hint)
        return card

    def _make_memory_card(self) -> QFrame:
        card, box = self._card("内存分配")

        radio_row = QHBoxLayout()
        radio_row.setSpacing(20)
        self.mem_follow = QRadioButton()
        self.bind(self.mem_follow, "跟随全局设置")
        self.mem_follow.setChecked(True)
        self.mem_follow.toggled.connect(self._on_memory_mode_changed)
        radio_row.addWidget(self.mem_follow)

        # 自动配置：空壳（PCL 会按装了哪些 Mod + 剩余内存动态算，逻辑不少）
        self.mem_auto = QRadioButton()
        self.bind(self.mem_auto, "自动配置")
        self.mem_auto.setEnabled(False)
        radio_row.addWidget(self.mem_auto)

        self.mem_custom = QRadioButton()
        self.bind(self.mem_custom, "自定义")
        radio_row.addWidget(self.mem_custom)

        radio_row.addStretch()
        box.addLayout(radio_row)

        self.mem_auto_hint = QLabel()
        self.mem_auto_hint.setObjectName("HintText")
        self.mem_auto_hint.setWordWrap(True)
        box.addWidget(self.mem_auto_hint)

        # 启动前内存优化（空壳）
        row = self._field_row("启动游戏前进行内存优化")
        self.optimize_combo = self._shell_combo(
            [tr("跟随全局设置"), tr("开启"), tr("关闭")])
        row.addWidget(self.optimize_combo)
        row.addStretch()
        box.addLayout(row)

        # 最小（数字框）+ 最大（滑块）—— 跟「设置」页同一套做法，
        # 用滑块而不是数字框：拖比点小箭头快，条的长度本身就是"内存有多大"的提示
        row = QHBoxLayout()
        row.setSpacing(10)
        min_label = self.label("最小", "FieldLabel")
        min_label.setMinimumWidth(LABEL_WIDTH)
        row.addWidget(min_label)
        self.min_mem = QSpinBox()
        self.min_mem.setRange(MEMORY_MIN, 32768)
        self.min_mem.setSingleStep(MEMORY_STEP)
        self.min_mem.setSuffix(" MB")
        self.min_mem.setValue(MEMORY_MIN)
        self.min_mem.valueChanged.connect(self._on_min_changed)
        row.addWidget(self.min_mem)
        row.addStretch()
        box.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(10)
        max_label = self.label("最大", "FieldLabel")
        max_label.setMinimumWidth(LABEL_WIDTH)
        row.addWidget(max_label)

        info = system_memory()
        upper = min(MEMORY_MAX, max(4096, info.total_mb)) if info.ok else MEMORY_MAX
        upper = (upper // MEMORY_STEP) * MEMORY_STEP

        self.max_mem = QSlider(Qt.Orientation.Horizontal)
        self.max_mem.setObjectName("MemorySlider")
        self.max_mem.setRange(MEMORY_MIN, upper)
        self.max_mem.setSingleStep(MEMORY_STEP)
        self.max_mem.setPageStep(MEMORY_STEP * 4)
        self.max_mem.setTickInterval(MEMORY_STEP * 8)
        self.max_mem.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.max_mem.setValue(MEMORY_MIN)
        # ⚠️ 先 setValue 再 connect：不然初始化那一下会被当成"用户拖了"
        self.max_mem.valueChanged.connect(self._on_max_changed)
        row.addWidget(self.max_mem, 1)

        # 数字单独一个标签：固定宽度，拖的时候不会把布局推来推去
        self.max_value = QLabel(f"{self.max_mem.value()} MB")
        self.max_value.setObjectName("FieldLabel")
        self.max_value.setFixedWidth(90)
        self.max_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.max_value)
        box.addLayout(row)

        box.addWidget(self.label("最小堆不能大于最大堆 —— 两边会自动联动。", "HintText"))

        self.memory_bar = MemoryBar()
        box.addWidget(self.memory_bar)

        self.memory_legend = QLabel()
        self.memory_legend.setObjectName("HintText")
        self.memory_legend.setWordWrap(True)
        box.addWidget(self.memory_legend)
        return card

    def _make_server_card(self) -> QFrame:
        card, box = self._card("服务器")

        # 登录方式：我们只有离线档案，所以这里只**显示**当前档案
        row = self._field_row("登录方式")
        self.account_label = QLabel()
        self.account_label.setObjectName("FieldLabel")
        self.account_label.setWordWrap(True)
        row.addWidget(self.account_label, 1)
        self.manage_btn = self.button("管理档案", "LinkButton")
        self.manage_btn.clicked.connect(self.accounts_requested.emit)
        row.addWidget(self.manage_btn)
        box.addLayout(row)
        self.login_hint = QLabel()
        self.login_hint.setObjectName("HintText")
        self.login_hint.setWordWrap(True)
        box.addWidget(self.login_hint)

        # 自动进入服务器（真）
        row = self._field_row("自动进入服务器")
        self.server_edit = QLineEdit()
        self.server_edit.setFixedWidth(self.FIELD_WIDTH)
        self.server_edit.textChanged.connect(self._on_changed)
        row.addWidget(self.server_edit)
        row.addStretch()
        box.addLayout(row)
        box.addWidget(self.label(
            "英文冒号分隔 IP 和端口，例如 233.233.233.233:12345。"
            "1.20 起走 QuickPlay，更老的版本走 --server/--port。", "HintText"))
        return card

    def _make_advanced_card(self) -> QFrame:
        """高级选项：卡片标题本身是个折叠开关（默认收起）"""
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 12, 20, 16)
        box.setSpacing(10)

        # 文字里带箭头，所以不用 self.button() 绑定，retranslate() 里重贴
        self.advanced_toggle = QPushButton()
        self.advanced_toggle.setObjectName("SectionToggle")
        self.advanced_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.advanced_toggle.clicked.connect(self._toggle_advanced)
        box.addWidget(self.advanced_toggle)

        self.advanced_body = QWidget()
        body = QVBoxLayout(self.advanced_body)
        body.setContentsMargins(0, 4, 0, 0)
        body.setSpacing(10)

        # Java 虚拟机参数（真）
        row = self._field_row("Java 虚拟机参数")
        self.jvm_edit = QPlainTextEdit()
        self.jvm_edit.setPlaceholderText(tr("例如 -XX:+UseG1GC"))
        self.jvm_edit.setFixedHeight(72)
        self.jvm_edit.textChanged.connect(self._on_changed)
        row.addWidget(self.jvm_edit, 1)
        body.addLayout(row)

        # 游戏参数（真）
        row = self._field_row("游戏参数")
        self.game_edit = QLineEdit()
        self.game_edit.setPlaceholderText(tr("例如 --width 1920 --height 1080"))
        self.game_edit.textChanged.connect(self._on_changed)
        row.addWidget(self.game_edit, 1)
        body.addLayout(row)

        # 内存管理（空壳）
        row = self._field_row("内存管理")
        self.gc_combo = self._shell_combo(
            [tr("不指定（可自定义）"), tr("标准 G1GC"), tr("调优 G1GC")])
        self.gc_combo.setFixedWidth(260)
        row.addWidget(self.gc_combo)
        row.addStretch()
        body.addLayout(row)

        # 启动前执行命令（空壳）
        row = self._field_row("启动前执行命令")
        self.pre_edit = self._shell_edit(tr("留空 = 不执行"))
        row.addWidget(self.pre_edit)
        row.addStretch()
        body.addLayout(row)

        self.shell_hint = QLabel()
        self.shell_hint.setObjectName("HintText")
        self.shell_hint.setWordWrap(True)
        body.addWidget(self.shell_hint)

        body.addWidget(self.label(
            "「Java 虚拟机参数」原样追加到 java 命令行；填错了 JVM 会拒绝启动。"
            "「游戏参数」追加在版本自带的参数后面，同名的选项以这里为准。", "HintText"))

        box.addWidget(self.advanced_body)
        self.advanced_body.setVisible(False)
        return card

    # ---------- Mod 页（占位） ----------

    def _make_mods_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 12, 12, 12)
        outer.addStretch()

        # 没有 Mod 加载器时显示的那张卡（照 PCL 的提示抄的）
        self.notice_card = QFrame()
        self.notice_card.setObjectName("Card")
        notice = QVBoxLayout(self.notice_card)
        notice.setContentsMargins(28, 26, 28, 26)
        notice.setSpacing(12)

        self.notice_title = self.label("该版本不可使用 Mod", "NoticeTitle")
        self.notice_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice.addWidget(self.notice_title)

        sep = QFrame()
        sep.setObjectName("CardSep")
        sep.setFixedHeight(1)
        notice.addWidget(sep)

        # 同上：两条说明各自写出来，别走 for ... in (...)，否则提取工具看不见
        self.notice_lines = [
            self.label("你需要先安装 Forge、Fabric 等 Mod 加载器才能使用 Mod，"
                       "请在下载页面安装这些版本。", "EmptyState"),
            self.label("如果你已经安装了 Mod 加载器，那么你很可能选错了版本，"
                       "点下面的「版本选择」切换一个。", "EmptyState"),
        ]
        for line in self.notice_lines:
            line.setWordWrap(True)
            notice.addWidget(line)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        buttons.addStretch()
        self.download_btn = self.button("转到下载页面", "PrimaryButton")
        self.download_btn.clicked.connect(self._on_download_clicked)
        buttons.addWidget(self.download_btn)
        self.pick_btn = self.button("版本选择", "LinkButton")
        self.pick_btn.clicked.connect(self.version_pick_requested.emit)
        buttons.addWidget(self.pick_btn)
        buttons.addStretch()
        notice.addLayout(buttons)

        self.download_tip = QLabel()
        self.download_tip.setObjectName("HintText")
        self.download_tip.setWordWrap(True)
        self.download_tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.download_tip.hide()
        notice.addWidget(self.download_tip)

        # 装了加载器的版本：列表还没做
        self.mods_placeholder = QLabel()
        self.mods_placeholder.setObjectName("EmptyState")
        self.mods_placeholder.setWordWrap(True)
        self.mods_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)

        outer.addWidget(self.notice_card)
        outer.addWidget(self.mods_placeholder)
        outer.addStretch()
        return page

    def _on_download_clicked(self):
        """下载 / 安装还没做（v0.4）—— 老老实实说，别装作按了会发生什么"""
        self.download_tip.setText(
            tr("下载和安装功能还没做（计划在 v0.4）。现在可以用「设置」页的"
               "游戏目录指向已经装好的整合包。"))
        self.download_tip.show()

    # ============================================================
    # 进来 / 切页
    # ============================================================

    def set_version(self, version: dict):
        """换一个版本（主窗口在导航过来时调用）"""
        self._version = version
        self.download_tip.hide()
        if version is None:
            return
        self._load()
        if not self._javas and self._java_scan is None:
            self._rescan_java()

    def set_account(self, account):
        """当前档案（只用来显示"登录方式"那一行）"""
        self._account = account
        self._refresh_account()

    def set_javas(self, javas):
        """外面已经扫过 Java 就把结果给进来，省一次扫描

        没有当前版本时只存着不重建下拉 —— 重建要靠 java_path 选索引，
        而那个得等 _load() 读完覆盖项。
        """
        if javas:
            self._javas = list(javas)
            if self._version is not None:
                self._fill_java_combo()
                self._refresh_java_hint()

    def set_tab(self, index: int):
        index = max(0, min(index, len(self.tab_buttons) - 1))
        for i, button in enumerate(self.tab_buttons):
            button.setChecked(i == index)
        self.stack.setCurrentIndex(index)

    def current_tab(self) -> int:
        return self.stack.currentIndex()

    @property
    def version_id(self) -> str:
        return (self._version or {}).get("id", "")

    # ============================================================
    # 读 / 写
    # ============================================================

    def _load(self):
        """把文件里的覆盖项贴到控件上（进来时、全局设置变了时都会调）"""
        self._loading = True
        version = self._version or {}
        version_id = self.version_id
        overrides = version_settings.get(version_id)
        effective = version_settings.resolve(version_id, launcher_defaults())

        self.name_label.setText(version.get("display_name", ""))
        self.path_label.setText(str(version.get("path", "")))

        # 启动选项
        self.isolate_combo.setCurrentIndex(0 if version.get("isolated") else 1)
        self.info_edit.setText(overrides.get("custom_info", ""))
        self.info_edit.setPlaceholderText(version.get("type", "") or tr("版本自带"))

        # 内存：文件里有没有这两个键 = 跟不跟随全局
        custom = "min_memory" in overrides or "max_memory" in overrides
        self.mem_custom.setChecked(custom)
        self.mem_follow.setChecked(not custom)
        self.min_mem.setValue(int(effective.get("min_memory", MEMORY_MIN)))
        self.max_mem.setValue(min(int(effective.get("max_memory", 2048)),
                                  self.max_mem.maximum()))
        self.max_value.setText(f"{self.max_mem.value()} MB")

        # 服务器 / 高级选项
        self.server_edit.setText(overrides.get("server_address", ""))
        self.jvm_edit.setPlainText(overrides.get("extra_jvm_args", ""))
        self.game_edit.setText(overrides.get("extra_game_args", ""))

        # ⚠️ Java 下拉必须在**读完覆盖项之后**重建：它是按存下来的 java_path
        # 选索引的。重建早了这个索引还是上一次的（默认"跟随全局设置"），
        # 界面显示错的还算小事，一存盘就把用户指定的 Java 冲掉了。
        self._fill_java_combo()

        self._loading = False
        self._sync_enabled()
        self._refresh_memory_bar()
        self._refresh_account()
        self._refresh_mods()
        self._refresh_java_hint()

    def _flush(self):
        """把界面上的值写回 versions.json

        空值不写（version_settings 会丢掉），所以"清空输入框" = 恢复成跟随默认。
        """
        if self._loading or not self._version:
            return
        overrides = {}

        java = self.java_combo.currentData() or ""
        if java:
            overrides["java_path"] = java
        if self.mem_custom.isChecked():
            overrides["min_memory"] = self.min_mem.value()
            overrides["max_memory"] = self.max_mem.value()
        for key, widget in (
            ("custom_info", self.info_edit),
            ("extra_game_args", self.game_edit),
            ("server_address", self.server_edit),
        ):
            text = widget.text().strip()
            if text:
                overrides[key] = text
        jvm = self.jvm_edit.toPlainText().strip()
        if jvm:
            overrides["extra_jvm_args"] = jvm

        version_settings.update(self.version_id, overrides)
        self.saved.emit()

    def refresh_from_global(self):
        """「设置」页改了全局默认：跟着重读一遍（跟随全局的那几项要立刻反映出来）"""
        if self._version is not None and not self._loading:
            self._load()

    def _on_changed(self, *_args):
        if self._loading:
            return
        self._save_timer.start()

    # ---------- 控件联动 ----------

    def _sync_enabled(self):
        """没选「自定义」时内存控件灰掉（但**仍然显示**最终生效的值）"""
        custom = self.mem_custom.isChecked()
        self.min_mem.setEnabled(custom)
        self.max_mem.setEnabled(custom)

    def _on_memory_mode_changed(self, *_args):
        self._sync_enabled()
        self._on_changed()

    def _on_min_changed(self, value: int):
        if self._loading:
            return
        if value > self.max_mem.value():
            self.max_mem.blockSignals(True)
            self.max_mem.setValue(value)
            self.max_mem.blockSignals(False)
            self.max_value.setText(f"{value} MB")
        self._refresh_memory_bar()
        self._on_changed()

    def _on_max_changed(self, value: int):
        if self._loading:
            return
        snapped = snap_memory(value)
        if snapped != value:
            self.max_mem.blockSignals(True)
            self.max_mem.setValue(snapped)
            self.max_mem.blockSignals(False)
            value = snapped
        if value < self.min_mem.value():
            self.min_mem.blockSignals(True)
            self.min_mem.setValue(value)
            self.min_mem.blockSignals(False)
        self.max_value.setText(f"{value} MB")
        self._refresh_memory_bar()
        self._on_changed()

    def _toggle_advanced(self):
        self.advanced_body.setVisible(not self.advanced_body.isVisible())
        self._refresh_advanced_toggle()

    def _refresh_advanced_toggle(self):
        arrow = "▾" if self.advanced_body.isVisible() else "▸"
        self.advanced_toggle.setText(f"{arrow}   {tr('高级选项')}")

    # ---------- 动态文字 ----------

    def _refresh_memory_bar(self):
        """画的是**最终会生效**的值（选了自定义就是这里的，否则是全局的）"""
        info = system_memory()
        heap = self.max_mem.value()
        overhead = jvm_overhead_mb(heap)
        self.memory_bar.set_values(info.total_mb, info.used_mb, heap, overhead)
        if info.ok:
            self.memory_legend.setText(tr(
                "物理内存 {total} GB · 系统已用 {used} GB · 游戏最多占用 {heap} GB"
                " · JVM 开销约 {overhead} GB",
                total=_gb(info.total_mb), used=_gb(info.used_mb),
                heap=_gb(heap), overhead=_gb(overhead)))
        else:
            self.memory_legend.setText(tr("读不出系统内存，这条只按你填的值画。"))

        self.mem_auto_hint.setText(tr(
            "「自动配置」还没做 —— 它会按装了哪些 Mod 和剩余内存动态算，"
            "先在下面自己拖。没选「自定义」时用的是启动器全局设置里的内存。"))

    def _refresh_account(self):
        account = self._account
        if account:
            self.account_label.setText(
                tr("离线登录 · {name}", name=account.get("name", "")))
        else:
            self.account_label.setText(tr("还没有档案"))
        self.login_hint.setText(tr(
            "现在只有离线登录（正版登录还没做）。离线档案按名字生成 UUID，"
            "和朋友联机时名字要对得上。"))

    def _refresh_java_hint(self):
        """Java 那一行下面写清楚**最终会用哪个**"""
        effective = version_settings.resolve(self.version_id, launcher_defaults())
        path = effective.get("java_path", "")
        version = self._version or {}
        major = version.get("java_major")
        if path:
            self.java_hint.setText(tr("将会使用（本版本指定）：{path}", path=path))
        elif major:
            self.java_hint.setText(
                tr("跟随全局设置；这个版本要求 Java {n}，启动时按它挑。", n=major))
        else:
            self.java_hint.setText(tr("跟随全局设置；版本没写 Java 要求，按默认挑。"))

    def _refresh_mods(self):
        version = self._version or {}
        has_loader = version.get("kind") == "loader" or bool(version.get("loader"))
        self.notice_card.setVisible(not has_loader)
        self.mods_placeholder.setVisible(has_loader)
        if has_loader:
            loader = (version.get("loader_label") or version.get("loader") or "")
            self.mods_placeholder.setText(tr(
                "这个版本装了 {loader}，但 Mod 列表还没做（下一步）。\n"
                "到时候会在这里列出 mods 文件夹里每个 Mod 的名字、版本和图标，"
                "不用联网。", loader=loader))
        else:
            self.mods_placeholder.setText("")

    # ---------- Java 扫描 ----------

    def _fill_java_combo(self):
        saved = version_settings.get(self.version_id).get("java_path", "")
        self.java_combo.blockSignals(True)
        self.java_combo.clear()
        self.java_combo.addItem(tr("跟随全局设置"), "")
        for info in self._javas:
            self.java_combo.addItem(f"Java {info.major} · {info.path}", info.path)
        # 存的那个 Java 现在扫不到了（卸载了/挪走了）也得出现在下拉里，
        # 不然一进这个页面就会被悄悄改成"跟随全局设置"
        if saved and self.java_combo.findData(saved) < 0:
            self.java_combo.addItem(tr("手动指定：{path}", path=saved), saved)
        self.java_combo.setCurrentIndex(max(0, self.java_combo.findData(saved)))
        self.java_combo.blockSignals(False)

    def _rescan_java(self):
        if self._java_scan is not None and self._java_scan.isRunning():
            return
        self.java_rescan_btn.setEnabled(False)
        self.java_rescan_btn.setText(tr("扫描中…"))
        self._java_scan = QuickJavaScanTask(scan_minecraft_dirs(), self, use_cache=False)
        self._java_scan.done.connect(self._on_java_scan_done)
        self._java_scan.start()

    def _on_java_scan_done(self, javas):
        self._javas = javas
        self.java_rescan_btn.setEnabled(True)
        self.java_rescan_btn.setText(tr("重新扫描"))
        self._fill_java_combo()
        self._refresh_java_hint()

    # ---------- 主题 / 语言 ----------

    def refresh_theme(self):
        self.memory_bar.refresh_theme()

    def retranslate(self):
        super().retranslate()
        # 箭头 + 文字是拼出来的；下面几条都带数字或路径，也是生成的
        self._refresh_advanced_toggle()
        self._refresh_memory_bar()
        self._refresh_account()
        self._refresh_java_hint()
        self._refresh_mods()
        self.isolate_hint.setText(tr(
            "还没接上。现在按版本文件夹里有没有 saves/mods 自动判断，"
            "这里显示的就是判断结果。"))
        self.title_hint.setText(tr(
            "还没接上。这个版本想单独用一个窗口标题的话，先把值写进"
            "「游戏参数」里（--title 还没验证过是否所有版本都吃）。"))
        self.shell_hint.setText(tr(
            "「内存管理」（自动挑 GC 参数）和「启动前执行命令」还是空壳 —— "
            "画出来占个位，功能后面补。"))
