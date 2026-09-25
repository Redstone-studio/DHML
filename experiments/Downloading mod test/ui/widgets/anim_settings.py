"""
动画预设的持久化

存在配置目录的 setting.json 里（和 core/cache.py 的缓存目录是同一个地方）。
只存一个 key，所以文件长这样：

    {"animation_preset": "slide_up"}

读取一律容错：文件坏了、key 是老的、值认不出来 —— 都退回默认，不抛异常。
设置这种东西坏了不该让程序起不来。
"""
import json
import os
from pathlib import Path

from ui.widgets.anim_prefs import DEFAULT_PRESET, DEFAULT_SPEED, PRESETS, SPEEDS

SETTING_FILE_NAME = "setting.json"
_KEY = "animation_preset"
_SPEED_KEY = "animation_speed"
_cache = None
_speed_cache = None


def _config_dir() -> Path:
    """配置目录。刻意不 import core.config —— 那个模块是主项目的，
    这里只是个实验项目，别把两边绑在一起（而且导入它有副作用）"""
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        root = Path(base)
    else:
        root = Path.home() / ".config"
    return root / "MosslightModTest"


def _setting_path() -> Path:
    return _config_dir() / SETTING_FILE_NAME


def get_preset_key() -> str:
    """当前选的是哪个预设（读不出来就是默认）"""
    global _cache
    if _cache is not None:
        return _cache

    key = DEFAULT_PRESET
    try:
        raw = json.loads(_setting_path().read_bytes())
        if isinstance(raw, dict):
            candidate = str(raw.get(_KEY, ""))
            if candidate in PRESETS:
                key = candidate
    except (OSError, ValueError):
        pass            # 没存过 / 文件坏了：用默认

    _cache = key
    return key


def set_preset_key(key: str) -> bool:
    """切换预设并落盘。返回是否写成功（写不进去也不影响这次使用）"""
    global _cache
    if key not in PRESETS:
        return False
    _cache = key
    return _save()


def get_speed_key() -> str:
    """当前速度档位（读不出来就是标准）"""
    global _speed_cache
    if _speed_cache is not None:
        return _speed_cache

    key = DEFAULT_SPEED
    try:
        raw = json.loads(_setting_path().read_bytes())
        if isinstance(raw, dict):
            candidate = str(raw.get(_SPEED_KEY, ""))
            if candidate in SPEEDS:
                key = candidate
    except (OSError, ValueError):
        pass

    _speed_cache = key
    return key


def set_speed_key(key: str) -> bool:
    """切换速度档位并落盘"""
    global _speed_cache
    if key not in SPEEDS:
        return False
    _speed_cache = key
    return _save()


def _save() -> bool:
    """把两项设置一起写下去

    ⚠️ 必须两个键一起写：只写自己那个的话，另一个会被整份文件覆盖掉
    （改一下速度，预设就悄悄回到默认了）。
    """
    payload = {
        _KEY: get_preset_key(),
        _SPEED_KEY: get_speed_key(),
    }
    try:
        path = _setting_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError as e:
        print(f"[AnimPrefs] 设置写不进去（{e}），这次只在内存里生效")
        return False
