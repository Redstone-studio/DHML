"""
游戏目录 / 版本文件夹解析

## 为什么要有这个文件

原来下载的落点写的是 `<游戏目录>/mods`，靠 `core.config.get_minecraft_dir()` ——
但**这个项目的 core 里根本没有 config.py**（那是主项目的）。import 直接抛
ImportError，被 `except Exception` 吞掉之后回退成 `Path.home()`。
所以那句"默认落到 <游戏目录>/mods"从来没生效过：每次点下载，文件夹对话框
都在用户主目录里开。

现在照 PCL2 / HMCL 的实际目录结构来：

    <游戏目录>/
        versions/
            1.20.4/                        原版：就一个游戏版本号
                mods/
            1.20.4-Fabric 0.19.5/          Fabric：`<游戏版本>-Fabric <loader 版本>`
                mods/
            1.12.2-Forge_14.23.5.2864/     Forge：**下划线**，不是空格
                mods/

所以"该下到哪" = 拿用户点的那个版本（游戏版本 + 加载器）去 `versions/` 里找
对应文件夹，再进它的 `mods/`（光影是 `shaderpacks/`、资源包是 `resourcepacks/`）。

⚠️ 这个文件**不读设置、不碰界面**，只做"给我一个游戏目录和一个版本，
告诉我要下到哪"。设置从哪来由 ui 那边决定（`ui/widgets/appearance.get_mc_dir`）。
"""
import os
import re
from pathlib import Path

# 项目类型 → (版本文件夹下的子目录, 界面上显示的名字)
# 这几个名字和 Modrinth 的 project_type 对齐（搜索页那几个标签页就是它）
TYPES = {
    "mod": ("mods", "Mod"),
    "shader": ("shaderpacks", "光影"),
    "resourcepack": ("resourcepacks", "资源包"),
    "datapack": ("datapacks", "数据包"),
    # 整合包不是"塞进某个版本"的东西，没有版本文件夹这一说
    "modpack": ("", "整合包"),
}

# 视作"原版"的加载器写法（Modrinth 给的可能是空串）
VANILLA = ("", "vanilla", "原版", "none")

# 猜游戏目录时扫的盘符。只扫这几个，免得在奇怪的机器上卡住
_DRIVE_LETTERS = "CDEFGH"


def subfolder_for(project_type: str) -> str:
    return TYPES.get((project_type or "mod").lower(), TYPES["mod"])[0]


def kind_for(project_type: str) -> str:
    """下载窗口里每行前面那个 `[类型]` 用它"""
    return TYPES.get((project_type or "mod").lower(), TYPES["mod"])[1]


# ---------- 版本文件夹 ----------

def versions_root(mc_dir) -> Path:
    return Path(mc_dir) / "versions"


def list_versions(mc_dir) -> list:
    """`versions/` 下所有"像个版本"的文件夹名

    判断标准：文件夹里有**同名的 .json**（PCL2 / HMCL / 官方启动器都是这个约定）。
    不这么卡的话，`versions/` 里那些缓存、备份、natives 目录也会被当成版本。
    """
    root = versions_root(mc_dir)
    if not root.is_dir():
        return []
    names = []
    try:
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            if (entry / f"{entry.name}.json").is_file():
                names.append(entry.name)
    except OSError:
        return []
    return sorted(names)


def split_version_name(name: str) -> tuple:
    """`1.20.4-Fabric 0.19.5` → `("1.20.4", "Fabric 0.19.5")`

    没有 `-` 的就是纯原版，尾巴给空串。
    """
    head, sep, tail = name.partition("-")
    return (head, tail) if sep else (name, "")


def _numbers(s: str) -> tuple:
    """从 `Fabric 0.19.5` 里抠出版本号元组 `(0, 19, 5)`，用来比大小"""
    return tuple(int(x) for x in re.findall(r"\d+", s))


def match_version(mc_dir, game_version: str, loader: str = ""):
    """在 `versions/` 里找最合适的那个版本文件夹

    返回 `(文件夹名 或 None, 说明)`。说明要给人看 —— "为什么选了这个"
    经常需要回头看（比如两个 Fabric 版本，挑了哪个）。

    匹配规则（从紧到松）：
      1. 名字完全等于游戏版本，且用户点的是原版 → 直接用
      2. `名字的 "-" 前面` == 游戏版本，且 `"-" 后面`以加载器名开头
         （`Fabric 0.19.5`、`Forge_14.23.5.2864` 两种写法都能中）
      3. 名字里同时含游戏版本和加载器名（宽松兜底）
    """
    names = list_versions(mc_dir)
    if not names:
        return None, "versions 目录里没有找到任何版本"
    if not game_version:
        return None, "接口没给游戏版本"

    loader = (loader or "").lower()
    want_loader = loader not in VANILLA

    exact, loose = [], []
    for name in names:
        head, tail = split_version_name(name)

        if not want_loader:
            # 找原版：名字就是游戏版本，或者 `-` 后面不是任何加载器
            if name == game_version or (head == game_version and not tail):
                exact.append(((), name))
            continue

        if head == game_version and tail.lower().startswith(loader):
            # ⚠️ 用 startswith 而不是 in：`in` 会让 forge 命中 neoforge 的文件夹
            exact.append((_numbers(tail), name))
        elif game_version in name and loader in name.lower():
            loose.append(name)

    if exact:
        # 同一个游戏版本+加载器可能装了好几份（Loader 版本不同）→ 取版本号最高的
        exact.sort(key=lambda p: p[0], reverse=True)
        return exact[0][1], f"匹配 {game_version} + {loader}"
    if loose:
        return loose[0], f"名字里同时含 {game_version} 和 {loader}（宽松匹配，自己核对一下）"
    return None, f"没有 {game_version} 的 {loader or '原版'} 版本文件夹"


def target_dir(mc_dir, game_version: str, loader: str = "", project_type: str = "mod"):
    """算出该下到哪个目录，并说明是怎么定下来的

    返回 `(Path 或 None, 说明文本)`。**这里不创建目录** ——
    用户可能在保存对话框里改到别处去，提前建了就是留一堆空文件夹。
    """
    if not mc_dir:
        return None, "还没设置游戏目录"
    mc = Path(mc_dir)
    sub = subfolder_for(project_type)
    name, why = match_version(mc, game_version, loader)
    if name:
        return mc / "versions" / name / sub, f"{name}（{why}）"
    return mc / sub, f"没匹配到版本文件夹（{why}），退回 {mc / sub}"


# ---------- 游戏目录 ----------

def normalize_mc_dir(path) -> Path:
    """把用户选中的目录纠正到 `.minecraft` 那一层

    用户在文件夹对话框里很可能直接点进了 `versions/`、甚至某个版本文件夹里。
    往上找两层，只要能看见 `versions/` 就认那一层。
    """
    p = Path(path)
    for cand in (p, p.parent, p.parent.parent):
        if (cand / "versions").is_dir():
            return cand
    return p


def _drives() -> list:
    if os.name != "nt":
        return [Path("/")]
    return [Path(f"{c}:\\") for c in _DRIVE_LETTERS if Path(f"{c}:\\").is_dir()]


def _candidates() -> list:
    """所有可能是游戏目录的地方（**不去重、不排序**，交给调用方打分）"""
    found = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        found.append(Path(appdata) / ".minecraft")
    found.append(Path.home() / ".minecraft")
    found.append(Path.home() / "AppData" / "Roaming" / ".minecraft")

    # 每个盘符下**第一层**子目录里的 .minecraft：便携整合包的常见布局
    # （这台机器就是 `D:/DHML/.minecraft`）。只扫一层、不递归。
    for drive in _drives():
        try:
            children = list(drive.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if not child.is_dir():
                    continue
            except OSError:
                continue
            found.append(child if child.name == ".minecraft" else child / ".minecraft")

    # 去重（保留先出现的那个），顺手过滤掉 versions 不存在的
    seen, out = set(), []
    for p in found:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if (p / "versions").is_dir():
            out.append(p)
    return out


def score_mc_dir(mc) -> int:
    """这个目录有多"像是拿来玩 mod 的"（用来在多份 .minecraft 里挑一个）

    ⚠️ 光看"%APPDATA%/.minecraft 存在就用它"是不够的 —— 实测这台机器上
    **六份都存在**：AppData 那份是官方启动器的（只有 1.21.11 和两个快照，
    一个 mods 目录都没有），真正在玩的是 `D:/DHML/.minecraft`（Fabric / Forge）。
    照老办法会默认到官方那份去，下完的 mod 根本没地方加载。

    所以就按"有没有装加载器"来打分。

    ⚠️ "有没有 mods 目录"两种布局都要认：PCL2 现在默认**版本隔离**，
    mods 在 `versions/<版本>/mods` 里，根目录那份是空的甚至没有。
    只认根目录的话，版本隔离的安装会被少算一分（实测就是这样被
    `D:/MINECRAFT/.minecraft` 反超的）。
    """
    root = Path(mc)
    names = list_versions(mc)
    if not names:
        return 0
    score = 1                                      # 有个能用的 versions 目录
    loaders = ("fabric", "forge", "neoforge", "quilt")
    if any(any(t in n.lower() for t in loaders) for n in names):
        score += 3                                  # 装了加载器 —— 最有力的信号
    if (root / "mods").is_dir():
        score += 1                                  # 根目录有 mods（非版本隔离）
    else:
        for n in names:
            if (root / "versions" / n / "mods").is_dir():
                score += 1                          # 版本隔离：mods 在版本文件夹里
                break
    return score


def activity_time(mc) -> float:
    """这个目录最后一次"被玩过"的时间戳

    优先看 `versions/*/logs/latest.log` —— 每次启动游戏都会被重写；
    没有就看 `options.txt`（改设置也会写），再退回版本文件夹本身的修改时间。

    ⚠️ 这个判据是必要的：实测这台机器上有 **6 份** .minecraft，
    其中 5 份都装了加载器、分数完全打平（都是 5 分），
    只看"有几个版本"会让 `D:/MINECRAFT/.minecraft`（52 个版本）赢过
    用户真正在用的 `D:/DHML/.minecraft`（11 个版本）。而按最近游玩时间排，
    后者是 09-24 14:31、前者是 09-24 05:11，一眼就分出来了。
    """
    root = Path(mc) / "versions"
    best = 0.0
    try:
        entries = [e for e in root.iterdir() if e.is_dir()]
    except OSError:
        return 0.0
    for v in entries:
        for rel in ("logs/latest.log", "options.txt"):
            try:
                best = max(best, (v / rel).stat().st_mtime)
            except OSError:
                pass
        try:
            best = max(best, v.stat().st_mtime)
        except OSError:
            pass
    return best


# 猜出来的结果缓存住：每次点下载都去列一遍盘符太浪费（虽然也就几十次 stat）
_GUESS_CACHE = []


def guess_mc_dir():
    """没设过游戏目录时猜一个，猜不到返回 None（交给用户自己选）

    在**所有**候选里挑（先比"像不像玩 mod 的"，同分再比最近游玩时间），
    而不是"第一个存在的"。

    猜到的结果**不写回配置**：用户换了机器或插了移动硬盘时，
    猜出来的东西不该被固化下来。
    """
    if _GUESS_CACHE:
        return _GUESS_CACHE[0]

    cands = _candidates()
    if not cands:
        return None
    best = max(cands, key=lambda p: (score_mc_dir(p), activity_time(p)))
    _GUESS_CACHE.append(best)
    return best


def describe_mc_dir(mc) -> str:
    """给设置界面的那句提示：几个版本、有没有加载器"""
    if not mc:
        return "还没找到游戏目录 —— 点「浏览」选到 .minecraft 那一层"
    mc = Path(mc)
    if not (mc / "versions").is_dir():
        return "这个目录里没有 versions 文件夹，可能选错层了"
    names = list_versions(mc)
    if not names:
        return "versions 里没有任何版本（缺同名的 .json，不算数）"
    loaders = []
    for token, label in (("fabric", "Fabric"), ("neoforge", "NeoForge"),
                         ("forge", "Forge"), ("quilt", "Quilt")):
        if any(token in n.lower() for n in names):
            loaders.append(label)
    tail = f"，含 {' / '.join(loaders)}" if loaders else "，只有原版"
    return f"{len(names)} 个版本{tail}"


def forget_guess():
    """清掉猜的缓存（测试和"我换了目录"的时候用）"""
    _GUESS_CACHE.clear()
