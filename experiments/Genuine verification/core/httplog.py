"""
带日志的 HTTP 调用

## 为什么要有这一层

这个项目里所有难查的问题都是同一类：**点下去没反应 / 报了个看不懂的错**，
而根因藏在 HTTP 层。所以每一次请求都必须留下痕迹 —— 打到哪个端点、
发了什么、服务端**原样**回了什么。

如果各个模块自己写 `requests.get(...)`，那就一定会有几条漏记 ——
而"出问题的那条恰好没记上"是最气人的。所以统一从 `call()` 走。

## 两条规矩

**① 日志先过 `core.logbook.redact()`。** 令牌、密码、设备码、
`Authorization` 头都会被遮成 `***`。这一层不负责脱敏（那是 logbook 的事），
但**必须把请求体原样交给它**，不能自己先挑挑拣拣。

**② 异常原样抛出去。** 不要在这里转成各家的自定义异常 ——
调用点要用自己的步骤名（"XSTS"/"OAuth2 设备码"…）包装，
这里吞了的话步骤名就丢了，界面上的日志也就没了"卡在哪一步"这个关键信息。
"""
import time

import requests

from core import logbook


#: 响应头里**值得记进日志**的几个
#:
#: ⚠️ 这不是可有可无的：authlib-injector 的自动识别靠的就是
#: `X-Authlib-Injector-API-Location` 这个**响应头**，它不在响应体里。
#: 只记 body 的话，"自动识别失败"在日志里会是一片空白，根本没法查。
INTERESTING_HEADERS = (
    "x-authlib-injector-api-location",
    "content-type",
    "www-authenticate",
    "retry-after",
)


def call(step: str, method: str, url: str, timeout: float, **kwargs):
    """发一个请求并记日志，返回 `requests.Response`

    `step` 是给用户看的步骤名（"XSTS"、"OAuth2 设备码"、"查档案"…）。
    """
    started = time.monotonic()
    try:
        response = requests.request(method, url, timeout=timeout, **kwargs)
    except requests.RequestException as e:
        logbook.http(step, method, url, error=f"{type(e).__name__}: {e}",
                     request=kwargs, elapsed=time.monotonic() - started)
        raise

    # 响应体尽量给 JSON（好读），不是 JSON 就给前 2000 字符的原文本
    body = None
    try:
        body = response.json()
    except ValueError:
        text = response.text or ""
        body = text[:2000] if text else None

    extra = " · ".join(
        f"{key}: {value}" for key, value in response.headers.items()
        if key.lower() in INTERESTING_HEADERS
    )
    logbook.http(step, method, url, status=response.status_code,
                 request=kwargs, response=body, extra=extra,
                 elapsed=time.monotonic() - started)
    return response
