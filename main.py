"""MC Launcher 入口"""

import sys
import traceback

from PyQt6.QtWidgets import QApplication, QMessageBox

from core.app_info import APP_NAME, APP_VERSION


def _excepthook(exc_type, exc_value, exc_tb):
    """未捕获异常 → 弹窗显示，而不是静默崩溃

    注意：PyQt6 里槽函数抛出的未捕获异常走完这个钩子后，Qt 仍可能终止进程，
    所以这里只是"让用户看到发生了什么"，不是"把程序救回来"。
    """
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(text, file=sys.stderr)
    try:
        box = QMessageBox()
        box.setWindowTitle("程序出错了")
        box.setText("发生未处理的异常")
        box.setDetailedText(text)
        box.setIcon(QMessageBox.Icon.Critical)
        box.exec()
    except Exception:
        pass


def main() -> int:
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
