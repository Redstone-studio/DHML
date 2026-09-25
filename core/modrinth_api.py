"""Modrinth 数据层：搜索 / 项目 / 版本（纯 Python，**不 import Qt**）

从 `experiments/Downloading mod test/core/modrinth_api.py` 搬过来，
按本项目的约定改了两处：

1. **文案/报错走 `tr()`** —— 异常信息会显示在界面状态行上，要跟着语言走
2. **`http_json()` 带镜像与重试** —— 跟 `core/install.py` 一个路子（那边的
   `http_json` 是给版本清单用的）。理由一样：这是整条链的入口，拿不到就
   什么都搜不出来，偏偏它最容易卡住。重试不解决"没有这个 mod"，所以
   404 直接返回、不浪费时间重试。

## 关于镜像

Modrinth 的 API 和 CDN **目前没有 BMCLAPI 镜像**（BMCLAPI 只镜像游戏本体：
清单 / 库 / 资源）。这里走 `mirror_url()` 只是为了**统一**：映射表里没有
前缀时它原样返回，将来真有镜像了往 `MIRROR_PAIRS` 加一行就行，这里不用改。

## 返回的是**原始 dict**，不是自定义模型

跟实验项目保持一致。界面那边（卡片、详情页）全是照 Modrinth 的字段名写的
（`hits` / `slug` / `icon_url` / `game_versions` …），包一层自定义对象会让
每个 `.get()` 都要改一遍，收益却只是"类型好看"。**防呆取值放在 UI 那一层**
（该有默认值的地方给默认值），数据层只负责"把 HTTP 和 JSON 弄干净"。

## 搜索接口的一个坑

`index="relevance"` 配**空查询**是没有意义的 —— Modrinth 会按自己的默认顺序
返回一串看不出规律的东西（没有"最热门"可言）。界面那边空查询时会改成
`downloads`，见 `ui/pages/mods_page.py`。
"""

import time

import requests

from core.download import USER_AGENT, mirror_url
from core.i18n import tr

API = "https://api.modrinth.com/v2"

# 请求超时（秒）：连接 / 读取
TIMEOUT = (15, 30)

# JSON 取不到时的重试次数与退避（跟 core/install.py 的 JSON_RETRY 一个量级）
RETRY = 2
BACKOFF = 0.6

# 搜索的排序方式（Modrinth 的 `index` 参数）。值是接口原文，**不翻译**
SORTS = ("relevance", "downloads", "follows", "newest", "updated")
DEFAULT_SORT = "relevance"

# Modrinth 单页上限
MAX_LIMIT = 100


class ModrinthError(RuntimeError):
    """Modrinth 那边出问题了（网络 / HTTP 状态码 / 返回的不是 JSON）

    `code` 是 HTTP 状态码；网络层面挂掉时是 0。界面想区分
    "这个 mod 被删了（404）"和"网络不通"时看它。
    """

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


def _get_json(url: str, params: dict = None):
    """发一次 GET 并解 JSON；失败抛 `ModrinthError`

    ⚠️ 返回值必须是 dict 或 list：Modrinth 偶尔会在 200 里返回别的东西
    （维护页 / 纯文本），解出来是字符串的话，后面 `.get()` 会炸在很远的地方，
    报错还看不出是网络问题。在这里拦住。
    """
    try:
        r = requests.get(url, params=params,
                         headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        raise ModrinthError(tr("Modrinth 返回 {code}", code=code), code=code) from e
    except requests.Timeout as e:
        raise ModrinthError(tr("连接 Modrinth 超时")) from e
    except requests.RequestException as e:
        raise ModrinthError(tr("连不上 Modrinth（网络或代理）")) from e
    except ValueError as e:                 # r.json() 解不出来
        raise ModrinthError(tr("Modrinth 返回的不是 JSON")) from e

    if not isinstance(data, (dict, list)):
        raise ModrinthError(tr("Modrinth 返回了看不懂的内容"))
    return data


def http_json(url: str, params: dict = None):
    """镜像优先 + 重试。**404 不重试**（那是"没有这个 mod"，重试没用）

    跟 `core/install.py` 的 `http_json` 是同一套策略，只是那边专门给
    版本清单用。这里不共用函数是因为两者的"失败长什么样"不一样：
    那个失败直接抛给安装流程，这个要变成 `ModrinthError` 让界面能分辨 404。
    """
    candidates = [mirror_url(url)]
    if candidates[0] != url:
        candidates.append(url)

    last = None
    for attempt in range(RETRY + 1):
        for cand in candidates:
            try:
                return _get_json(cand, params)
            except ModrinthError as e:
                last = e
                if e.code == 404:
                    raise                       # 换源/重试都救不了
        if attempt < RETRY:
            time.sleep(BACKOFF * (attempt + 1))
    raise last if last is not None else ModrinthError(tr("取不到 Modrinth 数据"))


def _clamp_limit(limit, fallback: int = 20) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = fallback
    return max(1, min(MAX_LIMIT, value))


def search_projects(query: str = "", mc_version: str = "全部",
                    loader: str = "全部", category="全部",
                    project_type: str = "mod", sort: str = DEFAULT_SORT,
                    limit: int = 20, offset: int = 0) -> dict:
    """搜索项目

    返回 `{"hits": [...], "total_hits": int, "offset": int, "limit": int}`。
    网络不通抛 `ModrinthError`。

    facets 是"数组的数组"：内层是**或**、外层是**且**。
    加载器只在 mod / modpack 上有意义 —— 资源包和光影包没有加载器一说，
    硬塞 `categories:fabric` 会返回 0 条，而且**不报错**（最难查的那种）。
    """
    import json

    facets = [["project_type:%s" % (project_type or "mod")]]

    if mc_version and mc_version != "全部":
        facets.append(["versions:%s" % mc_version])

    if loader and loader != "全部" and project_type in ("mod", "modpack"):
        facets.append(["categories:%s" % str(loader).lower()])

    if category and category != "全部":
        facets.append(["categories:%s" % category])

    index = sort if sort in SORTS else DEFAULT_SORT
    # 空查询按下载量排（理由见模块开头）
    if not (query or "").strip() and index == "relevance":
        index = "downloads"

    params = {
        "query": query or "",
        # 紧凑 JSON：facets 里带空格会被 requests 转义成 %20，日志里没法看
        "facets": json.dumps(facets, separators=(",", ":")),
        "index": index,
        "limit": _clamp_limit(limit),
        "offset": max(0, int(offset or 0)),
    }

    data = http_json("%s/search" % API, params)
    if not isinstance(data, dict):
        return {"hits": [], "total_hits": 0, "offset": 0, "limit": params["limit"]}
    data.setdefault("hits", [])
    data.setdefault("total_hits", 0)
    return data


def get_project(project_id: str):
    """项目详情（`project_id` 可以是 id，也可以是 slug 比如 "sodium"）

    没有这个项目（404）返回 None；网络不通抛 `ModrinthError`。
    """
    if not project_id:
        return None
    try:
        return http_json("%s/project/%s" % (API, project_id))
    except ModrinthError as e:
        if e.code == 404:
            return None
        raise


def get_project_versions(project_id: str) -> list:
    """项目的所有版本（**新的在前**）

    没有这个项目返回 []；网络不通抛 `ModrinthError`。

    ⚠️ 不在这里按游戏版本 / 加载器筛：详情页是**按分组展示全部版本**的
    （用户可能想找旧版本的），筛了反而看不到。要筛就在调用方做
    （`ui/pages/mods_page.py` 的分组本身就是按 游戏版本+加载器 分的）。
    """
    if not project_id:
        return []
    try:
        data = http_json("%s/project/%s/version" % (API, project_id))
    except ModrinthError as e:
        if e.code == 404:
            return []
        raise
    if not isinstance(data, list):
        return []
    # 字符串倒序 = 时间倒序：Modrinth 给的是 ISO-8601 UTC
    # （"2024-03-05T12:00:00+00:00"），这种格式按字典序排就是按时间排
    return sorted([v for v in data if isinstance(v, dict)],
                  key=lambda v: str(v.get("date_published") or ""), reverse=True)


def primary_file(version: dict):
    """版本里该下的那个文件（优先 primary，没有就第一个）

    一个版本可能带多个文件（sources / javadoc 之类），那些装进 mods\\
    只会让游戏报错。拿不到返回 None。
    """
    files = [f for f in (version or {}).get("files") or [] if isinstance(f, dict)]
    if not files:
        return None
    for f in files:
        if f.get("primary") and f.get("url"):
            return f
    for f in files:
        if f.get("url"):
            return f
    return None


def mcmod_search_url(name: str) -> str:
    """MC 百科搜索页（**直达词条**的解析在 core/mcmod.py，这一步只是兜底）"""
    from urllib.parse import quote
    return "https://search.mcmod.cn/s?key=%s" % quote(name or "")
