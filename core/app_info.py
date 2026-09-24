"""应用元信息

版本号只在这一处维护。
（旧代码把 "v0.1.0-dev" 硬编码在 ui/widgets/sidebar.py 里，而实际发布的
tag 早就到 v0.0.5-alpha 了，界面上显示的版本号是假的。）

## 两个名字是**故意分开**的（用户 2026-09 定）

    APP_NAME         界面上自己叫的名字：侧边栏左上角、关于页。短、干净
    APP_DISPLAY_NAME Windows 那边看到的：窗口标题、任务栏。完整、带 Launcher

所以改名字时想清楚改的是哪一个 —— 只改 APP_NAME 的话任务栏还是老的。

## 还有几处名字**不跟着这两个常量走**（要改全套时得一起动）

- `%APPDATA%\\MCLuncher`（core/config.py 的 DATA_DIR_NAME）：数据目录。
  改它会让老用户的配置变孤儿，得写一次性迁移，所以**这次没动**
- `.github/workflows/*.yml` 里的 `pyinstaller --name`：exe 文件名和压缩包名
- `core/repair.py` 的 USER_AGENT：下载镜像那边看到的名字
"""

APP_NAME = "Mosslight"
APP_DISPLAY_NAME = "Mosslight Launcher"
APP_VERSION = "v0.5.8-dev"
