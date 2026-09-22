from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QHBoxLayout, QLabel
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon


class AccountList(QWidget):
    """左侧账户列表 + 新建按钮"""
    account_selected = pyqtSignal(str)  # 传出账户名
    add_requested = pyqtSignal()        # 请求打开新建弹窗

    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.setObjectName("AccountPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(10)

        header = QLabel("档案")
        header.setObjectName("PanelTitle")
        layout.addWidget(header)

        self.list = QListWidget()
        self.list.setObjectName("AccountList")
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.currentItemChanged.connect(self._on_select)
        layout.addWidget(self.list, 1)

        # "+" 按钮
        add_row = QHBoxLayout()
        add_row.addStretch()
        self.add_btn = QPushButton("＋")
        self.add_btn.setObjectName("AddAccountButton")
        self.add_btn.setFixedSize(36, 36)
        self.add_btn.setToolTip("新建档案")
        self.add_btn.clicked.connect(self.add_requested.emit)
        add_row.addWidget(self.add_btn)
        layout.addLayout(add_row)

        self.refresh()

    def refresh(self):
        self.list.clear()
        for acc in self.manager.accounts:
            item = QListWidgetItem()
            label = self._format(acc)
            item.setText(label)
            item.setData(Qt.ItemDataRole.UserRole, acc["name"])
            self.list.addItem(item)

        # 选中当前
        cur = self.manager.get_current()
        if cur:
            for i in range(self.list.count()):
                it = self.list.item(i)
                if it.data(Qt.ItemDataRole.UserRole) == cur["name"]:
                    self.list.setCurrentItem(it)
                    break

    @staticmethod
    def _format(acc):
        icon = {"offline": "✂", "microsoft": "🛡", "thirdparty": "🔗"}.get(acc["type"], "👤")
        type_name = {"offline": "离线验证", "microsoft": "正版验证", "thirdparty": "第三方验证"}.get(acc["type"], "未知")
        return f"{icon}  {acc['name']}\n     {type_name}"

    def _on_select(self, current, previous):
        if current:
            name = current.data(Qt.ItemDataRole.UserRole)
            self.manager.set_current(name)
            self.account_selected.emit(name)
