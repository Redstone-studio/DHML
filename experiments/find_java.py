"""
Java 查找器
- 扫描系统里的 Java
- 列表显示路径、版本、位数、厂商
- Tkinter 界面（Windows 自带风格）
"""

import os
import re
import sys
import json
import platform
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# Java 查找逻辑
# ============================================================

def get_java_version(java_exe: str) -> dict:
    """运行 java -version，解析版本信息
    
    返回:
        {
            "path": "...",
            "version": "17.0.9",
            "major": 17,
            "vendor": "Oracle Corporation",
            "arch": "x86_64",
            "error": None,   # 或错误信息
        }
    """
    result = {
        "path": java_exe,
        "version": "",
        "major": 0,
        "vendor": "",
        "arch": "",
        "error": None,
    }
    try:
        # java -version 输出到 stderr
        proc = subprocess.run(
            [java_exe, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        output = proc.stderr + proc.stdout
        
        # 解析第一行：java version "17.0.9" 2023-10-17 LTS
        # 或 openjdk version "17.0.9" 2023-10-17
        # 或 java version "1.8.0_391"
        m = re.search(r'version\s+"([^"]+)"', output)
        if m:
            version = m.group(1)
            result["version"] = version
            
            # 解析 major
            # "17.0.9" → 17
            # "1.8.0_391" → 8
            parts = version.split(".")
            if parts[0] == "1" and len(parts) > 1:
                result["major"] = int(parts[1])
            else:
                result["major"] = int(parts[0])
        
        # 解析厂商
        # 常见格式：
        #   Java(TM) SE Runtime Environment ...
        #   OpenJDK Runtime Environment ...
        #   Java HotSpot(TM) 64-Bit Server VM ...
        for line in output.splitlines():
            line = line.strip()
            if "Runtime Environment" in line:
                result["vendor"] = line
                break
            if "OpenJDK" in line:
                result["vendor"] = line
                break
        
        # 解析架构
        if "64-Bit" in output or "x86_64" in output or "amd64" in output.lower():
            result["arch"] = "x86_64"
        elif "32-Bit" in output or "i386" in output or "x86" in output:
            result["arch"] = "x86"
        else:
            result["arch"] = platform.machine()
    
    except subprocess.TimeoutExpired:
        result["error"] = "运行超时"
    except FileNotFoundError:
        result["error"] = "文件不存在"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    
    return result


def scan_registry_java() -> list:
    """从 Windows 注册表扫描 Java 安装路径"""
    if sys.platform != "win32":
        return []
    
    result = []
    try:
        import winreg
    except ImportError:
        return []
    
    # 常见注册表路径
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\Java Development Kit"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\Java Runtime Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\JRE"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\JDK"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\JavaSoft\Java Development Kit"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\JavaSoft\Java Runtime Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Eclipse Adoptium\JDK"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Eclipse Foundation\JDK"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\JDK"),
    ]
    
    for hkey, subkey in reg_paths:
        try:
            with winreg.OpenKey(hkey, subkey) as key:
                # 枚举所有子键（每个版本一个子键）
                i = 0
                while True:
                    try:
                        ver_name = winreg.EnumKey(key, i)
                        i += 1
                        with winreg.OpenKey(key, ver_name) as ver_key:
                            try:
                                java_home, _ = winreg.QueryValueEx(ver_key, "JavaHome")
                                java_exe = Path(java_home) / "bin" / "java.exe"
                                if java_exe.exists():
                                    result.append(str(java_exe))
                            except FileNotFoundError:
                                pass
                    except OSError:
                        break
        except FileNotFoundError:
            continue
        except Exception:
            continue
    
    return result


def scan_common_dirs() -> list:
    """扫描常见安装目录"""
    result = []
    if sys.platform != "win32":
        return result
    
    # 常见位置
    search_dirs = [
        Path(r"C:\Program Files\Java"),
        Path(r"C:\Program Files (x86)\Java"),
        Path(r"C:\Program Files\Eclipse Adoptium"),
        Path(r"C:\Program Files\Eclipse Foundation"),
        Path(r"C:\Program Files\Microsoft"),
        Path(r"C:\Program Files\Zulu"),
        Path(r"C:\Program Files\BellSoft"),
        Path(r"C:\Program Files\Amazon Corretto"),
        Path(r"C:\Program Files\Semeru"),
        Path(r"C:\Program Files\RedHat"),
        Path(r"C:\Program Files\SapMachine"),
    ]
    
    for base in search_dirs:
        if not base.exists():
            continue
        try:
            for sub in base.iterdir():
                if not sub.is_dir():
                    continue
                # 查 bin/java.exe 和 bin/javaw.exe
                for exe_name in ("java.exe", "javaw.exe"):
                    exe = sub / "bin" / exe_name
                    if exe.exists():
                        result.append(str(exe))
                # 再深一层：Program Files\Java\latest\jre-1.8\bin\java.exe
                try:
                    for inner in sub.iterdir():
                        if not inner.is_dir():
                            continue
                        for exe_name in ("java.exe", "javaw.exe"):
                            exe = inner / "bin" / exe_name
                            if exe.exists():
                                result.append(str(exe))
                except (PermissionError, OSError):
                    pass
        except Exception:
            continue
    
    return result


def scan_path_env() -> list:
    """扫描 PATH 环境变量"""
    result = []
    path_env = os.environ.get("PATH", "")
    for p in path_env.split(os.pathsep):
        if not p:
            continue
        exe = Path(p) / ("java.exe" if sys.platform == "win32" else "java")
        if exe.exists():
            result.append(str(exe))
    return result


def scan_minecraft_runtime() -> list:
    """扫描 Minecraft 官方 / PCL 下载的 Java runtime

    ⚠️ 目录结构有两种，之前只处理了嵌套那种，所以一个都搜不到：
      扁平  runtime/<component>/bin/java.exe                         ← 官方和 PCL 都是这个
      嵌套  runtime/<component>/<os-arch>/<component>/bin/java.exe   ← 老版官方启动器
    """
    result = []
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / ".minecraft" / "runtime"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "minecraft" / "runtime"
    else:
        base = Path.home() / ".minecraft" / "runtime"
    
    if not base.exists():
        return result
    
    try:
        for component in base.iterdir():
            if not component.is_dir():
                continue
            # 扁平结构（实测：官方启动器和 PCL 都是这种）
            flat = component / "bin" / "java.exe"
            if flat.is_file():
                result.append(str(flat))
                continue
            # 嵌套结构（老版官方启动器用过，保险起见留着）
            try:
                for os_arch in component.iterdir():
                    if not os_arch.is_dir():
                        continue
                    nested = os_arch / component.name / "bin" / "java.exe"
                    if nested.is_file():
                        result.append(str(nested))
            except Exception:
                continue
    except Exception:
        pass
    
    return result


def scan_java_in_dir(base_dir: Path, max_depth: int = 4) -> list:
    """递归扫描目录下的 java.exe（限制深度）"""
    result = []
    if not base_dir.exists():
        return result
    
    def _scan(d: Path, depth: int):
        if depth > max_depth:
            return
        try:
            for item in d.iterdir():
                if item.is_dir():
                    _scan(item, depth + 1)
                elif item.name.lower() in ("java.exe", "javaw.exe"):
                    result.append(str(item))
        except (PermissionError, OSError):
            pass
    
    _scan(base_dir, 0)
    return result


def find_all_java() -> list:
    """查找所有 Java（去重 + 过滤）"""
    candidates = set()
    
    # 1. PATH
    candidates.update(scan_path_env())
    
    # 2. 注册表
    candidates.update(scan_registry_java())
    
    # 3. 常见目录
    candidates.update(scan_common_dirs())
    
    # 4. MC runtime
    candidates.update(scan_minecraft_runtime())
    
    # 5. 手动扫描
    for d in [r"D:\Java", r"D:\jdk", r"D:\Program Files\Java"]:
        candidates.update(scan_java_in_dir(Path(d), max_depth=3))
    
    # 过滤：只保留 java.exe，不保留 javaw.exe
    # 去重：用绝对路径
    final = set()
    for c in candidates:
        p = Path(c).resolve()
        if p.name.lower() == "javaw.exe":
            # 优先 java.exe（同目录）
            java = p.parent / "java.exe"
            if java.exists():
                final.add(str(java))
            else:
                final.add(str(p))
        else:
            final.add(str(p))
    
    return sorted(final)


# ============================================================
# GUI
# ============================================================

class JavaFinderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Java 查找器")
        self.root.geometry("1100x600")
        
        # 顶部工具栏
        toolbar = tk.Frame(root)
        toolbar.pack(fill=tk.X, padx=5, pady=5)
        
        self.scan_btn = tk.Button(toolbar, text="🔍 扫描 Java", command=self.scan, width=15)
        self.scan_btn.pack(side=tk.LEFT, padx=2)
        
        self.copy_btn = tk.Button(toolbar, text="📋 复制选中", command=self.copy_selected, width=12)
        self.copy_btn.pack(side=tk.LEFT, padx=2)
        
        self.export_btn = tk.Button(toolbar, text="💾 导出 JSON", command=self.export_json, width=12)
        self.export_btn.pack(side=tk.LEFT, padx=2)
        
        self.status_label = tk.Label(toolbar, text="就绪", anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, padx=20, fill=tk.X, expand=True)
        
        # 表格
        frame = tk.Frame(root)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        columns = ("path", "version", "major", "arch", "vendor", "error")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings")
        
        self.tree.heading("path", text="路径")
        self.tree.heading("version", text="版本")
        self.tree.heading("major", text="主版本")
        self.tree.heading("arch", text="架构")
        self.tree.heading("vendor", text="厂商")
        self.tree.heading("error", text="错误")
        
        self.tree.column("path", width=400, anchor=tk.W)
        self.tree.column("version", width=120, anchor=tk.W)
        self.tree.column("major", width=70, anchor=tk.CENTER)
        self.tree.column("arch", width=80, anchor=tk.CENTER)
        self.tree.column("vendor", width=250, anchor=tk.W)
        self.tree.column("error", width=150, anchor=tk.W)
        
        # 滚动条
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        
        # 双击复制路径
        self.tree.bind("<Double-1>", self.on_double_click)
        
        # 数据
        self.java_list = []
        
        # 自动扫描
        self.root.after(100, self.scan)
    
    def scan(self):
        self.scan_btn.config(state=tk.DISABLED)
        self.status_label.config(text="正在查找 Java...")
        self.tree.delete(*self.tree.get_children())
        self.java_list.clear()
        
        # 后台线程扫描
        import threading
        threading.Thread(target=self._scan_worker, daemon=True).start()
    
    def _scan_worker(self):
        try:
            # 1. 找所有候选
            candidates = find_all_java()
            self.root.after(0, lambda: self.status_label.config(
                text=f"找到 {len(candidates)} 个候选，正在检测版本..."))
            
            # 2. 并发检测
            results = []
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {executor.submit(get_java_version, c): c for c in candidates}
                for future in as_completed(futures):
                    results.append(future.result())
            
            # 3. 排序：major 降序
            results.sort(key=lambda x: (-x["major"], x["path"]))
            
            # 4. 更新 UI
            self.root.after(0, lambda: self._update_ui(results))
        except Exception as e:
            self.root.after(0, lambda: self.status_label.config(text=f"错误: {e}"))
            self.root.after(0, lambda: self.scan_btn.config(state=tk.NORMAL))
    
    def _update_ui(self, results):
        self.java_list = results
        self.tree.delete(*self.tree.get_children())
        
        for item in results:
            self.tree.insert("", tk.END, values=(
                item["path"],
                item["version"] or "?",
                item["major"] or "?",
                item["arch"] or "?",
                item["vendor"] or "?",
                item["error"] or "",
            ))
        
        self.status_label.config(text=f"✓ 找到 {len(results)} 个 Java")
        self.scan_btn.config(state=tk.NORMAL)
    
    def copy_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一行")
            return
        values = self.tree.item(sel[0], "values")
        path = values[0]
        self.root.clipboard_clear()
        self.root.clipboard_append(path)
        self.status_label.config(text=f"已复制: {path}")
    
    def export_json(self):
        if not self.java_list:
            messagebox.showinfo("提示", "没有数据")
            return
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="java_list.json",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.java_list, f, ensure_ascii=False, indent=2)
        self.status_label.config(text=f"已导出: {path}")
    
    def on_double_click(self, event):
        self.copy_selected()


def main():
    root = tk.Tk()
    app = JavaFinderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
