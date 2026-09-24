"""补全缺失的库

有些版本是"装了一半"的 —— 比如 `1.12.2-Forge_14.23.5.2864` 缺了
`org.apache.maven:maven-artifact:3.5.3`，而 Forge 启动时非要它不可，
于是崩在 `ClassNotFoundException: ...ArtifactVersion` 上。

这个模块只做一件事：**把版本 JSON 里声明了下载地址、但本地没有的库下回来。**

几个原则：
- 只补「JSON 里写了 url」的（那种才是真的该有却没下到）。
  像 LWJGL 2 的 `lwjgl-platform` 本来就没有主 jar，不算缺。
- 先试 BMCLAPI 镜像，失败再回退官方源 —— BMCLAPI 是志愿者节点，
  不能当唯一来源（见交接说明 9.5）。
- 下到临时文件、校验 sha1、再原子改名。中途失败不会留下半个 jar
  骗过后面的检查。
"""

import hashlib
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from core.app_info import APP_VERSION
from core.i18n import tr
from core.launch import maven_to_path, rules_allow

# BMCLAPI 的 maven 路径前缀。它是镜像，不是唯一来源 —— 失败要能回退。
MIRROR_PREFIX = "https://bmclapi2.bangbang93.com/maven"

TIMEOUT = 30
# 下载镜像那边看到的名字。用完整名（带 Launcher），跟窗口标题一致；
# 版本号从 app_info 取，别在这儿再抄一份（抄了就一定会忘）
USER_AGENT = (f"MosslightLauncher/{APP_VERSION.lstrip('v')} "
              "(+https://github.com/Redstone-studio/DHML)")


def _mirror_url(url: str) -> str:
    """把 Mojang 的 maven 地址换成 BMCLAPI 镜像地址"""
    for prefix in ("https://libraries.minecraft.net/", "http://libraries.minecraft.net/"):
        if url.startswith(prefix):
            return MIRROR_PREFIX + "/" + url[len(prefix):]
    return ""


def missing_libraries(mc_dir, version_json: dict) -> list:
    """列出「JSON 说有、本地却没有」的库

    返回 [{name, path, url, sha1, size}]，path 是相对 libraries/ 的路径。
    """
    mc_dir = Path(mc_dir)
    result = []
    seen = set()
    for lib in version_json.get("libraries") or []:
        if not rules_allow(lib.get("rules")):
            continue
        artifact = (lib.get("downloads") or {}).get("artifact") or {}
        rel = artifact.get("path")
        url = artifact.get("url")
        if not rel or not url:
            # 没有下载地址的：要么是老格式（用坐标拼），要么本来就没有主 jar
            rel, _ = maven_to_path(lib.get("name", ""))
            if not rel:
                continue
            if (mc_dir / "libraries" / rel).is_file():
                continue
            # 老格式没有 url，补不了，跳过（不算"缺"）
            continue
        target = mc_dir / "libraries" / rel
        if target.is_file() or rel in seen:
            continue
        seen.add(rel)
        result.append({
            "name": lib.get("name", "?"),
            "path": rel,
            "url": url,
            "sha1": (artifact.get("sha1") or "").lower(),
            "size": artifact.get("size") or 0,
        })
    return result


def _download_one(url: str, dest: Path, sha1: str, log) -> bool:
    """下载一个文件到 dest（先写 .part 再改名）"""
    temp = dest.with_suffix(dest.suffix + ".part")
    candidates = [u for u in (_mirror_url(url), url) if u]
    for candidate in candidates:
        try:
            request = urllib.request.Request(candidate, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data = response.read()
        except (urllib.error.URLError, OSError, ValueError) as e:
            log(tr("下载失败（{url}）：{err}", url=candidate, err=e))
            continue

        if sha1:
            actual = hashlib.sha1(data).hexdigest()
            if actual != sha1:
                log(tr("校验不通过（{url}）：期望 {want}，实际 {got}",
                       url=candidate, want=sha1[:8], got=actual[:8]))
                continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(data)
        shutil.move(str(temp), str(dest))     # 原子改名，别留半个文件
        return True
    return False


def repair(mc_dir, version_json: dict, log=None) -> tuple:
    """把缺的库补齐

    返回 (补好了几个, 失败列表)。log 是接受字符串的回调（可选）。
    """
    log = log or (lambda _msg: None)
    todo = missing_libraries(mc_dir, version_json)
    if not todo:
        return 0, []

    log(tr("发现 {n} 个库不在本地，开始补全…", n=len(todo)))
    done, failed = 0, []
    for i, item in enumerate(todo, 1):
        dest = Path(mc_dir) / "libraries" / item["path"]
        log(tr("[{i}/{n}] {name}", i=i, n=len(todo), name=item["name"]))
        if _download_one(item["url"], dest, item["sha1"], log):
            done += 1
        else:
            failed.append(item["name"])

    if done:
        log(tr("补全了 {n} 个库", n=done))
    return done, failed
