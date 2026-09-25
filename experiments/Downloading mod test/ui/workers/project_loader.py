"""
后台加载 mod 详情 + 版本列表
"""
import re
import requests
from PyQt6.QtCore import QThread, pyqtSignal


MODRINTH_API = "https://api.modrinth.com/v2"
HEADERS = {"User-Agent": "Mosslight-Launcher/1.0"}


def fetch_modrinth_project(mod_id: str):
    try:
        r = requests.get(f"{MODRINTH_API}/project/{mod_id}",
                         timeout=15, headers=HEADERS)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[ProjectLoader] 查询失败: {e}")
    return None


def fetch_all_versions(slug: str) -> list:
    try:
        r = requests.get(f"{MODRINTH_API}/project/{slug}/version",
                         timeout=20, headers=HEADERS)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[ProjectLoader] 版本查询失败: {e}")
    return []


class ProjectLoader(QThread):
    loaded = pyqtSignal(dict, list)   # (project, versions)
    failed = pyqtSignal(str)

    def __init__(self, mod_id: str):
        super().__init__()
        self.mod_id = mod_id

    def run(self):
        project = fetch_modrinth_project(self.mod_id)
        if not project:
            slug = re.sub(r"[^a-z0-9\-]", "", self.mod_id.lower().replace(" ", "-"))
            if slug and slug != self.mod_id:
                project = fetch_modrinth_project(slug)
        if not project:
            self.failed.emit(self.mod_id)
            return
        versions = fetch_all_versions(project["slug"])
        self.loaded.emit(project, versions)
