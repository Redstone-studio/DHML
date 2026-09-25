"""
界面冒烟测试（离屏跑，不需要显示器）

    python tools/smoke_test.py

验的是"结构对不对"，不是"好不好看"：
  · 窗口能建起来、样式变量全部替换掉
  · 验证页四种结果状态各自渲染成什么
  · 玩家卡片真的进了布局（**不是变成独立窗口**）
  · 头像下载器不会重复请求同一个 key

⚠️ 跑这个要用 `QT_QPA_PLATFORM=offscreen`，否则会弹出一个真窗口。
"""
import os
import sys
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


class _Ticker(QObject):
    """一个 8ms 的定时器：离屏跑的时候靠它把动画/定时器驱动起来"""
    sig = pyqtSignal()


def make_pump(app):
    def pump(seconds: float):
        """推进事件循环

        ⚠️ 必须显式处理 `DeferredDelete`：`processEvents()` 默认**不处理**它，
        `deleteLater()` 的控件永远不销毁，量到的几何是"新旧混在一起"的假象
        （另一个实验项目在这上面栽过两次）。
        """
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            time.sleep(0.003)
    return pump


def main() -> int:
    app = QApplication.instance() or QApplication([])
    ticker = _Ticker()
    ticker.sig.connect(lambda: None)
    timer = QTimer()
    timer.setInterval(8)
    timer.timeout.connect(ticker.sig.emit)
    timer.start()
    pump = make_pump(app)

    fails = []

    def check(name, got, want):
        ok = got == want
        print(f"  {'OK ' if ok else 'X  '} {name}: {got!r}"
              + ("" if ok else f"   期望 {want!r}"))
        if not ok:
            fails.append(name)

    # ---------- 1. 窗口 + 样式 ----------
    print("1. 窗口与样式")
    from core import theme
    from ui.main_window import MainWindow

    # ⚠️ 把配置目录指到临时目录：不隔离的话测试会读/写你**真实的** accounts.json
    # 和 client_id（跑一次测试就把账号改了，这种事不能干）
    import tempfile
    import pathlib
    from core import config as core_config
    from core import accounts as core_accounts
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="verifytest_"))
    core_config.get_config_dir = lambda: tmp_dir
    core_accounts.get_config_dir = lambda: tmp_dir
    core_accounts.accounts_path = lambda: tmp_dir / core_accounts.FILE_NAME

    # ⚠️ Fusion 是必需的：Windows 默认走原生样式（windows11/windowsvista），
    # 它会自己画按钮斜面、下拉箭头、勾选框方块，和这套 QSS 打架而且不报错。
    # 这条断言是防它被删掉的。
    import main as entry
    entry.configure_app(app)
    check("   app.setStyle 用了 Fusion", app.style().objectName().lower(), "fusion")

    win = MainWindow()
    win.resize(1080, 720)
    win.show()
    pump(0.6)

    check("   页面", sorted(win.pages), ["accounts", "log", "settings", "verify"])
    style = win.styleSheet()
    check("   样式非空", bool(style.strip()), True)
    check("   变量全部替换", theme.unresolved(style), [])
    check("   @ICONS@ 已替换", "@ICONS@" not in style, True)
    # 主项目 DARK 的 bg_page，替换成功才会出现
    check("   用上了主项目调色板", "#1b1c1f" in style, True)

    # ---------- 2. 注册表 ----------
    print("\n2. 验证方式注册表")
    from core.verify import all_verifiers, get_verifier, implemented_verifiers

    check("   方式数量", len(all_verifiers()), 3)
    check("   可用的（顺序 = 显示顺序）",
          [v.key for v in implemented_verifiers()],
          ["microsoft", "thirdparty", "player_list"])
    check("   取不存在的 key 有兜底", get_verifier("nope").key, "microsoft")
    check("   只有微软是交互式的",
          [v.key for v in all_verifiers() if v.interactive], ["microsoft"])
    check("   只有微软要客户端 ID",
          [v.key for v in all_verifiers() if v.needs_client_id], ["microsoft"])

    print("\n2b. 三种方式的面板切换")
    page0 = win.pages["verify"]
    for key, want_form, want_panel, want_pass in (
            ("microsoft", False, True, False),
            ("thirdparty", True, False, True),
            ("player_list", True, False, False)):
        page0._on_method(key)
        pump(0.2)
        has_panel = page0.login_panel is not None and page0.login_panel.isVisible()
        check(f"   {key}：表单可见", page0.form_card.isVisible(), want_form)
        check(f"   {key}：登录面板可见", has_panel, want_panel)
        check(f"   {key}：密码框可见", page0.pass_edit.isVisible(), want_pass)
    check("   Query 选项只对玩家列表有意义",
          page0.query_check.isVisible(), True)

    print("\n2c. 设备码面板：不会出现「点了没反应」")
    page0._on_method("microsoft")
    pump(0.2)
    panel = page0.login_panel

    # ⚠️ 防回归：第一版"没填 client_id 就禁用开始按钮 + 只给 tooltip"，
    # 而 **Windows 上禁用按钮不弹 tooltip** —— 用户点了就是"什么都没发生"。
    panel.client_edit.clear()
    pump(0.15)
    check("   没填 ID 时按钮仍可点", panel.start_btn.isEnabled(), True)
    panel.start()
    pump(0.2)
    check("   点了会说缺什么",
          "Azure" in panel.status_label.text(), True)

    panel.client_edit.setText("11111111-2222-3333-4444-555555555555")
    pump(0.15)
    check("   填了 ID 也能点", panel.start_btn.isEnabled(), True)
    check("   还没开始时不显示设备码", panel.code_box.isVisible(), False)

    # ⚠️ 防回归：失败之后按钮必须**恢复可点**，否则用户再也不能重试。
    # 第一版就是因为 `worker.isRunning()` 在信号处理那一刻还是 True，
    # 从此按钮永远是灰的。
    panel._on_failed("测试步骤", "测试消息", "测试提示")
    pump(0.15)
    check("   ⚠️ 失败后开始按钮恢复可点", panel.start_btn.isEnabled(), True)
    check("   失败后输入框也恢复", panel.client_edit.isEnabled(), True)
    check("   失败信息带步骤", "测试步骤" in panel.status_label.text(), True)

    panel._on_done({}, None)
    pump(0.15)
    check("   成功后也能再登一次", panel.start_btn.isEnabled(), True)
    panel.stop()

    print("\n2d. 账户页（本地存储是隔离的）")
    from core import accounts as acc
    page_accounts = win.pages["accounts"]
    win.switch_page("accounts")
    pump(0.2)
    check("   一开始没有账号", acc.all_accounts(), [])
    check("   空提示可见", page_accounts.empty_label.isVisible(), True)
    acc.upsert({"type": "microsoft", "name": "TestSteve",
                "uuid": "069a79f444e94726a5befca90e38aaf5",
                "access_token": "x", "refresh_token": "y", "expires_at": 0})
    page_accounts.reload()
    pump(0.2)
    check("   加一个就出现一行", len(page_accounts._rows), 1)
    check("   空提示藏起来", page_accounts.empty_label.isVisible(), False)
    # 令牌不能出现在界面上
    labels = [c.text() for r in page_accounts._rows
              for c in r.findChildren(type(page_accounts.empty_label))]
    check("   ⚠️ 界面标签里没有令牌明文",
          any("y" == t or "x" == t for t in labels), False)
    win.switch_page("verify")

    print("\n2e. 日志：脱敏（最要紧的一条）")
    from core import logbook
    secret = {"access_token": "S1", "refresh_token": "S2", "password": "S3",
              "identityToken": "S4", "RpsTicket": "S5", "device_code": "S6",
              "Authorization": "S7", "token_type": "Bearer", "expires_in": 3600,
              "author": "Notch", "uhs": "abc"}
    red = logbook.redact(secret)
    check("   令牌/密码/设备码全被遮",
          [red[k] for k in ("access_token", "refresh_token", "password",
                            "identityToken", "RpsTicket", "device_code",
                            "Authorization")],
          ["***"] * 7)
    # ⚠️ 这两条防的是"遮过头"：token_type 的值是 Bearer 不是秘密，
    # 而 author 会被 `auth` 这个关键词误伤
    check("   token_type 不遮", red["token_type"], "Bearer")
    check("   expires_in 不遮", red["expires_in"], 3600)
    check("   ⚠️ author 不被误伤", red["author"], "Notch")
    check("   uhs 不遮", red["uhs"], "abc")
    masked_url = logbook.redact_url("https://x/cb?code=ABC&state=D")
    check("   ⚠️ URL 里的授权码要遮", ("***" in masked_url and "ABC" not in masked_url), True)
    check("   普通查询参数不动",
          logbook.redact_url("https://a/b?query=sodium&limit=12"),
          "https://a/b?query=sodium&limit=12")

    print("\n2f. 日志页")
    logbook.clear()
    log_page = win.pages["log"]
    win.switch_page("log")
    pump(0.2)
    check("   没日志时的提示", "还没有日志" in log_page.empty_hint.text(), True)
    logbook.ok("OAuth2 设备码", "POST /devicecode → HTTP 200",
               request={"device_code": "SECRET-DEV"},
               response={"user_code": "K7Q9-XYZ"})
    logbook.error("XSTS", "POST /xsts → HTTP 401", response={"XErr": 2148916238})
    pump(0.4)
    body = log_page.view.toPlainText()
    check("   来了日志提示就藏起来", log_page.empty_hint.isVisible(), False)
    check("   ⚠️ 正文里没有令牌明文", "SECRET" in body, False)
    check("   有用信息还在（XErr 码）", "2148916238" in body, True)
    check("   级别计数", [b.text() for b in log_page.level_buttons.values()],
          ["信息 0", "成功 1", "警告 0", "错误 1"])
    for btn in log_page.level_buttons.values():
        btn.setChecked(False)
    log_page._rebuild()
    pump(0.2)
    check("   全筛掉时说的是「筛选」不是「没日志」",
          "筛选" in log_page.empty_hint.text() and log_page.empty_hint.isVisible(),
          True)
    for btn in log_page.level_buttons.values():
        btn.setChecked(True)
    logbook.clear()
    log_page._rebuild()
    pump(0.2)
    check("   清空后回到「没日志」", "还没有日志" in log_page.empty_hint.text(), True)
    win.switch_page("verify")

    # ---------- 3. 四种状态渲染 ----------
    print("\n3. 验证页四态渲染")
    from core.verify.base import VerifyResult
    from ui.pages.verify_page import BANNER_STYLE

    class FakeStatus:
        version, online, max, query_ok = "1.20.4", 3, 20, True
        motd, slp_players, query_players = "测试服", [], []

    page = win.pages["verify"]
    players = [
        {"name": "Notch", "uuid": "069a79f444e94726a5befca90e38aaf5", "source": "slp"},
        {"name": "jeb_", "uuid": "", "source": "query"},
    ]
    page.name_edit.setText("Notch")

    for state in ("verified", "absent", "inconclusive", "error"):
        result = VerifyResult(state, f"{state} 的说明", players=list(players),
                              server=FakeStatus(), host="127.0.0.1", port=25565,
                              elapsed=0.42)
        page._render(result)
        pump(0.25)
        check(f"   {state} 横幅样式", page.banner.objectName(), BANNER_STYLE[state])
        check(f"   {state} 卡片数", len(page._cards), 2)
        # ⚠️ 卡片必须有父控件：没有的话它是个**独立顶层窗口**
        check(f"   {state} 卡片有父控件",
              all(c.parent() is not None and not c.isWindow() for c in page._cards), True)
        # "就是你"那张要高亮
        check(f"   {state} 高亮命中", [c.highlight for c in page._cards], [True, False])

    check("   详情行", page.detail.text().startswith("实际连接 127.0.0.1:25565"), True)

    print("\n3b. host 是 URL 时不能拼 :端口")
    # ⚠️ 防回归：外置登录那条路的 host 是完整 API 根，
    # 无条件拼 ":端口" 会输出 `https://x/api/yggdrasil:0`（实测踩过）
    url_result = VerifyResult("verified", "外置登录通过",
                              players=[], server=None,
                              host="https://demo.lunch.ink/api/yggdrasil",
                              port=0, elapsed=1.5)
    page._render(url_result)
    pump(0.15)
    check("   标签是 API 根", page.detail.text().startswith("API 根 https://"), True)
    check("   没有 :0 这种尾巴", ":0" in page.detail.text(), False)

    # ---------- 4. 令牌校验（过期结果必须被丢掉）----------
    print("\n4. 过期结果不能覆盖界面")
    page._token = 7
    stale = VerifyResult("verified", "过期的结果", token=6)
    page.banner.setText("当前横幅")
    page._on_done(stale)
    pump(0.1)
    check("   过期结果被丢掉", page.banner.text(), "当前横幅")
    fresh = VerifyResult("absent", "新鲜的结果", token=7)
    page._on_done(fresh)
    pump(0.1)
    check("   当前结果被接受", "新鲜的结果" in page.banner.text(), True)

    # ---------- 5. 清空 ----------
    print("\n5. 清空结果")
    page.clear_results()
    pump(0.2)
    check("   卡片清空", len(page._cards), 0)
    check("   空状态可见", page.empty_label.isVisible(), True)
    check("   布局里只剩空状态 + 撑开项", page.players_layout.count(), 2)

    # ---------- 6. 头像下载器 ----------
    print("\n6. 头像下载器（不发真请求，只验状态机）")
    from ui.avatar_remote import AvatarPool, cache_key

    check("   cache_key 优先用 UUID",
          cache_key("Notch", "069a79f4-44e9-4726-a5be-fca90e38aaf5"),
          "069a79f444e94726a5befca90e38aaf5")
    check("   没 UUID 就用名字", cache_key("jeb_", ""), "jeb_")
    check("   空名字空 UUID", cache_key("", ""), "")

    pool = AvatarPool.instance()
    got = []
    pool.ready.connect(lambda k, d: got.append(k))
    # 塞一个假的"已下载"进内存，再请求它 → 应该立刻回调，不进线程池
    pool._memory["faketest"] = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    pool.request("faketest", "", 40)
    pump(0.2)
    check("   内存命中直接回调", got, ["faketest"])
    check("   没被塞进在途表", "faketest" in pool._inflight, False)

    print(f"\n>>> {'全部通过' if not fails else '失败: ' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
