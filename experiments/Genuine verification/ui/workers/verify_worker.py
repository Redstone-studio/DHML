"""
验证后台线程

**验证一定会联网**（查服务器、以后还有 OAuth），绝不能放主线程 ——
另一个实验项目里为"图标同步下载"卡过 2.4 秒，同一类坑。

用 QThread 而不是线程池：一次只会有一个验证在跑，而且需要**取消**
（用户改了地址又点一次，旧的那次应该被丢掉，不能让它回来覆盖新结果）。
"""
from PyQt6.QtCore import QThread, pyqtSignal

from core.verify.base import STATE_ERROR, VerifyResult


class VerifyWorker(QThread):
    """跑一次验证

    ⚠️ 结果要带**令牌**回主线程校验。用户连点两次"开始验证"时，
    先发出去的那次可能后回来，不校验的话界面会被过期结果覆盖
    （详情页踩过一模一样的问题，见另一个实验项目的第 11 条坑）。
    """

    done = pyqtSignal(object)          # VerifyResult（带 token）

    def __init__(self, verifier, ctx: dict, token: int, parent=None):
        super().__init__(parent)
        self.verifier = verifier
        self.ctx = dict(ctx)
        self.token = token
        self._cancelled = False

    def cancel(self):
        """标记取消。

        ⚠️ **不调 terminate()**：杀线程会让 socket 和锁停在半路，
        后面整个进程的状态都不可信。这里只置标志，
        结果回来时主线程按令牌丢弃就行。
        """
        self._cancelled = True

    def run(self):
        try:
            result = self.verifier.verify(self.ctx)
        except Exception as e:
            # 线程里抛出去的异常会把进程带走，必须兜住
            result = VerifyResult(STATE_ERROR,
                                  f"验证时出错：{type(e).__name__}: {e}")
        if self._cancelled:
            return
        result.token = self.token
        self.done.emit(result)
