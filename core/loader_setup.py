"""跑**外部安装器**装加载器（Forge / NeoForge / Cleanroom 那一类）

纯 Python（不 import Qt），从实验项目 `MC_LuncherTEST-3.0.py:453` 的
`ForgeDownloader.install_forge()` 搬过来，按我们的规矩补齐了它没做的两件事。

## 为什么要有这个模块

加载器分两类（见 `core/loader_install.py`）：

| 类型 | 谁 | 怎么装 |
|---|---|---|
| 有 profile JSON | Fabric / Quilt | 一个 GET 拿到版本 JSON → 下库 → 写 JSON，**不用 Java** |
| 只有安装器 jar | Forge / NeoForge / Cleanroom / OptiFine | 只能**把安装器跑起来**，它自己去打客户端 jar 的补丁 |

安装器是 Forge/NeoForge 官方给的工具，官方启动器也是这么装的，
所以**别自己去实现它的补丁过程**（那是在重新发明安装器）。

## 实测记下来的三件事（2026-09，别凭印象改）

1. ⚠️ **游戏目录里必须先有 `launcher_profiles.json`**（官方启动器的档案文件）。
   没有的话安装器直接报
   `There is no minecraft launcher profile in ...` 然后退出码 1 ——
   我们自己装的 `.minecraft` 里不会有这个文件，所以跑之前得先造一份
   （见 `ensure_launcher_profiles()`）。
2. ⚠️ **它很罗嗦**：Forge 1.20.1 装一次 **3 分 22 秒、34072 行输出**，
   其中绝大部分是 `Patching net/minecraft/...` 这种逐类刷屏。
   所以喂给界面的输出要**节流**（`on_line` 只收"有意思的行"+ 定期心跳），
   不然日志窗口直接被刷死。
3. ⚠️ **产物是"只有 JSON、没有 jar"的版本目录**（如
   `versions/1.20.1-forge-47.4.0/1.20.1-forge-47.4.0.json`），
   里面的 `inheritsFrom` 指原版、客户端 jar 仍从原版目录拿 ——
   **跟我们自己的"原版一个目录 + 加载器一个目录"设计一致**，不用额外处理。
   但它**不会**用我们想要的名字（`1.20.1-Forge_47.4.0`），所以要 `rename_version()`。
   安装器还可能**留下半成品**（中途失败时），所以装完必须 `verify_installed()`
   —— 实验项目的 BUGS.md BUG-04 就是踩的这个坑。

## 关于 Java

安装器自己就是个 Java 程序（Forge/NeoForge 的编译目标是 Java 8，新版本要 8+），
所以要挑一个 Java 来跑它。挑法跟启动游戏一样走 `core/java.py`，
只是**要求的最低版本跟着游戏版本走**（新版本要 17/21，别拿 Java 8 去跑）。
"""

import json
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from core.i18n import tr
from core.loaders import BMCLAPI, LOADER_NAMES

# 安装器自己的输出里，这些行是"逐条刷屏"，别一条条丢给界面。
#
# ⚠️ 这份名单是**照着一份真的日志数出来的**（`.tmp/loaders/forge-1.20.1-47.4.0-
# installer.jar.log`，34072 行，安装器自己写的）：
#      7590  `  Data  assets/minecraft/…`      ← BUNDLER_EXTRACT 一个资源一行
#      5887  `  Data  data/minecraft/…`
#      3368  裸类名 `net/minecraft/WorldlyContainer`（SpecialSource 在改名）
#      1724  裸类名 `net/minecraft/client/…`
#      1385  `  Patching net/minecraft/…`
#      1385  `Reading patch net.minecraft…`
#       321  `Cant Find Class: com/…`
# 只写 `Patching ` 是不够的（那只能挡住 1385 行，剩下 3 万行照样糊到界面上
# —— NeoForge 1.20.1 实测就是这样：32699 行里有用的不到 200 行）。
SPAM_PREFIXES = (
    "Patching ", "  Patching ",
    "Reading patch ", "  Reading patch ",
    "Data  ", "  Data  ",
    "Cant Find Class",
    # OptiFine 那个安装器（4000 行里大部分是它）：一条一条报"这个类跟原来一样"
    "Same: ",
)
# 还有一大类：**缩进两格的"一个条目一行"** —— 实测里 7756 行长这样：
#       `  com/mojang/blaze3d/Blaze3D.class`
#       `  net/minecraft/WorldlyContainer`
# 没有任何前缀能可靠地概括它们（前缀就是路径本身），所以按**形状**认：
# 去掉首尾空白之后，整行是"斜杠分隔的标识符/文件名"。
_LISTING_RE = re.compile(r"^[A-Za-z_$][\w$]*(?:/[\w$.]+)+$")


def looks_like_listing(line: str) -> bool:
    """这行是不是"一个文件/类一行"那种列表项（见 `_LISTING_RE`）

    ⚠️ 只在**整行**就是个路径时才认：`Downloading library from https://…`
    里有空格、`java.net.preferIPv4Stack=true` 里有 `=`、Windows 路径有 `:`，
    都不会被认成列表项（那几句恰恰是我们要给用户看的）。
    """
    text = str(line or "").strip()
    return bool(text) and bool(_LISTING_RE.match(text))
# 心跳：就算一直在刷屏，也每 N 行报一次进度（让界面知道它还活着）
HEARTBEAT_EVERY = 200
# ⚠️ **兜底**：不管安装器换成什么花样刷屏，喂给界面的"有意义行"最多这么多。
# 光靠前缀名单是"追着它们的输出格式跑"—— 换个大版本就可能漏（上面那次就是）。
# 到顶之后只说一句"输出太多了"，剩下的靠心跳报活着。
FORWARD_LIMIT = 300

# 安装器的套话：它每次失败都打这一句，信息量为零，真正的原因在上一行
GENERIC_ERRORS = ("There was an error during installation",)
# 安装器默认超时（秒）——实测 Forge 1.20.1 要 200 秒左右，给足余量
DEFAULT_TIMEOUT = 1800
# 没有 launcher_profiles.json 时造一份最小的（安装器只要求它存在且是个 JSON）
LAUNCHER_PROFILES = {
    "profiles": {},
    "settings": {"enableSnapshots": False, "enableAdvanced": False},
    "version": 3,
}


class SetupError(RuntimeError):
    """装不上（信息是给人看的：会显示在界面状态行上）"""


# ============================================================
# 1. 下载地址
# ============================================================

def installer_url(mc_version: str, key: str, loader_version: str,
                  extra: dict = None) -> str:
    """这个加载器的安装器从哪下

    - **Forge**：BMCLAPI `/forge/download?...&category=installer&format=jar`
      （实测 200，5.8 MB；BMCLAPI 会重定向到国内镜像）
    - **NeoForge**：官方 maven 的 `-installer.jar`
      （实测 200，6.5 MB。BMCLAPI 的 `/maven/` 也镜像了它，
      `core/download.py` 的 MIRROR_PAIRS 里有两家的映射，下载器会自动换）
    - **OptiFine**：BMCLAPI `/optifine/<mc>/<type>/<patch>`
      （`extra` 里要给 `type`（如 `HD_U`）和 `patch`（如 `I5`）——
      版本列表里就带着这两个字段，见 `core/loaders.parse_optifine`）
    - **Cleanroom**：GitHub release（要 `extra["url"]`，版本列表里给）
    """
    key = (key or "").lower()
    mc = str(mc_version or "").strip()
    lv = str(loader_version or "").strip()
    extra = extra or {}
    if key == "forge":
        from urllib.parse import urlencode
        query = urlencode({"mcversion": mc, "version": lv,
                           "category": "installer", "format": "jar"})
        return "%s/forge/download?%s" % (BMCLAPI, query)
    if key == "neoforge":
        # 1.20.1 那代的 NeoForge 走 `net/neoforged/forge/`（历史命名，
        # 版本号是 47.1.x —— 那时它还叫 "Forge 的分支"）。
        # 1.20.2 起才改成 `net/neoforged/neoforge/`。不用调用方传，
        # 按版本号自己认：省得每个调用点都要记得这条历史。
        package = str(extra.get("package") or "")
        if not package:
            package = "forge" if (mc == "1.20.1" or lv.startswith("47.")) else "neoforge"
        # ⚠️ 1.20.1 那批在 maven 里的**产物版本号带着游戏版本前缀**：
        #    `/net/neoforged/forge/1.20.1-47.1.106/forge-1.20.1-47.1.106-installer.jar`
        #    （实测 HEAD 200，7.8 MB）。而 BMCLAPI 的版本列表给的是 `47.1.106`
        #    （`rawVersion` 才是带前缀那个），所以这里要自己拼回去 ——
        #    直接拿 `47.1.106` 去问是 404（踩过）。
        artifact = lv
        if package == "forge" and not lv.startswith(mc + "-"):
            artifact = "%s-%s" % (mc, lv)
        return ("https://maven.neoforged.net/releases/net/neoforged/%s/%s/"
                "%s-%s-installer.jar" % (package, artifact, package, artifact))
    if key == "optifine":
        vtype = str(extra.get("type") or "").strip()
        patch = str(extra.get("patch") or "").strip()
        if not (vtype and patch):
            raise SetupError(tr("OptiFine 的版本数据里缺少 type / patch，拼不出下载地址"))
        return "%s/optifine/%s/%s/%s" % (BMCLAPI, mc, vtype, patch)
    if key == "cleanroom":
        url = str(extra.get("url") or "").strip()
        if not url:
            raise SetupError(tr("Cleanroom 的版本数据里没有下载地址"))
        return url
    raise SetupError(tr("{name} 的安装器还没有做", name=LOADER_NAMES.get(key, key)))


def installer_file_name(key: str, mc_version: str, loader_version: str) -> str:
    """安装器 jar 存成什么名字（放临时目录里，装完就删）"""
    key = (key or "").lower()
    if key == "forge":
        return "forge-%s-%s-installer.jar" % (mc_version, loader_version)
    if key == "neoforge":
        return "neoforge-%s-installer.jar" % loader_version
    if key == "optifine":
        return "OptiFine-%s-%s-installer.jar" % (mc_version, loader_version)
    return "%s-%s-installer.jar" % (key or "loader", loader_version)


def installer_dir(mc_dir) -> Path:
    """安装器 jar 放哪：`<游戏目录>/.mosslight/installers/`

    为什么不放系统临时目录：安装器跑起来要读到自己的 jar（`java -jar`），
    而且 OptiFine 那个只认工作目录/APPDATA 的货也得有个稳当的落脚点。
    放游戏目录里还有个好处 —— 装失败能直接翻出来手动重跑。

    ⚠️ **不自动删**：一个 5~6 MB，名字里带着版本号，装过的不会再下一遍，
    但"装了一半失败"时留着它才有得查（重试也不用重下）。
    """
    return Path(mc_dir) / ".mosslight" / "installers"


# ============================================================
# 2. 安装器要的那个 launcher_profiles.json
# ============================================================

def ensure_launcher_profiles(mc_dir) -> bool:
    """游戏目录里没有 `launcher_profiles.json` 就造一份最小可用的。

    返回是否**新建了**（给日志用）。详见模块开头第 1 条 —— 少了它 Forge
    安装器会直接失败，而且报的是一句"你去把启动器跑一遍"。

    ⚠️ 已存在就**一个字节都不动**：那里面是用户在官方启动器里的档案，
    我们没资格改。装完安装器自己会往里面加一条 Forge 档案，那是它的正常行为。
    """
    path = Path(mc_dir) / "launcher_profiles.json"
    if path.is_file():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(LAUNCHER_PROFILES, ensure_ascii=False,
                                   indent=2) + "\n", encoding="utf-8")
        return True
    except OSError as e:
        raise SetupError(tr("建不了 {path}（安装器要求有它）：{err}",
                            path=path, err=e)) from e


# ============================================================
# 3. 跑安装器
# ============================================================

def installer_arguments(key: str, mc_dir) -> list:
    """安装器的命令行参数（Forge / NeoForge 官方都是 `--installClient <目录>`）"""
    key = (key or "").lower()
    if key in ("forge", "neoforge", "cleanroom"):
        return ["--installClient", str(mc_dir)]
    if key == "optifine":
        # ⚠️ OptiFine 的安装器**不吃参数**：它把主类写成 Swing 窗口，
        # 但里面留了 `public static void main` —— 它调的是
        # `doInstall(Utils.getWorkingDirectory())`，也就是**认当前工作目录**。
        # 所以参数为空，靠 `cwd` / `APPDATA` 把它指到目标游戏目录（见 run_installer）。
        return []
    raise SetupError(tr("{name} 的安装器还没有做", name=LOADER_NAMES.get(key, key)))


# OptiFine 那个 jar 是**双入口**的（实测 MANIFEST + 字节码）：
#     Main-Class: optifine.InstallerFrame     ← `java -jar` 走这个 = **弹窗口**
#                                                要人点「Install」才装
#     optifine.Installer                      ← 这个的 main 直接
#                                                 `doInstall(getWorkingDirectory())`
# 所以 OptiFine **不能**用 `-jar`：在启动器里弹一个要用户自己点按钮的窗口，
# 等于让"一键装好"变成"你自己去点"（用户 2026-09 就是这么看到窗口的）。
# 要用 `-cp <jar> optifine.Installer` 走无窗口那条路。
OPTIFINE_HEADLESS_CLASS = "optifine.Installer"


def has_class(jar, dotted: str) -> bool:
    """这个 jar 里有没有某个类（决定 OptiFine 走无窗口入口还是开窗口）"""
    try:
        with zipfile.ZipFile(jar) as zf:
            return (dotted.replace(".", "/") + ".class") in zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def installer_command(java_path, installer_jar, mc_dir, key: str) -> list:
    """跑安装器的完整命令行

    - **Forge / NeoForge / Cleanroom**：`java -jar <安装器> --installClient <目录>`
    - **OptiFine**：`java -cp <安装器> optifine.Installer`
      （⚠️ 不用 `-jar` —— 那样开的是那个要人点按钮的窗口，见上）
      老版本万一没有 `optifine.Installer` 那个类，退回 `-jar`（只能开窗口，
      调用方会在控制台上写一句"请点一下 Install"）。
    """
    key = (key or "").lower()
    if key == "optifine" and has_class(installer_jar, OPTIFINE_HEADLESS_CLASS):
        return [str(java_path), "-cp", str(installer_jar),
                OPTIFINE_HEADLESS_CLASS]
    cmd = [str(java_path), "-jar", str(installer_jar)]
    return cmd + installer_arguments(key, mc_dir)


class OutputCollector:
    """收安装器的输出：**节流** + 挑出错在哪（纯逻辑，离线可测）

    为什么要单独一个类：安装器一次能刷 3 万多行（绝大部分是
    `  Data  assets/…` 和裸类名，见 `SPAM_PREFIXES` 上面那份实测统计），
    直接丢给日志窗口会把它刷死。这里只把"有意思的行"和心跳交给 `on_line`，
    但**全都留着**给排查用。

    两道闸门，缺一不可：
      1. 前缀名单（`SPAM_PREFIXES`）—— 挡住已知的刷屏，让第一次装的时候
         界面里能看到"下库 / 打补丁"这些真正有信息量的行
      2. **条数上限**（`FORWARD_LIMIT`）—— 兜底。名单是"追着它们的输出格式
         跑"，哪天它们换个说法就漏了（NeoForge 1.20.1 那次就是这么漏的：
         3 万行里喂了 32699 行）
    """

    def __init__(self, on_line=None):
        self.on_line = on_line
        self.lines = []
        self.spam = 0
        self.total = 0
        self.forwarded = 0
        self.capped = False
        self.last = ""

    def feed(self, line: str):
        line = (line or "").rstrip()
        if not line:
            return
        self.last = line
        self.lines.append(line)
        self.total += 1

        spam = line.startswith(SPAM_PREFIXES) or looks_like_listing(line)
        if spam:
            self.spam += 1

        if not self.on_line:
            return
        # 心跳按**总行数**算（不是只按刷屏行）—— 万一刷屏行一条都没被名单
        # 认出来，心跳也得照发，不然界面看着像死了
        if self.total % HEARTBEAT_EVERY == 0:
            self.on_line(tr("还在这装…（已处理 {n} 行）", n=self.total))
            return
        if spam:
            return
        if self.forwarded >= FORWARD_LIMIT:
            if not self.capped:
                self.capped = True        # 只说一次，别把这句话本身变成刷屏
                self.on_line(tr("安装器的输出太多了，后面只报进度"))
            return
        self.forwarded += 1
        self.on_line(line)

    def result(self) -> dict:
        return {"lines": self.lines, "spam": self.spam, "last": self.last,
                "hint": failure_hint(self.lines)}


def run_installer(java_path: str, installer_jar, mc_dir, key: str = "forge",
                  on_line=None, timeout: int = DEFAULT_TIMEOUT,
                  env: dict = None) -> dict:
    """跑安装器，逐行收输出。返回 `{rc, lines, seconds, spam, last, error}`

    参数：
        on_line(text)   把"有意思的输出行"喂给界面（**已经节流**，不会刷屏）
                        —— 子线程里调，别在里面碰控件
        env             额外/覆盖的环境变量（OptiFine 要靠 `APPDATA` 定位，见下）

    ⚠️ 三条跟模板一致、别改的细节：
      · `stderr` 合进 `stdout`（安装器的报错也走 stdout 混着打）
      · `cwd` = 安装器所在目录（OptiFine 那条命根子就在这儿）
      · Windows 上 `CREATE_NO_WINDOW`，否则会弹一个黑框
    ⚠️ 超时按**整体**算：实测 Forge 1.20.1 要 200 秒，别学模板那样不管它。
    """
    installer_jar = Path(installer_jar)
    mc_dir = Path(mc_dir)
    if not installer_jar.is_file():
        raise SetupError(tr("安装器文件不见了：{path}", path=installer_jar))

    import os
    child_env = dict(os.environ)
    if env:
        child_env.update(env)

    cmd = installer_command(java_path, installer_jar, mc_dir, key)
    # OptiFine：它自己 `getWorkingDirectory()` 在 Windows 上读 `%APPDATA%`，
    # 拼出 `<APPDATA>/.minecraft`。我们把 APPDATA 指到"游戏目录的上一级"，
    # 它就会装进我们要的那个 .minecraft 里（目录名不叫 .minecraft 时另说，
    # 那种情况由调用方先建临时目录再搬，见 loader_install 的说明）。
    if (key or "").lower() == "optifine":
        child_env["APPDATA"] = str(mc_dir.parent)
        if "-jar" in cmd:
            # 兜底那条路（老版本没有 `optifine.Installer`）只能开窗口，
            # 得**明说**，不然用户看着一个窗口发呆
            if on_line:
                on_line(tr("OptiFine 的安装器会弹出一个窗口，请点一下「Install」"))

    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(installer_jar.parent), env=child_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace",
            creationflags=(subprocess.CREATE_NO_WINDOW
                           if sys.platform == "win32" else 0),
        )
    except OSError as e:
        raise SetupError(tr("起不了安装器（{java}）：{err}",
                            java=java_path, err=e)) from e

    collector = OutputCollector(on_line)
    try:
        for raw in proc.stdout:
            collector.feed(raw)
            if time.monotonic() - started > timeout:
                proc.kill()
                raise SetupError(tr("安装器超过 {n} 分钟没装完，先停下了",
                                    n=timeout // 60))
    finally:
        rc = proc.wait()

    out = collector.result()
    out["rc"] = rc
    out["seconds"] = time.monotonic() - started
    # ⚠️ 只有**失败**时才给"原因"：成功时最后一行是
    # `Successfully installed client into launcher.`，当成错误显示会很好笑
    out["error"] = out["hint"] if rc else ""
    return out


def failure_hint(lines) -> str:
    """安装器输出里**最能说明问题**的一句（成功时也会返回一句，由调用方决定用不用）

    从后往前找，跳过两类：
      · `Patching ...` 那种刷屏
      · 安装器自己的套话（`There was an error during installation` ——
        这句它每次失败都打，信息量为零，真正的原因在它**上一行**，
        实测就是 `There is no minecraft launcher profile in ...`）
    """
    for line in reversed(lines or []):
        text = str(line).strip()
        if not text or text.startswith(SPAM_PREFIXES):
            continue
        if any(gen in text for gen in GENERIC_ERRORS):
            continue
        return text
    return ""


# ============================================================
# 4. 装完：认出新版本目录 → 改名 → 校验
# ============================================================

def version_dirs(mc_dir) -> set:
    """`versions/` 下现有的版本目录名（装之前记一份，装完对比）"""
    folder = Path(mc_dir) / "versions"
    try:
        return {p.name for p in folder.iterdir() if p.is_dir()}
    except OSError:
        return set()


def new_version_dirs(mc_dir, before) -> "list[str]":
    """装完之后**多出来**的版本目录（安装器自己起的名字）

    ⚠️ 用"前后对比"而不是"猜它叫什么"：Forge 写的是
    `1.20.1-forge-47.4.0`、NeoForge 写 `neoforge-21.1.72`，
    各版本还不一样（1.20.1 的 NeoForge 走 `forge-…` 历史命名）。
    对比一下最省事，也不用跟着他们的命名规则跑。
    """
    now = version_dirs(mc_dir)
    return sorted(now - set(before or ()))


def verify_installed(mc_dir, version_id: str, mc_id: str = "") -> "tuple[bool, str]":
    """装完校验：这个版本目录**真的能用**吗（`BUGS.md` 的 BUG-04）

    只看三件事（都是"能启动"的必要条件）：
      1. 目录里有 `<版本名>.json`
      2. JSON 是个 dict、有 `id`
      3. `mainClass` 有 —— **或者** `inheritsFrom` 指向的父版本在本地
         （Forge 1.13+ 的 JSON 里没有客户端 jar，主类在它自己那份里；
         老版本可能反过来）
    返回 `(行不行, 为什么不行)`。
    """
    folder = Path(mc_dir) / "versions" / version_id
    path = folder / ("%s.json" % version_id)
    if not path.is_file():
        return False, tr("没有版本 JSON：{path}", path=path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return False, tr("版本 JSON 读不了：{err}", err=e)
    if not isinstance(data, dict) or not data.get("id"):
        return False, tr("版本 JSON 里没有 id")
    if data.get("mainClass"):
        return True, ""
    parent = str(data.get("inheritsFrom") or "")
    if parent and (Path(mc_dir) / "versions" / parent
                   / ("%s.json" % parent)).is_file():
        return True, ""
    if not parent:
        return False, tr("JSON 里既没有主类、也没有 inheritsFrom")
    return False, tr("它继承的原版 {mc} 还没装（{path} 不存在）",
                     mc=parent, path=parent)


def rename_version(mc_dir, old_id: str, new_id: str) -> str:
    """把安装器起的名字改成我们要的名字，返回最终版本名

    改五样，**必须一起改**（版本列表和 `--version` 都靠它们一致）：
      1. 目录名 `<new_id>/`
      2. JSON 文件名 `<new_id>.json`
      3. JSON 里的 `id`
      4. JSON 里**指回自己**的 `jar` / `inheritsFrom`
      5. 目录里的 `<old_id>.jar` —— **OptiFine 那条路实测抓到的**：
         它装完是 `1.20.1-OptiFine_HD_U_I5/1.20.1-OptiFine_HD_U_I5.jar`
         （23 MB 的**打过补丁的客户端**）。目录改了名而 jar 没跟着改的话，
         `core/launch.py` 按"自己目录里的 `<目录名>.jar`"去找会扑空，
         然后**悄悄**退回父版本的原版 jar —— 表现是"进游戏没有光影"。

    ⚠️ 只在 `new_id` 和 `old_id` 不一样、而且**目标还不存在**时才动；
    目标已存在就原样返回 `old_id`（宁可名字难看，也不能覆盖用户已有的版本）。
    """
    old_id = str(old_id or "").strip()
    new_id = str(new_id or "").strip()
    if not old_id or not new_id or old_id == new_id:
        return old_id
    versions = Path(mc_dir) / "versions"
    src = versions / old_id
    dst = versions / new_id
    if not src.is_dir() or dst.exists():
        return old_id
    try:
        src.rename(dst)
        old_json = dst / ("%s.json" % old_id)
        if old_json.is_file():
            data = json.loads(old_json.read_text(encoding="utf-8-sig"))
            data["id"] = new_id
            for field in ("jar", "inheritsFrom"):
                if str(data.get(field) or "") == old_id:
                    data[field] = new_id
            new_json = dst / ("%s.json" % new_id)
            new_json.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            if new_json != old_json:
                old_json.unlink()
        old_jar = dst / ("%s.jar" % old_id)
        new_jar = dst / ("%s.jar" % new_id)
        if old_jar.is_file() and not new_jar.exists():
            old_jar.rename(new_jar)
    except (OSError, ValueError) as e:
        raise SetupError(tr("改名失败（{old} → {new}）：{err}",
                            old=old_id, new=new_id, err=e)) from e
    return new_id


def is_installer_jar(path) -> bool:
    """这个 jar 是安装器吗（装完顺手检查一下，别把半个 jar 当真）

    只有 `install_profile.json` 或 `version.json` 在根上才算 ——
    失败时下到的常常是 HTML 错误页改名成的 jar。
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
        return bool({"install_profile.json", "version.json"} & names)
    except (OSError, zipfile.BadZipFile):
        return False
