"""全局配置（%APPDATA%/MCLuncher/config.json）

注意：这个文件和 core/versions.py 之间存在双向的函数内 import
（default_minecraft_dir 读 config，Config.get_minecraft_dir 又调
default_minecraft_dir）。两边都是延迟导入，所以不会死循环，但它是个环形依赖，
以后重构要小心。

TODO(未修): load() 只保留 DEFAULT_CONFIG 里存在的键，配置文件里的未知键会被
丢弃；而下一次 save() 会把整个 self.data 写回去，那些键就永久消失了。
将来加字段后用户回退旧版本、或手动编辑过 config.json 时会丢配置。
"""

import json
import os
from pathlib import Path


def get_config_dir() -> Path:
    """和 accounts.py 保持一致"""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / "MCLuncher"
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_FILE = get_config_dir() / "config.json"

DEFAULT_CONFIG = {
    "minecraft_dir": "",      # 空字符串 = 用默认路径
    "java_path": "",          # 空 = 自动找
    "max_memory": 2048,       # MB
    "min_memory": 512,
    "close_on_launch": False, # 启动后关闭启动器
    "language": "zh_CN",      # 界面语言，见 core/i18n.py
    "theme": "system",        # system / dark / light，见 core/theme.py
    "accent_color": "",       # 空 = 用主题自带的强调色；否则 "#rrggbb"
}


class Config:
    """全局配置，单例式使用"""

    def __init__(self):
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                # 读字节：让 json 自己处理 BOM。手动存过 config.json 的话，
                # 编辑器可能加了 BOM，用 encoding="utf-8" 读会直接抛异常
                loaded = json.loads(CONFIG_FILE.read_bytes())
                if isinstance(loaded, dict):
                    # 和默认值合并，防止旧版本配置缺字段
                    self.data.update({k: loaded[k] for k in DEFAULT_CONFIG if k in loaded})
            except Exception as e:
                print(f"[Config] 读取失败: {e}")

    def save(self):
        CONFIG_FILE.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def get(self, key, default=None):
        return self.data.get(key, default if default is not None else DEFAULT_CONFIG.get(key))

    def set(self, key, value):
        self.data[key] = value
        self.save()

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
