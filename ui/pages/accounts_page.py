"""账户页

结构调整：旧版本把账户列表做成固定的一整列（约 250px 宽），三个账户常年占掉
四分之一窗口宽度，现在收进独立页面。

另外这里刻意**没有**连接 QListWidget.currentItemChanged —— 旧代码那样做会导致
每次 refresh() 里的 setCurrentItem 都触发 set_current + 写盘，并在刷新时重复
发信号。现在改成：单击只选中，**双击**（或点「设为当前」按钮）才真的切换。

列表行的做法和版本页一致：QListWidget + 自定义行控件。
三个 Qt 细节：
- 行控件是普通 QWidget 子类，默认不画 QSS 背景 —— 正好让选中底色透出来
- 行控件必须设 WA_TransparentForMouseEvents，否则点击被它吃掉，选不中
- "当前档案"用左侧竖条表示，不用右侧徽章：徽章飘在行尾离内容很远，很破坏观感
"""

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget
)

from core.accounts import type_label
from core.i18n import tr
from ui.icons import ACCOUNT_TYPE_ICONS, icon
from ui.translatable import TranslatableWidget

ROW_HEIGHT = 62


class _AccountRow(QWidget):
    """一行账户：图标 + 名字 / uuid · 类型；当前档案左侧带强调色竖条"""

    def __init__(self, account: dict, is_current: bool):
        super().__init__()
        # 让点击穿透到 QListWidget，否则选中/双击全都失效
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 14, 6)
        layout.setSpacing(10)

        # 左侧竖条。不管是不是当前档案都占位，只是颜色不同，
        # 这样两种行的文字起始位置能对齐。
        accent = QFrame()
        accent.setObjectName("AccountRowAccent" if is_current else "AccountRowAccentOff")
        accent.setFixedWidth(4)
        layout.addWidget(accent)

        avatar = QLabel()
        avatar.setFixedSize(30, 30)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setPixmap(
            icon(ACCOUNT_TYPE_ICONS.get(account.get("type"), "offline")).pixmap(QSize(20, 20))
        )
        layout.addWidget(avatar)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)

        name = QLabel(str(account.get("name", "?")))
        name.setObjectName("AccountRowNameCurrent" if is_current else "AccountRowName")
        text_box.addWidget(name)

        uuid_text = str(account.get("uuid", ""))
        short_uuid = f"{uuid_text[:8]}…" if uuid_text else "—"
        meta = QLabel(f"{short_uuid}    ·    {type_label(account.get('type'))}")
        meta.setObjectName("AccountRowMeta")
        text_box.addWidget(meta)

        layout.addLayout(text_box, 1)


class AccountsPage(TranslatableWidget):
    accounts_changed = pyqtSignal()
    add_requested = pyqtSignal()

    def __init__(self, account_manager):
        super().__init__()
        self.manager = account_manager

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(8)

        layout.addWidget(self.label("账户", "PageTitle"))
        layout.addWidget(self.label(
            "目前只有离线验证可用；正版验证和第三方验证还在路上", "PageSubtitle"
        ))

        layout.addSpacing(14)

        self.list = QListWidget()
        self.list.setObjectName("AccountList")
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list, 1)

        self.empty_label = self.label(
            "还没有任何档案，点下面的「新建档案」开始", "EmptyState"
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

        layout.addWidget(self.label("双击一行可以直接切换为当前档案", "HintText"))
        layout.addSpacing(8)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)

        self.add_btn = self.button("新建档案", "PrimaryButton")
        self.add_btn.clicked.connect(self.add_requested.emit)
        buttons.addWidget(self.add_btn)

        self.current_btn = self.button("设为当前")
        self.current_btn.clicked.connect(self._set_current)
        buttons.addWidget(self.current_btn)

        buttons.addStretch()

        self.remove_btn = self.button("删除", "DangerButton")
        self.remove_btn.clicked.connect(self._remove)
        buttons.addWidget(self.remove_btn)

        layout.addLayout(buttons)

        self.reload()

    # ---------- 语言切换 ----------

    def retranslate(self):
        super().retranslate()
        # 列表行是生成的，直接重建
        self.reload()

    # ---------- 列表 ----------

    def reload(self):
        previous = self._selected_name()

        self.list.clear()
        current = self.manager.current
        for account in self.manager.accounts:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, account["name"])
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.list.addItem(item)
            self.list.setItemWidget(item, _AccountRow(account, account["name"] == current))

        # 恢复选中：优先恢复刷新前选中的那一项，否则选当前档案
        target = previous or current
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == target:
                self.list.setCurrentRow(row)
                break
        if self.list.count() and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)

        has_any = self.list.count() > 0
        self.list.setVisible(has_any)
        self.empty_label.setVisible(not has_any)
        self.current_btn.setEnabled(has_any)
        self.remove_btn.setEnabled(has_any)

    def _selected_name(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ---------- 操作 ----------

    def _on_item_double_clicked(self, item):
        """双击 = 切换为当前档案"""
        self.list.setCurrentItem(item)
        self._set_current()

    def _set_current(self):
        name = self._selected_name()
        if not name or name == self.manager.current:
            return
        self.manager.set_current(name)
        self.accounts_changed.emit()

    def _remove(self):
        name = self._selected_name()
        if not name:
            return
        reply = QMessageBox.question(
            self, tr("删除档案"),
            tr("确定删除档案「{name}」吗？", name=name) + "\n\n"
            + tr("这只删除本地记录，不影响游戏内的数据。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.manager.remove(name)
        self.accounts_changed.emit()
