"""
在线玩家头像

服务器只给"名字"（Query）或"名字 + UUID"（SLP），皮肤得另外去取。
这里走公开的皮肤站，**带磁盘缓存**，并且网络拿不到时退回本地那 9 张默认皮肤。

## 三条规矩

**① 绝不在主线程联网。** 头像一次要取十几个，同步取就是十几个 RTT 的卡死
（另一个实验项目里为这张图卡过 2.4 秒，坑记着呢）。统一走线程池。

**② 一定要有本地兜底。** 皮肤站挂掉/断网时，头像位不能是空白 ——
退回 `ui/avatar.py` 那 9 张默认皮肤（按 UUID 挑，和游戏里的规则一致）。
这样"有没有网"只影响头像像不像，不影响能不能用。

**③ 缓存落盘。** 同一个玩家反复出现在列表里（刷新、重查）时不该重复下载。
缓存放配置目录，和主项目的做法一致。

## 用哪个皮肤站

| 服务 | 地址 | 说明 |
|---|---|---|
| mc-heads | `mc-heads.net/avatar/<名字或UUID>/<尺寸>` | 名字和 UUID 都吃，优先用它 |
| crafatar | `crafatar.com/avatars/<UUID>?size=<尺寸>` | 只吃 UUID，当备用 |
| minotar | `minotar.net/helm/<名字>/<尺寸>.png` | 名字兜底 |

⚠️ 这些都是**第三方服务**，可用性和限速都不归我们管，所以要有备用 + 缓存 + 本地兜底三层。
"""
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtGui import QPainter, QPainterPath, QPixmap
from PyQt6.QtCore import QRectF, Qt

from core.config import get_config_dir

CACHE_DIR_NAME = "avatars"
#: **单个服务**的超时。⚠️ 最坏情况是"三个服务挨个超时"，总耗时 = 3 × 这个值。
#: 实测 mc-heads 通常 <1s 就回，但偶尔会卡到十几秒（jeb_ 那次等了 10.5s，
#: 就是前两个服务拖掉的），所以这个值不能给太大。反正是在后台线程里取，
#: 期间卡片显示首字母占位，不会卡界面。
TIMEOUT = 4.0
#: 并发上限。别开太大 —— 皮肤站会限速，而且这是"顺带取一下"的东西
MAX_THREADS = 6

_HEADERS = {"User-Agent": "MosslightVerifyTest/0.1 (experiment)"}

#: 服务列表：(地址模板, 只能用 UUID 吗)
_SERVICES = (
    ("https://mc-heads.net/avatar/{key}/{size}", False),
    ("https://crafatar.com/avatars/{key}?size={size}&overlay", True),
    ("https://minotar.net/helm/{key}/{size}.png", False),
)


def cache_dir() -> Path:
    d = get_config_dir() / CACHE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_key(name: str, uuid: str) -> str:
    """缓存文件名用的 key：有 UUID 就用 UUID（更准），否则用名字"""
    raw = (uuid or "").replace("-", "").strip()
    if raw:
        return raw.lower()
    return "".join(c for c in (name or "") if c.isalnum() or c in "_-")[:32].lower()


def skin_cache_key(url: str) -> str:
    """皮肤按 **URL 的哈希** 缓存

    ⚠️ 不用角色名当 key：同一个角色换了皮肤，URL 会变（ALI 的纹理地址带哈希），
    按名字缓存的话会一直显示旧皮肤。
    """
    import hashlib
    return "skin_" + hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:20]


def cached_path(key: str, size: int) -> Path:
    return cache_dir() / f"{key}_{size}.png"


def read_cache(key: str, size: int):
    path = cached_path(key, size)
    try:
        if path.is_file() and path.stat().st_size > 0:
            return path.read_bytes()
    except OSError:
        pass
    return None


def write_cache(key: str, size: int, data: bytes):
    path = cached_path(key, size)
    try:
        # 先写临时文件再改名：避免"下到一半被读到"留下坏图
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError as e:
        print(f"[Avatar] 缓存写不进去（{e}）")


def fetch_remote(key: str, size: int, is_uuid: bool, timeout: float = TIMEOUT):
    """按服务顺序试，返回第一份拿到的图片字节；全失败返回 None"""
    import requests

    for template, uuid_only in _SERVICES:
        if uuid_only and not is_uuid:
            continue
        url = template.format(key=key, size=size)
        try:
            r = requests.get(url, headers=_HEADERS, timeout=timeout)
            if r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n":
                return r.content
        except Exception:
            continue
    return None


def round_pixmap(pixmap: QPixmap, radius_ratio: float = 0.28) -> QPixmap:
    """给头像切圆角

    ⚠️ 必须在**画的时候**裁：QSS 的 border-radius 只影响背景和边框，
    QLabel 里的图片该多尖还是多尖（`ui/avatar.py` 的注释里也写了这条）。
    """
    if pixmap is None or pixmap.isNull():
        return pixmap
    side = min(pixmap.width(), pixmap.height())
    out = QPixmap(pixmap.size())
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    radius = max(2.0, side * radius_ratio)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, pixmap.width(), pixmap.height()), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return out


class _Signals(QObject):
    ready = pyqtSignal(str, bytes)      # (缓存 key, png 字节) —— 已经是头像了
    skin_ready = pyqtSignal(str, str)   # (缓存 key, 皮肤文件的磁盘路径) —— 还是整张皮肤


class _Task(QRunnable):
    def __init__(self, key: str, size: int, is_uuid: bool, signals: _Signals):
        super().__init__()
        self.key, self.size, self.is_uuid, self.signals = key, size, is_uuid, signals
        self.setAutoDelete(True)

    def run(self):
        try:
            data = read_cache(self.key, self.size)
            if data is None:
                data = fetch_remote(self.key, self.size, self.is_uuid)
                if data is not None:
                    write_cache(self.key, self.size, data)
            if data:
                self.signals.ready.emit(self.key, data)
        except Exception as e:
            # 线程里抛异常会直接把整个进程带走，必须在这里兜住
            print(f"[Avatar] 取头像失败（{self.key}）：{type(e).__name__}: {e}")


class _SkinTask(QRunnable):
    """下载一张**皮肤**（不是头像）

    ALI 站点给的是 64×64 的皮肤本体，头像要自己从 (8,8) 抠。
    抠图放在**主线程**做（见 `face_pixmap`）—— 8×8 放大到 40×40 是微秒级，
    而 QPixmap 本来就不能在非 GUI 线程里用（QImage 可以，QPixmap 不行）。

    这里只负责"下载 + 落盘"，然后告诉主线程文件在哪。
    """

    def __init__(self, key: str, url: str, signals: _Signals, timeout: float):
        super().__init__()
        self.key, self.url, self.signals, self.timeout = key, url, signals, timeout
        self.setAutoDelete(True)

    def run(self):
        try:
            path = cached_path(self.key, 0)
            if path.is_file() and path.stat().st_size > 0:
                self.signals.skin_ready.emit(self.key, str(path))
                return
            import requests
            r = requests.get(self.url, headers=_HEADERS, timeout=self.timeout)
            if r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n":
                path.write_bytes(r.content)
                self.signals.skin_ready.emit(self.key, str(path))
        except Exception as e:
            print(f"[Avatar] 取皮肤失败（{self.url}）：{type(e).__name__}: {e}")


class AvatarPool(QObject):
    """头像下载调度器

    用法：
        pool = AvatarPool.instance()
        pool.ready.connect(self._on_avatar)   # (key, bytes)
        pool.request(name, uuid, 40)
    """

    ready = pyqtSignal(str, bytes)
    #: ALI 皮肤下载好了 —— 带的是**磁盘路径**，主线程自己抠脸（见 _SkinTask）
    skin_ready = pyqtSignal(str, str)

    _instance = None

    def __init__(self):
        super().__init__()
        self._signals = _Signals()
        # ⚠️ 必须接到 _on_ready 而不是直接接到 self.ready：
        # 直接转发的话 _memory / _inflight 永远不会更新 ——
        # 表现成"同一个头像只有第一次能取到，之后再也不发请求了"（_inflight 卡住）
        self._signals.ready.connect(self._on_ready)
        self._signals.skin_ready.connect(self._on_skin_ready)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(MAX_THREADS)
        self._memory = {}                 # key → bytes（本次运行内的缓存）
        self._inflight = set()
        self._lock = threading.Lock()

    @classmethod
    def instance(cls) -> "AvatarPool":
        if cls._instance is None:
            cls._instance = AvatarPool()
        return cls._instance

    def request(self, name: str, uuid: str = "", size: int = 40):
        """要一张头像。拿到之后会通过 ready 信号发回来（key 对得上就认领）"""
        key = cache_key(name, uuid)
        if not key:
            return
        with self._lock:
            data = self._memory.get(key)
            if data is not None:
                self.ready.emit(key, data)
                return
            if key in self._inflight:
                return                    # 同一张图正在取，别重复发请求
            self._inflight.add(key)
        self._pool.start(_Task(key, size, bool(uuid), self._signals))

    def request_skin(self, url: str, timeout: float = TIMEOUT):
        """要一张**皮肤**（ALI 站点给的 64×64 原图）

        拿到的走 `skin_ready(key, 磁盘路径)`，主线程再用
        `ui/avatar.py` 的 `face_pixmap` 抠出脸。
        """
        key = skin_cache_key(url)
        if not url:
            return ""
        with self._lock:
            if key in self._inflight:
                return key
            self._inflight.add(key)
        self._pool.start(_SkinTask(key, url, self._signals, timeout))
        return key

    def _on_ready(self, key: str, data: bytes):
        with self._lock:
            self._memory[key] = data
            self._inflight.discard(key)
        self.ready.emit(key, data)

    def _on_skin_ready(self, key: str, path: str):
        with self._lock:
            self._inflight.discard(key)
        self.skin_ready.emit(key, path)

    def has_memory(self, key: str) -> bool:
        return key in self._memory

    def memory(self, key: str):
        return self._memory.get(key)

    def wait_for_done(self, timeout_ms: int = 8000) -> bool:
        """等所有下载任务结束（**只给测试和关窗时用**，平时别调）"""
        return self._pool.waitForDone(timeout_ms)


def pixmap_from_bytes(data: bytes, size: int, dpr: float = 1.0):
    """字节 → 圆角 QPixmap"""
    pixmap = QPixmap()
    if not pixmap.loadFromData(data):
        return None
    side = max(1, int(round(size * max(1.0, dpr))))
    scaled = pixmap.scaled(side, side,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(max(1.0, dpr))
    return round_pixmap(scaled)
