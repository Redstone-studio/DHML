"""
外置登录验证（authlib-injector / Yggdrasil）

## 和"古法"那条路的根本区别

| | 玩家列表（古法） | 外置登录（这条） |
|---|---|---|
| 谁在验证 | **Mojang/微软**（服务端拿 session 去校验） | **第三方验证站**（自己的账号库） |
| 证明的是 | 这是**正版**账号 | 这个账号**在那个站上存在**，而且你能登录它 |
| 需要密码 | 不需要 | 查名字不需要；要证明"是你的"才需要 |

⚠️ **外置登录不等于正版验证。** 它证明的是"你拥有某验证站上的某个角色"。
对开了外置登录的服务器来说这就够了（服务器认这个站），但它跟 Mojang 没关系。
界面上必须把这句话写出来，不能让用户以为"验证通过 = 正版"。

## 两种模式

**查名字**（不用密码）
    站点 + 角色名 → `POST /api/profiles/minecraft` 查得到就是存在。
    适合"这个 ID 在这台验证站上注册了吗"。

**登录**（要密码）
    `POST /authserver/authenticate` → accessToken + selectedProfile。
    这才**证明是你的**。密码只在 `core/verify/yggdrasil.py` 那一层出现，
    **不落盘、不进日志、不留在界面上**。

## 自动识别

用户只填站点地址（`demo.lunch.ink`）就行 —— API 根由
`yggdrasil.discover_api_root()` 读站点响应头拿到。用户填了带路径的地址就用他填的。
"""
import time

from core.verify import yggdrasil
from core.verify.base import (
    STATE_ABSENT, STATE_ERROR, STATE_INCONCLUSIVE, STATE_VERIFIED,
    Verifier, VerifyResult,
)

#: 登录时角色名不止一个的话，先说清"验证的是哪一个"
MULTI_PROFILE_HINT = "（该账号下还有别的角色，这次只看选中的那个）"


class ThirdPartyVerifier(Verifier):
    key = "thirdparty"
    title = "外置登录（authlib-injector）"
    hint = ("填验证站地址（比如 demo.lunch.ink）就够，API 根会自动识别。\n"
            "查角色名＝站点上有没有这个 ID；填密码＝连登录一起做（证明是你的）。\n"
            "⚠️ 这**不是**正版验证 —— 它只证明这个账号在那台验证站上存在。")
    needs_server = True
    needs_player_name = True
    #: 密码是可选的：填了就登录，不填就只查名字
    needs_password = True

    def verify(self, ctx: dict) -> VerifyResult:
        site = (ctx.get("server_address") or "").strip()
        name = (ctx.get("player_name") or "").strip()
        password = ctx.get("password") or ""
        timeout = float(ctx.get("timeout") or yggdrasil.TIMEOUT)

        if not site:
            return VerifyResult(STATE_ERROR, "还没填验证站地址")
        if not name and not password:
            return VerifyResult(STATE_ERROR, "还没填角色名（或账号）")

        started = time.monotonic()

        # ---------- 1. 自动识别 API 根 ----------
        try:
            api_root, how = yggdrasil.discover_api_root(site, timeout)
        except yggdrasil.YggdrasilError as e:
            return VerifyResult(STATE_ERROR, f"找不到 API 地址：{e}")

        # ---------- 2. 读元数据 ----------
        try:
            meta = yggdrasil.fetch_meta(api_root, timeout)
        except yggdrasil.YggdrasilError as e:
            return VerifyResult(
                STATE_ERROR,
                f"{api_root} 不是有效的 authlib-injector API 根：{e}",
                host=api_root, port=0)

        server_name = meta["serverName"] or "(没写名字)"
        elapsed = lambda: time.monotonic() - started          # noqa: E731

        # ---------- 3. 登录（填了密码才走）----------
        if password:
            return self._verify_by_login(api_root, meta, name, password,
                                         timeout, started)

        # ---------- 4. 只查名字 ----------
        try:
            found = yggdrasil.lookup_profiles(api_root, [name], timeout)
        except yggdrasil.YggdrasilError as e:
            return VerifyResult(STATE_ERROR, f"查询失败：{e}", host=api_root)

        if not found:
            return VerifyResult(
                STATE_ABSENT,
                f"「{server_name}」上没有叫 {name} 的角色。\n"
                f"可能是名字写错了，或者这个 ID 还没在这台验证站上注册"
                f"（站点：{meta['links'].get('register') or site}）。",
                host=api_root, elapsed=elapsed())

        profile = found[0]
        players, extra = self._collect(api_root, meta, profile, timeout)

        return VerifyResult(
            STATE_VERIFIED,
            f"「{server_name}」上找到了角色 {profile['name']}。\n"
            f"⚠️ 这**不是**正版验证 —— 只说明这个 ID 在这台验证站上存在。"
            f"要证明它是你的，把密码填上再验证一次。" + extra,
            players=players, host=api_root, elapsed=elapsed())

    # ---------- 登录那条路 ----------

    def _verify_by_login(self, api_root, meta, name, password, timeout, started):
        try:
            auth = yggdrasil.authenticate(api_root, name, password, timeout=timeout)
        except yggdrasil.YggdrasilError as e:
            # ⚠️ 原样把服务端的话给用户看 —— "密码错"和"账号不存在"
            # 处理方式完全不同，糊成一句"登录失败"等于没说
            return VerifyResult(
                STATE_ERROR,
                f"登录失败：{e.message}\n（验证站：{meta['serverName'] or api_root}）",
                host=api_root, elapsed=time.monotonic() - started)

        selected = auth.get("selectedProfile") or {}
        profiles = auth.get("availableProfiles") or []
        if not selected:
            return VerifyResult(
                STATE_INCONCLUSIVE,
                f"登录成功了，但这个账号下**没有任何角色**，没法确定验证谁。\n"
                f"（验证站：{meta['serverName'] or api_root}）",
                host=api_root, elapsed=time.monotonic() - started)

        note = MULTI_PROFILE_HINT if len(profiles) > 1 else ""
        players, extra = self._collect(api_root, meta, selected, timeout)

        return VerifyResult(
            STATE_VERIFIED,
            f"登录成功，角色 {selected.get('name')} 属于这个账号。\n"
            f"验证站：{meta['serverName'] or api_root}{note}\n"
            f"⚠️ 外置登录证明的是「这个角色在这台验证站上、而且是你的」，"
            f"**不是** Mojang 正版。" + extra,
            players=players, host=api_root,
            elapsed=time.monotonic() - started)

    # ---------- 公共：拿档案 + 皮肤 ----------

    def _collect(self, api_root, meta, profile, timeout) -> tuple:
        """把角色整理成界面要的 `players`，顺带取出皮肤地址

        返回 `(players, 额外说明)`。
        """
        uid = str(profile.get("id") or "")
        name = str(profile.get("name") or "")
        skin = ""
        extra = ""
        try:
            full = yggdrasil.fetch_profile(api_root, uid, timeout)
            if full:
                textures = yggdrasil.decode_textures(full)
                skin = yggdrasil.skin_url(full, meta["skinDomains"])
                if not textures.get("SKIN"):
                    # ⚠️ 角色存在但**还没上传皮肤** —— 这是最常见的情况
                    # （新注册的角色就是空的）。要说出来，不然用户只会看到
                    # "头像怎么是个默认皮肤"，以为是加载失败。
                    # 实测：真站点上新角色返回的 textures 就是 {}
                    extra = "\n（这个角色还没上传皮肤，头像用的是按 UUID 挑的默认皮肤）"
                elif not skin:
                    # 有皮肤但域名不在 skinDomains 里 —— 按规范不能加载
                    extra = ("\n（站点给的皮肤地址不在它的 skinDomains 里，"
                             "按规范不能加载，所以没显示皮肤）")
        except yggdrasil.YggdrasilError:
            pass

        players = [{"name": name, "uuid": uid, "source": "ali", "skin_url": skin}]
        return players, extra
