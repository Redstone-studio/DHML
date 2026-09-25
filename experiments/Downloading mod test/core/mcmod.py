"""
MC 百科（mcmod.cn）链接解析

## 为什么需要这个

我们搜的是 Modrinth，用户想看的是 MC 百科。两边**没有任何共享 ID**，
名字也不完全一样（Modrinth 叫 "Fabric API"，百科词条叫 "FabricAPI"）。

Modrinth 的接口**帮不上忙**：`project.wiki_url` 指的是项目自己的 wiki
（`fabricmc.net/wiki`、GitHub wiki 那种），不是百科；搜索接口里更是连这个
字段都没有（实测过）。

## PCL2 是怎么做的（供参考）

它**自带一个离线数据库**：`Resources/mcmod.buf` 是个压缩过的 SQLite，
里面有 `WikiId` 字段 —— 也就是"Modrinth/CurseForge 项目 → 百科 class ID"
的映射表。好处是不联网、100% 准；代价是这个库得跟版本维护，而且我们没法
直接复用（格式是它自己的 protobuf + 压缩包）。

## 我们怎么做

**抓百科自己的搜索页**，从结果里挑最像的那个：

    https://search.mcmod.cn/s?key=<名字>   →   /class/<数字>.html

实测可行（`fabric api` → 3124，`sodium` → 2785，都对得上）。
百科的官方 API（`api.mcmod.cn`）是私有的，访问一律 403，所以走不通。

挑选逻辑见 `pick_best()`：先归一化（去掉空格和符号、转小写），
能精确对上就精确对上；否则要求"查询词完整包含在标题里"或"标题完整包含在查询里"，
再不够就按词重合度打分。**拿不准就返回 None**，让界面退回"搜索页"——
宁可让用户多点一下，也不要跳到一个错的词条。

## 线程

会联网，**必须在后台线程里调**（见 ui/workers/mcmod_worker.py）。
"""
import re
import time

import requests

SEARCH_URL = "https://search.mcmod.cn/s"
CLASS_URL = "https://www.mcmod.cn/class/{cid}.html"

HEADERS = {
    # 百科会挡没有 UA 的请求，用一个正常的浏览器 UA
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

TIMEOUT = 12

# ============================================================
# 限速（这个必须处理，不然会静默失败）
# ============================================================
#
# 实测：连续快速请求时，百科会**交替**返回正常页和"没有结果"的页 ——
# 而且 HTTP 仍然是 200，只看状态码发现不了，表现为"有时能匹配有时匹配不上"。
# （连续 10 次请求里 5 次是空的：Starlight/Create 命中，AppleSkin/Sodium/
#   Mod Menu/Fabric API 全空。）
#
# 两条对策：
#   1. 请求之间强制留一个最小间隔（_THROTTLE）
#   2. 页面里找不到结果块时，等一下重试（_RETRY / _RETRY_WAIT）
_THROTTLE = 0.5        # 两次请求之间至少隔这么久（秒）
_RETRY = 3             # 空结果时重试几次
_RETRY_WAIT = 0.7      # 每次重试前等多久
_last_request = 0.0


def _wait_for_slot():
    """把请求节流到 _THROTTLE 的节奏上"""
    global _last_request
    delta = time.monotonic() - _last_request
    if _last_request and delta < _THROTTLE:
        time.sleep(_THROTTLE - delta)
    _last_request = time.monotonic()

# 进程内缓存：同一个 mod 只解析一次（名字 → class id 或 None）
_CACHE = {}

# 结果块。百科的搜索页结构：
#   <div class="result-item">
#     <div class="head">...<a href="https://www.mcmod.cn/class/3124.html">名 (<em>Fabric</em> API)</a></div>
#     <div class="body">描述…</div>
#   </div>
_ITEM_RE = re.compile(r'(?:<div class="result-item">)(.*?)(?=<div class="result-item">|\Z)',
                      re.S)
_HEAD_LINK_RE = re.compile(
    r'<div class="head">.*?<a[^>]+href="([^"]*?/class/(\d+)\.html)"[^>]*>(.*?)</a>',
    re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def search_url(name: str) -> str:
    """百科搜索页（找不到对应词条时的兜底）"""
    from urllib.parse import quote
    return f"{SEARCH_URL}?key={quote(name or '')}"


def class_url(cid) -> str:
    return CLASS_URL.format(cid=cid)


def _clean_title(raw: str) -> str:
    """把 <em> 之类标签去掉，并去掉首尾空白"""
    return _TAG_RE.sub("", raw or "").strip()


def _normalize(text: str) -> str:
    """归一化：转小写、只留字母数字和中文

    这一步是匹配成功的关键 —— "Fabric API" 和 "FabricAPI" 归一化之后都是
    "fabricapi"，而 "Forgified Fabric API" 是 "forgifiedfabricapi"，
    前者能精确命中、后者不会。
    """
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (text or "").lower())


# 百科词条标题的常见写法是「[简称] 中文名 (English Name)」，
# 例如 "模组说明 (ModMenu)"、"星光 (Starlight)"、"高清修复 (OptiFine)"。
# 而 Modrinth 给的是**英文名**，所以得把括号里的英文单独抠出来当一个变体比，
# 否则一大半英文模组都对不上（实测 12 个里漏 7 个）。
_PAREN_RE = re.compile(r"[（(]\s*([^（()）]+?)\s*[)）]")


def _variants(title: str) -> list:
    """一个标题的所有"可比形式"：整体 + 括号里的英文名 + 去掉方括号简称的部分"""
    out = []

    whole = _normalize(title)
    if whole:
        out.append(whole)

    for inner in _PAREN_RE.findall(title or ""):
        norm = _normalize(inner)
        if norm:
            out.append(norm)

    # 去掉 [JEI] 这种方括号简称，剩下的再归一化一次
    stripped = re.sub(r"[\[【][^\]】]*[\]】]", " ", title or "")
    norm = _normalize(stripped)
    if norm:
        out.append(norm)

    return out


def parse_candidates(html: str) -> list:
    """从搜索页 HTML 里抠出候选 [(cid, 标题), ...]（保持页面顺序）"""
    out = []
    for block in _ITEM_RE.findall(html or ""):
        m = _HEAD_LINK_RE.search(block)
        if not m:
            continue
        url, cid, raw_title = m.group(1), m.group(2), m.group(3)
        title = _clean_title(raw_title)
        if cid and title:
            out.append((cid, title))
    return out


CONFIDENCE_EXACT = "exact"        # 归一化后完全相同 —— 可以直接跳
CONFIDENCE_STRONG = "strong"      # 互相包含且很接近 —— 建议让用户确认
CONFIDENCE_WEAK = "weak"          # 只靠词重合 —— 必须让用户确认


def pick_best(query: str, candidates: list):
    """从候选里挑最像 query 的那个

    返回 (class_id, 标题, 置信度)；都不满足就是 (None, None, None)。

    置信度分三档，**这是给界面用的**：

      · `exact`  —— 归一化后完全相同（"Fabric API" ≡ "FabricAPI"）。可以直接跳。
      · `strong` —— 互相包含且长度接近（"Fabric API" vs "Fabric API Legacy"）。
                    能用，但**建议让用户扫一眼**再跳。
      · `weak`   —— 只靠词重合度（Jaccard）。**必须确认**。

    为什么要分档：百科里一个名字会撞上一堆相似的 —— 搜 "Fabric API" 会同时出来
    `FabricAPI` / `[FFAPI] Forgified Fabric API` / `[QSL] Quilt Standard Libraries`
    / `Cloth API`。精确命中当然可以直接跳；但只做到"包含"级别的时候，
    选错就是让用户白跑一趟，那种情况应该问一句。
    """
    if not query or not candidates:
        return None, None, None

    q = _normalize(query)
    if not q:
        return None, None, None

    expanded = [(cid, title, _variants(title)) for cid, title in candidates]

    # 1) 精确
    for cid, title, variants in expanded:
        if q in variants:
            return cid, title, CONFIDENCE_EXACT

    # 2) 互相包含。两个门槛：
    #    · 查询至少 4 个字符（免得 "api" 乱匹配）
    #    · 长度比例 ≥ 0.6 —— "fabricapi"(9) vs "fabricapilegacy"(15) 比例 0.6 还行；
    #      vs "forgifiedfabricapi"(18) 只有 0.5，那明显是另一个模组，不给 strong
    if len(q) >= 4:
        for cid, title, variants in expanded:
            for v in variants:
                if not v:
                    continue
                longer, shorter = (v, q) if len(v) >= len(q) else (q, v)
                if shorter in longer:
                    if len(shorter) / len(longer) >= 0.6:
                        return cid, title, CONFIDENCE_STRONG
                    break        # 这个候选太不像（可能是它的衍生模组），看下一个

    # 3) 分词重合度
    def tokens(text):
        return {tok for tok in re.split(r"[^0-9a-z\u4e00-\u9fff]+", (text or "").lower())
                if len(tok) > 1}

    qt = tokens(query)
    if qt:
        best, best_score = None, 0.0
        for cid, title, variants in expanded:
            for v in variants:
                tt = tokens(v)
                if not tt:
                    continue
                score = len(qt & tt) / len(qt | tt)      # Jaccard
                if score > best_score:
                    best, best_score = (cid, title), score
        if best_score >= 0.55:
            return best[0], best[1], CONFIDENCE_WEAK

    return None, None, None


def _fetch_search(name: str) -> str:
    """取搜索页 HTML，带节流和空结果重试

    返回 HTML 文本；连续拿不到结果就返回空串（调用方当"未匹配"处理）。
    """
    for attempt in range(_RETRY):
        _wait_for_slot()
        try:
            r = requests.get(SEARCH_URL, params={"key": name},
                             headers=HEADERS, timeout=TIMEOUT)
        except (requests.RequestException, OSError) as e:
            print(f"[MCMod] 请求失败（{name}，第 {attempt + 1} 次）："
                  f"{type(e).__name__}: {e}")
            time.sleep(_RETRY_WAIT)
            continue

        if r.status_code != 200:
            print(f"[MCMod] HTTP {r.status_code}（{name}）")
            time.sleep(_RETRY_WAIT)
            continue

        r.encoding = r.apparent_encoding or "utf-8"
        # ⚠️ 不能只看状态码：被限速时它也是 200，只是页面里没有结果块
        if _ITEM_RE.search(r.text):
            return r.text
        time.sleep(_RETRY_WAIT)

    return ""


def resolve(name: str, use_cache: bool = True):
    """按名字解析出百科词条

    返回 `(class_id, 词条标题, 置信度, 搜索页链接)`：

      · 有结果 → ("3124", "FabricAPI", "exact", "https://search.mcmod.cn/s?key=...")
      · 没结果 → (None, None, None, 搜索页链接)

    **搜索页链接总是给**：解析失败时界面直接用它，不用再拼一次。

    ⚠️ 会联网，**别在主线程调**。限速与重试见 _fetch_search。
    """
    name = (name or "").strip()
    fallback = search_url(name)
    if not name:
        return None, None, None, fallback

    key = _normalize(name)
    if use_cache and key in _CACHE:
        cid, title, conf = _CACHE[key]
        return cid, title, conf, fallback

    cid, title, conf = None, None, None
    html = _fetch_search(name)
    if html:
        cid, title, conf = pick_best(name, parse_candidates(html))

    if use_cache:
        _CACHE[key] = (cid, title, conf)
    return cid, title, conf, fallback


def clear_cache():
    _CACHE.clear()
