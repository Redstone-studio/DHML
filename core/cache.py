"""图标磁盘缓存（纯 Python，**不 import Qt**）

从 `experiments/Downloading mod test/core/cache.py` 搬过来，改了一处：

⚠️ **缓存目录跟着本项目的配置目录走**。实验那版写死 `%APPDATA%/Mosslight/cache`，
而实验项目自己的数据存在 `%APPDATA%/MosslightModTest/` —— 也就是说它那份缓存
和真正的启动器数据**是两个地方**。这里统一用 `core.config.get_config_dir()`，
便携模式下缓存就落在启动器旁边的 `Mosslight/` 里，跟 config.json 一致。

## 为什么用 md5(url) 当文件名

URL 里带 `/` `:` `?`，不能直接当文件名；而且 Modrinth 的图标 URL 很长。
md5 够用（这里只求"同一个 URL 稳定映射到同一个文件"，
**不是**安全用途，不需要 blake2/sha256 那种强度）。

## 为什么失败一律静默

图标是纯装饰：下不到就留个 "?" 占位，**绝不该因为一张图标让整页报错**。
所以读写都吞 OSError。

## 只缓存图标，不缓存大文件

mod 动辄几十 MB，全缓存是硬盘杀手（用户 2026-09 提醒过）。这里只存图标
（几十 KB 的 png）。元数据缓存（搜索页 JSON）目前**没做** ——
Modrinth 的搜索结果是实时的，缓存反而会让搜索结果过期。
"""

import hashlib
from pathlib import Path

from core.config import get_config_dir

# 解析出来的缓存目录（见 cache_dir 的说明）
_RESOLVED = None


def cache_dir() -> Path:
    """图标缓存目录（跟着配置目录走）

    ⚠️ **探测一次就缓存**：这个函数在每张图标上都会被调到两次（读一次写一次），
    每次都去问 `core.config`（那边有便携模式探测、还会 mkdir）太浪费。
    """
    global _RESOLVED
    if _RESOLVED is not None:
        return _RESOLVED

    for make in (_config_cache_dir, _temp_cache_dir):
        try:
            path = make()
            path.mkdir(parents=True, exist_ok=True)
            _RESOLVED = path
            return path
        except OSError as e:
            print("[Cache] 缓存目录建不了（%s）：%s" % (type(e).__name__, e))
            continue

    # 两级都建不出来（磁盘只读 / 全盘满）：退回用户主目录。
    # 这里**不再 mkdir** —— 连临时目录都建不出来就别再试了，
    # 让 save 去报 OSError（反正调用方会吞掉，图标只是装饰）
    _RESOLVED = Path.home()
    return _RESOLVED


def _config_cache_dir() -> Path:
    from core.config import get_config_dir
    return get_config_dir() / "cache" / "icons"


def _temp_cache_dir() -> Path:
    import tempfile
    return Path(tempfile.gettempdir()) / "Mosslight" / "icons"


def _key(url: str) -> str:
    return hashlib.md5(url.encode("utf-8", "ignore")).hexdigest()


def _path_for(url: str) -> Path:
    """某个 URL 的缓存文件路径

    ⚠️ 每次都确保目录在：`cache_dir()` 只在**第一次**解析时建目录，
    而缓存目录可能在中途被清掉（用户清理 / 系统清临时文件），
    那时候写就会 FileNotFoundError —— 于是"缓存悄悄失效、每次都重新下载"。
    多一次 mkdir（已存在时几乎零成本）换掉这一类难查的问题。
    """
    directory = cache_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return directory / ("%s.bin" % _key(url))


def load_icon(url: str):
    """读缓存，返回 bytes；没有/读不了返回 None"""
    if not url:
        return None
    try:
        path = _path_for(url)
        if path.is_file():
            return path.read_bytes()
    except OSError:
        return None
    return None


def save_icon(url: str, data: bytes) -> bool:
    """写缓存。返回是否写成功（写不进去不影响这次显示）

    ⚠️ **失败要说出来**：以前这里是 `except OSError: pass`，于是"图标一张都
    没缓存上"这种问题完全静默 —— 表现只是"每次进来都重新下载"，很难发现。
    图标本身是装饰（失败不算错），但至少要留一行线索。
    """
    if not url or not data:
        return False
    path = _path_for(url)
    try:
        path.write_bytes(data)
        return True
    except OSError as e:
        print("[Cache] 图标写不进缓存（%s）：%s" % (type(e).__name__, e))
        return False


def version_icon_dir() -> Path:
    """**版本图标**放哪儿：`<配置目录>/icons`

    ⚠️ 跟上面那个"mod 图标缓存"**不是一个地方**：
      · 缓存（`cache/icons/*.bin`）是"临时下载的、清掉也无所谓"
      · 版本图标（`icons/*.png`）是**用户的东西** —— 版本设置页能挑，
        清缓存绝不能把它删了（见 `ui/icons.custom_icon_path`）
    """
    folder = get_config_dir() / "icons"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder


def install_version_icon(version_id: str, url: str, timeout=(10, 20)) -> str:
    """把一张网络图存成某个版本的图标，返回文件名（失败返回空串）

    用途：装完整合包，把 Modrinth 上那个包的图标设成实例的图标
    （PCL 也干这事 —— 它把包图标复制成 `PCL/Logo.png`）。

    我们的约定（`ui/icons.custom_icon_path`）：图放 `<配置目录>/icons/<名字>.png`，
    再给 `versions.json` 里那个版本写一条 `icon` 覆盖项。

    ⚠️ **失败一律吞掉**：图标是锦上添花，绝不能因为它让整包装不上 ——
    但也别静默，留一行 print。
    """
    version_id = str(version_id or "").strip()
    url = str(url or "").strip()
    if not version_id or not url:
        return ""

    import requests
    from core.download import USER_AGENT
    from core.version_settings import version_settings

    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT},
                         timeout=timeout)
        r.raise_for_status()
        data = r.content
    except Exception as e:                      # noqa: BLE001
        print("[Cache] 版本图标下载失败（%s）：%s" % (type(e).__name__, e))
        return ""
    if not data:
        return ""

    # 存成 png 扩展名：Qt 是按**内容**认格式的，扩展名只是给我们自己看的
    stem = "".join(c for c in version_id if c not in '\\/:*?"<>|').strip()
    stem = (stem or "modpack")[:48]
    name = "%s.png" % stem
    try:
        path = version_icon_dir() / name
        path.write_bytes(data)
    except OSError as e:
        print("[Cache] 版本图标写不进去：%s" % e)
        return ""

    try:
        cur = dict(version_settings.get(version_id))
        cur["icon"] = name
        version_settings.update(version_id, cur)
    except Exception as e:                      # noqa: BLE001
        print("[Cache] 版本图标记不进 versions.json：%s" % e)
        return ""
    return name


def clear_icons() -> int:
    """清空图标缓存，返回删了几个（设置页以后可以挂这个）

    ⚠️ `glob("*.bin")` 返回的是快照列表，所以边遍历边删是安全的。
    **不要**改成"边生成边删"的惰性遍历（比如自己在 iterdir() 的生成器上删）——
    POSIX 上删掉正在遍历的目录项会让枚举跳过下一个文件，删一半留一半。
    """
    removed = 0
    try:
        candidates = list(cache_dir().glob("*.bin"))
    except OSError:
        return 0
    for f in candidates:
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def forget_dir():
    """忘掉解析出来的目录（测试用：沙箱换了目录要重新解析）"""
    global _RESOLVED
    _RESOLVED = None
