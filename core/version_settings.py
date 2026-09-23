"""按版本覆盖启动参数

## 为什么不写进版本 JSON

`versions/<id>/<id>.json` 是**安装器管的**（PCL、HMCL、官方启动器都往里写），
改它有两个后果：

1. 我们自己的"文件校验 / 补全缺失的库"会把它当成被改坏的版本
2. 用户下次用别的启动器更新那个版本，改动直接被覆盖

所以放我们自己的边文件：`%APPDATA%/MCLuncher/versions.json`，
键是**版本 id**（就是 `versions/` 下面那个目录名）。

## 三态，不是两态

每一项要么「跟随启动器默认」—— 文件里**没有**这个键；
要么「这个版本自己定」—— 文件里有这个键。

**不是**"把默认值抄一份过来"。抄过来的话，以后改了启动器默认，
所有已经动过一次的版本都不会跟着变，用户会莫名其妙（PCL2 也是没动过的项跟着全局走）。
所以"取消自定义" = 把这个键删掉。

读写的套路跟 core/config.py 一样：认识的键才纠正、不认识的留着、原子写。
"""

import json
import os
from pathlib import Path

from core.config import as_int, as_str, get_config_dir

SETTINGS_FILE = get_config_dir() / "versions.json"

# 配置格式版本（跟 core/config.py 一个意思，以后改结构时 +1）
SETTINGS_VERSION = 1

# 一个版本能覆盖哪些项。加新项只改这一处：对话框和保存都按这个表走。
#
# 注意这里**没有**窗口尺寸之类 —— 那些还没有"启动器默认值"，
# 一个只有版本覆盖、没有全局默认的项，界面上的"跟随默认"就没意义了。
FIELDS = ("java_path", "min_memory", "max_memory", "extra_jvm_args")

_COERCE = {
    "java_path": as_str,
    "min_memory": as_int,
    "max_memory": as_int,
    "extra_jvm_args": as_str,
}


class VersionSettings:
    """所有版本的覆盖项。整体读一次、改完整体写回（文件很小）"""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else SETTINGS_FILE
        self.data = {}          # 版本 id -> {覆盖项}
        self.load()

    # ---------- 读写 ----------

    def load(self):
        raw = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_bytes())
            except Exception as e:
                # 读不出来就当没有 —— 跟 config.py 一样，不能让配置文件
                # 把启动器搞得起不来
                print(f"[VersionSettings] 读取失败，这次当空的: {e}")
                loaded = None
            if isinstance(loaded, dict):
                versions = loaded.get("versions")
                if isinstance(versions, dict):
                    raw = versions
            elif loaded is not None:
                print("[VersionSettings] 文件格式不对，忽略")

        self.data = {}
        for version_id, overrides in raw.items():
            clean = self._normalize(overrides)
            if clean:
                self.data[str(version_id)] = clean

    def save(self) -> bool:
        """原子写。写不进去只打日志、返回 False，不抛异常"""
        payload = {
            "settings_version": SETTINGS_VERSION,
            "versions": self.data,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp = self.path.parent / (self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            return True
        except OSError as e:
            print(f"[VersionSettings] 保存失败: {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @staticmethod
    def _normalize(overrides) -> dict:
        """只留认识的项、去掉空值、顺手纠正类型"""
        if not isinstance(overrides, dict):
            return {}
        clean = {}
        for key, coerce in _COERCE.items():
            if key not in overrides:
                continue
            value = coerce(overrides[key], None)
            # 手改过的文件里很容易多打空格（尤其 JVM 参数），统一去掉
            if isinstance(value, str):
                value = value.strip()
            # 空字符串 / 0 都等于"没设"，不存 —— 免得文件里一堆空项，
            # 界面上的"跟随默认"判起来也麻烦
            if value:
                clean[key] = value
        return clean

    # ---------- 查询与修改 ----------

    def get(self, version_id: str) -> dict:
        """这个版本的覆盖项（没有就是空字典）"""
        return dict(self.data.get(version_id, {}))

    def is_custom(self, version_id: str, field: str) -> bool:
        return field in self.data.get(version_id, {})

    def update(self, version_id: str, overrides: dict) -> bool:
        """整体替换这个版本的覆盖项。空字典 = 恢复成全部跟随默认"""
        clean = self._normalize(overrides)
        if clean:
            self.data[version_id] = clean
        else:
            self.data.pop(version_id, None)
        return self.save()

    def forget(self, version_id: str) -> bool:
        return self.update(version_id, {})

    def resolve(self, version_id: str, defaults: dict) -> dict:
        """把覆盖和启动器默认合起来，返回**最终生效**的值

        defaults 由调用方给（默认值在 core/config.py 里，这个模块不去读它，
        免得又多一个环形依赖）。
        """
        resolved = dict(defaults)
        resolved.update(self.get(version_id))

        # 最小堆不能大于最大堆：两边可能一个来自默认、一个来自覆盖
        # （默认 max 2048 + 版本自定义 min 4096），不夹一下 JVM 会直接拒绝启动
        low = as_int(resolved.get("min_memory"), 0)
        high = as_int(resolved.get("max_memory"), 0)
        if low and high and low > high:
            resolved["min_memory"] = high
        return resolved


# 全局单例（跟 core/config.py 的 config 一样，各处直接用）
version_settings = VersionSettings()
