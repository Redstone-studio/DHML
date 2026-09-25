"""
Mosslight 正版验证实验 —— 入口

跑法：
    cd "D:\\DHML-src\\experiments\\Genuine verification"
    python main.py

协议自测（不启动界面）：
    python -m core.verify.selftest       # 离线，用本地假服务器验协议实现
    python -m core.verify.mcping <地址>   # 查真实服务器
    python -m core.verify.srv <域名>      # 查 SRV 记录
"""
import sys
import traceback

from PyQt6.QtCore import qInstallMessageHandler
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.app_info import APP_DISPLAY_NAME
from core.config import get_config_dir

# Qt 自己会往 stderr 打这些噪音。**只滤确认无害的**，别把真问题一起吞了。
_QT_NOISE = (
    "QFont::setPointSize",                       # 组合框内部拿没设字号的字体去 set
    "QFontDatabase: Cannot find font directory",  # 开发环境没 Qt 自带字体目录，打包后没有
)


def _qt_message_handler(mode, context, message):
    for noise in _QT_NOISE:
        if message.startswith(noise):
            return
    print(message, file=sys.stderr)


def _excepthook(exc_type, exc_value, exc_tb):
    """未捕获异常 → 弹窗，而不是静默崩溃

    ⚠️ PyQt6 里槽函数抛出的未捕获异常走完这个钩子后，**Qt 仍可能终止进程**，
    所以这里只是"让用户看到发生了什么"，不是"把程序救回来"。
    """
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(text, file=sys.stderr)
    try:
        box = QMessageBox()
        box.setWindowTitle("程序出错了")
        box.setText("发生了未处理的异常")
        box.setDetailedText(text)
        box.setIcon(QMessageBox.Icon.Critical)
        box.exec()
    except Exception:
        pass


def _make_console_safe():
    """让 print() 绝不会因为控制台编码而抛异常

    Windows 控制台用本地代码页（中文系统 GBK）。这个项目到处在打服务器 MOTD、
    玩家名，里面有 emoji 和特殊符号，直接 print 会 UnicodeEncodeError。
    而 print 大多写在异常处理分支里，一抛就把"报个错"变成"崩掉"
    —— **实测已经踩过两次**（`python -m core.verify.mcping` 打 Hypixel 的
    MOTD 时直接炸）。

    errors="replace" 让编不出来的字符变成 ? 而不是抛异常。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass        # 打包成 --windowed 时 stdout/stderr 可能是 None


def configure_app(app):
    """应用级信息 + 样式基座

    ⚠️ **`app.setStyle("Fusion")` 不能省。**
    Windows 上 Qt 默认走原生样式（这台机器是 `windows11`，老系统是 `windowsvista`）。
    原生样式会**自己画**一部分控件 —— 按钮的斜面、组合框的下拉箭头、
    勾选框的方块、滚动条的滑块 —— 和 `assets/styles/*.qss` 那套深色皮肤打架，
    表现成"某些控件还是系统那个样子"，而且**不报错**。

    Fusion 是 Qt 自带的、完全由样式表驱动的样式，是自定义 QSS 主题的标准基座。
    必须在建任何控件**之前**设置。

    ⚠️ 离屏（offscreen）下 Fusion 和 windows11 渲出来的像素**完全一样**
    （实测逐像素比对 0% 差异），所以这个差别在自动化测试里量不出来，
    得在真窗口里看。
    """
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)

    app.setStyle("Fusion")


def main() -> int:
    _make_console_safe()
    # 消息钩子要在建 QApplication 之前装好，否则建它那一步的噪音会漏出来
    qInstallMessageHandler(_qt_message_handler)

    app = QApplication(sys.argv)
    configure_app(app)

    # 全局异常钩子要在建窗口之前装好
    sys.excepthook = _excepthook

    # 窗口延到这里再 import：万一是 UI 模块自己导入失败，
    # 异常钩子已经就位，至少能弹出来告诉用户，而不是黑一下就没了。
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    print(f"[Config] 配置目录：{get_config_dir()}")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
