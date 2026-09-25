"""
authlib-injector（外置登录 / Yggdrasil）协议

## 这是什么

`authlib-injector` 是一套**替身协议**：它把游戏客户端里的 Mojang 验证请求
劫持到一个第三方站点上。于是"外置登录"站（皮肤站、验证站）就能自己发账号 ——
玩家用站点的账号登录，游戏里照样能进开启了"外置登录"的服务器。

它对外就是一个 **Yggdrasil API**（Mojang 老版验证接口的形状）：

    <API 根>/
        authserver/authenticate    登录
        authserver/validate        校验令牌
        authserver/refresh         刷新令牌
        authserver/invalidate      作废令牌
        sessionserver/session/minecraft/profile/<uuid>   查档案（含皮肤）
        api/profiles/minecraft     按名字批量查档案
        api/user/profile/<uuid>    （可选）
        textures/<hash>            纹理文件（皮肤本体）

## 自动识别（用户说的"启动器会自动识别"）

用户只需要填**站点地址**（`demo.lunch.ink`），不用知道 API 根在哪。
启动器向站点发一个请求，读响应头：

    X-Authlib-Injector-API-Location: /api/yggdrasil/

⚠️ 这个值**可能是相对路径**（实测 demo.lunch.ink 给的就是 `/api/yggdrasil/`），
所以必须用 `urljoin` 按**请求的那个 URL** 拼，不能直接当成绝对地址用。
它也可能是完整 URL（跨域部署时）。两种都要认。

## 这个实现的两处"不标准"

实测 `demo.lunch.ink`（implementationName = `mc-yggdrasil`）有两点和规范不一样，
写代码时必须兼容，不然会误判成"服务器没数据"：

**① 外面套了一层信封。** 标准里 `POST /api/profiles/minecraft` 返回的是
一个**裸 JSON 数组**；这里返回的是 `{"success": true, "data": [...], "error": null}`。
API 根的元数据也是既在顶层又在 `data` 里各放一份。
所以统一走 `_unwrap()`：**是数组就用数组，是信封就掏 data**。

**② 错误体的 `error` 字段是对象不是字符串。** 标准里是
`{"error": "ForbiddenOperationException", "errorMessage": "..."}`，
这里是 `{"error": {"code": ..., "message": ...}, "errorMessage": "..."}`。
取错误信息一律优先读 `errorMessage`。
"""
import base64
import json
import uuid as uuid_module
from urllib.parse import urljoin, urlparse

import requests

from core import httplog

#: 默认的 API 根路径（发现不了时的兜底）
DEFAULT_API_PATH = "/api/yggdrasil"

TIMEOUT = 15

HEADERS = {
    "User-Agent": "MosslightVerifyTest/0.1 (experiment)",
    "Accept": "application/json",
}


class YggdrasilError(Exception):
    """带业务信息的错误

    `message` 直接来自服务端的 `errorMessage`，**要原样给用户看** ——
    它比"登录失败"有用得多（比如区分"密码错"和"令牌过期"）。
    """

    def __init__(self, message: str, status: int = 0, code: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code

    def __str__(self):
        return self.message


# ============================================================
# 小工具
# ============================================================

def _unwrap(payload):
    """把"信封"拆开

    `{"success": true, "data": X}` → X；裸数据原样返回。
    **没有这层的话，服务端明明返回了数据也会被当成空**（实测踩过）。
    """
    if isinstance(payload, dict) and "data" in payload and (
            "success" in payload or "traceId" in payload or "error" in payload):
        return payload["data"]
    return payload


def _error_message(response) -> str:
    """从错误响应里抠出人话

    优先 `errorMessage`（两种实现的差异见模块开头），
    其次 `error.message`，再其次 `message`，最后退回状态码。
    """
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict):
        for key in ("errorMessage", "message"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        err = body.get("error")
        if isinstance(err, dict):
            for key in ("message", "code"):
                if err.get(key):
                    return str(err[key])
        if isinstance(err, str) and err.strip():
            return err.strip()
    return f"HTTP {response.status_code}"


def normalize_site(text: str) -> str:
    """把用户填的东西变成 `https://<host>`

    能吃下这几种写法：
        demo.lunch.ink
        https://demo.lunch.ink/
        https://demo.lunch.ink/api/yggdrasil
        http://127.0.0.1:8080
    """
    text = (text or "").strip()
    if not text:
        return ""
    if not text.startswith(("http://", "https://")):
        # 本地/内网地址用 http 更常见；其余一律 https
        host = text.split("/")[0].split(":")[0]
        scheme = "http" if host in ("localhost", "127.0.0.1") or host.startswith("192.168.") else "https"
        text = f"{scheme}://{text}"
    parsed = urlparse(text)
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    return base + path


# ============================================================
# 自动识别
# ============================================================

def discover_api_root(site: str, timeout: float = TIMEOUT) -> tuple:
    """从站点地址找出真正的 API 根

    返回 `(api_root, 说明)`。说明是给界面显示的 —— "是怎么找到的"
    在排查"为什么连不上"时很关键。

    顺序：
      1. 用户填的地址里**已经带路径**（含 `/api/`）→ 直接用，不再猜
      2. 请求站点根，读 `X-Authlib-Injector-API-Location` 响应头（**自动识别**）
      3. 都失败 → 退回 `<站点>/api/yggdrasil`
    """
    site = normalize_site(site)
    if not site:
        raise YggdrasilError("还没填验证站地址")

    parsed = urlparse(site)
    if parsed.path and parsed.path not in ("", "/"):
        # 用户直接给了 API 根（或者别的路径），尊重他填的
        return site, "用户直接指定的路径"

    # ---- 自动识别 ----
    try:
        r = httplog.call("识别 API 根", "GET", site + "/", timeout,
                         headers=HEADERS, allow_redirects=True)
    except requests.RequestException as e:
        raise YggdrasilError(f"连不上 {site}：{type(e).__name__}") from e

    location = r.headers.get("X-Authlib-Injector-API-Location", "").strip()
    if location:
        # ⚠️ 相对路径要按**请求的那个 URL** 拼（实测给的就是 /api/yggdrasil/）
        resolved = urljoin(r.url, location).rstrip("/")
        return resolved, f"站点响应头指向 {location}"

    # ---- 兜底 ----
    return (site + DEFAULT_API_PATH).rstrip("/"), "站点没给响应头，按默认路径猜的"


# ============================================================
# 元数据
# ============================================================

def fetch_meta(api_root: str, timeout: float = TIMEOUT) -> dict:
    """读 API 根的元数据

    返回统一结构（**标准字段在顶层，但这家的信封里也有一份，两处都认**）：

        {"serverName": ..., "implementationName": ..., "skinDomains": [...],
         "features": {...}, "links": {...}}
    """
    try:
        r = httplog.call("读站点信息", "GET", api_root, timeout, headers=HEADERS)
    except requests.RequestException as e:
        raise YggdrasilError(f"连不上 API 根：{type(e).__name__}") from e
    if r.status_code != 200:
        raise YggdrasilError(_error_message(r), r.status_code)

    try:
        raw = r.json()
    except ValueError as e:
        raise YggdrasilError("API 根返回的不是 JSON") from e

    # 顶层有就用顶层，没有就掏信封
    body = raw if isinstance(raw, dict) and "meta" in raw else _unwrap(raw)
    if not isinstance(body, dict):
        raise YggdrasilError("API 根返回的结构不认识")

    meta = body.get("meta") or {}
    features = {k[len("feature."):]: v for k, v in meta.items()
                if k.startswith("feature.")}
    return {
        "serverName": meta.get("serverName") or "",
        "implementationName": meta.get("implementationName") or "",
        "implementationVersion": meta.get("implementationVersion") or "",
        "links": meta.get("links") or {},
        "features": features,
        "skinDomains": list(body.get("skinDomains") or []),
        "signaturePublickey": body.get("signaturePublickey") or "",
    }


# ============================================================
# 档案
# ============================================================

def _as_uuid(raw: str) -> str:
    """把 32 位无连字符的 id 变成标准 UUID 写法；不是 UUID 就原样返回"""
    text = str(raw or "").replace("-", "").strip()
    if len(text) == 32:
        try:
            return str(uuid_module.UUID(text))
        except ValueError:
            return text
    return str(raw or "")


def lookup_profiles(api_root: str, names, timeout: float = TIMEOUT) -> list:
    """按名字批量查档案

    `POST /api/profiles/minecraft`，body 是名字数组。

    返回 `[{"id": "<无连字符>", "name": "..."}]` —— **查不到就是空列表**，
    不是错误（实测这个站对未知名字返回 `{"success":true,"data":[]}`）。
    """
    names = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not names:
        return []
    url = f"{api_root}/api/profiles/minecraft"
    try:
        r = httplog.call("查角色", "POST", url, timeout,
                         headers={**HEADERS, "Content-Type": "application/json"},
                         json=names)
    except requests.RequestException as e:
        raise YggdrasilError(f"查询失败：{type(e).__name__}") from e
    if r.status_code != 200:
        raise YggdrasilError(_error_message(r), r.status_code)

    try:
        body = _unwrap(r.json())
    except ValueError as e:
        raise YggdrasilError("查询返回的不是 JSON") from e

    if not isinstance(body, list):
        return []
    out = []
    for item in body:
        if isinstance(item, dict) and item.get("id"):
            out.append({"id": str(item["id"]), "name": str(item.get("name") or "")})
    return out


def fetch_profile(api_root: str, player_uuid: str, timeout: float = TIMEOUT):
    """按 UUID 查档案（含皮肤纹理）

    ⚠️ **查不到时服务端返回 HTTP 204**（无内容），不是 404 ——
    所以"204"要当成"这个人不在这台服务器上"，返回 None，别当错误抛。
    """
    raw = str(player_uuid or "").replace("-", "").strip()
    if len(raw) != 32:
        return None
    url = f"{api_root}/sessionserver/session/minecraft/profile/{raw}"
    try:
        r = httplog.call("查档案", "GET", url, timeout, headers=HEADERS)
    except requests.RequestException as e:
        raise YggdrasilError(f"查档案失败：{type(e).__name__}") from e

    if r.status_code == 204 or not r.content:
        return None
    if r.status_code != 200:
        raise YggdrasilError(_error_message(r), r.status_code)
    try:
        body = _unwrap(r.json())
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def decode_textures(profile: dict) -> dict:
    """把档案里的 `textures` 属性解出来

    `properties` 里那条 `name == "textures"` 的 `value` 是
    **base64 过的 JSON**，解开长这样：

        {"textures": {"SKIN": {"url": "https://.../textures/abc"}, "CAPE": {...}}}

    ⚠️ base64 可能要补 `=`（长度不是 4 的倍数时），也可能用 URL-safe 字符集。
    """
    for prop in (profile or {}).get("properties") or []:
        if not isinstance(prop, dict) or prop.get("name") != "textures":
            continue
        value = str(prop.get("value") or "")
        if not value:
            continue
        try:
            padded = value + "=" * (-len(value) % 4)
            data = json.loads(base64.b64decode(padded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data.get("textures") or {}
    return {}


def skin_url(profile: dict, skin_domains=None) -> str:
    """档案 → 皮肤 PNG 的地址（没有就返回空串）

    会校验域名在不在 `skinDomains` 里。**这一步不是摆设**：
    ALI 的签名机制靠 skinDomains 限定"皮肤只能从这些域加载"，
    不校验的话等于允许任意第三方 URL 冒充皮肤。
    """
    url = ((decode_textures(profile).get("SKIN") or {}).get("url") or "").strip()
    if not url:
        return ""
    if not skin_domains:
        return url
    host = (urlparse(url).hostname or "").lower()
    for domain in skin_domains:
        d = str(domain).lower().lstrip(".")
        if host == d or host.endswith("." + d):
            return url
    return ""


# ============================================================
# 登录
# ============================================================

def authenticate(api_root: str, username: str, password: str,
                 client_token: str = "", timeout: float = TIMEOUT) -> dict:
    """登录，拿 accessToken 和角色档案

    返回 `{"accessToken", "clientToken", "selectedProfile", "availableProfiles"}`。

    ⚠️ **密码只在这一层出现，不要往日志/配置/界面里带。**
    这个函数不做任何持久化，调用方也不该做。
    """
    url = f"{api_root}/authserver/authenticate"
    body = {
        "username": username,
        "password": password,
        "clientToken": client_token or str(uuid_module.uuid4()),
        "requestUser": True,
        # agent 段是规范要求的；老实现会忽略它
        "agent": {"name": "Minecraft", "version": 1},
    }
    try:
        r = httplog.call("登录", "POST", url, timeout,
                         headers={**HEADERS, "Content-Type": "application/json"},
                         json=body)
    except requests.RequestException as e:
        raise YggdrasilError(f"登录请求失败：{type(e).__name__}") from e
    if r.status_code not in (200, 201):
        raise YggdrasilError(_error_message(r), r.status_code)

    try:
        data = _unwrap(r.json())
    except ValueError as e:
        raise YggdrasilError("登录返回的不是 JSON") from e
    if not isinstance(data, dict) or not data.get("accessToken"):
        raise YggdrasilError("登录返回里没有 accessToken")
    return data


def validate(api_root: str, access_token: str, client_token: str = "",
             timeout: float = TIMEOUT) -> bool:
    """校验令牌还有效吗（204 = 有效，403 = 已失效）"""
    url = f"{api_root}/authserver/validate"
    payload = {"accessToken": access_token}
    if client_token:
        payload["clientToken"] = client_token
    try:
        r = httplog.call("校验令牌", "POST", url, timeout,
                         headers={**HEADERS, "Content-Type": "application/json"},
                         json=payload)
    except requests.RequestException as e:
        raise YggdrasilError(f"校验请求失败：{type(e).__name__}") from e
    return r.status_code == 204


def refresh(api_root: str, access_token: str, client_token: str = "",
            timeout: float = TIMEOUT) -> dict:
    """刷新令牌（accessToken 过期但没作废时能续）"""
    url = f"{api_root}/authserver/refresh"
    payload = {"accessToken": access_token, "requestUser": True}
    if client_token:
        payload["clientToken"] = client_token
    try:
        r = httplog.call("刷新令牌", "POST", url, timeout,
                         headers={**HEADERS, "Content-Type": "application/json"},
                         json=payload)
    except requests.RequestException as e:
        raise YggdrasilError(f"刷新请求失败：{type(e).__name__}") from e
    if r.status_code not in (200, 201):
        raise YggdrasilError(_error_message(r), r.status_code)
    try:
        return _unwrap(r.json())
    except ValueError as e:
        raise YggdrasilError("刷新返回的不是 JSON") from e


# ============================================================
# 命令行自测：python -m core.verify.yggdrasil <站点> [角色名]
# ============================================================

def _main(argv):
    import sys
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    if len(argv) < 2:
        print(__doc__)
        print("用法: python -m core.verify.yggdrasil <站点地址> [角色名]")
        return 1

    site = argv[1]
    name = argv[2] if len(argv) > 2 else ""

    try:
        root, how = discover_api_root(site)
        print(f"API 根: {root}\n  （{how}）")
        meta = fetch_meta(root)
        print(f"  服务器名  : {meta['serverName']}")
        print(f"  实现      : {meta['implementationName']} {meta['implementationVersion']}")
        print(f"  skinDomains: {meta['skinDomains']}")
        print(f"  特性      : {meta['features']}")
    except YggdrasilError as e:
        print(f"识别失败：{e}")
        return 1

    if name:
        profiles = lookup_profiles(root, [name])
        print(f"\n查角色 {name!r}: {profiles if profiles else '这台服务器上没有这个角色'}")
        if profiles:
            profile = fetch_profile(root, profiles[0]["id"])
            print(f"  档案: id={profile.get('id')} name={profile.get('name')}")
            url = skin_url(profile, meta["skinDomains"])
            print(f"  皮肤: {url or '（没有皮肤 / 域名不在 skinDomains 里）'}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))
