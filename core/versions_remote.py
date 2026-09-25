"""远程版本清单：拉取 / 缓存 / 分组（纯 Python，不 import Qt）

下载页第一页要的东西：最新正式版 + 最新预览版 + 四个分组（正式版 / 预览版 /
远古版 / 愚人节版）。数据来自官方的 `version_manifest_v2.json`（270 KB，916 个版本）。

## 缓存

270 KB 每次进页面都拉一次不合适，所以缓存到配置目录的 `manifest_cache.json`
（跟 `java_cache.json` 一个路数）。规则：

- 缓存**没过期**（默认 6 小时）→ 直接用，一次网络请求都不发
- 过期了 → 拉一次；**拉失败就用旧的**（`stale=True`），并让界面提示"这是缓存" ——
  断网时列表还能看，比一片空白强得多
- 缓存文件坏了 → 当没有，重新拉

## 愚人节版

⚠️ 清单里的 `type` **只有** release / snapshot / old_beta / old_alpha ——
**没有"愚人节"这一类**，只能靠自己的名单认（PCL 也是这么干的，名单见用户给的截图）。
两个坑：

- `20w14∞` 在清单里叫 **`20w14infinite`**（∞ 只是显示别名）
- `2.0` 官方清单里**根本没有**（PCL 特供下载），所以名单里要允许"清单里找不到"

分组的**判定顺序很重要**：愚人节优先 —— 它们大多是 `snapshot`，
先判 snapshot 的话会被算进"预览版"。
"""

import json
import os
import time
from pathlib import Path

# 清单地址。**元数据用官方优先**：实测（.dhml-tests/source_probe2.py）
# 官方 manifest 1.08 s、BMCLAPI 1.53 s，而且 BMCLAPI 那个会间歇性慢到 5 s。
# （下载*文件*那边是镜像优先，两边策略不同是有意的。）
MANIFEST_URLS = (
    ("official", "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"),
    ("bmclapi", "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json"),
)

CACHE_NAME = "manifest_cache.json"
CACHE_MAX_AGE = 6 * 3600          # 秒

TIMEOUT = 20
USER_AGENT = "Mosslight/0.5 (+https://github.com/kongxia114/Mosslight-Launcher)"

# 愚人节版本名单：(清单里的 id, 年份)。
# ⚠️ id 必须写**清单里的原名**；显示名要改就加在 APRIL_FOOLS_ALIAS 里。
APRIL_FOOLS = (
    ("26w14a", 2026),
    ("25w14craftmine", 2025),
    ("24w14potato", 2024),
    ("23w13a_or_b", 2023),
    ("22w13oneblockatatime", 2022),
    ("20w14infinite", 2020),
    ("3D Shareware v1.34", 2019),
    ("1.RV-Pre1", 2016),
    ("15w14a", 2015),
    ("2.0", 2013),          # 官方清单里没有，只有 PCL 这类启动器提供
)

# 显示名和清单 id 不一样的（就这一个）
APRIL_FOOLS_ALIAS = {"20w14infinite": "20w14∞"}

# 分组顺序 = 界面上的顺序。愚人节**放最后判定**，但判定时**最先**判（见模块说明）
GROUP_RELEASE = "正式版"
GROUP_SNAPSHOT = "预览版"
GROUP_OLD = "远古版"
GROUP_APRIL = "愚人节版"
GROUP_ORDER = (GROUP_RELEASE, GROUP_SNAPSHOT, GROUP_OLD, GROUP_APRIL)


def cache_path() -> Path:
    from core.config import get_config_dir
    return get_config_dir() / CACHE_NAME


# ---------- 拉取 ----------

def _fetch(url: str):
    import requests
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or "versions" not in data:
        raise ValueError("清单格式不对（没有 versions 字段）")
    return data


def _read_cache(path: Path):
    try:
        raw = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or "versions" not in raw:
        return None
    return raw


def _write_cache(path: Path, data: dict, fetched_at: float):
    payload = dict(data)
    payload["fetched_at"] = fetched_at
    tmp = path.parent / (path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        print(f"[Manifest] 缓存写不进去（{e}），下次还得重拉")


def load_manifest(force: bool = False, max_age: float = CACHE_MAX_AGE,
                  path: Path = None):
    """拿清单。返回 (data, info)：

        data  清单 dict（含 versions / latest），拿不到就是 None
        info  {"source": "cache"/"official"/"bmclapi"/None,
               "stale": bool, "age": 秒或 None, "error": 文本}

    `force=True` 跳过缓存（界面上的「刷新」按钮用它）。
    """
    path = Path(path) if path is not None else cache_path()
    cached = _read_cache(path)
    age = None
    if cached is not None:
        age = max(0.0, time.time() - float(cached.get("fetched_at", 0) or 0))
        if not force and age < max_age:
            return cached, {"source": "cache", "stale": False, "age": age, "error": ""}

    last_error = ""
    for name, url in MANIFEST_URLS:
        try:
            data = _fetch(url)
        except Exception as e:                                  # noqa: BLE001
            last_error = "%s: %s" % (name, e)
            continue
        _write_cache(path, data, time.time())
        return data, {"source": name, "stale": False, "age": 0.0, "error": ""}

    # 全挂了：有旧缓存就用旧的（断网时至少还能看列表）
    if cached is not None:
        return cached, {"source": "cache", "stale": True, "age": age,
                        "error": last_error}
    return None, {"source": None, "stale": False, "age": None, "error": last_error}


# ---------- 分组 ----------

def _entry(item: dict) -> dict:
    vid = item.get("id", "")
    return {
        "id": vid,
        "display_name": APRIL_FOOLS_ALIAS.get(vid, vid),
        "type": item.get("type", ""),
        "release_time": item.get("releaseTime", ""),
        "url": item.get("url", ""),
    }


def april_fools_years() -> dict:
    """{id: 年份}，给界面显示"2025 | …"那种前缀用"""
    return {vid: year for vid, year in APRIL_FOOLS}


def group_versions(data: dict) -> dict:
    """把清单分成界面上那四组 + 最新两个

    返回：
        {"latest_release": entry|None, "latest_snapshot": entry|None,
         "groups": [(组名, [entry, ...]), ...], "missing_april_fools": [id, ...]}
    """
    versions = (data or {}).get("versions") or []
    april = april_fools_years()

    groups = {name: [] for name in GROUP_ORDER}
    seen = set()
    for item in versions:
        vid = item.get("id", "")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        entry = _entry(item)
        if vid in april:
            groups[GROUP_APRIL].append(entry)
            continue
        kind = item.get("type", "")
        if kind == "release":
            groups[GROUP_RELEASE].append(entry)
        elif kind == "snapshot":
            groups[GROUP_SNAPSHOT].append(entry)
        elif kind in ("old_beta", "old_alpha"):
            groups[GROUP_OLD].append(entry)
        # 别的 type（以后新增的）先不放进任何组，免得列表里冒出没归类的项

    # 清单里没有的愚人节版本（比如 2.0）单独报出来，界面可以标"官方清单里没有"
    missing = [vid for vid, _year in APRIL_FOOLS if vid not in seen]

    latest = (data or {}).get("latest") or {}
    def find(vid):
        for item in versions:
            if item.get("id") == vid:
                return _entry(item)
        return None

    return {
        "latest_release": find(latest.get("release", "")),
        "latest_snapshot": find(latest.get("snapshot", "")),
        "groups": [(name, groups[name]) for name in GROUP_ORDER],
        "missing_april_fools": missing,
    }


def fetch_grouped(force: bool = False, path: Path = None):
    """一步到位：拿清单 + 分组。返回 (grouped|None, info)"""
    data, info = load_manifest(force=force, path=path)
    if data is None:
        return None, info
    return group_versions(data), info
