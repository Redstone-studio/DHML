# DHML

PyQt6 写的 Minecraft 启动器。自己用的，顺手开源。

现在能扫本地的 `.minecraft/versions` 并把它们列出来，界面是深色的。
启动游戏、下载版本这些还没做。

## 跑起来

```
pip install -r requirements.txt
python main.py
```

**界面这块一定在本地跑，别走 CI。** 之前我在网页上改一行 QSS，然后等四分钟构建、
下载、解压、双击 —— 调个颜色这么搞是调不动的。

游戏目录默认在 `%APPDATA%\.minecraft`。我的整合包装在 D 盘，所以是在
「设置 → 游戏目录」里填的；嫌麻烦也可以临时设环境变量：

```powershell
$env:MCLUNCHER_MINECRAFT_DIR = "D:\MINECRAFT\.minecraft"
```

## 目录

```
main.py                   入口，带全局异常钩子
core/                     业务逻辑，这层不 import PyQt6
    accounts.py           账户，读写 accounts.json
    config.py             全局配置
    versions.py           扫描 .minecraft/versions
    resources.py          资源路径，打包后靠它找到 assets
    app_info.py           应用名和版本号，只在这一处
    i18n.py               多语言
ui/                       界面
    main_window.py        侧边栏 + 页面堆栈
    translatable.py       控件基类，管文案
    icons.py              SVG 图标
    widgets/  pages/  dialogs/
assets/                   主题、图标、语言词典
tools/extract_strings.py  文案提取与校验
```

## 几个踩过的坑

**打包后的 exe 一直没加载主题。** v0.0.1 到 v0.0.7 七个发行版，界面都是 Qt 默认
的浅色。原因是 `load_styles()` 用了相对路径 `open("assets/styles/dark.qss")`，
而 PyInstaller 6 会把 `--add-data` 的东西放进 `_internal/`，双击 exe 时的工作目录
又不在那儿，所以永远找不到；偏偏 `except FileNotFoundError: pass` 把异常吞了，
就这么过去了七个版本。现在路径统一走 `core/resources.py`，出错也会打印出来。

**QSS 有两个反直觉的地方**，都写在 `assets/styles/dark.qss` 开头：纯 QWidget
子类不画 `background-color`，得先开 `WA_StyledBackground`；而 `QLabel` 会被全局
的 `QWidget` 规则染色，得显式设成透明。这两个都是"看起来该生效但没生效"的那种，
不加注释下次还会踩。

## 界面文字

不要直接写中文，走文案系统：

```python
self.label("启动游戏", "PageTitle")             # 建控件
self.bind(combo, "搜索版本名…", "placeholderText")
tr("共 {n} 个版本", n=total)                    # 带变量，别用 f-string
```

中文本身就是词典的 key，所以漏翻了顶多退回中文显示，不会把 key 漏到界面上。
列表项、下拉项这类生成的内容，要在页面的 `retranslate()` 里重建，并且先调
`super().retranslate()`。

改完跑一下：

```
python tools/extract_strings.py --write    # 补词典、清掉没人用的条目
python tools/extract_strings.py            # 只检查：漏网的裸中文、f-string、打错的 key
```

## 接下来

```
v0.2.0   拼 Java 命令，真能启动游戏
v0.3.0   设置页补 Java 路径（要按版本挑 8 / 17 / 21）
v0.4.0   从 Mojang 拉版本清单下载
```

正版验证和第三方验证现在只有 UI，逻辑没写。
