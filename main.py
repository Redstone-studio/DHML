"""MC Launcher 入口"""

import sys
import traceback

from PyQt6.QtWidgets import QApplication, QMessageBox

from core.app_info import APP_NAME, APP_VERSION
from core.i18n import tr


def _excepthook(exc_type, exc_value, exc_tb):
    """未捕获异常 → 弹窗显示，而不是静默崩溃

    注意：PyQt6 里槽函数抛出的未捕获异常走完这个钩子后，Qt 仍可能终止进程，
    所以这里只是"让用户看到发生了什么"，不是"把程序救回来"。
    """
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(text, file=sys.stderr)
    try:
        box = QMessageBox()
        box.setWindowTitle(tr("程序出错了"))
        box.setText(tr("发生未处理的异常"))
        box.setDetailedText(text)
        box.setIcon(QMessageBox.Icon.Critical)
        box.exec()
    except Exception:
        pass


def _make_console_safe():
    """让 print() 绝不会因为控制台编码而抛异常

    Windows 控制台用的是本地代码页（中文系统 GBK，西文系统 cp1252）。
    在西文系统上 print("中文") 会抛 UnicodeEncodeError —— 而项目里的 print
    大多写在异常处理分支里，一抛就把"报个错"变成"崩掉"。
    errors="replace" 让不可编码的字符变成 ? 而不是抛异常。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass    # 打包成 --windowed 时 stdout/stderr 可能是 None


def main() -> int:
    _make_console_safe()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    # 全局异常钩子要在建窗口之前装好
    sys.excepthook = _excepthook

    # 窗口延迟到这里再 import：万一是 UI 模块本身导入失败，异常钩子已经就位，
    # 至少能弹出来告诉用户，而不是黑一下就没了。
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
