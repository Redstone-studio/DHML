"""
Modrinth API 封装
- 搜索项目
- 获取项目详情
- 获取版本列表
"""
import json
import requests
from urllib.parse import quote


API = "https://api.modrinth.com/v2"
HEADERS = {"User-Agent": "Mosslight-Launcher/1.0"}


def search_projects(query: str = "",
                    mc_version: str = "全部",
                    loader: str = "全部",
                    category: str = "全部",
                    project_type: str = "mod",
                    sort: str = "relevance",
                    limit: int = 20,
                    offset: int = 0) -> dict:
    """搜索项目

    返回：{"hits": [...], "total_hits": int, "offset": int, "limit": int}
    """
    facets = []

    # 项目类型（mod / shader / resourcepack / datapack / modpack）
    facets.append([f"project_type:{project_type}"])

    # MC 版本
    if mc_version and mc_version != "全部":
        facets.append([f"versions:{mc_version}"])

    # 加载器（只对 mod 有效）
    if loader and loader != "全部" and project_type == "mod":
        facets.append([f"categories:{loader}"])

    # 分类
    if category and category != "全部":
        facets.append([f"categories:{category}"])

    params = {
        "query": query,
        "facets": json.dumps(facets),
        "limit": limit,
        "offset": offset,
        "index": sort,
    }

    # 空查询 → 按下载量推荐
    if not query and sort == "relevance":
        params["index"] = "downloads"

    r = requests.get(f"{API}/search", params=params,
                     headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def get_project(project_id: str) -> dict:
    """获取项目详情（slug 或 project_id）"""
    r = requests.get(f"{API}/project/{project_id}",
                     headers=HEADERS, timeout=15)
    if r.status_code == 200:
        return r.json()
    return None


def get_project_versions(project_id: str) -> list:
    """获取项目的所有版本"""
    r = requests.get(f"{API}/project/{project_id}/version",
                     headers=HEADERS, timeout=20)
    if r.status_code == 200:
        return r.json()
    return []


def mcmod_search_url(name: str) -> str:
    """MC 百科搜索链接"""
    return f"https://search.mcmod.cn/s?key={quote(name)}"
