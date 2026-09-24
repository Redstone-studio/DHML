"""Minecraft 皮肤头像

从皮肤文件里抠出脸那一小块，放大成头像。

## 为什么是 (8, 8)

皮肤格式规定的：64×64 的皮肤里，**头部正面**那一小块就在 (8, 8)，8×8 像素。
上面那层 (40, 8) 是**帽子层**（第二层），很多皮肤靠它画头发、帽子、兽耳 ——
所以要叠在脸上一起抠，不然 Ari 的头发、Noor 的头巾都不见了。

## 挑哪张默认皮肤

1.19.4 之后游戏里有 **9 张默认皮肤**，没设皮肤的玩家显示哪一张由 UUID 决定。
我们算的离线 UUID 跟游戏**一模一样**（core/launch.py 的 offline_uuid 就是
`OfflinePlayer:<名字>` 的 MD5 + 置位），所以只要"挑哪张"的规则对得上，
启动器里的头像就跟游戏里一致。

⚠️ 规则（`floorMod(UUID.hashCode(), 9)`）和那 9 张的顺序是照 Minecraft 的实现写的，
**在这个环境里没法验证**。要是发现跟游戏里显示的不一样，只要改
`_DEFAULT_SKINS` 的顺序或者 `default_skin_name()` 里那一行即可 —— 都集中在这儿。

## 想换成自己的皮肤

把皮肤 png 放到**配置目录**的 `skins/<档案名>.png`（配置目录就是设置页底部写的那个），
优先级高于内置的默认皮肤。不用登录皮肤站，本地文件就行。
"""

import uuid as uuid_module
from pathlib import Path

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QImage, QPainter, QPainterPath, QPixmap

from core.config import get_config_dir
from core.resources import resource_path

SKIN_DIR = ("assets", "icons", "skins")
# 配置目录下的自定义皮肤位置（便携模式下就在启动器旁边）
CUSTOM_SKIN_DIR_NAME = "skins"

# 皮肤里那两块的位置：(x, y, 宽, 高)
# 圆角占边长的比例。0.28 左右是"圆滑但不圆成球"——
# 想更圆就往 0.5 调（0.5 = 正圆）
FACE_RADIUS_RATIO = 0.28

FACE_RECT = (8, 8, 8, 8)
HAT_RECT = (40, 8, 8, 8)

# 9 张默认皮肤。**顺序是照 Minecraft 的 DefaultPlayerSkin 抄的**，
# 顺序错了挑出来的就跟游戏不一样（改的时候注意）
_DEFAULT_SKINS = (
    "steve", "alex", "ari", "efe", "kai",
    "makena", "noor", "sunny", "zuri",
)


def java_uuid_hash(uuid_text: str) -> int:
    """Java 的 `UUID.hashCode()`

    Java 的实现是：高 64 位和低 64 位异或，再折成 32 位**有符号**整数：

        long hilo = mostSigBits ^ leastSigBits;
        return ((int)(hilo >> 32)) ^ (int)hilo;

    注意最后要按有符号看 —— `floorMod` 对负数的结果跟"无符号取模"差 4（mod 9），
    差这一点就会挑错皮肤。
    """
    value = uuid_module.UUID(str(uuid_text)).int
    hi = (value >> 64) & 0xFFFFFFFFFFFFFFFF
    lo = value & 0xFFFFFFFFFFFFFFFF
    hilo = hi ^ lo
    folded = ((hilo >> 32) ^ hilo) & 0xFFFFFFFF
    return folded - (1 << 32) if folded >= (1 << 31) else folded


def default_skin_name(account_uuid: str) -> str:
    """这个 UUID 在游戏里会显示哪张默认皮肤（挑不到就 Steve）"""
    if not account_uuid:
        return _DEFAULT_SKINS[0]
    try:
        index = java_uuid_hash(account_uuid) % len(_DEFAULT_SKINS)
    except (ValueError, AttributeError, TypeError):
        return _DEFAULT_SKINS[0]
    return _DEFAULT_SKINS[index]


def skin_path(name: str):
    """按名字找皮肤文件（配置目录里的自定义皮肤优先）"""
    name = str(name or "").strip()
    if not name or Path(name).name != name:
        return None

    mine = get_config_dir() / CUSTOM_SKIN_DIR_NAME / f"{name}.png"
    if mine.is_file():
        return mine

    builtin = Path(resource_path(*SKIN_DIR, f"{name.lower()}.png"))
    return builtin if builtin.is_file() else None


def account_skin_path(account) -> "Path | None":
    """这个档案用哪个皮肤文件

    查找顺序（**都是本地文件，不联网**）：

    1. 配置目录 `skins/<档案名>.png` —— 自己给这个档案准备的皮肤
    2. `assets/icons/skins/<档案名>.png` —— 放仓库里的（源码运行时方便）
    3. 按 UUID 挑的 9 张默认皮肤（同样先看配置目录，再看 assets）

    ⚠️ 为什么不把用户皮肤也放进 assets：**打包之后 assets 是只读的**
    （在 `_internal/` 里），用户往里放东西在 exe 版里根本不生效。
    所以"用户自己加的"必须落配置目录；assets 只放随程序分发的那 9 张。
    """
    if not account:
        return None
    name = str(account.get("name", "")).strip()
    if name:
        # 走 skin_path 而不是只看配置目录：这样 assets/icons/skins/<档案名>.png
        # 也能用（源码运行时直接丢文件进去就能看到效果）
        named = skin_path(name)
        if named is not None:
            return named
    account_type = account.get("type")
    if account_type == "offline":
        from core.launch import offline_uuid
        account_uuid = account.get("uuid") or offline_uuid(name)
    else:
        account_uuid = account.get("uuid", "")
    return skin_path(default_skin_name(account_uuid))


def face_pixmap(skin_file, size: int, dpr: float = 1.0, rounded: bool = True):
    """把皮肤里的脸抠出来放大成头像

    size 是**逻辑像素**，dpr 是屏幕缩放（125% 的屏传 1.25）。
    用**最近邻**放大：8×8 是像素画，平滑放大只会变成一团糊。
    倍数取整（8 → 40 是 5 倍），所以不会出现大小不一的像素。

    圆角**必须在画的时候裁**：QSS 的 border-radius 只影响背景和边框，
    QLabel 里的图片该多尖还是多尖（试过）。
    裁的是**放大之后**的图，边缘才有抗锯齿；在 8×8 上裁的话圆角会跟着放大成锯齿块。
    """
    if skin_file is None:
        return None
    image = QImage(str(skin_file))
    if image.isNull():
        return None

    # 脸 + 帽子层叠在一起（帽子层通常只有一部分像素不透明）
    head = QImage(8, 8, QImage.Format.Format_ARGB32)
    head.fill(Qt.GlobalColor.transparent)
    painter = QPainter(head)
    painter.drawImage(QRect(0, 0, 8, 8), image, QRect(*FACE_RECT))
    painter.drawImage(QRect(0, 0, 8, 8), image, QRect(*HAT_RECT))
    painter.end()

    scale = max(1, int(round(size * max(1.0, dpr) / 8)))
    side = 8 * scale
    scaled = head.scaled(side, side,
                         Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.FastTransformation)

    canvas = QPixmap(side, side)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    if rounded:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = max(2.0, side * FACE_RADIUS_RATIO)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, side, side), radius, radius)
        painter.setClipPath(path)
    painter.drawImage(0, 0, scaled)
    painter.end()

    canvas.setDevicePixelRatio(max(1.0, dpr))
    return canvas


def account_avatar(account, size: int = 32, dpr: float = 1.0, rounded: bool = True):
    """档案头像；没有皮肤文件就返回 None（调用方自己退回文字）"""
    return face_pixmap(account_skin_path(account), size, dpr, rounded)


def available_skins() -> "list[str]":
    """内置的默认皮肤名字（以后做"挑皮肤"界面用）"""
    try:
        return sorted(p.stem for p in resource_path(*SKIN_DIR).glob("*.png"))
    except OSError:
        return []


# 放进皮肤目录的说明。用户第一次打开那个目录时，得知道文件该叫什么名字
README_NAME = "说明.txt"
README_TEXT = """这个目录放你自己的 Minecraft 皮肤。

文件名 = 档案名。比如档案叫 BaLeLe，就把皮肤存成 BaLeLe.png
（档案名在「设置 → 账户」那一页能看到）

要求：
  · 64×64 的皮肤 png（老式 64×32 也行，脸的位置一样）
  · 头像取的是 (8,8) 到 (15,15) 那块脸，帽子层 (40,8) 会叠上去

放进来就优先用它，覆盖游戏默认的那 9 张。不用登录皮肤站，本地文件就行。
"""


def ensure_skin_dir():
    """确保"放自己皮肤"的目录存在，顺手放一份说明

    ⚠️ 这个目录**原来没人创建**：代码只是"去找"，找不到就退回内置皮肤。
    结果用户按设置页说的路径找过去，发现目录根本不存在，以为功能坏了
    （报了"没有啊"）。所以启动时建一下。
    """
    folder = get_config_dir() / CUSTOM_SKIN_DIR_NAME
    try:
        folder.mkdir(parents=True, exist_ok=True)
        readme = folder / README_NAME
        if not readme.is_file():
            readme.write_text(README_TEXT, encoding="utf-8", newline="\n")
    except OSError as e:
        print(f"[Avatar] 皮肤目录建不了: {e}")
    return folder
