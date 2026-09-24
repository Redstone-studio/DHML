"""启动页

结构调整：玩家名不再是一个可编辑的输入框。
旧代码里 main_window 会把当前账户名回填到 QLineEdit，但启动时读的又是输入框
里的文本 —— 用户可以随便改成非法名字，绕过 NewAccountDialog 的正则校验。
现在 --username / --uuid / --accessToken 全部来自 AccountManager，
所以这里把"当前档案"做成只读展示，从源头拆掉这个雷。

版式（2026-09 再调）：这页**只有右边那块启动面板**。
版本列表搬去了「版本」页 —— 同一份列表在两个页面各摆一个，跟"主界面和日志窗口
各显示一份日志"是同一个毛病，而且这页会被列表撑得很高。
当前版本由版本页的选中信号驱动（见 main_window 的接线），本页自己也扫一次
是为了冷启动时能按 config 里的 last_version 直接显示。

启动链路（v0.2.0）：
    core/java.py    按版本要求的 javaVersion.majorVersion 挑一个 Java
    core/launch.py  拼出完整参数列表（纯函数）
    ui/tasks.py     起进程、实时收日志
本页只负责把这三步串起来 + 显示状态。

文案：静态文字走 self.label()/self.button()；
面板上的元信息、扫描告警都是**生成**的，所以在 retranslate() 里整体重建。
"""

import time
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout
)

from core.accounts import type_label as account_type_label
from core.app_info import APP_NAME, APP_VERSION
from core.config import config
from core.i18n import system_locale, tr
from core.java import find_javas, pick_java, scan_minecraft_dirs
from core.launch import LaunchRequest, build_launch_plan, load_version, offline_uuid
from core.version_settings import version_settings
from core.versions import VersionScanner
from ui.dialogs.log_window import LogWindow
from ui.dialogs.version_settings_dialog import VersionSettingsDialog
from ui.tasks import LaunchTask, QuickJavaScanTask, RepairTask
from ui.translatable import TranslatableWidget

# 退出码非 0 且运行时间短于这个秒数，才算"秒退"（真出事了），
# 才值得弹窗。Minecraft 自己有时候正常退出也返回非 0，
# 一律弹窗会变成狼来了，用户看到堆栈也烦。
QUICK_EXIT_SECONDS = 20

# 版本 JSON 没写 javaVersion 时用这个（1.13 以前的版本都没有这个字段）
DEFAULT_JAVA_MAJOR = 8

# 日志行前缀。这是技术标记不是界面文案，不需要翻译
LOG_PREFIX = "[启动器] "  # noqa: i18n

# 启动面板宽度。太窄按钮排不下，太宽在 1280 的窗口里显得空
PANEL_WIDTH = 460


class LaunchButton(QFrame):
    """启动按钮：两行文字，两种字重（"启动游戏"大而粗，版本号小而细）

    为什么不用 QPushButton + HTML：**QPushButton 不排版富文本**。
    实测 `<b>启动游戏</b><br>1.20.4` 的 sizeHint 是 "1010 x 20" ——
    它按原始字符串当一行量宽度，drawText 再把富文本挤进那个矩形，结果是截断。
    所以自己拼：QFrame 负责背景/圆角/悬停（QSS 的 #LaunchButton 原样能用），
    两行 QLabel 负责两种字重。
    """

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LaunchButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(66)

        box = QVBoxLayout(self)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(1)

        self.title = QLabel()
        self.title.setObjectName("LaunchTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.title)

        self.subtitle = QLabel()
        self.subtitle.setObjectName("LaunchSubtitle")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.subtitle)

    def set_text(self, title: str, subtitle: str):
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        # 没有版本时不占位 —— 不然按钮里会留一条空白
        self.subtitle.setVisible(bool(subtitle))

    def mouseReleaseEvent(self, event):
        # QLabel 默认忽略鼠标事件，所以点在文字上也会冒泡到这儿
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class HomePage(TranslatableWidget):
    # 点了「选择版本」—— 请主窗口切到版本页（本页没有列表）
    versions_requested = pyqtSignal()

    def __init__(self, account_manager):
        super().__init__()
        self.account_manager = account_manager
        self._account = None
        self._version = None         # 当前版本（由版本页或自己的扫描给）
        self._task = None            # 当前这次启动
        # 显式的运行状态。不能靠 _is_running() 判断 —— _set_running(True)
        # 是在 task.start() 之前调的，那一刻 _task 还是旧的（首次是 None），
        # 会出现「按钮变红了但文字还是启动游戏」的错位。
        self._running = False
        self._javas = []             # Java 扫描结果（缓存）
        self._java_scan = None
        self._java_cache = {}       # 版本 id -> 合并后的 java_major
        self._started_at = None     # 这一局是什么时候起来的
        self._repair_task = None

        # 本页不显示列表，但要知道有哪些版本（面板要显示当前那个的信息）
        self.scanner = VersionScanner()

        layout = QVBoxLayout(self)
        # 右边和下边只留一点点：启动面板要"紧贴右下角"
        layout.setContentsMargins(32, 28, 12, 12)
        layout.setSpacing(6)

        layout.addWidget(self.label("启动游戏", "PageTitle"))
        layout.addWidget(self.label("选择版本和档案，然后启动", "PageSubtitle"))
        layout.addSpacing(12)

        # 扫描告警条（没有问题时隐藏）
        self.banner = QLabel()
        self.banner.setObjectName("WarningBanner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        layout.addWidget(self.banner)

        # 面板贴到右下角（用户要求）。所以右边和下边的边距留得很小，
        # 而 body 要吃掉剩下的整块高度 —— 不然它会被下面的空隙顶上去。
        body = QHBoxLayout()
        body.setSpacing(16)
        body.addStretch()
        body.addWidget(self._make_launch_panel(), 0,
                       Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        layout.addLayout(body, 1)

        # 日志窗口一开始就建好（不显示）：这样从第一行起都收得到。
        # 入口只有"启动游戏后自动打开日志窗口"那个开关（设置页），
        # 面板上不再放按钮 —— 这里是给玩家用的，日志是给开发者看的。
        self._log_window = LogWindow(self)
        self._log_window.stop_game.connect(self._stop_game)

        # Java 扫描有点慢（每个都要起一次 `java -version`），提前在后台跑好，
        # 等用户点启动时结果通常已经在了
        self._start_java_scan()
        self.reload_versions()

    # ---------- 启动面板 ----------

    def _make_launch_panel(self):
        card = QFrame()
        card.setObjectName("Card")
        card.setFixedWidth(PANEL_WIDTH)
        box = QVBoxLayout(card)
        box.setContentsMargins(22, 20, 22, 18)
        box.setSpacing(10)

        # 启动按钮：自己拼的两行按钮（见 LaunchButton），文案手工处理
        self.launch_btn = LaunchButton()
        self.launch_btn.clicked.connect(self._on_launch)
        box.addWidget(self.launch_btn)
        self._apply_launch_text()

        self.version_name = QLabel()
        self.version_name.setObjectName("VersionRowName")
        self.version_name.setWordWrap(True)
        box.addWidget(self.version_name)

        self.version_meta = QLabel()
        self.version_meta.setObjectName("VersionRowMeta")
        self.version_meta.setWordWrap(True)
        box.addWidget(self.version_meta)

        # 起不来的原因摆在这儿，别等用户点了启动才弹窗告诉他
        self.version_warn = QLabel()
        self.version_warn.setObjectName("BadgeWarn")
        self.version_warn.setWordWrap(True)
        self.version_warn.hide()
        box.addWidget(self.version_warn)

        self.account_label = QLabel()
        self.account_label.setObjectName("FieldLabel")
        self.account_label.setWordWrap(True)
        box.addWidget(self.account_label)

        box.addSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.choose_btn = self.button("选择版本")
        self.choose_btn.clicked.connect(self.versions_requested.emit)
        btn_row.addWidget(self.choose_btn)

        self.settings_btn = self.button("版本设置")
        self.settings_btn.clicked.connect(self._open_version_settings)
        btn_row.addWidget(self.settings_btn)
        btn_row.addStretch()
        box.addLayout(btn_row)

        box.addSpacing(4)

        # 状态行只显示**启动器自己**的消息（"[启动器] …"），游戏原始输出不进来 ——
        # 不然会变成一行跳动的英文。没有它的话，扫描 Java 那一两秒界面看着像卡住了。
        # 文字是动态给的，所以不放进自动绑定（否则切语言时会退回"就绪"，
        # 把当前进度弄丢）。切语言时由 retranslate() 重新贴一遍。
        self._status_text = tr("就绪")
        self.status_label = QLabel(self._status_text)
        self.status_label.setObjectName("StatusText")
        self.status_label.setWordWrap(True)
        box.addWidget(self.status_label)

        return card

    def set_version(self, version):
        """当前版本变了（版本页选中的，或者本页自己扫出来的）"""
        self._version = version
        self._update_panel()

    def _update_panel(self):
        version = self._version
        self.version_warn.hide()

        if not version:
            self.version_name.setText(tr("未找到本地版本"))
            self.version_meta.setText("")
            self.launch_btn.setEnabled(False)
            self.settings_btn.setEnabled(False)
            return

        self.launch_btn.setEnabled(True)
        self.settings_btn.setEnabled(True)
        self.version_name.setText(version["display_name"])
        self.version_name.setToolTip(str(version["path"]))
        self.version_meta.setText(self._meta_line(version))
        # 按钮上那行版本号也要跟着换
        self._apply_launch_text()

        problem = self._problem(version)
        if problem:
            self.version_warn.setText("⚠  " + problem)
            self.version_warn.show()

    def _meta_line(self, version) -> str:
        effective = self._effective(version)
        parts = [self._java_text(version)]
        memory = f"{tr('内存 {n} MB', n=effective['max_memory'])}"
        if version_settings.is_custom(version["id"], "max_memory"):
            memory += tr("（本版本）")
        parts.append(memory)
        if version["loader_label"]:
            parts.append(version["loader_label"])
        elif version["kind"] == "pack":
            parts.append(tr("整合包"))
        if version["isolated"]:
            parts.append(tr("版本隔离"))
        return "    ·    ".join(parts)

    def _effective(self, version) -> dict:
        """这个版本最终生效的启动参数：版本自己的设置 > 启动器默认

        默认值从 config 取（版本设置模块不去读 config，免得环形依赖）。
        """
        return version_settings.resolve(version["id"], {
            "java_path": config.get("java_path", ""),
            "min_memory": int(config.get("min_memory", 512)),
            "max_memory": int(config.get("max_memory", 2048)),
            "extra_jvm_args": "",
        })

    def _java_major(self, version):
        return version["java_major"] or self._merged_java_major(version) or DEFAULT_JAVA_MAJOR

    def _java_text(self, version) -> str:
        if not version["java_major"] and not self._merged_java_major(version):
            # 不是"读不到"，是那个年代还没有 javaVersion 这个概念。
            # 照实显示启动时会用的默认值，比一个"未知"有用。
            return tr("Java {n}（默认）", n=DEFAULT_JAVA_MAJOR)
        return f"Java {self._java_major(version)}"

    def _problem(self, version) -> str:
        """这个版本现在起不来 / 可能有问题的原因。没有就返回空串

        只做便宜检查（读一个 JSON + 在缓存好的 Java 列表里挑），
        每选一次版本都会跑一遍，所以不在这里算缺哪些库。
        """
        if not version["complete"]:
            return tr("缺少客户端 jar，这个版本启动不了")
        if self._javas:
            java, why = pick_java(self._javas, self._java_major(version),
                                  self._effective(version)["java_path"])
            if java is None:
                return why
        return ""

    def _open_version_settings(self):
        """改这个版本自己的启动设置（Java / 内存 / 额外 JVM 参数）"""
        if not self._version:
            return
        dialog = VersionSettingsDialog(self._version, self._javas, self)
        dialog.exec()
        if dialog.saved:
            # 内存、Java 变了，面板上那行元信息要跟着刷新
            self._update_panel()

    def _update_banner(self):
        errors = self.scanner.errors
        if not errors:
            self.banner.hide()
            return
        extra = tr("（另有 {n} 条）", n=len(errors) - 1) if len(errors) > 1 else ""
        self.banner.setText(
            tr("⚠  {n} 个版本读不了：{first}{extra}",
               n=len(errors), first=errors[0], extra=extra)
        )
        self.banner.show()

    # ---------- 档案 ----------

    def set_account(self, account):
        self._account = account
        if not account:
            self.account_label.setText(tr("未选择档案 —— 去「账户」页新建一个"))
            self.account_label.setToolTip("")
            return
        type_text = account_type_label(account.get("type"))
        self.account_label.setText(f"{account.get('name', '?')}    ·    {type_text}")
        self.account_label.setToolTip(tr("当前档案"))

    # ---------- Java ----------

    def _merged_java_major(self, version):
        """版本自己没写 javaVersion 时，去 inheritsFrom 链上找

        扫描器只读版本自己的 JSON（那样扫 60 个版本才够快），所以
        Fabric / NeoForge 这种"只写 inheritsFrom"的子版本会拿到 None，
        界面上就显示成「Java 未知」。这里按需补一次，结果缓存 ——
        每切换一次版本只多读一个 JSON。
        """
        vid = version.get("id")
        if vid in self._java_cache:
            return self._java_cache[vid]
        value = None
        try:
            path = Path(version["path"])
            merged = load_version(path.parent.parent, vid)
            value = (merged.get("javaVersion") or {}).get("majorVersion")
        except Exception:
            value = None
        self._java_cache[vid] = value
        return value

    def _start_java_scan(self):
        """后台扫一遍 Java。结果缓存起来，启动时直接用"""
        if self._java_scan is not None and self._java_scan.isRunning():
            return
        self._java_scan = QuickJavaScanTask(scan_minecraft_dirs(), self)
        self._java_scan.done.connect(self._on_java_scan_done)
        self._java_scan.start()

    def _on_java_scan_done(self, javas):
        self._javas = javas
        usable = [j for j in javas if j.usable]
        if usable:
            self.append_log(LOG_PREFIX + tr("检测到 {n} 个可用的 Java", n=len(usable)))
        # 扫完了才知道某个版本有没有 Java 可用，面板要跟着变
        self._update_panel()

    # ---------- 日志 ----------

    def append_log(self, text: str):
        self._log_window.append(text)
        if text.startswith(LOG_PREFIX):
            # 带上"[启动器] "前缀的才是我们自己的话，游戏原始输出不带
            self._status_text = text[len(LOG_PREFIX):]
            self.status_label.setText(self._status_text)

    def clear_log(self):
        self._log_window.clear()
        self._status_text = tr("就绪")
        self.status_label.setText(self._status_text)

    def open_log_window(self):
        """显示独立的日志窗口（已经开着就提到前面）"""
        self._log_window.show()
        self._log_window.raise_()
        self._log_window.activateWindow()
        return self._log_window

    def refresh_theme(self):
        """主窗口换主题时会被调用

        日志行的级别颜色是写进 QTextCharFormat 的，QSS 改不到，得让它自己重刷。
        """
        self._log_window.refresh_theme()

    def _stop_game(self):
        if self._is_running():
            self.append_log(LOG_PREFIX + tr("正在停止游戏…"))
            self._task.stop()

    # ---------- 语言切换 ----------

    def retranslate(self):
        super().retranslate()
        self._apply_launch_text()
        # 状态行是动态文字，重新贴一遍（语言变了，内容还是原来那条）
        self.status_label.setText(self._status_text)
        # 面板上的元信息是生成的（Java / 内存 / 版本隔离…），重建一遍。
        # 注意只需要重建，**不用重扫** —— 扫描结果是数据，不带语言。
        self._update_panel()
        self._update_banner()
        self.set_account(self._account)

    def _apply_launch_text(self):
        """两行：主文案（加粗放大）+ 版本号（细一点）"""
        main = tr("停止游戏") if self._running else tr("启动游戏")
        arrow = "\u25a0" if self._running else "\u25b6"
        name = (self._version or {}).get("display_name", "")
        self.launch_btn.set_text(f"{arrow}    {main}", name)

    # ---------- 版本 ----------

    def reload_versions(self):
        """扫一遍版本，挑出"当前版本"（面板要用它的完整信息）

        本页没有列表，扫描是为了冷启动时能按 config 里的 last_version
        直接显示；之后的选中由版本页的信号驱动（见 main_window 的接线）。
        """
        versions = self.scanner.scan()
        want = config.get("last_version", "")
        version = next((v for v in versions if v["id"] == want), None)
        if version is None and versions:
            version = versions[0]
        self.set_version(version)
        self._update_banner()

    def reset_scanner(self):
        """配置变更（游戏目录换了）后重建 scanner 并重扫"""
        self.scanner = VersionScanner()
        self.reload_versions()

    # ---------- 启动 ----------

    def _is_running(self) -> bool:
        return self._task is not None and self._task.isRunning()

    def _set_running(self, running: bool):
        self._running = running
        self.launch_btn.setObjectName("StopButton" if running else "LaunchButton")
        # 换了 objectName 必须重新 polish，否则 QSS 不重算
        self.launch_btn.style().unpolish(self.launch_btn)
        self.launch_btn.style().polish(self.launch_btn)
        self._apply_launch_text()
        # 跑起来以后别让人换版本 / 改设置：那些改的是"下一次"的参数，容易看糊涂
        self.choose_btn.setEnabled(not running)
        self.settings_btn.setEnabled(not running)

    def _build_plan(self, version, account):
        """把版本和档案拼成启动计划。返回 (plan, 错误文案)"""
        effective = self._effective(version)
        # 拿不到就退到 8。Fabric / NeoForge 的子版本不写 javaVersion，
        # 不合并的话会被 Fabric 以"需要 17+"拦下来。
        java_major = (version.get("java_major") or self._merged_java_major(version)
                      or DEFAULT_JAVA_MAJOR)
        java, message = self._pick_java(java_major, effective["java_path"])
        if java is None:
            return None, message
        self.append_log(message)

        # 离线账户的 UUID 是**名字的纯函数**，直接算，不用存的那份
        # （存的那份可能是早期版本算错的，见 core/accounts.py 的修复逻辑）
        if account.get("type") == "offline":
            uuid_value = offline_uuid(account.get("name", ""))
        else:
            uuid_value = account.get("uuid", "")

        version_dir = Path(version["path"])
        request = LaunchRequest(
            mc_dir=version_dir.parent.parent,   # <mc_dir>/versions/<id>
            version_id=version["id"],
            version_dir=version_dir,
            java_path=java.path,
            username=account.get("name", ""),
            uuid=uuid_value,
            min_memory=effective["min_memory"],
            max_memory=effective["max_memory"],
            extra_jvm_args=effective["extra_jvm_args"],
            launcher_name=APP_NAME,
            launcher_version=APP_VERSION,
            # 让游戏首次启动的默认语言跟着系统走
            locale=system_locale(),
        )
        return build_launch_plan(request), ""

    def _pick_java(self, required: int, preferred: str = ""):
        """按版本要求挑 Java，返回 (JavaInfo, 说明) 或 (None, 错误信息)"""
        if not self._javas:
            # 后台扫描还没跑完（或失败了），只能现场扫一次
            self.append_log(LOG_PREFIX + tr("正在查找 Java…"))
            self._javas = find_javas(scan_minecraft_dirs())

        java, why = pick_java(self._javas, required,
                              preferred or config.get("java_path", ""))
        if java is None:
            return None, why
        return java, LOG_PREFIX + f"{why} → {java.path}"

    def _on_launch(self):
        # 正在跑的时候这个按钮是「停止游戏」
        if self._is_running():
            self.append_log(LOG_PREFIX + tr("正在停止游戏…"))
            self._task.stop()
            return

        version = self._version
        if not isinstance(version, dict):
            return

        account = self.account_manager.get_current()
        if not account:
            QMessageBox.warning(self, tr("还没有档案"), tr("请先在「账户」页新建一个档案。"))
            return

        if not version["complete"]:
            QMessageBox.warning(
                self, tr("版本不完整"),
                tr("「{name}」缺少客户端 jar，无法启动。", name=version["display_name"])
                + "\n\n" + tr("请用 PCL / HMCL 重新安装该版本。")
            )
            return

        # 记住这次的版本，下次打开直接选中它
        config.set("last_version", version["id"])

        self.clear_log()
        self.append_log(LOG_PREFIX + tr("准备启动 {name}", name=version["display_name"]))
        try:
            plan, error = self._build_plan(version, account)
        except Exception as e:
            self.append_log(LOG_PREFIX + tr("拼装启动命令失败：{err}", err=f"{type(e).__name__}: {e}"))
            QMessageBox.critical(self, tr("启动失败"), f"{type(e).__name__}: {e}")
            return

        if plan is None:
            self.append_log(LOG_PREFIX + error)
            QMessageBox.warning(self, tr("找不到 Java"), error)
            return

        for warning in plan.warnings:
            self.append_log(LOG_PREFIX + "⚠ " + warning)

        if plan.missing_libraries:
            self.append_log(LOG_PREFIX + tr(
                "⚠ 本地缺 {n} 个库，游戏可能起不来：{first}",
                n=len(plan.missing_libraries), first=plan.missing_libraries[0]
            ))
            # 缺库基本必崩（1.12.2 的 Forge 就缺 maven-artifact）。
            # 与其让用户去别的启动器修，不如直接问一句帮他补上。
            if QMessageBox.question(
                self, tr("缺少必要的库"),
                tr("这个版本本地缺 {n} 个库，多半起不来。\n\n现在联网补全吗？",
                   n=len(plan.missing_libraries)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            ) == QMessageBox.StandardButton.Yes:
                self._start_repair(plan)
                return

        if config.get("show_log_window", True):
            self.open_log_window()

        self._set_running(True)
        task = LaunchTask(plan, self)
        def _on_started():
            self._started_at = time.monotonic()
            self.append_log(LOG_PREFIX + tr("游戏进程已启动"))

        task.started.connect(_on_started)
        task.log.connect(self.append_log)
        task.finished.connect(self._on_game_exit)
        task.failed.connect(self._on_launch_failed)
        self._task = task
        task.start()

    # ---------- 补全缺失的库 ----------

    def _start_repair(self, plan):
        self.launch_btn.setEnabled(False)
        self.choose_btn.setEnabled(False)
        self.append_log(LOG_PREFIX + tr("开始补全缺失的库…"))
        task = RepairTask(plan.mc_dir, plan.version_json, self)
        task.log.connect(self.append_log)
        task.done.connect(self._on_repair_done)
        self._repair_task = task
        task.start()

    def _on_repair_done(self, fixed: int, failed: list):
        self.launch_btn.setEnabled(True)
        self.choose_btn.setEnabled(True)
        self._repair_task = None
        if failed:
            self.append_log(LOG_PREFIX + tr(
                "还有 {n} 个库没补上：{first}", n=len(failed), first=failed[0]
            ))
        if fixed:
            self.append_log(LOG_PREFIX + tr("补全完成，重新启动…"))
            self._on_launch()      # 重拼一次命令（classpath 变了），直接再来
        else:
            self.append_log(LOG_PREFIX + tr("没有补上任何库，检查一下网络？"))

    def _on_game_exit(self, code: int):
        self._set_running(False)
        ran = (time.monotonic() - self._started_at) if self._started_at else None
        self._started_at = None
        self.append_log(LOG_PREFIX + tr("游戏已退出（退出码 {code}）", code=code))

        # 只有「刚起来就退了」才值得弹窗 —— 那才是真出事。
        # 玩了几分钟再退出、哪怕退出码不是 0，也只记日志不打扰用户：
        # Minecraft 自己有时就返回非 0，一律弹窗会变成狼来了。
        if code != 0 and ran is not None and ran < QUICK_EXIT_SECONDS:
            self.append_log(LOG_PREFIX + tr("启动后 {n} 秒就退出了，多半有问题", n=round(ran)))
            QMessageBox.warning(
                self, tr("游戏异常退出"),
                tr("退出码 {code}。最后几行日志：", code=code)
                + "\n\n" + (self._task.tail() or tr("（没有输出）"))
            )

    def _on_launch_failed(self, message: str):
        self._set_running(False)
        self.append_log(LOG_PREFIX + message)
        QMessageBox.critical(self, tr("启动失败"), message)
