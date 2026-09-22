class VersionScanner:
    def __init__(self, mc_dir: Path = None):
        # mc_dir 默认 %APPDATA%/.minecraft
        ...
    
    def scan(self) -> list[dict]:
        """返回所有本地版本
        每个元素: {
            "id": "1.20.4",
            "type": "release",
            "path": Path(...),
            "jar": Path(...),
            "json": Path(...),
        }
        """
        ...
