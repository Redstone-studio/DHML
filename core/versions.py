"""本地 Minecraft 版本扫描

从 .minecraft/versions 读取所有已安装版本，供首页的版本下拉框和启动逻辑使用。

这里踩过的坑（都是被真实数据逼出来的）：
1. 版本文件夹名 != 版本 id。以 <文件夹名>.json 里的 "id" 字段为准。
2. versions/ 底下会混进非版本的东西：.zip 压缩包、native-libraries 目录等。
   判据是"有没有一个带 id 的 json"，不是"是不是目录"。
3. 文件夹名可能带空格、中文、全角字符，全部按 UTF-8 原样处理，不做转义。
4. 单个版本坏掉（json 损坏、权限不足）不能影响其它版本，只记进 errors。
5. 模组版本（Fabric/Forge）自己往往没有 jar，真正的 jar 在 inheritsFrom
   指向的父版本里，所以 jar 可能是 None，由启动器顺着 inherits_from 往上找。
6. 整合包的 json 里 type 也写着 "release"、releaseTime 还很新，直接按类型+时间
   排序会让下拉框榜首全是整合包，所以必须分三段（见 _sort_key）。
7. 只依赖标准库，不 import PyQt6 —— core/ 不需要知道 UI 的存在。
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

__all__ = [
    "VersionScanner",
    "default_minecraft_dir",
    "scan_versions",
]

# 版本隔离的判定特征。这些文件/目录出现在版本文件夹里，说明玩家把这个版本
# 文件夹当成了独立的游戏目录（PCL / HMCL 的"版本隔离"）。
_ISOLATED_MARKERS = (
    "saves",
    "options.txt",
    "servers.dat",
    "logs",
    "mods",
    "resourcepacks",
    "shaderpacks",
)

# 加载器识别。顺序即优先级：neoforge 必须排在 forge 前面，否则会被 forge 抢先命中。
_LOADER_PATTERNS = (
    ("neoforge", "NeoForge", r"neoforge"),
    ("forge", "Forge", r"forge"),
    ("fabric", "Fabric", r"fabric"),
    ("quilt", "Quilt", r"quilt"),
    ("optifine", "OptiFine", r"optifine"),
    ("labymod", "LabyMod", r"labymod"),
    ("liteloader", "LiteLoader", r"liteloader"),
)

# 纯版本号文件夹名，如 1.20.1 / 1.7.10
_PLAIN_VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")

# 官方版本里"不算正式版"的类型，它们也归入第一段
_NON_RELEASE_OFFICIAL = ("snapshot", "old_beta", "old_alpha")

# 分类优先级：官方版本 → 加载器版本 → 整合包 / 认不出来的
_KIND_RANK = {"vanilla": 0, "loader": 1, "pack": 2}

# 排序用的版本类型优先级，越小越靠前
_TYPE_RANK = {"release": 0, "snapshot": 1, "old_beta": 2, "old_alpha": 3}
_UNKNOWN_RANK = 9


def default_minecraft_dir() -> Path:
    """默认的 .minecraft 路径

    查找顺序：
      1. 环境变量 MCLUNCHER_MINECRAFT_DIR（整合包装在 D 盘之类的地方就设这个）
      2. 各平台默认位置

    设置页做好之后，可以在这里加一条"读 config.json"，
    这样就不必让用户改环境变量了。
    """
    env = os.environ.get("MCLUNCHER_MINECRAFT_DIR")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / ".minecraft"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "minecraft"
    return Path.home() / ".minecraft"


def _to_timestamp(value) -> float:
    """把 releaseTime 转成时间戳

    解析不出来就返回 0；因为排序时取负号，0 会排到最后。
    """
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class VersionScanner:
    """扫描本地已安装的 Minecraft 版本"""

    def __init__(self, mc_dir: "Path | str | None" = None):
        self.mc_dir = Path(mc_dir).expanduser() if mc_dir else default_minecraft_dir()
        self.versions_dir = self.mc_dir / "versions"
        # 扫描过程中跳过的坏版本，留给 UI 去提示，不往上抛异常
        self.errors: "list[str]" = []

    # ---------- 对外接口 ----------

    def scan(self) -> "list[dict]":
        """返回所有本地版本，已排好序

        每个版本是一个 dict：
            id            str        版本唯一标识（取自 json 的 "id"）
            kind          str        vanilla / loader / pack，见 _classify()
            type          str        release / snapshot / old_beta / old_alpha / unknown
            dir_name      str        版本文件夹名（可能和 id 不一样）
            display_name  str        给 UI 显示的名字
            loader        str|None   主加载器：forge / fabric / neoforge / optifine ...
            loaders       list[str]  命中的所有加载器
            path          Path       版本文件夹
            jar           Path|None  客户端 jar，模组版本可能是 None
            json          Path       版本 json
            inherits_from str|None   父版本 id
            complete      bool       能不能直接启动（jar 在，或能继承父版本）
            game_dir      Path       该版本的游戏目录
            isolated      bool       是否版本隔离
            java_major    int|None   该版本要求的 Java 大版本（json 里的 javaVersion）
            release_time  str|None   json 里的 releaseTime

        排序规则见 _sort_key：官方版本 → 加载器版本 → 整合包。
        """
        self.errors = []

        if not self.versions_dir.is_dir():
            self.errors.append(f"版本目录不存在: {self.versions_dir}")
            return []

        try:
            entries = sorted(self.versions_dir.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            self.errors.append(f"版本目录无法读取: {e}")
            return []

        versions: "list[dict]" = []
        for entry in entries:
            if not entry.is_dir():      # 跳过 versions 底下的 .zip 之类
                continue
            info = self._read_one(entry)
            if info is not None:
                versions.append(info)

        self._mark_duplicate_ids(versions)
        versions.sort(key=self._sort_key)
        return versions

    def find(self, version_id: str) -> "dict | None":
        """按 id 或文件夹名找一个版本，给启动逻辑用"""
        for info in self.scan():
            if info["id"] == version_id or info["dir_name"] == version_id:
                return info
        return None

    # ---------- 内部实现 ----------

    def _read_one(self, dir_path: Path) -> "dict | None":
        """读一个版本文件夹；不是版本目录就返回 None"""
        loaded = self._load_json(dir_path)
        if loaded is None:
            return None
        json_path, data = loaded

        version_id = str(data.get("id") or dir_path.name)
        loaders = self._detect_loaders(f"{version_id} {dir_path.name}")
        game_dir, isolated = self._detect_game_dir(dir_path)
        jar = self._find_jar(dir_path, version_id)
        inherits_from = data.get("inheritsFrom")
        version_type = str(data.get("type") or "unknown")

        return {
            "id": version_id,
            "kind": self._classify(dir_path.name, loaders, version_type),
            "type": version_type,
            # 文件夹名通常比 json 里的 id 更有信息量（带加载器版本号、整合包名），
            # 两者不一样时优先显示文件夹名。
            "dir_name": dir_path.name,
            "display_name": dir_path.name if dir_path.name != version_id else version_id,
            "loader": loaders[0] if loaders else None,
            "loaders": loaders,
            "path": dir_path,
            "jar": jar,
            "json": json_path,
            "inherits_from": inherits_from,
            # 模组版本没有自己的 jar 是正常的（jar 在父版本里），所以只要
            # "jar 在" 或 "能继承" 就算可启动。
            "complete": jar is not None or bool(inherits_from),
            "game_dir": game_dir,
            "isolated": isolated,
            "java_major": (data.get("javaVersion") or {}).get("majorVersion"),
            "release_time": data.get("releaseTime"),
        }

    def _load_json(self, dir_path: Path) -> "tuple[Path, dict] | None":
        """找出并读出该版本的 json

        优先 <文件夹名>.json；对不上时退化为目录下其它的 *.json（整合包常被改名）。
        一个能用的 json 都没有 → 这根本不是版本目录，静默跳过。
        """
        preferred = dir_path / f"{dir_path.name}.json"
        candidates: "list[Path]" = []
        if preferred.is_file():
            candidates.append(preferred)
        candidates += [p for p in sorted(dir_path.glob("*.json")) if p != preferred]

        for path in candidates:
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception as e:
                # 只对"本该是版本 json"的那个报错，其它杂 json 不打扰用户
                if path == preferred:
                    self.errors.append(f"{dir_path.name}/{path.name}: 读取失败 ({e})")
                continue
            if isinstance(data, dict) and data.get("id"):
                return path, data

        return None

    @staticmethod
    def _find_jar(dir_path: Path, version_id: str) -> "Path | None":
        """找客户端 jar

        原版是 <id>.jar。模组版本经常没有自己的 jar（真正的 jar 在父版本里），
        这种情况下返回 None，让启动器顺着 inherits_from 往上找。
        """
        for name in (f"{version_id}.jar", f"{dir_path.name}.jar"):
            candidate = dir_path / name
            if candidate.is_file():
                return candidate
        # 文件夹和 jar 都改过名的情况：目录下只有一个 jar 就认它
        jars = sorted(dir_path.glob("*.jar"))
        return jars[0] if len(jars) == 1 else None

    def _detect_game_dir(self, dir_path: Path) -> "tuple[Path, bool]":
        """判断游戏目录

        版本隔离（PCL/HMCL）会把存档、配置直接放进版本文件夹。看到特征文件就
        认为 game_dir = 版本文件夹，否则和 .minecraft 共用。
        这是启发式判断，不是百分之百可靠。
        """
        for marker in _ISOLATED_MARKERS:
            if (dir_path / marker).exists():
                return dir_path, True
        return self.mc_dir, False

    @staticmethod
    def _detect_loaders(text: str) -> "list[str]":
        low = text.lower()
        return [key for key, _label, pattern in _LOADER_PATTERNS if re.search(pattern, low)]

    @staticmethod
    def _classify(dir_name: str, loaders: "list[str]", version_type: str) -> str:
        """把版本分成三类，决定下拉框里的分段

        vanilla: 官方版本（没加载器，文件夹名是纯版本号，或者是快照）
        loader:  带加载器的版本（Forge / Fabric / NeoForge ...）
        pack:    整合包，以及认不出来的东西
        """
        if loaders:
            return "loader"
        if _PLAIN_VERSION_RE.match(dir_name) or version_type in _NON_RELEASE_OFFICIAL:
            return "vanilla"
        return "pack"

    @staticmethod
    def _mark_duplicate_ids(versions: "list[dict]") -> None:
        """同一个 id 出现多次时，把文件夹名补进显示名，免得下拉框里两个一模一样"""
        groups: "dict[str, list[dict]]" = {}
        for info in versions:
            groups.setdefault(info["id"], []).append(info)
        for version_id, group in groups.items():
            if len(group) > 1:
                for info in group:
                    info["display_name"] = f'{version_id} | {info["dir_name"]}'

    @staticmethod
    def _sort_key(info: dict):
        rank = _KIND_RANK.get(info["kind"], 9)
        type_rank = _TYPE_RANK.get(info["type"], _UNKNOWN_RANK)
        has_loader = 1 if info["loader"] else 0
        # 时间取负 = 新的排前面；解析不出时间的是 0，自然落到最后
        return (rank, type_rank, has_loader, -_to_timestamp(info["release_time"]),
                info["display_name"].lower())


def scan_versions(mc_dir: "Path | str | None" = None) -> "list[dict]":
    """一行拿到版本列表，UI 用这个就够了

    需要读扫描告警（errors）时，自己 new 一个 VersionScanner。
    """
    return VersionScanner(mc_dir).scan()
