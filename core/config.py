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
}


class Config:
    """全局配置，单例式使用"""

    def __init__(self):
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
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
