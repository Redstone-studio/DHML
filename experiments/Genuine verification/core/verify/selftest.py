"""
离线自测：用本地假服务器验证协议实现

**为什么不直接打真服务器**：真服务器的返回值会变（人多人少、MOTD 改版），
拿它当断言等于没断言。这里起一个本地 TCP/UDP 服务，返回**写死的**状态包，
把"编码 → 发包 → 解析"整条链路验一遍。

    python -m core.verify.selftest
"""
import json
import socket
import struct
import sys
import threading

from core.verify import mcping


def _read_varint(sock):
    return mcping.read_varint(sock)


def _read_packet(sock) -> tuple:
    """读一个包，返回 (packet_id, payload_bytes)"""
    _read_varint(sock)                    # 包长度
    pid = _read_varint(sock)
    return pid, sock


STATUS_JSON = {
    "version": {"name": "1.20.4", "protocol": 765},
    "players": {
        "max": 20,
        "online": 3,
        # 真玩家两条 + 一条广告（id 是空的）—— 广告必须被过滤掉
        "sample": [
            {"name": "Notch", "id": "069a79f444e94726a5befca90e38aaf5"},
            {"name": "jeb_", "id": "853c80ef-3c37-49fd-aa49-938b674adae6"},
            {"name": "§c广告位招租 §e点我", "id": ""},
        ],
    },
    "description": {"text": "本地测试服 ", "extra": [{"text": "§a欢迎"}]},
}


def _serve_slp(sock):
    conn, _ = sock.accept()
    with conn:
        conn.settimeout(5)
        # 握手
        _read_varint(conn)
        _read_varint(conn)                       # packet id 0x00
        _read_varint(conn)                       # protocol version
        mcping.read_string(conn)                 # server address
        conn.recv(2)                             # port
        _read_varint(conn)                       # next state
        # 状态请求
        _read_varint(conn)
        _read_varint(conn)
        # 应答
        body = (mcping.write_varint(0)
                + mcping.write_string(json.dumps(STATUS_JSON)))
        conn.sendall(mcping.write_varint(len(body)) + body)


def _serve_query(sock):
    """假 Query 服务端

    ⚠️ 这里**故意带 `FE FD` 魔数**（原版 Java 服务端的行为），
    同时校验客户端回传的 challenge 对不对 —— 只回一个固定应答的话，
    "客户端有没有把 token 正确编码进去"这点根本验不到。
    """
    challenge = "12345"
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except OSError:
            return
        if len(data) < 7 or data[:2] != mcping._QUERY_MAGIC:
            continue
        session = data[3:7]
        if data[2:3] == b"\x09":
            sock.sendto(mcping._QUERY_MAGIC + b"\x09" + session
                        + challenge.encode() + b"\x00", addr)
        elif data[2:3] == b"\x00":
            # challenge 在 session 之后的 4 字节
            got = struct.unpack(">i", data[7:11])[0]
            if str(got) != challenge:
                # 回一个明显的错误应答，让测试能看出是 challenge 没对上
                sock.sendto(mcping._QUERY_MAGIC + b"\x00" + session
                            + b"splitnum\x00\x80\x00"
                            + b"hostname\x00CHALLENGE-MISMATCH\x00\x00", addr)
                continue
            pairs = [("hostname", "本地测试服"), ("numplayers", "2"),
                     ("maxplayers", "20"), ("player_0", "Notch"),
                     ("player_1", "jeb_")]
            payload = b"splitnum\x00\x80\x00"
            for k, v in pairs:
                payload += k.encode() + b"\x00" + v.encode() + b"\x00"
            payload += b"\x00"
            sock.sendto(mcping._QUERY_MAGIC + b"\x00" + session + payload, addr)


def _bare_server(handler, kind=socket.SOCK_STREAM):
    s = socket.socket(socket.AF_INET, kind)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    if kind == socket.SOCK_STREAM:
        s.listen(4)
    port = s.getsockname()[1]
    threading.Thread(target=handler, args=(s,), daemon=True).start()
    return s, port


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass

    fails = []

    def check(name, got, want):
        ok = got == want
        print(f"  {'OK ' if ok else 'X  '} {name}: {got!r}"
              + ("" if ok else f"  期望 {want!r}"))
        if not ok:
            fails.append(name)

    # ---------- 1. VarInt 往返 ----------
    print("1. VarInt 编解码")
    for value in (0, 1, 127, 128, 255, 2097151, 2147483647, -1, -2147483648):
        encoded = mcping.write_varint(value)
        # 解回来（用内存流模拟 socket）
        class _S:
            def __init__(self, data): self.data = data
            def recv(self, n):
                out, self.data = self.data[:n], self.data[n:]
                return out
        check(f"  {value}", mcping.read_varint(_S(encoded)), value)

    # ---------- 2. SLP ----------
    print("\n2. Server List Ping（本地假服务器）")
    _srv, port = _bare_server(_serve_slp)
    status = mcping.ping_status("127.0.0.1", port, timeout=3)
    check("  版本", status["version"]["name"], "1.20.4")
    check("  人数", (status["players"]["online"], status["players"]["max"]), (3, 20))
    check("  MOTD 拍平", mcping.status_to_text(status), "本地测试服 §a欢迎")
    players = mcping.players_from_status(status)
    check("  玩家数（广告被滤掉）", len(players), 2)
    check("  第一个玩家", players[0]["name"], "Notch")
    check("  UUID 带连字符也认",
          mcping._looks_like_uuid("853c80ef-3c37-49fd-aa49-938b674adae6"), True)

    # ---------- 3. Query ----------
    print("\n3. Query（本地假服务器）")
    _qsrv, qport = _bare_server(_serve_query, socket.SOCK_DGRAM)
    info = mcping.query_full("127.0.0.1", qport, timeout=3)
    check("  hostname", info.get("hostname"), "本地测试服")
    check("  numplayers", info.get("numplayers"), "2")
    qplayers = mcping.players_from_query(info)
    check("  玩家数", len(qplayers), 2)
    check("  名字", [p["name"] for p in qplayers], ["Notch", "jeb_"])
    check("  Query 不给 UUID", qplayers[0]["uuid"], "")

    # ---------- 4. fetch_players 合并两条路 ----------
    print("\n4. fetch_players（Query 优先，SLP 兜底）")
    # 两个假服务器在不同端口，这里分别验
    st = mcping.fetch_players("127.0.0.1", qport, timeout=3)
    print(f"  Query 端口: query_ok={st.query_ok} 玩家={[p['name'] for p in st.players]}")
    if not st.query_ok:
        fails.append("query_ok")
    # Query 通了但 SLP 不在这个端口 → 玩家应该来自 Query
    check("  玩家来自 Query", [p["name"] for p in st.players], ["Notch", "jeb_"])

    # ---------- 5. 错误路径 ----------
    print("\n5. 错误路径（不能抛异常出去）")
    st2 = mcping.fetch_players("127.0.0.1", 1, timeout=1.5)   # 没人听的端口
    check("  连不上时 error 非空", bool(st2.error), True)
    check("  连不上时玩家为空", st2.players, [])
    check("  连不上时不抛异常", True, True)

    print(f"\n>>> {'全部通过' if not fails else '失败: ' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
