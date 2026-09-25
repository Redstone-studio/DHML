"""
图标下载任务（用于 QThreadPool）
"""
import requests
from PyQt6.QtCore import QRunnable, QObject, pyqtSignal
from core import cache


HEADERS = {"User-Agent": "Mosslight-Launcher/1.0"}


class IconSignals(QObject):
    finished = pyqtSignal(str, bytes)   # (url, data)
    failed = pyqtSignal(str)


class IconTask(QRunnable):
    """单个图标下载任务"""

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.signals = IconSignals()

    def run(self):
        try:
            # 1. 磁盘缓存
            cached = cache.load(self.url)
            if cached:
                self.signals.finished.emit(self.url, cached)
                return

            # 2. 网络下载
            r = requests.get(self.url, headers=HEADERS, timeout=10)
            r.raise_for_status()
            data = r.content

            # 3. 保存
            cache.save(self.url, data)
            self.signals.finished.emit(self.url, data)
        except Exception:
            self.signals.failed.emit(self.url)
