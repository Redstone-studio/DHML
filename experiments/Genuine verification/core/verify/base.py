"""
验证方式的公共接口

**为什么要抽这层**：正版验证有好几种做法，它们的"能得出什么结论"完全不一样。
统一成一个接口之后，界面只认结果，不关心背后是玩家列表、OAuth 还是别的。

    · player_list  玩家列表（古法）—— 你进服，我查在线名单里有没有你
    · microsoft    OAuth 登录 —— 直连微软，拿 access_token（还没做）
    · thirdparty   authlib-injector 第三方验证（还没做）

以后加一种验证方式 = 新写一个 Verifier 子类 + 在 __init__.py 注册，
界面一行都不用改。
"""
from dataclasses import dataclass, field


# ---------- 结果状态 ----------

#: 在名单里 —— 通过
STATE_VERIFIED = "verified"
#: 拿到了**完整**名单，里面没有你 —— 没通过
STATE_ABSENT = "absent"
#: 只拿到**抽样**名单，里面没有你 —— **不能判定**
#:
#: ⚠️ 这个状态是这套方案的关键。SLP 协议最多只给 12 个玩家名
#: （原版就是这么实现的），服务器人多的时候你本来就可能不在抽样里。
#: 把"没抽到"当成"不是正版"，会冤枉一大批人。
STATE_INCONCLUSIVE = "inconclusive"
#: 查询本身失败（连不上、超时、协议不对）
STATE_ERROR = "error"

#: 状态的严重程度，界面据此上色（数字越大越"红"）
SEVERITY = {
    STATE_VERIFIED: 0,
    STATE_INCONCLUSIVE: 1,
    STATE_ABSENT: 2,
    STATE_ERROR: 3,
}

STATE_LABEL = {
    STATE_VERIFIED: "通过",
    STATE_ABSENT: "未通过",
    STATE_INCONCLUSIVE: "无法判定",
    STATE_ERROR: "查询失败",
}


@dataclass
class VerifyResult:
    """一次验证的结果

    `message` 是给人看的一句话，**要说清楚"凭什么"**：
    是"名单里有你"，还是"完整名单里没有"，还是"只查到抽样名单"。
    只给一个"失败"是没用的 —— 用户没法判断下一步该干嘛。
    """

    state: str = STATE_ERROR
    message: str = ""
    #: 服务器上的玩家 [{name, uuid, source}]
    players: list = field(default_factory=list)
    #: 查询到的服务器信息（core.verify.mcping.ServerStatus），失败时是 None
    server: object = None
    #: 实际连的地址（解析过 SRV 之后的），要显示出来 ——
    #: 用户填 play.example.com，实际连的是 node3.example.com:25566，
    #: 不显示的话"连不上"根本没法排查
    host: str = ""
    port: int = 0
    #: 查询耗时（秒）
    elapsed: float = 0.0
    #: 请求令牌。由后台线程填，界面拿它核对"这个结果是不是我这次要的" ——
    #: 用户连点两次时，先发出去的那次可能后回来，不核对就会覆盖掉新结果。
    #:
    #: ⚠️ 放在数据结构里而不是让 worker 临时挂一个属性：临时挂的话
    #: `VerifyResult(..., token=1)` 这种写法会直接 TypeError，
    #: 而"能构造出来"对测试很重要。
    token: int = 0

    @property
    def ok(self) -> bool:
        return self.state == STATE_VERIFIED

    @property
    def label(self) -> str:
        return STATE_LABEL.get(self.state, self.state)

    @property
    def severity(self) -> int:
        return SEVERITY.get(self.state, 9)


class Verifier:
    """一种验证方式的接口

    子类要实现 `key` / `title` / `hint` / `verify()`。
    **`verify()` 会联网，必须在后台线程里调**（见 ui/workers/verify_worker.py）。
    """

    key = ""
    title = ""
    hint = ""
    #: 这个方法需不需要用户提供一个"验证服务器地址"
    needs_server = False
    #: 这个方法需不需要用户提供玩家名
    needs_player_name = False
    #: 需不需要密码。**可选**的意思是"填了就多做一步"——
    #: 比如外置登录：不填只查角色存不存在，填了就真登录一次
    needs_password = False
    #: 需不需要填 Azure 的客户端 ID（微软登录要，别人不要）
    needs_client_id = False
    #: 是不是**交互式**的
    #:
    #: 前两种方式都是"填完点一下 → 一次查询 → 出结果"，一个 `verify()` 就完了。
    #: 但微软登录是"开始 → 显示一串码 → 等用户在浏览器里输 → 轮询 → 完成"，
    #: **中间的等待时间由用户决定**，塞不进一次调用里。
    #: 界面看到这个标记就换成那套专门的交互流程（见 ui/widgets/device_login.py）。
    interactive = False
    #: 还没实现的，界面上要禁用掉（别让用户点了没反应）
    implemented = True

    def verify(self, ctx: dict) -> VerifyResult:
        raise NotImplementedError

    def __repr__(self):
        return f"<Verifier {self.key}>"
