"""
MC 启动器测试窗口 v2.3
- 版本下拉框（拉取 BMCLAPI 版本清单）
- 支持原版 + Forge（动态解析 arguments）
- 支持下载新版本
- 实时显示日志
"""

import sys
import json
import platform
import subprocess
import threading
import hashlib
import zipfile
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel, QLineEdit, QFormLayout, QGroupBox,
    QMessageBox, QComboBox
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
# 工具
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


# ============================================================
# 版本清单获取
# ============================================================
class ManifestFetcher(QObject):
    """后台拉取 BMCLAPI 版本清单"""
    log = pyqtSignal(str)
    finished = pyqtSignal(list)   # list[dict]: [{id, type, releaseTime}, ...]

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
            self.log.emit(f"❌ 获取版本清单失败: {e}")
            self.finished.emit([])


# ============================================================
# 下载器
# ============================================================
class Downloader:
    def __init__(self, log_callback=None):
        self.log = log_callback or print
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "DHML/1.0"})

    @staticmethod
    def replace_mirror(url: str) -> str:
        if not url:
            return url
        return (url
            .replace("piston-data.mojang.com", "bmclapi2.bangbang93.com")
            .replace("piston-meta.mojang.com", "bmclapi2.bangbang93.com")
            .replace("libraries.minecraft.net", "bmclapi2.bangbang93.com/maven")
            .replace("resources.download.minecraft.net", "bmclapi2.bangbang93.com/assets")
            .replace("launcher.mojang.com", "bmclapi2.bangbang93.com")
            .replace("launchermeta.mojang.com", "bmclapi2.bangbang93.com")
        )

    @staticmethod
    def sha1_file(path) -> str:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def download_file(self, url, save_path, sha1=None, retries=2):
        """下载单个文件：SHA1 校验 + 重试 + 镜像失败回退官方"""
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

        # 1. 版本 JSON
        self.log(f"[1/5] 下载版本 JSON...")
        version_dir = mc_dir / "versions" / version_id
        version_dir.mkdir(parents=True, exist_ok=True)
        json_path = version_dir / f"{version_id}.json"

        if not self.download_file(version_json_url, json_path, version_json_sha1):
            raise RuntimeError("版本 JSON 下载失败")

        with open(json_path, "r", encoding="utf-8") as f:
            vj = json.load(f)

        # 2. 收集任务
        self.log(f"[2/5] 收集下载任务...")
        tasks = []

        # 2a. 客户端 jar
        client = vj["downloads"]["client"]
        tasks.append({"url": client["url"], "path": version_dir / f"{version_id}.jar", "sha1": client["sha1"]})

        # 2b. libraries
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

        # 2c. 资源索引
        ai = vj["assetIndex"]
        asset_index_path = mc_dir / "assets" / "indexes" / f"{ai['id']}.json"
        tasks.append({"url": ai["url"], "path": asset_index_path, "sha1": ai["sha1"]})

        # 2d. 日志配置
        if "logging" in vj:
            lf = vj["logging"]["client"]["file"]
            tasks.append({
                "url": lf["url"],
                "path": mc_dir / "assets" / "log_configs" / lf["id"],
                "sha1": lf["sha1"],
            })

        self.log(f"    共 {len(tasks)} 个文件")

        # 3. 批量下载
        self.log(f"[3/5] 下载 libraries + client...")
        done, failed = self.download_batch(tasks, max_workers=16, progress_cb=progress_cb)
        self.log(f"    完成 {done}/{len(tasks)}, 失败 {failed}")
        if failed > len(tasks) * 0.1:
            raise RuntimeError(f"{failed}/{len(tasks)} 个文件失败，超过 10% 阈值")
        elif failed > 0:
            self.log(f"⚠ 有 {failed} 个文件失败，继续安装")

        # 4. 资源文件
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

        # 5. natives
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
# 启动线程
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

    def _build_command(self):
        mc_dir = Path(self.config["mc_dir"])
        version = self.config["version"]
        username = self.config["username"]
        memory = self.config["memory"]
        java = self.config["java"]
        version_dir = self.config["version_dir"]

        json_path = version_dir / f"{version}.json"
        with open(json_path, "r", encoding="utf-8") as f:
            vj = json.load(f)

        classpath = []
        for lib in vj["libraries"]:
            if not rules_allow(lib.get("rules", [])):
                continue
            artifact = lib.get("downloads", {}).get("artifact")
            if artifact:
                classpath.append(str(mc_dir / "libraries" / artifact["path"]))
        classpath.append(str(version_dir / f"{version}.jar"))

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
            "${assets_index_name}": vj["assetIndex"]["id"],
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
        for arg in vj["arguments"]["jvm"]:
            jvm_args.extend(self._resolve_arg(arg, replacements))

        game_args = [vj["mainClass"]]
        for arg in vj["arguments"]["game"]:
            game_args.extend(self._resolve_arg(arg, replacements))

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
# 下载线程
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
            dl = Downloader(log_callback=self.log.emit)
            dl.install_version(
                self.version_id,
                self.mc_dir,
                self.version_json_url,
                self.version_json_sha1,
                progress_cb=lambda d, t, f: self.progress.emit(d, t, f),
            )
            self.finished.emit(True)
        except Exception as e:
            self.log.emit(f"❌ 下载失败: {type(e).__name__}: {e}")
            import traceback
            self.log.emit(traceback.format_exc())
            self.finished.emit(False)


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DHML 启动测试 v2.3")
        self.resize(1000, 750)

        self.manifest_versions = []   # 完整版本清单

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
        self.version_combo.setMinimumWidth(280)
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
                background-color: #3fa34d;
                color: white;
                font-size: 16px;
                font-weight: bold;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #4bb85a; }
            QPushButton:pressed { background-color: #348a40; }
            QPushButton:disabled { background-color: #555; color: #999; }
        """)
        self.launch_btn.clicked.connect(self.on_launch)

        self.download_btn = QPushButton("⬇  下载版本")
        self.download_btn.setFixedHeight(52)
        self.download_btn.setFixedWidth(140)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a9eff;
                color: white;
                font-size: 15px;
                font-weight: bold;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #5aaeff; }
            QPushButton:pressed { background-color: #3a8eef; }
            QPushButton:disabled { background-color: #555; color: #999; }
        """)
        self.download_btn.clicked.connect(self.on_download)

        self.stop_btn = QPushButton("■  停止")
        self.stop_btn.setFixedHeight(52)
        self.stop_btn.setFixedWidth(110)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_stop)

        self.clear_btn = QPushButton("清空日志")
        self.clear_btn.setFixedHeight(52)
        self.clear_btn.setFixedWidth(110)
        self.clear_btn.clicked.connect(lambda: self.log_text.clear())

        btn_row.addWidget(self.launch_btn, 1)
        btn_row.addWidget(self.download_btn)
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

        self.worker = None
        self.download_worker = None

        self.log("就绪。")
        self.log(f"MC 目录: {DEFAULT_MC_DIR}")
        self.log(f"Java: {DEFAULT_JAVA}")

        # 启动时自动拉取版本清单
        self.load_manifest()

    def log(self, msg):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---------- 拉取版本清单 ----------
    def load_manifest(self):
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

        # 本地已安装的版本
        local_installed = self._get_installed_versions()

        # 按类型分组，正式版优先
        release_versions = [v for v in versions if v.get("type") == "release"]
        snapshot_versions = [v for v in versions if v.get("type") == "snapshot"]
        old_versions = [v for v in versions if v.get("type") in ("old_beta", "old_alpha")]

        # 按发布时间倒序
        release_versions.sort(key=lambda v: v.get("releaseTime", ""), reverse=True)
        snapshot_versions.sort(key=lambda v: v.get("releaseTime", ""), reverse=True)

        def add_section(label, ver_list):
            if not ver_list:
                return
            # 分隔线
            idx = self.version_combo.count()
            self.version_combo.insertSeparator(idx)
            self.version_combo.addItem(f"── {label} ──")
            self.version_combo.model().item(self.version_combo.count() - 1).setEnabled(False)
            for v in ver_list:
                vid = v["id"]
                installed_mark = "  [已安装]" if vid in local_installed else ""
                label_text = f"{vid}{installed_mark}"
                self.version_combo.addItem(label_text, userData=v)
            # 加回分隔线
            self.version_combo.insertSeparator(self.version_combo.count())

        # 默认选中第一个正式版
        if release_versions:
            first = release_versions[0]
            installed_mark = "  [已安装]" if first["id"] in local_installed else ""
            self.version_combo.addItem(f"{first['id']}{installed_mark}", userData=first)

        add_section("正式版", release_versions[1:] if len(release_versions) > 1 else [])
        add_section("快照", snapshot_versions)
        add_section("远古版", old_versions)

        self.version_combo.setEnabled(True)
        self.version_combo.setCurrentIndex(0)
        self.log(f"✓ 版本下拉框已填充（{len(versions)} 个可选）")

    def _get_installed_versions(self):
        """扫描本地已安装版本"""
        mc_dir = Path(self.mc_dir_input.text().strip())
        versions_dir = mc_dir / "versions"
        if not versions_dir.exists():
            return set()
        try:
            result = set()
            for p in versions_dir.iterdir():
                if p.is_dir():
                    # 有 json 才算
                    if list(p.glob("*.json")):
                        result.add(p.name)
            return result
        except:
            return set()

    def _get_selected_version(self):
        """获取当前选中的版本信息"""
        data = self.version_combo.currentData()
        if data and isinstance(data, dict):
            return data
        return None

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
            self.log(f"   提示：这个版本还没下载，请先点「下载版本」")
            return
        if not Path(config["java"]).exists():
            self.log(f"❌ Java 不存在: {config['java']}")
            return

        self.launch_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self.worker = Worker(config)
        self.worker.log.connect(self.log)
        self.worker.finished.connect(self._on_launch_finished)
        threading.Thread(target=self.worker.launch, daemon=True).start()

    def _on_launch_finished(self):
        self.launch_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def on_stop(self):
        if self.worker:
            self.worker.stop()

    # ---------- 下载 ----------
    def on_download(self):
        vinfo = self._get_selected_version()
        if not vinfo:
            self.log("❌ 请先选择一个版本")
            return

        version = vinfo["id"]
        mc_dir = self.mc_dir_input.text().strip()

        if not Path(mc_dir).exists():
            self.log(f"❌ MC 目录不存在: {mc_dir}")
            return

        # 检查是否已安装
        version_dir = Path(mc_dir) / "versions" / version
        if (version_dir / f"{version}.json").exists():
            reply = QMessageBox.question(
                self, "重新下载？",
                f"版本 {version} 已经存在。\n要重新下载吗？（会覆盖已有文件）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        else:
            reply = QMessageBox.question(
                self, "确认下载",
                f"即将下载版本：{version}\n"
                f"类型：{vinfo.get('type', 'unknown')}\n"
                f"发布时间：{vinfo.get('releaseTime', '')[:10]}\n\n"
                f"保存到：{mc_dir}\n"
                f"可能需要下载几百 MB ~ 几 GB，确定吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.launch_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.log("=" * 60)
        self.log(f"开始下载版本: {version}")

        self.download_worker = DownloadWorker(
            version, mc_dir,
            vinfo["url"], vinfo.get("sha1")
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
        if success:
            self.log("🎉 下载完成！现在可以点击「启动游戏」")
            # 刷新版本列表（更新"已安装"标记）
            self.load_manifest()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
