"""模组加载器数据（下载页第二页用；纯 Python，**不 import Qt**）

PCL 第二页那几行是：Forge（展开有「最新版 / 全部版本」）、NeoForge、Fabric、OptiFine…

**我们现在五家都有可靠接口了**（2026-09 实测，见下面的 `_probe` 说明）：
Forge / NeoForge / Fabric / Quilt / OptiFine。
每一行有三种状态，界面必须照实显示：

    ok             能列出并安装
    not_implemented 我们还没做
    error          有接口但这次拉不到（网络问题）—— 这个**不能**说成"还没做"，
                   否则用户以为功能不存在，而其实只是网络抖了一下

## 接口（都实测过，2026-09）

| 加载器 | 接口 | 实测 |
|---|---|---|
| Forge     | BMCLAPI `/forge/minecraft/<mc>` | 200，1.20.1 有 132 条 |
| NeoForge  | BMCLAPI `/neoforge/list/<mc>` | 200，1.20.1 有 60 条 |
| Fabric    | BMCLAPI `/fabric-meta/v2/versions/loader/<mc>` | 200，1.20.1 有 253 条 |
| Quilt     | 官方 `meta.quiltmc.org/v3/versions/loader/<mc>` | 200，1.20.1 有 307 条 |
| OptiFine  | BMCLAPI `/optifine/<mc>` | 200，1.20.1 有 14 条 |

⚠️ **Quilt 没有 BMCLAPI 镜像**：`/quilt-meta/...` 和 `/quilt/...` 都是 **404**
（`COMMON_NO_SUCH_OBJECT`），所以走官方 meta。
⚠️ **Fabric 有 BMCLAPI 镜像**（`/fabric-meta/...`），比官方在国内稳，
所以用它而不是 `meta.fabricmc.net`。
（旧注释说"Fabric 的 meta 接口没镜像"，那是错的 —— 实测有。）
"""

import requests

USER_AGENT = "Mosslight/0.5 (+https://github.com/kongxia114/Mosslight-Launcher)"
TIMEOUT = 20
BMCLAPI = "https://bmclapi2.bangbang93.com"
# ⚠️ Fabric / Quilt 的 meta 是两套不同的服务：Fabric 有 BMCLAPI 镜像
# （国内稳），Quilt **没有**（实测 404），只能走官方。
FABRIC_META = BMCLAPI + "/fabric-meta"
QUILT_META = "https://meta.quiltmc.org"

# 第二页从上到下显示哪些加载器。顺序照 PCL（常用的在前）
#
# ⚠️ LiteLoader / LabyMod / Cleanroom **故意不列**（用户 2026-09 要求）：
#   前两个太老、Cleanroom 是 Forge 的分支，放进这个 UI 只是三行「还没做」
#   占地方。真要支持时再加回来（代码里 KEY 的名字还留着，不受影响）。
LOADER_ORDER = ("forge", "neoforge", "fabric", "quilt", "optifine")

# 显示名（键是内部 key；这些是产品名，不翻译）
LOADER_NAMES = {
    "forge": "Forge",
    "neoforge": "NeoForge",
    "fabric": "Fabric",
    "quilt": "Quilt",
    "optifine": "OptiFine",
    "liteloader": "LiteLoader",
    "labymod": "LabyMod",
    "cleanroom": "Cleanroom",
}

# 有接口的（其余是 not_implemented）
SUPPORTED = ("forge", "neoforge", "fabric", "quilt", "optifine")

STATE_OK = "ok"
STATE_NOT_IMPLEMENTED = "not_implemented"
STATE_ERROR = "error"


def _get_json(url: str, params: dict = None):
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT},
                     timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _version_key(text: str) -> tuple:
    """把版本号转成可比较的元组（`0.19.5` → `(0, 19, 5)`）

    ⚠️ 用**分段数字**比较，不要用字符串比：字符串比会把 `"0.9"` 排在 `"0.19"`
    前面（`"9" > "1"`），于是"最新版"变成 0.9 —— 这是最容易埋进去的一个错。
    预发布后缀（`0.20.0-beta.9`）拿不到正式号时按 0 处理，靠稳定标记决定顺序。
    """
    out = []
    for part in str(text or "").replace("-", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def _rows_from(data, item_parser) -> list:
    """把"接口回包"变成统一的行列表

    ⚠️ `data` 的形状**不由解析器负责**：网络层（`_get_json`）只保证"是个 JSON"，
    回包可能是 dict（某些镜像会把列表包一层）、可能是别的奇怪东西。
    所以这里统一挡一道 —— 形状不对就当空列表，**不要**让
    `for item in 42` 这种东西抛出来（那会一路冒到界面上变成一个看不懂的报错，
    而实际上"这家没数据"是正常情况）。
    """
    if isinstance(data, dict):
        # 有些镜像把列表包一层，比如 `{"versions": [...]}`
        for key in ("versions", "list", "data", "result"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        row = item_parser(item)
        if row is not None:
            out.append(row)
    return _sorted(out)


def parse_forge(data) -> list:
    """BMCLAPI `/forge/minecraft/<mc>` → 统一行（版本号大的在前）"""
    def one(item):
        if not isinstance(item, dict) or not item.get("version"):
            return None
        installer = None
        for f in item.get("files") or []:
            if isinstance(f, dict) and f.get("category") == "installer" \
                    and f.get("format") == "jar":
                installer = f
                break
        return {
            "version": str(item["version"]),
            "build": int(item.get("build") or 0),
            "modified": str(item.get("modified") or ""),
            "stable": True,                 # Forge 不分 release/beta
            "installer_hash": (installer or {}).get("hash"),
            # 排序：Forge 的 version 形如 `1.20.1-47.2.0`，取横线后面那段
            "_sort": _version_key(str(item["version"]).split("-")[-1]),
        }
    return _rows_from(data, one)


def parse_neoforge(data) -> list:
    """BMCLAPI `/neoforge/list/<mc>` → 统一行

    字段很短：`{"mcversion": "1.20.1", "version": "47.1.5", "rawVersion": "1.20.1-47.1.5"}`。
    ⚠️ **版本号用 `version` 而不是 `rawVersion`** —— 界面上显示 `47.1.5`
    比 `1.20.1-47.1.5` 干净（游戏版本已经显示在别处了）。
    """
    def one(item):
        if not isinstance(item, dict):
            return None
        version = str(item.get("version") or "")
        if not version:
            return None
        return {
            "version": version,
            "modified": "",
            "stable": True,
            "_sort": _version_key(version),
        }
    return _rows_from(data, one)


def parse_fabric(data) -> list:
    """Fabric / Quilt 的 `/v2|v3/versions/loader/<mc>` → 统一行

    两家形状一样：`[{"loader": {"version": "0.19.5", "stable": true}, ...}]`。
    ⚠️ `stable` **要留下来**：界面上"Forge 那种不分稳定/测试"和"Fabric 分"
    是不同信息，丢了用户就不知道哪个是稳定版。
    """
    def one(item):
        if not isinstance(item, dict):
            return None
        loader = item.get("loader") or {}
        version = str(loader.get("version") or "")
        if not version:
            return None
        return {
            "version": version,
            "modified": "",
            "stable": bool(loader.get("stable")),
            "_sort": _version_key(version),
        }
    return _rows_from(data, one)


# OptiFine 里**不算正式发布**的类型：
#   "preview"（预览）、"beta"（测试）—— 名字里也常带 `_pre1` / `_pre2` 后缀。
# 这些列在列表里会把"最新版"顶掉，而用户要装的多半是正式版
# （实测 1.21.1：不滤的话 latest 变成 `preview_OptiFine_1.21.1_HD_U_J1_pre15`）。
_OPTIFINE_PREVIEW_TYPES = ("preview", "beta", "alpha", "snapshot")


def parse_optifine(data) -> list:
    """BMCLAPI `/optifine/<mc>` → 统一行

    字段：`{"filename": "OptiFine_1.20.1_HD_U_I5.jar", "type": "HD_U",
            "patch": "I5", "forge": "Forge 47.0.35", "mcversion": "1.20.1"}`。

    版本号从 **filename** 里切（`OptiFine_1.20.1_HD_U_I5.jar` → `HD_U_I5`）：
      1. 去掉 `.jar`
      2. 去掉 `OptiFine_<mcversion>_` 前缀
      3. **再取最后两段**（下划线分段）→ `HD_U_I5`
    第 3 步不能省：预览版的文件名是
    `preview_OptiFine_1.20.1_HD_U_I6_pre6.jar`，只做前两步会得到
    `OptiFine_1.20.1_HD_U_I6_pre6` 这种怪东西（踩过）。

    ⚠️ `forge` 字段是"这个 OptiFine 配哪个 Forge"——只有装了 Forge 才需要，
    所以要带出去给界面（原版 + OptiFine 不用 Forge）。
    ⚠️ 预览版/测试版**不列**（理由见 `_OPTIFINE_PREVIEW_TYPES`）。
    """
    out = []
    if not isinstance(data, (list, tuple)):
        return []
    for item in data:
        if not isinstance(item, dict):
            continue
        vtype = str(item.get("type") or "").lower()
        filename = str(item.get("filename") or "")
        if vtype in _OPTIFINE_PREVIEW_TYPES or filename.lower().startswith("preview_"):
            continue
        name = filename[:-4] if filename.lower().endswith(".jar") else filename
        mc = str(item.get("mcversion") or "")
        prefix = "optifine_%s_" % mc.lower()
        if mc and name.lower().startswith(prefix):
            name = name[len(prefix):]
        parts = [p for p in name.split("_") if p]
        version = "_".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "")
        version = version or str(item.get("patch") or "")
        if not version:
            continue
        out.append({
            "version": version,
            "modified": "",
            "stable": True,
            "filename": filename,
            "forge": str(item.get("forge") or ""),
            "_sort": _version_key(version),
        })
    return _sorted(out)


def _sorted(rows: list) -> list:
    """按版本号**从新到旧**排（`_sort` 是内部字段，排完摘掉）"""
    rows.sort(key=lambda r: r.get("_sort") or (0,), reverse=True)
    for row in rows:
        row.pop("_sort", None)
    return rows


# 各加载器的解析器（`loader_rows` 按 key 取；加新加载器只要往这里加一条）
PARSERS = {
    "forge": parse_forge,
    "neoforge": parse_neoforge,
    "fabric": parse_fabric,
    "quilt": parse_fabric,          # 形状跟 Fabric 一样
    "optifine": parse_optifine,
}


# ---------- 各加载器的接口地址 ----------

def _url_for(key: str, mc_version: str) -> str:
    """这个加载器在哪个地址（都实测过，见模块开头的表）"""
    if key == "forge":
        return "%s/forge/minecraft/%s" % (BMCLAPI, mc_version)
    if key == "neoforge":
        return "%s/neoforge/list/%s" % (BMCLAPI, mc_version)
    if key == "fabric":
        return "%s/v2/versions/loader/%s" % (FABRIC_META, mc_version)
    if key == "quilt":
        return "%s/v3/versions/loader/%s" % (QUILT_META, mc_version)
    if key == "optifine":
        return "%s/optifine/%s" % (BMCLAPI, mc_version)
    raise KeyError(key)


def loader_versions(key: str, mc_version: str) -> list:
    """拉某一家的版本列表（**会联网**）；解析成统一的行

    拉不到会抛异常（由 `loader_rows` 接住并标成 `error`）——
    这样"网络抖动"和"我们还没做"在界面上是两种说法。
    """
    parser = PARSERS.get(key)
    if parser is None:
        # 开发期错误（往 LOADER_ORDER 加了 key 但没写 parser），
        # 会被 loader_rows 接住变成那条 error —— 不走 tr()，
        # 它不该出现在"面向用户的文案"里
        raise KeyError("no parser for loader: %s" % key)
    return parser(_get_json(_url_for(key, mc_version)))


# ---------- 旧的按名字取数据的入口（保留，界面/测试还在用）----------

def forge_versions(mc_version: str) -> list:
    """Forge 各版本，**构建号大的在前**"""
    return loader_versions("forge", mc_version)


def fabric_versions(mc_version: str) -> list:
    """Fabric 加载器各版本（有 stable 标记）"""
    return loader_versions("fabric", mc_version)


def neoforge_versions(mc_version: str) -> list:
    """NeoForge 各版本（新的在前）"""
    return loader_versions("neoforge", mc_version)


def quilt_versions(mc_version: str) -> list:
    """Quilt 各版本（新的在前）"""
    return loader_versions("quilt", mc_version)


def optifine_versions(mc_version: str) -> list:
    """OptiFine 各版本（新的在前）"""
    return loader_versions("optifine", mc_version)


# ---------- 汇总成"第二页那几行" ----------

def loader_rows(mc_version: str) -> list:
    """第二页要显示的行，顺序照 LOADER_ORDER

    每行：
        {"key", "name", "state", "count", "latest", "versions", "error"}

    - `state == "ok"` → count 是版本数，latest 是最新的那个版本号
      ⚠️ `count == 0` 是**正常情况**（这个游戏版本确实还没出这个加载器），
      界面显示成「这个版本没有可用的 X」，不是错误、也不该说"还没做"。
    - `state == "not_implemented"` → 界面显示"还没做"
    - `state == "error"` → 界面显示"拉不到"，带 error 文本；**别写成"还没做"**

    ⚠️ 每一家单独 try：一家的接口挂了不能让其它四家都不显示
    （旧写法用 if/else 把 fabric 和 forge 绑在一句话里，加新家很容易漏）。
    """
    rows = []
    for key in LOADER_ORDER:
        row = {"key": key, "name": LOADER_NAMES.get(key, key),
               "state": STATE_NOT_IMPLEMENTED, "count": 0,
               "latest": "", "versions": [], "error": ""}
        if key in SUPPORTED:
            try:
                versions = loader_versions(key, mc_version)
            except Exception as e:                              # noqa: BLE001
                row["state"] = STATE_ERROR
                row["error"] = "%s: %s" % (type(e).__name__, e)
            else:
                row["state"] = STATE_OK
                row["versions"] = versions
                row["count"] = len(versions)
                if versions:
                    row["latest"] = versions[0]["version"]
        rows.append(row)
    return rows


def row_by_key(rows: list, key: str):
    for row in rows:
        if row["key"] == key:
            return row
    return None


# ---------- 版本文件夹名 ----------

# 各加载器在文件夹名里**怎么拼**（照 PCL 的约定，实测这台机器上的目录名）：
#     1.20.4-Fabric 0.19.5          ← Fabric 用**空格**
#     1.12.2-Forge_14.23.5.2864     ← Forge 用**下划线**
# NeoForge / Quilt / OptiFine 的约定没实测过（这台机器上没有），用空格
# 兜底 —— ⚠️ 这跟 `core/mc_dir.py` 是**兼容**的：那边只要求
# "`-` 后面以加载器名开头"，不管后面跟空格还是下划线（见 match_version）。
_SEPARATOR_UNDERSCORE = ("forge",)
# 文件夹名里用的名字（不要 `.capitalize()`：那会把 NeoForge 变成 Neoforge）
FOLDER_NAMES = {
    "forge": "Forge",
    "neoforge": "NeoForge",
    "fabric": "Fabric",
    "quilt": "Quilt",
    "optifine": "OptiFine",
}


def version_folder_name(mc_version: str, loader_key: str,
                        loader_version: str) -> str:
    """按 PCL 的约定拼版本文件夹名：`26.3-Fabric 0.19.5` / `1.12.2-Forge_14.23.5.2864`

    ⚠️ **这个名字不只是好看**：`<游戏目录>/versions/<名字>/` 就是安装落点，
    而且 `core/mc_dir.py` 的 `match_version()` 靠它反查"这个版本的 mod 该装到哪"。
    所以格式必须跟那边的规则对得上（它比的是"`-` 后面以加载器名开头"）。
    改这里的格式前，先跑 `loaders_data_check` + `gamedir_check`。

    没有加载器（原版）就返回空的 mc_version 拼上 —— 调用方自己判断要不要用。
    """
    mc = str(mc_version or "").strip()
    key = str(loader_key or "").strip().lower()
    lv = str(loader_version or "").strip()
    if not key:
        return mc
    name = FOLDER_NAMES.get(key, key)
    if not lv:
        return "%s-%s" % (mc, name) if mc else name
    sep = "_" if key in _SEPARATOR_UNDERSCORE else " "
    return "%s-%s%s%s" % (mc, name, sep, lv) if mc else "%s%s%s" % (name, sep, lv)


def parse_version_folder_name(folder: str) -> "tuple[str, str, str]":
    """`26.3-Fabric 0.19.5` → `("26.3", "fabric", "0.19.5")`

    反着解一份，给"从已有版本目录猜它是什么加载器"用（界面/排查用）。
    认不出来时加载器和版本号给空串 —— **不要**瞎猜（猜错比空着糟）。
    """
    text = str(folder or "").strip()
    if not text:
        return "", "", ""
    head, sep, tail = text.partition("-")
    if not sep:
        return text, "", ""              # 纯版本号 = 原版
    low = tail.lower()
    for key in FOLDER_NAMES:
        if low.startswith(key):
            # 名字后面跟空格或下划线，再后面是版本号
            rest = tail[len(key):].lstrip(" _")
            return head, key, rest
    return head, "", tail
