"""关于内容：软件信息 / 隐私说明 / 版权声明 / 开源许可

单独抽成一个控件，因为它的宿主是**对话框**而不是页面。

以前它是侧边栏的一个导航项，但这类"一辈子看一次"的内容占一个常驻入口
不划算 —— 导航从 5 项涨到 6 项，为的却是个极少点开的东西。
现在改成设置页里一个按钮弹出来（桌面软件的标准做法，PCL2 也是收在设置里的）。

⚠️ 有两处**还没定**，都做成了模块级常量，定了改一行就行：
  - PROJECT_URL     启动器最终叫什么、仓库要不要改名都还没定，所以先留空。
                    留空时「查看源代码」按钮是禁用状态 + 提示"链接待定"，
                    而不是点开一个死链，也不是假装能点。
  - COPYRIGHT_HOLDER  版权归属，填你的名字或组织名。
"""

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
)

from core.app_info import APP_NAME, APP_VERSION
from core.i18n import tr
from ui.translatable import TranslatableWidget

# 留空 = 还没定。填上就自动变成可点。
PROJECT_URL = ""

COPYRIGHT_HOLDER = "Redstone Studio"
COPYRIGHT_YEAR = "2026"

# (名称, 版权与许可说明, 来源网址, 许可证网址)
# 版权行保留英文原文 —— 那是法律文本，不要翻译。
# 这三个是上游项目的官方地址，和本项目的名字无关，不会变。
DEPENDENCIES = (
    (
        "PyQt6",
        "Copyright © Riverbank Computing Limited\nLicensed under the GPL-3.0 license.",
        "https://www.riverbankcomputing.com/software/pyqt/",
        "https://www.gnu.org/licenses/gpl-3.0.html",
    ),
    (
        "Python",
        "Copyright © Python Software Foundation\nLicensed under the PSF License.",
        "https://www.python.org/",
        "https://docs.python.org/3/license.html",
    ),
    (
        "PyInstaller",
        "Copyright © PyInstaller Development Team\nLicensed under GPL-2.0-or-later "
        "with a special exception for bundled applications.",
        "https://pyinstaller.org/",
        "https://pyinstaller.org/en/stable/license.html",
    ),
)

GPL_URL = "https://www.gnu.org/licenses/gpl-3.0.html"


def _open(url: str):
    QDesktopServices.openUrl(QUrl(url))


class _LicenseRow(QWidget):
    """一行开源组件：左边名称 + 版权说明，右边两个按钮"""

    def __init__(self, name: str, description: str, site_url: str, license_url: str):
        super().__init__()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(14)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(3)

        title = QLabel(name)
        title.setObjectName("LicenseName")
        text_box.addWidget(title)

        desc = QLabel(description)
        desc.setObjectName("LicenseText")
        desc.setWordWrap(True)
        text_box.addWidget(desc)
        layout.addLayout(text_box, 1)

        self.site_btn = QPushButton()
        self.site_btn.setObjectName("LinkButton")
        self.site_btn.clicked.connect(lambda: _open(site_url))
        layout.addWidget(self.site_btn)

        self.license_btn = QPushButton()
        self.license_btn.setObjectName("LinkButton")
        self.license_btn.clicked.connect(lambda: _open(license_url))
        layout.addWidget(self.license_btn)

    def retranslate(self):
        self.site_btn.setText(tr("查看来源网站"))
        self.license_btn.setText(tr("查看许可文档"))


class AboutContent(TranslatableWidget):
    """关于页的全部内容。宿主负责把它放进对话框 / 页面里"""

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)

        layout.addWidget(self._make_app_card())
        layout.addWidget(self._make_privacy_card())
        layout.addWidget(self._make_copyright_card())
        layout.addWidget(self._make_license_card())
        layout.addStretch()

        self.retranslate()

    # ---------- 语言切换 ----------

    def retranslate(self):
        super().retranslate()
        for row in self.license_rows:
            row.retranslate()

    # ---------- 卡片 ----------

    def _card(self, title: str):
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 16, 20, 18)
        box.setSpacing(10)
        box.addWidget(self.label(title, "SectionTitle"))
        return card, box

    @staticmethod
    def _bullet(text: str) -> QLabel:
        label = QLabel(f"·  {text}")
        label.setObjectName("LicenseText")
        label.setWordWrap(True)
        return label

    def _make_app_card(self):
        card, box = self._card("软件信息")

        name_row = QHBoxLayout()
        name_row.setSpacing(10)

        name = QLabel(APP_NAME)
        name.setObjectName("AboutName")
        name_row.addWidget(name)

        version = QLabel(APP_VERSION)
        version.setObjectName("Badge")
        name_row.addWidget(version)
        name_row.addStretch()
        box.addLayout(name_row)

        box.addWidget(self.label(
            "一个用 Python + PyQt6 写的 Minecraft 启动器，能扫描本地版本、管理账户。"
            "启动游戏和版本下载还在做。", "LicenseText"
        ))

        self.source_btn = self.button("查看源代码")
        if PROJECT_URL:
            self.source_btn.clicked.connect(lambda: _open(PROJECT_URL))
        else:
            # 仓库地址还没定 —— 禁用而不是留个死链
            self.source_btn.setEnabled(False)
            self.bind(self.source_btn, "仓库地址还没定", "toolTip")

        source_row = QHBoxLayout()
        source_row.addWidget(self.source_btn)
        source_row.addStretch()
        box.addLayout(source_row)

        return card

    def _make_privacy_card(self):
        card, box = self._card("隐私说明")

        box.addWidget(self.label("这个启动器不向作者发送任何东西。", "LicenseText"))
        # 这四条刻意写成独立的 tr("...") 而不是 for 循环 ——
        # 提取工具靠"字面量是否直接出现在 tr() 调用里"判断，
        # 套一层变量它就会当成"没走文案系统"报出来（功能上没问题，但会一直告警）。
        box.addWidget(self._bullet(tr("没有统计、没有遥测、没有崩溃上报。作者不会知道你在用它。")))
        box.addWidget(self._bullet(tr("你的档案信息（包括离线 UUID）只写在本机 %APPDATA%/MCLuncher/ 下。")))
        box.addWidget(self._bullet(tr("启动器不会在后台联网；只有你主动操作（比如以后做版本下载）时才会访问网络。")))
        box.addWidget(self._bullet(tr("以后做正版登录时，只有登录那一步会直连微软服务器，启动器本身不做中转。")))

        box.addWidget(self.label(
            "游戏本身、以及你连接的服务器可能有各自的隐私政策，与本说明无关。",
            "HintText"
        ))
        return card

    def _make_copyright_card(self):
        card, box = self._card("版权声明")

        box.addWidget(self.label(tr(
            "Copyright © {year} {holder}", year=COPYRIGHT_YEAR, holder=COPYRIGHT_HOLDER
        ), "LicenseText"))
        box.addWidget(self.label(
            "本软件不是 Minecraft 官方产品，与 Mojang Studios 和 Microsoft 没有隶属关系，"
            "也未获其批准或背书。", "LicenseText"
        ))
        box.addWidget(self.label("Minecraft 是 Mojang Studios 的商标。", "HintText"))
        return card

    def _make_license_card(self):
        card, box = self._card("开源许可")

        box.addWidget(self.label("本软件使用了以下开源组件，在此致谢：", "LicenseText"))

        self.license_rows = []
        for name, description, site_url, license_url in DEPENDENCIES:
            row = _LicenseRow(name, description, site_url, license_url)
            self.license_rows.append(row)
            box.addWidget(row)

        box.addWidget(self.label(
            "因为使用了 PyQt6（GPL-3.0），本软件整体也以 GPL-3.0 分发，"
            "完整许可证文本见仓库根目录的 LICENSE 文件。", "LicenseText"
        ))

        license_btn = self.button("查看完整许可证")
        license_btn.clicked.connect(lambda: _open(GPL_URL))
        row = QHBoxLayout()
        row.addWidget(license_btn)
        row.addStretch()
        box.addLayout(row)

        return card
