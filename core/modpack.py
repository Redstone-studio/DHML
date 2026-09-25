"""Modrinth 整合包（`.mrpack`）解析（纯 Python，**不 import Qt**）

整合包安装的**计划**层，跟 `core/install.py` 是同一个路子：
这里只算"要下什么、要往哪写"，一个字节都不下 —— 真正的下载交给
`core/download.py` 的 `DownloadManager`，进度窗口用现有的 `DownloadWindow`。
**不要为整合包另写一套下载**：引擎里已经有多线程、每字节进度、镜像回退、
失败重试、sha1 校验、`.tmp` 原子改名、取消，全是白拿的。

## `.mrpack` 是什么

一个 ZIP，根目录必须有一份 UTF-8 的 `modrinth.index.json`：

    {
      "formatVersion": 1,
      "game": "minecraft",
      "versionId": "1.8.1",
      "name": "Cobblemon Official Modpack [Fabric]",
      "dependencies": {"minecraft": "1.21.1", "fabric-loader": "0.19.5"},
      "files": [
        {"path": "mods/xxx.jar",
         "hashes": {"sha1": "...", "sha512": "..."},
         "env": {"client": "required", "server": "unsupported"},
         "downloads": ["https://cdn.modrinth.com/data/.../xxx.jar"],
         "fileSize": 2023375}
      ]
    }

外加可选的 `overrides/`、`client-overrides/`、`server-overrides/` 目录：
解压后**按层覆盖**到实例目录（server-overrides 盖 overrides，
client-overrides 再盖一次）。我们是客户端，所以只做 overrides + client-overrides。

规格来源：<https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack>

## 实测（2026-09，Cobblemon Official Modpack [Fabric] 1.8.1）

    .mrpack 96.2 MB / zip 条目 2437 个
    index.files 75 条，共 203.8 MB
    地址域名：cdn.modrinth.com × 75（每条只有 1 个地址）
    hashes：75/75 都带 sha1（正好是 DownloadTask 要的字段）
    env：41 条 client=required/server=unsupported，34 条两边都要
    overrides：2436 个文件（含 config/、options.txt、存档）

## 落点：**版本隔离的实例目录**

`files[].path` 是"相对实例目录"的路径（`mods/xxx.jar`），
所以整包要装进 `<游戏目录>/versions/<版本名>/` —— 也就是 PCL 那种
"每个整合包一个实例目录"。这样它跟别的版本互不干扰，
而且 `core/versions.py` 扫到 `mods/` 特征时会认出这是隔离版本。

## ⚠️ 安全：path 必须挡住目录穿越

`files[].path` 和 zip 里的文件名**都是外部数据**。规格里明确要求：
不能含 `..`、不能是绝对路径、不能带盘符。不挡的话一个恶意整合包
能往系统目录写文件（ZIP 还有 `../../` 这种经典手法）。
`is_safe_relpath()` 就是干这个的，**别绕过它**。
（⚠️ 本模块里别写 Windows 路径字面量：非法转义序列会被 Python 报
SyntaxWarning，注释和 docstring 也一样会被扫到。）
"""

import io
import json
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from core.i18n import tr

INDEX_NAME = "modrinth.index.json"

# 覆盖层目录，**按这个顺序依次应用**（后面的盖前面的）。
# 我们是客户端，所以不含 server-overrides。
OVERRIDE_DIRS = ("overrides", "client-overrides")

# 只接受这两种 env（规格里的取值）
ENV_REQUIRED = "required"
ENV_OPTIONAL = "optional"
ENV_UNSUPPORTED = "unsupported"

# 依赖键 → 内部加载器 key（跟 core/loaders.py 的 LOADER_ORDER 对齐）
# ⚠️ Fabric 在 mrpack 里叫 `fabric-loader`，别写成 `fabric`
DEPENDENCY_LOADERS = {
    "forge": "forge",
    "neoforge": "neoforge",
    "fabric-loader": "fabric",
    "quilt-loader": "quilt",
}

# 缺依赖键时用来判断"这个整合包要不要加载器"
GAME_KEY = "minecraft"

# 支持到哪个 formatVersion（更高版本可能有我们没实现的字段）
SUPPORTED_FORMAT = 1

# 单个 .mrpack 的大小上限（防呆：正常整合包几十到几百 MB）
MAX_MRPACK_BYTES = 2 * 1024 * 1024 * 1024    # 2 GB


# Windows 保留设备名（大小写不敏感）
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM%d" % i for i in range(1, 10)]
    + ["LPT%d" % i for i in range(1, 10)]
)

# 实例目录名长度上限。留足余量：`<游戏目录>/versions/<名字>/mods/<很长的模组文件名>`
# 整体有 MAX_PATH 限制，名字这层别吃掉太多
MAX_INSTANCE_NAME = 64


def sanitize_instance_name(name: str, fallback: str = "modpack") -> str:
    """把整合包名字变成一个**能当目录名**的名字

    真实整合包的名字里什么都有（实测 `Cobblemon Official Modpack [Fabric]`、
    `Better MC [FABRIC] - BMC2`、带 `/` 和 `:` 的也有），直接拿去建目录在
    Windows 上会失败（`< > : " / \\ | ? *`）+ 结尾的点和空格也不合法。

    做法：
      · 非法字符换成 `-`
      · 去掉控制字符
      · 收掉连续空格 / 连字符、去掉首尾的点和空格（`...` / `abc.` 在
        Windows 上是非法名字）
      · 挡住 Windows 保留名（CON / PRN / AUX / NUL / COM1-9 / LPT1-9）——
        用 `CON` 当目录名会直接建不出来
      · 太长的截断（留出 `versions/` 那层的余量）

    返回空串时用 `fallback`，**保证返回的一定能当目录名**。
    """
    text = str(name or "").strip()
    # 控制字符 + Windows 非法字符
    text = "".join("-" if (ch in '<>:"/\\|?*' or ord(ch) < 32) else ch
                   for ch in text)
    # ⚠️ 只收**连续重复**的空白/连字符，别把单个 `-` 也吃掉 ——
    # `Better MC [FABRIC] - BMC2` 里的 `- BMC2` 是名字的一部分，
    # 收掉就变成 `Better MC [FABRIC] BMC2`（用户看得出变了样）。
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"-{2,}", "-", text)
    text = text.strip(" .-")
    if not text:
        return fallback
    # 保留名（`CON.txt` 也不行，所以比前缀）
    stem = text.split(".")[0].upper()
    if stem in _RESERVED_NAMES:
        text = "_" + text
    return text[:MAX_INSTANCE_NAME] or fallback


class ModpackError(RuntimeError):
    """整合包坏了 / 格式不认 / 不安全 —— 消息可以直接给用户看"""


# ============================================================
# 路径安全
# ============================================================

def is_safe_relpath(rel: str) -> bool:
    """这个相对路径能不能安全地拼到实例目录下

    挡的是（规格里点名的几类）：
      · 绝对路径（`/x`、`\\x`）
      · 盘符（`C:/x`、`C:\\x`）
      · 目录穿越（任何一段是 `..`）
      · 空路径、含 NUL 的路径

    ⚠️ 用 `PurePosixPath` 按 `/` 切：zip 里的路径**永远是 `/` 分隔**
    （Windows 上反斜杠也要当分隔符处理，所以先统一换成 `/`）。
    """
    text = (rel or "").strip().replace("\\", "/")
    if not text or "\x00" in text:
        return False
    if text.startswith("/"):
        return False
    # 盘符：`C:` / `c:` 开头
    if len(text) >= 2 and text[1] == ":":
        return False
    parts = PurePosixPath(text).parts
    if not parts or any(p in ("..", "") for p in parts):
        return False
    # `.` 段没意义但也别放进来（拼出来会把目录层级弄乱）
    if any(p == "." for p in parts):
        return False
    return True


def _join_under(root: Path, rel: str) -> Path:
    """把相对路径拼到 root 下，并**再确认一次**结果没跑出 root

    双保险：`is_safe_relpath()` 已经挡了 `..`，这里再比一次绝对路径前缀 ——
    万一以后有人改了上面那个函数，这一层还能兜住（写文件的位置不能只靠一处判断）。
    """
    root = Path(root)
    target = root.joinpath(*PurePosixPath(rel.replace("\\", "/")).parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        raise ModpackError(tr("整合包里的路径不安全，已中止：{path}", path=rel))
    return target


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ModpackFile:
    """`files[]` 里的一条：要下的一个文件"""
    path: str = ""              # 相对实例目录（`mods/xxx.jar`）
    sha1: str = ""
    sha512: str = ""
    size: int = 0
    url: str = ""               # 选中的那个下载地址
    urls: "list[str]" = field(default_factory=list)
    client: str = ENV_REQUIRED
    server: str = ENV_REQUIRED

    @classmethod
    def from_json(cls, data: dict) -> "ModpackFile":
        data = data or {}
        hashes = data.get("hashes") or {}
        urls = [u for u in (data.get("downloads") or []) if isinstance(u, str) and u]
        env = data.get("env") or {}
        return cls(
            path=str(data.get("path") or ""),
            # sha1 一律小写：引擎比对的是小写
            sha1=str(hashes.get("sha1") or "").lower(),
            sha512=str(hashes.get("sha512") or "").lower(),
            size=int(data.get("fileSize") or 0),
            url=urls[0] if urls else "",
            urls=urls,
            client=str(env.get("client") or ENV_REQUIRED),
            server=str(env.get("server") or ENV_REQUIRED),
        )

    def wanted_on_client(self) -> bool:
        """客户端要不要装这个文件

        `unsupported` = 明确"服务端专用"，客户端不该装
        （规格里写得很清楚：`server` 指**专用服务端**）。
        `optional` 我们**当成要装** —— 整合包作者标 optional 通常是
        "可选但推荐"，没装可能少功能；真要给用户勾选是后续的事。
        """
        return self.client != ENV_UNSUPPORTED


@dataclass
class ModpackPlan:
    """一个整合包的安装计划（给引擎喂的清单）"""
    name: str = ""
    version_id: str = ""
    summary: str = ""
    format_version: int = 0
    # 依赖：{"minecraft": "1.21.1", "fabric-loader": "0.19.5"}
    dependencies: "dict[str, str]" = field(default_factory=dict)
    # **客户端该装的那些**（已按 env 过滤）
    files: "list[ModpackFile]" = field(default_factory=list)
    # 被 env 过滤掉的（服务端专用），留个计数给界面说明
    skipped: int = 0
    # 覆盖层：[(zip 里的前缀, 实例目录下的相对路径), ...] 按应用顺序
    overrides: "list[tuple]" = field(default_factory=list)
    warnings: "list[str]" = field(default_factory=list)

    # ---------- 依赖便捷访问 ----------

    @property
    def minecraft(self) -> str:
        """要装哪个原版版本"""
        return str(self.dependencies.get(GAME_KEY) or "")

    def loader(self) -> "tuple[str, str]":
        """`(加载器 key, 版本号)`；没有加载器时 `("", "")`

        ⚠️ 一个整合包**正常只有一个加载器**（Forge 和 Fabric 不会同时出现）。
        真的出现多个时取第一个并记一条 warning —— 静默挑一个更难查。
        """
        found = [(DEPENDENCY_LOADERS[k], v)
                 for k, v in (self.dependencies or {}).items()
                 if k in DEPENDENCY_LOADERS and v]
        if not found:
            return "", ""
        if len(found) > 1:
            self.warnings.append(
                tr("这个整合包声明了多个加载器，只装第一个：{names}",
                   names=", ".join(n for n, _v in found)))
        return found[0]

    @property
    def total_bytes(self) -> int:
        return sum(f.size or 0 for f in self.files)

    def version_json(self, instance_name: str, overrides_written: int = 0) -> dict:
        """这个实例的版本 JSON（写进 `versions/<名字>/<名字>.json`）

        作用只有一个：**让启动器的版本列表认得这个实例**
        （`core/versions.py` 靠"目录里有没有同名 json + 有没有 id"来判定）。

        ⚠️ **原版和加载器不由这里装、也不在这里下载**（用户 2026-09 明确：
        整合包这一路的职责到"把整合包里的文件装好"为止，原版和加载器他自己装）。
        所以：
          · `inheritsFrom` **留空** —— 它现在还没有父版本可指。
            用户装好原版之后，可以在版本设置里指过去（或后续做"补全"时接上）。
          · `mainClass` / `libraries` 也是空的：真正的值在父版本 + 加载器里。
        也就是说这份 JSON 是**"还没接上游戏本体"的实例**，启动器能列出它、
        但直接点启动会缺 `mainClass` —— 这是**如实反映现状**，不是 bug。

        `modpack` 那一段是我们自己的扩展字段：把"这个包要什么原版 / 什么加载器"
        记下来，用户手动装的时候能对上号，排查问题也看得见来源。
        """
        loader_key, loader_version = self.loader()
        return {
            "id": instance_name,
            "type": "release",
            "releaseTime": "1970-01-01T00:00:00+00:00",
            "time": "1970-01-01T00:00:00+00:00",
            "mainClass": "",
            "libraries": [],
            "modpack": {
                "name": self.name,
                "versionId": self.version_id,
                "minecraft": self.minecraft,
                "loader": loader_key,
                "loaderVersion": loader_version,
                "summary": self.summary,
                "overridesWritten": overrides_written,
            },
        }

    def download_tasks(self, instance_dir) -> "list[dict]":
        """变成 `DownloadManager.add_all()` 能直接吃的 dict 列表

        kind 用 `tr("模组")` —— 进度窗口里显示成 `[模组] xxx.jar`，
        跟单装 mod 的行一致（引擎的 kind 是给界面看的前缀）。
        """
        instance_dir = Path(instance_dir)
        out = []
        for f in self.files:
            if not f.url:
                continue                    # `plan_modpack` 已经记过 warning
            name = posixpath.basename(f.path) or f.path
            out.append({
                "url": f.url,
                "path": _join_under(instance_dir, f.path),
                "sha1": f.sha1,
                "size": f.size,
                "label": name,
                "kind": tr("模组"),
            })
        return out


# ============================================================
# 解析
# ============================================================

def _read_index(zf: zipfile.ZipFile) -> dict:
    """读出 `modrinth.index.json` 并做基本校验"""
    try:
        raw = zf.read(INDEX_NAME)
    except KeyError:
        raise ModpackError(tr("这不是整合包：ZIP 里没有 {name}", name=INDEX_NAME))
    except (zipfile.BadZipFile, OSError) as e:
        raise ModpackError(tr("整合包读不了：{err}", err=e))

    try:
        data = json.loads(raw.decode("utf-8-sig"))     # 带 BOM 的也认
    except (ValueError, UnicodeDecodeError) as e:
        raise ModpackError(tr("{name} 不是合法的 JSON：{err}",
                              name=INDEX_NAME, err=e))
    if not isinstance(data, dict):
        raise ModpackError(tr("{name} 的内容不是对象", name=INDEX_NAME))

    fmt = data.get("formatVersion")
    try:
        fmt = int(fmt)
    except (TypeError, ValueError):
        raise ModpackError(tr("整合包没有 formatVersion（看不懂的格式）"))
    if fmt > SUPPORTED_FORMAT:
        # 不硬拒：更高版本的已知字段还是能用的，只提醒一句
        data.setdefault("_warn", []).append(
            tr("整合包格式版本 {fmt} 比本启动器支持的（{max}）新，可能装不全",
               fmt=fmt, max=SUPPORTED_FORMAT))
    game = str(data.get("game") or "minecraft")
    if game != "minecraft":
        raise ModpackError(tr("这个整合包不是给 Minecraft 的：{game}", game=game))
    return data


def build_plan(index: dict, zf: "zipfile.ZipFile | None" = None) -> ModpackPlan:
    """把 `modrinth.index.json` 变成一个 `ModpackPlan`

    `zf` 传进来时顺带扫一遍覆盖层目录（不传就只出 files 那部分清单）。
    """
    deps = index.get("dependencies")
    if not isinstance(deps, dict):
        deps = {}
    deps = {str(k): str(v) for k, v in deps.items() if v}

    plan = ModpackPlan(
        name=str(index.get("name") or ""),
        version_id=str(index.get("versionId") or ""),
        summary=str(index.get("summary") or ""),
        format_version=int(index.get("formatVersion") or 0),
        dependencies=deps,
        warnings=list(index.get("_warn") or []),
    )

    raw_files = index.get("files")
    if not isinstance(raw_files, list):
        raise ModpackError(tr("整合包没有 files 列表"))

    seen = set()
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        f = ModpackFile.from_json(item)
        if not f.path:
            plan.warnings.append(tr("整合包里有一条没有 path 的文件，已跳过"))
            continue
        if not is_safe_relpath(f.path):
            # 规格要求这里挡住；**不静默跳过** —— 这是安全事件，要能看见
            plan.warnings.append(
                tr("整合包里有不安全的路径，已跳过：{path}", path=f.path))
            continue
        if f.path in seen:
            # 同一个落点两条 → 后下的覆盖先下的，结果不确定，直接跳过后面那条
            plan.warnings.append(
                tr("整合包里有重复的落点，只装第一个：{path}", path=f.path))
            continue
        seen.add(f.path)

        if not f.wanted_on_client():
            plan.skipped += 1
            continue
        if not f.url:
            plan.warnings.append(
                tr("这个文件没有可用的下载地址：{path}", path=f.path))
            continue
        plan.files.append(f)

    if not plan.minecraft:
        plan.warnings.append(tr("整合包没说要用哪个 Minecraft 版本"))

    if zf is not None:
        plan.overrides = find_overrides(zf.namelist())
    return plan


def find_overrides(names) -> "list[tuple]":
    """从 zip 条目名里找出覆盖层 → `[(前缀, 相对路径), ...]`

    ⚠️ 返回值是**列表且按 OVERRIDE_DIRS 的顺序**（后面的要盖前面的），
    调用方按顺序展开就行，不要自己去排序 —— 排序会打乱覆盖优先级。
    ⚠️ 不在这里判断"文件还是目录"，也不读内容：调用方直接按条目名展开。
    """
    out = []
    for prefix in OVERRIDE_DIRS:
        head = prefix + "/"
        for name in names or []:
            if not isinstance(name, str) or not name.startswith(head):
                continue
            if name.endswith("/"):
                continue                    # 目录条目，跳过
            rel = name[len(head):]
            if not rel or not is_safe_relpath(rel):
                continue
            out.append((prefix, rel))
    return out


def load_index_from_bytes(blob: bytes) -> dict:
    """从 `.mrpack` 的字节流里读出 index（不解压别的）"""
    if not blob:
        raise ModpackError(tr("整合包是空的"))
    if len(blob) > MAX_MRPACK_BYTES:
        raise ModpackError(tr("这个整合包太大了（超过 {mb} MB），不敢装",
                              mb=MAX_MRPACK_BYTES // 1048576))
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            return _read_index(zf)
    except zipfile.BadZipFile as e:
        # 常是下载到一半 / 下串了（HTML 错误页也会走到这）
        raise ModpackError(tr("整合包不是有效的 ZIP（下载坏了？）：{err}", err=e))


def plan_from_bytes(blob: bytes, instance_dir=None) -> ModpackPlan:
    """一行拿到完整计划（含覆盖层清单）。

    `instance_dir` 只是给调用方方便，`build_plan` 不依赖它 ——
    files 的绝对路径是在 `plan.download_tasks(instance_dir)` 里才算的。
    """
    if not blob:
        raise ModpackError(tr("整合包是空的"))
    if len(blob) > MAX_MRPACK_BYTES:
        raise ModpackError(tr("这个整合包太大了（超过 {mb} MB），不敢装",
                              mb=MAX_MRPACK_BYTES // 1048576))
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            index = _read_index(zf)
            return build_plan(index, zf)
    except zipfile.BadZipFile as e:
        raise ModpackError(tr("整合包不是有效的 ZIP（下载坏了？）：{err}", err=e))


# ============================================================
# 覆盖层展开（纯文件操作，不联网、不碰 Qt）
# ============================================================

def apply_overrides(blob: bytes, plan: ModpackPlan, instance_dir,
                    progress=None) -> "tuple[int, list]":
    """把 `overrides/` + `client-overrides/` 展开到实例目录

    返回 `(写了几个文件, [失败说明...])`。

    ⚠️ **按 `plan.overrides` 的顺序依次写**（server/client 覆盖层要盖上
    普通 overrides）—— 所以这里不重新扫 zip、不自己排序。
    ⚠️ 单个文件失败只记一笔、不影响其它（配置里有个坏文件不该让整包失败）。
    写的时候**保留原有的修改时间**（zip 里有）—— 有些整合包靠时间戳判断
    "这份配置是不是用户改过"，全刷成当前时间会破坏那个判断。
    """
    instance_dir = Path(instance_dir)
    written = 0
    errors = []
    if not plan.overrides:
        return 0, errors

    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as e:
        return 0, [tr("整合包不是有效的 ZIP：{err}", err=e)]

    with zf:
        total = len(plan.overrides)
        for i, (prefix, rel) in enumerate(plan.overrides):
            target = _join_under(instance_dir, rel)
            try:
                data = zf.read("%s/%s" % (prefix, rel))
            except (KeyError, zipfile.BadZipFile, OSError) as e:
                errors.append("%s: %s" % (rel, e))
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                written += 1
            except OSError as e:
                errors.append("%s: %s" % (rel, e.strerror or e))
            if progress is not None:
                try:
                    progress(i + 1, total)
                except Exception:           # noqa: BLE001 —— 回调坏了不该中止安装
                    pass
    return written, errors
