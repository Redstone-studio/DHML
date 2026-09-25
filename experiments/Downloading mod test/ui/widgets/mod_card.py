"""
搜索结果卡片
- 图标（可异步更新）
- 名字、作者、描述、加载器标签、下载量、关注数
"""
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel
)
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont
from PyQt6.QtCore import Qt, QByteArray, pyqtSignal


def make_placeholder(size: int = 64, text: str = "?") -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(QColor("#2a2c30"))
    painter = QPainter(pm)
    painter.setPen(QColor("#6e7076"))
    font = QFont()
    font.setPointSize(20)
    painter.setFont(font)
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return pm


def format_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


PROJECT_TYPE_LABEL = {
    "mod": "模组",
    "shader": "光影",
    "resourcepack": "资源包",
    "datapack": "数据包",
    "modpack": "整合包",
}


class ModResultCard(QFrame):
    clicked = pyqtSignal(dict)

    def __init__(self, hit: dict):
        super().__init__()
        self.hit = hit
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self.refresh_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(14)

        # 图标
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(64, 64)
        self.icon_label.setPixmap(make_placeholder(64, "?"))
        layout.addWidget(self.icon_label)

        # 中间
        mid = QVBoxLayout()
        mid.setSpacing(4)

        name_row = QHBoxLayout()
        title = QLabel(hit.get("title", ""))
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #fff;")
        name_row.addWidget(title)

        author = QLabel(f"by {hit.get('author', '')}")
        author.setStyleSheet("font-size: 11px; color: #888;")
        name_row.addWidget(author)
        name_row.addStretch()
        mid.addLayout(name_row)

        desc = QLabel(hit.get("description", ""))
        desc.setStyleSheet("font-size: 12px; color: #a0a1a7;")
        desc.setWordWrap(True)
        mid.addWidget(desc)

        # 元信息
        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)

        # 项目类型
        ptype = hit.get("project_type", "mod")
        t = QLabel(PROJECT_TYPE_LABEL.get(ptype, ptype))
        t.setStyleSheet("""
            background-color: #3a3c42;
            color: #d0d0d0;
            border-radius: 3px;
            padding: 1px 6px;
            font-size: 10px;
        """)
        meta_row.addWidget(t)

        # 加载器
        cats = hit.get("display_categories", []) or hit.get("categories", [])
        loaders_shown = [c for c in cats if c in ("fabric", "forge", "neoforge", "quilt")]
        for ld in loaders_shown[:3]:
            tag = QLabel(ld)
            tag.setStyleSheet("""
                background-color: #2f5d3a;
                color: #b6f0bf;
                border-radius: 3px;
                padding: 1px 6px;
                font-size: 10px;
            """)
            meta_row.addWidget(tag)

        # 版本
        versions = hit.get("versions", [])
        if versions:
            v_text = ", ".join(versions[-3:])
            v_label = QLabel(f"🎮 {v_text}")
            v_label.setStyleSheet("font-size: 10px; color: #6e7076;")
            meta_row.addWidget(v_label)

        meta_row.addStretch()

        downloads = hit.get("downloads", 0)
        dl_label = QLabel(f"📥 {format_number(downloads)}")
        dl_label.setStyleSheet("font-size: 11px; color: #a0a1a7;")
        meta_row.addWidget(dl_label)

        follows = hit.get("follows", 0)
        fl_label = QLabel(f"⭐ {format_number(follows)}")
        fl_label.setStyleSheet("font-size: 11px; color: #a0a1a7;")
        meta_row.addWidget(fl_label)

        mid.addLayout(meta_row)
        layout.addLayout(mid, 1)

    def refresh_style(self):
        """按当前"卡片透明度"重建样式。

        透明度是在设置里改的，改完要遍历已经建出来的卡片逐个调它才看得到变化
        （各个页面负责遍历，见 search_page.refresh_card_style）。
        """
        from ui.widgets import card_style
        self.setStyleSheet(card_style.search_card_qss())

    def set_icon(self, data: bytes):
        pixmap = QPixmap()
        pixmap.loadFromData(QByteArray(data))
        if pixmap.isNull():
            return
        self.icon_label.setPixmap(pixmap.scaled(
            64, 64,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        ))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.hit)
