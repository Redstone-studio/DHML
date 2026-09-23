"""账户管理（%APPDATA%/MCLuncher/accounts.json）

目前只有离线验证可用；正版 / 第三方验证只占了 UI 的位置。

文案约定：账户类型的显示名走 type_label()（内部过 tr()），
不要在别处缓存这个字典 —— 那样语言切换后它不会更新。
"""

import json
import os
from datetime import datetime
from pathlib import Path

from core.i18n import tr
from core.launch import offline_uuid

# 账户类型的**源文案**（中文即 key，见 core/i18n.py）
_TYPE_LABELS = {
    "offline": "离线验证",
    "microsoft": "正版验证",
    "thirdparty": "第三方验证",
}  # noqa: i18n  —— 这些中文在 type_label() 里过 tr()


def type_label(key: str) -> str:
    """账户类型的显示名（每次调用都过 tr()，所以跟着语言走）"""
    return tr(_TYPE_LABELS.get(key, "未知"))


def get_config_dir() -> Path:
    """配置目录。

    以前这里抄了一份 core/config.py 的同名函数（两边都硬编码 "MCLuncher"），
    做便携模式时这种重复会立刻咬人 —— 便携判断只能有一处。
    所以现在直接转给 core/config.py 的那一个。
    """
    from core.config import get_config_dir as _shared
    return _shared()


ACCOUNTS_FILE = get_config_dir() / "accounts.json"


class AccountManager:
    def __init__(self):
        self.accounts = []   # list[dict]
        self.current = None  # account name
        self.load()

    def load(self):
        if ACCOUNTS_FILE.exists():
            try:
                # 读字节，让 json 自己处理 BOM（见 core/config.py 里同样的说明）
                data = json.loads(ACCOUNTS_FILE.read_bytes())
                self.accounts = data.get("accounts", [])
                self.current = data.get("current")
            except Exception as e:
                print(f"[Accounts] load failed: {e}")
                self.accounts, self.current = [], None
        self._repair_offline_uuids()

    def _repair_offline_uuids(self):
        """修掉历史遗留的错误离线 UUID

        早期版本是把 MD5 直接拼成 UUID 的，**漏了置版本位和 variant 位** ——
        那些值不是合法 UUID，而且和 PCL / HMCL 算出来的不一样，
        会让同一个玩家在存档和服务器里被当成两个人。

        离线 UUID 是玩家名的纯函数，重算一次就行，不用问用户。
        """
        changed = 0
        for account in self.accounts:
            if account.get("type") != "offline":
                continue
            correct = offline_uuid(account.get("name", ""))
            if account.get("uuid") != correct:
                account["uuid"] = correct
                changed += 1
        if changed:
            print(f"[Accounts] 修正了 {changed} 个离线 UUID（旧值漏了版本位）")
            self.save()

    def save(self):
        data = {"accounts": self.accounts, "current": self.current}
        ACCOUNTS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def add_offline(self, name: str):
        # 离线 UUID = "OfflinePlayer:<名字>" 的 MD5，再按规范置版本位 / variant 位。
        # 实现在 core/launch.py 里（那边有说明和测试值）——
        # 这里当初自己抄了一份，抄的时候漏了置位，才出的这个 bug。
        acc = {
            "type": "offline",
            "name": name,
            "uuid": offline_uuid(name),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        # 同名覆盖
        self.accounts = [a for a in self.accounts if a["name"] != name]
        self.accounts.append(acc)
        self.current = name
        self.save()
        return acc

    def remove(self, name: str):
        self.accounts = [a for a in self.accounts if a["name"] != name]
        if self.current == name:
            self.current = self.accounts[0]["name"] if self.accounts else None
        self.save()

    def set_current(self, name: str):
        if any(a["name"] == name for a in self.accounts):
            self.current = name
            self.save()

    def get_current(self):
        for a in self.accounts:
            if a["name"] == self.current:
                return a
        return None
