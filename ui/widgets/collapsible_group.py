"""折叠分组（下载页第一页用）

    ┌────────────────────────────────────────────┐
    │ 正式版  103                            ⌄  │  ← 点整条展开/收起
    ├────────────────────────────────────────────┤
    │ ▣ 1.20.4     发布于 2023-12-07 12:00       │
    │ ▣ 1.20.3     发布于 2023-12-01 12:00       │
    │ ...                                        │
    │            [ 加载更多 ]                    │
    └────────────────────────────────────────────┘

两个关键设计：

1. **懒构建**：`set_items()` 只存数据；**展开时**才建行，而且一次只建一页
   （`PAGE_SIZE`）。预览版有 744 个，全铺出来界面会卡死。
2. **折叠时不占高度**：收起就是把 body 隐藏，已经建好的行留着（再展开不用重建）；
   数据换了才清空重建。

行样式复用版本列表那套（`#VersionRowName` / `#VersionRowMeta`），
所以观感跟「版本管理」页一致。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
)

from core.i18n import tr
from ui.icons import version_pixmap, screen_dpr
from ui.translatable import TranslatableWidget

PAGE_SIZE = 50          # 一次铺多少行（滚到底再加载下一批）
ICON_SIZE = 28


def format_release_time(raw: str) -> str:
    """`2026-09-15T11:23:00+00:00` → `2026-09-15 11:23`

    不换算时区：清单里的时间就是 Mojang 发布时写的那串，PCL 也是照原样显示。
    解析不出来就原样返回（宁可难看也别显示成空白）。
    """
    text = str(raw or "")
    if len(text) >= 16 and text[10] == "T":
        return "%s %s" % (text[:10], text[11:16])
    return text


class RemoteVersionRow(QFrame):
    """一行远程版本：图标 + 名字 + 发布时间"""

    clicked = pyqtSignal(dict)

    def __init__(self, entry: dict, dpr: float = 1.0, parent=None):
        super().__init__(parent)
        self.entry = entry
        # 这行是分组里的第几个（父控件回填，用它反查"选中的是哪一行"）
        self.item_index = -1
        self._picked = False
        # ⚠️ 一开始就把 property 置上（不是等 set_picked() 时才置）：
        # QSS 那边读不到属性时值是 None，虽然也不匹配 `[picked="true"]`，
        # 但"有的行有属性、有的行没有"排查起来很烦
        self.setProperty("picked", False)
        self.setObjectName("RemoteVersionRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)

        icon = QLabel()
        icon.setObjectName("BadgeIcon")
        icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        pix = version_pixmap(entry, ICON_SIZE, dpr)
        if pix is not None:
            icon.setPixmap(pix)
        row.addWidget(icon)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        name = QLabel(entry.get("display_name") or entry.get("id", ""))
        name.setObjectName("VersionRowName")
        text.addWidget(name)
        stamp = format_release_time(entry.get("release_time", ""))
        if entry.get("latest_kind"):
            # 「最新版本」卡里那两行：显示成「最新正式版，发布于 …」
            meta_text = tr("{kind}，发布于 {time}",
                           kind=tr(entry["latest_kind"]), time=stamp)
        elif entry.get("april_fools_year"):
            # 愚人节版显示年份（清单里它们的发布时间意义不大）
            meta_text = tr("{year} 年愚人节版", year=entry["april_fools_year"])
        else:
            meta_text = tr("发布于 {time}", time=stamp)
        meta = QLabel(meta_text)
        meta.setObjectName("VersionRowMeta")
        text.addWidget(meta)
        row.addLayout(text, 1)

    def set_picked(self, picked: bool):
        """标成"用户选的就是这一行"

        ⚠️ 只改**动态属性** + unpolish/polish，颜色交给 QSS
        （`#RemoteVersionRow[picked="true"]`）；代码里写死颜色在浅色主题下会瞎。
        """
        picked = bool(picked)
        if self._picked == picked:
            return
        self._picked = picked
        self.setProperty("picked", picked)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def is_picked(self) -> bool:
        return self._picked

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.entry)
        super().mouseReleaseEvent(event)


class CollapsibleGroup(TranslatableWidget):
    """一个可折叠的版本分组

    信号：
        item_clicked(entry)     点了一行（页面据此进第二页）
        item_activated(entry, i) 点了一行，带上它是第几个 —— 安装选项页用它
                                 判"用户选中了哪个加载器"（见 install_options）
        header_clicked()        **锁着的时候**点了标题条（见 `set_locked`）
        expanded_changed(bool)  展开状态变了（页面可以按需拉数据）
    """

    item_clicked = pyqtSignal(dict)
    expanded_changed = pyqtSignal(bool)
    # 用户在某一行上**点了一下**（`(这行的数据, 第几个)`）—— 父控件拿它判
    # "用户选中了哪个加载器"，用来锁掉其他加载器（见 install_options）
    item_activated = pyqtSignal(object, int)
    # 锁定态下点了标题：不是"展开"，而是"我要改用这个"（见 `_on_header_click`）
    header_clicked = pyqtSignal()

    def __init__(self, title: str, expanded: bool = False, parent=None):
        super().__init__(parent)
        self._items = []
        self._hint = ""             # 没有版本时显示的那句话
        self._built = 0             # 已经建了多少行
        self._row_widgets = []      # 已经建出来的行控件（标"已选"要用）
        self._expanded = False
        self._dpr = screen_dpr(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 标题条：整条可点
        #
        # ⚠️ 标题条是一个**容器**（不是直接的按钮）：左边是标题、右边可以有一个
        # 副标题。副标题是**可选的**，不传就完全不占位 ——
        # 下载页第一页那个「正式版 103」不需要它，别让它白占一块。
        #
        # ⚠️ 按钮铺满整条、标题和副标题都**不吃鼠标事件**（透明），
        # 这样点框里任何地方都能展开/收起（副标题上点也能点）。
        self.header = QFrame()
        self.header.setObjectName("CollapsibleHeader")
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)

        self.header_btn = QPushButton()
        self.header_btn.setObjectName("CollapsibleHeaderButton")
        self.header_btn.setCheckable(True)
        self.header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_btn.clicked.connect(self._on_header_click)
        header_row.addWidget(self.header_btn, 1)

        # 标题文字画在按钮上（`_refresh_header` 里 setText）；
        # 副标题单独一个 QLabel，右对齐
        self.header_note = QLabel()
        self.header_note.setObjectName("CollapsibleHeaderNote")
        self.header_note.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.header_note.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.header_note.setVisible(False)
        header_row.addWidget(self.header_note, 0)
        # 点副标题也当点标题条
        self.header_btn.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        # 「锁定」状态（选了别的加载器时用）：不能点、不能展开、外观灰掉。
        # ⚠️ 用 `setProperty("locked", True)` + QSS 属性选择器上色，
        # **不在代码里写死颜色**（浅色主题会瞎）。
        self._locked = False
        # 「当前选中的就是这组」（加载器互斥里被选中的那个）：描边用强调色，
        # 一眼看出"现在装的是它"
        self._selected = False
        outer.addWidget(self.header)

        # 内容区（默认收起）
        self.body = QWidget()
        self.body.setObjectName("CollapsibleBody")
        self.body_box = QVBoxLayout(self.body)
        self.body_box.setContentsMargins(6, 2, 6, 8)
        self.body_box.setSpacing(1)
        outer.addWidget(self.body)

        self.empty_hint = QLabel()
        self.empty_hint.setObjectName("HintText")
        self.body_box.addWidget(self.empty_hint)

        self.more_btn = QPushButton()
        self.more_btn.setObjectName("LinkButton")
        self.more_btn.clicked.connect(self.load_more)
        self.body_box.addWidget(self.more_btn)

        self._title = title
        self._refresh_header()
        self.set_expanded(expanded)

    # ---------- 数据 ----------

    def set_items(self, items, hint: str = ""):
        """换一批数据（会清掉已经建好的行）"""
        self._hint = hint
        self._items = list(items or [])
        self._clear_rows()
        self._built = 0
        self._refresh_header()
        if self._expanded:
            self._build_page()

    def count(self) -> int:
        return len(self._items)

    def built_count(self) -> int:
        return self._built

    # ---------- 展开 / 收起 ----------

    def is_expanded(self) -> bool:
        return self._expanded

    def toggle(self):
        self.set_expanded(not self._expanded)

    def _on_header_click(self):
        """点了标题条：锁着的时候发 `header_clicked`，否则就是普通的展开/收起

        ⚠️ **锁死 ≠ 死路**（用户 2026-09 定的）：灰掉的那几组点标题=
        "我要改成这个加载器"，由父控件去换选择。所以锁着时按钮**不禁用**，
        只是不再展开 —— 禁用的话用户点错一次就得返回重来。
        """
        if self._locked:
            self.header_clicked.emit()
            return
        self.toggle()

    def set_expanded(self, expanded: bool):
        expanded = bool(expanded)
        # ⚠️ 这里**不能**用 self.body.isVisible() 做判断：父窗口还没显示时它
        # 永远是 False，构造时那次 set_expanded(False) 会被误判成"已经是收起
        # 状态"直接返回，body 就永远没被隐藏 —— 表现是收起状态下露出一个空框
        # （里面那个还没设文字的「加载更多」按钮）。只用 _expanded 判断就对了。
        if expanded == self._expanded:
            self.body.setVisible(expanded)
            self.header_btn.setChecked(expanded)
            return
        self._expanded = expanded
        self.header_btn.setChecked(expanded)
        self.body.setVisible(expanded)
        if expanded and self._built == 0:
            # ★ 展开时才建行，而且**只建一次**：收起再展开时已经有行了，
            # 再建一页会让行数一直涨（第一版就是这么错的，测试抓到了 200→250）
            self._build_page()
        self._refresh_header()
        self.expanded_changed.emit(expanded)

    # ---------- 行 ----------

    def _clear_rows(self):
        """删掉所有已建的行（保留 empty_hint / more_btn）"""
        for i in reversed(range(self.body_box.count())):
            item = self.body_box.itemAt(i)
            widget = item.widget()
            if widget is not None and widget not in (self.empty_hint, self.more_btn):
                self.body_box.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
        # ⚠️ 列表也要清：留着的话 `set_picked_index()` 会去碰已经
        # deleteLater 掉的控件（"wrapped C/C++ object has been deleted"）
        self._row_widgets = []

    def _build_page(self):
        """再建一页行（PAGE_SIZE 个）"""
        if not self._items:
            return
        end = min(len(self._items), self._built + PAGE_SIZE)
        for entry in self._items[self._built:end]:
            index = self._built
            row = RemoteVersionRow(entry, self._dpr)
            row.item_index = index
            row.clicked.connect(self.item_clicked.emit)
            # 带序号的另一份（安装选项页靠它记住"选的是第几个版本"）
            row.clicked.connect(
                lambda _e, e=entry, i=index: self.item_activated.emit(e, i))
            # 插在 empty_hint 之后、more_btn 之前
            self.body_box.insertWidget(self.body_box.indexOf(self.more_btn), row)
            self._row_widgets.append(row)
            self._built += 1
        self._refresh_footer()

    def load_more(self):
        """「加载更多」：再铺一页"""
        self._build_page()

    # ---------- 已选中的那一行 ----------

    def set_picked_index(self, index):
        """把第 `index` 行标成"已选"、其余取消（`-1` = 都不选）

        ⚠️ 只认**已经建出来**的行：还没铺到的行不存在，`picked_index()`
        这时返回 `-1`。调用方（install_options）自己另存着选中的索引，
        不靠这个反查。
        """
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = -1
        for row in self._row_widgets:
            row.set_picked(row.item_index == index)

    def picked_index(self) -> int:
        """当前标着"已选"的是第几行（没有就是 -1）"""
        for row in self._row_widgets:
            if row.is_picked():
                return row.item_index
        return -1

    # ---------- 文字 ----------

    def set_note(self, text: str):
        """标题右边的**副标题**（可选：传空串就整块收起来、不占位）

        用途：把"最新版 0.19.5（共 253 个）"这种提示**塞进同一个框里**，
        而不是在框外面单独占一行 —— 框外一行 + 框内一行看着散
        （用户 2026-09 提的）。
        它**不吃鼠标事件**，所以在副标题上点也能展开/收起。
        """
        text = (text or "").strip()
        self.header_note.setText(text)
        self.header_note.setVisible(bool(text))

    def note(self) -> str:
        """当前副标题（测试/排查用）

        ⚠️ 别用 `isVisible()` 判空：父窗口还没 `show()` 时它永远是 False
        （离屏测试里就是这样），会把有字的说成空的 —— 跟 `set_expanded()`
        里那个不能信 `body.isVisible()` 是同一个坑。
        """
        return self.header_note.text()

    # ---------- 锁定 ----------

    def set_locked(self, locked: bool, note: str = "", tooltip: str = ""):
        """把这个分组标成"现在不是它"（灰掉、收起来、副标题说清为什么）

        「选了 Forge 就不能同时选 Fabric」就靠它，外观由 QSS 的
        `[locked="true"]` 负责。
        ⚠️ **锁着不等于点不动**：标题按钮**不禁用**，点它会发 `header_clicked`
        （父控件据此改选），只是不再展开 —— 见 `_on_header_click`。
        `note` / `tooltip` 顺手把"为什么灰"和"点一下会怎样"写清楚。
        """
        locked = bool(locked)
        if self._locked != locked:
            self._locked = locked
            # 锁上时把它收起来（留着展开的话视觉上像"还能选"）
            if locked and self._expanded:
                self.set_expanded(False)
            # ⚠️ 改了 property 必须 unpolish/polish 一遍，QSS 才会重新匹配
            for w in (self.header, self.header_btn, self.header_note):
                w.setProperty("locked", locked)
                w.style().unpolish(w)
                w.style().polish(w)
            self.header.update()
        if note:
            self.set_note(note)
        self.header.setToolTip(tooltip or "")
        self.header_btn.setToolTip(tooltip or "")
        return self._locked

    def is_locked(self) -> bool:
        return self._locked

    def set_selected(self, selected: bool):
        """标成"当前选中的那一组"（外观走 QSS 的 `[selected="true"]`）"""
        selected = bool(selected)
        if self._selected == selected:
            return
        self._selected = selected
        for w in (self.header, self.header_btn, self.header_note):
            w.setProperty("selected", selected)
            w.style().unpolish(w)
            w.style().polish(w)
        self.header.update()

    def is_selected(self) -> bool:
        return self._selected

    def _refresh_header(self):
        arrow = "\u25be" if self._expanded else "\u25b8"      # ▾ / ▸
        label = tr(self._title)
        # ⚠️ 没版本时**别显示 0** —— 「NeoForge 0」看着像"查到了 0 个"，
        # 其实是"我们还没做"，标题上那个 0 纯属误导（用户 2026-09 指出）。
        # 真实情况由 `set_note()` 那句副标题说清楚。
        if self._items:
            self.header_btn.setText("%s   %s  %d" % (arrow, label, len(self._items)))
        else:
            self.header_btn.setText("%s   %s" % (arrow, label))

    def _refresh_footer(self):
        self.empty_hint.setVisible(not self._items)
        if not self._items:
            self.empty_hint.setText(self._hint or tr("这个分组里没有版本"))
        self.more_btn.setVisible(self._built < len(self._items))
        if self._built < len(self._items):
            self.more_btn.setText(tr("加载更多（还有 {n} 个）",
                                     n=len(self._items) - self._built))

    def retranslate(self):
        super().retranslate()
        self._refresh_header()
        self._refresh_footer()
