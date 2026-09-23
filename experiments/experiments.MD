
# 实验文件存档

这里是 Redstone Launcher 开发过程中的**实验文件**，不是正式代码。

## 目录结构

```
experiments/
├── experiments.md          # 本文件
├── v2.0/                   # 各版本实验代码
├── v2.1/
├── ...
├── v3.0/
├── assets/                 # 截图
└── reference/              # 参考文件（PCL2 生成的 .bat）
```

## 各版本功能

<table>
  <thead>
    <tr>
      <th align="left">功能</th>
      <th align="center">v2.0</th>
      <th align="center">v2.1</th>
      <th align="center">v2.2</th>
      <th align="center">v2.3</th>
      <th align="center">v2.4</th>
      <th align="center">v2.5</th>
      <th align="center">v2.6</th>
      <th align="center">v2.7</th>
      <th align="center">v2.8</th>
      <th align="center">v2.9</th>
      <th align="center">v3.0</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>读版本 JSON</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>动态解析 arguments</td>
      <td align="center">❌</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>拼 classpath</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>启动原版</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td><code>inheritsFrom</code> 合并</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>下载原版</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>版本下拉框</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>保留选中</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Forge 下载安装</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">⚠️</td>
      <td align="center">✅</td>
      <td align="center">⚠️</td>
      <td align="center">⚠️</td>
      <td align="center">⚠️</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Fabric 下载安装</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Fabric API 自动下载</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>本地版本分组</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Maven 格式解析</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">✅</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>日志过滤</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Forge / Fabric 共存</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">❌</td>
      <td align="center">✅</td>
    </tr>
  </tbody>
</table>

## 关键功能对比

<table>
  <thead>
    <tr>
      <th align="left">功能</th>
      <th align="center">最早实现</th>
      <th align="center">最新状态</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>读版本 JSON</td>
      <td align="center">v2.0</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>动态解析 arguments</td>
      <td align="center">v2.1</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>下载原版</td>
      <td align="center">v2.2</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>版本下拉框</td>
      <td align="center">v2.3</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>保留选中</td>
      <td align="center">v2.4</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td><code>inheritsFrom</code> 合并</td>
      <td align="center">v2.6</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Forge 安装</td>
      <td align="center">v2.6</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Fabric 安装</td>
      <td align="center">v2.7</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Fabric API 自动下载</td>
      <td align="center">v2.7</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>本地版本分组</td>
      <td align="center">v2.8</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Maven 格式解析</td>
      <td align="center">v2.9</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>日志过滤</td>
      <td align="center">v3.0</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Forge / Fabric 共存</td>
      <td align="center">v3.0</td>
      <td align="center">✅</td>
    </tr>
  </tbody>
</table>

## 各版本详细说明

### v2.0 - 最初的启动测试
- 读版本 JSON
- 拼 classpath
- 启动原版（**硬编码参数**）

### v2.1 - 动态解析 arguments
- 支持从 JSON 读 `arguments.jvm` 和 `arguments.game`
- 支持带 `rules` 的条件参数

### v2.2 - 加原版下载
- 从 BMCLAPI 下载版本 JSON
- 下载客户端 jar、libraries、assets
- SHA1 校验 + 镜像回退

### v2.3 - 版本下拉框
- 从 BMCLAPI 拉取版本清单（916 个版本）
- 按"正式版 / 快照 / 远古版"分组

### v2.4 - 刷新列表保留选中
- 下载完刷新列表时保留用户选中项

### v2.5 - Forge 下载安装（有 bug）
- 加 Forge 下载 + 静默安装
- ⚠️ 有 bug：`MainWindow.log` 不能 `.emit`

### v2.6 - Forge 修复 + `inheritsFrom` 合并
- 修复 `log_callback` 兼容问题
- 实现 `inheritsFrom` 合并
- 支持 Forge 启动

### v2.7 - Fabric 支持（第一版）
- 加 `FabricInstaller` 类
- 从 Fabric Meta API 获取 loader 版本
- 从 Modrinth 获取 Fabric API 版本
- ⚠️ 有 bug：Fabric 的 Maven 格式 library 未处理

### v2.8 - 本地版本分组
- 下拉框加"本地版本"分组
- Forge / Fabric 等本地版本能显示
- Fabric 装完自动选中

### v2.9 - Maven 格式修复
- **修复 Fabric library 的 Maven 格式下载**（之前"共 0 个库"）
- classpath 拼装支持 Maven 格式
- ✅ Fabric 完全可用

### v3.0 - Forge / Fabric 共存 + 日志过滤 ⭐ **最新**
- **恢复 Forge 按钮**（与 Fabric 共存）
- **加日志过滤**（隐藏"保存世界"等噪音）
- **完整功能**：原版 / Forge / Fabric 三者共存

## 已知未实现

- ❌ 启动时自动补全（**检测缺失 → 自动下载**）
- ❌ Mod 管理 UI（**列出 / 启用 / 禁用 / 删除**）
- ❌ 整合包导入
- ❌ 微软登录
- ❌ 配置持久化
- ❌ 代理设置 UI
- ❌ 自动更新

## 如何使用

每个版本是独立的 Python 脚本：

```bash
pip install PyQt6 requests
python v3.0/MC_LuncherTEST-3.0.py
```

**依赖**：
- Python 3.11+
- PyQt6
- requests

**不包含**：
- `.minecraft/` 游戏数据
- `versions/` 版本文件
- `libraries/` 依赖库
- `assets/` 资源文件
- `temp/` 临时文件

## 参考文件

`reference/` 里是 **PCL2 生成的启动 .bat**：

- `启动 1.20.3.bat` - 原版启动命令
- `启动 1.20-Forge.bat` - Forge 启动命令
- `启动 1.20.4-Fabric 0.19.5.bat` - Fabric 启动命令

这些文件**展示了 PCL2 是如何拼 Java 命令的**，很有参考价值。

## 截图

### v3.0 界面

<img width="1315" height="977" alt="v3.0 界面" src="https://github.com/user-attachments/assets/197b4ebb-9a6c-4338-8121-eb9b3866f0ed" />

### Fabric 版本选择对话框

<img width="337" height="232" alt="Fabric 选择" src="https://github.com/user-attachments/assets/e67df611-1acb-483d-adfc-b90864cd8d30" />

### Fabric + 10 个 mod 加载成功

<img width="1352" height="989" alt="Fabric 成功" src="https://github.com/user-attachments/assets/10778b1d-c3a4-4907-a14f-0ad60d820f59" />

## 里程碑

- **v2.0**（2026-09-23 6:00）：第一次成功启动原版
- **v2.2**（2026-09-23 7:30）：第一次成功下载原版
- **v2.6**（2026-09-23 8:00）：第一次成功启动 Forge
- **v2.9**（2026-09-23 10:00）：第一次成功启动 Fabric
- **v3.0**（2026-09-23 12:08）：**原版 / Forge / Fabric 版本列表共存**

## 许可证

GPL-3.0（跟主项目一致）

