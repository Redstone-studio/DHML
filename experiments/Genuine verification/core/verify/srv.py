"""
DNS SRV 查询（`_minecraft._tcp.<域名>`）

## 为什么需要它

Minecraft 服务器可以不写端口，靠 **SRV 记录**把域名指到任意端口上。
大多数公开服（尤其是国内的中转服）都是这么配的：

    _minecraft._tcp.play.example.com → 10 5 25566 mc-node3.example.com

**客户端必须先查 SRV**，查不到才退回默认的 25565。
不查的话，`play.example.com` 直连 25565 大概率连不上，
表现成"这个服务器挂了"—— 其实是没查 SRV。

## 为什么自己写 DNS 包

Python 标准库**没有** SRV 查询：
  · `socket.getaddrinfo` 只认 A/AAAA，没有 SRV
  · `dnspython` 能用，但为了一个查询多引一个第三方依赖不划算

DNS 协议本身很简单，自己拼一个 UDP 包就够（见下）。
⚠️ 应答里的域名可能是**压缩指针**（`0xC0` 开头），必须解压，
否则 target 会读成乱码 —— 这是自己写 DNS 解析最容易翻车的地方。
"""
import os
import random
import socket
import struct

DEFAULT_DNS_PORT = 53
DEFAULT_TIMEOUT = 4.0

# 公共 DNS。⚠️ 按顺序试，第一个不通就换下一个 ——
# 有些网络环境会屏蔽其中的某一个
PUBLIC_DNS = ("223.5.5.5", "119.29.29.29", "8.8.8.8", "1.1.1.1")

QTYPE_SRV = 33
QCLASS_IN = 1


def _encode_name(name: str) -> bytes:
    """域名 → DNS 的标签格式：`a.bc` → `\\x01a\\x02bc\\x00`"""
    out = bytearray()
    for label in (name or "").rstrip(".").split("."):
        if not label:
            continue
        raw = label.encode("idna") if any(ord(c) > 127 for c in label) else label.encode("ascii")
        if len(raw) > 63:
            raise ValueError(f"域名标签太长: {label}")
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def _read_name(data: bytes, offset: int, depth: int = 0) -> tuple:
    """读一个域名，返回 (名字, 新偏移)

    ⚠️ 压缩指针（高两位是 11）要跳过去读，而且**偏移量不进递归**：
    返回给调用方的"新偏移"始终是"跳过这个指针之后"的位置。
    这个细节写错的话，后面的记录位置全错。
    """
    labels = []
    jumped = False
    new_offset = offset
    while True:
        if offset >= len(data):
            raise ValueError("DNS 应答里的域名越界")
        length = data[offset]
        if length == 0:
            offset += 1
            if not jumped:
                new_offset = offset
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("DNS 压缩指针不完整")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                new_offset = offset + 2
            if depth > 8:
                raise ValueError("DNS 压缩指针套得太深（有环？）")
            offset = pointer
            jumped = True
            continue
        offset += 1
        labels.append(data[offset:offset + length].decode("ascii", errors="replace"))
        offset += length
    return ".".join(labels), new_offset


def _build_query(name: str, qtype: int) -> tuple:
    query_id = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    question = _encode_name(name) + struct.pack(">HH", qtype, QCLASS_IN)
    return query_id, header + question


def _parse_srv_response(data: bytes, query_id: int) -> list:
    """从应答里抠出所有 SRV 记录，返回 [{priority, weight, port, target}]"""
    if len(data) < 12:
        raise ValueError("DNS 应答太短")
    rid, flags, qdcount, ancount, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    if rid != query_id:
        raise ValueError("DNS 应答的 ID 对不上（串包了？）")
    rcode = flags & 0x000F
    if rcode == 3:
        return []                       # NXDOMAIN：这个域名根本没有 SRV，正常
    if rcode != 0:
        raise ValueError(f"DNS 返回错误码 {rcode}")

    offset = 12
    for _ in range(qdcount):            # 跳过问题段
        _name, offset = _read_name(data, offset)
        offset += 4

    records = []
    for _ in range(ancount):
        _name, offset = _read_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        rdata_start = offset
        if rtype == QTYPE_SRV and rdlength >= 7:
            priority, weight, port = struct.unpack(">HHH", data[rdata_start:rdata_start + 6])
            target, _ = _read_name(data, rdata_start + 6)
            records.append({"priority": priority, "weight": weight,
                            "port": port, "target": target.rstrip(".")})
        offset = rdata_start + rdlength

    # 按优先级升序、同优先级里权重高的优先（RFC 2782 的选法简化版）
    records.sort(key=lambda r: (r["priority"], -r["weight"]))
    return records


def lookup_srv(service: str, domain: str,
               timeout: float = DEFAULT_TIMEOUT,
               dns_servers=None) -> list:
    """查 `_<service>._tcp.<domain>` 的 SRV 记录

    查不到（NXDOMAIN / 没记录 / 网络不通）返回空列表 ——
    调用方据此退回默认端口。**不要因为查不到 SRV 就报错**，那是正常情况。
    """
    name = f"_{service}._tcp.{domain}"
    servers = list(dns_servers or PUBLIC_DNS)

    # 用系统解析器拿到的那台 DNS 也试一下（有些内网环境只有它通）
    try:
        local = socket.gethostbyname(socket.gethostname())
        if local and local not in servers:
            servers.append(local)
    except OSError:
        pass

    query_id, packet = _build_query(name, QTYPE_SRV)
    for server in servers:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(packet, (server, DEFAULT_DNS_PORT))
                data, _ = sock.recvfrom(4096)
            return _parse_srv_response(data, query_id)
        except (OSError, ValueError):
            continue
    return []


def resolve_minecraft(host: str, port: int = None):
    """把"用户填的地址"解析成真正要连的 (host, port)

    规则（和游戏客户端一致）：
      · 用户写了端口 → **原样用**，不查 SRV（写了端口就是明确指定了）
      · 没写端口 → 先查 SRV；查到就用 SRV 的目标；查不到用默认 25565
    """
    host = (host or "").strip()
    if not host:
        return "", 25565
    if port:
        return host, int(port)

    for rec in lookup_srv("minecraft", host):
        if rec.get("target"):
            return rec["target"], int(rec["port"] or 25565)
    return host, 25565


def parse_address(text: str) -> tuple:
    """把用户输入拆成 (host, port 或 None)

    支持 `host`、`host:25565`、`[::1]:25565` 三种写法。
    """
    text = (text or "").strip()
    if not text:
        return "", None
    if text.startswith("["):                 # IPv6
        end = text.find("]")
        if end > 0:
            host = text[1:end]
            rest = text[end + 1:]
            if rest.startswith(":") and rest[1:].isdigit():
                return host, int(rest[1:])
            return host, None
    if text.count(":") == 1:
        host, _, raw = text.partition(":")
        if raw.isdigit():
            return host.strip(), int(raw)
    return text, None


def _main(argv):
    import sys
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    if len(argv) < 2:
        print("用法: python -m core.verify.srv <域名>")
        return 1
    domain = argv[1]
    print(f"查 _minecraft._tcp.{domain} …")
    records = lookup_srv("minecraft", domain)
    if not records:
        print("  没有 SRV 记录（会用默认端口 25565）")
    for r in records:
        print(f"  priority={r['priority']} weight={r['weight']} "
              f"→ {r['target']}:{r['port']}")
    print(f"  最终解析: {resolve_minecraft(domain)}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))
