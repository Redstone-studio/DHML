# MC Launcher（DHML）

PyQt6 写的 Minecraft 启动器。当前进度：**v0.1.0-alpha**（本地版本扫描 + 界面重做）。
启动游戏、版本下载还没做。

## 本地运行

```bash
pip install -r requirements.txt
python main.py
```

> **强烈建议用本地运行来调界面。** 走 CI 打包的循环是「改代码 → 等 4 分钟构建 →
> 下载 exe → 双击」，调一个颜色要 4 分钟，这样是没法打磨 UI 的。

你的 `.minecraft` 如果不在默认位置（`%APPDATA%\.minecraft`），
在「设置 → 游戏目录」里填一次；或者临时设环境变量：

```powershell
$env:MCLUNCHER_MINECRAFT_DIR = "D:\MINECRAFT\.minecraft"
```

## 目录结构

```
main.py                     入口 + 全局异常钩子
core/                       业务逻辑（不 import PyQt6）
  accounts.py               账户管理（accounts.json）
  config.py                 全局配置（config.json）
  versions.py               扫描 .minecraft/versions
  resources.py              资源路径解析（源码 / 打包都能找到 assets）
  app_info.py               应用名与版本号（单一来源）
ui/                         界面
  main_window.py            侧边栏 + 页面堆栈
  widgets/sidebar.py        导航 + 当前档案卡片
  pages/home_page.py        启动页
  pages/versions_page.py    版本清单
  pages/accounts_page.py    账户管理
  pages/settings_page.py    设置
  dialogs/new_account_dialog.py
assets/styles/dark.qss      深色主题（调色板在文件顶部）
```

## 还没做

- `v0.2.0-alpha` 拼 Java 命令并真正启动游戏
- `v0.3.0-alpha` 设置页补 Java 路径（按版本挑 Java 8 / 17 / 21）
- `v0.4.0-beta` 从 Mojang 拉版本清单下载
- 正版验证 / 第三方验证（UI 占了位，逻辑没实现）
