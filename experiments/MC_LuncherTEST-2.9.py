"""
MC 启动器测试窗口 v2.9
- 版本下拉框（BMCLAPI 官方版本 + 本地版本分组）
- 原版下载
- Fabric 下载 + 安装（loader + API）
- Forge 下载 + 安装（暂时禁用按钮）
- 原版 + Forge + Fabric 启动（inheritsFrom 合并）
- 实时显示日志

v2.9 变化：
- 修复 Fabric library 的 Maven 格式下载（之前"共 0 个库"的 bug）
- 修复启动时 classpath 拼装：支持 Maven 格式 library
"""

import sys
import json
import platform
import subprocess
import threading
import hashlib
import zipfile
import shlex
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel, QLineEdit, QFormLayout, QGroupBox,
    QMessageBox, QComboBox, QDialog, QDialogButtonBox, QCheckBox
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QFont


# ============================================================
# 默认配置
# ============================================================
DEFAULT_MC_DIR = r"D:\DHML\.minecraft"
DEFAULT_JAVA = r"C:\Users\yexia\AppData\Roaming\.minecraft\runtime\java-runtime-delta\bin\java.exe"
DEFAULT_USERNAME = "BaBaLe"
DEFAULT_MEMORY = 8192
# ============================================================


# ============================================================
# 通用工具
# ============================================================
def rules_allow(rules):
    if not rules:
        return True
    sys_name = platform.system()
    current_os = {"Windows": "windows", "Darwin": "osx", "Linux": "linux"}.get(sys_name, "")
    current_arch = "x86_64" if platform.machine() in ("AMD64", "x86_64") else platform.machine().lower()
    for rule in rules:
        action = rule.get("action")
        os_rule = rule.get("os", {})
        os_name = os_rule.get("name")
        os_arch = os_rule.get("arch")
        if os_name and os_name != current_os:
            continue
        if os_arch:
            if os_arch == "x86" and current_arch != "x86":
                continue
            if os_arch == "x86_64" and current_arch != "x86_64":
                continue
        if "features" in rule:
            continue
        if action == "allow":
            return True
        if action == "disallow":
            return False
    return False


def get_natives_key(lib):
    natives = lib.get("natives", {})
    if not natives:
        return None
    sys_name = platform.system()
    os_key = {"Windows": "windows", "Darwin": "osx", "Linux": "linux"}.get(sys_name, "")
    key = natives.get(os_key)
    if not key:
        return None
    if "${arch}" in key:
        arch = "64" if platform.machine() in ("AMD64", "x86_64") else "32"
        key = key.replace("${arch}", arch)
    return key


def maven_to_path(name):
    """把 Maven 坐标转换成路径
    
    "net.fabricmc:fabric-loader:0.19.5"
        → ("net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar", "net/fabricmc/fabric-loader/0.19.5")
    """
    if not name or ":" not in name:
        return None, None
    parts = name.split(":")
    if len(parts) < 3:
        return None, None

    group = parts[0]
    artifact = parts[1]
    version = parts[2]
    classifier = parts[3] if len(parts) > 3 else None

    group_path = group.replace(".", "/")
    if classifier:
        filename = f"{artifact}-{version}-{classifier}.jar"
    else:
        filename = f"{artifact}-{version}.jar"

    rel_dir = f"{group_path}/{artifact}/{version}"
    rel_path = f"{rel_dir}/{filename}"
    return rel_path, rel_dir


def make_log_fn(log_callback):
    if log_callback is None:
        return print
    if hasattr(log_callback, "emit"):
        return log_callback.emit
    return log_callback


# ============================================================
# 版本清单拉取
# ============================================================
class ManifestFetcher(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal(list)

    def run(self):
        try:
            self.log.emit("正在获取版本清单...")
            url = "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json"
            r = requests.get(url, timeout=30, headers={"User-Agent": "DHML/1.0"})
            r.raise_for_status()
            data = r.json()
            versions = data.get("versions", [])
            self.log.emit(f"✓ 获取到 {len(versions)} 个版本")
            self.finished.emit(versions)
        except Exception as e:
            import traceback
            self.log.emit(f"❌ 获取版本清单失败: {e}")
            self.log.emit(traceback.format_exc())
            self.finished.emit([])


# ============================================================
# 下载器
# ============================================================
class Downloader:
    def __init__(self, log_callback=None):
        self.log = make_log_fn(log_callback)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "DHML/1.0"})

    @staticmethod
    def replace_mirror(url):
        if not url:
            return url
        return (url
            .replace("piston-data.mojang.com", "bmclapi2.bangbang93.com")
            .replace("piston-meta.mojang.com", "bmclapi2.bangbang93.com")
            .replace("libraries.minecraft.net", "bmclapi2.bangbang93.com/maven")
            .replace("resources.download.minecraft.net", "bmclapi2.bangbang93.com/assets")
            .replace("launcher.mojang.com", "bmclapi2.bangbang93.com")
            .replace("launchermeta.mojang.com", "bmclapi2.bangbang93.com")
            .replace("maven.minecraftforge.net", "bmclapi2.bangbang93.com/maven")
            .replace("maven.neoforged.net", "bmclapi2.bangbang93.com/maven")
        )

    @staticmethod
    def sha1_file(path):
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def download_file(self, url, save_path, sha1=None, retries=2):
        save_path = Path(save_path)

        if save_path.exists() and sha1:
            try:
                if self.sha1_file(save_path) == sha1:
                    return True
            except:
                pass

        save_path.parent.mkdir(parents=True, exist_ok=True)
        mirror_url = self.replace_mirror(url)
        urls_to_try = [mirror_url]
        if mirror_url != url:
            urls_to_try.append(url)

        last_error = None
        for target_url in urls_to_try:
            for attempt in range(retries):
                try:
                    r = self.session.get(target_url, stream=True, timeout=30)
                    r.raise_for_status()
                    tmp = save_path.with_suffix(save_path.suffix + ".tmp")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
                    if sha1:
                        actual = self.sha1_file(tmp)
                        if actual != sha1:
                            tmp.unlink()
                            self.log(f"❌ SHA1 不匹配: {save_path.name}")
                            continue
                    if save_path.exists():
                        save_path.unlink()
                    tmp.rename(save_path)
                    return True
                except requests.HTTPError as e:
                    last_error = e
                    if e.response.status_code == 404:
                        break
                except Exception as e:
                    last_error = e
                    if attempt == retries - 1:
                        break

        self.log(f"❌ 下载失败 {save_path.name}: {last_error}")
        return False

    def download_batch(self, tasks, max_workers=16, progress_cb=None):
        total = len(tasks)
        done = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.download_file, t["url"], t["path"], t.get("sha1")): t
                for t in tasks
            }
            for future in as_completed(futures):
                ok = future.result()
                done += 1
                if not ok:
                    failed += 1
                if progress_cb and (done % 20 == 0 or done == total):
                    progress_cb(done, total, failed)
        return done, failed

    def install_version(self, version_id, mc_dir, version_json_url, version_json_sha1=None, progress_cb=None):
        mc_dir = Path(mc_dir)

        self.log(f"[1/5] 下载版本 JSON...")
        version_dir = mc_dir / "versions" / version_id
        version_dir.mkdir(parents=True, exist_ok=True)
        json_path = version_dir / f"{version_id}.json"

        if not self.download_file(version_json_url, json_path, version_json_sha1):
            raise RuntimeError("版本 JSON 下载失败")

        with open(json_path, "r", encoding="utf-8") as f:
            vj = json.load(f)

        self.log(f"[2/5] 收集下载任务...")
        tasks = []

        client = vj["downloads"]["client"]
        tasks.append({"url": client["url"], "path": version_dir / f"{version_id}.jar", "sha1": client["sha1"]})

        for lib in vj["libraries"]:
            if not rules_allow(lib.get("rules", [])):
                continue
            downloads = lib.get("downloads", {})
            artifact = downloads.get("artifact")
            if artifact:
                tasks.append({
                    "url": artifact["url"],
                    "path": mc_dir / "libraries" / artifact["path"],
                    "sha1": artifact["sha1"],
                })
            classifiers = downloads.get("classifiers", {})
            nk = get_natives_key(lib)
            if nk and nk in classifiers:
                nat = classifiers[nk]
                tasks.append({
                    "url": nat["url"],
                    "path": mc_dir / "libraries" / nat["path"],
                    "sha1": nat["sha1"],
                })

        ai = vj["assetIndex"]
        asset_index_path = mc_dir / "assets" / "indexes" / f"{ai['id']}.json"
        tasks.append({"url": ai["url"], "path": asset_index_path, "sha1": ai["sha1"]})

        if "logging" in vj:
            lf = vj["logging"]["client"]["file"]
            tasks.append({
                "url": lf["url"],
                "path": mc_dir / "assets" / "log_configs" / lf["id"],
                "sha1": lf["sha1"],
            })

        self.log(f"    共 {len(tasks)} 个文件")

        self.log(f"[3/5] 下载 libraries + client...")
        done, failed = self.download_batch(tasks, max_workers=16, progress_cb=progress_cb)
        self.log(f"    完成 {done}/{len(tasks)}, 失败 {failed}")
        if failed > len(tasks) * 0.1:
            raise RuntimeError(f"{failed}/{len(tasks)} 个文件失败，超过 10% 阈值")
        elif failed > 0:
            self.log(f"⚠ 有 {failed} 个文件失败，继续安装")

        self.log(f"[4/5] 下载资源文件（最慢）...")
        if asset_index_path.exists():
            with open(asset_index_path, "r", encoding="utf-8") as f:
                index_data = json.load(f)
            asset_tasks = []
            for name, obj in index_data["objects"].items():
                h = obj["hash"]
                sub = h[:2]
                asset_tasks.append({
                    "url": f"https://resources.download.minecraft.net/{sub}/{h}",
                    "path": mc_dir / "assets" / "objects" / sub / h,
                    "sha1": h,
                })
            self.log(f"    共 {len(asset_tasks)} 个资源文件")
            done, failed = self.download_batch(asset_tasks, max_workers=32, progress_cb=progress_cb)
            self.log(f"    完成 {done}/{len(asset_tasks)}, 失败 {failed}")

        self.log(f"[5/5] 解压 natives...")
        self._extract_natives(vj, mc_dir, version_dir)

        self.log(f"✅ {version_id} 安装完成！")
        return version_dir

    def _extract_natives(self, vj, mc_dir, version_dir):
        natives_dir = version_dir / f"{version_dir.name}-natives"
        natives_dir.mkdir(parents=True, exist_ok=True)
        if any(natives_dir.glob("*.dll")):
            return
        for lib in vj["libraries"]:
            downloads = lib.get("downloads", {})
            classifiers = downloads.get("classifiers", {})
            nk = get_natives_key(lib)
            if nk and nk in classifiers:
                jar_path = mc_dir / "libraries" / classifiers[nk]["path"]
                if jar_path.exists():
                    try:
                        with zipfile.ZipFile(jar_path, "r") as z:
                            for name in z.namelist():
                                if name.startswith("META-INF/"):
                                    continue
                                z.extract(name, natives_dir)
                    except Exception as e:
                        self.log(f"⚠ 解压 {jar_path.name} 失败: {e}")


# ============================================================
# Forge 下载器
# ============================================================
class ForgeDownloader:
    BMCLAPI = "https://bmclapi2.bangbang93.com"

    def __init__(self, log_callback=None):
        self.log = make_log_fn(log_callback)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "DHML/1.0"})

    def get_forge_versions(self, mc_version):
        url = f"{self.BMCLAPI}/forge/minecraft/{mc_version}"
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()

        result = []
        for item in data:
            forge_ver = item.get("version", "")
            build = item.get("build", 0)
            modified = item.get("modified", "")
            installer = None
            for f in item.get("files", []):
                if f.get("category") == "installer" and f.get("format") == "jar":
                    installer = f
                    break
            result.append({
                "mcversion": mc_version,
                "version": forge_ver,
                "build": build,
                "modified": modified,
                "installer_hash": installer.get("hash") if installer else None,
            })
        result.sort(key=lambda x: x["build"], reverse=True)
        return result

    def download_installer(self, mc_version, forge_version, save_path, progress_cb=None):
        url = f"{self.BMCLAPI}/forge/download"
        params = {
            "mcversion": mc_version,
            "version": forge_version,
            "category": "installer",
            "format": "jar",
        }
        r = self.session.get(url, params=params, stream=True, timeout=60)
        r.raise_for_status()

        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb(done, total)
        return save_path

    def install_forge(self, installer_jar, mc_dir, java_path):
        cmd = [
            java_path,
            "-jar", str(installer_jar),
            "--installClient", str(mc_dir),
        ]
        self.log(f"运行安装器: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(installer_jar.parent),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        for line in process.stdout:
            line = line.rstrip()
            if line:
                self.log(f"[Forge] {line}")
        rc = process.wait()
        if rc != 0:
            raise RuntimeError(f"Forge 安装器返回码: {rc}")
        return rc


# ============================================================
# Fabric 安装器
# ============================================================
class FabricInstaller:
    META = "https://meta.fabricmc.net"
    MODRINTH = "https://api.modrinth.com/v2"

    def __init__(self, log_callback=None):
        self.log = make_log_fn(log_callback)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "DHML/1.0"})

    def get_loader_versions(self, mc_version):
        url = f"{self.META}/v2/versions/loader/{mc_version}"
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        result = []
        for item in data:
            loader = item.get("loader", {})
            result.append({
                "version": loader.get("version", ""),
                "stable": loader.get("stable", False),
            })
        return result

    def get_api_versions(self, mc_version):
        url = f"{self.MODRINTH}/project/fabric-api/version"
        params = {
            "game_versions": json.dumps([mc_version]),
            "loaders": json.dumps(["fabric"]),
        }
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        result = []
        for item in data:
            files = item.get("files", [])
            primary = next((f for f in files if f.get("primary")), None)
            if not primary and files:
                primary = files[0]
            if not primary:
                continue
            result.append({
                "version_number": item.get("version_number", ""),
                "date_published": item.get("date_published", ""),
                "download_url": primary.get("url", ""),
                "filename": primary.get("filename", ""),
                "size": primary.get("size", 0),
                "sha1": primary.get("hashes", {}).get("sha1"),
            })
        result.sort(key=lambda x: x["date_published"], reverse=True)
        return result

    def install(self, mc_version, loader_version, mc_dir,
                api_version_info=None, isolated=False, progress_cb=None):
        mc_dir = Path(mc_dir)

        # 1. 拿 Fabric 版本 JSON
        self.log(f"[1/4] 获取 Fabric 版本 JSON...")
        url = f"{self.META}/v2/versions/loader/{mc_version}/{loader_version}/profile/json"
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        vj = r.json()

        # 2. 保存版本 JSON
        version_id = f"{mc_version}-Fabric {loader_version}"
        version_dir = mc_dir / "versions" / version_id
        version_dir.mkdir(parents=True, exist_ok=True)

        vj["id"] = version_id
        vj["inheritsFrom"] = mc_version

        json_path = version_dir / f"{version_id}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(vj, f, ensure_ascii=False, indent=2)
        self.log(f"    ✓ 版本目录: {version_id}")

        # 3. 下载 Fabric libraries（支持 Maven 格式）
        self.log(f"[2/4] 下载 Fabric libraries...")
        tasks = []
        for lib in vj.get("libraries", []):
            if not rules_allow(lib.get("rules", [])):
                continue

            name = lib.get("name", "")
            downloads = lib.get("downloads", {})
            artifact = downloads.get("artifact")

            if artifact:
                # 标准格式
                tasks.append({
                    "url": artifact["url"],
                    "path": mc_dir / "libraries" / artifact["path"],
                    "sha1": artifact.get("sha1"),
                })
            elif name and ":" in name:
                # Fabric 的 Maven 格式
                rel_path, _ = maven_to_path(name)
                if rel_path:
                    repo_url = lib.get("url", "https://maven.fabricmc.net/")
                    if not repo_url.endswith("/"):
                        repo_url += "/"
                    tasks.append({
                        "url": repo_url + rel_path,
                        "path": mc_dir / "libraries" / rel_path,
                        "sha1": None,
                    })

        self.log(f"    共 {len(tasks)} 个库")
        dl = Downloader(log_callback=self.log)
        done, failed = dl.download_batch(tasks, max_workers=16, progress_cb=progress_cb)
        self.log(f"    完成 {done}/{len(tasks)}, 失败 {failed}")
        if failed > len(tasks) * 0.2:
            raise RuntimeError(f"{failed}/{len(tasks)} 个库失败")

        # 4. Fabric API
        if api_version_info:
            self.log(f"[3/4] 下载 Fabric API...")
            if isolated:
                mods_dir = version_dir / "mods"
            else:
                mods_dir = mc_dir / "mods"
            mods_dir.mkdir(parents=True, exist_ok=True)
            mod_path = mods_dir / api_version_info["filename"]
            if dl.download_file(
                api_version_info["download_url"],
                mod_path,
                sha1=api_version_info.get("sha1"),
            ):
                self.log(f"    ✓ {api_version_info['filename']}")
                self.log(f"    保存到: {mod_path}")
            else:
                self.log(f"    ⚠ Fabric API 下载失败，跳过")
        else:
            self.log(f"[3/4] 跳过 Fabric API")

        self.log(f"[4/4] 完成")
        self.log(f"✅ Fabric {loader_version} for {mc_version} 安装完成！")
        if api_version_info:
            self.log(f"   Fabric API: {api_version_info['version_number']}")
        return version_dir


# ============================================================
# 启动线程（支持 inheritsFrom 合并 + Maven 格式 library）
# ============================================================
class Worker(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.process = None
        self._stopped = False

    def launch(self):
        try:
            cmd = self._build_command()
            self.log.emit("=" * 60)
            self.log.emit("完整命令:")
            self.log.emit("-" * 60)
            for a in cmd:
                self.log.emit(f"  {a}" if len(a) < 120 else f"  {a[:117]}...")
            self.log.emit("-" * 60)
            self.log.emit(f"[{self._ts()}] 启动进程...")
            self.process = subprocess.Popen(
                cmd,
                cwd=str(self.config["version_dir"]),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            self.log.emit(f"[{self._ts()}] ✓ 进程已启动, PID={self.process.pid}")
            for line in self.process.stdout:
                if self._stopped:
                    break
                self.log.emit(line.rstrip())
            rc = self.process.wait()
            self.log.emit(f"[{self._ts()}] 进程退出, 返回码={rc}")
        except FileNotFoundError as e:
            self.log.emit(f"❌ 找不到文件: {e}")
        except Exception as e:
            self.log.emit(f"❌ 启动失败: {type(e).__name__}: {e}")
            import traceback
            self.log.emit(traceback.format_exc())
        finally:
            self.finished.emit()

    def stop(self):
        self._stopped = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.log.emit(f"[{self._ts()}] 已发送终止信号")

    def _load_version_with_inherits(self, mc_dir, version_id):
        cache = {}

        def _load(vid, depth=0):
            if vid in cache:
                return cache[vid]
            if depth > 5:
                raise RuntimeError(f"版本继承链过深: {vid}")
            version_dir = mc_dir / "versions" / vid
            json_path = version_dir / f"{vid}.json"
            if not json_path.exists():
                raise FileNotFoundError(f"版本 JSON 不存在: {json_path}")
            with open(json_path, "r", encoding="utf-8") as f:
                vj = json.load(f)
            parent_id = vj.get("inheritsFrom")
            if parent_id:
                self.log.emit(f"  加载: {vid}  (继承自 {parent_id})")
                parent = _load(parent_id, depth + 1)
                merged = self._merge_version(parent, vj)
            else:
                self.log.emit(f"  加载: {vid}")
                merged = vj
            cache[vid] = merged
            return merged

        return _load(version_id)

    def _merge_version(self, parent, child):
        libs_by_name = {}
        for lib in parent.get("libraries", []):
            libs_by_name[lib.get("name", "")] = lib
        for lib in child.get("libraries", []):
            libs_by_name[lib.get("name", "")] = lib

        main_class = child.get("mainClass") or parent.get("mainClass")

        child_args = child.get("arguments")
        parent_args = parent.get("arguments")
        if child_args and child_args.get("jvm") and child_args.get("game"):
            arguments = child_args
        elif parent_args and child_args:
            arguments = {
                "jvm": parent_args.get("jvm", []) + child_args.get("jvm", []),
                "game": parent_args.get("game", []) + child_args.get("game", []),
            }
        elif parent_args:
            arguments = parent_args
        elif child_args:
            arguments = child_args
        else:
            arguments = {"jvm": [], "game": []}

        mc_args = child.get("minecraftArguments") or parent.get("minecraftArguments")
        asset_index = child.get("assetIndex") or parent.get("assetIndex")
        downloads = dict(parent.get("downloads", {}))
        downloads.update(child.get("downloads", {}))
        java_version = child.get("javaVersion") or parent.get("javaVersion", {})
        logging_cfg = child.get("logging") or parent.get("logging")

        return {
            "id": child.get("id", ""),
            "mainClass": main_class,
            "javaVersion": java_version,
            "arguments": arguments,
            "minecraftArguments": mc_args,
            "libraries": list(libs_by_name.values()),
            "assetIndex": asset_index,
            "downloads": downloads,
            "logging": logging_cfg,
            "type": child.get("type") or parent.get("type", "release"),
            "assets": child.get("assets") or parent.get("assets"),
        }

    def _get_root_parent(self, mc_dir, version_id):
        current = version_id
        for _ in range(10):
            json_path = mc_dir / "versions" / current / f"{current}.json"
            if not json_path.exists():
                break
            with open(json_path, "r", encoding="utf-8") as f:
                vj = json.load(f)
            parent = vj.get("inheritsFrom")
            if not parent:
                return current
            current = parent
        return version_id

    def _resolve_library_path(self, mc_dir, lib):
        """从一个 library 解析出 jar 的本地路径（支持 Maven 格式）"""
        downloads = lib.get("downloads", {})
        artifact = downloads.get("artifact")
        if artifact:
            return mc_dir / "libraries" / artifact["path"]

        # Maven 格式
        name = lib.get("name", "")
        rel_path, _ = maven_to_path(name)
        if rel_path:
            return mc_dir / "libraries" / rel_path

        return None

    def _build_command(self):
        mc_dir = Path(self.config["mc_dir"])
        version = self.config["version"]
        username = self.config["username"]
        memory = self.config["memory"]
        java = self.config["java"]
        version_dir = self.config["version_dir"]

        self.log.emit(f"[启动] 加载版本 {version}")
        vj = self._load_version_with_inherits(mc_dir, version)
        self.log.emit(f"[启动] 合并后 {len(vj['libraries'])} 个 library")
        self.log.emit(f"[启动] 主类: {vj['mainClass']}")

        classpath = []
        for lib in vj["libraries"]:
            if not rules_allow(lib.get("rules", [])):
                continue
            jar_path = self._resolve_library_path(mc_dir, lib)
            if jar_path:
                classpath.append(str(jar_path))

        client_jar = version_dir / f"{version}.jar"
        if client_jar.exists():
            classpath.append(str(client_jar))
        else:
            parent_id = self._get_root_parent(mc_dir, version)
            parent_jar = mc_dir / "versions" / parent_id / f"{parent_id}.jar"
            if parent_jar.exists():
                classpath.append(str(parent_jar))
                self.log.emit(f"[启动] 使用父版本 jar: {parent_id}.jar")

        sep = ";" if sys.platform == "win32" else ":"
        classpath_str = sep.join(classpath)
        natives_dir = version_dir / f"{version}-natives"
        library_dir = mc_dir / "libraries"

        replacements = {
            "${natives_directory}": str(natives_dir),
            "${classpath}": classpath_str,
            "${classpath_separator}": sep,
            "${library_directory}": str(library_dir),
            "${launcher_name}": "DHML",
            "${launcher_version}": "1.0.0",
            "${auth_player_name}": username,
            "${version_name}": version,
            "${game_directory}": str(version_dir),
            "${assets_root}": str(mc_dir / "assets"),
            "${assets_index_name}": (vj.get("assetIndex") or {}).get("id", ""),
            "${auth_uuid}": "00000000000000000000000000000001",
            "${auth_access_token}": "0",
            "${clientid}": "",
            "${auth_xuid}": "",
            "${user_type}": "legacy",
            "${version_type}": vj.get("type", "release"),
            "${resolution_width}": "854",
            "${resolution_height}": "480",
            "${user_properties}": "{}",
        }

        jvm_args = [java, f"-Xmx{memory}m"]

        if vj.get("arguments") and vj["arguments"].get("jvm"):
            for arg in vj["arguments"]["jvm"]:
                jvm_args.extend(self._resolve_arg(arg, replacements))
        else:
            jvm_args.extend([
                f"-Djava.library.path={natives_dir}",
                f"-Djna.tmpdir={natives_dir}",
                f"-Dorg.lwjgl.system.SharedLibraryExtractPath={natives_dir}",
                f"-Dio.netty.native.workdir={natives_dir}",
                "-Dminecraft.launcher.brand=DHML",
                "-Dminecraft.launcher.version=1.0.0",
                "-cp", classpath_str,
            ])

        game_args = [vj["mainClass"]]

        if vj.get("arguments") and vj["arguments"].get("game"):
            for arg in vj["arguments"]["game"]:
                game_args.extend(self._resolve_arg(arg, replacements))
        elif vj.get("minecraftArguments"):
            raw = vj["minecraftArguments"]
            for k, val in replacements.items():
                raw = raw.replace(k, val)
            try:
                game_args.extend(shlex.split(raw, posix=False))
            except:
                game_args.extend(raw.split())

        cmd = jvm_args + game_args
        cmd.insert(1, "-Dstdout.encoding=utf-8")
        cmd.insert(2, "-Dstderr.encoding=utf-8")
        return cmd

    def _resolve_arg(self, arg, replacements):
        if isinstance(arg, str):
            for k, v in replacements.items():
                arg = arg.replace(k, v)
            return [arg] if arg else []
        if isinstance(arg, dict):
            if "rules" in arg and not rules_allow(arg["rules"]):
                return []
            value = arg.get("value", [])
            if isinstance(value, str):
                value = [value]
            result = []
            for v in value:
                for k, val in replacements.items():
                    v = v.replace(k, val)
                result.append(v)
            return result
        return []

    @staticmethod
    def _ts():
        return datetime.now().strftime("%H:%M:%S")


# ============================================================
# 原版下载线程
# ============================================================
class DownloadWorker(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal(bool)
    progress = pyqtSignal(int, int, int)

    def __init__(self, version_id, mc_dir, version_json_url, version_json_sha1):
        super().__init__()
        self.version_id = version_id
        self.mc_dir = mc_dir
        self.version_json_url = version_json_url
        self.version_json_sha1 = version_json_sha1

    def run(self):
        try:
            dl = Downloader(log_callback=self.log)
            dl.install_version(
                self.version_id, self.mc_dir,
                self.version_json_url, self.version_json_sha1,
                progress_cb=lambda d, t, f: self.progress.emit(d, t, f),
            )
            self.finished.emit(True)
        except Exception as e:
            self.log.emit(f"❌ 下载失败: {type(e).__name__}: {e}")
            import traceback
            self.log.emit(traceback.format_exc())
            self.finished.emit(False)


# ============================================================
# Forge 安装线程
# ============================================================
class ForgeInstallWorker(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal(bool)
    progress = pyqtSignal(int, int)

    def __init__(self, mc_version, forge_version, mc_dir, java_path):
        super().__init__()
        self.mc_version = mc_version
        self.forge_version = forge_version
        self.mc_dir = mc_dir
        self.java_path = java_path

    def run(self):
        try:
            dl = ForgeDownloader(log_callback=self.log)
            installer_name = f"forge-{self.mc_version}-{self.forge_version}-installer.jar"
            installer_path = Path(self.mc_dir) / "temp" / installer_name

            self.log.emit(f"[1/2] 下载 Forge installer...")
            dl.download_installer(
                self.mc_version, self.forge_version,
                installer_path,
                progress_cb=lambda d, t: self.progress.emit(d, t),
            )
            self.log.emit(f"    ✓ 下载完成: {installer_name}")

            self.log.emit(f"[2/2] 运行 Forge 安装器（可能几分钟）...")
            dl.install_forge(installer_path, Path(self.mc_dir), self.java_path)

            try:
                installer_path.unlink()
            except:
                pass

            self.log.emit(f"✅ Forge {self.forge_version} for {self.mc_version} 安装完成！")
            self.finished.emit(True)
        except Exception as e:
            self.log.emit(f"❌ Forge 安装失败: {type(e).__name__}: {e}")
            import traceback
            self.log.emit(traceback.format_exc())
            self.finished.emit(False)


# ============================================================
# Fabric 安装线程
# ============================================================
class FabricInstallWorker(QObject):
    log = pyqtSignal(str)
    finished = pyqtSignal(bool)
    progress = pyqtSignal(int, int, int)

    def __init__(self, mc_version, loader_version, mc_dir, api_info, isolated):
        super().__init__()
        self.mc_version = mc_version
        self.loader_version = loader_version
        self.mc_dir = mc_dir
        self.api_info = api_info
        self.isolated = isolated

    def run(self):
        try:
            installer = FabricInstaller(log_callback=self.log)
            installer.install(
                self.mc_version,
                self.loader_version,
                self.mc_dir,
                api_version_info=self.api_info,
                isolated=self.isolated,
                progress_cb=lambda d, t, f: self.progress.emit(d, t, f),
            )
            self.finished.emit(True)
        except Exception as e:
            self.log.emit(f"❌ Fabric 安装失败: {type(e).__name__}: {e}")
            import traceback
            self.log.emit(traceback.format_exc())
            self.finished.emit(False)


# ============================================================
# Forge 选择对话框
# ============================================================
class ForgeSelectDialog(QDialog):
    def __init__(self, mc_version, forge_list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"选择 Forge 版本 - MC {mc_version}")
        self.resize(480, 380)
        self.selected = None

        layout = QVBoxLayout(self)
        title = QLabel(f"MC {mc_version} 可用的 Forge 版本（{len(forge_list)} 个）")
        title.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        layout.addWidget(title)

        self.combo = QComboBox()
        for item in forge_list:
            label = f"{item['version']}  (build {item['build']})  {item.get('modified', '')[:10]}"
            self.combo.addItem(label, userData=item)
        layout.addWidget(self.combo)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.combo.currentIndexChanged.connect(self._on_change)
        self._on_change()
        layout.addStretch()

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_change(self):
        item = self.combo.currentData()
        if item:
            self.info.setText(
                f"MC 版本: {item['mcversion']}\n"
                f"Forge 版本: {item['version']}\n"
                f"Build: {item['build']}\n"
                f"发布时间: {item.get('modified', '')}"
            )

    def _on_ok(self):
        self.selected = self.combo.currentData()
        self.accept()


# ============================================================
# Fabric 选择对话框
# ============================================================
class FabricSelectDialog(QDialog):
    def __init__(self, mc_version, loader_list, api_list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"安装 Fabric - MC {mc_version}")
        self.resize(540, 480)
        self.selected = None

        layout = QVBoxLayout(self)

        title = QLabel(f"安装 Fabric - MC {mc_version}")
        title.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        layout.addWidget(title)

        layout.addWidget(QLabel("Fabric Loader"))
        self.loader_combo = QComboBox()
        for item in loader_list:
            stable = " [稳定]" if item.get("stable") else ""
            self.loader_combo.addItem(f"{item['version']}{stable}", userData=item)
        layout.addWidget(self.loader_combo)

        self.loader_info = QLabel("")
        layout.addWidget(self.loader_info)

        layout.addWidget(QLabel("Fabric API"))
        self.api_combo = QComboBox()
        self.api_combo.addItem("（不安装 Fabric API）", userData=None)
        for item in api_list:
            date = item.get("date_published", "")[:10]
            label = f"{item['version_number']}  ({date})"
            self.api_combo.addItem(label, userData=item)
        layout.addWidget(self.api_combo)

        self.api_info = QLabel("")
        layout.addWidget(self.api_info)

        self.isolated_check = QCheckBox("启用版本隔离（mods 放到版本目录内）")
        self.isolated_check.setChecked(False)
        layout.addWidget(self.isolated_check)

        hint = QLabel(
            "提示：\n"
            "· 不开启版本隔离 → mods 放 .minecraft/mods/\n"
            "· 开启版本隔离 → mods 放 versions/版本名/mods/"
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)

        layout.addStretch()

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.loader_combo.currentIndexChanged.connect(self._update_loader_info)
        self.api_combo.currentIndexChanged.connect(self._update_api_info)
        self._update_loader_info()
        self._update_api_info()

    def _update_loader_info(self):
        item = self.loader_combo.currentData()
        if item:
            self.loader_info.setText(
                f"  版本: {item['version']}\n"
                f"  稳定版: {'是' if item.get('stable') else '否'}"
            )

    def _update_api_info(self):
        item = self.api_combo.currentData()
        if item:
            size_mb = item.get("size", 0) / 1024 / 1024
            self.api_info.setText(
                f"  文件名: {item['filename']}\n"
                f"  大小: {size_mb:.1f} MB\n"
                f"  发布时间: {item.get('date_published', '')[:10]}"
            )
        else:
            self.api_info.setText("  （不安装 Fabric API）")

    def _on_ok(self):
        loader = self.loader_combo.currentData()
        api = self.api_combo.currentData()
        isolated = self.isolated_check.isChecked()
        if not loader:
            QMessageBox.warning(self, "提示", "请选择 Fabric Loader 版本")
            return
        self.selected = {
            "loader": loader,
            "api": api,
            "isolated": isolated,
        }
        self.accept()


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DHML 启动测试 v2.9")
        self.resize(1080, 760)

        self.manifest_versions = []
        self._remembered_version_id = None
        self.worker = None
        self.download_worker = None
        self.forge_worker = None
        self.fabric_worker = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # ========== 配置区 ==========
        config_box = QGroupBox("配置")
        form = QFormLayout(config_box)

        self.mc_dir_input = QLineEdit(DEFAULT_MC_DIR)
        form.addRow("MC 目录:", self.mc_dir_input)

        self.java_input = QLineEdit(DEFAULT_JAVA)
        form.addRow("Java 路径:", self.java_input)

        row = QHBoxLayout()
        row.addWidget(QLabel("版本:"))
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(380)
        self.version_combo.setEditable(False)
        row.addWidget(self.version_combo)

        self.reload_btn = QPushButton("🔄")
        self.reload_btn.setFixedSize(32, 32)
        self.reload_btn.setToolTip("重新拉取版本清单")
        self.reload_btn.clicked.connect(self.load_manifest)
        row.addWidget(self.reload_btn)

        row.addSpacing(15)
        row.addWidget(QLabel("玩家名:"))
        self.username_input = QLineEdit(DEFAULT_USERNAME)
        self.username_input.setFixedWidth(110)
        row.addWidget(self.username_input)

        row.addSpacing(15)
        row.addWidget(QLabel("内存(MB):"))
        self.memory_input = QLineEdit(str(DEFAULT_MEMORY))
        self.memory_input.setFixedWidth(80)
        row.addWidget(self.memory_input)
        row.addStretch()

        row_widget = QWidget()
        row_widget.setLayout(row)
        form.addRow("", row_widget)

        layout.addWidget(config_box)

        # ========== 按钮区 ==========
        btn_row = QHBoxLayout()

        self.launch_btn = QPushButton("▶  启动游戏")
        self.launch_btn.setFixedHeight(52)
        self.launch_btn.setStyleSheet("""
            QPushButton {
                background-color: #3fa34d; color: white;
                font-size: 16px; font-weight: bold;
                border: none; border-radius: 8px;
            }
            QPushButton:hover { background-color: #4bb85a; }
            QPushButton:pressed { background-color: #348a40; }
            QPushButton:disabled { background-color: #555; color: #999; }
        """)
        self.launch_btn.clicked.connect(self.on_launch)

        self.download_btn = QPushButton("⬇  下载原版")
        self.download_btn.setFixedHeight(52)
        self.download_btn.setFixedWidth(130)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a9eff; color: white;
                font-size: 14px; font-weight: bold;
                border: none; border-radius: 8px;
            }
            QPushButton:hover { background-color: #5aaeff; }
            QPushButton:disabled { background-color: #555; color: #999; }
        """)
        self.download_btn.clicked.connect(self.on_download)

        self.forge_btn = QPushButton("🔧  Forge（暂不可用）")
        self.forge_btn.setFixedHeight(52)
        self.forge_btn.setFixedWidth(180)
        self.forge_btn.setStyleSheet("""
            QPushButton {
                background-color: #555; color: #999;
                font-size: 13px; font-weight: bold;
                border: none; border-radius: 8px;
            }
        """)
        self.forge_btn.setEnabled(False)
        self.forge_btn.setToolTip("Forge 安装暂时禁用，避免和 Fabric 冲突")

        self.fabric_btn = QPushButton("🧵  安装 Fabric")
        self.fabric_btn.setFixedHeight(52)
        self.fabric_btn.setFixedWidth(150)
        self.fabric_btn.setStyleSheet("""
            QPushButton {
                background-color: #7c3aed; color: white;
                font-size: 14px; font-weight: bold;
                border: none; border-radius: 8px;
            }
            QPushButton:hover { background-color: #8b4bf7; }
            QPushButton:pressed { background-color: #6b2ad8; }
            QPushButton:disabled { background-color: #555; color: #999; }
        """)
        self.fabric_btn.clicked.connect(self.on_install_fabric)

        self.stop_btn = QPushButton("■  停止")
        self.stop_btn.setFixedHeight(52)
        self.stop_btn.setFixedWidth(100)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_stop)

        self.clear_btn = QPushButton("清空")
        self.clear_btn.setFixedHeight(52)
        self.clear_btn.setFixedWidth(80)
        self.clear_btn.clicked.connect(lambda: self.log_text.clear())

        btn_row.addWidget(self.launch_btn, 1)
        btn_row.addWidget(self.download_btn)
        btn_row.addWidget(self.forge_btn)
        btn_row.addWidget(self.fabric_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.clear_btn)
        layout.addLayout(btn_row)

        # ========== 日志区 ==========
        layout.addWidget(QLabel("日志:"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #333;
                border-radius: 6px;
            }
        """)
        layout.addWidget(self.log_text, 1)

        self.log("就绪。")
        self.log(f"MC 目录: {DEFAULT_MC_DIR}")
        self.log(f"Java: {DEFAULT_JAVA}")
        self.log("Forge 按钮暂时禁用（避免与 Fabric 冲突）")

        self.load_manifest()

    def log(self, msg):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---------- 版本清单 ----------
    def load_manifest(self):
        current = self._get_selected_version()
        self._remembered_version_id = current["id"] if current else None

        self.version_combo.clear()
        self.version_combo.addItem("（加载中...）")
        self.version_combo.setEnabled(False)

        self.manifest_fetcher = ManifestFetcher()
        self.manifest_fetcher.log.connect(self.log)
        self.manifest_fetcher.finished.connect(self._on_manifest_loaded)
        threading.Thread(target=self.manifest_fetcher.run, daemon=True).start()

    def _on_manifest_loaded(self, versions):
        self.manifest_versions = versions
        self.version_combo.clear()

        if not versions:
            self.version_combo.addItem("（获取失败）")
            return

        local_installed = self._get_installed_versions()
        official_ids = {v["id"] for v in versions}

        local_only = sorted(local_installed - official_ids)

        release_versions = [v for v in versions if v.get("type") == "release"]
        snapshot_versions = [v for v in versions if v.get("type") == "snapshot"]
        old_versions = [v for v in versions if v.get("type") in ("old_beta", "old_alpha")]
        release_versions.sort(key=lambda v: v.get("releaseTime", ""), reverse=True)
        snapshot_versions.sort(key=lambda v: v.get("releaseTime", ""), reverse=True)

        def add_group_title(label):
            self.version_combo.insertSeparator(self.version_combo.count())
            self.version_combo.addItem(f"── {label} ──")
            idx = self.version_combo.count() - 1
            self.version_combo.model().item(idx).setEnabled(False)

        if local_only:
            add_group_title("本地版本")
            for vid in local_only:
                self.version_combo.addItem(
                    vid,
                    userData={"id": vid, "_local": True},
                )
            self.version_combo.insertSeparator(self.version_combo.count())

        if release_versions:
            first = release_versions[0]
            mark = "  [已安装]" if first["id"] in local_installed else ""
            self.version_combo.addItem(f"{first['id']}{mark}", userData=first)

            if len(release_versions) > 1:
                add_group_title("正式版")
                for v in release_versions[1:]:
                    vid = v["id"]
                    mark = "  [已安装]" if vid in local_installed else ""
                    self.version_combo.addItem(f"{vid}{mark}", userData=v)
                self.version_combo.insertSeparator(self.version_combo.count())

        if snapshot_versions:
            add_group_title("快照")
            for v in snapshot_versions:
                vid = v["id"]
                mark = "  [已安装]" if vid in local_installed else ""
                self.version_combo.addItem(f"{vid}{mark}", userData=v)
            self.version_combo.insertSeparator(self.version_combo.count())

        if old_versions:
            add_group_title("远古版")
            for v in old_versions:
                vid = v["id"]
                mark = "  [已安装]" if vid in local_installed else ""
                self.version_combo.addItem(f"{vid}{mark}", userData=v)

        self.version_combo.setEnabled(True)

        restored = False
        remembered = self._remembered_version_id
        if remembered:
            for i in range(self.version_combo.count()):
                data = self.version_combo.itemData(i)
                if isinstance(data, dict) and data.get("id") == remembered:
                    self.version_combo.setCurrentIndex(i)
                    restored = True
                    self.log(f"✓ 已恢复选中版本: {remembered}")
                    break
        if not restored:
            self.version_combo.setCurrentIndex(0)

        self.log(f"✓ 版本下拉框已填充（{len(versions)} 个官方 + {len(local_only)} 个本地）")

    def _get_installed_versions(self):
        mc_dir = Path(self.mc_dir_input.text().strip())
        versions_dir = mc_dir / "versions"
        if not versions_dir.exists():
            return set()
        try:
            result = set()
            for p in versions_dir.iterdir():
                if p.is_dir() and list(p.glob("*.json")):
                    result.add(p.name)
            return result
        except:
            return set()

    def _get_selected_version(self):
        data = self.version_combo.currentData()
        if not data or not isinstance(data, dict):
            return None
        return data

    def _refresh_installed_marks(self):
        installed = self._get_installed_versions()
        for i in range(self.version_combo.count()):
            data = self.version_combo.itemData(i)
            if not isinstance(data, dict):
                continue
            if data.get("_local"):
                continue
            vid = data["id"]
            mark = "  [已安装]" if vid in installed else ""
            self.version_combo.setItemText(i, f"{vid}{mark}")

    # ---------- 启动 ----------
    def on_launch(self):
        vinfo = self._get_selected_version()
        if not vinfo:
            self.log("❌ 请先选择一个版本")
            return

        version = vinfo["id"]
        config = {
            "mc_dir": self.mc_dir_input.text().strip(),
            "java": self.java_input.text().strip(),
            "version": version,
            "username": self.username_input.text().strip(),
            "memory": int(self.memory_input.text().strip() or "2048"),
        }
        config["version_dir"] = Path(config["mc_dir"]) / "versions" / version

        if not Path(config["mc_dir"]).exists():
            self.log(f"❌ MC 目录不存在: {config['mc_dir']}")
            return
        json_path = config["version_dir"] / f"{version}.json"
        if not json_path.exists():
            self.log(f"❌ 版本 JSON 不存在: {json_path}")
            return
        if not Path(config["java"]).exists():
            self.log(f"❌ Java 不存在: {config['java']}")
            return

        self.launch_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.fabric_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self.worker = Worker(config)
        self.worker.log.connect(self.log)
        self.worker.finished.connect(self._on_launch_finished)
        threading.Thread(target=self.worker.launch, daemon=True).start()

    def _on_launch_finished(self):
        self.launch_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.fabric_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def on_stop(self):
        if self.worker:
            self.worker.stop()

    # ---------- 下载原版 ----------
    def on_download(self):
        vinfo = self._get_selected_version()
        if not vinfo:
            self.log("❌ 请先选择一个版本")
            return
        if vinfo.get("_local"):
            self.log("❌ 本地版本（Forge/Fabric）不需要下载原版")
            return
        if "url" not in vinfo:
            self.log("❌ 这个版本没有下载信息")
            return

        version = vinfo["id"]
        mc_dir = self.mc_dir_input.text().strip()

        if not Path(mc_dir).exists():
            self.log(f"❌ MC 目录不存在: {mc_dir}")
            return

        version_dir = Path(mc_dir) / "versions" / version
        if (version_dir / f"{version}.json").exists():
            reply = QMessageBox.question(
                self, "重新下载？",
                f"版本 {version} 已存在。\n要重新下载吗？（会覆盖）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        else:
            reply = QMessageBox.question(
                self, "确认下载",
                f"下载版本：{version}\n类型：{vinfo.get('type', 'unknown')}\n"
                f"发布时间：{vinfo.get('releaseTime', '')[:10]}\n\n"
                f"保存到：{mc_dir}\n"
                f"可能几百 MB ~ 几 GB，确定吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.launch_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.fabric_btn.setEnabled(False)
        self.log("=" * 60)
        self.log(f"开始下载版本: {version}")

        self.download_worker = DownloadWorker(
            version, mc_dir, vinfo["url"], vinfo.get("sha1")
        )
        self.download_worker.log.connect(self.log)
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.finished.connect(self._on_download_finished)
        threading.Thread(target=self.download_worker.run, daemon=True).start()

    def _on_download_progress(self, done, total, failed):
        self.log(f"    进度: {done}/{total} (失败 {failed})")

    def _on_download_finished(self, success):
        self.launch_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.fabric_btn.setEnabled(True)
        if success:
            self.log("🎉 下载完成！")
            self._refresh_installed_marks()

    # ---------- 安装 Forge（按钮禁用） ----------
    def on_install_forge(self):
        self.log("Forge 安装暂时禁用，后续版本恢复")

    # ---------- 安装 Fabric ----------
    def on_install_fabric(self):
        vinfo = self._get_selected_version()
        if not vinfo:
            self.log("❌ 请先选择一个版本")
            return
        if vinfo.get("_local"):
            self.log("❌ 不能给本地版本（Forge/Fabric）装 Fabric")
            self.log("   请先选一个官方原版版本")
            return

        mc_version = vinfo["id"]
        mc_dir = self.mc_dir_input.text().strip()

        if not Path(mc_dir).exists():
            self.log(f"❌ MC 目录不存在: {mc_dir}")
            return

        version_dir = Path(mc_dir) / "versions" / mc_version
        if not (version_dir / f"{mc_version}.json").exists():
            self.log(f"❌ 原版 {mc_version} 还没下载，请先下载原版")
            return

        self.log(f"正在获取 MC {mc_version} 的 Fabric 版本...")
        try:
            installer = FabricInstaller(log_callback=self.log)
            loader_list = installer.get_loader_versions(mc_version)
            api_list = installer.get_api_versions(mc_version)
        except Exception as e:
            import traceback
            self.log(f"❌ 获取 Fabric 列表失败: {e}")
            self.log(traceback.format_exc())
            return

        if not loader_list:
            self.log(f"❌ MC {mc_version} 没有可用的 Fabric Loader")
            return

        self.log(f"✓ 找到 {len(loader_list)} 个 loader, {len(api_list)} 个 API")

        dlg = FabricSelectDialog(mc_version, loader_list, api_list, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not dlg.selected:
            return

        loader_version = dlg.selected["loader"]["version"]
        api_info = dlg.selected["api"]
        isolated = dlg.selected["isolated"]

        reply = QMessageBox.question(
            self, "确认安装",
            f"即将安装：\n"
            f"MC 版本: {mc_version}\n"
            f"Fabric Loader: {loader_version}\n"
            f"Fabric API: {api_info['version_number'] if api_info else '不安装'}\n"
            f"版本隔离: {'是' if isolated else '否'}\n\n"
            f"确定吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.launch_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.fabric_btn.setEnabled(False)
        self.log("=" * 60)
        self.log(f"开始安装 Fabric {loader_version} for MC {mc_version}")

        self.fabric_worker = FabricInstallWorker(
            mc_version, loader_version, mc_dir, api_info, isolated
        )
        self.fabric_worker.log.connect(self.log)
        self.fabric_worker.progress.connect(self._on_fabric_progress)
        self.fabric_worker.finished.connect(self._on_fabric_finished)
        threading.Thread(target=self.fabric_worker.run, daemon=True).start()

    def _on_fabric_progress(self, done, total, failed):
        self.log(f"    进度: {done}/{total} (失败 {failed})")

    def _on_fabric_finished(self, success):
        self.launch_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.fabric_btn.setEnabled(True)
        if success:
            self.log("🎉 Fabric 安装完成！刷新列表查看新版本")
            if self.fabric_worker:
                fabric_version = f"{self.fabric_worker.mc_version}-Fabric {self.fabric_worker.loader_version}"
                self._remembered_version_id = fabric_version
                self.log(f"    → 刷新后自动选中: {fabric_version}")
            self.load_manifest()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
