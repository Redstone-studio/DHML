"""
ALI（authlib-injector）协议离线自测

**为什么不用真站点测**：真站点上"查到了"那条路需要一个确实存在的角色，
而且皮肤、响应头、错误形状都会随站点变。这里起一个**按规范实现**的假 ALI 服务器，
把所有分支（找到/找不到/密码错/令牌失效/皮肤）都覆盖掉，断言才是确定的。

    python -m core.verify.selftest_ali

⚠️ 这个假服务器**刻意模仿了 demo.lunch.ink 的两处不标准**（信封 + error 是对象），
不然测出来的是"规范实现能跑"，而不是"这台站能跑"。
"""
import base64
import json
import socket
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.verify import yggdrasil

#: 假服务器上的测试账号
TEST_USER = "TestUser"
TEST_PASS = "correct-horse"
TEST_UUID = "069a79f444e94726a5befca90e38aaf5"

#: 假皮肤：64x64，脸那一块 (8,8) 涂成这个颜色 —— 用来验"头像真的从皮肤抠出来了"
FACE_RGBA = (0x5E, 0xC2, 0x69, 0xFF)     # 强调绿


def make_skin_png() -> bytes:
    """造一张 64x64 的合法皮肤

    ⚠️ 用 Qt 而不是手写 PNG：皮肤格式要的尺寸/通道对不上就白测了。
    但**不能 import PyQt6 到这个模块的顶层** —— 协议自测不该依赖 Qt。
    所以延迟到真正要造图的时候再 import。
    """
    from PyQt6.QtCore import QBuffer, QByteArray, Qt
    from PyQt6.QtGui import QColor, QImage

    image = QImage(64, 64, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    # 脸 (8,8)-(15,15)
    for y in range(8, 16):
        for x in range(8, 16):
            image.setPixelColor(x, y, QColor(*FACE_RGBA))
    # 帽子层 (40,8) —— 全透明，这样头像应该就是脸的颜色，
    # 不会被帽子层盖掉（帽子层是要叠上去的，见 ui/avatar.py）
    storage = QByteArray()
    buf = QBuffer(storage)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    buf.close()
    return bytes(storage)


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeALI/1.0"

    # ---------- 工具 ----------

    def _send(self, status: int, body=None, headers=None, raw: bytes = None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if raw is not None:
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _envelope(self, data):
        """这家实现的信封（见 yggdrasil.py 模块开头）"""
        return {"success": True, "data": data, "error": None, "traceId": "fake-trace"}

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return None

    def log_message(self, *args):
        pass                                  # 别把测试输出刷满

    # ---------- 元数据 ----------

    def _meta_payload(self):
        meta = {
            "serverName": "假验证站",
            "implementationName": "fake-mc-yggdrasil",
            "implementationVersion": "0.1",
            "links": {"homepage": self.server.base_url},
            "feature.non_email_login": True,
        }
        # ⚠️ 和 demo.lunch.ink 一样：标准字段**顶层也放一份**
        return {
            "meta": meta,
            "skinDomains": ["127.0.0.1"],
            "signaturePublickey": "-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----",
            "success": True,
            "data": {"meta": meta, "skinDomains": ["127.0.0.1"],
                     "signaturePublickey": "-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----"},
            "error": None,
            "traceId": "fake-trace",
        }

    def _profile_payload(self):
        textures = {"textures": {"SKIN": {
            "url": f"{self.server.base_url}/api/yggdrasil/textures/skin1"}}}
        value = base64.b64encode(json.dumps(textures).encode()).decode()
        return {"id": TEST_UUID, "name": TEST_USER,
                "properties": [{"name": "textures", "value": value}]}

    # ---------- 路由 ----------

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/":
            # 自动识别的那个响应头，**相对路径**（和真站点一样）
            return self._send(200, headers={"X-Authlib-Injector-API-Location":
                                            "/api/yggdrasil/"})
        if path == "/api/yggdrasil":
            return self._send(200, self._meta_payload())
        prefix = "/api/yggdrasil/sessionserver/session/minecraft/profile/"
        if path.startswith(prefix):
            uid = path[len(prefix):].replace("-", "").lower()
            if uid == TEST_UUID:
                return self._send(200, self._profile_payload())
            return self._send(204)                     # 未知 UUID → 204（规范行为）
        if path == "/api/yggdrasil/textures/skin1":
            return self._send(200, raw=make_skin_png(),
                              headers={"Content-Type": "image/png"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        body = self._json_body()

        if path == "/api/yggdrasil/api/profiles/minecraft":
            names = body if isinstance(body, list) else []
            data = [{"id": TEST_UUID, "name": TEST_USER}
                    for n in names if str(n).lower() == TEST_USER.lower()]
            return self._send(200, self._envelope(data))

        if path == "/api/yggdrasil/authserver/authenticate":
            body = body or {}
            if (str(body.get("username")) == TEST_USER
                    and str(body.get("password")) == TEST_PASS):
                return self._send(200, self._envelope({
                    "accessToken": "fake-access-token",
                    "clientToken": body.get("clientToken") or "fake-client",
                    "selectedProfile": {"id": TEST_UUID, "name": TEST_USER},
                    "availableProfiles": [{"id": TEST_UUID, "name": TEST_USER}],
                }))
            # ⚠️ 错误体：error 是**对象**不是字符串（这家的写法）
            return self._send(403, {
                "error": {"code": "HTTP_403", "message": "登录失败"},
                "errorMessage": "Invalid credentials. Invalid username or password.",
                "success": False, "data": None, "message": "登录失败"})

        if path == "/api/yggdrasil/authserver/validate":
            token = (body or {}).get("accessToken")
            return self._send(204 if token == "fake-access-token" else 403,
                              None if token == "fake-access-token"
                              else {"errorMessage": "Invalid token."})

        if path == "/api/yggdrasil/authserver/refresh":
            return self._send(403, {"errorMessage": "Invalid token."})

        return self._send(404, {"error": "not found"})


class FakeAliServer:
    """假 ALI 服务器（上下文管理器，用完自动关）"""

    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        # ⚠️ 处理函数里的 `self.server` 是 **ThreadingHTTPServer 本身**，
        # 不是外面这个包装对象。要用的东西得挂到 httpd 上，
        # 否则 `self.server.base_url` 会 AttributeError（踩过）。
        self.httpd.base_url = self.base_url
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def api_root(self) -> str:
        return f"{self.base_url}/api/yggdrasil"

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


# ============================================================
# 断言
# ============================================================

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

    with FakeAliServer() as srv:
        # ---------- 1. 自动识别 ----------
        print("1. 自动识别 API 根")
        root, how = yggdrasil.discover_api_root(srv.base_url, timeout=5)
        check("   从头识别", root, srv.api_root)
        check("   说明了怎么找到的", "响应头" in how, True)
        root2, _ = yggdrasil.discover_api_root(srv.api_root, timeout=5)
        check("   已经带路径就用它", root2, srv.api_root)

        print("\n2. 地址写法归一化")
        check("   裸域名补 https", yggdrasil.normalize_site("demo.lunch.ink"),
              "https://demo.lunch.ink")
        check("   本地地址用 http", yggdrasil.normalize_site("127.0.0.1:8080"),
              "http://127.0.0.1:8080")
        check("   去掉尾斜杠", yggdrasil.normalize_site("https://a.b/c/"),
              "https://a.b/c")

        # ---------- 3. 元数据 ----------
        print("\n3. 元数据")
        meta = yggdrasil.fetch_meta(root, timeout=5)
        check("   服务器名", meta["serverName"], "假验证站")
        check("   实现名", meta["implementationName"], "fake-mc-yggdrasil")
        check("   skinDomains", meta["skinDomains"], ["127.0.0.1"])
        check("   feature 拆出来了", meta["features"], {"non_email_login": True})

        # ---------- 4. 查角色 ----------
        print("\n4. 按名字查角色（含信封解包）")
        found = yggdrasil.lookup_profiles(root, [TEST_USER], timeout=5)
        check("   查到", [(p["id"], p["name"]) for p in found],
              [(TEST_UUID, TEST_USER)])
        check("   查不到是空列表不是错误",
              yggdrasil.lookup_profiles(root, ["Nobody"], timeout=5), [])
        check("   大小写不敏感",
              len(yggdrasil.lookup_profiles(root, ["testuser"], timeout=5)), 1)
        check("   空输入不发请求", yggdrasil.lookup_profiles(root, [], timeout=5), [])

        # ---------- 5. 档案 + 皮肤 ----------
        print("\n5. 档案与皮肤纹理")
        profile = yggdrasil.fetch_profile(root, TEST_UUID, timeout=5)
        check("   拿到档案", (profile["id"], profile["name"]), (TEST_UUID, TEST_USER))
        check("   未知 UUID 返回 None（204 不是错误）",
              yggdrasil.fetch_profile(root, "0" * 32, timeout=5), None)
        check("   id 长度不对直接返回 None",
              yggdrasil.fetch_profile(root, "abc", timeout=5), None)

        textures = yggdrasil.decode_textures(profile)
        check("   base64 纹理解开了", "SKIN" in textures, True)
        url = yggdrasil.skin_url(profile, ["127.0.0.1"])
        check("   皮肤地址（域名在 skinDomains 里）", url,
              f"{srv.base_url}/api/yggdrasil/textures/skin1")
        check("   ⚠️ 域名不在 skinDomains 里就拒掉",
              yggdrasil.skin_url(profile, ["example.com"]), "")

        # ---------- 6. 登录 ----------
        print("\n6. 登录 / 校验")
        auth = yggdrasil.authenticate(root, TEST_USER, TEST_PASS, timeout=5)
        check("   拿到 accessToken", auth["accessToken"], "fake-access-token")
        check("   选中角色", auth["selectedProfile"]["name"], TEST_USER)
        check("   令牌校验通过",
              yggdrasil.validate(root, "fake-access-token", timeout=5), True)
        check("   假令牌判为无效",
              yggdrasil.validate(root, "wrong", timeout=5), False)

        print("\n7. 错误信息要能读出来（error 是对象那种）")
        try:
            yggdrasil.authenticate(root, TEST_USER, "wrong-password", timeout=5)
            check("   密码错要抛异常", "没抛", "抛了")
        except yggdrasil.YggdrasilError as e:
            check("   密码错读出了服务端原话", e.message,
                  "Invalid credentials. Invalid username or password.")
            check("   带上状态码", e.status, 403)

        # ---------- 8. 头像：从真皮肤抠脸 ----------
        print("\n8. 皮肤 → 头像（走 ui/avatar.py 那套抠图）")
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance() or QApplication([])
            from ui.avatar import face_pixmap
            import requests, pathlib, tempfile
            data = requests.get(url, timeout=8).content
            check("   下载到皮肤", data[:8], b"\x89PNG\r\n\x1a\n")
            tmp = pathlib.Path(tempfile.mkdtemp()) / "skin.png"
            tmp.write_bytes(data)
            pixmap = face_pixmap(tmp, 40, 1.0)
            check("   抠出头像", pixmap is not None and not pixmap.isNull(), True)
            # 放大是最近邻，中心那块应该还是脸的颜色
            img = pixmap.toImage()
            c = img.pixelColor(img.width() // 2, img.height() // 2)
            check("   头像中心 = 皮肤上脸的颜色",
                  (c.red(), c.green(), c.blue(), c.alpha()), FACE_RGBA)
        except ImportError as e:
            print(f"   跳过（没装 PyQt6：{e}）")

        # ---------- 9. 验证器本身（不依赖 Qt）----------
        print("\n9. ThirdPartyVerifier 的几种结果")
        from core.verify.thirdparty import ThirdPartyVerifier
        verifier = ThirdPartyVerifier()

        r = verifier.verify({"server_address": srv.base_url,
                             "player_name": TEST_USER, "timeout": 5})
        check("   查名字找到 → 通过", r.state, "verified")
        check("   带上了角色", [(p["name"], p["uuid"]) for p in r.players],
              [(TEST_USER, TEST_UUID)])
        check("   取到了皮肤地址", bool(r.players[0]["skin_url"]), True)
        check("   消息里点明「不是正版验证」", "不是**正版验证**" in r.message
              or "不是" in r.message, True)

        r = verifier.verify({"server_address": srv.base_url,
                             "player_name": "Nobody", "timeout": 5})
        check("   查不到 → 未通过", r.state, "absent")

        r = verifier.verify({"server_address": srv.base_url, "player_name": TEST_USER,
                             "password": TEST_PASS, "timeout": 5})
        check("   登录成功 → 通过", r.state, "verified")
        check("   登录时也拿到了皮肤", bool(r.players[0]["skin_url"]), True)

        r = verifier.verify({"server_address": srv.base_url, "player_name": TEST_USER,
                             "password": "wrong", "timeout": 5})
        check("   密码错 → 查询失败", r.state, "error")
        check("   原样带出服务端的话", "Invalid username or password" in r.message, True)

        r = verifier.verify({"server_address": "", "player_name": "x"})
        check("   没填站点 → 查询失败", r.state, "error")

        r = verifier.verify({"server_address": srv.base_url, "player_name": ""})
        check("   没填角色 → 查询失败", r.state, "error")

        r = verifier.verify({"server_address": "http://127.0.0.1:1",
                             "player_name": TEST_USER, "timeout": 2})
        check("   连不上 → 查询失败", r.state, "error")

        print("\n10. 密码不能出现在结果里")
        r = verifier.verify({"server_address": srv.base_url, "player_name": TEST_USER,
                             "password": TEST_PASS, "timeout": 5})
        blob = json.dumps({"m": r.message, "h": r.host, "p": getattr(r, "players", [])},
                          ensure_ascii=False, default=str)
        check("   结果里没有密码明文", TEST_PASS in blob, False)

    print(f"\n>>> {'全部通过' if not fails else '失败: ' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
