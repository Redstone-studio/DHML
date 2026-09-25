"""
具体版本卡片
"""
from datetime import datetime
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton
)
from PyQt6.QtCore import Qt, pyqtSignal


def format_date(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return (iso_str or "")[:10]


class VersionCard(QFrame):
    # (版本数据, 游戏版本, 加载器) —— 后两个是"用户是在哪个分组里点的下载"，
    # 详情页要靠它们算出该下到哪个版本文件夹（见 core/mc_dir.py）
    download_requested = pyqtSignal(dict, str, str)

    def __init__(self, version: dict, game_version: str = "", loader: str = ""):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.version = version
        self.game_version = game_version
        self.loader = loader
        self.refresh_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(4)

        name_row = QHBoxLayout()
        name = QLabel(version.get("name", version.get("version_number", "")))
        name.setStyleSheet("font-size: 13px; font-weight: bold; color: #fff;")
        name_row.addWidget(name)

        vtype = version.get("version_type", "release")
        type_color = {
            "release": "#3f8f4b",
            "beta": "#d97706",
            "alpha": "#b91c1c",
        }.get(vtype, "#555")
        type_label = QLabel(vtype.capitalize())
        type_label.setStyleSheet(f"""
            background-color: {type_color};
            color: white; border-radius: 4px;
            padding: 1px 6px; font-size: 10px;
        """)
        name_row.addWidget(type_label)
        name_row.addStretch()
        left.addLayout(name_row)

        date = format_date(version.get("date_published", ""))
        downloads = version.get("downloads", 0)
        sub = QLabel(f"{date}  ·  {downloads:,} 次下载  ·  {version.get('version_number', '')}")
        sub.setStyleSheet("font-size: 11px; color: #6e7076;")
        left.addWidget(sub)

        layout.addLayout(left, 1)

        btn = QPushButton("⬇  下载")
        btn.setFixedSize(90, 32)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background-color: #3f8f4b; color: white;
                border: none; border-radius: 6px;
                font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background-color: #4da25a; }
        """)
        btn.clicked.connect(
            lambda: self.download_requested.emit(
                version, self.game_version, self.loader))
        layout.addWidget(btn)

    def refresh_style(self):
        """按当前"卡片透明度"重建样式（见 ui/widgets/card_style.py）"""
        from ui.widgets import card_style
        self.setStyleSheet(card_style.version_card_qss())