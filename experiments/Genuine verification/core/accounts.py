"""
本地账户存储

启动器要能"记住登录过的账号，重启之后还在"，就得把账户落盘。
存在配置目录的 `accounts.json` 里（便携模式下就在启动器旁边）。

## 存了什么

    {
      "current": "<uuid>",
      "accounts": [
        {
          "type": "microsoft",
          "name": "Steve",              # 游戏内名字
          "uuid": "069a79f4...",
          "access_token": "...",        # Minecraft 的 access token（会过期）
          "refresh_token": "...",       # 微软的刷新令牌
          "expires_at": 1790000000.0,   # **绝对时间戳**，不是"还有多少秒"
          "client_id": "...",           # 用哪个 Azure 应用登的
          "skin_url": "https://textures.minecraft.net/...",
          "added_at": 1790000000.0
        }
      ]
    }

## ⚠️ 关于令牌的安全，必须说清楚

`refresh_token` 等价于**长期的账号访问权** —— 拿到它就能换出新的
access_token。所以：

  · 这个文件**不要**提交到 git、不要发给别人
  · 现在**没有加密**，就是明文 json。要防的是"别人能读你磁盘"这种场景，
    那种情况下加密也挡不住（密钥也在同一台机器上），所以没做假动作
  · 界面上**只显示名字和 UUID**，不显示令牌
  · 真要更安全的话，Windows 上可以用 DPAPI（`CryptProtectData`）绑到当前用户，
    以后要做再说 —— 现在先把功能跑通

## 为什么 expires_at 存绝对时间

存"还有 3600 秒"的话，程序重启之后这个数字就没有参照点了，
只能当成"还没过期"，然后拿一个早就过期的 token 去请求。
"""
import json
import time
from pathlib import Path

from core.config import get_config_dir

FILE_NAME = "accounts.json"

#: 微软那边的 access token 大概是 24 小时。给一点提前量，
#: 免得"刚好在第 86399 秒"的时候拿去用
EXPIRY_MARGIN = 300.0


def accounts_path() -> Path:
    return get_config_dir() / FILE_NAME


def load() -> dict:
    """读整份账户表

    ⚠️ 坏了就当空的，但**不删原文件** —— 用户手改坏了一个字符就丢掉全部账号，
    代价太大。返回空表时原文件还在，可以自己去修。
    """
    try:
        raw = json.loads(accounts_path().read_bytes())
    except (OSError, ValueError):
        return {"current": "", "accounts": []}
    if not isinstance(raw, dict):
        return {"current": "", "accounts": []}
    accounts = raw.get("accounts")
    if not isinstance(accounts, list):
        accounts = []
    return {"current": str(raw.get("current") or ""), "accounts": accounts}


def save(data: dict) -> bool:
    try:
        path = accounts_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        return True
    except OSError as e:
        print(f"[Accounts] 账户写不进去（{e}）")
        return False


# ---------- 读 ----------

def all_accounts() -> list:
    return list(load().get("accounts") or [])


def find(key: str):
    """按 uuid 或名字找一个账户（uuid 优先）"""
    key = str(key or "").strip().lower()
    if not key:
        return None
    accounts = all_accounts()
    for acc in accounts:
        if str(acc.get("uuid", "")).replace("-", "").lower() == key.replace("-", ""):
            return acc
    for acc in accounts:
        if str(acc.get("name", "")).lower() == key:
            return acc
    return None


def get_current():
    """当前选中的账户；没有就返回第一个；一个都没有返回 None"""
    data = load()
    accounts = data.get("accounts") or []
    if not accounts:
        return None
    current = find(data.get("current") or "")
    return current or accounts[0]


def is_expired(account: dict, now: float = None) -> bool:
    """access_token 过期了没（过期不代表账号没用，还能用 refresh_token 刷）"""
    if not account:
        return True
    expires_at = float(account.get("expires_at") or 0)
    if expires_at <= 0:
        return False                    # 没记时间就当它还能用，让请求自己去撞
    return (now if now is not None else time.time()) >= expires_at - EXPIRY_MARGIN


# ---------- 写 ----------

def upsert(account: dict) -> bool:
    """加一个账户，或者按 uuid 覆盖已有的

    覆盖时**保留旧的 refresh_token**（如果新的没带）——
    微软刷新回来的响应偶尔不给新 token，那时候把旧的抹掉就等于把账号弄丢了。
    """
    if not account or not account.get("uuid"):
        return False
    data = load()
    accounts = data.get("accounts") or []

    uid = str(account["uuid"]).replace("-", "").lower()
    for index, old in enumerate(accounts):
        if str(old.get("uuid", "")).replace("-", "").lower() == uid:
            merged = dict(old)
            merged.update({k: v for k, v in account.items() if v not in ("", None)})
            if not merged.get("refresh_token"):
                merged["refresh_token"] = old.get("refresh_token", "")
            if not merged.get("added_at"):
                merged["added_at"] = old.get("added_at") or time.time()
            accounts[index] = merged
            break
    else:
        account.setdefault("added_at", time.time())
        accounts.append(dict(account))

    data["accounts"] = accounts
    data["current"] = account["uuid"]
    return save(data)


def set_current(uid: str) -> bool:
    data = load()
    data["current"] = str(uid or "")
    return save(data)


def remove(uid: str) -> bool:
    data = load()
    target = str(uid or "").replace("-", "").lower()
    kept = [a for a in (data.get("accounts") or [])
            if str(a.get("uuid", "")).replace("-", "").lower() != target]
    if len(kept) == len(data.get("accounts") or []):
        return False                    # 没找到，什么都没改
    data["accounts"] = kept
    if str(data.get("current", "")).replace("-", "").lower() == target:
        data["current"] = kept[0]["uuid"] if kept else ""
    return save(data)


def clear() -> bool:
    """清空所有账户（界面上"退出登录"用）"""
    return save({"current": "", "accounts": []})
