"""把版本 JSON 变成"要下载哪些文件"（纯 Python，不 import Qt，也不联网）

从 TEST-3.0 的 `Downloader.install_version` 里把**规划**那部分拆出来。
它原来把"读 JSON / 算清单 / 下载 / 解压"揉在一个方法里，导致：

- 没法单独测（要真联网下几个 G 才知道清单算对没有）
- 规则判断、natives 挑拣、资源索引这些容易错的细节全埋在下载流程里

这里只做**规划**：给一个版本 JSON（+ 资源索引 JSON）→ 一串下载任务 + natives 列表。

## 两个刻意的设计

1. **rules 过滤器是注入进来的**（`rules_allow` 参数），不 import `core/launch.py` ——
   那个模块的规则实现是给"启动"用的，安装这边不该跟它耦合；测试里塞个假的就行。
2. **资源索引 JSON 由调用方先下好再传进来** —— 索引本身也是要下载的文件（几十 KB），
   但它是"先有鸡才有蛋"：得先拿到它才知道要下哪些资源。分开之后两边都能单独测。

解压 natives 不在这儿做（那是 `core/natives.py` 的活，已经有了）。
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# core/i18n 是纯 Python（不 import PyQt6），所以 core 里也能直接 tr()。
# 计划里的 kind（[库] / [资源]…）是给界面显示的前缀，要能跟着语言走。
from core.download import USER_AGENT
from core.i18n import tr

# 资源文件（objects）的官方地址；镜像替换交给 core/download.py 的 mirror_url
RESOURCES_BASE = "https://resources.download.minecraft.net"

# 版本清单 / 版本 JSON / 资源索引的重试次数与退避（秒）
JSON_RETRY = 2
JSON_BACKOFF = 0.6


@dataclass
class Task:
    """一个要下载的文件"""
    url: str
    path: Path
    sha1: str = ""
    size: int = 0
    label: str = ""
    # 界面上显示成 `[库] xxx.jar` 那种前缀（跟实验里 `[Mod] xxx` 一个路子）
    kind: str = ""

    def as_tuple(self):
        return (self.url, self.path, self.sha1, self.size, self.label, self.kind)


@dataclass
class InstallPlan:
    """一个版本的安装计划"""
    version_id: str = ""
    version_dir: Path = None
    client_jar: Task = None
    libraries: "list[Task]" = field(default_factory=list)
    assets: "list[Task]" = field(default_factory=list)
    asset_index: Task = None
    logging: Task = None
    # 有 native 的库（下载完由 core/natives.py 解压）
    natives: "list[Task]" = field(default_factory=list)
    # 计划里需要、但 JSON 没给全的东西（调用方决定要不要拦）
    warnings: "list[str]" = field(default_factory=list)

    @property
    def tasks(self) -> "list[Task]":
        """全部下载任务（顺序：客户端 → 库 → natives → 资源索引 → 资源 → 日志）

        ⚠️ **natives 一定要在里面**。第一版漏了它，后果不是"少下几个文件"，
        而是老版本（1.19 之前那些用 classifiers 装 native 的）永远解不出
        lwjgl 的 dll —— 下载看着 100% 完成，游戏起来就崩，而且现场没有任何
        报错线索。真机端到端测试（install_full_check.py）就是这么抓出来的：
        extract_natives 一路报"native 包不在"。
        """
        out = []
        if self.client_jar is not None:
            out.append(self.client_jar)
        out.extend(self.libraries)
        out.extend(self.natives)
        if self.asset_index is not None:
            out.append(self.asset_index)
        out.extend(self.assets)
        if self.logging is not None:
            out.append(self.logging)
        return out

    def total_bytes(self) -> int:
        return sum(t.size or 0 for t in self.tasks)


def write_version_json(vj: dict, version_dir, jar_name: str = "") -> Path:
    """把版本 JSON 落到 `<版本目录>/<名字>.json`

    ⚠️ 这一步**必须做**，而且要在下载之前做。少了它，装完就只有 jar、
    没有 json —— 版本列表是靠 `<目录>/<目录>.json` 认版本的，
    于是"下载明明成功了，列表里却找不到这个版本"（用户 2026-09 报的）。

    注意：**不是**从 jar 里那个 `version.json` 取 —— jar 内那个只有
    id / pack_version / java_version 之类的元数据，是游戏自己用的；
    启动器要的是版本清单（piston-meta）给的这份（downloads / libraries /
    mainClass / assetIndex…），我们本来就下到了，只是以前没落盘。

    文件名跟目录名保持一致（用户改过版本名时用改后的名字）；
    必要时把 JSON 里的 id 也改成新名字，保持"目录 = 文件名 = id"三者一致
    —— 否则启动时 `--version <id>` 和目录对不上。
    """
    version_dir = Path(version_dir)
    name = jar_name or vj.get("id", "")
    data = dict(vj)
    if name and data.get("id") != name:
        data["id"] = name
    version_dir.mkdir(parents=True, exist_ok=True)
    path = version_dir / ("%s.json" % name)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def http_json(url: str, timeout: float = 30):
    """取一个 JSON（版本清单 / 版本 JSON / 资源索引都走它）

    **镜像优先、官方兜底，失败还重试几次。**
    为什么不能只发一次裸请求：这两样是整条链的入口 —— 拿不到清单，
    版本列表就是空的；拿不到版本 JSON，安装直接失败。而它们偏偏是唯一
    **没走镜像**的东西，在 piston-meta 不稳的网络里最容易卡住；
    后面下几千个文件反倒有镜像 + 重试 + 熔断，入口没有，说不过去。

    requests 放在函数里 import：core/install.py 的单元测试是纯离线的，
    不想因为 import 这个模块就顺带把网络栈也拉进来。
    """
    import requests

    from core.download import mirror_url
    mirrored = mirror_url(url)
    candidates = [mirrored] if mirrored == url else [mirrored, url]

    last = None
    for attempt in range(JSON_RETRY + 1):
        for cand in candidates:
            try:
                r = requests.get(cand, headers={"User-Agent": USER_AGENT},
                                 timeout=timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:          # noqa: BLE001
                last = e
        if attempt < JSON_RETRY:
            time.sleep(JSON_BACKOFF * (attempt + 1))
    raise last if last is not None else RuntimeError("取不到 %s" % url)


def looks_like_launcher_json(vj) -> bool:
    """这份 JSON 是"启动器要的那份"吗（而不是 jar 里那个 422 字节的元数据）

    ⚠️ 用户真踩过：版本目录里只有 jar，于是从压缩包里把 jar 内的
    `version.json` 拖出来当版本 JSON 用。那个文件只有
    id / pack_version / java_version，**没有** mainClass / libraries / downloads，
    拿它启动必然崩，而且"补全"也救不了 —— 补全得先知道该下什么。

    判据：有 mainClass，并且有 libraries / downloads / inheritsFrom 之一。
    """
    if not isinstance(vj, dict):
        return False
    if not vj.get("mainClass"):
        return False
    return bool(vj.get("libraries")) or bool(vj.get("downloads")) \
        or bool(vj.get("inheritsFrom"))


def rebuild_version_json(mc_dir, version_id: str, version_dir=None) -> bool:
    """按版本名去**官方清单**里把版本 JSON 取回来并覆盖写盘

    专治"有 jar 但 JSON 不对/来自 jar"：缺了正确的那份，什么都规划不出来
    （不知道该下哪些库和资源，也不知道 mainClass），而这个版本其实
    清单里就有 —— 把 JSON 取回来的成本只有几十 KB，jar 一个字节都不用重下。

    返回是否真的重建了（清单里找不到这个名字就返回 False）。
    """
    from core import versions_remote as vr

    data, _info = vr.load_manifest()
    if not data:
        return False
    entry = None
    for item in data.get("versions", []):
        if item.get("id") == version_id and item.get("url"):
            entry = item
            break
    if entry is None:
        return False
    vj = http_json(entry["url"])
    write_version_json(vj, version_dir or (Path(mc_dir) / "versions" / version_id),
                       version_id)
    return True


def load_asset_index(mc_dir, vj: dict, fetch=None):
    """拿资源索引 JSON：**本地有就用本地的**，没有才用 `fetch(url)` 去下

    为什么本地优先：重装 / 补全的时候索引基本都已经在盘上了（几十 KB 到几 MB），
    没必要为了列一遍资源再联网取一次。索引坏了就当没有，继续往下走网络。

    拿不到就返回 None —— 那也能干活，只是资源文件列不出来（客户端和库照下）。
    """
    meta = vj.get("assetIndex") or {}
    index_id = meta.get("id", "")
    if index_id:
        local = Path(mc_dir) / "assets" / "indexes" / ("%s.json" % index_id)
        try:
            if local.is_file():
                return json.loads(local.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass                    # 坏了就走网络那份
    url = meta.get("url")
    if url:
        try:
            return (fetch or http_json)(url)
        except Exception:           # noqa: BLE001
            return None
    return None


def maven_to_path(name: str) -> str:
    """`group:artifact:version` → `group/path/artifact/version/artifact-version.jar`

    3.0 的 util 里也有一个，逻辑一样，这里自带一份免得又去依赖那个实验模块。
    """
    parts = (name or "").split(":")
    if len(parts) < 3:
        return ""
    group, artifact, version = parts[0], parts[1], parts[2]
    # 有的版本带了 classifier（`group:artifact:version:classifier`），文件名要带上
    classifier = ""
    if len(parts) >= 4 and parts[3]:
        classifier = "-" + parts[3]
    return "%s/%s/%s/%s-%s%s.jar" % (group.replace(".", "/"), artifact, version,
                                     artifact, version, classifier)


def _native_key_for_this_os(lib: dict):
    """这个库在当前系统上要哪个 native（没有就返回空串）

    `natives` 长这样：{"windows": "natives-windows", "linux": "natives-linux"}
    """
    natives = lib.get("natives") or {}
    if os.name == "nt":
        return natives.get("windows", "")
    if os.sys.platform == "darwin":
        return natives.get("osx", natives.get("macos", ""))
    return natives.get("linux", "")


def plan_version(vj: dict, mc_dir, rules_allow=None,
                 asset_index: dict = None, jar_name: str = "") -> InstallPlan:
    """算出一个版本的安装计划

    参数：
        vj           版本 JSON（**已经合并过 inheritsFrom** —— 那是 core/launch.py 的活）
        mc_dir       .minecraft 目录
        rules_allow  规则过滤器：`f(rules) -> bool`，默认全放行
        asset_index  资源索引 JSON（由调用方先下好；没有就只规划索引本身）
        jar_name     客户端 jar 的文件名（默认用 vj["id"]）—— 用户改了版本名时用它
    """
    mc_dir = Path(mc_dir)
    version_id = vj.get("id", "")
    version_dir = mc_dir / "versions" / (jar_name or version_id)
    allow = rules_allow or (lambda _rules: True)

    plan = InstallPlan(version_id=version_id, version_dir=version_dir)

    # ---------- 客户端 jar ----------
    client = (vj.get("downloads") or {}).get("client") or {}
    if client.get("url"):
        plan.client_jar = Task(
            url=client["url"],
            path=version_dir / ("%s.jar" % (jar_name or version_id)),
            sha1=client.get("sha1", ""),
            size=client.get("size", 0),
            label="client.jar", kind=tr("客户端"))
    else:
        plan.warnings.append("版本 JSON 里没有客户端 jar（downloads.client）")

    # ---------- 库 + natives ----------
    for lib in vj.get("libraries") or []:
        if not allow(lib.get("rules") or []):
            continue

        downloads = lib.get("downloads") or {}
        artifact = downloads.get("artifact") or {}
        if artifact.get("url") and artifact.get("path"):
            plan.libraries.append(Task(
                url=artifact["url"],
                path=mc_dir / "libraries" / artifact["path"],
                sha1=artifact.get("sha1", ""),
                size=artifact.get("size", 0),
                label=Path(artifact["path"]).name, kind=tr("库")))
        elif lib.get("name") and not downloads.get("classifiers"):
            # 老版本 / Forge 的库常常只写 name（没有 downloads 段）→ 按 maven 规则拼。
            #
            # ⚠️ 但**只有 classifiers 的库千万别拼**：像
            #     org.lwjgl.lwjgl:lwjgl-platform:2.9.0、net.java.jinput:jinput-platform:2.0.5
            # 这类库压根没有主 artifact（官方启动器也不下它），
            # 按 name 拼出来的 lwjgl-platform-2.9.0.jar 必然 404 ——
            # 用户会在进度窗口里看到几条刺眼的"失败"，其实什么都不缺。
            # 它们的价值全在下面的 classifiers（native）。
            rel = maven_to_path(lib["name"])
            if rel:
                base = (lib.get("url") or "https://libraries.minecraft.net/").rstrip("/")
                plan.libraries.append(Task(
                    url="%s/%s" % (base, rel),
                    path=mc_dir / "libraries" / rel,
                    label=Path(rel).name, kind=tr("库")))
            else:
                plan.warnings.append("库名看不懂，跳过：%s" % lib.get("name"))

        # native（老版本才需要：把 dll/so 从 jar 里解出来）
        key = _native_key_for_this_os(lib)
        if key:
            native = (downloads.get("classifiers") or {}).get(key) or {}
            if native.get("url"):
                plan.natives.append(Task(
                    url=native["url"],
                    path=mc_dir / "libraries" / native.get("path", ""),
                    sha1=native.get("sha1", ""),
                    size=native.get("size", 0),
                    label=Path(native.get("path", "")).name,
                    kind=tr("原生库")))
            else:
                plan.warnings.append("这个库说要 native（%s）但 JSON 里没有：%s"
                                     % (key, lib.get("name", "")))

    # ---------- 资源索引 ----------
    index = vj.get("assetIndex") or {}
    if index.get("url"):
        plan.asset_index = Task(
            url=index["url"],
            path=mc_dir / "assets" / "indexes" / ("%s.json" % index.get("id", "index")),
            sha1=index.get("sha1", ""),
            size=index.get("size", 0),
            label="assets index", kind=tr("资源索引"))

    # ---------- 资源文件（objects）----------
    if asset_index:
        objects = (asset_index.get("objects") or {})
        for name, info in objects.items():
            h = info.get("hash", "")
            if not h:
                continue
            plan.assets.append(Task(
                url="%s/%s/%s" % (RESOURCES_BASE, h[:2], h),
                path=mc_dir / "assets" / "objects" / h[:2] / h,
                sha1=h,                      # 资源文件的 sha1 就是它的文件名
                size=info.get("size", 0),
                label=name, kind=tr("资源")))

    # ---------- log4j 配置（1.7~1.17 的日志配置，有就下）----------
    logging_cfg = (vj.get("logging") or {}).get("client") or {}
    log_file = logging_cfg.get("file") or {}
    if log_file.get("id") and log_file.get("url"):
        plan.logging = Task(
            url=log_file["url"],
            path=mc_dir / "assets" / "log_configs" / log_file["id"],
            sha1=log_file.get("sha1", ""),
            size=log_file.get("size", 0),
            label="log4j config", kind=tr("日志配置"))

    return plan


# ============================================================
# 安装：把计划交给下载引擎，然后解压 natives
# ============================================================

def start_install(plan: InstallPlan, manager) -> int:
    """把计划里的任务全部交给下载管理器（`core/download.py` 的 DownloadManager）

    返回任务数。**只负责塞任务，不启动** —— 什么时候 start 由调用方决定
    （界面要先把进度窗口摆出来再开跑，否则前几百毫秒的进度会丢）。

    ⚠️ 引擎那边已经处理了：多线程、每字节进度、速度、取消、sha1 校验、
    镜像优先 + 官方回退、`.tmp` + 原子改名。这里不要再自己写一套。
    """
    tasks = plan.tasks
    manager.add_all([t.as_tuple() for t in tasks])
    return len(tasks)


def extract_natives(plan: InstallPlan, natives_dir, only_if_missing: bool = True):
    """把 natives 库里的 dll/so 解到 natives_dir

    返回 (解压了几个, [失败说明...])。

    - **跳过 META-INF/**：那是签名信息，解出来没用还可能干扰
    - `only_if_missing=True` 时，目录里已经有 dll/so 就整个跳过（幂等，
      重复点"安装"不该反复解压）
    - 单个 jar 坏了只记一笔，不影响其它的（老版本缺一个 dll 也能起，缺多了才炸）
    """
    import zipfile

    natives_dir = Path(natives_dir)
    errors = []
    if not plan.natives:
        return 0, errors

    if only_if_missing and natives_dir.is_dir():
        if any(natives_dir.glob("*.dll")) or any(natives_dir.glob("*.so")) \
                or any(natives_dir.glob("*.dylib")):
            return 0, errors            # 已经有解压好的，不重复干

    natives_dir.mkdir(parents=True, exist_ok=True)
    done = 0
    for task in plan.natives:
        jar = Path(task.path)
        if not jar.is_file():
            errors.append("native 包不在：%s" % jar.name)
            continue
        try:
            with zipfile.ZipFile(jar, "r") as z:
                for name in z.namelist():
                    if name.startswith("META-INF/"):
                        continue
                    if name.endswith("/"):
                        continue
                    try:
                        z.extract(name, natives_dir)
                        done += 1
                    except OSError as e:
                        errors.append("%s 解压失败：%s" % (name, e))
        except zipfile.BadZipFile:
            errors.append("%s 不是有效的 zip（下载坏了？）" % jar.name)
        except OSError as e:
            errors.append("%s 读不了：%s" % (jar.name, e))
    return done, errors
