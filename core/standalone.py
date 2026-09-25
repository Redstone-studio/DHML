"""把「原版 + 加载器」两层合进**一个**版本目录（纯 Python，**不 import Qt**）

## 为什么要有这个模块

加载器版本一直是**两层**的（PCL / HMCL 都这样）：

    versions/1.20.1/                  原版（客户端 jar + 版本 JSON）
    versions/1.20.1-Forge_47.4.0/     加载器（只有一份 JSON，`inheritsFrom` 指上面）

好处是原版的客户端 jar 只有一份、被同一个游戏版本的所有加载器共用。
代价是**用户看到的版本列表会多出一行**（那个原版），而且每装一个加载器就多
一层；用户 2026-09 明确说过这样"难受、分多版本不方便管理"，要
**一个版本一个独立目录**。

所以这里做的是：加载器装完之后**把两层合起来** ——

1. 加载器那份 JSON 写成**自包含**的（原版的 mainClass / 库 / 参数 / 资源索引
   全都合进去，去掉 `inheritsFrom`），`id` 就是目录名
2. 客户端 jar 放进它自己的目录（默认用**硬链接**指向那份原版，**不额外占盘**；
   不支持硬链接的文件系统上退回复制）
3. 原版那层搬进 `<游戏目录>/.mosslight/vanilla/<版本>/` **藏起来、不外露**，
   但**留着** —— 以后装同一个游戏版本的别的加载器时直接搬回来用，
   不用重新下那 25 MB

⚠️ **只动"我们自己造出来的"那层原版**：用户自己装的原版版本（或已经被别的
版本 `inheritsFrom` 引用的）**一个字节都不碰**（见 `cleanup_vanilla`）。

⚠️ **老的版本照旧能启动**：带 `inheritsFrom` 的版本走 `core/launch.py` 的合并
那条路（那条路一直留着）。这个模块只影响**新装的**版本。
"""

import json
import os
import shutil
from pathlib import Path

from core.i18n import tr

# 藏原版的地方（跟安装器 jar 一个屋檐下）
STASH_DIR_NAME = ".mosslight"
STASH_SUBDIR = "vanilla"

# `core.launch.merge_version()` 会自己算出来的那些键（见下面 make_standalone 的说明）
_MERGE_KEYS = frozenset((
    "id", "mainClass", "type", "javaVersion", "jar", "arguments",
    "minecraftArguments", "libraries", "assetIndex", "assets", "downloads",
    "logging",
))


# ============================================================
# 藏 / 取原版那层
# ============================================================

def stash_root(mc_dir) -> Path:
    """藏起来的那份原版放哪：`<游戏目录>/.mosslight/vanilla/`"""
    return Path(mc_dir) / STASH_DIR_NAME / STASH_SUBDIR


def stashed_dir(mc_dir, mc_id: str) -> Path:
    """某个原版版本藏起来之后的目录（**不判断存不存在**）"""
    return stash_root(mc_dir) / str(mc_id)


def has_stash(mc_dir, mc_id: str) -> bool:
    """藏着一份能用的原版目录吗（有 JSON 才算）"""
    folder = stashed_dir(mc_dir, mc_id)
    return (folder / ("%s.json" % mc_id)).is_file()


def materialize(mc_dir, mc_id: str) -> bool:
    """把藏着的原版**搬回** `versions/<版本>/`，返回是否真搬了

    装加载器之前要调它：安装器（Forge 那几家）和版本 JSON 都要求原版那一层
    在 `versions/` 下露着。已经是露着的话什么都不做。

    ⚠️ 用 `os.rename`（同盘就是改个目录项，瞬间完成），不是复制。
    """
    if not mc_id:
        return False
    mc_dir = Path(mc_dir)
    visible = mc_dir / "versions" / mc_id
    stashed = stashed_dir(mc_dir, mc_id)
    if visible.exists() or not stashed.is_dir():
        return False
    try:
        visible.parent.mkdir(parents=True, exist_ok=True)
        os.rename(stashed, visible)
        return True
    except OSError:
        # 跨盘/被占用：退回复制（慢一点但能用）
        try:
            shutil.copytree(stashed, visible)
            shutil.rmtree(stashed, ignore_errors=True)
            return True
        except OSError:
            return False


def referenced_by(mc_dir, mc_id: str) -> "list[str]":
    """还有哪些版本目录写了 `inheritsFrom: <mc_id>`（自包含的那些不算）

    用来决定"原版那一层能不能收起来"：只要还有一个版本指着它，就不能动。
    """
    out = []
    root = Path(mc_dir) / "versions"
    if not root.is_dir():
        return out
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name == mc_id:
            continue
        path = folder / ("%s.json" % folder.name)
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and str(data.get("inheritsFrom") or "") == mc_id:
            out.append(folder.name)
    return out


def stash(mc_dir, mc_id: str) -> bool:
    """把 `versions/<版本>/` 搬去藏起来，返回是否真搬了

    ⚠️ **调用方必须先确认这层原版是我们自己造的、而且没别的版本指着它**
    （见 `cleanup_vanilla`）—— 这里只管搬。
    """
    if not mc_id:
        return False
    mc_dir = Path(mc_dir)
    visible = mc_dir / "versions" / mc_id
    if not visible.is_dir():
        return False
    stashed = stashed_dir(mc_dir, mc_id)
    try:
        if stashed.exists():
            shutil.rmtree(stashed, ignore_errors=True)
        stashed.parent.mkdir(parents=True, exist_ok=True)
        os.rename(visible, stashed)
        return True
    except OSError:
        return False


def cleanup_vanilla(mc_dir, mc_id: str, created_by_us: bool) -> str:
    """装完之后决定原版那层怎么处理，返回一句给人看的话（给日志用）

    - **不是我们造的**（用户自己装的原版）→ 留着，一个字都不动
    - **还有别的版本 `inheritsFrom` 它**（老的加载器版本、整合包实例）→ 留着
    - 否则 → 搬进 `.mosslight/vanilla/` 藏起来

    ⚠️ 这个判断**不能省**：把用户自己装的原版藏起来，他会以为启动器把他的
    版本吃了；把别人还依赖的原版藏起来，那些版本会直接启动不了。
    """
    if not mc_id:
        return ""
    if not created_by_us:
        return tr("原版 {mc} 本来就有，留着不动", mc=mc_id)
    others = referenced_by(mc_dir, mc_id)
    if others:
        return tr("原版 {mc} 还被 {names} 用着，留着不动",
                  mc=mc_id, names="、".join(others))
    if stash(mc_dir, mc_id):
        return tr("原版 {mc} 收进 .mosslight/vanilla/ 了（以后要用直接搬回来）",
                  mc=mc_id)
    return ""


# ============================================================
# 合两层
# ============================================================

def link_or_copy(src, dst) -> str:
    """尽量用**硬链接**把 `src` 放到 `dst`，不行就复制。返回 `"link"` / `"copy"`

    ⚠️ 为什么优先硬链接：客户端 jar 一个 25 MB 上下，每个自包含版本都复制一份
    的话，装十个版本就白白多占 250 MB。硬链接在同一个盘上是**零成本**的
    （两个目录项指向同一份数据），而且删掉任何一个目录项都不影响另一个。

    FAT32 / exFAT 的 U 盘、或者跨盘的时候硬链接建不了 → 退回复制（照样能用）。
    """
    src, dst = Path(src), Path(dst)
    if dst.exists():
        return "keep"
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        pass
    shutil.copyfile(src, dst)
    return "copy"


def make_standalone(mc_dir, version_id: str, vanilla_id: str = "") -> "tuple[bool, str]":
    """把 `versions/<version_id>/` 写成**自包含**的一份，返回 `(行不行, 说明)`

    做三件事（顺序有讲究）：
      1. 用 `core.launch.load_version()` 把继承链合出来（原版 + 加载器）
      2. 把客户端 jar 放进自己目录 —— 已经有自己那份（OptiFine 装出来的、
         打过补丁的那个）就**不动它**
      3. 写回 JSON：**去掉 `inheritsFrom`**、`id` = 目录名、`jar` 指自己

    ⚠️ 第 2 步失败（原版 jar 根本不在）时**不写 JSON**：写下去就是一份
    "看着自包含、其实没有客户端"的版本，比留着一份带 inheritsFrom 的更糟。
    """
    from core import install as install_mod
    from core.launch import load_version

    mc_dir = Path(mc_dir)
    version_id = str(version_id or "").strip()
    if not version_id:
        return False, tr("没给版本目录名")
    folder = mc_dir / "versions" / version_id
    path = folder / ("%s.json" % version_id)
    if not path.is_file():
        return False, tr("没有版本 JSON：{path}", path=path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return False, tr("版本 JSON 读不了：{err}", err=e)

    parent = str(raw.get("inheritsFrom") or "")
    if not parent:
        # 已经是自包含的（自己就是原版 / 之前合过了）：只需要确认 jar 在
        own = folder / ("%s.jar" % version_id)
        if own.is_file() or not vanilla_id:
            return True, ""
        parent = vanilla_id           # 原版自己那种情况：给它补一个 jar

    # ① 合继承链（这一步会把原版的 mainClass / 库 / 参数 / 资源索引都带过来）
    try:
        merged = dict(load_version(mc_dir, version_id))
    except Exception as e:                              # noqa: BLE001
        return False, tr("合不出这个版本（{err}）", err=e)

    # ② 客户端 jar：自己目录里那个优先（OptiFine 的补丁 jar 就是它）
    own = folder / ("%s.jar" % version_id)
    how = "keep"
    if not own.is_file():
        # 优先用"这个版本自己 JSON 里写的那个 jar 版本"，没有就用调用方给的
        src_id = str(merged.get("jar") or "") or parent
        src = mc_dir / "versions" / src_id / ("%s.jar" % src_id)
        if not src.is_file():
            src = mc_dir / "versions" / parent / ("%s.jar" % parent)
        if not src.is_file():
            return False, tr("找不到客户端 jar（{path}），先不合并",
                             path=src)
        try:
            how = link_or_copy(src, own)
        except OSError as e:
            return False, tr("放客户端 jar 失败：{err}", err=e)

    # ③ 写回自包含那份
    #
    # ⚠️ `load_version()` 只合**固定那几项**（mainClass / 库 / 参数 / 索引…），
    # 我们自己写进去的东西它一个都不认 —— 最典型的是整合包那份 `modpack` 元数据，
    # 还有 `releaseTime` / `time`（版本列表上「发布于 …」就是它）。
    # 不把它们搬过来的话，合并这一步会**悄悄把整合包信息抹掉**
    # （离线测试没抓到，是真装 Forge 整合包时才发现的）。
    merged.pop("inheritsFrom", None)
    merged["id"] = version_id
    for key, value in raw.items():
        if key in _MERGE_KEYS or key == "inheritsFrom":
            continue
        merged[key] = value
    if own.is_file():
        merged["jar"] = version_id
    try:
        install_mod.write_version_json(merged, folder, version_id)
    except Exception as e:                              # noqa: BLE001
        return False, tr("写回版本 JSON 失败：{err}", err=e)

    if how == "link":
        return True, tr("客户端 jar 用硬链接指过去（不额外占盘）")
    if how == "copy":
        return True, tr("客户端 jar 复制了一份（这个盘不支持硬链接）")
    return True, ""
