"""全局配置（%APPDATA%/Mosslight/config.json）

（2026-09 之前叫 MCLuncher，改名时会把老目录整个改过来，用户不用手动搬。）

注意：这个文件和 core/versions.py 之间存在双向的函数内 import
（default_minecraft_dir 读 config，Config.get_minecraft_dir 又调
default_minecraft_dir）。两边都是延迟导入，所以不会死循环，但它是个环形依赖，
以后重构要小心。

## 这个文件是给人看的，也是给人改的

所以：**不认识的键会原样留着**。旧版本写进去的字段、或者用户自己加的字段，
都不会被静默丢掉 —— 以前 load() 只保留 DEFAULT_CONFIG 里有的键，于是
"回退旧版本 / 手改过配置" 都会在下一次 save() 时永久丢数据。
一条读不出来的配置（写了一半、手改坏了）也只会退回默认值，不会让程序起不来。

## 写的时候是原子的

先写 config.json.tmp，fsync，再 os.replace 替换过去。直接覆写的话，
写到一半断电/被杀进程就只剩半个 JSON，下次启动读不出来，用户的配置全丢。
"""

import json
import os
import re
from pathlib import Path

from core.resources import app_dir

# 配置格式版本。以后要改结构（挪字段、改名）就 +1，并在 _migrate() 里补一段，
# 这样老用户的配置能平滑升上来。
CONFIG_VERSION = 1

# ---------- 便携模式 ----------
#
# 默认配置放 %APPDATA%\Mosslight（跟系统规矩走，装在 Program Files 里也能写）。
# 想"拷走整个文件夹就带走设置"（绿色版）的话，在**启动器目录**下放一个
# portable.txt 就行 —— 也可以直接把 Mosslight 目录拷到启动器旁边，
# 那样不用标记文件也会被认出来。
#
# 三个必须处理的情况：
#   1. 打包后 __file__ 在 _internal 里 → 用 core/resources.app_dir()
#   2. Program Files 下写不进去 → 探测一次，写不进去就老实退回 %APPDATA%
#   3. 从 %APPDATA% 切过来的老用户 → 把已有的几个文件**拷**过来（不是移动，也不覆盖）
PORTABLE_MARKER = "portable.txt"

# 数据目录名。2026-09 跟着改名从 MCLuncher 换成了 Mosslight（跟 APP_NAME 一致）。
DATA_DIR_NAME = "Mosslight"

# 改名之前用过的目录名。老用户升级上来时要把数据**接上** ——
# 光换个名字不管旧目录，用户会以为"账号和设置突然全没了"。
LEGACY_DIR_NAMES = ("MCLuncher",)

# 切到便携模式时要带过去的文件（就这几个，别的都是临时/缓存）
_PORTABLE_FILES = ("config.json", "accounts.json", "versions.json")


def _adopt_legacy_dir(base: Path, target: Path) -> Path:
    """把改名前的数据目录改名成新的，返回**这次该用哪个目录**

    只在"新目录还不存在"时才改名 —— 两个目录都存在的话，谁新谁旧猜不出来，
    宁可让用户自己看一眼，也不能把数据盖掉。

    改不动（被别的进程占着、权限不够）就继续用老目录：
    **数据能用比目录名好看重要得多**。
    """
    if target.exists():
        return target
    for name in LEGACY_DIR_NAMES:
        legacy = base / name
        if not legacy.is_dir():
            continue
        try:
            os.replace(legacy, target)
            print(f"[Config] 数据目录改名：{legacy} → {target}")
            return target
        except OSError as e:
            print(f"[Config] 数据目录改不过来（{e}），这次继续用 {legacy}")
            return legacy
    return target


def _system_config_dir() -> Path:
    base = (Path(os.environ.get("APPDATA", Path.home())) if os.name == "nt"
            else Path.home() / ".config")
    return _adopt_legacy_dir(base, base / DATA_DIR_NAME)


def _portable_dir() -> Path:
    base = app_dir()
    return _adopt_legacy_dir(base, base / DATA_DIR_NAME)


def _writable(path: Path) -> bool:
    """探测目录能不能写。只看权限，不留垃圾文件"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _portable_requested() -> bool:
    """用户想不想要便携模式：放了标记文件，或者数据目录已经在旁边了"""
    try:
        if (app_dir() / PORTABLE_MARKER).is_file():
            return True
        return _portable_dir().is_dir()
    except OSError:
        return False


def _migrate_to_portable(target: Path):
    """把 %APPDATA% 里已有的配置拷一份过来

    只拷**目标里没有**的，绝不覆盖 —— 用户可能已经在新位置放了东西。
    原文件留着不动：万一是误判（比如把便携目录删了），退回系统目录还能用。
    """
    source = _system_config_dir()
    if not source.is_dir() or source == target:
        return
    for name in _PORTABLE_FILES:
        old = source / name
        new = target / name
        if old.is_file() and not new.exists():
            try:
                new.write_bytes(old.read_bytes())
                print(f"[Config] 便携模式：已从 {old} 拷来 {name}")
            except OSError as e:
                print(f"[Config] 便携模式：{name} 拷不过来（{e}）")


def get_config_dir() -> Path:
    """配置目录：便携模式下是启动器旁边的 Mosslight，否则是 %APPDATA%\\Mosslight

    改名前的 MCLuncher 目录会在第一次调用时被**改名**过来（见 _adopt_legacy_dir）。
    """
    if _portable_requested():
        target = _portable_dir()
        if _writable(target):
            _migrate_to_portable(target)
            return target
        # 装在 Program Files、又没有管理员权限时会走到这儿。
        # 静默退回系统目录，不然启动器直接起不来。
        print(f"[Config] {target} 写不进去，这轮回退到系统配置目录")

    d = _system_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_portable() -> bool:
    """当前是不是在用便携模式（设置页要显示出来）"""
    try:
        return get_config_dir() == _portable_dir()
    except OSError:
        return False


CONFIG_FILE = get_config_dir() / "config.json"

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "minecraft_dir": "",      # 空字符串 = 用默认路径
    "java_path": "",          # 空 = 自动找
    "max_memory": 2048,       # MB
    "min_memory": 512,
    "close_on_launch": False, # 启动后关闭启动器
    "show_log_window": True,  # 启动游戏后自动打开日志窗口
    "language": "",            # 界面语言；留空 = 首次启动按系统语言定（见 core/i18n.py）
    "theme": "system",        # system / dark / light，见 core/theme.py
    "accent_color": "",       # 空 = 用主题自带的强调色；否则 "#rrggbb"
    "last_version": "",       # 上次启动的版本 id，首页默认选中它
    # 记住用过的 .minecraft 目录（PCL 那种"文件夹列表"）：最近的排前面。
    # 只是方便下次再选，删掉也不影响功能。
    "known_minecraft_dirs": [],
    # ---------- 多线程下载（移植自 experiments/Downloading mod test 的 appearance）----------
    # multi_thread 是"用我自己设的线程数"开关。关着 = 用 DEFAULT_THREADS，
    # 开着才用 download_threads 的值 —— 实验那边就是这么设计的（原注释写着
    # "先让用户自己开，出问题好定位"），所以这里照搬，别自作主张改成"关=单线程"。
    "multi_thread": False,
    "download_threads": 8,     # 1~32，见 THREAD_RANGE
    # ---------- 背景 / 动效（移植自 experiments/Downloading mod test，见 core/appearance.py）----------
    # 这些键**只在 core/appearance.py 里读写**，别处别直接摸（那边管类型纠正和范围）。
    "bg_image": "",            # 背景图路径；"" = 不用图（纯色才生效）
    "bg_mode": "center",       # 铺法：fill / fit / stretch / center / span
    "bg_dim": 90,              # 压暗 0~200（亮背景会把卡片和字冲得看不清）
    "bg_color": "",            # 纯色背景；"" = 用主题的页面色
    "card_opacity": 100,       # 卡片底色不透明度 10~100（只动底色，不动文字）
    "anim_fade": True,         # 入场动画带不带淡入（关掉能省一点合成开销）
    "anim_preset": "slide_left",   # 动效风格，见 core/anim_prefs.py
    "anim_speed": "normal",        # 速度档，见 core/anim_prefs.py
    # 动效总开关。关掉之后列表**根本不包那层动画控件**（不是"动画时长为 0"），
    # 所以是真的省开销：省掉每张卡片的离屏合成、定时器和属性动画。
    "anim_enabled": True,
}

# 下载线程数：范围和默认值。改这里就够，界面和引擎都读它
THREAD_RANGE = (1, 32)
DEFAULT_THREADS = 8

THEME_MODES = ("system", "dark", "light")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---------- 类型纠正 ----------
#
# as_int / as_bool / as_str 是**公开**的：core/version_settings.py 也要用
# （同一类"手改过的配置文件"问题，别再抄一份）。
#
# 配置文件人手改过之后，值可能是 "4096"（字符串）、"false"（字符串）、
# 或者干脆是乱七八糟的东西。与其让它在很远的地方炸掉（比如 bool("false") 是 True，
# 或者主题色拼错导致整个样式表算不出来），不如在读进来的时候就纠正一次。
#
# 注意：只动**认识的**键，不认识的键原样保留。

def as_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def as_bool(value, fallback):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "on", "1"):
            return True
        if low in ("false", "no", "off", "0"):
            return False
        return fallback
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


def as_str(value, fallback):
    return value if isinstance(value, str) else (fallback if value is None else str(value))


def _as_theme(value, fallback):
    return value if value in THEME_MODES else fallback


def _as_color(value, fallback):
    """强调色只接受 #rrggbb，别的（拼错、写成颜色名）一律退回默认

    core/theme.py 会拿它做颜色运算，喂进去一个非法值会直接抛异常，
    那样整个界面就没样式了 —— 手滑改错一个字符不该有这种后果。
    """
    if isinstance(value, str) and _COLOR_RE.match(value.strip()):
        return value.strip()
    return fallback


def _as_dir_list(value, fallback):
    """一串目录路径（记住用过的 .minecraft）

    只留字符串、去空、去重、保序。写坏了（比如手改成字符串）就当空的 ——
    这个列表只是"方便下次再选"，丢了不影响任何功能，没必要为它报错。
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return list(fallback or [])
    seen, out = set(), []
    for item in value:
        text = str(item).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())      # Windows 路径大小写不敏感
            out.append(text)
    return out


_COERCE = {
    "config_version": as_int,
    "min_memory": as_int,
    "max_memory": as_int,
    "close_on_launch": as_bool,
    "show_log_window": as_bool,
    "minecraft_dir": as_str,
    "java_path": as_str,
    "language": as_str,
    "theme": _as_theme,
    "accent_color": _as_color,
    "last_version": as_str,
    "known_minecraft_dirs": _as_dir_list,
    "multi_thread": as_bool,
    "download_threads": as_int,
    # 背景 / 动效
    "bg_image": as_str,
    "bg_mode": as_str,
    "bg_dim": as_int,
    "bg_color": as_str,
    "card_opacity": as_int,
    "anim_fade": as_bool,
    "anim_preset": as_str,
    "anim_speed": as_str,
    "anim_enabled": as_bool,
}


def _migrate(data: dict) -> dict:
    """把老格式的配置升到当前版本

    现在还没有要改的东西，留着这个函数是为了以后：**每次改结构都在这儿加一段**，
    而且只处理"老版本 → 新版本"的差异，不要写成"每次都跑一遍"的幂等逻辑，
    否则以后加第二段时很容易互相踩。
    """
    version = as_int(data.get("config_version"), 0)
    if version >= CONFIG_VERSION:
        return data

    if version < 1:
        # 最早的配置没有 config_version，也没有 last_version —— 缺的交给默认值
        pass

    data["config_version"] = CONFIG_VERSION
    return data


class Config:
    """全局配置，单例式使用"""

    def __init__(self):
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        loaded = {}
        if CONFIG_FILE.exists():
            try:
                # 读字节：让 json 自己处理 BOM。手动存过 config.json 的话，
                # 编辑器可能加了 BOM，用 encoding="utf-8" 读会直接抛异常
                raw = json.loads(CONFIG_FILE.read_bytes())
            except Exception as e:
                print(f"[Config] 读取失败，这次用默认值: {e}")
                raw = None
            if isinstance(raw, dict):
                loaded = raw
            elif raw is not None:
                print("[Config] 配置不是键值对，忽略")
        self.data = self._normalize(loaded)

    def _normalize(self, loaded: dict) -> dict:
        """默认值 + 文件里的值（包括不认识的键）"""
        data = dict(DEFAULT_CONFIG)
        data.update(loaded)          # 未知键也一起进来，不做过滤
        data = _migrate(data)
        for key, coerce in _COERCE.items():
            data[key] = coerce(data.get(key), DEFAULT_CONFIG.get(key))
        return data

    def save(self):
        """原子写。返回是否写成功（写不进去只打日志，不抛异常）

        调用方大多是"用户改了个开关"，这种时候宁可这次没存上，
        也不该把整个界面崩掉。
        """
        text = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        tmp = CONFIG_FILE.parent / (CONFIG_FILE.name + ".tmp")
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_FILE)
            return True
        except OSError as e:
            print(f"[Config] 保存失败: {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def get(self, key, default=None):
        return self.data.get(key, default if default is not None else DEFAULT_CONFIG.get(key))

    def set(self, key, value):
        self.data[key] = value
        return self.save()

    def remember_minecraft_dir(self, path_text: str) -> bool:
        """把一个目录记进"用过的列表"，最近的排最前面

        目录列表只用来填那个下拉/列表，所以**去重按大小写不敏感**
        （Windows 上 D:\\MC 和 d:\\mc 是同一个目录，记两条会让用户莫名其妙）。
        """
        text = str(path_text).strip()
        if not text:
            return False
        known = [p for p in self.get("known_minecraft_dirs", [])
                 if p.lower() != text.lower()]
        known.insert(0, text)
        return self.set("known_minecraft_dirs", known[:12])

    def get_minecraft_dir(self) -> Path:
        """返回有效的 .minecraft 路径
        配置为空时回退到默认路径
        """
        custom = self.data.get("minecraft_dir", "").strip()
        if custom:
            p = Path(custom).expanduser()
            return p
        # 复用 versions.py 的默认逻辑
        from core.versions import default_minecraft_dir
        return default_minecraft_dir()


# 全局单例
config = Config()


# ---------- 多线程下载 ----------
# 移植自 experiments/Downloading mod test 的 appearance.effective_threads()：
# **干活的地方统一读 effective_threads()**，别去读 get_thread_count() ——
# "开关关着时用哪个值"这个语义只该有一处实现，各处各写一遍迟早不一致。

def get_multi_thread() -> bool:
    """多线程下载开关（= 用不用用户自己设的线程数）"""
    return bool(config.get("multi_thread", False))


def get_thread_count() -> int:
    """设置里填的线程数（不管开关开没开，读的都是这个值）"""
    try:
        value = int(config.get("download_threads", DEFAULT_THREADS))
    except (TypeError, ValueError):
        value = DEFAULT_THREADS
    low, high = THREAD_RANGE
    return max(low, min(high, value))


def effective_threads() -> int:
    """**实际该用几个线程** —— 下载页建 DownloadManager 时用这个

    开关关着 → DEFAULT_THREADS（不是 1：见 DEFAULT_CONFIG 里的说明），
    开着 → 用户设的值（1~32 夹紧，填错也不至于开 500 个线程）。
    """
    if not get_multi_thread():
        return DEFAULT_THREADS
    return get_thread_count()
