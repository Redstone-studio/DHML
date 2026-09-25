"""
古法验证：玩家列表

## 原理

Minecraft 服务器有两种模式：

  · `online-mode=true`（正版服）—— 玩家进服时，服务端会拿他的 session
    去 **Mojang/微软的 session server** 校验一次。**校验不过直接踢**，
    玩家根本进不来。
  · `online-mode=false`（离线服）—— 不看，随便填个名字都能进。

所以"你能不能出现在正版服的在线名单里"，本身就是一次正版验证 ——
**验证这一步是服务端做的，不是我们做的**。我们要做的只是"看一眼名单"。

这就是所谓**古法**：不用 OAuth、不用登录、不用拿到任何令牌，
借服务端的鉴权结果来判断。PCL / HMCL 早期都提供过类似入口。

## 流程

    1. 用户用启动器正常启动游戏，进指定的验证服
    2. 启动器查这台服务器的在线玩家名单（core.verify.mcping）
    3. 名单里有你的名字 → 服务端认了你 → 正版

## 三个必须讲清楚的坑

**① 只有完整名单才能下结论。**
Query 协议给的是**完整**名单（要服务端开 `enable-query`）；
SLP 协议只给**最多 12 个抽样**名字（原版行为，人多的时候未必抽到你）。
所以：
  · Query 通了 + 你不在 → 确实没通过
  · 只有 SLP + 你不在 → **不能判定**，得让用户换一台开了 Query 的服，
    或者等人少了再试

**② 名字大小写。** 正版服的在线名单是**服务端账户名**（大小写按账号来），
Minecraft 的名字本身不区分大小写，所以这里按 lowercase 比对。

**③ 离线服上这个方法没有任何意义。**
服务端不校验，谁都能用你的名字进去 —— 那时"名单里有你"什么都证明不了。
界面上必须把这句话写出来，不能给用户一个虚假的安全感。
"""
import time

from core.verify import mcping, srv
from core.verify.base import (
    STATE_ABSENT, STATE_ERROR, STATE_INCONCLUSIVE, STATE_VERIFIED,
    Verifier, VerifyResult,
)


class PlayerListVerifier(Verifier):
    key = "player_list"
    title = "玩家列表（古法）"
    hint = ("用启动器进入验证服，然后这里查服务器的在线名单。\n"
            "名单里有你 = 服务端已经用正版账号把你认下来了。\n"
            "⚠️ 只对 online-mode=true 的正版服有效。")
    needs_server = True
    needs_player_name = True
    implemented = True

    def verify(self, ctx: dict) -> VerifyResult:
        raw_address = (ctx.get("server_address") or "").strip()
        name = (ctx.get("player_name") or "").strip()
        timeout = float(ctx.get("timeout") or mcping.DEFAULT_TIMEOUT)
        prefer_query = bool(ctx.get("prefer_query", True))

        if not raw_address:
            return VerifyResult(STATE_ERROR, "还没填验证服务器地址")
        if not name:
            return VerifyResult(STATE_ERROR, "还没填要验证的玩家名")

        host, port = srv.parse_address(raw_address)
        started = time.monotonic()
        try:
            # 没写端口时先查 SRV —— 公开服大多靠 SRV 指端口，
            # 不查的话直连 25565 大概率不通（见 core/verify/srv.py）
            real_host, real_port = srv.resolve_minecraft(host, port)
        except (OSError, ValueError) as e:
            return VerifyResult(STATE_ERROR, f"地址解析失败：{e}")

        try:
            status = mcping.fetch_players(real_host, real_port, timeout,
                                          prefer_query=prefer_query)
        except (OSError, ValueError) as e:
            # fetch_players 自己会把错误放进 status，能走到这里说明出了别的岔子
            return VerifyResult(STATE_ERROR, f"{type(e).__name__}: {e}",
                                host=real_host, port=real_port)

        elapsed = time.monotonic() - started
        players = list(status.players)
        common = dict(players=players, server=status,
                      host=real_host, port=real_port, elapsed=elapsed)

        # 一台服务器两种查询都失败
        if not players and not status.query_ok and status.error:
            return VerifyResult(
                STATE_ERROR,
                f"连不上服务器（{real_host}:{real_port}）：{status.error}",
                **common)

        # 名字比对（不分大小写）
        wanted = name.lower()
        hit = next((p for p in players if (p.get("name") or "").lower() == wanted),
                   None)
        if hit is not None:
            return VerifyResult(
                STATE_VERIFIED,
                f"在线名单里有 {hit['name']}（共 {status.online} 人在线）——"
                f"服务端已经用正版账号验证过这个玩家。",
                **common)

        # 没找到：**要区分"确实不在完整名单"和"只有抽样、不能下结论"**
        if status.query_ok:
            return VerifyResult(
                STATE_ABSENT,
                f"Query 拿到了完整名单（{len(status.query_players)} 人），"
                f"里面没有 {name}。\n"
                f"要么还没进服，要么这个账号没能通过服务端的正版校验。",
                **common)

        detail = ("这台服务器没开 Query（拿不到完整名单），"
                  "而 SLP 只给最多 12 个抽样名字。")
        if not players:
            detail += "\n而且这次连抽样名字都没拿到 —— 很多服务器会把抽样位当广告位。"
        return VerifyResult(
            STATE_INCONCLUSIVE,
            f"{detail}\n所以「名单里没有 {name}」**不能说明不是正版**。",
            **common)
