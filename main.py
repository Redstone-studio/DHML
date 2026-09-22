import sys
import traceback
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QTimer
from ui.main_window import MainWindow


def excepthook(exc_type, exc_value, exc_tb):
    """未捕获的异常 → 弹窗显示，而不是静默崩溃"""
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(tb, file=sys.stderr)
    # 尝试弹窗
    try:
        msg = QMessageBox()
        msg.setWindowTitle("程序出错了")
        msg.setText("发生未处理的异常：")
        msg.setDetailedText(tb)
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.exec()
    except Exception:
        pass


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MC Launcher")

    # 装全局异常钩子
    sys.excepthook = excepthook

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
