"""解压 natives

Minecraft 的部分依赖把动态库（.dll / .so / .dylib）放在**单独的 classifier jar** 里，
启动前要解到一个目录，再用 `-Djava.library.path` 之类的参数指过去。

为什么统一都解一遍（哪怕 1.19+ 其实能自己解压）：
- 1.18 及以前**必须**预先解好，否则游戏起不来
- 1.19 起 LWJGL 3 能自己从 classpath 解压，但目录仍然要存在
统一处理比按版本分叉可靠，代价也就是解几个小 jar。

解压规则照 JSON 里的 `extract.exclude` 走（通常是排除 `META-INF/`）。
"""

import zipfile
from pathlib import Path

from core.i18n import tr
from core.launch import natives_key, rules_allow


def natives_dir(version_dir, version_id: str) -> Path:
    """natives 目录的位置

    和 PCL / HMCL 的约定一致（`<版本目录>/<版本id>-natives`），
    所以它们装好的版本能直接复用。
    """
    return Path(version_dir) / f"{version_id}-natives"


def native_entries(version_json: dict) -> list:
    """列出这个版本需要的原生库 jar

    返回 [(库名, 本地 jar 路径, 解压排除规则)]。只保留在当前系统上适用的。
    """
    entries = []
    for lib in version_json.get("libraries") or []:
        if not rules_allow(lib.get("rules")):
            continue
        key = natives_key(lib)
        if not key:
            continue

        downloads = lib.get("downloads") or {}
        classifiers = downloads.get("classifiers") or {}
        artifact = classifiers.get(key) or {}
        rel = artifact.get("path")
        if not rel:
            # 老格式：只有 downloads.classifiers 没有 path 时，按坐标拼
            from core.launch import maven_to_path
            name = lib.get("name", "")
            rel_dir, _ = maven_to_path(name)
            if not rel_dir:
                continue
            # Maven 坐标里的 classifier 就是 natives_key
            parts = name.split(":")
            if len(parts) >= 3:
                group, artifact_name, version = parts[0], parts[1], parts[2]
                rel = (f"{group.replace('.', '/')}/{artifact_name}/{version}/"
                       f"{artifact_name}-{version}-{key}.jar")
            else:
                continue

        exclude = (lib.get("extract") or {}).get("exclude") or ["META-INF/"]
        entries.append((lib.get("name", "?"), rel, exclude))
    return entries


def ensure_natives(version_json, mc_dir, version_dir, version_id: str, log=None):
    """把 natives 解压好

    返回 (解压了多少个文件, 缺失的 jar 列表)。
    缺失不一定是致命的 —— 按调用方的策略决定要不要拦。
    """
    target = natives_dir(version_dir, version_id)
    target.mkdir(parents=True, exist_ok=True)

    libraries_root = Path(mc_dir) / "libraries"
    extracted = 0
    missing = []
    already = 0

    for name, rel, exclude in native_entries(version_json):
        jar_path = libraries_root / rel
        if not jar_path.is_file():
            missing.append(f"{name} ({rel})")
            continue

        marker = target / (Path(rel).name + ".extracted")
        if marker.is_file():
            already += 1
            continue

        try:
            with zipfile.ZipFile(jar_path) as zf:
                for member in zf.namelist():
                    if any(member.startswith(prefix) for prefix in exclude):
                        continue
                    if member.endswith("/"):
                        continue
                    zf.extract(member, target)
                    extracted += 1
            # 记个标记：下次不用重新解（按 jar 名区分）
            marker.write_text("", encoding="utf-8")
        except (zipfile.BadZipFile, OSError) as e:
            missing.append(f"{name} ({e})")

    if log:
        if extracted:
            log(tr("解压了 {n} 个原生库文件 → {path}", n=extracted, path=target))
        elif already:
            log(tr("原生库已经解压过了（{n} 个）", n=already))
    return extracted, missing
