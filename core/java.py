"""Java 查找与选择

纯 Python（不 import PyQt6）—— core/ 的约定。这样能直接单测，
也能在没有界面的情况下跑。

两件事：
    find_javas()   把机器上的 Java 全找出来（注册表 + 常见目录 + PATH + MC 自带运行时）
    pick_java()    按版本的 javaVersion.majorVersion 挑一个

**为什么不自己维护"哪个版本要哪个 Java"的对照表**：版本 JSON 里已经有
`"javaVersion": {"majorVersion": 17}` 了，照着读就行。自己维护的表一定会过期。

⚠️ 两个实测出来的坑（都踩过）：

1. **MC 自带运行时有两种目录布局**，只处理嵌套那种会一个都搜不到：
      扁平  .minecraft/runtime/<component>/bin/java.exe
      嵌套  .minecraft/runtime/<component>/<os-arch>/<component>/bin/java.exe
   官方启动器和 PCL 用的都是**扁平**那种。

2. **Oracle 会留一个 `latest` 软链目录**（`Program Files\\Java\\latest\\jre-1.8\\bin`），
   只扫一层的话会漏。
"""

import json
import os
import platform
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from core.i18n import tr

# java -version 超时（秒）。JDK 冷启动偶尔要一两秒
VERSION_TIMEOUT = 5

# 扫描结果的缓存文件（放配置目录里）。改缓存结构时 +1，旧的自然作废。
CACHE_VERSION = 2
CACHE_FILE_NAME = "java_cache.json"

# 探测失败的路径多久之内不再重试（秒）。
#
# 为什么连失败也要缓存：这台机器上有两个 Oracle 的 shim
# （`Common Files\Oracle\Java\javapath\java.exe`）要跑 **4.9 秒**才返回一个
# 解析不出的结果 —— 光它们俩就让"每次启动"多等 5 秒。
# 缓存失败之后，第一次仍然慢，之后就是毫秒级；而真要是临时故障
# （文件被占用之类），过几个小时还会再试一次，不会永久拉黑。
FAILURE_RETRY_SECONDS = 6 * 3600

# 缓存里那条"上次没读出来"在界面上怎么显示。
# ⚠️ 这句**不进缓存**（缓存只存"失败过"这个事实和时间戳）—— 存了的话
# 用户换语言以后会看到一句旧语言的错误提示。
FAILED_CACHE_MESSAGE = "上次扫描没读出它的版本，暂时跳过"

# 常见安装位置。列表末尾会再往下一层找（Oracle 的 latest 软链）
COMMON_DIRS = (
    r"C:\Program Files\Java",
    r"C:\Program Files (x86)\Java",
    r"C:\Program Files\Eclipse Adoptium",
    r"C:\Program Files\Eclipse Foundation",
    r"C:\Program Files\Microsoft",
    r"C:\Program Files\Zulu",
    r"C:\Program Files\BellSoft",
    r"C:\Program Files\Amazon Corretto",
    r"C:\Program Files\Semeru",
    r"C:\Program Files\RedHat",
    r"C:\Program Files\SapMachine",
    r"C:\Program Files\Common Files\Oracle\Java",
)

# 注册表里可能写着 JavaHome 的位置
REGISTRY_KEYS = (
    r"SOFTWARE\JavaSoft\Java Development Kit",
    r"SOFTWARE\JavaSoft\Java Runtime Environment",
    r"SOFTWARE\JavaSoft\JRE",
    r"SOFTWARE\JavaSoft\JDK",
    r"SOFTWARE\WOW6432Node\JavaSoft\Java Development Kit",
    r"SOFTWARE\WOW6432Node\JavaSoft\Java Runtime Environment",
    r"SOFTWARE\Eclipse Adoptium\JDK",
    r"SOFTWARE\Eclipse Foundation\JDK",
    r"SOFTWARE\Microsoft\JDK",
)


@dataclass
class JavaInfo:
    """一个可用的 Java"""
    path: str
    version: str = ""
    major: int = 0
    vendor: str = ""
    arch: str = ""
    error: str = ""

    @property
    def usable(self) -> bool:
        return not self.error and self.major > 0

    @property
    def is_64bit(self) -> bool:
        return self.arch == "x86_64"

    # ---------- 存缓存用 ----------
    #
    # 读一次版本要起一个 java 进程（这台机器上 12 个候选要 5 秒多），
    # 所以扫到的结果按"exe 的 mtime + 大小"存起来，下次直接复用。
    # 存的是**原始数据**，不存 error —— 因为 error 文案是翻译过的，
    # 存进文件以后再换语言就变成另一种语言的旧句子了。

    def to_dict(self) -> dict:
        return {"version": self.version, "major": self.major,
                "vendor": self.vendor, "arch": self.arch}

    @classmethod
    def from_dict(cls, path: str, data: dict) -> "JavaInfo":
        return cls(
            path=path,
            version=str(data.get("version", "")),
            major=int(data.get("major", 0) or 0),
            vendor=str(data.get("vendor", "")),
            arch=str(data.get("arch", "")),
        )

    def label(self) -> str:
        """界面上显示的一行，例如 `JDK 17 · 64 位 · Oracle`"""
        kind = "JDK" if "jdk" in self.path.lower() else "JRE"
        arch = tr("64 位") if self.is_64bit else (tr("32 位") if self.arch else tr("位数未知"))
        vendor = self.vendor or tr("未知厂商")
        return f"{kind} {self.major or '?'} · {arch} · {vendor}"


# ============================================================
# 读一个 java 的信息
# ============================================================

def read_java_info(java_exe) -> JavaInfo:
    """跑一次 `java -version` 把版本/位数/厂商读出来

    `java -version` 是往 **stderr** 输出的，两路都要收。
    """
    info = JavaInfo(path=str(java_exe))
    try:
        proc = subprocess.run(
            [str(java_exe), "-version"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=VERSION_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        info.error = tr("运行超时")
        return info
    except FileNotFoundError:
        info.error = tr("文件不存在")
        return info
    except OSError as e:
        info.error = tr("无法执行：{err}", err=e)
        return info

    output = (proc.stderr or "") + (proc.stdout or "")

    m = re.search(r'version\s+"([^"]+)"', output)
    if not m:
        info.error = tr("无法解析版本")
        return info

    info.version = m.group(1)
    parts = info.version.split(".")
    try:
        # "1.8.0_481" → 8 ；"17.0.9" → 17
        info.major = int(parts[1]) if parts[0] == "1" and len(parts) > 1 else int(parts[0])
    except (ValueError, IndexError):
        info.error = tr("版本号看不懂：{ver}", ver=info.version)
        return info

    # 厂商：取 "Runtime Environment" 那一行
    for line in output.splitlines():
        line = line.strip()
        if "Runtime Environment" in line:
            info.vendor = line
            break
    if not info.vendor:
        m2 = re.search(r"^(OpenJDK|Java\(TM\)|Microsoft|Zulu|Temurin).*", output, re.M)
        if m2:
            info.vendor = m2.group(0).strip()[:60]

    # 位数：优先看输出里的字样，其次看进程本身
    lower = output.lower()
    if "64-bit" in lower or "x86_64" in lower or "amd64" in lower:
        info.arch = "x86_64"
    elif "32-bit" in lower or "i386" in lower:
        info.arch = "x86"
    else:
        machine = platform.machine().lower()
        info.arch = "x86_64" if machine in ("amd64", "x86_64") else machine

    return info


# ============================================================
# 找候选路径
# ============================================================

def _registry_javas() -> list:
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []

    found = []
    for subkey in REGISTRY_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
                index = 0
                while True:
                    try:
                        version_name = winreg.EnumKey(key, index)
                        index += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(key, version_name) as ver_key:
                            home, _ = winreg.QueryValueEx(ver_key, "JavaHome")
                            exe = Path(home) / "bin" / "java.exe"
                            if exe.is_file():
                                found.append(exe)
                    except (FileNotFoundError, OSError):
                        continue
        except (FileNotFoundError, OSError):
            continue
    return found


def _dir_javas(base: Path, max_depth: int = 2) -> list:
    """在 base 下面找 bin/java.exe（默认往下两层）"""
    found = []
    if not base.is_dir():
        return found

    def walk(d: Path, depth: int):
        if depth > max_depth:
            return
        try:
            entries = list(d.iterdir())
        except (PermissionError, OSError):
            return
        for sub in entries:
            if not sub.is_dir():
                continue
            exe = sub / "bin" / "java.exe"
            if exe.is_file():
                found.append(exe)
            walk(sub, depth + 1)

    walk(base, 1)
    return found


def _path_env_javas() -> list:
    found = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        exe = Path(entry) / ("java.exe" if sys.platform == "win32" else "java")
        if exe.is_file():
            found.append(exe)
    return found


def _minecraft_runtime_javas(mc_dirs) -> list:
    """扫 Minecraft / PCL 下载的自带运行时

    默认位置是 `%APPDATA%\\.minecraft\\runtime`，但也接受调用方传进来的游戏目录
    （有些玩家把 .minecraft 放别处）。
    """
    found = []
    roots = []
    appdata = os.environ.get("APPDATA")
    if sys.platform == "win32" and appdata:
        roots.append(Path(appdata) / ".minecraft" / "runtime")
    elif sys.platform == "darwin":
        roots.append(Path.home() / "Library" / "Application Support" / "minecraft" / "runtime")
    else:
        roots.append(Path.home() / ".minecraft" / "runtime")

    for mc_dir in mc_dirs or ():
        roots.append(Path(mc_dir) / "runtime")

    for root in roots:
        if not root.is_dir():
            continue
        try:
            components = list(root.iterdir())
        except (PermissionError, OSError):
            continue
        for component in components:
            if not component.is_dir():
                continue
            # 扁平布局（官方 + PCL 实测）
            flat = component / "bin" / "java.exe"
            if flat.is_file():
                found.append(flat)
                continue
            # 嵌套布局（老版官方启动器）
            try:
                for os_arch in component.iterdir():
                    if not os_arch.is_dir():
                        continue
                    nested = os_arch / component.name / "bin" / "java.exe"
                    if nested.is_file():
                        found.append(nested)
            except (PermissionError, OSError):
                continue
    return found


def candidate_paths(mc_dirs=()) -> list:
    """所有可能的 java.exe 路径（去重、过滤掉 javaw.exe）"""
    pool = []
    pool += _path_env_javas()
    pool += _registry_javas()
    pool += _minecraft_runtime_javas(mc_dirs)
    if sys.platform == "win32":
        for d in COMMON_DIRS:
            pool += _dir_javas(Path(d), max_depth=2)

    # 用 resolve() 归一化，避免同一个 Java 被算两次
    unique, seen = [], set()
    for p in pool:
        try:
            resolved = str(Path(p).resolve()).lower()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(Path(p))
    return unique


# ============================================================
# 对外接口
# ============================================================

def _cache_path():
    """Java 扫描结果的缓存文件（放配置目录里，跟 config.json 作伴）"""
    from core.config import get_config_dir      # 函数内 import：避免顶层环形依赖
    return get_config_dir() / CACHE_FILE_NAME


def _fingerprint(java_exe) -> list:
    """判断"还是不是同一个 java.exe"的依据：mtime + 大小

    ⚠️ 不能只看路径：同一个路径上重装了别的 Java 是完全正常的
    （卸载 8 装 17），只看路径会把旧版本号一直用下去。
    """
    st = os.stat(java_exe)
    return [st.st_mtime, st.st_size]


def _within_retry_window(entry: dict) -> bool:
    """这条失败记录还在"先别再试"的窗口里吗

    时间戳读不出来（手改坏了 / 老格式）就当不在窗口里 —— 宁可多探一次，
    也别让某个 Java 永远不出现在列表里。
    """
    try:
        failed_at = float(entry.get("failed_at", 0))
    except (TypeError, ValueError):
        return False
    return (time.time() - failed_at) < FAILURE_RETRY_SECONDS


def _load_cache() -> dict:
    """读缓存。读不出来就当没有 —— 缓存坏了最多是慢一次，不该让扫描失败"""
    try:
        raw = json.loads(_cache_path().read_bytes())
    except (OSError, ValueError):
        return {}
    entries = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries, dict) or raw.get("cache_version") != CACHE_VERSION:
        return {}
    return entries


def _save_cache(entries: dict) -> None:
    """原子写缓存。写不进去只打日志 —— 缓存是纯加速，不能因为它失败影响启动"""
    path = _cache_path()
    payload = {"cache_version": CACHE_VERSION, "entries": entries}
    tmp = path.parent / (path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        print(f"[Java] 扫描缓存写不进去（{e}），下次还得重扫")


def find_javas(mc_dirs=(), max_workers: int = 8, on_progress=None,
               use_cache: bool = True) -> list:
    """扫描所有 Java 并读出版本信息，按主版本从高到低排

    读版本要起进程（这台机器上 12 个候选要 5 秒多），所以：

    1. **并发跑**（串行的话 8 个 Java 要等十几秒）
    2. 结果按 exe 的 mtime + 大小存进缓存，下次启动直接复用 ——
       只有"新出现的 / 换过的" java.exe 才需要重新探测。
       （实测：第二次启动这一段从 5.2 秒降到几毫秒。）

    关掉缓存传 use_cache=False（测试和"重新扫描"按钮会用到）。
    """
    paths = candidate_paths(mc_dirs)
    if on_progress:
        on_progress(tr("正在检测 {n} 个候选…", n=len(paths)))

    cached = _load_cache() if use_cache else {}
    results, fresh, todo = [], {}, []
    for path in paths:
        key = str(path)
        entry = cached.get(key)
        if isinstance(entry, dict) and entry.get("fp"):
            try:
                same = entry["fp"] == _fingerprint(path)
            except OSError:
                same = False        # stat 失败（刚被删/挪走）→ 当没缓存
            if same and entry.get("ok"):
                results.append(JavaInfo.from_dict(key, entry.get("info") or {}))
                fresh[key] = entry
                continue
            # 上次探测失败：在重试窗口内不再花几秒去碰它
            if same and _within_retry_window(entry):
                results.append(JavaInfo(path=key, error=tr(FAILED_CACHE_MESSAGE)))
                fresh[key] = entry
                continue
        todo.append(path)

    if todo:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(read_java_info, p): p for p in todo}
            for future in as_completed(futures):
                path = futures[future]
                try:
                    info = future.result()
                except Exception as e:                  # 兜底：单个失败不影响整体
                    info = JavaInfo(path=str(path), error=f"{type(e).__name__}: {e}")
                results.append(info)
                try:
                    fp = _fingerprint(path)
                except OSError:
                    continue        # 连文件都 stat 不到，没什么可记的
                if info.usable:
                    fresh[str(path)] = {"ok": True, "fp": fp, "info": info.to_dict()}
                else:
                    fresh[str(path)] = {"ok": False, "fp": fp,
                                        "failed_at": time.time()}

    if use_cache:
        _save_cache(fresh)

    results.sort(key=lambda j: (-j.major, j.path))
    return results


def pick_java(javas, required_major: int, preferred: str = ""):
    """按版本要求的 Java 主版本挑一个

    返回 (JavaInfo | None, 说明文字)。

    挑选顺序：
      1. 用户手动指定的那个（配置里的 java_path）—— 只要它能用就用它
      2. 主版本**正好相等**的，优先 64 位
      3. 没有正好的，退而求其次找**更高**的（并说明）
         —— 注意不能拿更低的：1.17+ 的版本在 Java 8 上起不来
      4. 都没有 → None
    """
    usable = [j for j in (javas or []) if j.usable]

    ignored = None
    if preferred:
        for j in usable:
            if Path(j.path) == Path(preferred) or j.path.lower() == str(preferred).lower():
                if j.major == required_major:
                    return j, tr("使用你指定的 Java")
                # 主版本对不上就**不硬来** —— 硬用的话，指定了 Java 8
                # 会把所有要 17 的版本一起弄坏。自动挑，并在说明里讲清楚。
                ignored = j
                break

    def _note(java, why):
        if ignored is not None:
            why += tr("（你指定的 Java {n} 主版本对不上，已忽略）", n=ignored.major)
        return java, why

    exact = [j for j in usable if j.major == required_major]
    if exact:
        exact.sort(key=lambda j: (not j.is_64bit, j.path))
        return _note(exact[0], tr("自动选择（要求 Java {n}）", n=required_major))

    newer = [j for j in usable if j.major > required_major]
    if newer:
        newer.sort(key=lambda j: (j.major, not j.is_64bit, j.path))
        return _note(newer[0], tr("没有 Java {need}，用 Java {have} 顶替（可能起不来）",
                                  need=required_major, have=newer[0].major))

    return _note(None, tr("没找到 Java {n}，请先安装", n=required_major))


def required_major(version_json: dict, default: int = 8) -> int:
    """版本 JSON 要求的主版本；老版本没有 javaVersion 字段就按 8 算"""
    return int((version_json.get("javaVersion") or {}).get("majorVersion", default))


def scan_minecraft_dirs() -> list:
    """猜几个可能放着 .minecraft 的目录（给上面几个函数当 mc_dirs 用）"""
    cands = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        cands.append(Path(appdata) / ".minecraft")
    home = Path.home()
    for name in (".minecraft", "AppData/Roaming/.minecraft"):
        p = home / name
        if p.is_dir():
            cands.append(p)
    return [str(p) for p in cands if p.is_dir()]
