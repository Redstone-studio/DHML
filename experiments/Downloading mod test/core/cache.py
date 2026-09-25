"""
缓存管理
- 内存缓存（程序运行时）
- 磁盘缓存（%APPDATA%/Mosslight/cache/）
"""
import os
import hashlib
from pathlib import Path


def get_cache_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / "Mosslight" / "cache" / "icons"
    d.mkdir(parents=True, exist_ok=True)
    return d


CACHE_DIR = get_cache_dir()


def _key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def load(url: str):
    """从磁盘读缓存，返回 bytes 或 None"""
    if not url:
        return None
    path = CACHE_DIR / f"{_key(url)}.bin"
    if path.exists():
        try:
            return path.read_bytes()
        except Exception:
            return None
    return None


def save(url: str, data: bytes):
    """保存到磁盘缓存"""
    if not url or not data:
        return
    path = CACHE_DIR / f"{_key(url)}.bin"
    try:
        path.write_bytes(data)
    except Exception:
        pass


def clear():
    """清空磁盘缓存"""
    for f in CACHE_DIR.glob("*.bin"):
        try:
            f.unlink()
        except Exception:
            pass
