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


def stylesheet_path() -> Path:
    """主题模板的位置

    这个文件里颜色全是 @变量@，由 core/theme.py 在加载时替换。
    """
    return resource_path("assets", "styles", "app.qss")
