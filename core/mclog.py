"""把游戏控制台的输出还原成人看的一行行

## 为什么控制台是一坨 XML

Mojang 从 1.12 起让客户端用 `client-1.12.xml` 配置 log4j2（版本 JSON 的
`logging.client` 指向它，启动器要把它当 `-Dlog4j.configurationFile` 传进去，
见 core/launch.py）。那个文件里两个 appender 用的**不是同一种布局**：

    <Console name="SysOut" target="SYSTEM_OUT">
        <LegacyXMLLayout />                                    ← 控制台：XML
    </Console>
    <RollingRandomAccessFile name="File" fileName="logs/latest.log">
        <PatternLayout pattern="[%d{HH:mm:ss}] [%t/%level]: %msg{nolookups}%n" />
    </RollingRandomAccessFile>                                 ← 文件：给人看的

也就是说：**同一件事，写进 logs/latest.log 的是可读文本，打到控制台的是 XML**。
这段 XML 本来就是设计给启动器解析的 —— 官方启动器、PCL、HMCL 都会解析，
所以直接读进程标准输出的启动器（比如我们之前）会看到满屏 `<log4j:Event>`。

1.11 及更早没有这段配置，控制台本来就是可读文本，所以这里两种都要能处理：
认不出 XML 的行原样放过去。

## 还原成什么样

照 `logs/latest.log` 的样子来（也是玩家在别的启动器里看惯了的样子）：

    [05:42:24] [Server thread/INFO]: Saving and pausing game...

时间戳是 epoch 毫秒，转成本地时间。

## 异常堆栈为什么整块缩进一个 tab

XML 里堆栈在 `<log4j:Throwable>` 的 CDATA 里。还原时我把**每一行都缩进一个 tab**：

1. 日志窗口靠"缩进行 / `at ` 开头"判断这一行是上一条的续行
   （过滤、上色都跟着上一条走，见 ui/dialogs/log_window.py）
2. 不缩进的话，堆栈第一行（`java.lang.NullPointerException: ...`）既不缩进、
   也不以 `at ` 开头，会被当成一条**新**日志 —— 于是"藏掉 ERROR"之后
   异常头还挂在屏幕上，看着就像坏了
"""

import time
from xml.etree import ElementTree

# LegacyXMLLayout 输出的 <log4j:Event> **不带 xmlns 声明**，而 ElementTree
# 碰到没绑定的前缀会直接报错，所以解析前先补一个（log4j 1.x 时代的老命名空间）
_NAMESPACE = "http://logging.apache.org/log4j/"
_EVENT_OPEN = "<log4j:Event"
_EVENT_CLOSE = "</log4j:Event>"


def _tag_name(tag: str) -> str:
    """把 "{命名空间}Message" 变成 "Message" """
    return tag.rsplit("}", 1)[-1]


def _format_time(raw) -> str:
    """epoch 毫秒 → HH:MM:SS（本地时间）"""
    try:
        return time.strftime("%H:%M:%S", time.localtime(int(raw) / 1000))
    except (TypeError, ValueError, OSError):
        return "--:--:--"


def _render(fragment: str) -> "list[str]":
    """一个完整的 <log4j:Event> → 可读的若干行"""
    if "xmlns:log4j" not in fragment:
        fragment = fragment.replace(
            _EVENT_OPEN, f'{_EVENT_OPEN} xmlns:log4j="{_NAMESPACE}"', 1)

    try:
        event = ElementTree.fromstring(fragment)
    except ElementTree.ParseError:
        # 认不出来就原样放出去 —— 显示得难看，总比把内容吞掉强
        return fragment.splitlines()

    level = event.get("level", "")
    thread = event.get("thread", "")
    when = _format_time(event.get("timestamp"))

    message = ""
    throwable = ""
    for child in event:
        name = _tag_name(child.tag)
        if name == "Message":
            message = (child.text or "").strip("\n")
        elif name == "Throwable":
            # 堆栈一般以 \n 结尾（log4j 自己加的），去掉尾部空行
            throwable = (child.text or "").strip("\n")

    prefix = f"[{when}]"
    if thread:
        prefix += f" [{thread}/{level}]"
    elif level:
        prefix += f" [{level}]"
    head = f"{prefix}: {message}" if message else f"{prefix}:"

    lines = [head]
    # 每条堆栈行都缩进一个 tab，见模块开头的说明
    lines.extend("\t" + text for text in throwable.splitlines() if text.strip())
    return lines


class Log4jXmlReader:
    """一行一行喂进来，吐出可读的行（可能 0 行，也可能好几行）

    必须做成有状态的：一条日志事件在控制台里是 **3 行**（甚至更多，
    带堆栈时），得攒齐了才能还原。

        reader = Log4jXmlReader()
        for raw_line in stream:
            for line in reader.feed(raw_line):
                show(line)
        for line in reader.flush():     # 流断了但还有半条没收完
            show(line)
    """

    def __init__(self):
        self._pending = []
        self._in_event = False

    def feed(self, line: str) -> "list[str]":
        if not self._in_event:
            if _EVENT_OPEN in line:
                self._pending = [line]
                self._in_event = True
                # 理论上不会一行就是一条完整事件，但真碰上别卡住
                if _EVENT_CLOSE in line:
                    return self._finish()
                return []
            # XML 声明 / DOCTYPE 是布局自己加的壳，不是日志内容
            if line.startswith("<?xml") or line.startswith("<!DOCTYPE"):
                return []
            return [line]           # 老版本：控制台本来就是可读文本

        self._pending.append(line)
        if _EVENT_CLOSE in line:
            return self._finish()
        return []

    def flush(self) -> "list[str]":
        """流结束了。还有没收完的事件就原样吐出来，不吞内容"""
        if not self._pending:
            return []
        leftover = list(self._pending)
        self._pending = []
        self._in_event = False
        return leftover

    def _finish(self) -> "list[str]":
        fragment = "\n".join(self._pending)
        self._pending = []
        self._in_event = False
        return _render(fragment)
