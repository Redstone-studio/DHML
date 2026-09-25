"""
Minecraft 服务器查询（Server List Ping / Query）—— 拿在线玩家列表

这是"古法正版验证"的数据来源，**纯协议、不依赖界面**，可以单独跑：

    python -m core.verify.mcping mc.hypixel.net

## 两条路，能力不一样

**Server List Ping（SLP）** —— 游戏里那个"多人游戏"列表用的就是它
  · TCP，**不用服务器开任何配置**，绝大多数服务器都支持
  · 缺点：玩家列表是**抽样**的，服务器最多给 12 个名字
    （原版就是这么实现的，人多了你未必在名单里）
  · 有些服务器（尤其是商业服）会把 sample 塞成自己的广告词，
    里面根本没有真玩家 —— 所以**必须校验每一条的 UUID 是否合法**，
    否则会把广告当成玩家显示出来

**Query** —— GameSpy4 协议
  · UDP，**能拿到完整玩家名单**，不是抽样
  · 缺点：要服务器在 server.properties 里开 `enable-query=true`，
    默认是关的，所以不是所有服务器都能用
  · 正因为是完整的，验证时**应该优先用 Query，Query 不通再退回 SLP**

## 为什么不用第三方 API

`api.mojang.com` 只能"按名字查 UUID"，查不到"谁在线"；
mcstatus.io / mcsrvstat.us 这类是别人搭的中转，多一层依赖、也可能被限速。
自己发几个字节的包就能问出来，没必要绕。
"""
import json
import socket
import struct

DEFAULT_PORT = 25565
DEFAULT_TIMEOUT = 5.0

# SLP 握手时用的协议版本。**填 -1 是故意的**：
# 服务端只看 next_state，协议号不匹配它也不会拒绝状态查询，
# 填死某个版本反而会在"服务器版本更新了"之后需要跟着改。
PROTOCOL_ANY = -1


# ============================================================
# VarInt（Minecraft 的变长整数，协议里到处都是）
# ============================================================

def write_varint(value: int) -> bytes:
    """把整数编码成 VarInt（7 位一组，最高位表示"还有后续"）

    ⚠️ 负数要先当成**无符号 32 位**再编码。Python 的整数没有位宽，
    直接 `while value:` 对负数会死循环（右移负数永远是 -1）。
    """
    value &= 0xFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def read_varint(sock) -> int:
    """从 socket 读一个 VarInt（最多 5 字节）"""
    result = 0
    for shift in range(0, 35, 7):
        chunk = _recv_exact(sock, 1)
        if not chunk:
            raise ConnectionError("连接在读到 VarInt 之前就断了")
        byte = chunk[0]
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            # 按有符号 32 位还原（协议里的负数就是这么传的）
            if result >= 1 << 31:
                result -= 1 << 32
            return result
    raise ValueError("VarInt 超过 5 个字节，不是合法的包")


def _recv_exact(sock, count: int) -> bytes:
    """一定要读满 count 个字节（TCP 是流，一次 recv 可能只给一半）"""
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def write_string(text: str) -> bytes:
    raw = (text or "").encode("utf-8")
    return write_varint(len(raw)) + raw


def read_string(sock) -> str:
    length = read_varint(sock)
    if length < 0:
        raise ValueError("字符串长度是负数")
    return _recv_exact(sock, length).decode("utf-8", errors="replace")


def _write_packet(sock, payload: bytes):
    sock.sendall(write_varint(len(payload)) + payload)


# ============================================================
# Server List Ping
# ============================================================

def ping_status(host: str, port: int = DEFAULT_PORT,
                timeout: float = DEFAULT_TIMEOUT) -> dict:
    """取服务器状态（version / players / description）

    返回解析后的 JSON 字典。连不上/超时/格式不对都会抛异常，
    由调用方决定怎么呈现（**不要在协议层吞掉错误**）。
    """
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)

        # 1) 握手：告诉服务端"我不是来玩游戏的，是来查状态的"
        handshake = (b"\x00" + write_varint(PROTOCOL_ANY)
                     + write_string(host) + struct.pack(">H", port)
                     + write_varint(1))
        _write_packet(sock, handshake)

        # 2) 状态请求：一个空包
        _write_packet(sock, b"\x00")

        # 3) 应答：长度 + 包 ID + JSON 字符串
        read_varint(sock)                     # 整个包的长度（用不上，按流读就行）
        packet_id = read_varint(sock)
        if packet_id != 0x00:
            raise ValueError(f"状态应答的包 ID 是 {packet_id}，期望 0")
        text = read_string(sock)

    return json.loads(text)


# ============================================================
# Query（GameSpy4）
# ============================================================

_QUERY_MAGIC = b"\xFE\xFD"


def _split_query_response(data: bytes, expect_type: bytes) -> tuple:
    """拆 Query 应答 → (session_id, payload)

    ⚠️ 这里必须**容忍两种应答格式**：有的服务端在类型字节前面会带上
    `FE FD` 魔数（原版 Java 服务端就是这样，很多第三方实现也有），
    有的不带。按固定偏移量解析的话，换一台服务端就整个错位
    —— 实测第一版写死 `data[16:]`，结果 hostname/numplayers 全是 None、
    玩家列表空，看着像"服务器没开 query"，其实是自己解析错了。

    拆完之后结构才是统一的：`类型(1) | session(4) | 正文`
    """
    if data[:2] == _QUERY_MAGIC:
        data = data[2:]
    if len(data) < 5:
        raise ValueError("Query 应答太短")
    if data[:1] != expect_type:
        raise ValueError(f"Query 应答类型是 {data[:1]!r}，期望 {expect_type!r}")
    return data[1:5], data[5:]


def query_full(host: str, port: int = DEFAULT_PORT,
               timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Query 协议拿**完整**玩家名单

    ⚠️ 走的是 UDP，端口通常是游戏端口（有些服务端会另开一个 query.port）。
    服务器没开 `enable-query` 的话不会有任何回应 —— 表现为超时，
    这是**正常情况**，调用方应该退回 SLP。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        session_id = 0x0A0B0C0D

        # 1) 握手拿 challenge token
        sock.sendto(_QUERY_MAGIC + b"\x09" + struct.pack(">i", session_id),
                    (host, port))
        data, _ = sock.recvfrom(2048)
        _session, payload = _split_query_response(data, b"\x09")
        token = payload.split(b"\x00")[0]
        if not token:
            raise ValueError("Query 握手没拿到 challenge token")
        challenge = int(token)

        # 2) 用 token 换完整状态（challenge 是 4 字节整数，后面跟 4 字节填充）
        request = (_QUERY_MAGIC + b"\x00" + struct.pack(">i", session_id)
                   + struct.pack(">i", challenge) + b"\x00\x00\x00\x00")
        sock.sendto(request, (host, port))
        data, _ = sock.recvfrom(65535)

    _session, payload = _split_query_response(data, b"\x00")
    return _parse_query_payload(payload)


def _parse_query_payload(payload: bytes) -> dict:
    """Query 的正文是 `splitnum\\x00\\x80\\x00` + `key\\x00value\\x00…`

    玩家是 player_0、player_1…
    """
    marker = b"splitnum\x00\x80\x00"
    if payload.startswith(marker):
        payload = payload[len(marker):]

    parts = payload.split(b"\x00")
    result = {}
    for i in range(0, len(parts) - 1, 2):
        key = parts[i].decode("utf-8", errors="replace").strip()
        value = parts[i + 1].decode("utf-8", errors="replace").strip()
        if key:
            result[key] = value
    return result


# ============================================================
# 归一化：不管走哪条路，都吐同一种结构
# ============================================================

def _looks_like_uuid(text: str) -> bool:
    """32 位十六进制（可带连字符）才算真 UUID

    ⚠️ 这一步是**必需的**，不是保险：很多服务器把 sample 当广告位，
    塞 "§c欢迎来到xxx" 这种，name 里有人话、id 是空的或乱填。
    不校验就会把广告当成在线玩家显示出来。
    """
    raw = (text or "").replace("-", "")
    return len(raw) == 32 and all(c in "0123456789abcdefABCDEF" for c in raw)


def players_from_status(status: dict) -> list:
    """从 SLP 的 JSON 里抠出玩家（**抽样，最多 12 个**）"""
    sample = ((status or {}).get("players") or {}).get("sample") or []
    out = []
    for entry in sample:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        uid = str(entry.get("id") or "").strip()
        if not name or not _looks_like_uuid(uid):
            continue        # 广告位，跳过
        out.append({"name": name, "uuid": uid, "source": "slp"})
    return out


def players_from_query(info: dict) -> list:
    """从 Query 的键值对里抠出玩家（**完整名单**）

    键名是 player_0、player_1…；Query 只给名字，不给 UUID
    （此时 uuid 留空，头像那一步按名字去取）。
    """
    out = []
    for key, value in (info or {}).items():
        if not key.startswith("player_"):
            continue
        name = (value or "").strip()
        if name:
            out.append({"name": name, "uuid": "", "source": "query"})
    return out


class ServerStatus:
    """一次查询的结果

    slp_players / query_players 分开留着 —— 两个来源的玩家数对不上时
    （Query 说 30 人、SLP 只抽样到 8 个）要能看出是"抽样"而不是"少了人"。
    """

    def __init__(self):
        self.online = 0
        self.max = 0
        self.version = ""
        self.motd = ""
        self.players = []
        self.slp_players = []
        self.query_players = []
        self.query_ok = False
        self.error = ""

    def __repr__(self):
        return (f"<ServerStatus {self.online}/{self.max} 玩家={len(self.players)} "
                f"query={'通' if self.query_ok else '不通'}>")


def status_to_text(status: dict) -> str:
    """把 description 拍平成一行文字

    MOTD 是个聊天组件树（1.20.3 之后还可能是纯字符串），
    里面套 extra/label，得递归抽 text。不处理的话界面上只会显示个空。
    """
    desc = (status or {}).get("description")
    return _flatten_chat(desc).strip()


def _flatten_chat(node) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_flatten_chat(x) for x in node)
    if isinstance(node, dict):
        text = node.get("text", "")
        if not isinstance(text, str):
            text = str(text)
        for key in ("extra", "with"):
            if key in node:
                text += _flatten_chat(node[key])
        return text
    return str(node)


def strip_motd_codes(text: str) -> str:
    """去掉 § 颜色代码（§a、§l 这种）"""
    out = []
    i = 0
    while i < len(text):
        if text[i] == "§" and i + 1 < len(text):
            i += 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def fetch_players(host: str, port: int = DEFAULT_PORT,
                  timeout: float = DEFAULT_TIMEOUT,
                  prefer_query: bool = True) -> ServerStatus:
    """查一台服务器：先试 Query（完整名单），再退回 SLP（抽样）

    **任何一个环节失败都不抛异常** —— 这是个"能拿到多少算多少"的查询，
    把错误放进 status.error 给界面显示。
    """
    result = ServerStatus()

    if prefer_query:
        try:
            info = query_full(host, port, timeout)
            result.query_ok = True
            result.query_players = players_from_query(info)
            result.online = _to_int(info.get("numplayers"))
            result.max = _to_int(info.get("maxplayers"))
            result.motd = strip_motd_codes(info.get("motd", "") or "")
            result.players = list(result.query_players)
        except (OSError, ValueError) as e:
            result.error = f"{type(e).__name__}: {e}"

    try:
        status = ping_status(host, port, timeout)
        result.slp_players = players_from_status(status)
        result.version = ((status.get("version") or {}).get("name") or "").strip()
        result.motd = result.motd or strip_motd_codes(status_to_text(status))
        players = (status.get("players") or {})
        if not result.query_ok:
            result.online = _to_int(players.get("online"))
            result.max = _to_int(players.get("max"))
            result.players = list(result.slp_players)
        result.error = ""
    except (OSError, ValueError, json.JSONDecodeError) as e:
        if not result.query_ok:
            result.error = f"{type(e).__name__}: {e}"

    return result


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ============================================================
# 命令行自测：python -m core.verify.mcping <服务器> [端口]
# ============================================================

def _main(argv):
    import sys
    # ⚠️ Windows 控制台默认 GBK，MOTD 里全是 emoji / 特殊符号，
    # 直接 print 会 UnicodeEncodeError 把自测脚本炸掉（实测踩过）
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass

    if len(argv) < 2:
        print(__doc__)
        print("用法: python -m core.verify.mcping <服务器地址> [端口]")
        return 1

    host = argv[1]
    port = int(argv[2]) if len(argv) > 2 else DEFAULT_PORT

    print(f"查询 {host}:{port} …")
    st = fetch_players(host, port)
    print(f"  版本  : {st.version}")
    print(f"  人数  : {st.online}/{st.max}")
    print(f"  标语  : {st.motd}")
    print(f"  Query : {'通' if st.query_ok else '不通'}"
          + (f"（{st.error}）" if st.error else ""))
    print(f"  SLP 抽样 {len(st.slp_players)} 个: "
          + ", ".join(p["name"] for p in st.slp_players))
    print(f"  Query 完整 {len(st.query_players)} 个: "
          + ", ".join(p["name"] for p in st.query_players))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))
