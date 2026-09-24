# 版本类型图标

版本列表左边那个方块用的图。**没放图会自动退回原来的文字徽章**（原 / FA / 包），
所以可以慢慢补，不会因为缺一张图就出空白。

## ⚠️ 来源和授权（用之前确认一下）

现在这批图（`vanilla` / `snapshot` / `old` / `forge` / `fabric` / `neoforge` /
`quilt` / `optifine` / `labymod` / `cleanroom`）来自用户提供的
`D:\DHML\从其他地方来的Images\Blocks\`，看文件名和内容**是 PCL2 / PCL2-CE 的素材**
（同一套里还有 `Heads/bangbang93.png`、`PCL-Community.png` 这些）。

- **PCL2-CE 是 GPL-3.0**，跟本仓库一致 —— 用的话在 README 或「关于」里注明出处就行
- **PCL2 原版的协议比 GPL 严格**，如果这批是从原版拿的要再确认
- `Fabric`（线轴）、`NeoForge`（狐狸头）、`Quilt`（拼布）、`OptiFabric`、`LabyMod`、
  `Cleanroom` 这些是**各项目自己的商标**，用来标示"这个版本是哪个加载器"
  属于常规的指示性使用（PCL / HMCL 都这么做）

`pack.png`（整合包箱子）还是从 `E:\item-assets` 挑的物品图，那个是自己找的，没这问题。
**想换回来**：`E:\item-assets` 里的 `GRASS_BLOCK` / `COBBLESTONE` / `ANVIL` / `LOOM` /
`NETHERITE_INGOT` / `WHITE_WOOL` / `SPYGLASS` / `CHEST` 就是原来那套。

## 格式

**PNG，64×64 就够**（列表里那个方块 44 逻辑像素，125% 的屏上是 55 物理像素）。
带透明通道。

- 不用 SVG：这些是渲染好的图，矢量化反而费劲，用户自己换图也麻烦
- 不用 ICO：ICO 是给 **exe / 任务栏**用的（一个文件里塞 16/32/48/256 多档），
  列表里显示的是能随时缩放的小图，PNG 更合适
- **源图不用太大**：64×64 完全够，256×256 缩下来反而更容易发软

## 内置映射

| 文件名 | 什么时候用 | 用的是哪张图 |
|---|---|---|
| `vanilla.png` | 正式版 | `Grass` 草方块 |
| `snapshot.png` | 快照 | `CommandBlock` 命令方块（开发版） |
| `old.png` | 远古版本（alpha / beta） | `CobbleStone` 圆石 |
| `forge.png` | Forge | `Anvil` 铁砧 |
| `fabric.png` | Fabric | `Fabric` 线轴（官方 logo） |
| `neoforge.png` | NeoForge | `NeoForge` 狐狸头（官方 logo） |
| `quilt.png` | Quilt | `Quilt` 拼布（官方 logo） |
| `optifine.png` | OptiFine | `OptiFabric` |
| `labymod.png` | LabyMod | `LabyMod` |
| `cleanroom.png` | Cleanroom | `Cleanroom`（Forge 的分支） |
| `pack.png` | 整合包 | `CHEST` 箱子（来自 item-assets） |

判定顺序：**整合包 → 加载器 → 版本类型**。

- 整合包排最前：右边的标签已经写了加载器名，图标用箱子能多带一点信息
- 加载器里**具体的一定要排在泛化的前面**：
  `neoforge` 里含 `"forge"`、`optifabric` 里含 `"fabric"` ——
  顺序写反了 NeoForge 会显示成铁砧

加新图标：把 png 丢进来，然后在 `ui/icons.py` 的 `_LOADER_ICONS` / `_TYPE_ICONS`
里加一行。只加文件不加映射是**不会生效**的。

## ⚠️ 为什么图标会"糊"，怎么调

调过一轮，记一下结论（免得以后又绕回来）：

1. **显示尺寸是第一位的**。物品图缩到 32 显示会发软，方块从 38 调到 **44**
   （图标 38 逻辑像素）之后就清楚了。**源图分辨率一直够用，不够的是"画多大"**。
2. **高分屏要按 DPR 出图**。屏幕 125% 时那个方块要 55 个物理像素，只给 38 的话
   Qt 会把图拉大 → 糊。`ui/icons.py` 的 `version_pixmap(version, size, dpr)`
   会按 `size * dpr` 缩放，再用 `setDevicePixelRatio()` 告诉 Qt 这是给几倍屏用的。
3. **缩小时分步来**。256 → 38 一次缩 6.7 倍会丢细节；`_scale_crisp()` 先对半砍到
   2 倍以内再收尾。现在这批源图是 64，目标 48 物理像素，几乎不重采样，所以特别清楚。
4. **别指望把小图放大**。平滑放大会糊；最近邻只在"源图是像素画且整数倍"时才有意义。
   58×58 够用是因为目标只有 48 物理像素。

## 用户自定义图标（界面还没做）

`versions.json` 里那个版本写一个 `icon` 字段，`ui/icons.py` 的
`custom_icon_path()` 就去**配置目录**的 `icons/` 里找：

```
<配置目录>/icons/diamond.png        写 "diamond" 或 "diamond.png" 都认
```

配置目录就是设置页底部写的那个；便携模式下就是启动器旁边的 `Mosslight/icons/`。
只接受**文件名，不接受路径**（`../evil.png` 这种会被拒），免得一个配置项能指到别处去。

`icon` 在 `core/version_settings.py` 的 `FIELDS` 里，所以它能正常落盘
（以前没在里面，写进去会被 `_normalize()` 悄悄丢掉 —— 那时"数据侧已经通了"是句空话）。

### ⚠️ 曾经有一份 64 张的内置调色板，2026-09 删了

原来这里是 `assets/icons/version/custom/`：64 张 256×256 的物品图（446 KB），
打算做"从我们给的图里挑一个"的选择界面。后来发现：

- 没有任何界面能用上它（`palette_icons()` 一个调用者都没有）
- 列表里只画 44 px，256×256 的源图纯属浪费（缩到 64 更清楚，也更小）

所以整批删了，现在只剩"用户放自己的图"这一条路。要恢复"内置可选图标"的话，
**记得先把图缩到 64×64**，并在 `core/version_settings.py` 的 FIELDS 里确认 `icon` 还在。

## 程序图标（另一回事）

启动器的**窗口图标 / 任务栏 / exe 图标**是 `assets/icons/icon32/64/128/512.ico`，
`ui/icons.py` 的 `app_icon()` 把四个尺寸塞进同一个 QIcon（Windows 自己挑）。
两个 workflow 里用 `--icon assets/icons/icon512.ico`。