"""
下载核心：任务模型 + 后台 worker + 管理器

## 为什么要做成"任务列表"而不是"下载一个文件"

现在只下一个 mod 文件，但**以后整合包是几十个文件并发**。所以这里从一开始就按
多任务设计：

    DownloadManager          管一队任务、按线程数并发跑、汇总总进度
      └── DownloadTask       一个文件：状态 / 已收字节 / 速度 / 错误
            └── DownloadWorker（QThread）真正收数据

## 进度怎么来的

`Content-Length` 有就用它算百分比；没有（有些 CDN 不给）就只报"已收多少 MB"，
百分比留空 —— 别拿 0 当分母算出个假的 100%。

## 速度怎么算

每收到一块就累加字节，同时按**滑动窗口**算速度（最近 `SPEED_WINDOW` 秒内
收了多少）。不能直接"总字节 / 总耗时"：那样一开始慢一下，后面一直显示低值。
"""
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from PyQt6.QtCore import QThread, pyqtSignal

HEADERS = {"User-Agent": "Mosslight-Launcher/1.0"}

# 一次收多大。64KB 是吞吐和响应性的折中：太小了回调太频繁，太大了取消不灵敏
CHUNK = 64 * 1024
# 算速度用的滑动窗口（秒）
SPEED_WINDOW = 2.0
TIMEOUT = 30

# 任务状态
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

_ACTIVE = (PENDING, RUNNING)


def human_size(n) -> str:
    """字节 → 人看的大小"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def human_speed(n) -> str:
    return human_size(n) + "/s" if n else "—"


# eq=False：任务要按**身份**比较（同一个对象才是同一个任务），
# 而且管理器拿它当字典键去映射 worker —— 默认的 dataclass 会生成 __eq__、
# 把 __hash__ 置空，直接用会报 "unhashable type"。
@dataclass(eq=False)
class DownloadTask:
    """一个待下载的文件"""
    url: str
    filename: str
    save_dir: Path
    size: int = 0                 # 服务端给的大小（可能为 0 = 未知）
    title: str = ""               # 界面上显示的名字（默认用 filename）
    kind: str = "Mod"             # Mod / 整合包 / 资源包…（界面上的前缀）

    # ---------- 运行状态 ----------
    state: str = PENDING
    done_bytes: int = 0
    speed: float = 0.0            # 字节/秒
    error: str = ""
    _samples: list = field(default_factory=list, repr=False)
    _started_at: float = field(default=None, repr=False)

    def __post_init__(self):
        self.save_dir = Path(self.save_dir)
        if not self.title:
            self.title = self.filename

    @property
    def path(self) -> Path:
        return self.save_dir / self.filename

    @property
    def active(self) -> bool:
        return self.state in _ACTIVE

    @property
    def total(self) -> int:
        """总大小：优先用服务端给的，没有就用"已收 + 预估" """
        return self.size or self.done_bytes

    @property
    def percent(self):
        """0~100；总大小未知时返回 None（界面显示"不确定"）"""
        if self.size > 0:
            return min(100.0, self.done_bytes / self.size * 100.0)
        return None

    # ---------- 速度 ----------
    def note_bytes(self, amount: int):
        self.done_bytes += amount
        now = time.monotonic()
        if self._started_at is None:
            self._started_at = now
        self._samples.append((now, amount))
        # 丢掉窗口外的
        cutoff = now - SPEED_WINDOW
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.pop(0)
        if len(self._samples) >= 2:
            span = self._samples[-1][0] - self._samples[0][0]
            got = sum(a for _, a in self._samples[1:])
            if span > 0:
                self.speed = got / span
                return
        # 窗口里样本还不够（刚开始那一下）：用"总字节 / 总耗时"兜底，
        # 否则前一两秒速度一直显示 "—"，看着像没在下
        elapsed = now - (self._started_at or now)
        self.speed = (self.done_bytes / elapsed) if elapsed > 0.05 else 0.0

    def status_text(self) -> str:
        if self.state == DONE:
            return "完成"
        if self.state == FAILED:
            return f"失败：{self.error}"
        if self.state == CANCELLED:
            return "已取消"
        if self.state == PENDING:
            return "等待中"
        return human_size(self.done_bytes) + (
            f" / {human_size(self.size)}" if self.size else "")


class DownloadWorker(QThread):
    """收一个文件

    ⚠️ 取消是**靠标志位**，不是 terminate()：terminate 会把线程硬砍在
    `f.write()` 中间，留下半个文件。收到标志后我们自己跳出循环、
    把 .part 删掉，干净收场。
    """
    progressed = pyqtSignal()
    finished_task = pyqtSignal(str)     # state

    def __init__(self, task: DownloadTask, cancel_event, parent=None):
        super().__init__(parent)
        self.task = task
        self.cancel = cancel_event

    def run(self):
        task = self.task
        temp = task.path.with_suffix(task.path.suffix + ".part")
        try:
            task.save_dir.mkdir(parents=True, exist_ok=True)
            with requests.get(task.url, headers=HEADERS, stream=True,
                              timeout=TIMEOUT) as r:
                r.raise_for_status()
                length = r.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > 0:
                    task.size = int(length)
                with open(temp, "wb") as f:
                    for chunk in r.iter_content(CHUNK):
                        if self.cancel.is_set():
                            raise _Cancelled()
                        if not chunk:
                            continue
                        f.write(chunk)
                        task.note_bytes(len(chunk))
                        self.progressed.emit()
            if self.cancel.is_set():
                raise _Cancelled()
            # 原子改名：中途失败不会留下半个文件骗过后面的检查
            temp.replace(task.path)
            task.state = DONE
        except _Cancelled:
            task.state = CANCELLED
            _quiet_unlink(temp)
        except requests.RequestException as e:
            task.state = FAILED
            task.error = _short_error(e)
            _quiet_unlink(temp)
        except OSError as e:
            task.state = FAILED
            task.error = f"写入失败：{e.strerror or e}"
            _quiet_unlink(temp)
        finally:
            task.speed = 0.0
            self.finished_task.emit(task.state)


class _Cancelled(Exception):
    """内部用：让取消能从深层跳出到统一的清理逻辑"""


def _quiet_unlink(path: Path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _short_error(e) -> str:
    """把 requests 的长异常压成一句人话"""
    if isinstance(e, requests.Timeout):
        return "超时"
    if isinstance(e, requests.ConnectionError):
        return "连接失败（网络或镜像不可用）"
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    return str(e)[:80]


class DownloadManager:
    """管一队下载任务，按线程数并发跑

    对外只需要：
        add(task) / start() / cancel_all() / progress() / active_count
    """

    def __init__(self, parent=None, on_update=None):
        self._tasks: list[DownloadTask] = []
        self._cancel = None
        self._workers = {}
        self._parent = parent
        self._on_update = on_update
        self._max_workers = 1

    # ---------- 任务 ----------

    def add(self, task: DownloadTask):
        self._tasks.append(task)

    def tasks(self) -> list:
        return list(self._tasks)

    def set_max_workers(self, n: int):
        self._max_workers = max(1, int(n))

    # ---------- 控制 ----------

    def start(self):
        """开始（或**重新**开始）调度

        ⚠️ 已经取消过的话要换一个新的取消标志：
        原来只在 `_cancel is None` 时建，所以用户点过一次"取消下载"之后，
        这个事件一直是置位的 —— 后面再排队的新任务会被 worker 立刻判定成
        "已取消"，一个都下不动（实测踩过）。
        """
        if self._cancel is not None and self._cancel.is_set():
            self._cancel = threading.Event()
        elif self._cancel is None:
            self._cancel = threading.Event()
        self._pump()

    def cancel_all(self):
        """取消所有没下完的（正在下的会自己收尾，不会留半个文件）"""
        if self._cancel is not None:
            self._cancel.set()
        for task in self._tasks:
            if task.active:
                task.state = CANCELLED

    def wait_all(self, timeout_ms: int = 2500) -> bool:
        """等所有 worker 真的退出，全部退完返回 True

        ⚠️ `cancel_all()` 只是把取消标志置位，worker 得等手上那块 64KB
        写完、把 .part 删掉才会返回 —— 是异步的。窗口如果紧接着被销毁，
        QThread 就在"还在运行"的状态下被析构，Qt 会报
        `QThread: Destroyed while thread is still running`，运气不好直接崩。
        所以关窗前要等一下（有上限，别把界面卡死）。
        """
        deadline = time.monotonic() + timeout_ms / 1000.0
        for worker in list(self._workers.values()):
            left_ms = int(max(0.0, deadline - time.monotonic()) * 1000)
            if not worker.wait(left_ms):
                return False
        self._workers.clear()
        return True

    # ---------- 汇总 ----------

    def active_count(self) -> int:
        return sum(1 for t in self._tasks if t.active)

    def finished_count(self) -> int:
        return sum(1 for t in self._tasks if t.state in (DONE, FAILED, CANCELLED))

    def progress(self):
        """(已完成字节, 总字节, 百分比或 None, 合计速度)"""
        got = sum(t.done_bytes for t in self._tasks)
        total = sum(t.size for t in self._tasks)
        speed = sum(t.speed for t in self._tasks)
        percent = (got / total * 100.0) if total > 0 else None
        return got, total, percent, speed

    # ---------- 调度 ----------

    def _pump(self):
        """把还没跑的任务塞进 worker，直到占满线程数"""
        if self._cancel is None:
            self._cancel = threading.Event()

        # 回收已经结束的 worker
        for task, worker in list(self._workers.items()):
            if worker.isFinished():
                self._workers.pop(task, None)

        while len(self._workers) < self._max_workers:
            nxt = next((t for t in self._tasks if t.state == PENDING), None)
            if nxt is None:
                break
            nxt.state = RUNNING
            worker = DownloadWorker(nxt, self._cancel, self._parent)
            worker.progressed.connect(self._notify)
            worker.finished_task.connect(lambda _s: self._on_worker_done())
            self._workers[nxt] = worker
            worker.start()

    def _on_worker_done(self):
        self._notify()
        self._pump()

    def _notify(self):
        if self._on_update is not None:
            self._on_update()
