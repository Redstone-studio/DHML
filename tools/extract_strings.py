"""同步中文文案到 assets/lang/zh_CN.json

用法：
    python tools/extract_strings.py            # 只报告差异
    python tools/extract_strings.py --write    # 报告并补齐 zh_CN.json

它做三件事：

1. **补条目**：代码里出现、但 JSON 里还没有的中文文案，自动补进去
   （value 先填中文本身，等真要翻译时再改）
2. **报废弃**：JSON 里有、但代码里已经没人用的条目
3. **报漏网**：代码里的中文没走文案系统 —— 包括含中文的 f-string。
   f-string 没法自动提取（变量会把文案碎成好几段），必须手工改成
   tr("共 {n} 个版本", n=total) 这种形式。

约定见 ui/translatable.py 的模块说明。
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "assets" / "lang" / "zh_CN.json"

SCAN_DIRS = ("core", "ui")
SCAN_FILES = ("main.py",)

CJK = re.compile(r"[\u4e00-\u9fff]")

# 这些文案**故意不翻译**，别报"漏翻"。
# 语言名按惯例用该语言自己写（中文写"简体中文"、英文写"English"），
# 翻译了反而让用户找不到自己的语言。
NO_TRANSLATE = {"简体中文", "繁體中文", "日本語"}

# 这些调用的字符串参数算"走了文案系统"，不算漏网。
# 后面几个是本项目自己的辅助方法：_card 是设置页建卡片用的，
# 它内部用 self.label() 把标题登记进了文案系统（_field_row 同理，是
# 版本设置页建"字段名 + 控件"那一行用的）。
WRAPPERS = {"tr", "label", "button", "bind", "_card", "_field_row"}


class Collector(ast.NodeVisitor):
    def __init__(self, rel_path: str, lines: "list[str]"):
        self.rel = rel_path
        self._lines = lines
        self.items = []          # (文案, 行号) —— 按出现顺序，已去重
        self._seen = set()
        self.wrapped = set()     # id(node) —— 已走文案系统的字面量
        self.silenced = set()    # id(node) —— 被 `# noqa: i18n` 标记为"不用翻译"
        self.loose = []          # (行号, 文案) —— 没走文案系统的裸字面量
        self.fstrings = []       # (行号, 源码片段) —— 含中文的 f-string
        self._docstrings = set()
        self._in_print = 0
        self._in_fstring = 0

    # ---------- noqa ----------

    def _has_marker(self, node) -> bool:
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start)
        for line in range(start, end + 1):
            if 1 <= line <= len(self._lines) and "noqa: i18n" in self._lines[line - 1]:
                return True
        return False

    def visit_Assign(self, node):
        """整条赋值语句上带 `# noqa: i18n` 时，里面的中文都算"不用翻译"

        因为文案表通常长这样，注释只能写在右括号那行：
            NAV_ITEMS = (
                ("home", "▶", "启动"),
                ...
            )  # noqa: i18n
        这些中文仍然是**源文案**（会在别处被 tr() 消费），
        所以照样要收进词典，只是不再报"漏网"。
        """
        if self._has_marker(node):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant):
                    self.silenced.add(id(sub))
        self.generic_visit(node)

    # ---------- docstring ----------

    def _mark_docstring(self, node):
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            self._docstrings.add(id(body[0].value))

    def visit_Module(self, node):
        self._mark_docstring(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self._mark_docstring(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._mark_docstring(node)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    # ---------- 调用 ----------

    def visit_Call(self, node):
        name = _func_name(node.func)
        if name == "print":
            self._in_print += 1
            self.generic_visit(node)
            self._in_print -= 1
            return
        if name in WRAPPERS:
            for arg in node.args:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Constant):
                        self.wrapped.add(id(sub))
        self.generic_visit(node)

    # ---------- f-string ----------

    def visit_JoinedStr(self, node):
        # print() 里的 f-string 是调试输出，不算文案
        if not self._in_print:
            text = "".join(
                part.value for part in node.values if isinstance(part, ast.Constant)
            )
            if CJK.search(text):
                self.fstrings.append((node.lineno, text.strip()))
        self._in_fstring += 1
        self.generic_visit(node)
        self._in_fstring -= 1

    # ---------- 字符串 ----------

    def visit_Constant(self, node):
        if not isinstance(node.value, str) or not CJK.search(node.value):
            return
        if id(node) in self._docstrings or self._in_print:
            return
        # 注意：tr(...) 嵌在 f-string 里时也要收集（比如 f"...{tr('当前使用')}"），
        # 所以走过文案系统的字面量不能被 _in_fstring 挡住
        if self._in_fstring and id(node) not in self.wrapped:
            return

        text = node.value
        if text not in self._seen:
            self._seen.add(text)
            self.items.append((text, node.lineno))

        if id(node) not in self.wrapped and id(node) not in self.silenced:
            self.loose.append((node.lineno, text))


def _func_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def target_files():
    files = [ROOT / name for name in SCAN_FILES]
    for sub in SCAN_DIRS:
        files.extend(sorted((ROOT / sub).rglob("*.py")))
    return [f for f in files if f.is_file()]


def scan():
    """返回 (全部文案 {文案: 首次出现的相对路径}, 漏网清单)

    行尾带 `# noqa: i18n` 的会跳过 —— 用来标记那些"看起来是中文、
    但确实不该翻译"的行（比如文案表本身，它在别处被 tr() 消费）。
    """
    found = {}
    loose = []
    fstrings = []

    for path in target_files():
        rel = path.relative_to(ROOT).as_posix()
        try:
            source = path.read_bytes().decode("utf-8-sig")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as e:
            print(f"跳过 {rel}: {e}")
            continue

        lines = source.splitlines()

        collector = Collector(rel, lines)
        collector.visit(tree)

        for text, _line in collector.items:
            found.setdefault(text, rel)
        for line, text in collector.loose:
            loose.append((rel, line, text))
        for line, text in collector.fstrings:
            fstrings.append((rel, line, text))

    return found, loose, fstrings


def main() -> int:
    parser = argparse.ArgumentParser(description="同步中文文案到 zh_CN.json")
    parser.add_argument("--write", action="store_true", help="补齐缺失的条目")
    args = parser.parse_args()

    found, loose, fstrings = scan()

    current = {}
    if JSON_PATH.is_file():
        try:
            current = json.loads(JSON_PATH.read_bytes())
        except ValueError as e:
            print(f"zh_CN.json 解析失败: {e}")
            return 1

    missing = [text for text in found if text not in current]
    unused = [key for key in current if key not in found]

    print(f"代码里发现 {len(found)} 条中文文案，JSON 里有 {len(current)} 条")
    print()

    if missing:
        print(f"== JSON 里缺的（{len(missing)}） ==")
        for text in missing:
            print(f"   [{found[text]}]  {text}")
        print()
    if unused:
        print(f"== JSON 里没人用的（{len(unused)}） ==")
        for text in unused:
            print(f"   {text}")
        print()
    if fstrings:
        print(f"== 含中文的 f-string，必须手工改成 tr(...)（{len(fstrings)}） ==")
        for rel, line, text in fstrings:
            print(f"   {rel}:{line}  {text}")
        print()
    if loose:
        print(f"== 没走文案系统的中文字面量（{len(loose)}） ==")
        for rel, line, text in loose:
            print(f"   {rel}:{line}  {text}")
        print()

    # 其它语言文件的两项检查：
    #   - key 在源码里找不到 → 多半是打错了（打错会静默退回中文，很难发现）
    #   - 源码里有、译文里没有 → 漏翻（会退回中文显示）
    for other in sorted(JSON_PATH.parent.glob("*.json")):
        if other == JSON_PATH:
            continue
        try:
            table = json.loads(other.read_bytes())
        except (OSError, ValueError) as e:
            print(f"== {other.name} 读不了: {e} ==")
            continue

        orphans = [key for key in table if key not in found]
        untranslated = [
            key for key in found if key not in table and key not in NO_TRANSLATE
        ]

        if orphans:
            print(f"== {other.name} 里有 {len(orphans)} 个 key 在源码里找不到（打错了？） ==")
            for key in orphans:
                print(f"   {key}")
            print()
        if untranslated:
            print(f"== {other.name} 还差 {len(untranslated)} 条没翻（会退回中文） ==")
            for key in untranslated:
                print(f"   {key}")
            print()

        # 值跟键一模一样 = 值还是中文原文（zh_CN 是 key -> 中文，所以相等就是没翻）。
        # ⚠️ 只**提示**：有些语言里确实同形（繁体的 "OptiFine 各版本"、日语借词），
        # 所以措辞是"可能没翻"，别当错误。
        same_as_source = [
            key for key in table
            if key not in NO_TRANSLATE and table[key] == key
        ]
        if same_as_source:
            print(f"== {other.name} 里有 {len(same_as_source)} 条值跟中文原文一样（可能没翻） ==")
            for key in same_as_source[:15]:
                print(f"   {key}")
            if len(same_as_source) > 15:
                print(f"   …还有 {len(same_as_source) - 15} 条")
            print()

    if args.write and (missing or unused):
        # 以源码为准重建：补上缺的、去掉没人用的，顺序按源码出现顺序
        merged = {text: current.get(text, text) for text in found}
        JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        JSON_PATH.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"已写入 {JSON_PATH.relative_to(ROOT)}（新增 {len(missing)}，移除 {len(unused)}）")

    if not (missing or unused or fstrings or loose):
        print("全部一致，没有需要处理的东西。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
