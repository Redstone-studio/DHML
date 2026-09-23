"""多语言支持

设计取舍 —— 为什么用"源文案即 key"，而不是 `tr("home.launch")` 这种语义 key：

1. 代码里直接看得见中文，review 和写代码都方便（两个 AI 协作时尤其重要）
2. 中文是**源语言**，所以漏翻 / key 打错都只会退回中文显示，
   永远不会把 `home.launch` 或 `#lang_1#` 这种东西糊到用户脸上
3. 提取可以脚本化（tools/extract_strings.py），不用手工给 133 条起名字

代价：以后改中文措辞时，那一条的英文翻译会失效（退回中文）。
因为现在还没有英文翻译，这个代价此刻是零。

这个模块是**纯 Python，不 import PyQt6** —— 所以 core/ 里也能直接调 tr()，
不违反"core 不依赖界面"的约定。
"""

import json
import locale
import os
import sys

from core.config import config
from core.resources import resource_path

FALLBACK_LANG = "zh_CN"
LANG_DIR = ("assets", "lang")

# 语言代码 → 显示名（设置页下拉框用）。没列到的语言直接显示代码本身。
# 语言名按惯例用**该语言自己**写，所以这里的中文不需要翻译。
# 这个字典的顺序就是下拉框里的顺序。
LANGUAGE_NAMES = {
    "zh_CN": "简体中文",   # noqa: i18n
    "zh_TW": "繁體中文",   # noqa: i18n
    "en_US": "English",
    "ja_JP": "日本語",     # noqa: i18n
    "ru_RU": "Русский",
}

_current_lang = FALLBACK_LANG
_strings = {}


# ---------- 语言文件 ----------

def _lang_path(lang: str):
    return resource_path(*LANG_DIR, f"{lang}.json")


def load_language(lang: str) -> dict:
    """读某个语言的词典；读不到就返回空表（于是全部退回中文）"""
    try:
        # 读字节而不是 read_text：json 模块自己能识别 BOM 和 UTF-16/32，
        # 而 encoding="utf-8" 碰到带 BOM 的文件会在第一个字符就报错
        # （记事本、某些编辑器的"UTF-8 with BOM"就会这样）
        data = json.loads(_lang_path(lang).read_bytes())
    except OSError as e:
        print(f"[i18n] 语言文件读不到: {_lang_path(lang)} ({e})")
        return {}
    except ValueError as e:
        print(f"[i18n] 语言文件格式错误: {_lang_path(lang)} ({e})")
        return {}
    return data if isinstance(data, dict) else {}


def available_languages() -> "list[str]":
    """assets/lang/ 下实际存在的语言，按 LANGUAGE_NAMES 的顺序排"""
    try:
        found = {p.stem for p in resource_path(*LANG_DIR).glob("*.json")}
    except OSError:
        found = set()
    found.add(FALLBACK_LANG)

    known = [code for code in LANGUAGE_NAMES if code in found]
    unknown = sorted(found - set(LANGUAGE_NAMES))
    return known + unknown


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


# ---------- 切换与取值 ----------


# ============================================================
# 系统语言
# ============================================================

def _normalize_locale(tag: str) -> str:
    """把各种写法的语言标记归到我们支持的那几种

    输入可能是 "zh-CN"（BCP-47）、"zh_CN"（POSIX）、
    "Chinese (Simplified)_China"（Windows 的老写法）……
    """
    if not tag:
        return ""
    raw = tag.replace("-", "_")
    parts = [x for x in raw.split("_") if x]
    if not parts:
        return ""
    lang = parts[0].lower()
    region = parts[1].upper() if len(parts) > 1 else ""

    if lang == "zh" or "chinese" in lang:
        # 中文必须分简繁，不然台湾用户会看到简体
        if region in ("TW", "HK", "MO") or "HANT" in raw.upper() or "Traditional" in tag:
            return "zh_TW"
        return "zh_CN"

    for prefix, code in (("en", "en_US"), ("ja", "ja_JP"), ("ru", "ru_RU"),
                         ("japanese", "ja_JP"), ("russian", "ru_RU"),
                         ("english", "en_US")):
        if lang.startswith(prefix):
            return code

    # 不支持的语言退回英文 —— 总比让外国用户看到中文强
    return "en_US"


def system_locale() -> str:
    """猜系统界面语言，返回我们支持的语言代码

    Windows 上用 GetUserDefaultLocaleName —— 它给的是干净的 BCP-47（"zh-CN"）。
    Python 的 locale 模块在 Windows 上会返回
    "Chinese (Simplified)_China" 这种老写法，而且 getdefaultlocale()
    在 3.15 里已经被标记要删除了，所以只当兜底。
    """
    if sys.platform == "win32":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85) and buf.value:
                got = _normalize_locale(buf.value)
                if got:
                    return got
        except Exception:
            pass

    for tag in (_python_locale(), os.environ.get("LANG", ""),
                os.environ.get("LANGUAGE", "")):
        got = _normalize_locale(tag)
        if got:
            return got
    return "en_US"


def _python_locale() -> str:
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return (locale.getdefaultlocale() or (None, None))[0] or ""
    except Exception:
        return ""


def resolve_language() -> str:
    """启动时决定用哪个语言：配置里存过就用存的，没存过就按系统语言定

    定完顺手写回配置 —— 这样设置页里显示的就是当前实际在用的，
    用户想改也改得掉。
    """
    try:
        saved = config.get("language", "")
    except Exception:
        saved = ""
    if saved:
        return saved

    detected = system_locale()
    try:
        config.set("language", detected)
    except Exception:
        pass        # 写不进去也不影响这次启动
    return detected


def set_language(lang: str):
    """切换语言。注意：这只会换词典，界面上已经建好的控件要自己重设文字 ——
    见 ui/translatable.py 的 TranslatableWidget。"""
    global _current_lang, _strings
    if lang not in available_languages():
        lang = FALLBACK_LANG
    _current_lang = lang
    _strings = load_language(lang)


def current_language() -> str:
    return _current_lang


def tr(text: str, **fmt) -> str:
    """取当前语言的文案

    text 是**中文源文案**。查不到翻译就原样返回中文 ——
    所以调错了、漏翻了，用户看到的也只是中文，不会看到 key。

    带变量的文案要走参数，不要自己拼 f-string：
        tr("已选中 {name}", name=name)
    """
    out = _strings.get(text)
    if out is None:
        out = text
    if fmt:
        try:
            out = out.format(**fmt)
        except (KeyError, IndexError):
            # 译文里的占位符和源文案对不上时，宁可显示原文也不要抛异常
            out = text.format(**fmt)
    return out


# 启动时按配置加载
set_language(resolve_language())
