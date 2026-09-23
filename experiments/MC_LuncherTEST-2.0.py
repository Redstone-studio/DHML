"""
极简 MC 启动器测试窗口
- 读版本 JSON
- 拼 Java 命令
- 启动游戏
- 实时显示日志
"""

import sys
import json
import subprocess
import threading
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel, QLineEdit, QFormLayout, QGroupBox
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QFont


# ============================================================
# 默认配置（按你的实际环境填好了）
# ============================================================
DEFAULT_MC_DIR = r"D:\DHML\.minecraft"
DEFAULT_VERSION = "1.20.3"
DEFAULT_JAVA = r"C:\Users\yexia\AppData\Roaming\.minecraft\runtime\java-runtime-delta\bin\java.exe"
DEFAULT_USERNAME = "BaBaLe"
DEFAULT_MEMORY = 8192
# ============================================================


class Worker(QObject):
    """后台线程：启动游戏，捕获输出"""
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

            # 实时读输出
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

        version_dir = mc_dir / "versions" / version
        json_path = version_dir / f"{version}.json"

        with open(json_path, "r", encoding="utf-8") as f:
            vj = json.load(f)

        # ---- classpath ----
        classpath = []
        for lib in vj["libraries"]:
            if not self._rules_allow(lib.get("rules", [])):
                continue
            artifact = lib.get("downloads", {}).get("artifact")
            if artifact:
                classpath.append(str(mc_dir / "libraries" / artifact["path"]))

        client_jar = version_dir / f"{version}.jar"
        classpath.append(str(client_jar))

        sep = ";" if sys.platform == "win32" else ":"
        classpath_str = sep.join(classpath)

        # ---- natives ----
        natives_dir = version_dir / f"{version}-natives"

        # ---- JVM 参数 ----
        cmd = [
            java,
            f"-Xmx{memory}m",
            f"-Djava.library.path={natives_dir}",
            f"-Djna.tmpdir={natives_dir}",
            f"-Dorg.lwjgl.system.SharedLibraryExtractPath={natives_dir}",
            f"-Dio.netty.native.workdir={natives_dir}",
            "-Dminecraft.launcher.brand=DHML",
            "-Dminecraft.launcher.version=1.0.0",
            "-cp", classpath_str,
        ]

        # ---- 主类 + 游戏参数 ----
        cmd.append(vj["mainClass"])
        cmd.extend([
            "--username", username,
            "--version", version,
            "--gameDir", str(version_dir),
            "--assetsDir", str(mc_dir / "assets"),
            "--assetIndex", vj["assetIndex"]["id"],
            "--uuid", "00000000000000000000000000000001",
            "--accessToken", "0",
            "--userType", "legacy",
            "--versionType", "release",
        ])

        return cmd

    def _rules_allow(self, rules):
        if not rules:
            return True
        import platform
        sys_name = platform.system()
        current_os = {"Windows": "windows", "Darwin": "osx", "Linux": "linux"}.get(sys_name, "")

        allowed = False
        for rule in rules:
            action = rule.get("action")
            os_name = rule.get("os", {}).get("name")
            if os_name and os_name != current_os:
                continue
            if action == "allow":
                allowed = True
            elif action == "disallow":
                return False
        return allowed

    @staticmethod
    def _ts():
        return datetime.now().strftime("%H:%M:%S")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DHML 启动测试")
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
        self.version_input.setFixedWidth(120)
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
        self.worker_thread = None

        self.log(f"就绪。")
        self.log(f"MC 目录: {DEFAULT_MC_DIR}")
        self.log(f"Java: {DEFAULT_JAVA}")
        self.log(f"点击「启动游戏」开始测试")

    def log(self, msg):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_launch(self):
        config = {
            "mc_dir": self.mc_dir_input.text().strip(),
            "java": self.java_input.text().strip(),
            "version": self.version_input.text().strip(),
            "username": self.username_input.text().strip(),
            "memory": int(self.memory_input.text().strip() or "2048"),
        }
        config["version_dir"] = Path(config["mc_dir"]) / "versions" / config["version"]

        # 校验
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
        self.stop_btn.setEnabled(True)

        self.worker = Worker(config)
        self.worker.log.connect(self.log)
        self.worker.finished.connect(self._on_finished)

        self.worker_thread = threading.Thread(target=self.worker.launch, daemon=True)
        self.worker_thread.start()

    def _on_finished(self):
        self.launch_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def on_stop(self):
        if self.worker:
            self.worker.stop()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()