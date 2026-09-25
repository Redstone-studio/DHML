"""资源路径解析

为什么需要这个模块：

打包后 PyInstaller 会把 --add-data 的内容放进 exe 旁边的 _internal/ 目录，
并把该目录设置为 sys._MEIPASS。所以 assets/ 这种资源**不能用相对路径打开**：
双击 exe 时工作目录是 exe 所在目录，而不是 _internal/。

这是 0.0.x 系列里"样式一直不生效"的根因 —— 旧的 load_styles() 用的是
open("assets/styles/dark.qss")，而 exe 旁边根本没有 assets/，抛出的
FileNotFoundError 又被静默吞掉了，所以坏了五个版本都没人发现。
"""

import sys
from pathlib import Path


def project_root() -> Path:
    """项目根目录（源码运行时的那个目录）"""
    # core/resources.py -> core -> 项目根
    return Path(__file__).resolve().parents[1]


def app_dir() -> Path:
    """**启动器自己**的目录（便携模式要往这里放数据）

    ⚠️ 不能像 resource_path 那样用 `__file__`：打包后 PyInstaller 把东西放进
    `_internal/`，`__file__` 指的是那里，而用户眼里的"启动器目录"是 **exe 旁边**。
    所以打包后要用 `sys.executable` 的父目录。

    源码运行时就是项目根 —— 开发时数据落在仓库里，正好方便看。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return project_root()


def resource_path(*parts: str) -> Path:
    """定位随程序一起分发的资源

    打包后:  <exe目录>/_internal/<parts...>   （sys._MEIPASS 指向 _internal）
    源码运行: <项目根>/<parts...>
    """
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else project_root()
    return root.joinpath(*parts)


def stylesheet_paths() -> "list[Path]":
    """要加载的全部样式片段，**按加载顺序**

    Qt 的 QSS **没有 @import**（写 `@import "x.qss";` 只会被当成一条无效规则），
    所以"拆成多个文件"唯一的做法就是：读进来、按顺序拼成一个字符串、
    再交给 setStyleSheet()。

    顺序 = `app.qss`（文件头说明 + 侧边栏 + 页面通用）→ `parts/*.qss` 按文件名排序。
    **后面的规则覆盖前面的**，所以 parts 前面的数字是有意义的：
    10 按钮 → 20 输入控件 → 30 徽章 → 40 关于/日志 → 50 内存条 → 60 对话框
    → 70 版本设置页。新加片段按这个规律取名字就行。

    找不到 parts 目录也能跑（只有 app.qss），不会因为少一个文件就整份样式失效。
    """
    base = resource_path("assets", "styles")
    paths = [base / "app.qss"]
    parts_dir = base / "parts"
    if parts_dir.is_dir():
        paths.extend(sorted(p for p in parts_dir.glob("*.qss") if p.is_file()))
    return [p for p in paths if p.is_file()]


def load_stylesheet() -> str:
    """把所有样式片段拼成一份 QSS

    拼的时候给每段加一行出处注释 —— Qt 的样式报错只给行号，不带文件名，
    出了"某条规则没生效"的时候这行注释是唯一能定位到文件的线索。
    注释对 QSS 解析没有影响。
    """
    chunks = []
    for path in stylesheet_paths():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            # 不静默吞掉：少一段样式是肉眼可见的
            print(f"[UI] 样式片段读不了: {path} ({e})")
            continue
        chunks.append(f"/* ======== {path.name} ======== */\n{text}")
    return "\n".join(chunks)
