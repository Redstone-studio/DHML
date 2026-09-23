"""
MC 启动器测试窗口 v2.2
- 支持原版 + Forge（动态解析 arguments）
- 支持下载新版本（BMCLAPI）
- 读版本 JSON
- 拼 Java 命令
- 启动游戏
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
    QMessageBox
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QFont


# ============================================================
# 默认配置
# ============================================================
DEFAULT_MC_DIR = r"D:\DHML\.minecraft"
DEFAULT_VERSION = "1.20-Forge"
DEFAULT_JAVA = r"C:\Users\yexia\AppData\Roaming\.minecraft\runtime\java-runtime-delta\bin\java.exe"
DEFAULT_USERNAME = "BaBaLe"
DEFAULT_MEMORY = 8192
# ============================================================


# ============================================================
# 通用工具
# ============================================================
def rules_allow(rules):
    """判断 rules 是否允许当前系统"""
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
    """根据当前系统找 natives classifier key"""
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
# 下载器
# ============================================================
class Downloader:
    """从 BMCLAPI 下载 MC 版本"""

    def __init__(self, log_callback=None):
        self.log = log_callback or print
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "DHML/1.0"})

    @staticmethod
    def replace_mirror(url: str) -> str:
        """官方 URL → BMCLAPI 镜像"""
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
        """下载单个文件，带 SHA1 校验和重试"""
        save_path = Path(save_path)

        # 已存在且 SHA1 匹配 → 跳过
        if save_path.exists() and sha1:
            try:
                if self.sha1_file(save_path) == sha1:
                    return True
            except:
                pass

        save_path.parent.mkdir(parents=True, exist_ok=True)
        mirror_url = self.replace_mirror(url)

        for attempt in range(retries):
            try:
                r = self.session.get(mirror_url, stream=True, timeout=30)
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

            except Exception as e:
                if attempt == retries - 1:
                    self.log(f"❌ 下载失败 {mirror_url}: {e}")
                    return False
                # 重试
        return False

    def download_batch(self, tasks, max_workers=16, progress_cb=None):
        """批量下载"""
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

    def fetch_manifest(self):
        url = "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json"
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.json()

    def install_version(self, version_id, mc_dir, progress_cb=None):
        mc_dir = Path(mc_dir)

        # 1. 版本清单
        self.log(f"[1/6] 获取版本清单...")
        manifest = self.fetch_manifest()
        version_info = next((v for v in manifest["versions"] if v["id"] == version_id), None)
        if not version_info:
            raise ValueError(f"版本 {version_id} 不存在")

        # 2. 版本 JSON
        self.log(f"[2/6] 下载版本 JSON...")
        version_dir = mc_dir / "versions" / version_id
        version_dir.mkdir(parents=True, exist_ok=True)
        json_path = version_dir / f"{version_id}.json"

        if not self.download_file(version_info["url"], json_path, version_info["sha1"]):
            raise RuntimeError("版本 JSON 下载失败")

        with open(json_path, "r", encoding="utf-8") as f:
            vj = json.load(f)

        # 3. 收集任务
        self.log(f"[3/6] 收集下载任务...")
        tasks = []

        # 3a. 客户端 jar
        client = vj["downloads"]["client"]
        tasks.append({"url": client["url"], "path": version_dir / f"{version_id}.jar", "sha1": client["sha1"]})

        # 3b. libraries
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

        # 3c. 资源索引
        ai = vj["assetIndex"]
        asset_index_path = mc_dir / "assets" / "indexes" / f"{ai['id']}.json"
        tasks.append({"url": ai["url"], "path": asset_index_path, "sha1": ai["sha1"]})

        # 3d. 日志配置
        if "logging" in vj:
            lf = vj["logging"]["client"]["file"]
            tasks.append({
                "url": lf["url"],
                "path": mc_dir / "assets" / "log_configs" / lf["id"],
                "sha1": lf["sha1"],
            })

        self.log(f"    共 {len(tasks)} 个文件")

        # 4. 下载
        self.log(f"[4/6] 下载 libraries + client...")
        done, failed = self.download_batch(tasks, max_workers=16, progress_cb=progress_cb)
        self.log(f"    完成 {done}/{len(tasks)}, 失败 {failed}")
        if failed > 0:
            raise RuntimeError(f"{failed} 个文件下载失败")

        # 5. 资源文件
        self.log(f"[5/6] 下载资源文件（最慢）...")
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

        # 6. natives
        self.log(f"[6/6] 解压 natives...")
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

        # classpath
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

    def __init__(self, version_id, mc_dir):
        super().__init__()
        self.version_id = version_id
        self.mc_dir = mc_dir

    def run(self):
        try:
            dl = Downloader(log_callback=self.log.emit)
            dl.install_version(
                self.version_id,
                self.mc_dir,
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
        self.setWindowTitle("DHML 启动测试 v2.2")
        self.resize(950, 700)

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
        self.version_input = QLineEdit(DEFAULT_VERSION)
        self.version_input.setFixedWidth(150)
        self.username_input = QLineEdit(DEFAULT_USERNAME)
        self.username_input.setFixedWidth(120)
        self.memory_input = QLineEdit(str(DEFAULT_MEMORY))
        self.memory_input.setFixedWidth(80)

        row.addWidget(QLabel("版本:"))
        row.addWidget(self.version_input)
        row.addSpacing(15)
        row.addWidget(QLabel("玩家名:"))
        row.addWidget(self.username_input)
        row.addSpacing(15)
        row.addWidget(QLabel("内存(MB):"))
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
        self.log(f"默认版本: {DEFAULT_VERSION}")
        self.log("点击「启动游戏」启动，或「下载版本」下载新版本")

    def log(self, msg):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---------- 启动 ----------
    def on_launch(self):
        config = {
            "mc_dir": self.mc_dir_input.text().strip(),
            "java": self.java_input.text().strip(),
            "version": self.version_input.text().strip(),
            "username": self.username_input.text().strip(),
            "memory": int(self.memory_input.text().strip() or "2048"),
        }
        config["version_dir"] = Path(config["mc_dir"]) / "versions" / config["version"]

        if not Path(config["mc_dir"]).exists():
            self.log(f"❌ MC 目录不存在: {config['mc_dir']}")
            return
        json_path = config["version_dir"] / f"{config['version']}.json"
        if not json_path.exists():
            self.log(f"❌ 版本 JSON 不存在: {json_path}")
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
        version = self.version_input.text().strip()
        mc_dir = self.mc_dir_input.text().strip()

        if not version:
            self.log("❌ 请填写要下载的版本号")
            return
        if not Path(mc_dir).exists():
            self.log(f"❌ MC 目录不存在: {mc_dir}")
            return

        reply = QMessageBox.question(
            self, "确认下载",
            f"即将下载版本：{version}\n"
            f"保存到：{mc_dir}\n\n"
            f"可能需要下载几百 MB ~ 几 GB，确定吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.launch_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.log("=" * 60)
        self.log(f"开始下载版本: {version}")

        self.download_worker = DownloadWorker(version, mc_dir)
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
        else:
            self.log("❌ 下载失败，请看上方日志")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
