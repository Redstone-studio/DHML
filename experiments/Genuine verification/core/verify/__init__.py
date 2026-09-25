"""
验证方式注册表

界面上那排"验证方式"就是从这儿来的（顺序即显示顺序）。
加一种新方式：写个 Verifier 子类 → 在 VERIFIERS 里加一行，界面自动支持。
"""
from core.verify.base import (          # noqa: F401  （对外导出，方便调用方 import）
    STATE_ABSENT, STATE_ERROR, STATE_INCONCLUSIVE, STATE_VERIFIED,
    STATE_LABEL, SEVERITY, Verifier, VerifyResult,
)
from core.verify.microsoft_verifier import MicrosoftVerifier
from core.verify.player_list import PlayerListVerifier
from core.verify.thirdparty import ThirdPartyVerifier


#: 显示顺序 = 列表顺序
VERIFIERS = (
    MicrosoftVerifier(),
    ThirdPartyVerifier(),
    PlayerListVerifier(),
)

_BY_KEY = {v.key: v for v in VERIFIERS}


def all_verifiers() -> tuple:
    return VERIFIERS


def implemented_verifiers() -> list:
    return [v for v in VERIFIERS if v.implemented]


def get_verifier(key: str) -> Verifier:
    """按 key 取，取不到就给第一个能用的（别让界面拿到 None 再炸）"""
    verifier = _BY_KEY.get(key)
    if verifier is not None:
        return verifier
    usable = implemented_verifiers()
    return usable[0] if usable else VERIFIERS[0]
