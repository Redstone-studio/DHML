"""新建档案对话框

相对旧版的改动：
- 去掉 setFixedSize：固定尺寸配两行文字的 radio 很容易挤，改成最小宽度
- 选了未实现的验证方式时，直接把「继续」禁用掉，比点了之后弹提示清楚
- 去掉没用到的 Qt 导入
"""

import re

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QRadioButton, QVBoxLayout, QWidget
)

from ui.icons import icon

NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,16}$")

# (类型, 图标名, 标题, 说明)
# 图标是 assets/icons/*.svg。不要用 emoji —— 🛡🔗✂ 的渲染取决于系统字体，
# 大小和基线都不受我们控制，会跟旁边的文字对不齐。
ACCOUNT_TYPES = (
    ("microsoft", "shield", "正版验证", "使用微软账户登录"),
    ("thirdparty", "network", "第三方验证", "使用 authlib-injector 服务器"),
    ("offline", "offline", "离线验证", "仅本地使用，无需登录"),
)


class NewAccountDialog(QDialog):
    """新建档案：选择验证类型 + 填信息"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建档案")
        self.setModal(True)
        self.setMinimumWidth(460)

        # 成功后填 (type, name)
        self.result_account = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel("新建档案")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)

        layout.addWidget(self._field_label("选择验证方式"))

        self.type_group = QButtonGroup(self)
        self.type_buttons = {}
        for key, icon_name, text, hint in ACCOUNT_TYPES:
            button = QRadioButton(f"{text}      {hint}")
            button.setIcon(icon(icon_name))
            button.setIconSize(QSize(18, 18))
            button.setProperty("key", key)
            self.type_group.addButton(button)
            self.type_buttons[key] = button
            layout.addWidget(button)

        self.type_buttons["offline"].setChecked(True)

        layout.addSpacing(4)

        # 玩家名（只有离线模式需要）
        self.name_row = QWidget()
        name_layout = QHBoxLayout(self.name_row)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(10)
        name_layout.addWidget(self._field_label("玩家名"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("3-16 位，只能字母、数字、下划线")
        self.name_input.textChanged.connect(self._validate)
        name_layout.addWidget(self.name_input, 1)
        layout.addWidget(self.name_row)

        self.hint = QLabel()
        self.hint.setObjectName("HintText")
        self.hint.setWordWrap(True)
        self.hint.setMinimumHeight(34)
        layout.addWidget(self.hint)

        layout.addSpacing(6)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        self.ok_btn = QPushButton("继续")
        self.ok_btn.setObjectName("PrimaryButton")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._on_ok)
        buttons.addWidget(self.ok_btn)
        layout.addLayout(buttons)

        self.type_group.buttonClicked.connect(self._on_type_changed)
        self._on_type_changed(self.type_buttons["offline"])

    # ---------- 内部 ----------

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    def _current_key(self) -> str:
        button = self.type_group.checkedButton()
        return button.property("key") if button else "offline"

    def _on_type_changed(self, _button):
        key = self._current_key()
        is_offline = key == "offline"

        self.name_row.setVisible(is_offline)
        self.ok_btn.setEnabled(is_offline)

        if is_offline:
            self.hint.setText("离线模式：名字只用于本地游戏内显示，不联网验证。")
        elif key == "microsoft":
            self.hint.setText("\u26a0 微软登录尚未实现，该选项暂不可用。")
        else:
            self.hint.setText("\u26a0 第三方验证尚未实现，该选项暂不可用。")

        if is_offline:
            self._validate()

    def _validate(self):
        if self._current_key() != "offline":
            return
        name = self.name_input.text().strip()
        if not name:
            self.hint.setText("离线模式：名字只用于本地游戏内显示，不联网验证。")
            self.ok_btn.setEnabled(False)
        elif not NAME_PATTERN.match(name):
            self.hint.setText("\u274c 名字不合法：需要 3-16 位字母、数字或下划线")
            self.ok_btn.setEnabled(False)
        else:
            self.hint.setText("离线模式：名字只用于本地游戏内显示，不联网验证。")
            self.ok_btn.setEnabled(True)

    def _on_ok(self):
        if self._current_key() != "offline":
            return
        name = self.name_input.text().strip()
        if not NAME_PATTERN.match(name):
            self.hint.setText("\u274c 名字不合法：需要 3-16 位字母、数字或下划线")
            return
        self.result_account = ("offline", name)
        self.accept()
