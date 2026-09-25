"""屏幕缩放等小工具

`screen_dpr` 的逻辑照主项目 `ui/icons.py` 抄的（那个文件还带一堆图标处理，
这个实验用不到，所以没整份复制）。
"""
from PyQt6.QtGui import QGuiApplication


def screen_dpr(widget=None) -> float:
    """当前屏幕缩放（系统 125% 就返回 1.25）

    取不到就退回 1.0 —— 宁可画得普通一点，也不要因为拿不到屏幕就崩。
    """
    try:
        if widget is not None:
            dpr = widget.devicePixelRatioF()
            if dpr and dpr > 0:
                return float(dpr)
    except Exception:
        pass
    try:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            return float(screen.devicePixelRatio())
    except Exception:
        pass
    return 1.0
