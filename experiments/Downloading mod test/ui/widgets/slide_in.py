"""
入场动画：从左侧 / 下方滑入（可选回弹）

## 为什么不用 move() 做

这些卡片都放在 QVBoxLayout / QHBoxLayout 里，布局每一轮都会用 setGeometry()
把子控件摆回原位 —— 用 move() 挪出来的偏移量会被立刻抹掉，动画看不见。

所以偏移改从**布局内边距**下手：

    容器（布局给它的矩形不动）
      └── 卡片        ← 靠容器的 contentsMargins 把它整体推出去

内边距变的是"布局给子控件算出来的可用区域"，**不改变容器自己的几何位置**，
所以父布局不会重排，也就没有抖动。左边距为负 = 卡片往左溢出（从左侧滑入），
上边距为负 = 卡片往上溢出（从下方滑入）。溢出的部分被滚动区的 viewport 裁掉，
视觉上就是从那个方向滑进来的。

## 回弹怎么做

**不要用 QEasingCurve.Back**：那是先往反方向拉一下再冲过去，
用在"从左边滑入"上会先往右退一点，看着很别扭。

真正的"滑到位再弹一下"是三段（见 _build_animations）：

    起点 ──(滑到位，占 72% 时长)──> 0 ──> 冲过头 ──> 0

## 用法

    from ui.widgets.slide_in import SlideInRow, StaggerReveal
    from ui.widgets.anim_prefs import preset

    p = preset("slide_up")
    stagger = StaggerReveal(self, interval=p.interval, initial_offset=p.offset,
                            direction=p.direction, style=p.style,
                            duration=p.duration)
    row = SlideInRow(card, duration=p.duration, offset=p.offset,
                     direction=p.direction, style=p.style)
"""
from PyQt6.QtCore import (
    QTimer, QPropertyAnimation, QEasingCurve, QSequentialAnimationGroup,
    QParallelAnimationGroup, QVariantAnimation, pyqtProperty, pyqtSignal,
)
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect, QSizePolicy, QVBoxLayout, QWidget,
)

from ui.widgets.anim_prefs import (
    DIR_HORIZONTAL, DIR_VERTICAL, ANIM_SIMPLE, ANIM_BOUNCE, ANIM_SPRING,
    ANIM_PCL, EASE_CUBIC, EASE_QUINT, EASE_INOUT,
)
from ui.widgets import pcl_ease

# ============================================================
# PCL 的入场配方（真实数值，来自 ModAnimation 的实际调用点）
# ============================================================
#
# PCL 让元素进场时**不是一条曲线走完**，而是三条并行：
#
#   透明度   0 → 1        100ms  OutFluent(Weak)
#   位移 A   +5px → 0     250ms  OutFluent()      ← 快速把主要位移走完
#   位移 B   +11px → 0    350ms  OutBack()        ← 慢一点、带轻微过冲
#
# A、B 同时在跑，合成轨迹是"先快后慢 + 结尾轻轻一顿"。
# 实测 PCL 的合成曲线：189ms 处过冲 +2.55px，250ms 走完 96% 位移，
# 350ms 精确归零。
#
# 这比"选一条缓动曲线"自然得多 —— 单一曲线要么结尾太硬（OutCubic），
# 要么全程软绵绵（InOutCubic）。之前几档看着"一个味道"，根子就在这。
PCL_FADE_MS = 100        # 透明度那一条
PCL_FADE_MS_MIN = 60     # 太短会看不见淡入
PCL_FAST_RATIO = 0.71    # 位移 A 占总时长的比例（250/350）
PCL_FAST_SHARE = 0.31    # A 承担多少位移（5/16）
PCL_SLOW_SHARE = 0.69    # B 承担多少位移（11/16）
# 默认值（没传预设时用；等于"左侧滑入"那一档的参数）
DEFAULT_DURATION = 420       # 单张卡片滑完要多久（毫秒）
DEFAULT_INTERVAL = 95        # 相邻两张之间隔多久（毫秒）
DEFAULT_OFFSET = 120         # 起点离终点多远（像素）
DEFAULT_OVERSHOOT = 16       # 回弹时冲过头多少像素
DEFAULT_BOUNCE_RATIO = 0.72  # 只对 bounce 有效：多久滑到位
DEFAULT_EASE = EASE_CUBIC

# 弹簧：阻尼正弦。周期数越多，来回弹的次数越多
SPRING_CYCLES = 1.15
SPRING_DECAY = 5.0

_EASE_CURVES = {
    EASE_CUBIC: QEasingCurve.Type.OutCubic,
    EASE_QUINT: QEasingCurve.Type.OutQuint,
    EASE_INOUT: QEasingCurve.Type.InOutCubic,
}


def _ease_curve(name: str):
    """名字 → QEasingCurve

    支持两种写法：
      · `"cubic"` / `"quint"` / `"inout"` —— Qt 内置的
      · `"back:0.18"` / `"spring:0.14"` / `"elastic"` —— 自己在 pcl_ease 里
        实现的参数化版本（冒号后面是过冲比例）。Qt 内置的过冲写死 10%，
        想要别的比例就得用这种。
    """
    if name in _EASE_CURVES:
        return _EASE_CURVES[name]

    if name and ":" in name:
        head, _, raw = name.partition(":")
        try:
            arg = float(raw)
        except ValueError:
            arg = None
        if head == "back" and arg is not None:
            return pcl_ease.named("back_out", overshoot=arg)
        if head == "spring" and arg is not None:
            return pcl_ease.named("spring", overshoot=arg)
        if head == "power" and arg is not None:
            return pcl_ease.named("power_out", power=arg)

    if name == "back":
        return pcl_ease.named("back_out", overshoot=0.10)
    if name == "elastic":
        return pcl_ease.named("elastic_out")
    if name == "spring":
        return pcl_ease.named("spring")

    return QEasingCurve.Type.OutCubic


def _layout_of(widget: QWidget):
    """控件当前的布局（还没设过就建一个，边距清零）"""
    layout = widget.layout()
    if layout is None:
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
    return layout


class SlideInRow(QWidget):
    """把已有控件包起来，给它加"滑入"动画

    为什么包一层而不是直接动画卡片本身：卡片的布局是卡片自己的，
    在这里改它的内边距会跟卡片内部样式打架。包一层之后，
    卡片照旧按原来的方式工作（信号、样式都不用动），
    这一层只负责"它在屏幕上的位置"。
    """

    # 位移变了（调试/观测用；动画过程本身不依赖它）
    slideOffsetChanged = pyqtSignal(float)

    def __init__(self, child: QWidget, duration: int = DEFAULT_DURATION,
                 offset: int = DEFAULT_OFFSET, parent: QWidget = None,
                 direction: str = DIR_HORIZONTAL, style: str = ANIM_SIMPLE,
                 overshoot: int = DEFAULT_OVERSHOOT,
                 bounce_ratio: float = DEFAULT_BOUNCE_RATIO,
                 ease: str = DEFAULT_EASE, fade: bool = None):
        super().__init__(parent)

        # ⚠️ **垂直方向咬死**：这一行是包在 QVBoxLayout 里的，如果它是默认的
        # Preferred/Preferred，父布局在"内容不满一屏"时会把多出来的高度
        # **平摊给每一行** —— 实测搜索结果只有 2 条时，每行被拉到 224px
        # （包着的卡片本身 setFixedHeight(100) 没变），多出来的 124px 就表现成
        # "两张卡片之间隔了一大段空白"。
        # 一行的高度应该永远等于它包着的内容，多出来的空间留给父布局的撑开项。
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        # fade 传 None = 听设置的（见 settings 里的"入场淡入"开关）。
        # 用 None 而不是 True 当默认，是为了让"关掉淡入"这个设置真的生效 ——
        # 默认写 True 的话就永远覆盖掉用户的设置。
        if fade is None:
            from ui.widgets import appearance
            fade = appearance.get_anim_fade()
        self._child = child
        self._offset = max(0, int(offset))
        self._duration = max(1, int(duration))
        self._direction = direction
        self._style = style
        self._overshoot = max(1, int(overshoot))
        self._bounce_ratio = min(0.95, max(0.05, float(bounce_ratio)))
        self._ease = ease
        self._slide_offset = float(self._offset)

        layout = _layout_of(self)
        layout.addWidget(child)

        # 起手先摆到屏幕外面去：add() 和 reveal() 之间会隔着几帧，
        # 不先摆好的话会闪一下"已经就位"的样子
        self._apply(self._offset)

        # ⚠️ 动画的属性必须是**标量**，而且动画要能"分段"（回弹是三段），
        # 所以用 QSequentialAnimationGroup 装。别用 b"pos"：QPoint 没法 round()。
        self.anim = QSequentialAnimationGroup(self)
        self.anim.finished.connect(self._on_finished)
        self._on_done = None

        # 淡入：纯位移的入场看着机械，位移 + 透明度一起动才像"出现"。
        # 用 QGraphicsOpacityEffect 而不是把卡片底色做半透明 —— 那是两回事：
        # 底色透明是"卡片变透"，这个是"整张卡片渐显"。
        # ⚠️ 动画一结束就把 effect 摘掉：它会让 Qt 每次都先渲染到离屏缓冲，
        # 挂着不用纯属白白拖慢滚动。
        self._fade = None
        self._fade_anim = None
        if fade:
            self._fade = QGraphicsOpacityEffect(self)
            self._fade.setOpacity(1.0)
            self.setGraphicsEffect(self._fade)

        # ⚠️ PCL 那种"位移拆两条并行曲线"必须用**两个独立属性**，
        # 不能两条 QPropertyAnimation 都写 slideOffset —— 同一个属性被两条
        # 动画同时写，Qt 这边会打架（实测：位移卡在起手值、父组状态变 Stopped
        # 但里面那条还在跑，动画等于废了）。所以拆成 fast/slow 两个分量，
        # 合起来才是真正要用的偏移量。
        self._fast_offset = 0.0
        self._slow_offset = 0.0

    # ---------- 动画属性 ----------

    def get_slide_offset(self) -> float:
        """当前"被推出去多少像素"（0 = 已就位）"""
        return self._slide_offset

    def set_slide_offset(self, value):
        self._slide_offset = float(value)
        self._apply(value)
        self.slideOffsetChanged.emit(self._slide_offset)

    slideOffset = pyqtProperty(float, fget=get_slide_offset, fset=set_slide_offset)

    def get_fast_offset(self) -> float:
        return self._fast_offset

    def set_fast_offset(self, value):
        self._fast_offset = float(value)
        self._sync_offset()

    def get_slow_offset(self) -> float:
        return self._slow_offset

    def set_slow_offset(self, value):
        self._slow_offset = float(value)
        self._sync_offset()

    # PCL 双曲线模型的两个分量：fast 走主要位移，slow 负责收尾和过冲。
    # 两者相加才是实际偏移量（见 _sync_offset）。
    fastOffset = pyqtProperty(float, fget=get_fast_offset, fset=set_fast_offset)
    slowOffset = pyqtProperty(float, fget=get_slow_offset, fset=set_slow_offset)

    def _sync_offset(self):
        """把两个分量合成总偏移"""
        total = self._fast_offset + self._slow_offset
        self._slide_offset = total
        self._apply(total)
        self.slideOffsetChanged.emit(total)

    # ---------- 内部 ----------

    def _apply(self, value):
        """把"推出多少像素"写进布局内边距

        用 setContentsMargins（QLayout 上都有），不用 setViewportMargins ——
        后者只存在于 QAbstractScrollArea 那一路，QVBoxLayout 上没有。
        """
        margin = -int(round(value))
        layout = _layout_of(self)
        if self._direction == DIR_VERTICAL:
            layout.setContentsMargins(0, margin, 0, 0)
        else:
            layout.setContentsMargins(margin, 0, 0, 0)

    def _track(self, start: float, end: float, duration: int, curve,
               prop: bytes = b"slideOffset"):
        anim = QPropertyAnimation(self, prop)
        anim.setDuration(max(1, int(duration)))
        anim.setStartValue(float(start))
        anim.setEndValue(float(end))
        anim.setEasingCurve(curve)
        return anim

    def _spring(self) -> QVariantAnimation:
        """真·弹簧：阻尼正弦

            offset(t) = overshoot · (包络(t) / 包络峰值) · (-sin(2π·cycles·t))

        t 从 0 到 1。t=0 时值就是 0（已就位），随后往**负方向**冲出去
        （负 = 卡片继续往滑入的那一侧多走一点），再阻尼收敛回 0。

        两个细节，都是调出来的：
          · 前面乘 -1：不乘的话第一个峰值跑到**正**方向，看着是"滑过头往回退"，
            方向反了。要从滑入方向过冲才对。
          · 包络除以它自己的峰值：不这么做的话 overshoot 只是个系数，
            实际过冲量会被衰减吃掉一大半（设 16px 只能冲到 6px 左右），
            参数就没法照着"想弹多少像素"来填了。
        """
        import math

        anim = QVariantAnimation(self)
        anim.setDuration(self._duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.Linear)   # 曲线自己在回调里算

        # 包络 e^(-λt)·|sin| 的峰值出现在 t*，用它归一化，保证过冲正好是 overshoot
        t_peak = (1.0 / (2 * math.pi * SPRING_CYCLES)) * math.atan(
            2 * math.pi * SPRING_CYCLES / SPRING_DECAY)
        peak = (math.exp(-SPRING_DECAY * t_peak)
                * math.sin(2 * math.pi * SPRING_CYCLES * t_peak))
        scale = self._overshoot / peak if peak > 1e-6 else 0.0

        def on_value(t):
            t = float(t)
            value = -(scale
                      * math.exp(-SPRING_DECAY * t)
                      * math.sin(2 * math.pi * SPRING_CYCLES * t))
            self.set_slide_offset(value)

        anim.valueChanged.connect(on_value)
        return anim

    def _build_pcl(self):
        """PCL 那套：位移拆两条并行曲线（快 + 慢带回弹），透明度单独一条淡入

        时间轴（以总时长 T 为基准）：
            0 ─────────────── T*0.71 ────────── T
            位移A(快的部分, 走 31% 位移) ─┘
            位移B(慢的部分, 走 69% 位移, 带回弹) ─────┘
            透明度(前 100ms 淡入) ─┘

        两条位移加起来正好是 offset，所以终点还是精确落位。
        """
        fast_ms = max(60, int(self._duration * PCL_FAST_RATIO))
        slow_ms = max(fast_ms + 20, self._duration)
        a0 = self._offset * PCL_FAST_SHARE
        b0 = self._offset * PCL_SLOW_SHARE

        group = QParallelAnimationGroup(self)
        # 位移 A：主要位移，快速走完，收尾绵长（写 fastOffset）
        group.addAnimation(
            self._track(a0, 0, fast_ms, QEasingCurve.Type.OutQuint, b"fastOffset"))
        # 位移 B：慢一点，带轻微过冲（写 slowOffset）
        group.addAnimation(
            self._track(b0, 0, slow_ms, pcl_ease.named("pcl_back", power=2.0),
                        b"slowOffset"))
        # 透明度：淡入，和位移同时开始
        fade_ms = max(PCL_FADE_MS_MIN, min(PCL_FADE_MS, fast_ms))
        self._fade_anim = QPropertyAnimation(self._fade, b"opacity")
        self._fade_anim.setDuration(fade_ms)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(self._fade_anim)

        self.anim.addAnimation(group)

    def _build_animations(self):
        """攒出这一段动画（普通 / 两段回弹 / 弹簧 / PCL 双曲线）"""
        self.anim.clear()
        if self._style == ANIM_PCL and self._fade is not None:
            self._build_pcl()
            return
        if self._style == ANIM_SPRING:
            self.anim.addAnimation(self._spring())
        elif self._style == ANIM_BOUNCE:
            # 滑到位 → 冲过头 → 收回来
            main = int(self._duration * self._bounce_ratio)
            back = max(2, self._duration - main)
            self.anim.addAnimation(
                self._track(self._offset, 0, main, _ease_curve(self._ease)))
            self.anim.addAnimation(
                self._track(0, -self._overshoot, back // 2,
                            QEasingCurve.Type.OutQuad))
            self.anim.addAnimation(
                self._track(-self._overshoot, 0, back - back // 2,
                            QEasingCurve.Type.InOutQuad))
        else:
            self.anim.addAnimation(
                self._track(self._offset, 0, self._duration,
                            _ease_curve(self._ease)))

    def _on_finished(self):
        # 结束时兜一次底：动画最后一步的值偶尔差不到 1px，
        # 不留着这点偏移，免得卡片看着像没对齐。
        # 两个分量也要一起归零，不然下次播放会从残留值起手。
        self._fast_offset = 0.0
        self._slow_offset = 0.0
        self._apply(0)
        self.slideOffsetChanged.emit(0.0)
        # 淡入结束就把 effect 摘掉（见构造函数里的说明）
        if self._fade is not None:
            self.setGraphicsEffect(None)
            self._fade = None
        done, self._on_done = self._on_done, None
        if done is not None:
            done()

    # ---------- 对外 ----------

    def reveal(self, on_done=None):
        """开始滑入；已经在播的会先停掉重来"""
        self.anim.stop()
        self._on_done = on_done
        self._build_animations()
        if self._style == ANIM_PCL and self._fade is not None:
            # PCL 档：两个分量各自从起手值开始（由 _build_pcl 里的动画负责）
            self._fast_offset = 0.0
            self._slow_offset = 0.0
            self._apply(self._offset)
        else:
            self._apply(self._offset)
        self.show()
        self.anim.start()

    def stop(self):
        """停下动画并**归零**（不是重置到起手值）

        ⚠️ 这里必须是"就位"，不能是"回到起手位置"：
        `clear()` 会对**已经播完**的行也调它（注册表里就存着），
        如果这里把偏移重置成起手值，那些已经就位的卡片会被重新推到屏幕外
        （实测：搜索页 12 张卡片全卡在偏移 120px，等于整个列表看不见）。
        """
        self.anim.stop()
        self._fast_offset = 0.0
        self._slow_offset = 0.0
        self._apply(0)
        if self._fade is not None:
            self.setGraphicsEffect(None)
            self._fade = None
        self._on_done = None

    def closeEvent(self, event):
        self.anim.stop()
        super().closeEvent(event)


class StaggerReveal:
    """让一批 SlideInRow 依次滑入

    用**一个**定时器按顺序点名，而不是给每张卡片各来一个 singleShot：
    重新搜索时要能一句话全部叫停，否则定时器会打到已经被 deleteLater()
    删掉的控件上。

    这个类不是 QWidget —— 它只是个调度器，生命周期挂在 parent 上。
    """

    def __init__(self, parent: QWidget, interval: int = DEFAULT_INTERVAL,
                 initial_offset: int = DEFAULT_OFFSET, on_all_done=None,
                 direction: str = DIR_HORIZONTAL, style: str = ANIM_SIMPLE,
                 duration: int = None, overshoot: int = DEFAULT_OVERSHOOT,
                 bounce_ratio: float = DEFAULT_BOUNCE_RATIO,
                 ease: str = DEFAULT_EASE):
        self._parent = parent
        self._interval = max(0, int(interval))
        self._initial_offset = max(0, int(initial_offset))
        self._direction = direction
        self._style = style
        self._duration = duration          # None = 交给 SlideInRow 的默认值
        self._overshoot = overshoot
        self._bounce_ratio = bounce_ratio
        self._ease = ease
        self._pending = []
        self._spawned = []          # 已经出场（正在播或播完）的，clear 时要停掉
        self._on_all_done = on_all_done
        self._running = False

        self._timer = QTimer(parent)
        self._timer.setInterval(max(1, self._interval))
        self._timer.timeout.connect(self._on_tick)

    # ---------- 收集 ----------

    def _wrap(self, widget: QWidget) -> SlideInRow:
        if isinstance(widget, SlideInRow):
            return widget
        kwargs = dict(
            offset=self._initial_offset,
            direction=self._direction,
            style=self._style,
            overshoot=self._overshoot,
            bounce_ratio=self._bounce_ratio,
            ease=self._ease,
        )
        if self._duration is not None:
            kwargs["duration"] = self._duration
        return SlideInRow(widget, **kwargs)

    def add(self, widget: QWidget) -> SlideInRow:
        """排一个（返回包好的 SlideInRow，记得把它加进布局）"""
        row = self._wrap(widget)
        # 先脱开父控件：轮到它之前不该出现在界面上，也不能被布局量进去
        # （否则会先闪一下"已就位"再开始滑）
        row.setParent(None)
        row.setVisible(False)
        self._pending.append(row)
        return row

    def add_many(self, widgets) -> list:
        """排一批，排完自动开始"""
        rows = [self.add(w) for w in widgets]
        if rows:
            self.start()
        return rows

    # ---------- 调度 ----------

    def start(self):
        if not self._pending:
            return
        self._running = True
        self._timer.start()
        self._on_tick()

    def clear(self):
        """叫停并清空（重新搜索 / 换详情页时调）

        注意这里**两件事都做**：
          · 清掉还没轮到播的队列
          · 停掉**正在播**的那些（它们已经在界面上了，不停的话会继续动）
        只做前者的话，旧行的动画会继续跑打在即将被删除的控件上。
        """
        self._timer.stop()
        self._running = False
        for row in self._pending:
            row.stop()
        for row in self._spawned:
            row.stop()
        self._pending.clear()
        self._spawned.clear()

    def pending_count(self) -> int:
        """还没开始滑的有几个（只读，给测试/调试用）"""
        return len(self._pending)

    def _on_tick(self):
        if self._pending:
            row = self._pending.pop(0)
            self._spawned.append(row)
            row.reveal()
        if not self._pending:
            # 队列空了但最后一张还在滑 —— 这时候停掉定时器是对的，
            # 收尾回调由最后一张的 finished 负责（on_all_done 在这里给）
            self._timer.stop()
            if self._running:
                self._running = False
                done, self._on_all_done = self._on_all_done, None
                if done is not None:
                    done()
