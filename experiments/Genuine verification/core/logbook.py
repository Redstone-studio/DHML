"""
运行日志

## 为什么要有个"看得见"的日志

这个项目里所有难查的问题都是同一类：**点下去没反应 / 报了个看不懂的错**。
而根因往往藏在 HTTP 层：哪个 URL、什么请求体、服务端到底回了什么。

控制台能看到，但要一边开终端一边用界面，还得往上翻 —— 实际用起来没人会这么做。
所以这里把日志收进内存，界面上单独一页实时显示。

## ⚠️ 最重要的规矩：**令牌绝不进日志**

日志里会记 HTTP 请求体和响应体，而它们里面有：

    password / access_token / refresh_token / identityToken / RpsTicket /
    device_code / Authorization 头 …

这些一旦进了日志，就会：显示在界面上（截图、录屏、共享屏幕全泄露）、
被写进磁盘、被贴到聊天里发给别人求助。

所以 `redact()` 是**这一层唯一的强制入口**：所有进日志的东西都必须先过它。
而且它是**按 key 递归处理的**，不是简单的字符串替换 ——
字符串替换漏一个大小写变体就白做了。

**宁可把不该遮的遮掉，也不能漏掉该遮的。**
"""
import copy
import json
import threading
import time

#: 一级/二级最多留多少条（界面用不着无限长，而且多了会卡）
MAX_ENTRIES = 500

#: 名字里含这些片段的 key，值一律换成 ***
#:
#: ⚠️ 用"包含"而不是"等于"：真实世界里同一个东西有好几种写法
#: （`access_token` / `accessToken` / `Access-Token`），
#: 用等于匹配迟早漏掉一个。
#:
#: ⚠️ 但**不能把 `auth` 当关键词** —— `author` 会被误伤，
#: 搜索结果里那一堆 author 全变成 ***。所以只认完整的 `authorization`。
#: 同理 `code` 也不能当关键词：错误响应里的 `code` 是错误码（有用信息），
#: 而真正的授权码出现在 **URL 查询串**里 —— 那个由 `redact_url()` 单独处理。
SECRET_HINTS = (
    "password", "passwd", "secret",
    "token", "ticket",           # access_token / identityToken / RpsTicket / UserTokens
    "authorization",
    "cookie", "session",
    "devicecode",                # 设备码流程里的 device_code
    "apikey",
)

#: 例外：名字里带上面那些词、但**不是**秘密的
SAFE_KEYS = {
    "token_type",                  # 值是 "Bearer"，不是秘密
    "expires_in",
    "userhash", "uhs",             # 用户哈希，界面上本来就显示
}

#: URL 查询串里出现这些参数名 → 值遮掉
#:
#: ⚠️ 这一条是**必需的**：OAuth 的授权码就是 `?code=...` 这样过来的，
#: 而它出现在 URL 里，不是 JSON 的 key，`redact()` 那套按 key 匹配的规则管不到。
#: （用户第一条消息里给的链接就长这样：`.../callback?code=M.C511_...`）
URL_SECRET_PARAMS = (
    "code", "access_token", "refresh_token", "id_token", "token",
    "device_code", "client_secret", "password", "state",
)

MASK = "***"


def _is_secret(key: str) -> bool:
    lowered = str(key).lower().replace("-", "").replace("_", "")
    if lowered in {k.replace("-", "").replace("_", "") for k in SAFE_KEYS}:
        return False
    return any(hint.replace("_", "") in lowered for hint in SECRET_HINTS)


def redact_url(url: str) -> str:
    """把 URL 查询串里的敏感参数遮掉

    路径和域名留着 —— 排查问题正需要知道"打到哪个端点了"。
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    text = str(url or "")
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.query:
        return text

    pairs, changed = [], False
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if any(hint in key.lower() for hint in URL_SECRET_PARAMS):
            pairs.append((key, MASK))
            changed = True
        else:
            pairs.append((key, value))
    if not changed:
        return text
    # ⚠️ safe="*"：不加的话 `***` 会被编码成 `%2A%2A%2A`，
    # 日志里看着像乱码，一眼认不出"这里被遮了"
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(pairs, safe="*"), parts.fragment))


def redact(value, _depth: int = 0):
    """递归地把敏感字段换成 ***

    · 字典：按 key 判断
    · 列表：逐个递归
    · 字符串：**原样返回** —— 见下面那段说明
    """
    if _depth > 8:
        return value

    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if _is_secret(key):
                out[key] = MASK
            else:
                out[key] = redact(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, bytes):
        return f"<{len(value)} 字节>"
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + f"…（共 {len(value)} 字符）"
    return value


def _pretty(value) -> str:
    """把请求/响应体变成人能读的一行行文字"""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(redact(value), ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return repr(value)
    if isinstance(value, bytes):
        return f"<{len(value)} 字节>"
    text = str(value)
    return text if len(text) <= 4000 else text[:4000] + "…（截断）"


class Entry:
    __slots__ = ("time", "level", "step", "title", "detail", "request",
                 "response")

    def __init__(self, level, step, title, detail="", request="", response=""):
        self.time = time.time()
        self.level = level          # info / ok / warn / error
        self.step = step            # 哪一步（"OAuth2 设备码"、"XSTS"…）
        self.title = title
        self.detail = detail
        self.request = request
        self.response = response

    @property
    def clock(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.time))

    def text(self) -> str:
        """整条日志的纯文本（复制给别人才好使）"""
        parts = [f"[{self.clock}] {self.level.upper():5} {self.step} — {self.title}"]
        if self.detail:
            parts.append(self.detail)
        if self.request:
            parts.append(f"--- 请求 ---\n{self.request}")
        if self.response:
            parts.append(f"--- 响应 ---\n{self.response}")
        return "\n".join(parts)


_entries = []
_listeners = []
_lock = threading.Lock()


def record(level: str, step: str, title: str, detail: str = "",
           request=None, response=None) -> Entry:
    """记一条。`request` / `response` 会先过 `redact()`"""
    entry = Entry(level, step, title, detail,
                  _pretty(request), _pretty(response))
    with _lock:
        _entries.append(entry)
        if len(_entries) > MAX_ENTRIES:
            del _entries[:len(_entries) - MAX_ENTRIES]
        listeners = list(_listeners)
    for callback in listeners:
        try:
            callback(entry)
        except Exception as e:
            # 监听者（界面）出错不能把记录日志的人带崩
            print(f"[Logbook] 监听回调出错：{type(e).__name__}: {e}")
    return entry


def info(step, title, detail="", request=None, response=None):
    return record("info", step, title, detail, request, response)


def ok(step, title, detail="", request=None, response=None):
    return record("ok", step, title, detail, request, response)


def warn(step, title, detail="", request=None, response=None):
    return record("warn", step, title, detail, request, response)


def error(step, title, detail="", request=None, response=None):
    return record("error", step, title, detail, request, response)


def http(step: str, method: str, url: str, status=None,
         request=None, response=None, error: str = "", elapsed: float = 0.0,
         extra: str = ""):
    """记一次 HTTP 调用

    ⚠️ `request` 里如果带了 headers，headers 也要一起过 redact ——
    `Authorization: Bearer xxx` 就是这么漏出去的。

    `extra` 是给"响应头里有重要信息"那种情况用的（比如 authlib-injector
    的 `X-Authlib-Injector-API-Location` 就在响应头里，不在响应体里）——
    只记 body 的话，自动识别失败时日志里什么都看不到。
    """
    level = "info"
    safe_url = redact_url(url)
    if error:
        level, title = "error", f"{method} {safe_url} → {error}"
    elif status is not None and int(status) >= 400:
        level, title = "error", f"{method} {safe_url} → HTTP {status}"
    elif status is not None:
        level, title = "ok", f"{method} {safe_url} → HTTP {status}"
    else:
        title = f"{method} {safe_url}"

    bits = []
    if elapsed:
        bits.append(f"{elapsed:.2f}s")
    if extra:
        bits.append(extra)
    return record(level, step, title, " · ".join(bits), request, response)


def entries() -> list:
    with _lock:
        return list(_entries)


def clear():
    with _lock:
        _entries.clear()


def subscribe(callback):
    """订阅新日志（界面用）。返回一个"退订"函数

    ⚠️ 一定要退订：页面被销毁之后回调还会被调到，
    轻则白干活，重则 RuntimeError。
    """
    with _lock:
        _listeners.append(callback)

    def unsubscribe():
        with _lock:
            if callback in _listeners:
                _listeners.remove(callback)
    return unsubscribe


def as_text() -> str:
    """全部日志拼成纯文本（"复制全部"用）"""
    return "\n\n".join(e.text() for e in entries())
