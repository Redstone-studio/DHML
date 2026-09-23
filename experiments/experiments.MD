# 实验文件存档

这里是 Redstone Launcher 开发过程中的**实验文件**，不是正式代码。

## 目录结构

（同上）

## 各版本功能

### v2.0 - 最初的启动测试
- ✅ 读版本 JSON
- ✅ 拼 classpath
- ✅ 启动原版
- ❌ 动态解析 arguments

### v2.1 - 动态解析 arguments
- 在 v2.0 基础上：
- ✅ 动态解析 `arguments.jvm` 和 `arguments.game`
- ✅ 支持带 `rules` 的条件参数

### v2.2 - 加原版下载
- ✅ 下载版本 JSON
- ✅ 下载客户端 jar
- ✅ 下载 libraries（多线程）
- ✅ 下载 assets
- ✅ 解压 natives
- ✅ SHA1 校验 + 镜像失败回退官方

### v2.3 - 版本下拉框
- ✅ 从 BMCLAPI 拉取版本清单（916 个版本）
- ✅ 下拉框按"正式版 / 快照 / 远古版"分组
- ✅ 本地已安装的标记 `[已安装]`

### v2.4 - 刷新列表保留选中
- ✅ 下载完刷新列表时，保留用户原来的选中项

### v2.5 - 加 Forge 下载安装
- ⚠️ Forge 下载 + 静默安装
- ❌ Bug：`MainWindow.log` 是方法，不能 `.emit`
- ❌ Bug：`ForgeInstallWorker` 传了 `pyqtSignal` 对象，内部无法调用

### v2.6 - 修复 Forge 相关 bug ⭐ **能用**
- ✅ `inheritsFrom` 合并（支持 Forge 启动）
- ✅ `make_log_fn` 统一处理日志回调
- ✅ 变量名冲突修复
- ✅ 所有 `except` 加 `traceback`

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
      <td><code>inheritsFrom</code> 合并</td>
      <td align="center">v2.6</td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Forge 安装</td>
      <td align="center">v2.6</td>
      <td align="center">✅</td>
    </tr>
  </tbody>
</table>

## 已知未实现

（同上）

## 如何使用

（同上）

## 参考文件

（同上）

## 许可证

（同上）
