"""启动命令拼装

**纯函数，不依赖 Qt，也不读全局配置。** 这是刻意的 —— 这样它能直接单测，
也能对着真实命令行逐项 diff（PCL 导出的 .bat 就是现成的对照物）。

分工：
    这个模块负责  「版本 JSON + 一堆参数」 → 「完整的 java 参数列表 + 工作目录」
    调用方负责    起进程、收日志、解压 natives

几个容易错的点，都在这里一次性处理掉：

1. **rules 的语义是「最后一条匹配的规则生效」**，不是第一条。
   而且 `features` 必须显式判断 —— 游戏分辨率参数（--width/--height）就藏在
   `has_custom_resolution` 这个 feature 规则后面，一律跳过就会丢。

2. **离线 UUID 不能随便编。** 必须是 `OfflinePlayer:<名字>` 的 MD5（v3 UUID），
   否则同一个玩家在不同启动器里 UUID 不一样，存档和服务器的玩家数据全对不上。

3. **natives 目录**用 `<版本目录>/<版本id>-natives` —— 和 PCL / HMCL 的约定一致，
   所以它们装好的版本能直接启动。但这个目录不保证存在，缺了要解压（本模块只报告，
   不动手，解压交给调用方）。
"""

import hashlib
import platform
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from core import branding
from core.app_info import APP_NAME as DEFAULT_LAUNCHER_NAME
from core.app_info import APP_VERSION as DEFAULT_LAUNCHER_VERSION
from core.i18n import tr

# 上面两个名字/版本号跟随 core/app_info.py，不再各写一份字符串。
# 真实调用方（首页）会把自己的值传进来，这两个只是兜底。

# features：Minecraft 用这套开关决定哪些参数要加。
# has_custom_resolution 必须是 True —— 我们总是传 --width/--height。
# 其余几个（演示模式、快速进入）都是 False，跳过对应参数。
DEFAULT_FEATURES = {
    "is_demo_user": False,
    "has_custom_resolution": True,
    "has_quick_plays_support": False,
    "is_quick_play_singleplayer": False,
    "is_quick_play_multiplayer": False,
    "is_quick_play_realms": False,
}

MAX_INHERIT_DEPTH = 5


# ============================================================
# 当前环境
# ============================================================

def platform_name() -> str:
    """JSON 里 os.name 用的写法：windows / osx / linux"""
    return {"Windows": "windows", "Darwin": "osx", "Linux": "linux"}.get(
        platform.system(), platform.system().lower()
    )


def arch_name() -> str:
    """JSON 里 os.arch 用的写法。只有 32 位才需要特别标明"""
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if machine in ("x86", "i386", "i686"):
        return "x86"
    return machine


# ============================================================
# rules / natives / maven
# ============================================================

def _rule_matches(rule: dict, features: dict) -> bool:
    """这一条规则在当前环境下适不适用"""
    os_rule = rule.get("os") or {}
    if "name" in os_rule and os_rule["name"] != platform_name():
        return False
    if "arch" in os_rule and os_rule["arch"] != arch_name():
        return False
    if "version" in os_rule:
        # 正则匹配系统版本号，几乎没有 JSON 用，但规范里有
        try:
            if not re.search(os_rule["version"], platform.version()):
                return False
        except re.error:
            return False

    for key, wanted in (rule.get("features") or {}).items():
        if bool(features.get(key, False)) != bool(wanted):
            return False
    return True


def rules_allow(rules, features: dict = None) -> bool:
    """判断一个带 rules 的库/参数在当前环境下要不要

    官方语义（和 PCL、官方启动器一致）：
      - rules 缺失或为空 → 允许
      - 否则：**最后一条匹配的规则**决定结果
      - 一条都没匹配上 → 不允许
    """
    if not rules:
        return True

    features = features if features is not None else DEFAULT_FEATURES
    allowed = False
    for rule in rules:
        if _rule_matches(rule, features):
            allowed = rule.get("action") == "allow"
    return allowed


def natives_key(lib: dict) -> "str | None":
    """取这个库的原生文件 classifier（如 natives-windows-x86_64）"""
    natives = lib.get("natives") or {}
    if not natives:
        return None
    key = natives.get(platform_name())
    if not key:
        return None
    return key.replace("${arch}", "64" if arch_name() == "x86_64" else "32")


def maven_to_path(name: str):
    """Maven 坐标 → (相对路径, 相对目录)

    支持 `group:artifact:version[:classifier][@ext]`
    """
    if not name or ":" not in name:
        return None, None

    ext = "jar"
    if "@" in name:
        name, ext = name.rsplit("@", 1)

    parts = name.split(":")
    if len(parts) < 3:
        return None, None
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else None

    filename = f"{artifact}-{version}"
    if classifier:
        filename += f"-{classifier}"
    filename += f".{ext}"

    rel_dir = f"{group.replace('.', '/')}/{artifact}/{version}"
    return f"{rel_dir}/{filename}", rel_dir


# ============================================================
# 离线账户
# ============================================================

def offline_uuid(name: str) -> str:
    """离线模式的标准 UUID

    等价于 Java 的 `UUID.nameUUIDFromBytes(("OfflinePlayer:" + name).getBytes())`，
    也就是 MD5 后把版本位置成 3、variant 位置成 10xx。

    ⚠️ 不要图省事写死成全零 —— 那样同一个玩家在不同启动器里 UUID 不同，
    存档里的玩家数据、服务器白名单会对不上。
    """
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


# ============================================================
# 版本 JSON：读取 + inheritsFrom 合并
# ============================================================

def _sorted_libraries(parent: dict, child: dict) -> list:
    """合并两个版本的库列表，同名以后出现的为准

    顺序也保持：父版本在前，子版本覆盖的同名库留在原位置（官方启动器就是这么做的，
    Forge 的 `-DignoreList` 依赖 classpath 顺序）。
    """
    merged, index = [], {}
    for lib in list(parent.get("libraries") or []) + list(child.get("libraries") or []):
        name = lib.get("name", "")
        if name in index:
            merged[index[name]] = lib
        else:
            index[name] = len(merged)
            merged.append(lib)
    return merged


def merge_version(parent: dict, child: dict) -> dict:
    """把子版本合到父版本上（处理 inheritsFrom）

    子版本的字段优先；libraries 合并去重；arguments 两边拼起来。
    """
    child_args = child.get("arguments")
    parent_args = parent.get("arguments")

    if child_args and parent_args:
        arguments = {
            "jvm": list(parent_args.get("jvm") or []) + list(child_args.get("jvm") or []),
            "game": list(parent_args.get("game") or []) + list(child_args.get("game") or []),
        }
    else:
        arguments = child_args or parent_args or {"jvm": [], "game": []}

    downloads = dict(parent.get("downloads") or {})
    downloads.update(child.get("downloads") or {})

    return {
        "id": child.get("id") or parent.get("id", ""),
        "mainClass": child.get("mainClass") or parent.get("mainClass", ""),
        "type": child.get("type") or parent.get("type", "release"),
        "javaVersion": child.get("javaVersion") or parent.get("javaVersion") or {},
        # 客户端 jar 用哪个版本目录的：
        #   1. 子版本自己写了 jar 字段 → 用它（Forge 1.16- 的写法）
        #   2. 否则用父版本已经确定好的（一路回退到继承链的根）
        #   3. 都没有 → 父版本的 id 就是根，用它目录下的 jar
        # ⚠️ 只认 jar 字段是不够的：Fabric / NeoForge 的 JSON 常常**只写 inheritsFrom**
        #    不写 jar，结果就是去找 <子版本id>.jar（不存在）→ classpath 里没有
        #    Minecraft 的类 → Fabric 报 "couldn't locate the game"。
        "jar": child.get("jar") or parent.get("jar") or parent.get("id"),
        "arguments": arguments,
        "minecraftArguments": child.get("minecraftArguments") or parent.get("minecraftArguments"),
        "libraries": _sorted_libraries(parent, child),
        "assetIndex": child.get("assetIndex") or parent.get("assetIndex"),
        "assets": child.get("assets") or parent.get("assets"),
        "downloads": downloads,
        "logging": child.get("logging") or parent.get("logging"),
    }


def load_version_json(mc_dir, version_id: str) -> dict:
    """读版本 JSON（**不**处理继承链，扫描列表时用这个）"""
    path = Path(mc_dir) / "versions" / version_id / f"{version_id}.json"
    import json
    return json.loads(path.read_bytes())


def load_version(mc_dir, version_id: str) -> dict:
    """读版本 JSON 并把 inheritsFrom 链合并掉"""
    mc_dir = Path(mc_dir)
    cache = {}

    def _load(vid: str, depth: int) -> dict:
        if vid in cache:
            return cache[vid]
        if depth > MAX_INHERIT_DEPTH:
            raise RuntimeError(tr("版本继承链过深（超过 {n} 层）：{vid}",
                                  n=MAX_INHERIT_DEPTH, vid=vid))

        path = mc_dir / "versions" / vid / f"{vid}.json"
        if not path.is_file():
            raise FileNotFoundError(tr("版本 JSON 不存在：{path}", path=path))

        raw = load_version_json(mc_dir, vid)
        parent_id = raw.get("inheritsFrom")
        if parent_id:
            merged = merge_version(_load(parent_id, depth + 1), raw)
        else:
            # 没有父版本，但 JSON 自己可能就写了重复项（老版本常见），照样去重
            merged = dict(raw)
            merged["libraries"] = _sorted_libraries({}, raw)
        # ⚠️ 继承链合出来的 `jar` 只看了 **JSON 字段**，漏了"自己目录里就躺着一个
        # `<id>.jar`"这种情况 —— 而官方启动器的规矩是**自己目录里那个优先**。
        #
        #   OptiFine 就是踩这个：它的 JSON 是 `inheritsFrom: 1.20.1`、**没有** jar
        #   字段，但自己目录里有一个 23 MB 的 `<id>.jar`（打过补丁的客户端）。
        #   照 `merge_version` 那条回退会退回父版本的原版 jar —— 等于 OptiFine
        #   白装了（进游戏一看没有光影），而且**一声不响**。
        #
        # 只在 JSON **没有**明确写 `jar` 时才补这一刀：显式写了就听它的
        # （Forge 1.16- 那种 `"jar": "<自己的目录名>"` 的写法不能被顶掉）。
        if not raw.get("jar") and (mc_dir / "versions" / vid
                                   / f"{vid}.jar").is_file():
            merged["jar"] = vid
        cache[vid] = merged
        return merged

    return _load(version_id, 0)


# ============================================================
# 拼命令
# ============================================================

@dataclass
class LaunchRequest:
    """启动一个版本所需的全部输入（都是纯数据，方便测试）"""
    mc_dir: Path
    version_id: str
    java_path: str
    username: str
    # 版本目录可以显式指定 —— 目录名和 JSON 里的 id 不一定一致，
    # 扫描器知道真实路径，让它传进来比在这里猜可靠
    version_dir: Path = None
    min_memory: int = 512          # MB
    max_memory: int = 2048         # MB
    width: int = 854
    height: int = 480
    uuid: str = ""                 # 留空则按离线规则算
    access_token: str = "0"
    user_type: str = "legacy"      # 离线用 legacy
    launcher_name: str = DEFAULT_LAUNCHER_NAME
    launcher_version: str = DEFAULT_LAUNCHER_VERSION
    # 系统语言（"zh_CN" 这种）。给了就通过 -Duser.language 传给 JVM，
    # 游戏首次启动时的默认语言就跟着系统走。
    locale: str = ""
    version_type: str = ""         # 留空则用 JSON 里的 type
    # 这个版本额外要加的 JVM 参数（版本设置里填的，见 core/version_settings.py）
    extra_jvm_args: str = ""
    # 加载器的显示名（`Fabric` / `Forge`…）。只用来拼「版本信息」那个品牌串。
    # ⚠️ JSON 里没有这个东西（它只在目录名/我们的元数据里），所以由调用方传
    # （扫描器 `core/versions.py` 已经算好了 `loader_label`）。
    loader_label: str = ""
    # 这个版本是不是"版本隔离"的（决定去哪个 `mods/` 数模组）。
    # None = 不知道 → 两边都数（见 core/branding.mod_dirs）
    isolated: bool = None
    # 额外要加的游戏参数（版本设置里的"游戏参数"，追加在 JSON 自带参数后面）
    extra_game_args: str = ""
    # 自动进入服务器："ip" 或 "ip:port"。空 = 不自动进服
    server_address: str = ""


@dataclass
class LaunchPlan:
    """拼好的启动计划。调用方拿它去起进程"""
    args: list = field(default_factory=list)
    cwd: Path = None               # ⚠️ 必须是版本目录，和 --gameDir 一致
    java: str = ""
    natives_dir: Path = None
    needs_natives: bool = False    # natives 目录不存在，启动前要先解压
    classpath: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    # 本地缺少的库（JSON 说了该有、但文件不在）—— 不拦启动，只提示
    missing_libraries: list = field(default_factory=list)
    # 启动前的准备工作要用到（解压 natives）
    mc_dir: Path = None
    version_id: str = ""
    version_json: dict = field(default_factory=dict)
    # `${version_type}` 最终用的那个值 —— 它就是**游戏主界面那行字里斜杠后面那段**
    # （启动器品牌就塞在这儿）。单独留一份是为了能打日志：不然"游戏里怎么没显示品牌"
    # 这种事，日志里一点线索都没有（用户 2026-09 就是这么问的）。
    version_type: str = ""

    def describe(self) -> str:
        """给日志用的一行命令（**路径缩短 + classpath 折叠**）

        完整的命令行有几十个 classpath 路径、几 KB 长，直接打进日志窗口没法看。
        这里只做两件事：把 mc_dir 前缀缩成相对路径、把 `-cp` 那一长串换成
        `<N 个文件>`。**别的参数一个不删** —— `--versionType` / `-Xmx` /
        用户自己加的 JVM 参数正是要靠这一行看的，早先这里截断到十几个参数，
        结果把游戏参数（`--versionType` 就在那儿）全截掉了，等于白打。
        """
        out = []
        skip_next = False
        for i, arg in enumerate(self.args):
            if skip_next:
                skip_next = False
                continue
            if arg == "-cp" and i + 1 < len(self.args):
                out.append("-cp")
                out.append("<%d 个文件>" % len(str(self.args[i + 1]).split(";")))
                skip_next = True
                continue
            text = str(arg)
            if self.mc_dir:
                prefix = str(self.mc_dir)
                text = text.replace(prefix + "\\", "").replace(prefix + "/", "")
            if len(text) > 200:
                text = text[:200] + "…"
            out.append(text)
        return " ".join(out)


def _version_dir(mc_dir: Path, version_id: str) -> Path:
    return mc_dir / "versions" / version_id


# QuickPlay（--quickPlayMultiplayer）是 1.20 引入的。按发布时间判断而不是按
# 版本号：1.20 的正式版发布日是 2023-06-07，往前留一点余量给快照
# （发布日之前的快照也可能已经带上了）。这跟 PCL 的判断方式一致。
_QUICK_PLAY_SINCE = "2023-05-04"


def _supports_quick_play(release_time: str) -> bool:
    """这个版本该用 QuickPlay 还是老的 --server/--port

    releaseTime 是 ISO8601（"2023-06-07T12:00:00+00:00"），前 10 位就是日期，
    字典序比较即可。读不出来（老版本 JSON 没这个字段）就当作不支持。
    """
    value = str(release_time)[:10]
    # 先确认它真的像个日期：乱填的字符串按字典序会排到 "2023-…" 后面
    # （'u' > '2'），那样会给出错的选择
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    return value >= _QUICK_PLAY_SINCE


def _split_args(text: str) -> list:
    """把一串参数按空格拆成列表，但尊重引号（引号本身去掉）

    **刻意不用 shlex**：POSIX 模式会把 Windows 路径里的反斜杠当转义符吃掉
    （`-Djava.library.path=D:\\mc` 变成 `D:mc`），而不是 POSIX 模式又根本不合并引号
    （`-Dpath="C:\\Program Files"` 会被拆成两个参数）。自己扫一遍最稳，
    而且未闭合的引号也不会抛异常 —— 用户填错一个引号不该让启动直接失败。
    """
    if not text:
        return []
    args, current, quote = [], [], ""
    for ch in text:
        if quote:
            if ch == quote:
                quote = ""
            else:
                current.append(ch)
        elif ch in "\"'":
            quote = ch
        elif ch.isspace():
            if current:
                args.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        args.append("".join(current))
    return args


def _resolve_library(mc_dir: Path, lib: dict):
    """库文件在本地的路径。优先用 downloads.artifact.path，没有就按坐标拼"""
    artifact = (lib.get("downloads") or {}).get("artifact")
    if artifact and artifact.get("path"):
        return mc_dir / "libraries" / artifact["path"]
    rel, _ = maven_to_path(lib.get("name", ""))
    return mc_dir / "libraries" / rel if rel else None


def build_launch_plan(req: LaunchRequest, version: dict = None) -> LaunchPlan:
    """把请求和版本 JSON 拼成完整的启动计划

    version 可以外部传进来（已经合并好的），不传就自己读 + 合并。
    """
    mc_dir = Path(req.mc_dir)
    version_id = req.version_id
    version_dir = Path(req.version_dir) if req.version_dir else _version_dir(mc_dir, version_id)
    vj = version if version is not None else load_version(mc_dir, version_id)
    warnings = []

    # ---------- classpath ----------
    classpath = []
    missing_libs = []
    seen_paths = set()
    for lib in vj.get("libraries") or []:
        if not rules_allow(lib.get("rules")):
            continue
        path = _resolve_library(mc_dir, lib)
        if path is not None and path.is_file():
            # 老版本的 JSON 常把同一个库写两遍（一遍带 downloads、一遍只有 serverreq），
            # 而且它们**没有 inheritsFrom**，走不到合并那步的去重 —— 这里按真实路径兜一次
            key = str(path).lower()
            if key not in seen_paths:
                seen_paths.add(key)
                classpath.append(str(path))
            continue
        # 文件不在本地。要分清两种情况：
        #   1. JSON 明确写了下载地址 → 它本该在，缺了要报出来（多半是安装不完整）
        #   2. 没有下载地址 → 很多库（尤其 LWJGL 2 的 lwjgl-platform / jinput-platform）
        #      **本来就只有 natives、没有主 jar**，不该塞进 classpath，也不算缺
        artifact = (lib.get("downloads") or {}).get("artifact") or {}
        if artifact.get("url"):
            missing_libs.append(lib.get("name", "?"))

    # 客户端 jar：优先用 JSON 的 jar 字段（Forge 1.16- 用它指向父版本），
    # 其次找本版本的 jar
    jar_id = vj.get("jar") or version_id
    client_jar = mc_dir / "versions" / jar_id / f"{jar_id}.jar"
    if not client_jar.is_file() and jar_id != version_id:
        client_jar = version_dir / f"{version_id}.jar"
    if client_jar.is_file():
        classpath.append(str(client_jar))
    else:
        warnings.append(tr("找不到客户端 jar：{path}", path=client_jar))
        missing_libs.append(f"{jar_id}.jar")

    sep = ";" if platform_name() == "windows" else ":"
    classpath_str = sep.join(classpath)

    # ---------- natives ----------
    natives_dir = version_dir / f"{version_id}-natives"
    needs_natives = not natives_dir.is_dir()

    # ---------- 占位符 ----------
    assets_index = (vj.get("assetIndex") or {}).get("id", "")
    # ⚠️ `${version_type}` = 游戏**主界面那行字**里唯一由启动器控制的部分：
    # `"Minecraft " + 版本名 + (type == "release" ? "" : "/" + type)`（1.20.4 实测）。
    # 用户填过「自定义信息」就用他的；没填就给一份品牌默认值（`Mosslight/Fabric`），
    # 这就是"启动器名字进游戏"。填 `release` 可以关掉它（游戏那边按原样不显示）。
    # 见 core/branding.py 的模块说明（以及为什么不是"改 version.json"）。
    version_type = str(req.version_type or "").strip()
    if not version_type:
        mods = branding.mod_dirs(mc_dir, version_dir, req.isolated)
        version_type = branding.launch_brand(req.loader_label,
                                             branding.count_mods(*mods))
    replacements = {
        "${natives_directory}": str(natives_dir),
        "${classpath}": classpath_str,
        "${classpath_separator}": sep,
        "${library_directory}": str(mc_dir / "libraries"),
        "${launcher_name}": req.launcher_name,
        "${launcher_version}": req.launcher_version,
        "${auth_player_name}": req.username,
        "${auth_uuid}": req.uuid or offline_uuid(req.username),
        "${auth_access_token}": req.access_token,
        "${clientid}": "",
        "${auth_xuid}": "",
        "${user_type}": req.user_type,
        "${version_name}": version_id,
        "${version_type}": version_type or vj.get("type", "release"),
        "${game_directory}": str(version_dir),
        "${assets_root}": str(mc_dir / "assets"),
        "${assets_index_name}": assets_index,
        "${resolution_width}": str(req.width),
        "${resolution_height}": str(req.height),
        "${user_properties}": "{}",
        # 老版本（1.6 及以前）用的两个占位符，之前漏了 —— 会原样留在命令行里。
        # game_assets 指向"虚拟资源目录"，跟 PCL 用的完全一致；
        # 少了它，1.6.4 会去早就下线的 s3.amazonaws.com/MinecraftResources/ 找资源然后崩。
        "${game_assets}": str(mc_dir / "assets" / "virtual" / "legacy"),
        "${auth_session}": req.access_token or "0",
        # 快速进入：我们用不到，给空串（正常也不会被加进来，
        # 因为对应的 feature 在 DEFAULT_FEATURES 里是 False）
        "${quickPlayPath}": "",
        "${quickPlayRealms}": "",
        "${quickPlayMultiplayer}": "",
        "${quickPlaySingleplayer}": "",
        # 老版本（1.7/1.8）用到的
        "${arch}": "64" if arch_name() == "x86_64" else "32",
        "${primary_jar}": str(client_jar),
    }

    def resolve(value: str) -> str:
        for key, val in replacements.items():
            value = value.replace(key, val)
        return value

    def resolve_arg(arg):
        """一条参数 → 零条或多条"""
        if isinstance(arg, str):
            return [resolve(arg)]
        if isinstance(arg, dict):
            if not rules_allow(arg.get("rules")):
                return []
            value = arg.get("value", [])
            values = [value] if isinstance(value, str) else list(value)
            return [resolve(v) for v in values]
        return []

    # ---------- JVM 参数 ----------
    jvm_args = [f"-Xms{req.min_memory}m", f"-Xmx{req.max_memory}m"]
    # 输出编码：不设的话 Java 18+ 打出来的中文日志是乱码
    jvm_args += ["-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8"]

    # 版本自己加的额外参数（版本设置对话框里填的）。
    # 位置很讲究：**必须在 -cp 之前** —— 写到 -cp <classpath> 后面，
    # JVM 会把第一个参数当成主类名，直接报"找不到主类"。
    # 放在内存参数后面则是故意的：用户真想覆盖 -Xmx 时，后写的那个生效。
    if req.extra_jvm_args:
        jvm_args += _split_args(req.extra_jvm_args)

    jvm_from_json = (vj.get("arguments") or {}).get("jvm") or []
    if jvm_from_json:
        for arg in jvm_from_json:
            jvm_args.extend(resolve_arg(arg))
    else:
        # 老版本（1.12 及以前）JSON 里没有 jvm 参数，得自己补
        jvm_args += [
            f"-Djava.library.path={natives_dir}",
            f"-Djna.tmpdir={natives_dir}",
            f"-Dorg.lwjgl.system.SharedLibraryExtractPath={natives_dir}",
            f"-Dio.netty.native.workdir={natives_dir}",
            f"-Dminecraft.launcher.brand={req.launcher_name}",
            f"-Dminecraft.launcher.version={req.launcher_version}",
            "-cp", classpath_str,
        ]

    # ---------- 让游戏的默认语言跟随系统 ----------
    # 游戏第一次启动时会按 Java 的 locale 挑语言，把系统的传进去最保险。
    # （之后玩家在游戏里改过语言，那个选择记在 options.txt 里，这里不影响。）
    if req.locale:
        lang, _, region = req.locale.partition("_")
        for flag, value in (("-Duser.language=", lang.lower()),
                            ("-Duser.country=", region.upper())):
            if value and not any(a.startswith(flag) for a in jvm_args):
                jvm_args.append(flag + value)

    # ---------- 启动器该补的 JVM 参数 ----------
    # ⚠️ 这几个**不在版本 JSON 里**（实测 Forge / NeoForge / OptiFine 的 JSON 都没有），
    # 是启动器的责任。PCL 也是自己加的 —— 对着它导出的命令行 diff 才发现漏了。
    #   签名校验：不关掉的话，老版本 Forge 会拒绝加载没签名的 mod
    #   补丁差异：OptiFine 这类改过 Minecraft jar 的版本，不关掉 Forge 会直接拒绝启动
    #   Log4Shell：1.7~1.17 的 log4j 有远程执行漏洞，这是官方的缓解开关
    for extra in (
        "-Dfml.ignoreInvalidMinecraftCertificates=true",
        "-Dfml.ignorePatchDiscrepancies=true",
        "-Dlog4j2.formatMsgNoLookups=true",
    ):
        key = extra.split("=", 1)[0] + "="
        if not any(a.startswith(key) for a in jvm_args):
            jvm_args.append(extra)

    # ---------- 游戏参数 ----------
    game_args = [vj.get("mainClass", "net.minecraft.client.main.Main")]

    game_from_json = (vj.get("arguments") or {}).get("game") or []
    if game_from_json:
        for arg in game_from_json:
            game_args.extend(resolve_arg(arg))
    elif vj.get("minecraftArguments"):
        # 老格式：一整个字符串
        game_args.extend(_split_args(resolve(vj["minecraftArguments"])))

    # 版本设置里追加的游戏参数。放**最后**：MC 的选项解析对同一个选项
    # 取后面的值，所以用户填的 --width/--height 能盖掉 JSON 里那份。
    if req.extra_game_args:
        game_args.extend(_split_args(req.extra_game_args))

    # 自动进入服务器
    server = (req.server_address or "").strip()
    if server:
        if _supports_quick_play(vj.get("releaseTime", "")):
            # 1.20 起 --server/--port 没了，改走 QuickPlay
            game_args.extend(["--quickPlayMultiplayer", server])
        else:
            host, _, port = server.partition(":")
            game_args.extend(["--server", host, "--port", port or "25565"])

    # ---------- log4j 配置（有就用，没有不影响启动）----------
    logging_cfg = (vj.get("logging") or {}).get("client") or {}
    log_arg = logging_cfg.get("argument")
    if log_arg:
        file_id = (logging_cfg.get("file") or {}).get("id", "")
        for candidate in (
            mc_dir / "assets" / "log_configs" / file_id,
            mc_dir / "assets" / "log4j_configs" / file_id,
        ):
            if candidate.is_file():
                jvm_args.append(resolve(log_arg.replace("${path}", str(candidate))))
                break

    return LaunchPlan(
        args=[req.java_path] + jvm_args + game_args,
        cwd=version_dir,
        java=req.java_path,
        natives_dir=natives_dir,
        needs_natives=needs_natives,
        classpath=classpath,
        missing_libraries=missing_libs,
        warnings=warnings,
        mc_dir=mc_dir,
        version_id=version_id,
        version_json=vj,
        version_type=version_type or vj.get("type", "release"),
    )
