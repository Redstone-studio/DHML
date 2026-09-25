"""下载引擎（纯 Python，**不 import Qt**）

这是从两个实验合并来的（2026-09）：

- **安装编排 / 单文件下载 / 镜像映射**：`experiments/MC_LuncherTEST-3.0/core/download.py`
- **每字节进度 / 滑动窗口速度 / 取消**：`experiments/Downloading mod test/core/download.py`

## 为什么进度必须按字节

3.0 那边只报 `(done, total, failed)` 文件数 —— 下 client.jar（几十 MB）时
进度条会一动不动，用户以为卡死了。现在每个任务自己累计已收字节，
管理器汇总出"总进度 + 合计速度"。

## 为什么不用 QThread

Qt 的规矩：**销毁一个还在跑的 QThread 会让 Qt 直接终止进程**（我们已经被
`QThread: Destroyed while thread is still running` 坑过一次）。
这里用**普通 Python 线程 + 线程池**，UI 那边用 QTimer 定时取 `snapshot()`，
从根上避开这一类崩溃：线程的生死归我们自己管，不需要 Qt 参与。

## 源：镜像优先、官方兜底

所有下载地址先过 `mirror_url()` 换成 BMCLAPI，失败再回原始地址（跟 3.0 一致）。
⚠️ 每条映射都该用 `.dhml-tests/source_probe*.py` 实测过再往里加 ——
mirror 猜错不会崩，但会白等一次超时。
"""

import hashlib
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

# 算速度用的滑动窗口（秒）。
# ⚠️ 注意窗口有**两处**用：单个任务自己的速度（DownloadTask.note_bytes），
# 和整个管理器的总速度（DownloadManager._aggregate_speed）。
# 界面上的"下载速度"读的是后者，见 _aggregate_speed 里的说明。
SPEED_WINDOW = 3.0
CHUNK = 64 * 1024
CONNECT_TIMEOUT = 15          # 连接超时
READ_TIMEOUT = 30             # 每读一块的超时
# 镜像熔断：BMCLAPI 抽风时（实测过一次清单接口读超时），
# 如果每个文件都"先试镜像、等超时、再回退官方"，764 个文件能拖到几十分钟，
# 用户看到的就是"进度条卡住不动"。所以连续失败几次就先把镜像摘掉一段时间，
# 冷却结束再放回来试 —— 镜像恢复了也能自动用上。
MIRROR_TRIP_AFTER = 3         # 连续失败几次就熔断
MIRROR_COOLDOWN = 60.0        # 熔断后多久再试镜像（秒）

# 失败重试：同一个文件最多再试几次、每次之前等多久。
# 网络抖动（读超时 / 连接被重置 / 服务器 5xx）非常常见，再试一次基本就过了。
RETRY_TIMES = 2               # 额外重试次数（总计 1 + 2 次）
RETRY_BACKOFF = 0.5           # 第 n 次重试前等 RETRY_BACKOFF * n 秒（线性退避）

# 任务状态。界面按它上色 / 分类，所以别在别处再手写一份字符串
# （写错了不会报错，只会让"失败"显示成普通灰色，很难发现）。
STATE_WAITING = "等待"
STATE_RUNNING = "下载中"
STATE_DONE = "完成"
STATE_FAILED = "失败"
STATE_CANCELLED = "取消"
USER_AGENT = "Mosslight/0.5 (+https://github.com/kongxia114/Mosslight-Launcher)"

# ---------- 镜像映射 ----------
#
# （官方前缀, 镜像前缀）。顺序有意义：长的、更具体的放前面。
# 实测记录（2026-09，见 .dhml-tests/source_probe2.py）：
#   libraries.minecraft.net/<p>            → BMCLAPI /maven/<p>        ✅ 测过
#   resources.download.minecraft.net/<a>/<h> → BMCLAPI /assets/<a>/<h>  ✅ 同理
#   piston-data / piston-meta              → BMCLAPI 同路径            ⚠️ 没实测，
#       失败会自动回退到官方，所以先留着
MIRROR_PAIRS = (
    ("https://libraries.minecraft.net", "https://bmclapi2.bangbang93.com/maven"),
    ("https://resources.download.minecraft.net",
     "https://bmclapi2.bangbang93.com/assets"),
    ("https://piston-data.mojang.com", "https://bmclapi2.bangbang93.com"),
    ("https://piston-meta.mojang.com", "https://bmclapi2.bangbang93.com"),
    ("https://launcher.mojang.com", "https://bmclapi2.bangbang93.com"),
    ("https://launchermeta.mojang.com", "https://bmclapi2.bangbang93.com"),
    ("https://maven.minecraftforge.net", "https://bmclapi2.bangbang93.com/maven"),
    ("https://maven.neoforged.net", "https://bmclapi2.bangbang93.com/maven"),
)


def mirror_url(url: str, enabled: bool = True) -> str:
    """把官方地址换成镜像地址；换不了/关掉镜像就原样返回"""
    if not url or not enabled:
        return url
    for official, mirror in MIRROR_PAIRS:
        if url.startswith(official):
            return mirror + url[len(official):]
    return url


def human_size(n) -> str:
    """1234567 → "1.18 MB" """
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f %s" % (n, unit) if unit == "B" else "%.2f %s" % (n, unit)
        n /= 1024
    return "%.2f GB" % n


def human_speed(n) -> str:
    """字节/秒 → "1.2 MB/s"；速度还是 0 时给"—"（免得显示 "0 B/s" 像卡住了）"""
    return "—" if not n else human_size(n) + "/s"


def sha1_file(path: Path, cancel: threading.Event = None) -> str:
    """算文件的 sha1（取消时抛 _Cancelled）"""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(256 * 1024), b""):
            if cancel is not None and cancel.is_set():
                raise _Cancelled()
            h.update(block)
    return h.hexdigest()


class _Cancelled(Exception):
    """用户点了取消（内部用，不让它冒到界面上当错误）"""


class DownloadTask:
    """一个待下载的文件：状态 / 已收字节 / 速度 / 错误"""

    def __init__(self, url: str, dest, sha1: str = "", size: int = 0,
                 label: str = "", kind: str = ""):
        self.url = url
        self.dest = Path(dest)
        self.sha1 = (sha1 or "").lower()
        self.size = int(size or 0)
        self.label = label or self.dest.name
        # 界面上显示成 `[库] xxx.jar` 那种前缀（移植自实验的 `[Mod] xxx`）
        self.kind = kind or ""

        self.done_bytes = 0
        self.total = self.size          # 服务器给了 Content-Length 就用它
        self.speed = 0.0
        self.error = ""
        self.state = "等待"             # 等待 / 下载中 / 完成 / 失败 / 取消
        self.skipped = False            # 本地已有且校验通过
        self._samples = []              # [(时间, 累计字节)] 用来算滑动窗口速度
        self._started_at = None         # 第一次收到字节的时间（兜底速度要用）
        self._cancel = threading.Event()

    # ---------- 对外 ----------

    @property
    def active(self) -> bool:
        return self.state == "下载中"

    @property
    def finished(self) -> bool:
        return self.state in ("完成", "失败", "取消")

    @property
    def percent(self):
        """0.0~1.0；总大小未知时返回 None（界面用不确定进度条）"""
        if self.total > 0:
            return min(1.0, self.done_bytes / self.total)
        return None

    def cancel(self):
        self._cancel.set()

    def status_text(self) -> str:
        if self.skipped:
            return "已存在"
        if self.state == "下载中":
            pct = "%.0f%%" % (self.percent * 100) if self.percent is not None else "?" 
            return "%s  %s  %s" % (pct, human_size(self.done_bytes), human_speed(self.speed))
        if self.state == "完成":
            return "完成  %s" % human_size(self.done_bytes)
        if self.error:
            return self.error
        return self.state

    def note_bytes(self, amount: int):
        """收了一块：累计并且更新速度（最近 SPEED_WINDOW 秒的滑动窗口）"""
        self.done_bytes += amount
        now = time.monotonic()
        if self._started_at is None:
            self._started_at = now
        self._samples.append((now, self.done_bytes))
        cutoff = now - SPEED_WINDOW
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.pop(0)
        span = self._samples[-1][0] - self._samples[0][0]
        if span >= 0.25:
            self.speed = (self._samples[-1][1] - self._samples[0][1]) / span
        else:
            # 窗口还没建立起来（刚开始那一两下）：用"总字节 / 总耗时"兜底，
            # 否则前一两秒速度一直显示"—"，看着像没在下。
            # ⚠️ `_started_at` 以前根本没赋值过，getattr 每次都拿到 now，
            # 于是 elapsed=0 → 这里恒返回 0.0。现在在 __init__ / 上面补上了。
            elapsed = now - (self._started_at or now)
            self.speed = (self.done_bytes / elapsed) if elapsed > 0.05 else 0.0


def _is_not_found(exc: Exception) -> bool:
    """404：这个源上就没有这个文件。换源有用，重试没用"""
    return (isinstance(exc, requests.HTTPError) and exc.response is not None
            and exc.response.status_code == 404)


def _backoff(seconds: float, task):
    """等一会儿再重试。

    ⚠️ 拆成小段睡：直接 `time.sleep(2)` 的话用户点了取消要等两秒才停，
    界面上会像卡住。被取消要立刻抛，别把"取消"变成"失败"。
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if task._cancel.is_set():
            raise _Cancelled()
        time.sleep(0.05)


def short_error(exc: Exception) -> str:
    """把网络异常翻成人话（别把 traceback 甩给用户）"""
    if isinstance(exc, _Cancelled):
        return "已取消"
    if isinstance(exc, requests.Timeout):
        return "连接超时"
    if isinstance(exc, requests.ConnectionError):
        return "网络连不上"
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        return "服务器返回 %d" % code
    return "%s: %s" % (type(exc).__name__, exc)


class DownloadManager:
    """一队下载任务，用普通线程池并发跑

    ⚠️ 所有对外方法（snapshot / cancel_all）都只读或只用 Event，
    真正改状态的是工作线程 —— UI 单线程取快照就够了。
    """

    def __init__(self, max_workers: int = 16, use_mirror: bool = True,
                 on_done=None):
        self.max_workers = max(1, int(max_workers))
        self.use_mirror = use_mirror
        self.on_done = on_done          # 全部跑完时回调（参数：失败数）
        self.tasks = []
        self._dest_index = {}           # 目标路径 → 任务，用来去重（见 add）
        self._lock = threading.Lock()
        self._pool = None
        self._started = False
        # 已经提交给线程池的任务（按 id()）—— 见 start() 的说明：
        # 靠它才能支持"中途 add() 新任务再 start()"
        self._submitted = set()
        # 镜像熔断状态（工作线程会写，所以要加锁）
        self._mirror_fails = 0
        self._mirror_off_until = 0.0
        # 总速度用的滑动窗口：[（时刻, 已下字节合计）]。见 _aggregate_speed
        self._speed_samples = deque()

    # ---------- 加任务 ----------

    def add(self, url: str, dest, sha1: str = "", size: int = 0, label: str = "",
            kind: str = "") -> DownloadTask:
        """加一个任务；**同一个目标文件只留一个任务**

        ⚠️ 去重不是洁癖，是必须的：版本 JSON 里同一个库真的会列两遍
        （rd-132211 的 net.java.jinput:jinput-platform:2.0.5 就是两次），
        两个任务写的是**同一个 .tmp**、最后又各自 os.replace 到同一个目标，
        结果就是 Windows 上 WinError 32「文件正被另一个程序使用」，
        或者两个线程往同一个 tmp 里交错写、内容悄悄坏掉。
        真机端到端测试就是这么抓到的那一条失败。
        """
        key = str(Path(dest))
        with self._lock:
            if key in self._dest_index:
                return self._dest_index[key]
            task = DownloadTask(url, dest, sha1, size, label, kind)
            self.tasks.append(task)
            self._dest_index[key] = task
            return task

    def add_all(self, items) -> list:
        """items: [(url, dest, sha1, size, label), ...] 或 dict 列表"""
        out = []
        for it in items:
            if isinstance(it, dict):
                out.append(self.add(it["url"], it["path"], it.get("sha1", ""),
                                    it.get("size", 0), it.get("label", ""),
                                    it.get("kind", "")))
            else:
                out.append(self.add(*it))
        return out

    # ---------- 跑 ----------

    def start(self):
        """开始下载**所有还没提交过的任务**

        ⚠️ 这里**不能**写成 `if self._started: return`（原来就是那样）。
        那样的话"一批任务"这个假设被写死了：第一次 start 之后再 `add()` 进来的
        新任务**永远不会被提交**，界面上的表现是它们全都停在「等待」、
        进度条再也不动，而且**不报任何错**。
        实测（2026-09，做整合包安装时踩到）：`.mrpack` 自己一批、里面的文件
        又一批，第二批就卡死在「等待」。
        `_submitted` 记住哪些任务已经提交过，所以反复调 start() 是安全的、
        而且新加的任务一定能跑起来。
        """
        self._started = True
        pending = [t for t in self.tasks if id(t) not in self._submitted]
        if not pending:
            return
        self._launch(pending)
        threading.Thread(target=self._watch, daemon=True).start()

    def _launch(self, tasks):
        """把给定的任务丢进一个新线程池（start 和 retry_failed 都用它）"""
        self._pool = ThreadPoolExecutor(max_workers=self.max_workers)
        for task in tasks:
            self._submitted.add(id(task))
            self._pool.submit(self._run_one, task)

    def retry_failed(self) -> int:
        """把**失败的那些**任务重置后再跑一遍；返回重试了几个

        为什么不整份清单重跑一遍：几千个文件重新校验 sha1 很慢，
        而用户要的只是把那几个失败的下回来。
        单个文件内部本来就还有 RETRY_TIMES 次重试，这个是"全都试完还是失败"
        之后用户手动再来一轮（比如刚才网络确实断了，现在恢复）。

        可以反复调；没有失败的任务时返回 0、什么都不做。
        """
        todo = [t for t in self.tasks if t.state == STATE_FAILED]
        if not todo:
            return 0
        for task in todo:
            task.state = STATE_WAITING
            task.error = ""
            task.done_bytes = 0
            task.speed = 0.0
            task.skipped = False
            task._samples.clear()
            task._cancel.clear()
            task._started_at = None
            # ⚠️ 从"已提交"里摘掉：`_launch()` 就是按这个集合跳过已提交任务的，
            # 不摘的话重试的任务会被当成"早就提交过"而**一个都不重跑**
            self._submitted.discard(id(task))
        with self._lock:
            # 速度窗口清掉：不清的话下一次快照会把"上次那批字节"也算进变化量，
            # 冒出一个假的速度峰值
            self._speed_samples.clear()
        self._started = True
        self._launch(todo)
        threading.Thread(target=self._watch, daemon=True).start()
        return len(todo)

    def _watch(self):
        if self._pool is not None:
            self._pool.shutdown(wait=True)      # 等所有任务跑完（不阻塞 UI）
        failed = sum(1 for t in self.tasks if t.state == "失败")
        if self.on_done is not None:
            try:
                self.on_done(failed)
            except Exception:                   # 回调出错不该带走整个下载
                pass

    def _run_one(self, task: DownloadTask):
        task._started_at = time.monotonic()
        try:
            self._run_task(task)
        except _Cancelled:
            task.state = "取消"
            _quiet_unlink(_tmp_of(task.dest))
        except Exception as e:                  # noqa: BLE001 —— 单个失败不影响别的
            task.state = "失败"
            task.error = short_error(e)
            _quiet_unlink(_tmp_of(task.dest))

    def _run_task(self, task: DownloadTask):
        dest = task.dest

        # 本地已有且校验通过 → 直接跳过（增量下载的基础）
        if dest.is_file():
            if not task.sha1:
                task.skipped = True
                task.done_bytes = dest.stat().st_size
                task.total = task.total or task.done_bytes
                task.state = "完成"
                return
            if sha1_file(dest, task._cancel) == task.sha1:
                task.skipped = True
                task.done_bytes = dest.stat().st_size
                task.total = task.total or task.done_bytes
                task.state = "完成"
                return

        dest.parent.mkdir(parents=True, exist_ok=True)

        # 失败重试。最常见的失败根本不是"这个文件下不到"，而是网络抖动：
        # 读超时、连接被重置、服务器 5xx —— 隔一会儿再试一次基本就过了。
        # 所以每个文件额外再试 RETRY_TIMES 次（每次都是"镜像 → 官方"整轮重来）。
        # 404 例外：那是源上本来就没有，换源有用、重试没用，直接放弃。
        last = None
        for attempt in range(RETRY_TIMES + 1):
            if task._cancel.is_set():
                raise _Cancelled()
            last = self._try_urls(task)
            if last is None:
                return                              # 成功
            if task._cancel.is_set():
                raise _Cancelled()
            if _is_not_found(last):
                break
            if attempt < RETRY_TIMES:
                _backoff(RETRY_BACKOFF * (attempt + 1), task)
        raise last if last is not None else RuntimeError("没有可用的下载源")

    def _try_urls(self, task: DownloadTask):
        """按「镜像 → 官方」试**一轮**；成功返回 None，全挂了返回最后一个异常"""
        mirrored = mirror_url(task.url, self._mirror_usable())
        urls = [mirrored] + ([task.url] if mirrored != task.url else [])
        is_mirror = mirrored != task.url      # 第一个地址是不是镜像

        last = None
        for idx, url in enumerate(urls):
            if task._cancel.is_set():
                raise _Cancelled()
            first_is_mirror = idx == 0 and is_mirror
            try:
                self._fetch(task, url)
                if first_is_mirror:
                    self._note_mirror(True)
                return None
            except _Cancelled:
                raise
            except requests.HTTPError as e:
                last = e
                # 404 说明这个源没有这个文件 → 立刻换下一个源。
                # 这不算镜像的错，不该记进熔断计数。
                if _is_not_found(e):
                    continue
                if first_is_mirror:
                    self._note_mirror(False)
            except Exception as e:              # noqa: BLE001
                last = e
                if first_is_mirror:
                    self._note_mirror(False)
        return last

    # ---------- 镜像熔断 ----------

    def _mirror_usable(self) -> bool:
        """镜像现在能不能用（关掉了 / 正在熔断冷却中 → 不用）"""
        if not self.use_mirror:
            return False
        return time.monotonic() >= self._mirror_off_until

    def _note_mirror(self, ok: bool):
        """记一次镜像的成功/失败；连续失败够了就熔断一段时间

        ⚠️ 关键：熔断的是"再试镜像"这件事，不是下载本身 ——
        失败的文件照样会走官方源下下来，用户不会因此少文件。
        """
        with self._lock:
            if ok:
                self._mirror_fails = 0
                return
            self._mirror_fails += 1
            if self._mirror_fails >= MIRROR_TRIP_AFTER:
                self._mirror_fails = 0
                self._mirror_off_until = time.monotonic() + MIRROR_COOLDOWN

    def _fetch(self, task: DownloadTask, url: str):
        """真正收数据：.tmp 写、sha1 校验、原子改名"""
        tmp = _tmp_of(task.dest)
        task.state = "下载中"
        task.done_bytes = 0
        with requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True,
                          timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
            r.raise_for_status()
            length = r.headers.get("Content-Length")
            if length and length.isdigit():
                task.total = int(length)
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    if task._cancel.is_set():
                        raise _Cancelled()
                    if not chunk:
                        continue
                    f.write(chunk)
                    task.note_bytes(len(chunk))

        if task.sha1:
            actual = sha1_file(tmp, task._cancel)
            if actual != task.sha1:
                _quiet_unlink(tmp)
                raise ValueError("SHA1 不符（下载到的东西不对）")

        os.replace(tmp, task.dest)              # 原子：不会留半个文件
        task.state = "完成"

    # ---------- 给界面看 ----------

    def snapshot(self) -> dict:
        """界面每 200ms 取一次

        ⚠️ 严格说它现在**不是只读**了：为了算总速度，这里会往
        `_speed_samples` 里记一笔（加了锁，很快）。之所以这么做，
        是因为"速度"天然需要两次采样求差，而界面正好是唯一的定时采样者。
        """
        got = sum(t.done_bytes for t in self.tasks)
        total = sum(t.total for t in self.tasks)
        # 速度只统计**真的从网上下来的**字节：已存在的文件（skipped）在开始那一下
        # 会一次性把整个文件大小记进 done_bytes，算进去会在安装刚开头
        # 冒出一个几百 MB/s 的假峰值。
        streamed = sum(t.done_bytes for t in self.tasks if not t.skipped)
        done = sum(1 for t in self.tasks if t.finished)
        failed = sum(1 for t in self.tasks if t.state == "失败")
        active = sum(1 for t in self.tasks if t.active)
        return {
            "tasks": self.tasks,
            "done": done,
            "failed": failed,
            "active": active,
            "count": len(self.tasks),
            "bytes": got,
            "total": total,
            "percent": (got / total) if total > 0 else None,
            "speed": self._aggregate_speed(streamed),
            "finished": done == len(self.tasks) and len(self.tasks) > 0,
            # 镜像正在熔断冷却中（界面可以借此说一句"镜像不通，先走官方源"，
            # 免得用户以为卡住了 —— 实测 BMCLAPI 抽风时就是这么个观感）
            "mirror_off": self.use_mirror and time.monotonic() < self._mirror_off_until,
        }

    def _aggregate_speed(self, streamed: int) -> float:
        """总速度：用"所有任务已下字节合计"在滑动窗口里的变化来算

        为什么不是把每个任务的 speed 加起来（原来就是那么算的）：
        单个任务的速度是"它自己最近几秒的窗口"，而小文件从开始到结束
        常常不到 0.25 秒 —— 这种任务**整个生命周期里 speed 都是 0**。
        而《我的世界》的下载里绝大多数就是几十 KB 的小文件（资源文件尤其），
        于是一批小文件同时下的时候"总速度"就是 0，界面显示成"—"，
        看着像断了 —— 其实字节一直在下。这就是"速度断断续续、
        有时候直接不显示"的根因。

        改成看**总量**之后：只要字节在流就不可能突然变 0；
        真的不动了（下完了 / 卡住了）才归零，那才是这个数字该有的含义。
        """
        now = time.monotonic()
        with self._lock:
            self._speed_samples.append((now, streamed))
            cutoff = now - SPEED_WINDOW
            while len(self._speed_samples) > 2 and self._speed_samples[0][0] < cutoff:
                self._speed_samples.popleft()
            first_at, first_bytes = self._speed_samples[0]

        span = now - first_at
        if span >= 0.25:
            return max(0.0, (streamed - first_bytes) / span)
        # 窗口还没建立（刚开始下）：退回"各任务速度相加"，总比显示 0 强
        return sum(t.speed for t in self.tasks if t.active)

    def cancel_all(self):
        for t in self.tasks:
            if not t.finished:
                t.cancel()

    def wait_all(self, timeout: float = 10.0) -> bool:
        """等所有任务收尾；超时返回 False

        ⚠️ 关窗口/退出前**一定要调**：线程还在跑就被销毁，
        Qt 那边会直接终止进程（我们被坑过一次）。
        这里用普通线程，最坏情况是进程退出时被强杀，不会带上 Qt 一起崩。
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if all(t.finished for t in self.tasks):
                return True
            time.sleep(0.05)
        return False


def _tmp_of(dest: Path) -> Path:
    return dest.with_suffix(dest.suffix + ".tmp")


def _quiet_unlink(path: Path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
