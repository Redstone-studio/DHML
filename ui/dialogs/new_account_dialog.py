from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QButtonGroup, QRadioButton, QWidget   # ← 加上 QWidget
)
from PyQt6.QtCore import Qt
import re


class NewAccountDialog(QDialog):
    """新建档案 - 选择验证类型 + 填信息"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建档案")
        self.setModal(True)
        self.setFixedSize(420, 360)

        self.result_account = None  # 成功后填 (type, name)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("新建档案 - 选择验证类型")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)

        # 三个验证类型
        self.type_group = QButtonGroup(self)
        self.type_buttons = {}
        types = [
            ("microsoft", "🛡  正版验证", "使用微软账户登录（暂未实现）"),
            ("thirdparty", "🔗  第三方验证", "使用 authlib-injector 服务器（暂未实现）"),
            ("offline", "✂  离线验证", "仅本地使用，无需登录"),
        ]
        for key, text, hint in types:
            btn = QRadioButton(f"{text}\n{hint}")
            btn.setObjectName("TypeRadio")
            btn.setProperty("key", key)
            self.type_group.addButton(btn)
            self.type_buttons[key] = btn
            layout.addWidget(btn)

        self.type_buttons["offline"].setChecked(True)  # 默认离线

        layout.addSpacing(6)

        # 玩家名输入（离线用）
        self.name_row = QWidget()
        nr = QHBoxLayout(self.name_row)
        nr.setContentsMargins(0, 0, 0, 0)
        nr.addWidget(QLabel("玩家名:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("仅允许字母数字下划线，3-16 位")
        nr.addWidget(self.name_input, 1)
        layout.addWidget(self.name_row)

        # 提示
        self.hint = QLabel("离线模式：名字只用于本地游戏内显示")
        self.hint.setObjectName("DialogHint")
        layout.addWidget(self.hint)

        layout.addStretch()

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton("继续")
        self.ok_btn.setObjectName("PrimaryButton")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.ok_btn)
        layout.addLayout(btn_row)

        # 选择类型时更新提示
        self.type_group.buttonClicked.connect(self._on_type_changed)

    def _on_type_changed(self, btn):
        key = btn.property("key")
        if key == "offline":
            self.name_row.setVisible(True)
            self.hint.setText("离线模式：名字只用于本地游戏内显示")
        elif key == "microsoft":
            self.name_row.setVisible(False)
            self.hint.setText("⚠️ 微软登录功能尚未实现，敬请期待")
        else:
            self.name_row.setVisible(False)
            self.hint.setText("⚠️ 第三方验证功能尚未实现，敬请期待")

    def _on_ok(self):
        key = self.type_group.checkedButton().property("key")

        if key == "offline":
            name = self.name_input.text().strip()
            if not self._valid_name(name):
                self.hint.setText("❌ 名字不合法：3-16 位字母数字下划线")
                return
            self.result_account = ("offline", name)
            self.accept()
        else:
            self.hint.setText("⚠️ 该验证方式暂未实现")
            return

    @staticmethod
    def _valid_name(name: str) -> bool:
        return bool(re.match(r"^[A-Za-z0-9_]{3,16}$", name))
