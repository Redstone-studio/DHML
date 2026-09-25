"""
微软登录验证器（正版）

## 为什么它是"交互式"的

另外两种验证方式都是"填完点一下、一次查询、出结果"，一个 `verify()` 就完了。
微软登录不是：**中间的等待时间由用户在浏览器里决定**（可能半分钟，也可能放着不管）。
所以它的 `interactive = True`，界面会换成专门的流程：

    开始 → 拿到设备码 → 显示码 + 网址 → 用户去输 → 轮询 → 完成

## 这里只做"协调"，协议细节在 core/verify/microsoft.py

这个类负责把六步链路串起来、把每一步的失败翻译成能看懂的话，
并且**在成功之后把账户存下来**（`core/accounts.py`），这样重启之后还在。
"""
import time

from core.accounts import upsert
from core.verify import microsoft as ms
from core.verify.base import (
    STATE_ERROR, STATE_INCONCLUSIVE, STATE_VERIFIED, Verifier, VerifyResult,
)


class MicrosoftVerifier(Verifier):
    key = "microsoft"
    title = "微软登录（正版）"
    hint = ("直连微软拿令牌，走完整六步（OAuth → Xbox Live → XSTS → Minecraft）。\n"
            "⚠️ 必须先有自己的 Azure 应用，而且要申请 Minecraft API 权限 —— "
            "详见 README 的「微软登录怎么开通」。")
    needs_client_id = True
    interactive = True
    implemented = True

    def __init__(self, endpoints=None):
        """`endpoints` 可以换成指向本地假服务器的地址

        ⚠️ 不注入的话整条链路**没法离线测** —— 测试会真的去打微软，
        拿到"device_code 无效"之类的真错误，测出来的是网络不是逻辑。
        （第一次写的时候没留这个口子，自测直接打到真端点上了。）
        """
        self.endpoints = endpoints or ms.DEFAULT_ENDPOINTS

    def verify(self, ctx: dict) -> VerifyResult:
        """交互式的验证器不通过这个方法跑 —— 界面走 device_login 那套

        留个实现是为了**契约完整**：万一以后有人按统一接口调它，
        得到的是一句明确的话，而不是 NotImplementedError。
        """
        client_id = (ctx.get("client_id") or "").strip()
        if not client_id:
            return VerifyResult(STATE_ERROR, "还没填 Azure 客户端的 ID")
        return VerifyResult(
            STATE_ERROR,
            "微软登录是交互式的，不能这样一次跑完。\n"
            "（界面走「开始登录」那套：先给设备码，等你授权完再继续。）")

    # ---------- 给界面用的那套 ----------

    def begin(self, client_id: str, timeout: float = ms.TIMEOUT) -> dict:
        """第一步：申请设备码。返回给用户看的东西（含 user_code）"""
        return ms.request_device_code(client_id, self.endpoints, timeout=timeout)

    def poll_once(self, client_id: str, device_code: str,
                  timeout: float = ms.TIMEOUT) -> dict:
        """轮询一次。

        · 还没授权 → 抛 `ms.AuthorizationPending`（**不是失败**）
        · 授权完了 → 直接**走完后面五步并落盘**，返回 (账户, VerifyResult)

        ⚠️ 有一种情况**不是错误，是"判不了"**：前三步（微软登录、Xbox Live、
        XSTS）都过了，但第 4 步因为 Azure 应用没拿到 Minecraft API 权限而 403。
        这时候微软账号**是真的登录成功了**，只是拿不到 Minecraft 档案。
        当成"失败"报出去，会让人以为是账号或者代码的问题
        —— 而实际上卡的是**应用审批**。所以单独识别成 `inconclusive`。
        """
        oauth = ms.poll_device_code(client_id, device_code, self.endpoints,
                                    timeout=timeout)

        try:
            account = ms.complete_login(client_id, oauth, self.endpoints,
                                        timeout=timeout)
        except ms.MicrosoftAuthError as e:
            if e.code == "INVALID_APP_REGISTRATION":
                return None, VerifyResult(
                    STATE_INCONCLUSIVE,
                    "微软账号**登录成功了**，Xbox Live 也通过了 ——\n"
                    "但这只证明你拥有一个微软账号，**证明不了拥有 Minecraft**。\n"
                    "卡在第 4 步：这个 Azure 应用没有 Minecraft API 的权限。\n"
                    "这是**应用审批**的问题，不是你的账号或代码有问题。",
                    elapsed=0.0)
            raise

        saved = upsert(account)
        saved_note = "" if saved else "\n⚠️ 账户**没能写进本地文件**，这次只在内存里有效。"

        return account, VerifyResult(
            STATE_VERIFIED,
            f"登录成功：{account['name']}\n"
            f"UUID {account['uuid']}\n"
            f"⚠️ 这次**确实是正版验证** —— 第 5 步查过了，这个账号拥有 "
            f"Minecraft: Java Edition。\n"
            f"账户已经存到本地，重启之后还能用。{saved_note}",
            players=[{"name": account["name"], "uuid": account["uuid"],
                      "source": "microsoft", "skin_url": account.get("skin_url", "")}],
            elapsed=0.0)

    def refresh_account(self, account: dict, timeout: float = ms.TIMEOUT) -> dict:
        """用 refresh_token 续期（access_token 过期时用）

        ⚠️ 微软的 refresh_token 会**轮换**，换来的必须立刻存回去 ——
        所以这里拿到新令牌后马上 `upsert`。
        """
        client_id = str(account.get("client_id") or "").strip()
        refresh_token = str(account.get("refresh_token") or "").strip()
        if not client_id or not refresh_token:
            raise ms.MicrosoftAuthError("刷新", "这个账户没有 refresh_token，得重新登录")

        oauth = ms.refresh_oauth(client_id, refresh_token, self.endpoints,
                                 timeout=timeout)
        updated = ms.complete_login(client_id, oauth, self.endpoints, timeout=timeout)
        # complete_login 只带 refresh_token（新的那个），把旧的资料补回去
        updated["refresh_token"] = oauth.get("refresh_token") or refresh_token
        updated["added_at"] = account.get("added_at") or time.time()
        upsert(updated)
        return updated
