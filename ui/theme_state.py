"""当前生效的主题状态

有些控件要**自己画**（比如开关 Switch —— QSS 管不到 paintEvent），
它们得拿到实际颜色。放一个模块出来，免得每处各抄一份主题解析逻辑。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication

from core import theme
from core.config import config


def system_is_dark() -> bool:
    """系统是不是深色（Qt 6.5+ 才有 colorScheme）"""
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return True


def current_mode() -> str:
    """配置里的 system/dark/light 落到实际的 dark/light"""
    return theme.effective_mode(config.get("theme", "system"), system_is_dark())


def palette() -> dict:
    """当前主题的调色板（含用户自定义的强调色）"""
    return theme.palette(current_mode(), config.get("accent_color", ""))
