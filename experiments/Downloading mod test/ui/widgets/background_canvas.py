"""
背景画布

主窗口最底下那层容器：负责把背景图/背景色画出来，上面再叠一层半透明黑色
（压暗），这样卡片和文字才不会被亮背景冲得看不清。

## 为什么不用 QSS 的 background-image

Qt 样式表的 `background-image` 在 QMainWindow 上没法可靠地"铺满并保持比例"，
而且**没法叠加压暗层**（同一控件只能有一个 background-image）。
背景图是用户自己丢进来的，尺寸什么都有，所以干脆自己画：
想要什么铺法（填充/适应/拉伸/居中/跨区）和压暗程度都能精确控制。

## 为什么用 setParent(None) 挂载

这里有内存管理上的讲究，见 `attach()` 的说明。
"""
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from ui.widgets import appearance


class BackgroundCanvas(QWidget):
    """画背景的容器。把真实的界面放进去当子控件就行"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BackgroundCanvas")
        self.setAutoFillBackground(False)

        self._pixmap = None        # 原图
        self._scaled = None        # 缩放后的缓存（跟着控件尺寸失效）
        self._scaled_for = (0, 0)
        self._settings = {}
        self._image_path = None
        self.reload()

    # ---------- 配置 ----------

    def reload(self):
        """重新读一遍外观设置（设置界面里改完调它）"""
        self._settings = appearance.get_bg()
        path = self._settings.get("image") or ""
        if path != self._image_path:
            self._image_path = path
            self._pixmap = QPixmap(path) if path else None
            if self._pixmap is not None and self._pixmap.isNull():
                print(f"[Background] 图片读不出来：{path}")
                self._pixmap = None
            self._scaled = None
            self._scaled_for = (0, 0)
        self.update()

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()

        image = self._pixmap
        if image is None:
            # 没图：纯色（没设过就用默认深色）
            painter.fillRect(rect, QColor(self._settings.get("color")
                                          or appearance.FALLBACK_COLOR))
        else:
            self._draw_image(painter, rect, image)

        dim = int(self._settings.get("dim", 0) or 0)
        if dim > 0 and image is not None:
            # 压暗层。只压图，不压纯色（纯色本来就是给眼睛省事的）
            painter.fillRect(rect, QColor(0, 0, 0, min(255, dim)))
        painter.end()

    def _draw_image(self, painter, rect, image):
        """按"铺法"把图画上去。命名和 Windows 的六种一一对应"""
        mode = self._settings.get("mode", "fill")

        if mode == "center":
            # 居中：原图大小，放中间
            painter.drawPixmap(rect.center().x() - image.width() // 2,
                               rect.center().y() - image.height() // 2,
                               image)
            return

        if mode == "stretch":
            # 拉伸：不管比例，直接拉满（会变形，但写实/纯色图无所谓）
            painter.drawPixmap(rect, image.scaled(
                rect.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            return

        if mode == "fit":
            # 适应：整张图都要看得见，不够的地方留边
            scaled = image.scaled(rect.size(),
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(rect.center().x() - scaled.width() // 2,
                               rect.center().y() - scaled.height() // 2,
                               scaled)
            return

        if mode == "span":
            # 跨区：横向铺满、纵向居中裁切。
            # Windows 那边是"一张图横跨所有显示器"，我们只有一个窗口，
            # 所以取它的视觉特征：宽度优先、上下对称裁掉多余部分。
            scaled = image.scaled(rect.width(), rect.height(),
                                  Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                  Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(rect, scaled,
                               QRect((scaled.width() - rect.width()) // 2,
                                     (scaled.height() - rect.height()) // 2,
                                     rect.width(), rect.height()))
            return

        # fill（默认）：等比放大到铺满，多出来的裁掉
        painter.drawPixmap(rect, self._cover(rect))

    def _cover(self, rect):
        """按"铺满且不变形"缩放，结果缓存起来（窗口大小没变就直接复用）"""
        size = (max(1, rect.width()), max(1, rect.height()))
        if self._scaled is not None and self._scaled_for == size:
            return self._scaled
        self._scaled = self._pixmap.scaled(
            size[0], size[1],
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._scaled_for = size
        return self._scaled

    def resizeEvent(self, event):
        self._scaled = None            # 尺寸变了，缓存作废
        self._scaled_for = (0, 0)
        super().resizeEvent(event)


def attach(window):
    """给主窗口装一个背景画布，返回画布

    用法：把原来 setCentralWidget(...) 的内容改成塞进返回的画布。

    ⚠️ 必须先把原来的中央控件 setParent(None) 再改挂到画布上。
    Qt 里一个控件只能有一个父控件，直接 addWidget 会把它从旧父控件上摘下来，
    但中央控件是 QMainWindow 显式管理的，先摘干净更稳妥。
    """
    old = window.centralWidget()
    canvas = BackgroundCanvas(window)
    layout = QVBoxLayout(canvas)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    if old is not None:
        old.setParent(None)
        layout.addWidget(old)

    window.setCentralWidget(canvas)
    return canvas
