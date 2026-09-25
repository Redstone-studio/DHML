"""
微软登录整条链的离线自测

    python -m core.verify.selftest_ms

## 为什么必须有个假服务器

真链路上要凑齐"能登的微软账号 + 买过游戏 + 有 Minecraft API 权限的 Azure 应用"
才能跑一次，任何一样缺了都会让测试变成碰运气。而且真链路上
**"用户还没输码"和"真的失败了"是两种完全不同的状态**，
不控节奏根本测不到。

所以这里按规范起一个假服务器，把微软 + Xbox Live + XSTS + Minecraft
四家的端点都实现了，然后：

  · 让轮询先返回两次 authorization_pending / slow_down，再成功
    （验"还没输码不能当失败"）
  · 让 XSTS 返回一个 XErr 码（验那张错误码表）
  · 让 mc_login 返回 403 Invalid app registration（验最容易卡住的那一步）
  · 让 entitlements 为空（验"能登录 ≠ 有正版"）
  · 验 refresh_token 轮换之后旧的不会被抹掉
"""
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.verify import microsoft as ms

#: 假服务器认的 client_id
GOOD_CLIENT_ID = "11111111-2222-3333-4444-555555555555"
WRONG_CLIENT_ID = "00000000-0000-0000-0000-000000000000"

#: 假账号
PLAYER_NAME = "TestSteve"
PLAYER_UUID = "069a79f444e94726a5befca90e38aaf5"
SKIN_URL = "https://textures.minecraft.net/texture/deadbeef"


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeMS/1.0"

    def _send(self, status, body=None):
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def _form(self):
        """OAuth 的请求体是 form-urlencoded，不是 json"""
        from urllib.parse import parse_qs
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def log_message(self, *args):
        pass

    # ---------- 路由 ----------

    def do_POST(self):
        path = self.path.split("?")[0]
        srv = self.server

        if path == "/devicecode":
            form = self._form()
            if form.get("client_id") != GOOD_CLIENT_ID:
                return self._send(400, {"error": "invalid_client",
                                        "error_description": "bad client id"})
            if "XboxLive.signin" not in (form.get("scope") or ""):
                # 不带这个 scope 的话真微软也会以很难懂的方式报错
                return self._send(400, {"error": "invalid_scope",
                                        "error_description": "missing XboxLive.signin"})
            srv.polls = 0
            return self._send(200, {
                "user_code": "K7Q9-XYZ",
                "device_code": "fake-device-code",
                "verification_uri": "https://microsoft.com/link",
                "verification_uri_complete": "https://microsoft.com/link?otp=K7Q9XYZ",
                "interval": 1, "expires_in": 900,
                "message": "去 microsoft.com/link 输入 K7Q9-XYZ"})

        if path == "/token":
            form = self._form()
            if form.get("client_id") != GOOD_CLIENT_ID:
                return self._send(400, {"error": "invalid_client",
                                        "error_description": "bad client id"})
            if form.get("grant_type") == "refresh_token":
                # ⚠️ 真的实现**轮换**：认了旧的之后，旧的就作废、只认新的。
                # 假服务器如果不轮换，"旧的应该失效"这条就永远测不到
                # （第一版就是这样，测试自己骗自己）。
                if form.get("refresh_token") != srv.valid_refresh:
                    return self._send(400, {"error": "invalid_grant",
                                            "error_description": "bad refresh token"})
                srv.valid_refresh = "ms-refresh-2"
                return self._send(200, {"access_token": "ms-access-2",
                                        "refresh_token": "ms-refresh-2",
                                        "expires_in": 3600, "token_type": "Bearer"})
            # 设备码轮询：先 pending、再 slow_down、第三次才成功
            srv.polls += 1
            if srv.polls <= 1:
                return self._send(400, {"error": "authorization_pending"})
            if srv.polls == 2:
                return self._send(400, {"error": "slow_down"})
            return self._send(200, {"access_token": "ms-access", "refresh_token": "ms-refresh",
                                    "expires_in": 3600, "token_type": "Bearer"})

        if path == "/xbl":
            body = self._body()
            ticket = str((body.get("Properties") or {}).get("RpsTicket") or "")
            # ⚠️ `d=` 前缀不能少 —— 少了就按真服务端那样拒掉
            if not ticket.startswith("d="):
                return self._send(400, {"Message": "RpsTicket must be prefixed with d="})
            return self._send(200, {"Token": "xbl-token",
                                    "DisplayClaims": {"xui": [{"uhs": "user-hash"}]}})

        if path == "/xsts":
            body = self._body()
            rp = str(body.get("RelyingParty") or "")
            if rp != "rp://api.minecraftservices.com/":
                return self._send(400, {"Message": "unexpected RelyingParty"})
            tokens = (body.get("Properties") or {}).get("UserTokens") or []
            if tokens and tokens[0] == "child-token":
                # 未成年账号：真服务端会 401 + XErr 码
                return self._send(401, {"Identity": "0", "XErr": 2148916238,
                                        "Message": "",
                                        "Redirect": "https://start.ui.xboxlive.com/AddChildToFamily"})
            return self._send(200, {"Token": "xsts-token",
                                    "DisplayClaims": {"xui": [{"uhs": "user-hash"}]}})

        if path == "/mc_login":
            body = self._body()
            if not str(body.get("identityToken") or "").startswith("XBL3.0 x="):
                return self._send(400, {"error": "INVALID_IDENTITY_TOKEN"})
            if srv.reject_app_registration:
                return self._send(403, {"path": "/authentication/login_with_xbox",
                                        "error": "INVALID_APP_REGISTRATION",
                                        "errorMessage": "Invalid app registration"})
            return self._send(200, {"username": PLAYER_UUID, "roles": [],
                                    "access_token": "mc-access", "token_type": "Bearer",
                                    "expires_in": 86400})

        return self._send(404, {"error": "not found"})

    def do_GET(self):
        path = self.path.split("?")[0]
        srv = self.server
        auth = self.headers.get("Authorization") or ""
        if path in ("/entitlements", "/profile") and not auth.startswith("Bearer "):
            return self._send(401, {"error": "UNAUTHORIZED"})

        if path == "/entitlements":
            items = [] if srv.no_game else [{"name": "product_minecraft"},
                                            {"name": "game_minecraft"}]
            return self._send(200, {"items": items, "signature": "jwt", "keyId": "1"})

        if path == "/profile":
            if srv.no_profile:
                return self._send(404, {"path": "/minecraft/profile", "error": "NOT_FOUND"})
            return self._send(200, {
                "id": PLAYER_UUID, "name": PLAYER_NAME,
                "skins": [{"id": "s1", "state": "ACTIVE", "url": SKIN_URL,
                           "variant": "CLASSIC", "alias": "STEVE"}],
                "capes": []})

        return self._send(404, {"error": "not found"})


class FakeMicrosoft:
    """假微软 + Xbox + Minecraft（上下文管理器）"""

    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        base = f"http://127.0.0.1:{self.port}"
        self.httpd.polls = 0
        self.httpd.reject_app_registration = False
        self.httpd.no_game = False
        self.httpd.no_profile = False
        self.httpd.valid_refresh = "ms-refresh"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

        # 全部端点指向本地
        self.endpoints = ms.Endpoints(
            device_code=f"{base}/devicecode", token=f"{base}/token",
            xbl=f"{base}/xbl", xsts=f"{base}/xsts",
            mc_login=f"{base}/mc_login", mc_entitlements=f"{base}/entitlements",
            mc_profile=f"{base}/profile")

    @property
    def reject_app_registration(self):
        return self.httpd.reject_app_registration

    @reject_app_registration.setter
    def reject_app_registration(self, value):
        self.httpd.reject_app_registration = bool(value)

    @property
    def no_game(self):
        return self.httpd.no_game

    @no_game.setter
    def no_game(self, value):
        self.httpd.no_game = bool(value)

    @property
    def no_profile(self):
        return self.httpd.no_profile

    @no_profile.setter
    def no_profile(self, value):
        self.httpd.no_profile = bool(value)

    def close(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass

    fails = []

    def check(name, got, want):
        ok = got == want
        print(f"  {'OK ' if ok else 'X  '} {name}: {got!r}"
              + ("" if ok else f"   期望 {want!r}"))
        if not ok:
            fails.append(name)

    with FakeMicrosoft() as srv:
        ep = srv.endpoints

        # ---------- 1. 设备码 ----------
        print("1. 设备码")
        device = ms.request_device_code(GOOD_CLIENT_ID, ep, timeout=5)
        check("   拿到 user_code", device["user_code"], "K7Q9-XYZ")
        check("   给了网址", bool(device["verification_uri"]), True)
        check("   轮询间隔", device["interval"], 1)

        try:
            ms.request_device_code(WRONG_CLIENT_ID, ep, timeout=5)
            check("   错的 client_id 要报错", "没报", "报了")
        except ms.MicrosoftAuthError as e:
            check("   错的 client_id 报 invalid_client", e.code, "invalid_client")
            check("   且给出「开公共客户端流」的提示",
                  "公共客户端流" in e.hint, True)

        # ---------- 2. 轮询：pending 不是失败 ----------
        print("\n2. 轮询（先 pending / slow_down，再成功）")
        pending_count = 0
        token = None
        for _ in range(10):
            try:
                token = ms.poll_device_code(GOOD_CLIENT_ID, device["device_code"],
                                            ep, timeout=5)
                break
            except ms.AuthorizationPending:
                pending_count += 1
        check("   遇到两次「还没授权」", pending_count, 2)
        check("   ⚠️ 没把 pending 当失败", token is not None, True)
        check("   拿到微软 access_token", token["access_token"], "ms-access")
        check("   拿到 refresh_token", token["refresh_token"], "ms-refresh")

        # ---------- 3. 整条链 ----------
        print("\n3. 走完 2~6 步")
        account = ms.complete_login(GOOD_CLIENT_ID, token, ep, timeout=5)
        check("   类型", account["type"], "microsoft")
        check("   游戏名", account["name"], PLAYER_NAME)
        check("   UUID", account["uuid"], PLAYER_UUID)
        check("   MC access_token", account["access_token"], "mc-access")
        check("   皮肤地址", account["skin_url"], SKIN_URL)
        check("   expires_at 是绝对时间戳", account["expires_at"] > 1_700_000_000, True)

        # ---------- 4. 最容易卡住的那一步 ----------
        print("\n4. 403 Invalid app registration 要单独认出来")
        srv.reject_app_registration = True
        try:
            ms.complete_login(GOOD_CLIENT_ID, token, ep, timeout=5)
            check("   要抛错", "没抛", "抛了")
        except ms.MicrosoftAuthError as e:
            check("   卡在第 4 步", e.step, "Minecraft 登录")
            check("   识别成没权限", e.code, "INVALID_APP_REGISTRATION")
            check("   提示里有申请表链接", "mce-reviewappid" in e.hint, True)

        # ⚠️ 但**验证器**要把它当"判不了"，不是"失败" ——
        # 前三步（微软登录 + Xbox Live + XSTS）是真过了的。
        # 报成 error 会让人以为账号或代码有问题，而实际卡的是应用审批。
        from core.verify.microsoft_verifier import MicrosoftVerifier
        verifier = MicrosoftVerifier(ep)          # ⚠️ 必须注入假端点，否则会打真微软
        account2, result2 = verifier.poll_once(GOOD_CLIENT_ID, "fake-device-code",
                                               timeout=5)
        check("   ⚠️ 验证器判「无法判定」而不是失败", result2.state, "inconclusive")
        check("   不返回账户（没登进去）", account2, None)
        check("   说清是应用审批问题", "应用审批" in result2.message, True)
        check("   说清账号本身没问题", "登录成功了" in result2.message, True)
        srv.reject_app_registration = False

        # ---------- 5. XErr 表 ----------
        print("\n5. XSTS 的 XErr 码要能翻译成人话")
        try:
            ms.xsts_authorize("child-token", ep, timeout=5)
            check("   要抛错", "没抛", "抛了")
        except ms.MicrosoftAuthError as e:
            check("   卡在 XSTS", e.step, "XSTS")
            check("   认出是未成年账号", "未成年" in e.message, True)
            check("   给出家庭组提示", "family" in e.hint, True)
            check("   带上了错误码", e.code, "XErr 2148916238")

        # ---------- 6. 能登录 ≠ 有正版 ----------
        print("\n6. 没买游戏的账号：前面都能过，第 5 步必须拦住")
        srv.no_game = True
        try:
            ms.complete_login(GOOD_CLIENT_ID, token, ep, timeout=5)
            check("   要抛错", "没抛", "抛了")
        except ms.MicrosoftAuthError as e:
            check("   卡在查所有权", e.step, "查游戏所有权")
            check("   说明白原因", "没有购买" in e.message, True)
        srv.no_game = False

        print("\n7. 没档案")
        srv.no_profile = True
        try:
            ms.complete_login(GOOD_CLIENT_ID, token, ep, timeout=5)
            check("   要抛错", "没抛", "抛了")
        except ms.MicrosoftAuthError as e:
            check("   卡在查档案", e.step, "查档案")
        srv.no_profile = False

        # ---------- 8. refresh_token 轮换 ----------
        print("\n8. refresh_token 是会轮换的")
        refreshed = ms.refresh_oauth(GOOD_CLIENT_ID, "ms-refresh", ep, timeout=5)
        check("   换到新 access_token", refreshed["access_token"], "ms-access-2")
        check("   ⚠️ 服务端给了新的 refresh_token", refreshed["refresh_token"],
              "ms-refresh-2")
        try:
            ms.refresh_oauth(GOOD_CLIENT_ID, "ms-refresh", ep, timeout=5)
            check("   旧的应该作废", "还能用", "不能用了")
        except ms.MicrosoftAuthError as e:
            check("   旧的确实作废了", e.code, "invalid_grant")

        # ---------- 9. 两个容易写错的地方 ----------
        print("\n9. 两个容易写错的地方")
        # 假服务器的 /xbl 会拒掉没有 `d=` 前缀的 RpsTicket。
        # `xbox_authenticate` 内部负责加前缀，所以**能成功就说明加对了** ——
        # 这里不能"自己传一个没前缀的串进去"来测，那个前缀是函数加的，传不进去。
        try:
            ms.xbox_authenticate("any-ms-token", ep, timeout=5)
            check("   RpsTicket 自动加了 d= 前缀", True, True)
        except ms.MicrosoftAuthError as e:
            check("   RpsTicket 自动加了 d= 前缀", f"没加：{e.message}", True)
        check("   RelyingParty 不对会被拒", _xsts_with_bad_rp(ep), "被拒了")

        # ---------- 10. 皮肤地址白名单 ----------
        print("\n10. 皮肤地址只认 minecraft.net")
        check("   正常档案取到",
              ms.skin_url_from_profile({"skins": [{"state": "ACTIVE", "url": SKIN_URL}]}),
              SKIN_URL)
        check("   非 ACTIVE 不要",
              ms.skin_url_from_profile({"skins": [{"state": "INACTIVE", "url": SKIN_URL}]}), "")
        check("   ⚠️ 别的域名一律拒",
              ms.skin_url_from_profile({"skins": [{"state": "ACTIVE",
                                                   "url": "https://evil.example/x.png"}]}), "")

    # ---------- 11. 账户存储 ----------
    print("\n11. 本地账户存储")
    import tempfile, pathlib
    from core import accounts as acc

    tmp = pathlib.Path(tempfile.mkdtemp())
    acc.get_config_dir = lambda: tmp          # 别污染真配置目录
    acc.accounts_path = lambda: tmp / acc.FILE_NAME

    check("   一开始是空的", acc.all_accounts(), [])
    check("   没有当前账户", acc.get_current(), None)

    record = {"type": "microsoft", "name": PLAYER_NAME, "uuid": PLAYER_UUID,
              "access_token": "mc-access", "refresh_token": "ms-refresh",
              "expires_at": 0, "skin_url": SKIN_URL}
    check("   加一个", acc.upsert(record), True)
    check("   读得回来", acc.get_current()["name"], PLAYER_NAME)

    # 模拟"刷新回来的响应没带 refresh_token"
    acc.upsert({"uuid": PLAYER_UUID, "name": PLAYER_NAME,
                "access_token": "mc-access-2", "expires_at": 0})
    current = acc.get_current()
    check("   覆盖时更新了 access_token", current["access_token"], "mc-access-2")
    check("   ⚠️ 但旧的 refresh_token 保住了", current["refresh_token"], "ms-refresh")

    check("   按名字也能找到", acc.find(PLAYER_NAME)["uuid"], PLAYER_UUID)
    check("   过期判断（0 = 不过期）", acc.is_expired(current), False)
    acc.upsert({"uuid": PLAYER_UUID, "name": PLAYER_NAME,
                "expires_at": __import__("time").time() - 10})
    check("   真过期了要认", acc.is_expired(acc.get_current()), True)

    check("   删掉", acc.remove(PLAYER_UUID), True)
    check("   删完就空了", acc.all_accounts(), [])

    print(f"\n>>> {'全部通过' if not fails else '失败: ' + ', '.join(fails)}")
    return 1 if fails else 0


def _xsts_with_bad_rp(ep) -> str:
    """RelyingParty 写错的场景（假服务器会拒）"""
    import requests
    try:
        r = requests.post(ep.xsts, json={"Properties": {"SandboxId": "RETAIL",
                                                        "UserTokens": ["x"]},
                                         "RelyingParty": "rp://wrong/",
                                         "TokenType": "JWT"}, timeout=5)
        return "过了" if r.status_code == 200 else "被拒了"
    except Exception:
        return "被拒了"


if __name__ == "__main__":
    raise SystemExit(main())
