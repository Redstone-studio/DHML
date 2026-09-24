"""后台任务：启动游戏

**这是项目里第一次引入 QThread。** 启动这件事有两处必然阻塞：

  1. 准备阶段（校验库、解压 natives）
  2. 读游戏输出 —— `for line in proc.stdout` 会一直阻塞到游戏退出

放在主线程里界面会直接冻住，所以必须扔进线程。

分工：`core/launch.py` 只负责**拼出参数列表**（纯函数），这里负责**把它跑起来**。
"""

import subprocess
import sys
from collections import deque

from PyQt6.QtCore import QThread, pyqtSignal

from core.i18n import tr
from core.mclog import Log4jXmlReader


def decode_output(raw: bytes) -> str:
    """把游戏输出的一行解码成文字

    ⚠️ 不能写死 UTF-8：Java 8 的输出用的是**系统编码**（中文 Windows 上是 GBK），
    硬按 UTF-8 解会整段乱码，把真正的报错也一起盖掉 —— 这个坑踩过。

    所以按可能性逐个试：现代的 Java（我们用 -Dstdout.encoding 强制 UTF-8）先中，
    老版本走 GBK；最后 latin-1 兜底（它不会失败，只可能显示得不好看）。
    """
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding).rstrip()
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace").rstrip()

# 留着最后这么多行日志 —— 游戏秒退时，原因就在这几行里
TAIL_LINES = 30

# 日志行前缀，不是界面文案
LOG_PREFIX = "[启动器] "  # noqa: i18n


class LaunchTask(QThread):
    """启动游戏并实时把输出吐给界面

    信号：
        log(str)        一行游戏输出
        started()       进程真的起来了
        finished(int)   游戏退出，带退出码
        failed(str)     没能起来（参数错、找不到 Java、natives 没解压…）
    """

    log = pyqtSignal(str)
    started = pyqtSignal()
    finished = pyqtSignal(int)
    failed = pyqtSignal(str)

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self._proc = None
        self._stopping = False
        # 环形缓冲：不管日志多长，最后 30 行一直留着
        self.recent = deque(maxlen=TAIL_LINES)

    # ---------- 给界面用的 ----------

    def tail(self) -> str:
        """最近若干行日志。游戏异常退出时拿它去弹窗"""
        return "\n".join(self.recent)

    def stop(self):
        """请求停止。会先 terminate，等不够再由界面决定要不要 kill"""
        self._stopping = True
        if self._proc is not None and self._proc.poll() is None:
            self.log.emit(LOG_PREFIX + tr("正在停止游戏…"))
            self._proc.terminate()

    # ---------- 线程主体 ----------

    def run(self):
        plan = self.plan

        # ---- 准备阶段：解压 natives ----
        # 有磁盘 I/O，所以放在工作线程里做，不冻界面。
        # 1.18 及以前必须预先解好；1.19+ 其实能自己解压，但目录也得存在，
        # 所以统一都解一遍。
        try:
            from core.natives import ensure_natives
            _extracted, missing = ensure_natives(
                plan.version_json, plan.mc_dir, plan.cwd, plan.version_id, self.log.emit
            )
        except Exception as e:
            self.failed.emit(tr("解压原生库失败：{err}", err=e))
            return

        if missing:
            self.failed.emit(tr(
                "缺少 {n} 个原生库，没法启动：\n{first}\n\n"
                "这个版本不是本启动器安装的，缺的部分要重新下载才能补上。",
                n=len(missing), first=missing[0],
            ))
            return

        if not plan.cwd or not plan.cwd.is_dir():
            self.failed.emit(tr("版本目录不存在：\n{path}", path=plan.cwd))
            return

        try:
            self._proc = subprocess.Popen(
                plan.args,
                cwd=str(plan.cwd),          # 必须和 --gameDir 一致，否则存档写错地方
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,   # 合并，日志才按时间顺序
                stdin=subprocess.DEVNULL,   # 别让游戏等着读标准输入
                # 不用 text=True：解码自己来（Java 8 的输出是 GBK，见 decode_output）
                # 用 java.exe 而不是 javaw.exe：javaw 会把控制台输出整个丢掉，
                # 游戏崩了什么都看不到。加这个 flag 既不留黑框、又能收到日志。
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except FileNotFoundError:
            self.failed.emit(tr("找不到可用的 Java：\n{path}\n\n去「设置」里重新指定。", path=plan.java))
            return
        except OSError as e:
            self.failed.emit(tr("启动失败：{err}", err=e))
            return

        self.started.emit()

        # 流式读日志。stop() 会 terminate 进程，管道随之关闭，这个循环就出来了
        #
        # 这里要过一道 Log4jXmlReader：1.12 以后的控制台输出是 log4j2 的 XML
        # （见 core/mclog.py），一条日志在这里是 3 行，攒齐了才还原成可读的一行。
        reader = Log4jXmlReader()
        try:
            for raw in self._proc.stdout:
                for line in reader.feed(decode_output(raw)):
                    self.recent.append(line)
                    self.log.emit(line)
                if self._stopping and self._proc.poll() is not None:
                    break
        except (OSError, ValueError):
            pass        # 进程被强杀时管道会断，正常
        finally:
            # 进程死在半条事件中间时，把剩下的原样放出去，别默默吞掉
            for line in reader.flush():
                self.recent.append(line)
                self.log.emit(line)
            code = self._proc.wait()

        self.finished.emit(code)


class QuickJavaScanTask(QThread):
    """后台扫 Java

    扫一遍要起好几个 `java -version` 进程（这台机器上 12 个候选要 5 秒多），
    主线程里做会直接卡住界面，所以放线程里 —— 而且 find_javas 有缓存，
    **启动时那次**基本都是直接命中缓存、几毫秒就回来。

    用户点「重新扫描」时要传 use_cache=False：那个按钮的意思就是"别信旧的"。
    """

    done = pyqtSignal(list)         # list[JavaInfo]
    progress = pyqtSignal(str)

    def __init__(self, mc_dirs=(), parent=None, use_cache: bool = True):
        super().__init__(parent)
        self.mc_dirs = list(mc_dirs)
        self.use_cache = use_cache

    def run(self):
        from core.java import find_javas, scan_minecraft_dirs
        mc_dirs = self.mc_dirs or scan_minecraft_dirs()
        try:
            result = find_javas(mc_dirs, on_progress=self.progress.emit,
                                use_cache=self.use_cache)
        except Exception as e:
            self.progress.emit(tr("扫描 Java 出错：{err}", err=e))
            result = []
        self.done.emit(result)


class RepairTask(QThread):
    """后台补全缺失的库

    有些版本是"装了一半"的（比如 1.12.2 的 Forge 缺 maven-artifact），
    联网把 JSON 里声明过、但本地没有的库下回来，比让用户去别的启动器修省事。
    """

    log = pyqtSignal(str)
    done = pyqtSignal(int, list)      # (补好了几个, 没补上的库名)

    def __init__(self, mc_dir, version_json, parent=None):
        super().__init__(parent)
        self.mc_dir = mc_dir
        self.version_json = version_json

    def run(self):
        from core.repair import repair
        try:
            fixed, failed = repair(self.mc_dir, self.version_json, self.log.emit)
        except Exception as e:
            self.log.emit(tr("补全失败：{err}", err=e))
            fixed, failed = 0, []
        self.done.emit(fixed, failed)
