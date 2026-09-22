"""账户管理（%APPDATA%/MCLuncher/accounts.json）

目前只有离线验证可用；正版 / 第三方验证只占了 UI 的位置。
"""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path


def get_config_dir() -> Path:
    """跨平台的配置目录

    （和 core/config.py 里的同名函数重复了。两边都硬编码了 "MCLuncher"
    这个目录名，改的时候记得同时改。）
    """
    if os.name == "nt":  # Windows
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / "MCLuncher"
    d.mkdir(parents=True, exist_ok=True)
    return d


ACCOUNTS_FILE = get_config_dir() / "accounts.json"

# 账户类型的显示名
TYPE_LABELS = {
    "offline": "离线验证",
    "microsoft": "正版验证",
    "thirdparty": "第三方验证",
}

TYPE_ICONS = {
    "offline": "✂",
    "microsoft": "🛡",
    "thirdparty": "🔗",
}


class AccountManager:
    def __init__(self):
        self.accounts = []   # list[dict]
        self.current = None  # account name
        self.load()

    def load(self):
        if ACCOUNTS_FILE.exists():
            try:
                data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
                self.accounts = data.get("accounts", [])
                self.current = data.get("current")
            except Exception as e:
                print(f"[Accounts] 读取失败: {e}")
                self.accounts, self.current = [], None

    def save(self):
        data = {"accounts": self.accounts, "current": self.current}
        ACCOUNTS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def add_offline(self, name: str):
        # 生成离线 UUID（和 Minecraft 一致：基于 "OfflinePlayer:<name>" 的 MD5）
        md5 = hashlib.md5(f"OfflinePlayer:{name}".encode()).hexdigest()
        uuid_str = f"{md5[:8]}-{md5[8:12]}-{md5[12:16]}-{md5[16:20]}-{md5[20:]}"

        acc = {
            "type": "offline",
            "name": name,
            "uuid": uuid_str,
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
