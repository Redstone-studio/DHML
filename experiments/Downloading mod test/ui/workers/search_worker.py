"""
搜索后台线程
"""
from PyQt6.QtCore import QThread, pyqtSignal
from core import modrinth_api


class SearchWorker(QThread):
    results = pyqtSignal(list, int)   # hits, total

    def __init__(self, query: str = "",
                 mc_version: str = "全部",
                 loader: str = "全部",
                 category: str = "全部",
                 project_type: str = "mod",
                 sort: str = "relevance",
                 limit: int = 20,
                 offset: int = 0):
        super().__init__()
        self.query = query
        self.mc_version = mc_version
        self.loader = loader
        self.category = category
        self.project_type = project_type
        self.sort = sort
        # 分页：offset = 从第几条开始，limit = 这一批要几条
        self.limit = limit
        self.offset = offset

    def run(self):
        try:
            data = modrinth_api.search_projects(
                query=self.query,
                mc_version=self.mc_version,
                loader=self.loader,
                category=self.category,
                project_type=self.project_type,
                sort=self.sort,
                limit=self.limit,
                offset=self.offset,
            )
            hits = data.get("hits", [])
            total = data.get("total_hits", 0)
            self.results.emit(hits, total)
        except Exception as e:
            print(f"[SearchWorker] 失败: {e}")
            import traceback
            traceback.print_exc()
            self.results.emit([], 0)
