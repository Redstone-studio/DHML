"""系统内存查询 + 给游戏的内存建议

Windows 上 `GlobalMemoryStatusEx` 一次就能拿到总量 / 可用 / 占用百分比，
不用装 psutil（少一个依赖，打包也小一截）。其他平台老实返回"读不出来"——
这个功能只是显示和建议，读不到就不显示，不要假装知道。

**这里只做"显示和建议"，不碰启动参数**：`-Xmx` 加多少是用户的事
（GC 参数在不同 Java 大版本之间差异很大，加错了 JVM 直接拒绝启动）。
"""

import ctypes
import sys
from dataclasses import dataclass

MB = 1024 * 1024

# 建议值的天花板 / 地板（MB）
VANILLA_RANGE = (2048, 4096)
MODDED_RANGE = (4096, 12288)

# 超过物理内存的这个比例就画一条红线 —— 再往上容易开始换页，表现是越玩越卡
PAGE_RISK_RATIO = 0.6

# 内存滑块的档位（MB）。512 的倍数最省事：拖出来的值不会出现 3586 这种数
MEMORY_STEP = 512
MEMORY_MIN = 512
MEMORY_MAX = 65536


def snap_memory(value: int) -> int:
    """取整到 512 的档位 —— 滑块拖出来的值不会是整数档

    界面上所有能改内存的地方都要过这一道（设置页、版本设置页），
    不然配置里会出现用户没想选的数字。
    """
    stepped = int(round(int(value) / MEMORY_STEP)) * MEMORY_STEP
    return max(MEMORY_MIN, stepped)


@dataclass
class MemoryInfo:
    total_mb: int = 0
    available_mb: int = 0
    percent_used: int = 0

    @property
    def ok(self) -> bool:
        return self.total_mb > 0

    @property
    def used_mb(self) -> int:
        return max(0, self.total_mb - self.available_mb)


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def system_memory() -> MemoryInfo:
    """当前系统内存情况。读不出来就返回全 0（用 .ok 判断）"""
    if sys.platform != "win32":
        return MemoryInfo()
    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return MemoryInfo()
    except Exception:
        # 拿不到就算了，界面退回"只按你填的值画"
        return MemoryInfo()
    return MemoryInfo(
        total_mb=int(status.ullTotalPhys // MB),
        available_mb=int(status.ullAvailPhys // MB),
        percent_used=int(status.dwMemoryLoad),
    )


def jvm_overhead_mb(heap_mb: int) -> int:
    """堆之外大概还要占多少（Metaspace、代码缓存、GC 结构、直接内存…）

    只是个经验估算：几百兆固定开销 + 堆的百分之几。
    界面上一定要写"约"—— `-Xmx` 是**上限**不是预留，别让用户以为配了 4G 就实占 4G。
    """
    return int(320 + max(0, int(heap_mb)) * 0.06)


def recommend_memory(total_mb: int, modded: bool = False) -> int:
    """按物理内存给一个建议的最大堆

    经验值：物理内存的 1/4 起步，再按版本类型夹到合理区间，
    并且**不能超过物理内存的 60%**（超了就是拿换页换内存，越玩越卡）。
    小内存机器（4G 以下）另算 —— 那种机器怎么配都紧张。
    """
    low, high = MODDED_RANGE if modded else VANILLA_RANGE
    if total_mb <= 0:
        return low
    if total_mb <= 4096:
        # 4G 及以下：给一半，但至少 1G，别把系统挤死
        return _round_to_step(max(1024, min(total_mb // 2, 2048)))
    ceiling = int(total_mb * PAGE_RISK_RATIO)
    want = max(low, total_mb // 4)
    return _round_to_step(max(1024, min(want, high, ceiling)))


def _round_to_step(value: int, step: int = 512) -> int:
    """取整到 512 的倍数 —— 滑块和下拉框里好看，也没人会真要 3586 这种数"""
    return max(step, int(round(value / step)) * step)
