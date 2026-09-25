"""模组加载器安装：算"该下什么、该写什么"（纯 Python，**不 import Qt**）

界面只做两件事：把这里算出来的任务塞进下载引擎（`core/download.py`）、
下载完把 JSON 落盘（`write_profile()`）。**不碰网络以外的副作用**。

## 两类加载器

| 类型 | 谁 | 怎么装 |
|---|---|---|
| 有 profile JSON | **Fabric / Quilt** | 一个 GET 就拿到启动器要的版本 JSON（mainClass + libraries 都写好了）→ 下库 → 落盘。**不用 Java** |
| 只有安装器 jar | Forge / NeoForge / OptiFine | 得下安装器再 `java -jar installer.jar --installClient` —— 那是另一条路，见 `installer_loaders()` |

`installable(key)` 给界面判断用：能装的直接装，不能装的老实说
「还得跑安装器」，**别假装能装**（这是 `core/loaders.py` 那套
"能装 / 还没做 / 拉不到"三态一路下来的规矩）。

## 落盘约定（跟 PCL 一致，`core/mc_dir.match_version()` 认的就是它）

    versions/<mc>/<mc>.json                            原版（加载器版本 inheritsFrom 它）
    versions/<mc>-Fabric 0.19.5/<同名>.json            加载器 profile
    versions/<mc>-Fabric 0.19.5/<同名>-natives/        启动时解出来的 natives
    versions/<mc>-Fabric 0.19.5/mods/<Fabric API>.jar  mod 走**版本隔离**目录

⚠️ 加载器版本 JSON 里的 **`inheritsFrom` 一定要写**：`core/launch.py` 的
`merge_version()` 靠它去合原版的 mainClass / 参数 / 资源索引，并且顺带把
客户端 jar 指到原版目录（`jar` 字段回退成父版本 id）。少写这一行，
classpath 里就没有 Minecraft 的类，Fabric 会报 "couldn't locate the game"。

⚠️ 名字跟原版 id **撞了**（用户手改名字改成 `1.21.4`）时不能再写
`inheritsFrom` —— 那是自己继承自己，读取时会撞上 `MAX_INHERIT_DEPTH`。
这种情况改成**把原版合进来、写成自包含的一份**（见 `profile_version_json`）。
"""

from dataclasses import dataclass, field
from pathlib import Path

from core import install as install_mod
from core import loaders as loaders_mod
from core.i18n import tr

# 有干净 profile JSON 的（不用 Java 就能装）
PROFILE_LOADERS = ("fabric", "quilt")
# 只有安装器 jar 的（要 Java 跑 installer，还没做）
INSTALLER_LOADERS = ("forge", "neoforge", "optifine")

# Fabric / Quilt 的 profile 里，库默认挂在 maven 的根上
DEFAULT_MAVEN = "https://maven.fabricmc.net/"

# Fabric API 这个 mod 的 Modrinth 项目 id（要装它得先选 Fabric）
FABRIC_API_PROJECT = "fabric-api"


class LoaderError(RuntimeError):
    """加载器装不了 / 数据不对（信息是给人看的，会显示在界面上）"""


def installable(key: str) -> bool:
    """这个加载器现在**能不能**由启动器自己装（不用 Java）"""
    return (key or "").lower() in PROFILE_LOADERS


def installer_loaders() -> tuple:
    """要跑安装器 jar 的那几家（界面据此说"还得跑安装器"）"""
    return INSTALLER_LOADERS


# ============================================================
# 取 profile
# ============================================================

def profile_url(mc_version: str, key: str, loader_version: str) -> str:
    """profile JSON 的地址；这家没有 profile（要跑安装器）时返回空串

    ⚠️ Fabric 走 **BMCLAPI 镜像**（`/fabric-meta/...`，实测同一个文件、
    国内更快）；Quilt **没有镜像**（`/quilt-meta/...` 实测 404），只能走官方。
    跟 `core/loaders.py` 里版本列表那两个地址是同一套规则。
    """
    key = (key or "").lower()
    mc = str(mc_version or "").strip()
    lv = str(loader_version or "").strip()
    if not (mc and lv):
        return ""
    if key == "fabric":
        return "%s/v2/versions/loader/%s/%s/profile/json" % (
            loaders_mod.FABRIC_META, mc, lv)
    if key == "quilt":
        return "%s/v3/versions/loader/%s/%s/profile/json" % (
            loaders_mod.QUILT_META, mc, lv)
    return ""


def check_profile(data, key: str = "", loader_version: str = "") -> dict:
    """确认拿到的是"启动器要的那份 profile"（**纯函数**，离线可测）

    只认 `mainClass` + 非空 `libraries`：少了任何一个，写出去的版本 JSON
    要么起不来、要么少库，而这两种失败在界面上都只表现为"启动崩了"，
    离真正的原因很远。
    """
    if not isinstance(data, dict):
        raise LoaderError(tr("这个加载器没有可用的版本数据"))
    name = loaders_mod.LOADER_NAMES.get((key or "").lower(), key or "")
    if not str(data.get("mainClass") or "").strip():
        raise LoaderError(tr("{name} 的版本数据里没有主类，装不了", name=name))
    libs = data.get("libraries")
    if not isinstance(libs, list) or not libs:
        raise LoaderError(tr("{name} 的版本数据里没有库列表，装不了", name=name))
    return data


def fetch_profile(mc_version: str, key: str, loader_version: str) -> dict:
    """取 profile JSON（镜像 + 重试走 `core/install.py` 那个 `http_json`）

    网络不通 / 那家要跑安装器 → 抛 `LoaderError`（信息能直接显示给用户）。
    """
    url = profile_url(mc_version, key, loader_version)
    if not url:
        name = loaders_mod.LOADER_NAMES.get((key or "").lower(), key or "")
        raise LoaderError(tr("{name} 得跑安装器才能装，这个还没做", name=name))
    try:
        data = install_mod.http_json(url)
    except Exception as e:                          # noqa: BLE001
        raise LoaderError(tr("拿不到 {name} 的版本数据（网络问题）", name=key)) from e
    return check_profile(data, key, loader_version)


# ============================================================
# 算任务
# ============================================================

def _platform_rules():
    """按当前系统挑库的规则过滤器（`core/launch.py` 的那套）

    放在函数里 import：`core/loader_install.py` 的单元测试是纯离线的，
    不想因为 import 这个模块就顺带把启动那一套也拉进来。
    """
    from core.launch import rules_allow
    return rules_allow


def library_tasks(vj: dict, mc_dir, rules_allow=None) -> list:
    """profile 里的 `libraries` → 下载任务（**纯函数**）

    ⚠️ **不复用 `install.plan_version()`**：Fabric / Quilt 的 profile 里库是
    `{name, url, sha1, size}` 这种 maven 简写，**没有 `downloads.artifact`**。
    `plan_version()` 会走"按 name 拼 maven 路径"的兜底分支，那条分支
    **不带 sha1、也不带 size** —— 结果是既没有完整性校验，进度条也算不出总量
    （用户会看到一条没有百分比、速度也不准的进度窗口）。这里自己拼，
    把能拿到的 sha1 / size 都带上。

    `downloads.artifact` 那种（Mojang 风格）照样认，方便以后复用。
    """
    mc_dir = Path(mc_dir)
    allow = rules_allow or (lambda _rules: True)
    out = []

    for lib in vj.get("libraries") or []:
        if not isinstance(lib, dict):
            continue
        if not allow(lib.get("rules") or []):
            continue

        artifact = ((lib.get("downloads") or {}).get("artifact") or {})
        if artifact.get("url") and artifact.get("path"):
            rel = artifact["path"]
            task = install_mod.Task(
                url=artifact["url"], path=mc_dir / "libraries" / rel,
                sha1=artifact.get("sha1", ""), size=artifact.get("size", 0),
                label=Path(rel).name, kind=tr("库"))
            out.append(task)
            continue

        name = lib.get("name") or ""
        if not name or ":" not in name:
            continue
        # ⚠️ 只有 classifiers（native）的库别拼主 artifact：它压根没有主 jar，
        # 拼出来的路径必然 404（跟 plan_version 里那条注释一个道理）
        if (lib.get("downloads") or {}).get("classifiers"):
            continue
        rel = install_mod.maven_to_path(name)
        if not rel:
            continue
        base = (lib.get("url") or DEFAULT_MAVEN).rstrip("/")
        out.append(install_mod.Task(
            url="%s/%s" % (base, rel), path=mc_dir / "libraries" / rel,
            sha1=lib.get("sha1", ""), size=lib.get("size", 0),
            label=Path(rel).name, kind=tr("库")))
    return out


def profile_version_json(profile: dict, mc_version: str, version_id: str,
                         vanilla_vj: dict = None) -> dict:
    """profile → 存盘用的版本 JSON（**纯函数**）

    正常情况：改个 `id`、写上 `inheritsFrom` 就成了。

    ⚠️ 例外：`version_id == mc_version`（用户把名字改成了原版 id）时
    **不能**写 `inheritsFrom` —— 那是自己继承自己，`load_version()` 会一路
    递归到 `MAX_INHERIT_DEPTH` 然后报错。这种情况把原版合进来写成自包含的一份
    （要调用方把原版 JSON 通过 `vanilla_vj` 传进来；没传就只留 profile 自己）。
    """
    data = dict(profile or {})
    data["id"] = version_id or data.get("id", "")
    data.setdefault("type", "release")

    if version_id and version_id == mc_version:
        if isinstance(vanilla_vj, dict) and vanilla_vj:
            from core.launch import merge_version
            merged = merge_version(vanilla_vj, data)
            merged.pop("inheritsFrom", None)
            merged["id"] = version_id
            # jar 指回自己：合并时它被写成父版本 id（原版 id），
            # 而自包含版本的 jar 就在本目录、名字是本目录名
            merged["jar"] = version_id
            return merged
        data.pop("inheritsFrom", None)
        return data

    data["inheritsFrom"] = mc_version
    return data


def mod_task(version: dict, mods_dir) -> "install_mod.Task":
    """Modrinth 的一个版本 → 一条 mod 下载任务（**纯函数**）

    ⚠️ 必须用 `primary_file()`：一个版本可能带 sources / javadoc，
    那些塞进 `mods\\` 只会让游戏报错。
    """
    from core.modrinth_api import primary_file
    f = primary_file(version)
    if not f:
        raise LoaderError(tr("这个 mod 版本没有可下载的文件"))
    filename = f.get("filename") or ""
    if not filename:
        raise LoaderError(tr("这个 mod 版本没有文件名"))
    return install_mod.Task(
        url=f.get("url", ""), path=Path(mods_dir) / filename,
        sha1=(f.get("hashes") or {}).get("sha1", ""),
        size=f.get("size", 0), label=filename, kind=tr("模组"))


# ============================================================
# 打包成一个计划
# ============================================================

@dataclass
class LoaderPlan:
    """一个加载器的安装计划

    `tasks` = 加载器的库 + 一起装的 mod（Fabric API），**不含**原版那一堆
    —— 原版的计划由 `core/install.py` 的 `plan_version()` 出，两边任务
    塞进同一个下载窗口。
    """
    key: str = ""
    version: str = ""
    version_id: str = ""
    version_dir: Path = None
    version_json: dict = field(default_factory=dict)
    libraries: "list" = field(default_factory=list)
    mods: "list" = field(default_factory=list)
    warnings: "list" = field(default_factory=list)
    # ---- 下面几个只有「要跑安装器」那几家才用得上（见 installer_plan）----
    installer: bool = False            # 这份计划靠跑安装器落地，不是靠我们自己写 JSON
    installer_jar: Path = None         # 安装器 jar 落在哪（下载任务的目标）
    installer_extra: dict = field(default_factory=dict)
    vanilla_vj: dict = field(default_factory=dict)   # 原版 JSON（挑 Java 用得上）

    @property
    def tasks(self) -> list:
        return list(self.libraries) + list(self.mods)

    def total_bytes(self) -> int:
        return sum(t.size or 0 for t in self.tasks)


def build_plan(mc_version: str, key: str, loader_version: str, mc_dir,
               version_id: str = "", profile: dict = None,
               vanilla_vj: dict = None, api_version: dict = None,
               rules_allow=None) -> LoaderPlan:
    """算出"装这个加载器要下什么、写什么"

    参数：
        mc_version      游戏版本（原版 id，加载器版本 `inheritsFrom` 它）
        key / loader_version   加载器与它的版本
        mc_dir          .minecraft 目录
        version_id      版本目录名（默认按 PCL 约定拼：`1.21.4-Fabric 0.19.5`）
        profile         已经取好的 profile JSON（不给就自己去取）
        vanilla_vj      原版版本 JSON（只在名字跟原版撞了时用得上，见
                        `profile_version_json`）
        api_version     Modrinth 的一个版本 dict（Fabric API），会一起装进
                        `mods/`
    """
    key = (key or "").lower()
    version_id = version_id or loaders_mod.version_folder_name(
        mc_version, key, loader_version)
    if not version_id:
        raise LoaderError(tr("加载器版本不对，拼不出目录名"))

    if profile is None:
        profile = fetch_profile(mc_version, key, loader_version)
    profile = check_profile(profile, key, loader_version)

    # ⚠️ 不传就按**当前系统**挑库（不是"全放行"）：加载器的 profile 里
    # 常常带别的系统的库（Quilt 的 osx 那几个），全下下来纯属白费流量
    blocks_allow = rules_allow or _platform_rules()

    mc_dir = Path(mc_dir)
    version_dir = mc_dir / "versions" / version_id

    plan = LoaderPlan(
        key=key, version=loader_version, version_id=version_id,
        version_dir=version_dir,
        version_json=profile_version_json(profile, mc_version, version_id,
                                          vanilla_vj=vanilla_vj),
        libraries=library_tasks(profile, mc_dir, rules_allow=blocks_allow),
    )

    if api_version:
        plan.mods.append(mod_task(api_version, version_dir / "mods"))

    if not plan.libraries:
        plan.warnings.append(tr("加载器的库里一个能下的都没有（数据不对？）"))
    return plan


def write_profile(plan: LoaderPlan) -> Path:
    """把加载器的版本 JSON 落盘（`<版本目录>/<目录名>.json`）

    ⚠️ 跟原版一样**必须写在下载之前**：装完没有 json 的话，版本列表
    （靠 `<目录>/<目录>.json` 认版本）根本看不到它 —— "下完了但列表里没有"。
    """
    return install_mod.write_version_json(
        plan.version_json, plan.version_dir, plan.version_id)


# ============================================================
# 「跑官方安装器」那几家（Forge / NeoForge / OptiFine）
# ============================================================

def installer_plan(mc_version: str, key: str, loader_version: str, mc_dir,
                   version_id: str = "", extra: dict = None) -> LoaderPlan:
    """算出"装这个加载器要下什么"——**只下一个安装器 jar**

    跟 `build_plan()` 的根本区别：这里的 `version_json` **是空的、也不会被
    写下去**。版本 JSON 和库都是安装器自己弄出来的 —— 我们先写一份的话，
    安装器会覆盖它（白写），更糟的是**半成品会被当成已安装**
    （实验项目 `BUGS.md` BUG-04 就是这个坑）。

    所以这份计划的 `tasks` 里只有一个安装器 jar，剩下的活在下载完之后，
    由 `finish_installer()` 接着干。

    `extra` 是给 `core/loader_setup.installer_url()` 用的额外数据：
    OptiFine 要 `type`/`patch`（版本列表里带着），NeoForge 1.20.1 及以前
    要 `package="forge"`（历史命名）。
    """
    from core import loader_setup as setup
    key = (key or "").lower()
    if key not in INSTALLER_LOADERS:
        raise LoaderError(tr("{name} 不用跑安装器", name=key))

    version_id = version_id or loaders_mod.version_folder_name(
        mc_version, key, loader_version)
    if not version_id:
        raise LoaderError(tr("加载器版本不对，拼不出目录名"))

    extra = dict(extra or {})
    try:
        url = setup.installer_url(mc_version, key, loader_version, extra)
    except setup.SetupError as e:
        raise LoaderError(str(e)) from e

    mc_dir = Path(mc_dir)
    jar = setup.installer_dir(mc_dir) / setup.installer_file_name(
        key, mc_version, loader_version)
    return LoaderPlan(
        key=key, version=loader_version, version_id=version_id,
        version_dir=mc_dir / "versions" / version_id,
        libraries=[install_mod.Task(
            url=url, path=jar, sha1="", size=0,
            label=jar.name, kind=tr("安装器"))],
        installer=True, installer_jar=jar, installer_extra=extra,
    )


def installer_java(vanilla_vj: dict = None, key: str = "") -> "tuple":
    """给安装器挑个 Java：`(JavaInfo 或 None, 说明)`

    ⚠️ 要求的最低版本**跟着游戏版本走**（`core/java.py` 的 `required_major`）：
    1.20.1 的 Forge 安装器要 Java 17 才跑得动，拿 Java 8 去跑会
    `UnsupportedClassVersionError`。但也不能低于 8（安装器自己编译目标是 8）。

    ⚠️ **OptiFine 是例外，而且方向相反**：它那个安装器是 Java 8 编的，
    在 Java 17 上会**卡住不返回**（2026-09 实测：10 分钟一行输出都没有、
    也装不完；换 Java 8 只要 8 秒装完）。所以 OptiFine 一律优先 Java 8，
    哪怕游戏本身要 17 —— 安装器只是拿来打补丁的，跟游戏跑在哪个 Java 上无关。
    """
    from core.config import config
    from core import java as java_mod
    if (key or "").lower() == "optifine":
        need = 8
    else:
        need = max(java_mod.required_major(vanilla_vj or {}, default=8), 8)
    javas = java_mod.find_javas(java_mod.scan_minecraft_dirs())
    # ⚠️ `config` 是**实例**（`core/config.py` 末尾 `config = Config()`），
    # 不是模块。写成 `from core import config` 再 `config.get(...)` 会
    # AttributeError —— 这个错是 `installer_wire_run.py` 真跑一遍才抓到的
    # （离线测试全绿也没发现，因为它不经过这条路）。
    return java_mod.pick_java(javas, need, config.get("java_path", ""))


def finish_installer(plan: LoaderPlan, mc_dir, on_line=None,
                     timeout: int = 0) -> "tuple[str, str]":
    """下载完之后：**跑安装器 → 校验 → 改成我们要的目录名**

    返回 `(最终版本目录名, 错误信息)`；出错时目录名给空串。
    已经装好过（目标目录存在且有 JSON）就直接返回，**不重复跑** ——
    安装器一遍要 3 分钟，重装一遍纯属折磨人。

    `on_line(text)` 收安装器的输出（**已经节流**，见 `loader_setup.OutputCollector`），
    子线程里调，别在里面碰控件。
    """
    from core import loader_setup as setup

    if not plan or not plan.installer:
        return plan.version_id if plan else "", ""

    mc_dir = Path(mc_dir)
    wanted = plan.version_id

    # 已经装好了？（重试/重装时别白等三分钟）
    ok, _why = setup.verify_installed(mc_dir, wanted, "")
    if ok:
        return wanted, ""

    java, why = installer_java(plan.vanilla_vj, plan.key)
    if java is None:
        return "", tr("没有能跑安装器的 Java：{why}", why=why)
    if on_line:
        on_line(tr("用 {path} 跑安装器", path=java.path))
        # OptiFine 偏偏要 Java 8（17 上它那个安装器会卡住）。机器上没有 8、
        # 只能拿个更高的顶替时**先说一声**，免得卡住时用户以为程序死了。
        if (plan.key or "").lower() == "optifine" and java.major > 8:
            on_line(tr("⚠️ 这台机器上没有 Java 8，只能用 Java {n} 顶替 —— "
                       "OptiFine 的安装器在它上面可能卡住不返回",
                       n=java.major))

    created = setup.ensure_launcher_profiles(mc_dir)
    if created and on_line:
        on_line(tr("游戏目录里没有 launcher_profiles.json，建了一份（安装器要求有）"))

    before = setup.version_dirs(mc_dir)
    kwargs = {"timeout": timeout} if timeout else {}
    try:
        out = setup.run_installer(java.path, plan.installer_jar, mc_dir,
                                  plan.key, on_line=on_line, **kwargs)
    except setup.SetupError as e:
        return "", str(e)

    if out.get("rc"):
        return "", (out.get("error")
                    or tr("安装器退出码 {n}", n=out.get("rc")))

    made = setup.new_version_dirs(mc_dir, before)
    if not made:
        # 没报错但也没新目录 —— 要么它写进了老目录，要么装了但被我们看成"已存在"
        ok, why = setup.verify_installed(mc_dir, wanted, "")
        if ok:
            return wanted, ""
        return "", tr("安装器跑完了，但没看到新的版本目录（{why}）", why=why)

    # ⚠️ 先校验再改名：半成品改名之后就更难认了（BUGS.md BUG-04）
    final = made[0]
    ok, why = setup.verify_installed(mc_dir, final, "")
    if not ok:
        return "", tr("{name} 装了一半（{why}），删掉这个目录重试一次",
                      name=final, why=why)
    try:
        final = setup.rename_version(mc_dir, final, wanted)
    except setup.SetupError as e:
        # 改名失败不算装失败：名字难看但版本能用
        if on_line:
            on_line(str(e))
        return final, ""
    return final, ""


# ============================================================
# 「原版 + 加载器」一整批（两个页面共用）
# ============================================================

def vanilla_json_url(mc_id: str) -> str:
    """从官方清单里找这个原版版本的 JSON 地址

    下载页那条路手上已经有地址了（版本列表里带 `url`）；**整合包那条路没有**
    —— `.mrpack` 里只写 `dependencies.minecraft`，不给 URL。所以这里自己去
    清单里查一次（`core/versions_remote.load_manifest()` 自带 6 小时缓存）。
    查不到返回空串（调用方据此说"没有这个原版版本"，而不是崩）。
    """
    from core import versions_remote as vr
    try:
        data, _info = vr.load_manifest()
    except Exception as e:                          # noqa: BLE001
        raise LoaderError(tr("拿不到原版版本清单（网络问题）")) from e
    if not data:
        # ⚠️ 清单拉不到时**要抛**，不能返回空串：返回空串调用方只会说
        # 「清单里没有原版 1.20.4」，把网络问题说成"这个版本不存在"（最难查的那种）
        raise LoaderError(tr("拿不到原版版本清单（网络问题）"))
    for item in ((data or {}).get("versions") or []):
        if isinstance(item, dict) and item.get("id") == mc_id:
            return str(item.get("url") or "")
    return ""


def install_stack(mc_id: str, vj: dict, mc_dir, manager, version_id: str,
                  loader_key: str = "", loader_version: str = "",
                  api_version: dict = None, rules_allow=None,
                  fetch=None, meta: dict = None,
                  vanilla_name: str = "", extra: dict = None) -> tuple:
    """把"原版 + 可选加载器"这一整批任务塞进 `manager`（**不 start**）

    返回 `(原版计划, 加载器计划或 None, 加载器失败原因)`。

    两个页面共用这一份编排（下载页的"装 Minecraft + Fabric"、模组页的
    "装整合包 + 它要的加载器"），因为里面的顺序和落点都很讲究：

    1. **两份 JSON 都要在下载之前落盘**（列表靠 `<目录>/<目录>.json` 认版本）
    2. **原版装到 `versions/<原版 id>/`** —— 加载器那份 JSON 里的
       `inheritsFrom` 是它，启动时正是去那儿找 JSON 和客户端 jar
    3. **加载器失败不能拖累原版**：原版的任务照下，只是最后多报一句
       （先抛异常就不启动下载，会留下"有 json 没 jar"的半成品版本，最糟）
    4. 给了 `meta`（整合包元数据）就塞进加载器那份 JSON；**加载器装不了时**
       由调用方退回 `modpack.version_json()` 那种"只有元数据"的 JSON
    5. **要跑安装器的那几家**（Forge / NeoForge / OptiFine）这里只下一个
       安装器 jar，回来的计划上 `installer=True` —— 下载完之后调用方还要
       调 `finish_installer()` 把安装器跑起来（那一步要 Java，而且一跑
       三分钟，不能塞进下载线程里假装是"下载"）

    `vanilla_name` 不传就按 `mc_id` 走；只有"名字跟原版 id 撞车"那种情况
    才需要另说（见 `profile_version_json`）。
    `extra` 是给安装器的额外数据（OptiFine 的 type/patch 等，见 `installer_plan`）。
    """
    vanilla_name = vanilla_name or mc_id or version_id
    allow = rules_allow or _platform_rules()

    # ---------- 原版 ----------
    index = None
    if fetch is not None:
        try:
            index = install_mod.load_asset_index(mc_dir, vj, fetch=fetch)
        except Exception:                           # noqa: BLE001
            index = None        # 拿不到资源索引就只下客户端和库，别整个失败
    install_mod.write_version_json(vj, Path(mc_dir) / "versions" / vanilla_name,
                                  vanilla_name)
    vanilla_plan = install_mod.plan_version(vj, mc_dir, rules_allow=allow,
                                           asset_index=index,
                                           jar_name=vanilla_name)
    install_mod.start_install(vanilla_plan, manager)

    # ---------- 加载器（可选）----------
    loader_plan = None
    loader_error = ""
    if loader_key:
        try:
            if (loader_key or "").lower() in INSTALLER_LOADERS:
                # 要跑官方安装器那几家：这里**只下安装器 jar**，
                # 版本 JSON 和库都归安装器管（见 installer_plan 的说明）。
                # 剩下的活在下载完之后 —— 调用方接着调 finish_installer()。
                loader_plan = installer_plan(
                    mc_id, loader_key, loader_version, mc_dir,
                    version_id=version_id, extra=extra)
                loader_plan.vanilla_vj = dict(vj or {})
                manager.add_all([t.as_tuple() for t in loader_plan.tasks])
            else:
                loader_plan = build_plan(mc_id, loader_key, loader_version,
                                         mc_dir, version_id=version_id,
                                         vanilla_vj=vj,
                                         api_version=api_version,
                                         rules_allow=allow)
                if meta:
                    loader_plan.version_json["modpack"] = dict(meta)
                write_profile(loader_plan)
                manager.add_all([t.as_tuple() for t in loader_plan.tasks])
        except LoaderError as e:
            loader_plan = None
            loader_error = str(e)
        except Exception as e:                      # noqa: BLE001
            loader_plan = None
            loader_error = "%s: %s" % (type(e).__name__, e)
    return vanilla_plan, loader_plan, loader_error


# ============================================================
# Fabric API（选了 Fabric 才有意义）
# ============================================================

def fabric_api_versions(mc_version: str) -> list:
    """Fabric API 里能配这个游戏版本的那些版本（新的在前）

    走 Modrinth 的项目版本接口（`core/modrinth_api.py`），**不在服务端筛** ——
    那边一次给全部版本，本地按 `game_versions` + `loaders` 筛更省一次往返，
    而且筛法跟详情页共用（`pick_version`）。
    """
    from core import modrinth_api as mr
    try:
        versions = mr.get_project_versions(FABRIC_API_PROJECT)
    except Exception as e:                          # noqa: BLE001
        raise LoaderError(tr("拿不到 Fabric API 的版本列表（网络问题）")) from e
    return mr.filter_versions(versions, mc_version, "fabric")


def latest_fabric_api(mc_version: str):
    """Fabric API 里最合适的那个版本（没有就 None）"""
    found = fabric_api_versions(mc_version)
    return found[0] if found else None
