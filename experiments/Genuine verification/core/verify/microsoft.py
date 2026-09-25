"""
微软登录（OAuth2 → Xbox Live → XSTS → Minecraft）

## 六步链路

    1. OAuth2 拿微软 access_token         （设备码 / 授权码两种走法，见下）
    2. Xbox Live 认证                      user.auth.xboxlive.com
    3. XSTS 授权                           xsts.auth.xboxlive.com
    4. Minecraft 登录                      api.minecraftservices.com/authentication/login_with_xbox
    5. 查是否拥有游戏                       .../entitlements/mcstore
    6. 查档案（名字 + UUID + 皮肤）          .../minecraft/profile

**每一步都可能失败，而失败原因完全不一样**，所以错误信息必须带上"卡在第几步"
（见 `MicrosoftAuthError.step`）。糊成一句"登录失败"等于没说。

## 关于"老版本那种复制链接"的登录方式

老办法是让浏览器最后跳到 `https://login.live.com/oauth20_desktop.srf?code=...`，
用户把整条 URL 复制回启动器。**这条路现在是废弃的**：
微软会把它重定向成 `oauth20_desktop.srf?removed=true`，code 拿不到。
（就算还能用也不该用 —— 下面两种都更好。）

现在的两条正路：

| 走法 | 用户体验 | 说明 |
|---|---|---|
| **设备码**（`request_device_code`） | 显示一串码，用户去 `microsoft.com/link` 输入 | 不用本地端口、不用复制长 URL。**PCL CE 用的就是这个** |
| 授权码 + 回环 | 自动开浏览器，授权完自动跳回来 | 最顺，但要本地起一个临时 HTTP 服务器接回调 |

这个模块先实现**设备码**那条 —— 它最稳（不受本地防火墙/端口占用影响），
而且和用户原来那套"开浏览器登录"的心理模型最接近。

## ⚠️ 两个硬门槛（不满足的话代码再对也没用）

**① 必须用 `consumers` 租户。** 带上 AAD 租户 ID 或者 `common` 会直接报错，
而且只能登**个人微软账号**（企业账号进不来）。

**② 新注册的 Azure 应用必须申请 Minecraft API 权限。**
没申请的话第 4 步会返回 **403 `Invalid app registration`**。
申请表：<https://aka.ms/mce-reviewappid>。
这是 Mojang 加的限制，**光在 Azure 门户点几下是不够的**。

## 客户端 ID 从哪来

**必须是自己的**。PCL CE 的 client id 编译在密钥里（`Secrets.MSOAuthClientId`），
公开仓库里没有 —— 用别人的等于冒用别人的应用，出问题也是别人被封。
所以这个模块**不内置任何 client id**，由调用方传（界面上填，存在配置里）。
"""
import time
import uuid as uuid_module
from dataclasses import dataclass, field

import requests

from core import logbook

#: 只能用 consumers —— 见模块开头第 ① 条
TENANT = "consumers"
#: 必须带 XboxLive.signin，否则第 2 步会以很难看懂的方式报错
SCOPE = "XboxLive.signin offline_access"

TIMEOUT = 20
HEADERS = {"User-Agent": "MosslightVerifyTest/0.1 (experiment)",
           "Accept": "application/json"}


@dataclass
class Endpoints:
    """所有端点都可替换 —— 测试时指向本地假服务器用

    ⚠️ 不做成模块常量而是做成对象：**不这样的话整条链路根本没法离线测**
    （没网/没 client_id/没买游戏，任何一条都会让测试变成"碰运气"）。
    """
    device_code: str = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/devicecode"
    token: str = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
    xbl: str = "https://user.auth.xboxlive.com/user/authenticate"
    xsts: str = "https://xsts.auth.xboxlive.com/xsts/authorize"
    mc_login: str = "https://api.minecraftservices.com/authentication/login_with_xbox"
    mc_entitlements: str = "https://api.minecraftservices.com/entitlements/mcstore"
    mc_profile: str = "https://api.minecraftservices.com/minecraft/profile"


DEFAULT_ENDPOINTS = Endpoints()


def _call(step: str, method: str, url: str, timeout: float, **kwargs):
    """走公共的带日志 HTTP 层（见 `core/httplog.py`）

    包一层是为了**不改各调用点的写法**，同时保证一条都不会漏记。
    """
    from core.httplog import call
    return call(step, method, url, timeout, **kwargs)


class MicrosoftAuthError(Exception):
    """带"卡在哪一步"的错误

    `step` 是给人看的步骤名，`code` 是服务端给的错误码（如果有）。
    界面上要能把这两样都显示出来。
    """

    def __init__(self, step: str, message: str, code: str = "", status: int = 0,
                 hint: str = ""):
        super().__init__(message)
        self.step = step
        self.message = message
        self.code = code
        self.status = status
        #: 额外提示（比如"去申请 Minecraft API 权限"）
        self.hint = hint

    def __str__(self):
        text = f"[{self.step}] {self.message}"
        if self.hint:
            text += f"\n{self.hint}"
        return text


class AuthorizationPending(Exception):
    """设备码还没被授权 —— **这不是错误**，是轮询的正常中间状态

    单独一个异常类型：混在 MicrosoftAuthError 里的话，调用方会把
    "用户还没输完码"当成"登录失败"直接报错（这是设备码流程最容易写错的地方）。
    """


# ============================================================
# 1. OAuth2：设备码
# ============================================================

def request_device_code(client_id: str, endpoints: Endpoints = DEFAULT_ENDPOINTS,
                        timeout: float = TIMEOUT) -> dict:
    """申请设备码，返回给用户看的东西

    返回 `{"user_code", "device_code", "verification_uri",
           "verification_uri_complete", "interval", "expires_in", "message"}`

    界面上要显示的是 `user_code`（比如 `K7Q9-XYZ`）和 `verification_uri`
    （`https://microsoft.com/link`）。`verification_uri_complete` 如果服务端给了，
    就是个已经把码带进去的链接，直接让用户点它最省事。
    """
    _require_client_id(client_id, "申请设备码")
    try:
        r = _call("OAuth2 设备码", "POST", endpoints.device_code, timeout=timeout,
                  headers=HEADERS,
                  data={"client_id": client_id, "scope": SCOPE})
    except requests.RequestException as e:
        raise MicrosoftAuthError("OAuth2 设备码", f"请求失败：{type(e).__name__}") from e

    if r.status_code != 200:
        raise _oauth_error("OAuth2 设备码", r)

    data = r.json()
    if not data.get("user_code") or not data.get("device_code"):
        raise MicrosoftAuthError("OAuth2 设备码", "服务端没返回 user_code / device_code")
    data.setdefault("interval", 5)
    data.setdefault("verification_uri", "https://microsoft.com/link")
    data.setdefault("expires_in", 900)
    return data


def poll_device_code(client_id: str, device_code: str,
                     endpoints: Endpoints = DEFAULT_ENDPOINTS,
                     timeout: float = TIMEOUT) -> dict:
    """轮询一次令牌端点

    · 用户还没输码 → 抛 `AuthorizationPending`（**不是失败**）
    · 输完了       → 返回 `{"access_token", "refresh_token", "expires_in", ...}`
    · 真出错       → 抛 `MicrosoftAuthError`

    调用方按 `interval` 秒的节奏反复调这个，直到拿到令牌或者设备码过期。
    """
    _require_client_id(client_id, "轮询令牌")
    try:
        r = _call("OAuth2 轮询", "POST", endpoints.token, timeout=timeout,
                  headers=HEADERS, data={
                      "client_id": client_id,
                      "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                      "device_code": device_code,
                  })
    except requests.RequestException as e:
        raise MicrosoftAuthError("OAuth2 轮询", f"请求失败：{type(e).__name__}") from e

    if r.status_code == 200:
        data = r.json()
        if not data.get("access_token"):
            raise MicrosoftAuthError("OAuth2 轮询", "服务端没返回 access_token")
        return data

    try:
        err = r.json()
    except ValueError:
        err = {}
    code = str(err.get("error") or "")
    if code in ("authorization_pending", "slow_down"):
        raise AuthorizationPending(code)
    if code == "expired_token":
        raise MicrosoftAuthError("OAuth2 轮询", "设备码过期了，重新登录一次")
    if code == "authorization_declined":
        raise MicrosoftAuthError("OAuth2 轮询", "你在浏览器里拒绝了这次授权")
    raise _oauth_error("OAuth2 轮询", r)


def refresh_oauth(client_id: str, refresh_token: str,
                  endpoints: Endpoints = DEFAULT_ENDPOINTS,
                  timeout: float = TIMEOUT) -> dict:
    """用 refresh_token 换新的 access_token

    ⚠️ 微软的 refresh_token 是**会滚动**的：换一次给一个新的，
    旧的作废。所以拿到之后必须**立刻存回去**，不然下次就用不了了。
    """
    _require_client_id(client_id, "刷新令牌")
    try:
        r = _call("OAuth2 刷新", "POST", endpoints.token, timeout=timeout,
                  headers=HEADERS, data={
                      "client_id": client_id,
                      "grant_type": "refresh_token",
                      "refresh_token": refresh_token,
                      "scope": SCOPE,
                  })
    except requests.RequestException as e:
        raise MicrosoftAuthError("OAuth2 刷新", f"请求失败：{type(e).__name__}") from e
    if r.status_code != 200:
        raise _oauth_error("OAuth2 刷新", r)

    data = r.json()
    # 服务端没给新的就沿用旧的（规范允许，但微软一般会给）
    data.setdefault("refresh_token", refresh_token)
    return data


def _require_client_id(client_id: str, step: str):
    if not (client_id or "").strip():
        raise MicrosoftAuthError(
            step, "还没填 Azure 应用的客户端 ID",
            hint="去 Azure 门户注册一个应用，把「应用程序(客户端) ID」填进设置。\n"
                 "详见 README 的「微软登录怎么开通」。")


def _oauth_error(step: str, response) -> MicrosoftAuthError:
    """把 OAuth 的错误响应变成人话

    微软的错误体长这样：`{"error": "invalid_client", "error_description": "..."}`
    """
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = str(body.get("error") or "")
    desc = str(body.get("error_description") or "").strip()
    if not desc:
        desc = f"HTTP {response.status_code}"

    hint = ""
    # ⚠️ **先看 AADSTS 码，再看 OAuth 的 error**。
    # 因为一个 OAuth 错误码会对应好几种完全不同的真实原因：
    # 实测"ID 根本不存在"返回的是 `unauthorized_client` + AADSTS700038，
    # 而"应用没开公共客户端流"也是 `unauthorized_client`。
    # 只看 error 的话会把"抄错了 ID"说成"去开公共客户端流"，白折腾一轮。
    lowered = desc.lower()
    if "aadsts700038" in lowered or "not a valid application identifier" in lowered:
        hint = ("这个客户端 ID **在微软那边不存在** —— 大概率是抄错了，"
                "或者用了另一个租户的应用。\n"
                "去 Azure 门户 → 应用注册 → 概览，把「应用程序(客户端) ID」整段复制过来。")
    elif "aadsts700016" in lowered or "not found in the directory" in lowered:
        hint = "这个应用在你当前的目录里找不到。确认账号选的是个人账户那个应用。"
    elif "aadsts7000218" in lowered or "client_secret" in lowered:
        hint = "这个应用被当成机密客户端了。Azure → 身份验证 → 打开「允许公共客户端流」。"
    elif code == "invalid_client":
        hint = ("客户端 ID 不对，或者这个应用没开「允许公共客户端流」。\n"
                "Azure 门户 → 应用注册 → 身份验证 → 最下面「允许公共客户端流」打开。")
    elif code == "unauthorized_client":
        hint = ("这个应用没被允许用设备码流程。确认它是「移动和桌面应用程序」类型，"
                "并且开了「允许公共客户端流」。")
    return MicrosoftAuthError(step, f"{code or 'HTTP ' + str(response.status_code)}：{desc}",
                              code=code, status=response.status_code, hint=hint)


# ============================================================
# 2~3. Xbox Live → XSTS
# ============================================================

#: XSTS 的 XErr 码 → 人话。照 Minecraft Wiki 那张表抄的。
#: **这张表很值钱**：不查表的话用户只会看到 "401" 和一个数字，
#: 而实际上每条都对应一个明确的自救动作。
XERR_MESSAGES = {
    2148916227: ("这个账号被 Xbox 封禁了。", ""),
    2148916233: ("这个微软账号**还没有 Xbox 档案**。",
                 "先去 minecraft.net 登录一次（会顺带建好 Xbox 档案），再回来试。"),
    2148916235: ("账号所在的国家/地区用不了 Xbox Live。", ""),
    2148916236: ("账号需要在 Xbox 页面完成成年人验证（韩国）。", ""),
    2148916237: ("账号需要在 Xbox 页面完成成年人验证（韩国）。", ""),
    2148916238: ("这是个**未成年账号**，必须由家长加进家庭组才能登录。",
                 "用家长账号在 microsoft.com/family 里把它加进来。"),
    2148916262: ("Xbox 返回了一个不常见的错误。", "稍后再试，或者换个网络环境。"),
}


def xbox_authenticate(ms_access_token: str, endpoints: Endpoints = DEFAULT_ENDPOINTS,
                      timeout: float = TIMEOUT) -> tuple:
    """第 2 步：拿 Xbox Live 令牌。返回 `(xbl_token, user_hash)`

    ⚠️ `RpsTicket` 前面那个 `d=` **不能少**，少了会报一个看不懂的错。
    """
    body = {
        "Properties": {
            "AuthMethod": "RPS",
            "SiteName": "user.auth.xboxlive.com",
            "RpsTicket": f"d={ms_access_token}",
        },
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    }
    try:
        r = _call("Xbox Live", "POST", endpoints.xbl, timeout=timeout,
                  headers={**HEADERS, "Content-Type": "application/json"},
                  json=body)
    except requests.RequestException as e:
        raise MicrosoftAuthError("Xbox Live", f"请求失败：{type(e).__name__}") from e
    if r.status_code != 200:
        raise MicrosoftAuthError("Xbox Live", _plain_error(r), status=r.status_code)

    data = r.json()
    token = data.get("Token")
    xui = ((data.get("DisplayClaims") or {}).get("xui") or [{}])[0]
    user_hash = xui.get("uhs")
    if not token or not user_hash:
        raise MicrosoftAuthError("Xbox Live", "响应里没有 Token / userhash")
    return token, user_hash


def xsts_authorize(xbl_token: str, endpoints: Endpoints = DEFAULT_ENDPOINTS,
                   timeout: float = TIMEOUT) -> tuple:
    """第 3 步：换 XSTS 令牌。返回 `(xsts_token, user_hash)`

    ⚠️ `RelyingParty` 必须是 `rp://api.minecraftservices.com/` ——
    这是给 Minecraft 用的那个沙箱，写错了后面第 4 步会拒。
    """
    body = {
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
        "RelyingParty": "rp://api.minecraftservices.com/",
        "TokenType": "JWT",
    }
    try:
        r = _call("XSTS", "POST", endpoints.xsts, timeout=timeout,
                  headers={**HEADERS, "Content-Type": "application/json"},
                  json=body)
    except requests.RequestException as e:
        raise MicrosoftAuthError("XSTS", f"请求失败：{type(e).__name__}") from e

    if r.status_code != 200:
        # 401 的时候带 XErr 码 —— 这才是真正有用的信息
        try:
            body_json = r.json()
        except ValueError:
            body_json = {}
        xerr = body_json.get("XErr")
        if xerr is not None:
            try:
                xerr = int(xerr)
            except (TypeError, ValueError):
                xerr = None
        if xerr is not None and xerr in XERR_MESSAGES:
            message, hint = XERR_MESSAGES[xerr]
            raise MicrosoftAuthError("XSTS", message,
                                     code=f"XErr {xerr}", status=r.status_code,
                                     hint=hint)
        if xerr is not None:
            raise MicrosoftAuthError("XSTS", f"Xbox 返回了未知的 XErr {xerr}",
                                     code=f"XErr {xerr}", status=r.status_code)
        raise MicrosoftAuthError("XSTS", _plain_error(r), status=r.status_code)

    data = r.json()
    token = data.get("Token")
    xui = ((data.get("DisplayClaims") or {}).get("xui") or [{}])[0]
    user_hash = xui.get("uhs")
    if not token or not user_hash:
        raise MicrosoftAuthError("XSTS", "响应里没有 Token / userhash")
    return token, user_hash


# ============================================================
# 4~6. Minecraft
# ============================================================

def minecraft_login(xsts_token: str, user_hash: str,
                    endpoints: Endpoints = DEFAULT_ENDPOINTS,
                    timeout: float = TIMEOUT) -> dict:
    """第 4 步：登录 Minecraft，拿 MC 的 access_token

    ⚠️ **这一步是"新注册的 Azure 应用"最容易卡住的地方**，会返回 403：
        {"error": "INVALID_APP_REGISTRATION", "errorMessage": "Invalid app registration"}
    原因是应用没有 Minecraft API 的权限，要另外申请（见模块开头第 ② 条）。
    这里特意把它单独识别出来 —— 否则用户会以为是账号问题。
    """
    body = {"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"}
    try:
        r = _call("Minecraft 登录", "POST", endpoints.mc_login, timeout=timeout,
                  headers={**HEADERS, "Content-Type": "application/json"},
                  json=body)
    except requests.RequestException as e:
        raise MicrosoftAuthError("Minecraft 登录", f"请求失败：{type(e).__name__}") from e

    if r.status_code == 403:
        text = r.text.lower()
        if "app registration" in text or "invalid_app" in text:
            raise MicrosoftAuthError(
                "Minecraft 登录",
                "这个 Azure 应用**没有 Minecraft API 的权限**（403 Invalid app registration）。",
                code="INVALID_APP_REGISTRATION", status=403,
                hint="去 https://aka.ms/mce-reviewappid 填表申请，通过之后才能用。\n"
                     "注意：光在 Azure 门户里配权限是不够的，必须提交这张表。")
        raise MicrosoftAuthError("Minecraft 登录", _plain_error(r), status=403)

    if r.status_code != 200:
        raise MicrosoftAuthError("Minecraft 登录", _plain_error(r), status=r.status_code)

    data = r.json()
    if not data.get("access_token"):
        raise MicrosoftAuthError("Minecraft 登录", "响应里没有 access_token")
    return data


def check_entitlements(mc_access_token: str,
                       endpoints: Endpoints = DEFAULT_ENDPOINTS,
                       timeout: float = TIMEOUT) -> bool:
    """第 5 步：这个账号**有没有买过游戏**

    ⚠️ 前面四步**任何普通微软账号都能走通** —— 没买游戏的账号一样能拿到
    MC access_token。所以"能不能登录"和"有没有正版"是两件事，
    必须查这一步才算数。
    """
    try:
        r = _call("查游戏所有权", "GET", endpoints.mc_entitlements,
                  timeout=timeout,
                  headers={**HEADERS, "Authorization": f"Bearer {mc_access_token}"})
    except requests.RequestException as e:
        raise MicrosoftAuthError("查游戏所有权", f"请求失败：{type(e).__name__}") from e
    if r.status_code != 200:
        raise MicrosoftAuthError("查游戏所有权", _plain_error(r), status=r.status_code)
    items = (r.json() or {}).get("items") or []
    return any(str(i.get("name", "")).startswith(("product_minecraft", "game_minecraft"))
               for i in items if isinstance(i, dict))


def fetch_profile(mc_access_token: str, endpoints: Endpoints = DEFAULT_ENDPOINTS,
                  timeout: float = TIMEOUT):
    """第 6 步：拿名字 / UUID / 皮肤。没档案返回 None

    皮肤地址在 `skins[0]["url"]`（`textures.minecraft.net`），
    可以照 ALI 那条路一样下载下来抠脸当前头像。
    """
    try:
        r = _call("查档案", "GET", endpoints.mc_profile, timeout=timeout,
                  headers={**HEADERS, "Authorization": f"Bearer {mc_access_token}"})
    except requests.RequestException as e:
        raise MicrosoftAuthError("查档案", f"请求失败：{type(e).__name__}") from e
    if r.status_code == 404:
        return None                      # 没档案（比如 Game Pass 用户还没进过游戏）
    if r.status_code != 200:
        raise MicrosoftAuthError("查档案", _plain_error(r), status=r.status_code)
    return r.json()


def skin_url_from_profile(profile: dict) -> str:
    """档案 → 皮肤地址（没有就空串）

    ⚠️ 微软这边**没有 skinDomains 那套校验**，皮肤固定来自
    `textures.minecraft.net`。所以这里只挑一下域名，防止档案被塞了别的地址。
    """
    from urllib.parse import urlparse
    for skin in (profile or {}).get("skins") or []:
        if not isinstance(skin, dict) or skin.get("state") != "ACTIVE":
            continue
        url = str(skin.get("url") or "").strip()
        host = (urlparse(url).hostname or "").lower()
        if url and (host == "textures.minecraft.net" or host.endswith(".minecraft.net")):
            return url
    return ""


# ============================================================
# 一整条：设备码 → 到档案
# ============================================================

def complete_login(client_id: str, oauth: dict,
                   endpoints: Endpoints = DEFAULT_ENDPOINTS,
                   timeout: float = TIMEOUT) -> dict:
    """拿到 OAuth 令牌之后，走完 2~6 步

    返回一个"账户记录"（`core/accounts.py` 认识的结构）。
    没买游戏的话抛 `MicrosoftAuthError`（step = "查游戏所有权"）。
    """
    xbl, user_hash = xbox_authenticate(oauth["access_token"], endpoints, timeout)
    xsts, user_hash = xsts_authorize(xbl, endpoints, timeout)
    minecraft = minecraft_login(xsts, user_hash, endpoints, timeout)

    mc_token = minecraft["access_token"]
    if not check_entitlements(mc_token, endpoints, timeout):
        raise MicrosoftAuthError(
            "查游戏所有权",
            "这个微软账号**没有购买 Minecraft: Java Edition**。",
            hint="前面几步都成功了，但它们对任何微软账号都会成功 —— "
                 "只有这一步才真正检查有没有正版。")

    profile = fetch_profile(mc_token, endpoints, timeout)
    if not profile:
        raise MicrosoftAuthError("查档案", "这个账号还没有 Minecraft 档案",
                                 hint="先用官方启动器登录一次建好档案。")

    expires_in = int(minecraft.get("expires_in") or 0)
    return {
        "type": "microsoft",
        "name": str(profile.get("name") or ""),
        "uuid": str(profile.get("id") or ""),
        "access_token": mc_token,
        "refresh_token": oauth.get("refresh_token") or "",
        #: 存的是**绝对时间戳**，不是"还有多少秒" ——
        #: 存相对时间的话，程序重启之后就没法判断过没过期了
        "expires_at": time.time() + expires_in if expires_in else 0,
        "client_id": client_id,
        "skin_url": skin_url_from_profile(profile),
        "added_at": time.time(),
    }


def _plain_error(response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict):
        for key in ("errorMessage", "error_description", "Message", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return f"{value.strip()}（HTTP {response.status_code}）"
    return f"HTTP {response.status_code}"


def new_client_token() -> str:
    return str(uuid_module.uuid4())
