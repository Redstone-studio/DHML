"""Mosslight 入口"""

import sys
import traceback

from PyQt6.QtCore import qInstallMessageHandler
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.app_info import APP_DISPLAY_NAME, APP_VERSION
from core.i18n import tr


# Qt 自己会往 stderr 打一些噪音。下面这些是**确认无害**的，按前缀滤掉；
# 其余警告/错误照旧输出 —— 别把真正的问题一起吞了。
#
#   QFont::setPointSize: Point size <= 0
#     某些控件（尤其组合框、滚动条内部）会拿一个没设字号的字体去 set，
#     Qt 收到 -1 后**忽略并沿用原字号**，纯粹是提醒。原因在 Qt 内部，
#     我们的样式表里每条 font-size 都写全了，代码里也没有任何 QFont 用法。
#
#   QFontDatabase: Cannot find font directory
#     开发环境没有 Qt 自带的字体目录时才出现，打包后的 exe 不会有。
_QT_NOISE = (
    "QFont::setPointSize",
    "QFontDatabase: Cannot find font directory",
)


def _qt_message_handler(mode, context, message):
    for noise in _QT_NOISE:
        if message.startswith(noise):
            return
    print(message, file=sys.stderr)



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


def set_windows_app_id(app_id: str = "Mosslight.Launcher"):
    """Windows：把进程的 AppUserModelID 设成自己的

    不设的话任务栏会把我们归到 `python.exe`（源码运行）上，或者跟别的窗口混在
    一起 —— 图标和分组都不对。**必须在建窗口之前调**。
    非 Windows、或者老系统没有这个 API，直接跳过（拿不到不影响使用）。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass        # 设不上就设不上，不该因此起不来


def configure_app(app):
    """设置应用级信息

    Windows 的任务栏、Alt+Tab、窗口标题取的就是这几个值 —— 统一用**完整名**。
    单独抽成函数是为了能测：`ui_check.py` 直接调它，不用去跑整个 main()。
    """
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setApplicationVersion(APP_VERSION)

    # 图标和 AppID 也在这儿设。ui.icons 延迟导入 —— 保持"UI 导入失败也要能弹窗"
    # 这个约定（见下面 main() 里的说明）
    try:
        from ui.icons import app_icon
        app.setWindowIcon(app_icon())
    except Exception as e:
        print(f"[UI] 程序图标加载失败: {e}")

    # 「放自己皮肤」的目录：以前没人创建，用户按设置页说的路径找过去发现不存在，
    # 以为功能坏了。启动时建一下（顺手放份说明）
    try:
        from ui.avatar import ensure_skin_dir
        ensure_skin_dir()
    except Exception as e:
        print(f"[UI] 皮肤目录准备失败: {e}")

    set_windows_app_id()


def main() -> int:
    _make_console_safe()

    # 消息钩子要在建 QApplication 之前装好，不然建它那一步的噪音漏得掉
    qInstallMessageHandler(_qt_message_handler)

    app = QApplication(sys.argv)
    configure_app(app)

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
