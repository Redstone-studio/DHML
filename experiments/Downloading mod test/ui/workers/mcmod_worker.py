"""
MC 百科链接解析（后台线程）

`core/mcmod.resolve()` 要联网、还带节流和重试，慢的时候要 2~4 秒 ——
**绝不能放主线程**（这个项目已经因为主线程联网卡过一次了，见使用说明第 6 节）。

用法：
    task = McmodResolveWorker("Fabric API")
    task.done.connect(lambda cid, title: ...)
    task.start()
"""
from PyQt6.QtCore import QThread, pyqtSignal

from core import mcmod


class McmodResolveWorker(QThread):
    # (class_id, 词条标题, 置信度, 搜索页链接)
    # 没解析出来时是 (None, None, None, 搜索页链接)
    done = pyqtSignal(object, object, object, object)

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name

    def run(self):
        try:
            cid, title, conf, fallback = mcmod.resolve(self.name)
        except Exception as e:                      # 兜底：线程里别让异常逃出去
            print(f"[MCMod] 解析线程出错（{self.name}）：{type(e).__name__}: {e}")
            cid, title, conf = None, None, None
            fallback = mcmod.search_url(self.name)
        self.done.emit(cid, title, conf, fallback)
