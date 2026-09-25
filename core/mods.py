"""模组数据层：Modrinth 检索 / 项目 / 版本 → 下载任务（**纯 Python，不 import Qt**）

这是模组安装器的**第一步**（见 `.dhml-tests/plan_mod_installer.md` 第五节）：
只负责"回答问题和算清单"，一个字节都不下、一个控件都不建。

    search()          → 一页搜索结果（MOD 卡片用）
    get_project()     → 项目详情（详情页用）
    get_versions()    → 版本列表（挑版本用）
    select_version()  → 按游戏版本 / 加载器挑出该装哪个版本
    download_tasks()  → 变成 core/download.py 能直接吃的 (url, 路径, sha1, 大小)
    resolve_mods_dir()→ 该装进哪个 mods\\ 目录（版本隔离在这里落地）

## 为什么 HTTP 是**注入**的

`fetch` 参数默认是 `fetch_json`（真联网），测试里塞一个假函数就能把整条链
跑一遍 —— 包括 facets 到底拼成什么样。这是从 `core/install.py` 的
`load_asset_index(..., fetch=...)` 学来的路子：网络是唯一的不可控因素，
把它顶到边界上，里面全是纯逻辑，于是**离线也能断言**。

## 网络出错时：抛 ModrinthError；404 才软失败

这两件事必须分开，否则界面没法给出正确的提示：

  - **404** = "这个东西不存在"（作者删了、id 打错了）→ 返回 `None` / `[]`。
    调用方不需要 try，直接判空就行。
  - **网络不通 / 超时 / 5xx** → 抛 `ModrinthError`。
    调用方要 try 一下，然后**明确告诉用户是网络问题**。

为什么网络错误不能也返回空：那样"这个项目没有版本"和"Modrinth 连不上"
在界面上长得一模一样，用户完全不知道该等一会儿还是该换 mod。
（这跟 `core/versions_remote.py` 一个态度 —— 那边也是把错误信息交上去，
让下载页显示"网络环境不佳"那一页。）

调用方看着三行就够：

    try:
        result = mods.search(query)
    except mods.ModrinthError as e:
        ...显示"网络不通"...
    if result is None or not result.hits:
        ...显示"没有结果"...

mod 下载**不做缓存**：mod 动辄几十 MB，全缓存是硬盘杀手（用户 2026-09 提醒过，
见 `ui/pages/download_page.py` 里那段注释）。只有搜索这种几百 KB 的
元数据将来才值得缓存。
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.download import USER_AGENT
from core.i18n import tr

API = "https://api.modrinth.com/v2"

# 搜索结果里"全部"的哨兵值。界面上那三个下拉框（游戏版本 / 加载器 / 分类）
# 都用它表示"不限" —— 拼 facets 时看到它就跳过。
ANY = "全部"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100                 # Modrinth 单页上限

# 排序方式（search 的 index 参数）。值是 Modrinth 的原文，别翻译。
SORTS = ("relevance", "downloads", "follows", "newest", "updated")
DEFAULT_SORT = "relevance"

# 项目类型（Modrinth 的 project_type facet）。
# 这一版只做 mod，别的先留着 —— 资源包 / 光影包 / 整合包以后接上来是一样的路子。
PROJECT_TYPES = ("mod", "modpack", "resourcepack", "shader", "datapack")

# 加载器 → Modrinth 的 categories 名字。**必须用 Modrinth 的写法**，
# 写成 "forge" 之外的东西（比如 "Forge"）facet 会静默匹配不到任何东西。
LOADERS = ("fabric", "forge", "neoforge", "quilt", "liteloader", "rift")

TIMEOUT = 20.0


class ModrinthError(RuntimeError):
    """Modrinth 那边出问题了（网络 / HTTP 状态码 / 返回的不是 JSON）

    `code` 是 HTTP 状态码，网络层面挂掉时是 0 —— 界面想区分
    "这个 mod 被删了(404)"和"网络不通"时看它。
    """

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


def _real(value) -> str:
    """把哨兵「全部」归一成空串

    ⚠️ 这个看起来很啰嗦的函数是个**真 bug 的修复**：`search` 用 `ANY` 当
    "不限"的哨兵，而 `ModVersion.matches("")` 用空串当"不筛"。
    以前 `get_versions()` 直接把 `ANY` 转手给 `matches()`，于是默认调用
    （`get_versions(pid)` —— 详情页第一次打开就是这样）会拿 "全部" 去和
    `game_versions` 比，**每个版本都被筛掉**，永远返回空列表。
    哨兵只该活在"用户界面 → 查询参数"这一段，进到比较逻辑之前必须归一。
    """
    if value is None:
        return ""
    text = _s(value).strip()
    return "" if text in (ANY, "*", "-") else text


def is_missing(exc) -> bool:
    """这个异常是"东西不存在"(404)吗 —— 界面区分提示用

    404 已经在数据层变成 None 了，这个函数是给"自己 catch 了异常想再分一下"
    的调用方用的。
    """
    return isinstance(exc, ModrinthError) and exc.code == 404


# ============================================================
# HTTP
# ============================================================

def fetch_json(path: str, params: dict = None, timeout: float = TIMEOUT):
    """GET 一个 API 路径并解成 JSON；失败抛 `ModrinthError`

    `path` 是 API 下的相对路径（`/search`、`/project/sodium`），
    这样调用方不用自己拼 API 前缀，测试里也好看。

    ⚠️ 返回值**必须**是 dict 或 list：Modrinth 偶尔会在 200 里返回别的东西
    （维护页），当成 JSON 解出来是字符串的话，后面 `data.get(...)` 会炸在
    很远的地方，报错还看不出是网络问题。在这里拦住。
    """
    import requests        # 函数内 import：core/install.py 也这么干，
                           # 纯逻辑的单元测试不该顺带把网络栈拖进来

    url = path if path.startswith("http") else API + path
    try:
        r = requests.get(url, params=params,
                         headers={"User-Agent": USER_AGENT}, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        raise ModrinthError("Modrinth 返回 %d" % code, code=code) from e
    except requests.Timeout as e:
        raise ModrinthError("连接 Modrinth 超时") from e
    except requests.RequestException as e:
        raise ModrinthError("连不上 Modrinth（网络或代理）") from e
    except ValueError as e:                    # r.json() 解不出来
        raise ModrinthError("Modrinth 返回的不是 JSON") from e

    if not isinstance(data, (dict, list)):
        raise ModrinthError("Modrinth 返回了看不懂的内容")
    return data


def _get(path: str, params: dict, fetch):
    """统一入口：拿数据 + 把"没有这个项目(404)"变成 None

    404 单独处理是因为它不是"出错"，而是"这个东西不存在"：
    搜索页点了一个已经被作者删掉的 mod，界面该说"找不到"，
    而不是弹一句网络错误。

    ⚠️ 注入的 `fetch` 签名必须是 `(path, params)`（测试里写
    `lambda path, params=None: ...` 就行）。**故意不去兼容**只收一个参数的
    假函数 —— 用 try/except TypeError 兜的话，假函数自己写错时
    （比如内部 TypeError）会被当成"签名不对"重试一次，把真正的 bug 盖掉。
    """
    try:
        return (fetch or fetch_json)(path, params)
    except ModrinthError as e:
        if e.code == 404:
            return None
        raise


# ============================================================
# 数据模型
# ============================================================

def _s(value, fallback: str = "") -> str:
    """取一个字符串字段；None / 数字 / 乱七八糟的东西都变成字符串

    ⚠️ 不要省这一步。Modrinth 的字段**不是每个项目都有**（老项目缺
    `license`、`icon_url`，某些版本缺 `downloads`），直接
    `data["icon_url"]` 会 KeyError；而 `str(None)` 会给界面送去一个
    "None" 字符串 —— 加载器会去下一个叫"None"的文件。
    """
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    return str(value)


def _i(value, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _tags(value) -> "list[str]":
    if not isinstance(value, (list, tuple)):
        return []
    return [_s(v) for v in value if isinstance(v, (str, int))]


@dataclass
class ModFile:
    """版本里的一个文件（一个版本可能有多个：主文件 + 附加的 sources / javadoc）"""
    url: str = ""
    filename: str = ""
    sha1: str = ""
    size: int = 0
    primary: bool = False

    @classmethod
    def from_json(cls, data: dict) -> "ModFile":
        data = data or {}
        url = _s(data.get("url"))
        hashes = data.get("hashes") or {}
        return cls(
            url=url,
            # ⚠️ 文件名以 URL 末尾为准：Modrinth 的 `filename` 字段偶尔是空的，
            # 而 URL 里一定有。都没有的话才真的没法下（调用方会跳过）。
            filename=_s(data.get("filename")) or url.rsplit("/", 1)[-1],
            sha1=_s(hashes.get("sha1")).lower(),
            size=_i(data.get("size")),
            primary=bool(data.get("primary")),
        )


@dataclass
class ModVersion:
    """一个可下载的版本"""
    id: str = ""
    project_id: str = ""
    name: str = ""
    version_number: str = ""
    version_type: str = "release"       # release / beta / alpha
    game_versions: "list[str]" = field(default_factory=list)
    loaders: "list[str]" = field(default_factory=list)
    date_published: str = ""
    downloads: int = 0
    files: "list[ModFile]" = field(default_factory=list)
    dependencies: "list[dict]" = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict) -> "ModVersion":
        data = data or {}
        files = [ModFile.from_json(f) for f in (data.get("files") or [])
                 if isinstance(f, dict)]
        return cls(
            id=_s(data.get("id")),
            project_id=_s(data.get("project_id")),
            name=_s(data.get("name")),
            version_number=_s(data.get("version_number")),
            version_type=_s(data.get("version_type"), "release"),
            game_versions=_tags(data.get("game_versions")),
            loaders=_tags(data.get("loaders")),
            date_published=_s(data.get("date_published")),
            downloads=_i(data.get("downloads")),
            files=files,
            dependencies=[d for d in (data.get("dependencies") or [])
                          if isinstance(d, dict)],
        )

    # ---------- 便捷 ----------

    def primary_file(self) -> "ModFile | None":
        """要下的那个文件

        优先 `primary=True` 的；没有就取第一个 —— 有些版本（尤其老的）
        根本没标 primary，这种时候"第一个"就是作者想让你下的那个。
        """
        for f in self.files:
            if f.primary:
                return f
        return self.files[0] if self.files else None

    def display(self) -> str:
        """一行里显示的版本名（版本号比 `name` 短、更常用）"""
        return self.version_number or self.name or self.id

    def matches(self, game_version: str = "", loader: str = "") -> bool:
        """这个版本适不适合给定的游戏版本 / 加载器

        两个参数留空 = 不筛。游戏版本用**相等**而不是"包含"：
        Modrinth 的 `game_versions` 里偶尔会有 "1.20" 这种只有大版本的写法，
        真做兼容判断会很绕；界面那边是"从实际装了的版本里选"，精确匹配够用。

        哨兵「全部」也当不筛（`_real()` 做归一）—— 调用方从界面直接转手
        传进来的是哨兵，不是空串。
        """
        game_version = _real(game_version)
        loader = _real(loader)
        if game_version and game_version not in self.game_versions:
            return False
        if loader and loader not in self.loaders:
            return False
        return True


@dataclass
class ModProject:
    """搜索结果 / 项目详情

    搜索和 `/project/{id}` 的字段有重叠但不完全一样（搜索少了 body、
    多了 project_id），所以两边都用这个类装，缺的字段留默认值。
    """
    id: str = ""
    slug: str = ""
    title: str = ""
    description: str = ""
    body: str = ""                      # 详情页的长描述（markdown）
    icon_url: str = ""
    author: str = ""
    downloads: int = 0
    follows: int = 0
    categories: "list[str]" = field(default_factory=list)
    loaders: "list[str]" = field(default_factory=list)
    game_versions: "list[str]" = field(default_factory=list)
    versions: "list[str]" = field(default_factory=list)   # 搜索给的版本 id 列表
    project_type: str = "mod"
    license: str = ""
    updated: str = ""
    team: str = ""

    @classmethod
    def from_json(cls, data: dict) -> "ModProject":
        data = data or {}
        # `loaders` 是搜索接口给的；详情接口给的是 `categories` 里混着加载器。
        # 两个都收下，界面想显示哪个都行。
        loaders = _tags(data.get("loaders"))
        categories = _tags(data.get("categories"))
        if not loaders:
            loaders = [c for c in categories if c.lower() in LOADERS]

        # 作者：搜索接口没有，详情接口有；`team` 是团队 id，不能直接当名字显示
        author = _s(data.get("author"))
        license_data = data.get("license") or {}
        license_name = (_s(license_data.get("name")) if isinstance(license_data, dict)
                        else _s(license_data))

        return cls(
            id=_s(data.get("project_id")) or _s(data.get("id")),
            slug=_s(data.get("slug")),
            title=_s(data.get("title")),
            description=_s(data.get("description")),
            body=_s(data.get("body")),
            icon_url=_s(data.get("icon_url")),
            author=author,
            downloads=_i(data.get("downloads")),
            follows=_i(data.get("follows")),
            categories=categories,
            loaders=loaders,
            game_versions=_tags(data.get("game_versions")),
            versions=_tags(data.get("versions")),
            project_type=_s(data.get("project_type"), "mod"),
            license=license_name,
            updated=_s(data.get("updated")) or _s(data.get("date_modified")),
            team=_s(data.get("team")),
        )


@dataclass
class ModSearchResult:
    """一页搜索结果"""
    hits: "list[ModProject]" = field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = DEFAULT_LIMIT

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.hits) < self.total


# ============================================================
# facets：搜索的筛选条件
# ============================================================

def build_facets(project_type: str = "mod", game_version: str = ANY,
                 loader: str = ANY, categories=()) -> "list[list[str]]":
    """拼 Modrinth 的 facets

    格式是"数组的数组"：`[["a","b"],["c"]]` 念作 **(a 或 b) 且 c** ——
    内层是或、外层是且。游戏版本只有一个值时是单元素内层，无所谓；
    以后要做"这个 mod 支持哪些版本"的多选，往内层加就行。

    分类传多个也拼进**同一个内层**（或关系）—— 用户勾了"科技"和"魔法"，
    意思是"这两个我都要看"，不是"同时属于这两类的 mod"。
    """
    ptype = _s(project_type, "mod") or "mod"
    facets = [["project_type:%s" % ptype]]

    if game_version and game_version != ANY:
        facets.append(["versions:%s" % game_version])

    # 加载器只在 mod / modpack 上有意义：资源包和光影包没有加载器一说，
    # 硬塞 categories:fabric 进去会返回 0 条（而且不报错，最难查）
    if loader and loader != ANY and ptype in ("mod", "modpack"):
        if loader.lower() in LOADERS:
            facets.append(["categories:%s" % loader.lower()])

    picked = [_s(c) for c in (categories or []) if _s(c)]
    if picked:
        facets.append(["categories:%s" % c for c in picked])

    return facets


def _facets_param(facets) -> str:
    """facets 要**紧凑** JSON：requests 的默认编码会把空格转成 %20，
    而 Modrinth 对 facets 的解析对格式化后的 JSON 一样能用，但紧凑写法
    在日志和测试里一眼能看明白到底几个条件。"""
    return json.dumps(facets, separators=(",", ":"))


# ============================================================
# 对外：搜索 / 项目 / 版本
# ============================================================

def search(query: str = "", game_version: str = ANY, loader: str = ANY,
           categories=(), project_type: str = "mod", sort: str = DEFAULT_SORT,
           limit: int = DEFAULT_LIMIT, offset: int = 0,
           fetch=None) -> "ModSearchResult | None":
    """搜索项目

    ⚠️ **`query` 留空 + `sort="relevance"` 时改用 downloads**：
    空查询下 relevance 没有意义，Modrinth 会按自己的默认顺序给一串
    看不出规律的东西（没有"最热门"可言）。改成按下载量排，空搜索页
    就是"最流行的 mod"，这才是用户想看的。
    """
    index = sort if sort in SORTS else DEFAULT_SORT
    if not query.strip() and index == "relevance":
        index = "downloads"

    limit = max(1, min(MAX_LIMIT, _i(limit, DEFAULT_LIMIT) or DEFAULT_LIMIT))
    params = {
        "query": query or "",
        "facets": _facets_param(build_facets(project_type, game_version,
                                             loader, categories)),
        "index": index,
        "limit": limit,
        "offset": max(0, _i(offset)),
    }

    data = _get("/search", params, fetch)
    if not isinstance(data, dict):
        return None

    hits = [ModProject.from_json(h) for h in (data.get("hits") or [])
            if isinstance(h, dict)]
    return ModSearchResult(
        hits=hits,
        # `total_hits` 是 Modrinth 的叫法；有些镜像会写成 `total`，都收
        total=_i(data.get("total_hits", data.get("total"))),
        offset=_i(data.get("offset", offset)),
        limit=_i(data.get("limit", limit)),
    )


def get_project(project_id: str, fetch=None) -> "ModProject | None":
    """取项目详情（`project_id` 可以是 id 也可以是 slug，比如 "sodium"）

    没有这个项目（404）返回 None；网络不通抛 `ModrinthError`（见模块开头
    那段"网络出错时"的说明）。
    """
    if not project_id:
        return None
    data = _get("/project/%s" % project_id, None, fetch)
    if not isinstance(data, dict):
        return None
    return ModProject.from_json(data)


def get_versions(project_id: str, game_version: str = ANY, loader: str = ANY,
                 fetch=None) -> "list[ModVersion]":
    """取项目的版本列表（**新的在前**），可按游戏版本 / 加载器筛

    Modrinth 的 `/version` 支持 `game_versions` / `loaders` 查询参数，
    但**服务端筛和客户端筛都用**：服务端省流量，客户端那份是保险 ——
    参数拼错或 API 行为变了也不至于把不兼容的版本列给用户。

    没有这个项目（404）返回 []；网络不通抛 `ModrinthError`。
    """
    if not project_id:
        return []

    params = {}
    if game_version and game_version != ANY:
        params["game_versions"] = json.dumps([game_version])
    if loader and loader != ANY and loader.lower() in LOADERS:
        params["loaders"] = json.dumps([loader.lower()])

    data = _get("/project/%s/version" % project_id, params, fetch)
    if not isinstance(data, list):
        return []

    # ⚠️ 过筛和排序前先归一哨兵（见 _real 的说明：不归一的话默认调用
    # 会把所有版本筛掉）
    want_version = _real(game_version)
    want_loader = _real(loader)

    versions = [ModVersion.from_json(v) for v in data if isinstance(v, dict)]
    versions = [v for v in versions if v.matches(want_version, want_loader)]
    # 新的在前。按**字符串**倒序：Modrinth 给的是 ISO-8601 UTC
    # （"2024-03-05T12:00:00+00:00"），这种格式按字典序排就是按时间排。
    versions.sort(key=lambda v: v.date_published, reverse=True)
    return versions


def select_version(versions, game_version: str = "", loader: str = "",
                   prefer_release: bool = True) -> "ModVersion | None":
    """从版本列表里挑一个"该装"的

    规则（顺序即优先级）：
      1. 先按游戏版本 / 加载器筛；筛完是空的就**放宽**到没筛的列表
         （宁可装个可能不兼容的，也别让用户点了没反应 —— 界面上会提示）
      2. 有正式版就优先正式版（`release`），全是 beta/alpha 才退回它们
      3. 同样条件下选**最新**的（列表已经新的在前，取第一个）

    ⚠️ 第 1 条的"放宽"是刻意的：Modrinth 上很多 mod 的 `game_versions`
    写的是 "1.20" 而用户装的是 "1.20.1"，严格筛会得到空列表。放宽之后
    用户至少能看到东西，界面上会说明"没有精确匹配的版本"。
    """
    versions = list(versions or [])
    if not versions:
        return None

    picked = [v for v in versions if v.matches(game_version, loader)]
    if not picked:
        picked = versions

    if prefer_release:
        releases = [v for v in picked if v.version_type == "release"]
        if releases:
            picked = releases

    return picked[0]


# ============================================================
# 安装目录
# ============================================================

def resolve_mods_dir(mc_dir, version_info: dict = None) -> Path:
    """该把 mod 装进哪个 `mods\\` 目录（**已经有 scans 结果时用这个**）

    **默认版本隔离**（用户 2026-09 定的）：`core/versions.py` 扫版本时已经
    判过这件事了 —— 版本文件夹里出现 `mods` / `saves` / `options.txt` 之类
    特征就认为 `game_dir` 就是这个版本文件夹（PCL / HMCL 的版本隔离），
    否则 `game_dir` 就是共用的 `.minecraft`。

    所以这里**不自己猜**、也不重新扫盘：直接把 `version_info["game_dir"]`
    接过来用，保证"启动器认为的目录"和"启动时用的目录"始终是同一个
    （自己再判一次迟早会跟 `VersionScanner` 判得不一样，那种 bug 表现为
    "mod 装上了但游戏里没有"）。

    ⚠️ **大多数时候该用 `mods_dir_for()` 而不是这个函数**：从 Modrinth 搜索
    进来时手上只有"游戏版本 + 加载器"，没有 `VersionScanner` 的结果
    （见 `mods_dir_for` 的说明）。

    没给 `version_info`（或者里面没有 `game_dir`）时退回 `<mc_dir>/mods`。
    """
    info = version_info or {}
    game_dir = info.get("game_dir") if isinstance(info, dict) else None
    if game_dir:
        return Path(game_dir) / "mods"
    return Path(mc_dir) / "mods"


# ---------- 按"游戏版本 + 加载器"反查版本文件夹 ----------
#
# 这一段的规则是照 PCL2 / HMCL 的实际文件夹命名来的（从实验项目
# `experiments/Downloading mod test/core/mc_dir.py` 搬过来，并按本项目的
# 约定重写）：
#
#     versions/1.20.4/                        原版：就一个游戏版本号
#     versions/1.20.4-Fabric 0.19.5/          Fabric：`<游戏版本>-Fabric <loader 版本>`
#     versions/1.12.2-Forge_14.23.5.2864/     Forge：**下划线**，不是空格
#
# ⚠️ **为什么需要这个反查**：Modrinth 只知道"1.20.4 + fabric"，
# 而磁盘上的文件夹叫 `1.20.4-Fabric 0.19.5` —— 光有游戏版本拼不出目录名
# （加载器版本号只有磁盘上那份才知道）。所以必须**列目录 + 按名字匹配**。
#
# ⚠️ 为什么不用 `VersionScanner.scan()` 去筛：这里要的是"**文件夹名**"，
# 而 scanner 给的是 `dir_name` / `id` 都有的 dict，还得跟 Modrinth 的
# 游戏版本/加载器对上；自己列目录反而更直接，而且**不用 import
# core/versions.py**（避免 core 内部多一条依赖）。

# 视作"原版"的加载器写法（Modrinth 给的可能只是空串）
VANILLA = ("", "vanilla", "原版", "none", "全部")


def _digits(text: str) -> tuple:
    """从 `Fabric 0.19.5` 里抠出版本号元组 `(0, 19, 5)`，用来比大小"""
    return tuple(int(x) for x in re.findall(r"\d+", text or ""))


def list_version_dirs(mc_dir) -> "list[str]":
    """`versions/` 下所有**像个版本**的文件夹名

    判据是"文件夹里有同名的 .json" —— PCL2 / HMCL / 官方启动器都是这个约定。
    不这么卡的话，`versions/` 里的缓存、备份、natives 目录也会被当成版本。

    ⚠️ 用 `core.versions._is_dir` 那套安全探测（Python 3.11/3.12 上
    `Path.is_dir()` 碰到 WinError 5 会**抛异常**而不是返回 False，
    真实环境里确实有这种目录）。这里自带一份最小实现，不 import
    core/versions.py。
    """
    root = Path(mc_dir) / "versions"
    out = []
    try:
        entries = list(root.iterdir())
    except (OSError, ValueError):
        return []
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
            if (entry / (entry.name + ".json")).is_file():
                out.append(entry.name)
        except OSError:
            continue            # 单个坏目录不能拖垮整次枚举
    return sorted(out)


def split_version_folder(name: str) -> "tuple[str, str]":
    """`1.20.4-Fabric 0.19.5` → `('1.20.4', 'Fabric 0.19.5')`

    没有 `-` 的就是纯原版，尾巴给空串。
    """
    head, sep, tail = (name or "").partition("-")
    return (head, tail) if sep else (head, "")


def loader_token(tail: str) -> str:
    """从 `-` 后面那段里抠出**加载器名**：`NeoForge_47.1.106` → `neoforge`

    分隔符是空格或下划线 —— PCL2 两种都写（`Fabric 0.19.5` / `Forge_14.23.5.2864`）。
    返回小写；空串就是没有加载器。

    ⚠️ **为什么要单独抠出来、而不是 `tail.lower().startswith(loader)`**：
    找 forge 时 `"neoforge_47.1.106".startswith("forge")` 是 **False**，
    所以"startswith"看着能用 —— 但反过来是 **True**（"neoforge" 本身
    以 "forge" 开头），于是找 **forge** 会直接命中 **NeoForge** 的文件夹。
    实验项目 `mc_dir.py` 里正是这么写的（注释说用 startswith 是为了防这个，
    其实防不住）。按加载器名**整体**比对才是对的。

    `1.20.4-1.20.4-forge-51.0.0` 这类（Forge 官方安装器生成的中间目录）
    也能抠出来。
    """
    token = (tail or "").strip()
    if not token:
        return ""
    # 空格和下划线都是分隔符；`.` 不算（版本号里有），但要拦住
    # "forge-51.0.0" 这种又连着加载器版本的情况
    for sep in (" ", "_", "-"):
        token = token.split(sep)[0]
    return token.strip().lower()


def _is_loader_of(name: str, loader: str) -> bool:
    """这个文件夹名里的加载器**是不是** `loader`（宽严兜底用）

    先看 `-` 后面那段的加载器名（`NeoForge_47.1.106` → neoforge），
    对不上再在整串里找 `legacy-fabric` 这种**带前缀的写法**
    （Legacy Fabric 的文件夹就长这样）。

    ⚠️ 这里也必须整体比对：不能写成 `loader in name.lower()` ——
    那样找 forge 会命中 `1.20.1-NeoForge_47.1.106`（"neoforge" 含 "forge"）。
    """
    token = loader_token(split_version_folder(name)[1])
    if token == loader:
        return True
    low = name.lower()
    return ("legacy-" + loader) in low or ("legacy_" + loader) in low


def match_version_dir(mc_dir, game_version: str, loader: str = ""):
    """在 `versions/` 里找最合适的版本文件夹

    返回 `(文件夹名 或 None, 说明)`。说明是给人看的 ——
    "为什么选了这个"经常要回头看（比如同一个游戏版本装了两份 Fabric）。

    匹配规则（从紧到松）：
      1. 要原版：名字**就是**游戏版本，或者 `-` 后面是空的
      2. 要加载器：`-` 前面 == 游戏版本，且 `-` 后面的**加载器名整体**
         等于它（见 `loader_token`：`Fabric 0.19.5`、`Forge_14.23.5.2864`
         两种写法都能中，而找 forge **不会**命中 NeoForge）
      3. 兜底：名字里同时含游戏版本和加载器名（宽松匹配，说明里会标出来）
    """
    names = list_version_dirs(mc_dir)
    if not names:
        return None, tr("versions 目录里没有找到任何版本")
    if not game_version:
        return None, tr("不知道游戏版本")

    want = (loader or "").strip()
    low = want.lower()
    want_loader = low not in VANILLA

    exact, loose = [], []
    for name in names:
        head, tail = split_version_folder(name)

        if not want_loader:
            if name == game_version or (head == game_version and not tail):
                exact.append(((), name))
            continue

        if head == game_version and loader_token(tail) == low:
            # 同一个游戏版本+加载器可能装了好几份（加载器版本不同）→ 取版本号最高的
            exact.append((_digits(tail), name))
        elif game_version in name and _is_loader_of(name, low):
            loose.append(name)

    if exact:
        exact.sort(key=lambda pair: pair[0], reverse=True)
        return exact[0][1], tr("匹配 {version} + {loader}",
                               version=game_version, loader=want)
    if loose:
        return loose[0], tr("按名字宽松匹配到 {name}（自己核对一下）", name=loose[0])
    return None, tr("没找到 {version} 的 {loader} 版本文件夹",
                    version=game_version, loader=want or tr("原版"))


def mods_dir_for(mc_dir, game_version: str = "", loader: str = "",
                 version_info: dict = None) -> Path:
    """**从 Modrinth 搜索进来时该用这个**：按游戏版本 + 加载器算 `mods\\` 目录

    优先顺序：
      1. 有 `version_info`（`VersionScanner` 扫出来的那份）→ 用它算好的
         `game_dir`（最准，见 `resolve_mods_dir`）
      2. 没有 → 按游戏版本 + 加载器去 `versions/` 里反查文件夹名
         （版本隔离：`versions/1.20.4-Fabric 0.19.5/mods`）
      3. 还是没匹配到 → 退回 `<mc_dir>/mods`

    ⚠️ 第 3 条不是错误路径，是**正常会走到**的：用户搜了个 1.21.4 的 mod，
    但本地只装了 1.20.1 —— 这时候装进共用的 `mods/` 并让界面提示
    "没有匹配的版本文件夹"，比硬造一个目录名强。
    """
    if version_info:
        return resolve_mods_dir(mc_dir, version_info)
    if not game_version:
        return Path(mc_dir) / "mods"
    name, _why = match_version_dir(mc_dir, game_version, loader)
    if name:
        return Path(mc_dir) / "versions" / name / "mods"
    return Path(mc_dir) / "mods"


# ============================================================
# 版本 → 下载任务
# ============================================================

def download_tasks(version: "ModVersion | dict | None", mods_dir,
                   kind: str = None) -> "list[dict]":
    """把一个版本变成"能塞给 DownloadManager"的任务列表

    返回 dict 列表（`core/download.py` 的 `add_all` 能直接吃）：

        {"url": ..., "path": Path, "sha1": ..., "size": ..., "label": ..., "kind": ...}

    为什么返回 dict 而不是 `core/install.py` 的 `Task` 对象：
    那个 Task 是**安装计划**的一部分（带 kind=库/资源），mod 这边是
    "用户挑的几个文件"，两者只有四个字段重合。用 dict 走
    `DownloadManager.add_all` 的 dict 分支，谁也不依赖谁。

    **主文件只有一个**（`primary_file()`）：一个 Modrinth 版本可能带
    sources / javadoc 之类的附加文件，那些装进 mods\\ 只会让游戏报错。
    以后要做"整合包里的每个 mod"时再另写一个函数。

    拿不到可下载的文件就返回 []（界面上说"这个版本没有可下载的文件"）。
    """
    if version is None:
        return []
    if isinstance(version, dict):
        version = ModVersion.from_json(version)

    f = version.primary_file()
    if f is None or not f.url:
        return []

    mods_dir = Path(mods_dir)
    label = f.filename or version.display()
    return [{
        "url": f.url,
        "path": mods_dir / label,
        "sha1": f.sha1,
        "size": f.size,
        # 行里的前缀是 `[Mod] sodium-0.5.8.jar` 这种（见 download_window 的 _Row）
        "kind": kind if kind is not None else tr("模组"),
        "label": label,
    }]


def add_to_manager(manager, version: "ModVersion | dict | None", mods_dir,
                   kind: str = None) -> int:
    """把任务的活干完：算清单 + 塞进管理器；返回**真的加了几个**

    ⚠️ 只塞不 start —— 跟 `core/install.py` 的 `start_install` 同一个理由：
    界面要先把进度窗口摆出来，否则前几百毫秒的进度会丢。

    ⚠️ 返回值是"新加进去的个数"，不是"任务总数"：管理器按目标路径去重
    （同一个 mod 点两次只会有一个任务），所以重复调用时这个数会是 0。
    """
    tasks = download_tasks(version, mods_dir, kind=kind)
    if not tasks:
        return 0
    before = len(manager.tasks)
    manager.add_all(tasks)
    return len(manager.tasks) - before
