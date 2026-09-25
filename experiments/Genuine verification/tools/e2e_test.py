"""
端到端：假服务器 → 验证器 → 界面（含真实头像）

    python tools/e2e_test.py

和 `smoke_test.py` 的区别：那个验"结构"，这个验**整条链路真的通**：
起一个本地 Minecraft 假服务器（会应答 SLP 和 Query），
经 `PlayerListVerifier` 查它，再把结果喂给界面页面，最后确认头像真的下载并画上去了。

⚠️ 这一条会**真的联网**（取头像走公开皮肤站）。断网时会退回本地默认皮肤，
那时头像那几项会显示"本地兜底"，不算失败。
"""
import os
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, OSError):
    pass

from PyQt6.QtCore import QEvent, QObject, QTimer, pyqtSignal      # noqa: E402
from PyQt6.QtWidgets import QApplication                          # noqa: E402

from core.verify import player_list, selftest                     # noqa: E402


def _solid_png(size: int = 48) -> bytes:
    """造一张纯色 PNG 的字节（测试里当"已经下载好的头像"用）

    ⚠️ `QByteArray` 必须**留一个引用**。写成 `QBuffer(QByteArray())` 的话
    那个临时对象马上被 GC 回收，QBuffer 就指向一块已经释放的内存 ——
    进程直接 0xC0000409 崩掉，而且没有任何 Python 异常可看（踩过）。
    """
    from PyQt6.QtCore import QBuffer, QByteArray
    from PyQt6.QtGui import QColor, QPixmap

    pixmap = QPixmap(size, size)
    pixmap.fill(QColor("#5ec269"))
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(storage)


def main() -> int:
    app = QApplication.instance() or QApplication([])

    class Ticker(QObject):
        sig = pyqtSignal()
    ticker = Ticker()
    ticker.sig.connect(lambda: None)
    timer = QTimer()
    timer.setInterval(8)
    timer.timeout.connect(ticker.sig.emit)
    timer.start()

    def pump(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            time.sleep(0.004)

    fails = []

    def check(name, got, want):
        ok = got == want
        print(f"  {'OK ' if ok else 'X  '} {name}: {got!r}"
              + ("" if ok else f"   期望 {want!r}"))
        if not ok:
            fails.append(name)

    # ---------- 起假服务器 ----------
    print("1. 起本地假服务器")
    _slp, slp_port = selftest._bare_server(selftest._serve_slp)
    _qry, query_port = selftest._bare_server(selftest._serve_query,
                                             socket.SOCK_DGRAM)
    print(f"   SLP 端口={slp_port}  Query 端口={query_port}")

    # ---------- 验证器整条链路 ----------
    print("\n2. PlayerListVerifier（走真实 socket）")
    v = player_list.PlayerListVerifier()

    r = v.verify({"server_address": f"127.0.0.1:{query_port}",
                  "player_name": "Notch", "timeout": 3})
    check("   命中 → 通过", r.state, "verified")
    check("   拿到玩家", [p["name"] for p in r.players], ["Notch", "jeb_"])
    check("   地址是解析后的", (r.host, r.port), ("127.0.0.1", query_port))
    check("   有耗时", r.elapsed > 0, True)

    r2 = v.verify({"server_address": f"127.0.0.1:{query_port}",
                   "player_name": "herobrine", "timeout": 3})
    check("   大小写不敏感", r2.state, "absent")

    r3 = v.verify({"server_address": f"127.0.0.1:{slp_port}",
                   "player_name": "Herobrine", "timeout": 3})
    check("   只有 SLP → 无法判定", r3.state, "inconclusive")

    # ---------- 界面 ----------
    print("\n3. 界面渲染 + 头像")
    from ui.avatar_remote import AvatarPool, cache_key
    from ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1080, 720)
    win.show()
    pump(0.5)

    page = win.pages["verify"]
    page.address_edit.setText(f"127.0.0.1:{query_port}")
    page.name_edit.setText("Notch")

    # 同步跑一次（不起线程，方便断言）
    result = v.verify({"server_address": f"127.0.0.1:{query_port}",
                       "player_name": "Notch", "timeout": 3})
    players = list(result.players)
    # ⚠️ **用验证器真实返回的那份玩家**来算 key。
    # Query 那条路是**不给 UUID** 的，所以卡片的 key 会是名字（"notch"）而不是 UUID——
    # 上一版测试按 UUID 种缓存，于是"内存里明明有"却对不上，
    # 白白报了一个假失败。
    keys = [cache_key(p["name"], p["uuid"]) for p in players]
    print(f"   玩家={[p['name'] for p in players]}  来源={players[0]['source']}  keys={keys}")

    pool = AvatarPool.instance()

    # ---- 3a. 内存命中：不发请求，直接画 ----
    for key in keys:
        pool._memory[key] = _solid_png(48)

    called = []
    original_request = pool.request

    def spy_request(name, uuid="", size=40):
        called.append(cache_key(name, uuid))
        return original_request(name, uuid, size)

    pool.request = spy_request
    try:
        page._render(result)
        pump(0.4)

        check("   横幅是「通过」", page.banner.objectName(), "VerifyBannerOk")
        check("   卡片数", len(page._cards), len(players))
        check("   高亮的是 Notch",
              [c.player["name"] for c in page._cards if c.highlight], ["Notch"])
        check("   每张卡都有父控件",
              all(c.parent() is not None and not c.isWindow() for c in page._cards), True)
        check("   内存命中：不重复发请求", called, [])
        check("   头像画上去了",
              [c.avatar.pixmap().isNull() for c in page._cards], [False] * len(players))
        check("   头像尺寸",
              [c.avatar.pixmap().width() for c in page._cards], [40] * len(players))

        # ---- 3b. 内存没有时：必须**真的发起请求** ----
        # 这条是防回归的：第一版 PlayerCard 只 connect 了信号、没调 request()，
        # 头像永远停在首字母占位而且不报错。
        for key in keys:
            pool._memory.pop(key, None)
        called.clear()
        page._render(result)
        pump(0.3)
        check("   内存没有时每张卡都发起了请求", sorted(called), sorted(keys))
    finally:
        pool.request = original_request

    # ---- 3c. 等真实下载/磁盘缓存回来，头像最终要有 ----
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        pump(0.3)
        if all(not c.avatar.pixmap().isNull() for c in page._cards):
            break
    drawn = [not c.avatar.pixmap().isNull() for c in page._cards]
    print(f"   最终头像状态: {drawn}"
          f"（全 False 说明断网且磁盘缓存也没有 —— 不算失败）")

    # ---------- 4. 外置登录（authlib-injector）整条链路 ----------
    print("\n4. 外置登录：假验证站 → 验证器 → 界面 → 真皮肤头像")
    from core.verify.selftest_ali import TEST_USER, TEST_UUID, FACE_RGBA, FakeAliServer

    with FakeAliServer() as ali:
        page._on_method("thirdparty")
        pump(0.2)
        page.address_edit.setText(ali.base_url)
        page.name_edit.setText(TEST_USER)

        # ⚠️ 走**完整的 run_verify**（真的起 QThread），不是直接调 _render ——
        # 直接调的话线程、令牌、按钮状态那几段都没测到
        page.run_verify()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and not page.run_btn.isEnabled():
            pump(0.2)
        check("   线程跑完了", page.run_btn.isEnabled(), True)
        check("   横幅是通过", page.banner.objectName(), "VerifyBannerOk")
        check("   结果里点明不是正版验证", "正版" in page.banner.text(), True)
        check("   一张玩家卡", len(page._cards), 1)
        card = page._cards[0]
        check("   角色名", card.player["name"], TEST_USER)
        check("   拿到了皮肤地址", bool(card.player["skin_url"]), True)

        # 等皮肤下载 + 抠脸
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and card.avatar.pixmap().isNull():
            pump(0.25)
        check("   头像画上去了（真皮肤）", card.avatar.pixmap().isNull(), False)
        img = card.avatar.pixmap().toImage()
        c = img.pixelColor(img.width() // 2, img.height() // 2)
        check("   ⚠️ 头像中心 = 皮肤上脸的颜色",
              (c.red(), c.green(), c.blue(), c.alpha()), FACE_RGBA)

        # 切回玩家列表，确认密码框藏起来、地址被清掉
        shot_ali = Path(r"D:\deepseek\.uishots6\verify_ali.png")
        win.grab().save(str(shot_ali))
        print(f"   截图（外置登录）: {shot_ali}")

        page._on_method("player_list")
        pump(0.2)
        check("   切方式后密码框藏了", page.pass_edit.isVisible(), False)
        check("   切方式后地址清了", page.address_edit.text(), "")

    out = Path(r"D:\deepseek\.uishots6\verify_e2e.png")
    win.grab().save(str(out))
    print(f"   截图: {out}")

    pool.wait_for_done(3000)

    print(f"\n>>> {'全部通过' if not fails else '失败: ' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
