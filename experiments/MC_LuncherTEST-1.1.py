import json
import subprocess
from pathlib import Path

# ============ 配置 ============
MC_DIR = Path(r"D:\DHML\.minecraft")
VERSION_ID = "1.20.3"
JAVA = r"C:\Users\yexia\AppData\Roaming\.minecraft\runtime\java-runtime-delta\bin\java.exe"
USERNAME = "BaBaLe"
MEMORY = 8192   # MB
# ==============================

def main():
    version_dir = MC_DIR / "versions" / VERSION_ID
    json_path = version_dir / f"{VERSION_ID}.json"
    
    # 1. 读版本 JSON
    with open(json_path, "r", encoding="utf-8") as f:
        vj = json.load(f)
    
    print(f"版本: {vj['id']}")
    print(f"主类: {vj['mainClass']}")
    print(f"Java 需求: {vj.get('javaVersion', {})}")
    
    # 2. 拼 classpath
    classpath = []
    for lib in vj["libraries"]:
        # 简化：不过滤 rules，全部加进去
        artifact = lib.get("downloads", {}).get("artifact")
        if artifact:
            jar = MC_DIR / "libraries" / artifact["path"]
            classpath.append(str(jar))
    
    client_jar = version_dir / f"{VERSION_ID}.jar"
    classpath.append(str(client_jar))
    
    classpath_str = ";".join(classpath)
    print(f"classpath: {len(classpath)} 个 jar")
    
    # 3. natives 目录
    natives_dir = version_dir / f"{VERSION_ID}-natives"
    
    # 4. 拼 JVM 参数
    jvm_args = [
        JAVA,
        f"-Xmx{MEMORY}m",
        f"-Djava.library.path={natives_dir}",
        f"-Djna.tmpdir={natives_dir}",
        f"-Dorg.lwjgl.system.SharedLibraryExtractPath={natives_dir}",
        f"-Dio.netty.native.workdir={natives_dir}",
        "-Dminecraft.launcher.brand=DHML",
        "-Dminecraft.launcher.version=1.0.0",
        "-cp", classpath_str,
    ]
    
    # 5. 拼游戏参数
    game_args = [
        vj["mainClass"],
        "--username", USERNAME,
        "--version", VERSION_ID,
        "--gameDir", str(version_dir),
        "--assetsDir", str(MC_DIR / "assets"),
        "--assetIndex", vj["assetIndex"]["id"],
        "--uuid", "00000000000000000000000000000001",
        "--accessToken", "0",
        "--userType", "legacy",
        "--versionType", "release",
    ]
    
    # 6. 完整命令
    cmd = jvm_args + game_args
    
    # 7. 打印命令（方便对比 .bat）
    print("\n===== 完整命令 =====")
    for a in cmd:
        if len(a) > 100:
            print(f"  {a[:97]}...")
        else:
            print(f"  {a}")
    print("=" * 60)
    
    # 8. 启动
    print("\n启动中...")
    process = subprocess.Popen(
        cmd,
        cwd=str(version_dir),
    )
    process.wait()
    print(f"游戏退出，返回码: {process.returncode}")


if __name__ == "__main__":
    main()