"""配置读写（精简版）

主项目的 `core/config.py` 有便携模式、旧目录改名迁移、一堆别的键 ——
这里只要"配置目录 + 读写一个 json"，所以自己写一个小的，
**不复制主项目那份**（复制过来会带一堆这个实验用不到的依赖）。

`ui/avatar.py` 是从主项目直接拷来的，它 import 的是
`core.config.get_config_dir`，所以这个函数名必须一致。
"""
import json
import os
import sys
from pathlib import Path

from core.app_info import CONFIG_DIR_NAME

CONFIG_FILE_NAME = "config.json"

# 默认值。读不到/坏了就退回这里，和主项目一个套路
DEFAULTS = {
    "server_address": "",      # 验证服务器地址，形如 mc.example.com 或 host:25565
    "player_name": "",         # 要验证的玩家名
    "prefer_query": True,      # 优先用 Query（完整名单），退回 SLP（抽样）
    "timeout": 5.0,            # 单次查询超时（秒）
    "show_avatars": True,      # 显示头像
}


def _portable_requested() -> bool:
    """便携模式：启动器目录下放一个 portable.txt 就启用（和主项目同名同义）"""
    if os.environ.get("MOSS_VERIFY_PORTABLE"):
        return True
    return (Path(__file__).resolve().parents[1] / "portable.txt").is_file()


def _portable_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parents[1]
    return base / CONFIG_DIR_NAME


def _system_config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / CONFIG_DIR_NAME


def get_config_dir() -> Path:
    """配置目录（便携模式优先）"""
    if _portable_requested():
        target = _portable_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
            return target
        except OSError as e:
            print(f"[Config] {target} 写不进去（{e}），回退到系统配置目录")
    d = _system_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return get_config_dir() / CONFIG_FILE_NAME


def load_all() -> dict:
    """读整份配置（读不出来/坏了 → 空表，调用方各自回退默认）

    ⚠️ 只 catch `OSError` 和 `ValueError`，**不 catch Exception** ——
    写错一个 import 被吞掉的话，会表现成"设置永远不生效"，
    很难查（主项目踩过这个坑）。
    """
    try:
        raw = json.loads(config_path().read_bytes())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_all(data: dict) -> bool:
    try:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        return True
    except OSError as e:
        print(f"[Config] 写不进去（{e}）")
        return False


def get(key: str, fallback=None):
    """读一个键。没存过就取 DEFAULTS；DEFAULTS 里也没有就用 fallback"""
    value = load_all().get(key)
    if value is None:
        if key in DEFAULTS:
            return DEFAULTS[key]
        return fallback
    return value


def set_key(key: str, value) -> bool:
    """改一个键并写回 —— **先把整份读出来**，避免覆盖别的键"""
    data = load_all()
    data[key] = value
    return save_all(data)
