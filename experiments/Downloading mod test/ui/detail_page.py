"""
详情页（mod 详情 + 版本列表）
"""
import re
import time
import requests
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer

from ui.workers.project_loader import ProjectLoader
from ui.widgets.detail_card import ModDetailCard
from core import mc_dir as mcd
from ui.widgets import appearance
from ui.widgets.collapsible_group import CollapsibleGroup
from ui.widgets.slide_in import StaggerReveal, SlideInRow
from ui.widgets.loading_bar import LoadingBar
from ui.widgets import anim_settings
from ui.widgets.anim_prefs import preset, preset_for_versions, scaled, speed_factor


def current_preset():
    """当前生效的动画参数（预设 + 速度档，和搜索页共用同一份设置）"""
    return scaled(preset(anim_settings.get_preset_key()),
                  speed_factor(anim_settings.get_speed_key()))


def current_version_preset():
    """组内"具体版本"卡片用哪套参数（同一风格，更短的时长）"""
    return scaled(preset_for_versions(anim_settings.get_preset_key()),
                  speed_factor(anim_settings.get_speed_key()))


def fmt_size(n: int) -> str:
    """字节 → 人看的大小（下载确认框里用）"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "未知"
    if n <= 0:
        return "未知"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"

# 每批渲染多少个版本分组。Fabric API 这种项目有 400 多个分组，
# 一次性全建出来会明显卡一下，而且大部分用户根本翻不到那么远。
GROUP_BATCH_SIZE = 12
# 离底部还有这么多像素就开始建下一批。30 ≈ 拖到底才建（只留一点余量）
LOAD_MORE_THRESHOLD = 30
# 详情页加载条至少显示这么久（毫秒）。比搜索页长：点进详情时用户是"在等页面"，
# 一闪而过会让人以为没反应
DETAIL_MIN_VISIBLE_MS = 550
# "内容不够一屏时自动补几批"的上限（理由见 search_page.py 里的同名常量）
MAX_AUTO_FILL = 1
# 布局稳定下来要等多久（毫秒）：滚动条的 rangeChanged / 视口高度在这一小段
# 时间里还会变，必须等它不再变再判断
LAYOUT_SETTLE_MS = 120
# 每帧最多花多少毫秒造分组控件（超过就交给下一帧，理由见 search_page.py）
FRAME_BUDGET_MS = 12.0
# 每帧最多建几个分组（条数硬上限，理由见 search_page.py）
FRAME_MAX_ITEMS = 6


HEADERS = {"User-Agent": "Mosslight-Launcher/1.0"}
LOADER_ORDER = {"neoforge": 0, "fabric": 1, "forge": 2, "quilt": 3}
LOADER_LABEL = {
    "neoforge": "NeoForge",
    "fabric": "Fabric",
    "forge": "Forge",
    "quilt": "Quilt",
}


def parse_version_tuple(v: str):
    nums = re.findall(r"\d+", v)
    if nums:
        return tuple(int(x) for x in nums)
    return (0,)


def group_versions(versions: list) -> list:
    groups = {}
    for v in versions:
        vtype = v.get("version_type", "release")
        is_prerelease = vtype in ("beta", "alpha")
        for gv in v.get("game_versions", []):
            for loader in v.get("loaders", []):
                key = (gv, loader, is_prerelease)
                groups.setdefault(key, []).append(v)

    result = []
    for (gv, loader, is_prerelease), vs in groups.items():
        loader_label = LOADER_LABEL.get(loader, loader.capitalize())
        title = f"{loader_label} {gv}"
        if is_prerelease:
            title += " 预览版"
        vs_sorted = sorted(vs, key=lambda x: x.get("date_published", ""), reverse=True)
        sort_key = (
            tuple(-x for x in parse_version_tuple(gv)),
            1 if is_prerelease else 0,
            LOADER_ORDER.get(loader, 99),
        )
        result.append({
            "key": (gv, loader, is_prerelease),
            "title": title,
            "versions": vs_sorted,
            "sort_key": sort_key,
            # 下载时要靠这两个定位版本文件夹（见 core/mc_dir.py）
            "game_version": gv,
            "loader": loader,
        })
    result.sort(key=lambda x: x["sort_key"])
    return result


class DetailPage(QWidget):
    back_requested = pyqtSignal()

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 顶部返回
        top = QHBoxLayout()
        back_btn = QPushButton("← 返回")
        back_btn.setFixedHeight(32)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2c30; color: #e9e9ec;
                border: 1px solid #3a3c42; border-radius: 6px;
                padding: 0 14px; font-size: 12px;
            }
            QPushButton:hover { background-color: #34363a; border-color: #5ec269; }
        """)
        back_btn.clicked.connect(self.back_requested.emit)
        top.addWidget(back_btn)
        top.addStretch()
        layout.addLayout(top)

        # ===== 主介绍：**固定不滚动** =====
        # 以前详情卡和版本列表挤在同一个滚动区里，往下翻的时候图标、下载量、
        # 「转到 Modrinth」这些全被滚走了。现在只让下面的版本列表滚。
        self.detail_card = ModDetailCard()
        layout.addWidget(self.detail_card)

        # ===== 版本列表：只有它滚 =====
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.container = QWidget()
        self.container.setStyleSheet("background: transparent;")
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setSpacing(10)
        # 左右留 14px：分组卡片 hover 时的绿色描边会被滚动区裁掉
        self.container_layout.setContentsMargins(14, 2, 14, 12)
        self.scroll.setWidget(self.container)
        # 版本列表吃掉剩余的全部高度（详情卡是固定高的，所以不会被它挤没）
        layout.addWidget(self.scroll, 1)

        self.groups_container = QWidget()
        self.groups_layout = QVBoxLayout(self.groups_container)
        self.groups_layout.setSpacing(6)
        self.groups_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.addWidget(self.groups_container)

        # 兜底按钮常驻在分组区最后（隐藏状态），内容不够一屏时才显示
        self._more_btn = None
        self._needs_load_more = False
        self._auto_fills = 0
        self.container_layout.addStretch()
        self._loader = None
        # 版本分组的入场动画调度器（见 ui/widgets/slide_in.py），
        # 用哪一档由「动效」设置决定
        _p = current_preset()
        self.stagger = StaggerReveal(
            self, interval=_p.interval, initial_offset=_p.offset,
            direction=_p.direction, style=_p.style,
            overshoot=_p.overshoot, bounce_ratio=_p.bounce_ratio,
        )

        # 分批渲染的状态
        self._all_groups = []       # 服务端给的全部分组（按需一批批建控件）
        self._shown = 0             # 已经建出来多少个
        self._rows = []
        # 请求令牌：每次 load 递增，回调里核对，丢弃过期结果（防"上一个请求晚到"）
        self._load_token = 0
        # 当前显示的是哪个 mod（同一个再进来时不清列表，见 load()）
        self._current_mod = None
        # 当前这个项目的类型（mod / shader / resourcepack…），决定下载进哪个子目录
        self._project_type = "mod"

        # 加载条：详情要连打"项目"和"版本"两个接口，等待感比搜索更明显。
        # 最短显示时间给到 550ms —— 数据本地有缓存、或者镜像很快的时候，
        # 请求可能几十毫秒就回来了，不兜一下的话这条会一闪而过，等于没有。
        self.loading_bar = LoadingBar(min_visible_ms=DETAIL_MIN_VISIBLE_MS)
        layout.addWidget(self.loading_bar)

        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_load_more)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._on_range_changed)

        # "内容够不够一屏"必须等布局稳定了再看 —— rangeChanged 第一次触发时
        # 视口高度还只有几十像素，那时候判断会把"一屏放得下"错判成"不够一屏"，
        # 于是连着自动补好几批（实测：滚一次就把 90 多个分组全建出来了）。
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(LAYOUT_SETTLE_MS)
        self._check_timer.timeout.connect(self._check_short_content)

    def load(self, mod_id: str):
        """载入一个 mod 的详情

        ⚠️ **必须能扛住"上一个请求晚到"**：用户点进 A、马上返回再点 B 时，
        A 的请求可能比 B 晚回来（各自要打两次 HTTP，1~3 秒）。
        原来没做防护，A 的结果回来就把 B 的界面覆盖成 A 了 —— 表现为
        "点 Sodium 却显示 Fabric API"，而且加载条还在转（因为 B 的结果早到了、
        加载条已经收过一次）。实测踩过。

        做法：每次 load 递增一个令牌，回调里核对令牌，对不上就整个丢掉。
        比对 mod_id 更稳 —— 用户可能连点同一个 mod 两次。

        **同一个 mod 再次进入时不清列表**：数据还是那份，清掉再等 1~3 秒拉回来
        会让用户以为"列表加载出来后又丢了"（实测踩过）。保留旧的先看着，
        新数据回来时 _on_loaded 会自己清一次再重建。
        """
        same_mod = (mod_id == self._current_mod)
        self._current_mod = mod_id

        # 先叫停上一轮的动画，避免定时器打到马上要被删掉的分组上
        self.stagger.clear()
        # 换 mod 才清列表；同一个 mod 再进来先留着旧的（见下面说明）
        if not same_mod:
            self._clear_group_list()

        self.loading_bar.show_state("正在加载项目详情…")

        self._load_token += 1
        token = self._load_token

        loader = ProjectLoader(mod_id)
        loader.loaded.connect(lambda p, v, t=token: self._on_loaded(p, v, t))
        loader.failed.connect(lambda mid, t=token: self._on_failed(mid, t))
        self._loader = loader
        loader.start()

    def _clear_group_list(self):
        """把版本列表清空（控件、状态、滚动位置一起）"""
        self.stagger.clear()
        while self.groups_layout.count():
            item = self.groups_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._all_groups = []
        self._shown = 0
        self._rows = []
        self._auto_fills = 0
        self._check_timer.stop()
        self._hide_more_btn()
        self.scroll.verticalScrollBar().setValue(0)

    def prefetch(self, hit: dict):
        """用搜索结果先把卡片填上（详情接口回来之前先有个正确的样子）

        见 ui/widgets/detail_card.py 的 prefetch 说明。
        顺便记下项目类型 —— 下载时靠它决定进 mods / shaderpacks / resourcepacks
        （搜索结果里有这个字段，详情接口返回的 project 里也有，但那时候
        用户可能已经点了下载，所以在这就记下来）。
        """
        self._project_type = (hit.get("project_type") or "mod").lower()
        self.detail_card.prefetch(hit)

    def _is_current(self, token) -> bool:
        return token == self._load_token

    def _on_failed(self, mod_id: str, token=None):
        if not self._is_current(token):
            return          # 过期请求的失败，别弹框打扰用户
        self.loading_bar.hide_now("加载失败")
        QMessageBox.warning(self, "未找到", f"未找到 mod: {mod_id}")

    def _on_loaded(self, project: dict, versions: list, token=None):
        if not self._is_current(token):
            return          # 过期请求的结果，直接丢（这就是那个覆盖 bug 的修法）
        self.detail_card.load(project)
        groups = group_versions(versions)

        # ⚠️ **渲染新数据前必须清一次**。
        # 同一个 mod 再进来时 load() 故意保留旧列表（免得闪空），
        # 但如果这里不清，新分组会**追加**在旧的后面 —— 实测每次进入
        # 分组数翻倍（12 → 24 → 48）。所以"保留旧的"和"替换成新的"要配合好：
        # 旧的留到这一刻，重建前丢掉。
        self._clear_group_list()

        if not groups:
            self.loading_bar.hide_now("没有可用版本")
            lbl = QLabel("没有可用版本")
            lbl.setStyleSheet("color: #888; padding: 20px;")
            self.groups_layout.addWidget(lbl)
            return

        # 数据到了：先收起加载条，再一批批把分组滑进来
        self.loading_bar.hide_now(f"共 {len(groups)} 个版本分组")
        self._all_groups = groups
        self._load_more_groups()

    # ---------- 分批渲染 ----------

    def refresh_card_style(self):
        """卡片透明度改了以后，把已建出来的版本分组刷新一遍"""
        for row in self._rows:
            for group in row.findChildren(CollapsibleGroup):
                group.refresh_style()

    def _load_more_groups(self):
        """再建一批分组控件（**按帧预算分批**，理由见 search_page.py 的 FRAME_BUDGET_MS）

        一个分组要建它的折叠按钮 + 组内所有版本卡片，12 个一起建同样会顿一下。
        """
        if self._shown >= len(self._all_groups):
            return
        batch = self._all_groups[self._shown:self._shown + GROUP_BATCH_SIZE]
        # 组内卡片用同一风格（更短一档），展开分组时按这套参数滑入
        vp = current_version_preset()
        index = 0

        def iterate() -> bool:
            nonlocal index
            start = time.monotonic()
            made = 0
            while index < len(batch) and made < FRAME_MAX_ITEMS:
                g = batch[index]
                gw = CollapsibleGroup(
                    g["title"], g["versions"],
                    slide_duration=vp.duration, slide_offset=vp.offset,
                    slide_interval=vp.interval, slide_style=vp.style,
                    slide_direction=vp.direction, slide_overshoot=vp.overshoot,
                    slide_bounce_ratio=vp.bounce_ratio, slide_ease=vp.ease,
                    game_version=g["game_version"], loader=g["loader"],
                )
                gw.download_requested.connect(self._download_version)
                # 建一个就入一个布局（和搜索页同理：别攒到最后一次性插）
                row = self.stagger.add(gw)
                self.groups_layout.addWidget(row)
                self._rows.append(row)
                self._shown += 1
                index += 1
                made += 1
                if (time.monotonic() - start) * 1000 >= FRAME_BUDGET_MS:
                    break
            if index < len(batch):
                QTimer.singleShot(0, iterate)
                return True
            self.stagger.start()
            if self._shown >= len(self._all_groups):
                self._hide_more_btn()      # 全建完了，按钮收起来
            return False

        iterate()

    def _at_bottom(self) -> bool:
        """是不是已经拖到底了（留 LOAD_MORE_THRESHOLD 像素余量）"""
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - LOAD_MORE_THRESHOLD

    def _maybe_load_more(self, *_):
        """**拖到底**才建下一批（用户主动滚，不设上限）"""
        self._needs_load_more = False
        if self._shown >= len(self._all_groups):
            return
        if self._at_bottom():
            self._auto_fills = 0
            self._load_more_groups()

    def _on_range_changed(self, _min: int, max_value: int):
        """内容高度变了 → 防抖一下再判断要不要补

        这里**只**处理"内容不够一屏、滚动条拉不动"那种卡死
        （光靠滚动永远触发不了加载）。判断放在 _check_short_content 里。
        """
        self._check_timer.start()

    def _check_short_content(self):
        """布局稳定后：内容真的不够一屏吗？"""
        if self._shown >= len(self._all_groups):
            return
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() > bar.height():
            return          # 能滚，交给滚动加载
        if self._auto_fills >= MAX_AUTO_FILL:
            self._show_more_btn()
            return
        self._auto_fills += 1
        self._load_more_groups()

    def resizeEvent(self, event):
        """窗口变大后重新判一次"要不要补" """
        super().resizeEvent(event)
        self._auto_fills = 0
        self._check_timer.start()

    # ---------- 末尾的「加载更多」兜底按钮 ----------

    def _make_more_btn(self):
        from PyQt6.QtWidgets import QPushButton
        btn = QPushButton("加载更多版本分组")
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
        btn.clicked.connect(self._load_more_groups)
        return btn

    def _show_more_btn(self):
        if self._more_btn is None:
            self._more_btn = self._make_more_btn()
            self.container_layout.addWidget(self._more_btn)
            self._needs_load_more = True
        self._more_btn.setVisible(True)

    def _hide_more_btn(self):
        self._needs_load_more = False
        if self._more_btn is not None:
            self._more_btn.setVisible(False)

    def _download_version(self, version: dict, game_version: str = "",
                          loader: str = ""):
        """下载一个版本的主文件

        流程：确认 → 算出落点 → 「另存为」确认路径 → 交给 DownloadWindow。

        ⚠️ 真正的下载**不在主线程**做（交给 DownloadWindow 里的后台线程）。
        原来是在主线程 `requests.get(..., stream=True)` 一直收完 ——
        界面在这期间整个冻住（跟第 1 条坑同一个毛病），而且没有任何进度。

        ⚠️ 落点是**算出来的**，不是写死 `<游戏目录>/mods`：
        照 PCL2/HMCL 的目录结构，mod 要进
        `<游戏目录>/versions/<游戏版本>-<加载器>/mods`（版本隔离）。
        解析规则和内嵌的坑见 core/mc_dir.py。
        """
        primary = None
        for f in version.get("files", []):
            if f.get("primary"):
                primary = f
                break
        if not primary and version.get("files"):
            primary = version["files"][0]

        if not primary:
            QMessageBox.warning(self, "错误", "没有可下载的文件")
            return

        filename = primary["filename"]
        url = primary["url"]
        size = int(primary.get("size") or 0)
        ptype = self._project_type or "mod"

        mc = appearance.get_mc_dir()
        target, why = mcd.target_dir(mc, game_version, loader, ptype)
        if target is None:
            QMessageBox.warning(
                self, "还没有游戏目录",
                "要把 mod 下到游戏里，得先知道游戏装在哪。\n\n"
                "点工具栏「背景」→「游戏目录」→ 浏览，选到 .minecraft 那一层。")
            return

        reply = QMessageBox.question(
            self, "确认下载",
            f"文件: {filename}\n大小: {fmt_size(size)}\n\n"
            f"保存到: {target}\n（{why}）\n\n要下载吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 「另存为」：预填到算好的位置，用户想改还能改。
        # 目录先建出来 —— 不然对话框不会跳进去（目标不存在时它会停在别处）。
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "建不了目录", f"{target}\n\n{e}")
            return

        ext = Path(filename).suffix.lower() or ".jar"
        label = mcd.kind_for(ptype)
        chosen, _ = QFileDialog.getSaveFileName(
            self, "选择保存位置", str(target / filename),
            f"{label} 文件 (*{ext});;所有文件 (*)")
        if not chosen:
            return

        chosen = Path(chosen)
        save_dir, filename = chosen.parent, chosen.name

        from core.download import DownloadTask
        from ui.dialogs.download_window import DownloadWindow

        task = DownloadTask(url=url, filename=filename, save_dir=save_dir,
                            size=size, kind=label)
        window = self._download_window()
        window.enqueue([task])
        window.show()
        window.raise_()
        window.activateWindow()

    def _download_window(self):
        """复用同一个下载窗口（关掉之后再点会重新建）"""
        win = getattr(self, "_dl_window", None)
        if win is None or not win.isVisible():
            from ui.dialogs.download_window import DownloadWindow
            win = DownloadWindow(self.window())
            self._dl_window = win
        return win
