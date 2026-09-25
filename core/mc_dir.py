"""游戏目录 / 版本文件夹解析（纯 Python，**不 import Qt**）

从 `experiments/Downloading mod test/core/mc_dir.py` 搬过来，按本项目的约定改了：

1. **文案走 `tr()`**（提示语会显示在确认框和状态行上）
2. **游戏目录改从 `core.config` 取** —— 实验项目那份 `core/` 里没有 config.py
   （那是主项目的），所以它自己另写了一套 `appearance.get_mc_dir()` + 独立
   `setting.json`。本项目已经有 `config.minecraft_dir`，不该再开一份配置
3. **`core.versions` 里已有（且经过测试）的安全探测**直接复用，别抄第二份

## 这个文件解决什么问题

下载的落点是**算出来的**，不是写死 `<游戏目录>/mods`：

    <游戏目录>/versions/<版本文件夹>/<mods | shaderpacks | resourcepacks | datapacks>/

照 PCL2 / HMCL 的实际目录结构（**版本隔离**）：

    versions/
        1.20.4/                        原版：就一个游戏版本号
        1.20.4-Fabric 0.19.5/          Fabric：`<游戏版本>-Fabric <loader 版本>`
        1.12.2-Forge_14.23.5.2864/     Forge：**下划线**，不是空格

⚠️ **Modrinth 只知道"1.20.4 + fabric"，拼不出目录名** —— 加载器版本号只有
磁盘上那份才知道。所以必须列 `versions/` 再按名字匹配（见 `match_version`）。

## 为什么不用"包含"匹配加载器名

`"forge" in "1.20.1-neoforge 20.4"` 是 **True** —— 用包含的话，装 NeoForge 的
用户点 Forge 的版本会下进 NeoForge 的文件夹。

⚠️ 实验项目的注释说"必须用 startswith"来防这个 —— **不够**：
`"neoforge_47.1.106".startswith("forge")` 是 False（主判断确实挡住了），
但宽松兜底那条一旦写成 `loader in name.lower()` 就会误命中。
所以这里**按加载器名整体比对**（`_loader_token`），两条路径都挡得住。
"""

import os
import re
from pathlib import Path

from core.i18n import tr

# 项目类型 → (版本文件夹下的子目录, 界面上显示的名字)
# 这几个名字和 Modrinth 的 project_type 对齐（搜索页那几个标签页就是它）
TYPES = {
    "mod": ("mods", "模组"),
    "shader": ("shaderpacks", "光影"),
    "resourcepack": ("resourcepacks", "资源包"),
    "datapack": ("datapacks", "数据包"),
    # 整合包不是"塞进某个版本"的东西，没有版本文件夹这一说
    "modpack": ("", "整合包"),
}

# 视作"原版"的加载器写法（Modrinth 给的可能是空串）
VANILLA = ("", "vanilla", "原版", "none", "全部")

# 猜游戏目录时扫的盘符。只扫这几个，免得在奇怪的机器上卡住
_DRIVE_LETTERS = "CDEFGH"

# 安全探测（跟 core/versions.py 同一套：Python 3.11/3.12 上 Path.is_dir()
# 碰到 WinError 5 会**抛异常**而不是返回 False，真实环境里确实有这种目录）
def _safe_is_dir(path) -> bool:
    try:
        return Path(path).is_dir()
    except (OSError, ValueError):
        return False


def _safe_is_file(path) -> bool:
    try:
        return Path(path).is_file()
    except (OSError, ValueError):
        return False


def subfolder_for(project_type: str) -> str:
    """项目类型 → 版本文件夹下的子目录名（mods / shaderpacks / …）"""
    return TYPES.get((project_type or "mod").lower(), TYPES["mod"])[0]


def kind_for(project_type: str) -> str:
    """下载窗口里每行前面那个 `[类型]` 用它（跟着语言走）"""
    return tr(TYPES.get((project_type or "mod").lower(), TYPES["mod"])[1])


# ---------- 版本文件夹 ----------

def versions_root(mc_dir) -> Path:
    return Path(mc_dir) / "versions"


def list_versions(mc_dir) -> "list[str]":
    """`versions/` 下所有"像个版本"的文件夹名

    判断标准：文件夹里有**同名的 .json**（PCL2 / HMCL / 官方启动器都是这个约定）。
    不这么卡的话，`versions/` 里那些缓存、备份、natives 目录也会被当成版本。
    """
    root = versions_root(mc_dir)
    if not _safe_is_dir(root):
        return []
    names = []
    try:
        for entry in root.iterdir():
            if not _safe_is_dir(entry):
                continue
            if _safe_is_file(entry / ("%s.json" % entry.name)):
                names.append(entry.name)
    except OSError:
        return []
    return sorted(names)


def split_version_name(name: str) -> tuple:
    """`1.20.4-Fabric 0.19.5` → `('1.20.4', 'Fabric 0.19.5')`

    没有 `-` 的就是纯原版，尾巴给空串。
    """
    head, sep, tail = (name or "").partition("-")
    return (head, tail) if sep else (head, "")


def _numbers(text: str) -> tuple:
    """从 `Fabric 0.19.5` 里抠出版本号元组 `(0, 19, 5)`，用来比大小"""
    return tuple(int(x) for x in re.findall(r"\d+", text or ""))


def _loader_token(tail: str) -> str:
    """从 `-` 后面那段里抠出**加载器名**：`NeoForge_47.1.106` → `neoforge`

    分隔符是空格或下划线 —— PCL2 两种都写（`Fabric 0.19.5` /
    `Forge_14.23.5.2864`）。返回小写；空串就是没有加载器。

    ⚠️ **为什么要单独抠出来、而不是直接 `tail.lower().startswith(loader)`**：
    找 forge 时 `"neoforge_47.1.106".startswith("forge")` 是 False，
    看着"能用"；但只要有一处宽松兜底写成 `loader in name.lower()`，
    就会把 NeoForge 的文件夹捞回来。按加载器名**整体**比对最稳。
    """
    token = (tail or "").strip()
    if not token:
        return ""
    for sep in (" ", "_", "-"):
        token = token.split(sep)[0]
    return token.strip().lower()


def _is_loader_of(name: str, loader: str) -> bool:
    """文件夹名里的加载器**是不是** `loader`（宽松兜底用）

    先看 `-` 后面那段的加载器名；对不上再认 `legacy-fabric` 这种**带前缀**的
    写法（Legacy Fabric 的文件夹就长这样）。
    """
    if _loader_token(split_version_name(name)[1]) == loader:
        return True
    low = (name or "").lower()
    return ("legacy-" + loader) in low or ("legacy_" + loader) in low


def match_version(mc_dir, game_version: str, loader: str = ""):
    """在 `versions/` 里找最合适的版本文件夹

    返回 `(文件夹名 或 None, 说明)`。说明是给人看的 —— "为什么选了这个"
    经常需要回头看（比如同一个游戏版本装了两份 Fabric，挑了哪个）。

    匹配规则（从紧到松）：
      1. 要原版：名字**就是**游戏版本，或者 `-` 后面是空的
      2. 要加载器：`-` 前面 == 游戏版本，且 `-` 后面的加载器名整体等于它
      3. 兜底：名字里同时含游戏版本和加载器名（宽松匹配，说明里标出来）
    """
    names = list_versions(mc_dir)
    if not names:
        return None, tr("versions 目录里没有找到任何版本")
    if not game_version:
        return None, tr("接口没给游戏版本")

    want = (loader or "").strip()
    low = want.lower()
    want_loader = low not in VANILLA

    exact, loose = [], []
    for name in names:
        head, tail = split_version_name(name)

        if not want_loader:
            # 找原版：名字就是游戏版本，或者 `-` 后面不是任何加载器
            if name == game_version or (head == game_version and not tail):
                exact.append(((), name))
            continue

        if head == game_version and _loader_token(tail) == low:
            exact.append((_numbers(tail), name))
        elif game_version in name and _is_loader_of(name, low):
            loose.append(name)

    if exact:
        # 同一个游戏版本+加载器可能装了好几份（加载器版本不同）→ 取版本号最高的
        exact.sort(key=lambda pair: pair[0], reverse=True)
        return exact[0][1], tr("匹配 {version} + {loader}",
                               version=game_version, loader=want)
    if loose:
        return loose[0], tr("按名字宽松匹配到 {name}（自己核对一下）", name=loose[0])
    return None, tr("没找到 {version} 的 {loader} 版本文件夹",
                    version=game_version, loader=want or tr("原版"))


def target_dir(mc_dir, game_version: str, loader: str = "",
               project_type: str = "mod"):
    """算出该下到哪个目录，并说明是怎么定下来的

    返回 `(Path 或 None, 说明文本)`。**这里不创建目录** ——
    创建失败要**中止下载**（权限 / 盘符掉线 / 路径太长），由调用方处理：
    否则下载线程会在一个不存在的目录里写 `.tmp`，最后报一个和真实原因
    八竿子打不着的错。
    """
    if not mc_dir:
        return None, tr("还没设置游戏目录")
    mc = Path(mc_dir)
    sub = subfolder_for(project_type)
    name, why = match_version(mc, game_version, loader)
    if name:
        return mc / "versions" / name / sub, "%s（%s）" % (name, why)
    return mc / sub, tr("没匹配到版本文件夹（{why}），退回 {path}", why=why,
                        path=mc / sub)


# ---------- 游戏目录 ----------

def normalize_mc_dir(path) -> Path:
    """把用户选中的目录纠正到 `.minecraft` 那一层

    用户在文件夹对话框里很可能直接点进了 `versions/`、甚至某个版本文件夹里。
    往上找两层，只要能看见 `versions/` 就认那一层。
    """
    p = Path(path)
    for cand in (p, p.parent, p.parent.parent):
        if _safe_is_dir(cand / "versions"):
            return cand
    return p


def _drives() -> list:
    if os.name != "nt":
        return [Path("/")]
    return [Path("%s:\\" % c) for c in _DRIVE_LETTERS
            if _safe_is_dir(Path("%s:\\" % c))]


def _candidates() -> list:
    """所有可能是游戏目录的地方（**不去重、不排序**，交给调用方打分）"""
    found = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        found.append(Path(appdata) / ".minecraft")
    found.append(Path.home() / ".minecraft")

    # 每个盘符下**第一层**子目录里的 .minecraft：便携整合包的常见布局
    # （这台机器就是 `D:/DHML/.minecraft`）。只扫一层、不递归。
    for drive in _drives():
        try:
            children = list(drive.iterdir())
        except OSError:
            continue
        for child in children:
            if not _safe_is_dir(child):
                continue
            found.append(child if child.name == ".minecraft"
                         else child / ".minecraft")

    # 去重（保留先出现的那个），顺手过滤掉 versions 不存在的
    seen, out = set(), []
    for p in found:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if _safe_is_dir(p / "versions"):
            out.append(p)
    return out


def score_mc_dir(mc) -> int:
    """这个目录有多"像是拿来玩 mod 的"（用来在多份 .minecraft 里挑一个）

    ⚠️ 光看"`%APPDATA%/.minecraft` 存在就用它"是不够的 —— 实测这台机器上
    **六份都存在**：AppData 那份是官方启动器的（只有原版），真正在玩的在别处。
    所以按"有没有装加载器"来打分。

    ⚠️ "有没有 mods 目录"两种布局都要认：PCL2 现在默认**版本隔离**，
    mods 在 `versions/<版本>/mods` 里，根目录那份是空的甚至没有。
    只认根目录的话，版本隔离的安装会被少算一分。
    """
    root = Path(mc)
    names = list_versions(mc)
    if not names:
        return 0
    score = 1                                   # 有个能用的 versions 目录
    loaders = ("fabric", "forge", "neoforge", "quilt")
    if any(any(t in n.lower() for t in loaders) for n in names):
        score += 3                              # 装了加载器 —— 最有力的信号
    if _safe_is_dir(root / "mods"):
        score += 1
    else:
        for n in names:
            if _safe_is_dir(root / "versions" / n / "mods"):
                score += 1                      # 版本隔离：mods 在版本文件夹里
                break
    return score


def activity_time(mc) -> float:
    """这个目录最后一次"被玩过"的时间戳

    优先看 `versions/*/logs/latest.log` —— 每次启动游戏都会被重写；
    没有就看 `options.txt`（改设置也会写），再退回版本文件夹本身的修改时间。

    ⚠️ 这个判据是必要的：实测这台机器上有 **6 份** .minecraft，其中 5 份都装了
    加载器、分数完全打平，只看"有几个版本"会让 52 个版本的那份赢过用户真正
    在用的那份。按最近游玩时间排才分得出来。
    """
    root = Path(mc) / "versions"
    best = 0.0
    try:
        entries = [e for e in root.iterdir() if _safe_is_dir(e)]
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
    猜出来的东西不该被固化下来。想固定就去设置页选一次。
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
    """给界面的一句提示：几个版本、有没有加载器"""
    if not mc:
        return tr("还没找到游戏目录 —— 点「浏览」选到 .minecraft 那一层")
    mc = Path(mc)
    if not _safe_is_dir(mc / "versions"):
        return tr("这个目录里没有 versions 文件夹，可能选错层了")
    names = list_versions(mc)
    if not names:
        return tr("versions 里没有任何版本（缺同名的 .json，不算数）")
    loaders = []
    for token, label in (("fabric", "Fabric"), ("neoforge", "NeoForge"),
                         ("forge", "Forge"), ("quilt", "Quilt")):
        if any(token in n.lower() for n in names):
            loaders.append(label)
    if loaders:
        return tr("{n} 个版本，含 {loaders}", n=len(names),
                  loaders=" / ".join(loaders))
    return tr("{n} 个版本，只有原版", n=len(names))


def forget_guess():
    """清掉猜的缓存（测试和"我换了目录"的时候用）"""
    _GUESS_CACHE.clear()


def mc_dir_from_config() -> Path:
    """当前该用的游戏目录 —— **界面的统一入口**

    优先用配置里存过的（`config.minecraft_dir`，就是设置页「游戏目录」那一项）；
    没设过就自动探测一个。跟 `core.versions.default_minecraft_dir()` 的区别是：
    那个不探测、只回退到平台默认路径，结果可能是"官方启动器那份"（往往没有
    mods），而这里会挑"真正在玩的那份"。

    探测结果**不写配置**（理由见 `guess_mc_dir`）。
    """
    from core.config import config

    try:
        saved = str(config.get("minecraft_dir", "") or "").strip()
    except Exception:                       # noqa: BLE001 —— 配置坏了不该挡下载
        saved = ""
    if saved:
        p = Path(saved).expanduser()
        return p
    guessed = guess_mc_dir()
    if guessed is not None:
        return guessed
    from core.versions import default_minecraft_dir
    return default_minecraft_dir()
