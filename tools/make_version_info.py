# -*- coding: utf-8 -*-
"""生成 PyInstaller 用的 Windows 版本资源文件

exe 上「右键 → 属性 → 详细信息」里显示的文件说明 / 产品名称 / 版本号、
以及任务栏悬浮、部分杀软和安装器读到的版本，全都来自这个文件。
PyInstaller 用 `--version-file <file>` 指定它，文件内容是**一个 Python 表达式**
（`VSVersionInfo(...)`，由 PyInstaller 自己 eval），不是 JSON。

为什么用脚本生成而不是往仓库里放一份写死的：
版本号只有一个来源（`core/app_info.py`），写死的那种"发版时忘了改"迟早发生 ——
exe 属性里显示上一个版本号，比不显示还糟。

用法：
    python tools/make_version_info.py version_info.txt
    python tools/make_version_info.py version_info.txt --version v0.5.8
    python tools/make_version_info.py version_info.txt --exe-name Mosslight-Launcher

发版时 workflow 会用 `--version <tag>` 传**发版输入的那个版本号**，
这样 exe 属性里显示的就是 GitHub 上那个 tag，两个地方对得上。
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.app_info import APP_DISPLAY_NAME, APP_VERSION  # noqa: E402

REPO_URL = "https://github.com/kongxia114/Mosslight-Launcher"

# 资源里的语言：0409 = 英语(美国)，1200 = Unicode。Windows 的属性对话框
# 标签是系统语言，这里选哪个只影响"以谁的名义"存这份字符串表，选英语最通用。
LANG_CODE = "040904B0"
LANG_ID = 0x0409
CHARSET_ID = 1200

TEMPLATE = """\
# 由 tools/make_version_info.py 生成，别手改（发版时 workflow 会重新生成）
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers},
    prodvers={vers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '{lang_code}',
        [StringStruct('CompanyName', '{company}'),
         StringStruct('FileDescription', '{description}'),
         StringStruct('FileVersion', '{file_version}'),
         StringStruct('InternalName', '{internal}'),
         StringStruct('LegalCopyright', '{copyright}'),
         StringStruct('OriginalFilename', '{filename}'),
         StringStruct('ProductName', '{product}'),
         StringStruct('ProductVersion', '{product_version}'),
         StringStruct('Comments', '{comments}')])
    ]),
    VarFileInfo([VarStruct('Translation', [{lang}, {charset}])])
  ]
)
"""


def version_tuple(version: str) -> tuple:
    """'v0.5.8-dev' → (0, 5, 8, 0)

    Windows 的 FixedFileInfo 要 4 个整数，多退少补。
    连一个数字都没有（版本号写坏了）就给 (0,0,0,0) —— 让构建继续跑，
    属性里显示 0.0.0.0 也好过整个发版挂掉。
    """
    head = str(version).split("-")[0]
    nums = [int(n) for n in re.findall(r"\d+", head)][:4]
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums)


def render(version: str, exe_name: str) -> str:
    nums = version_tuple(version)
    return TEMPLATE.format(
        vers="(%d, %d, %d, %d)" % nums,
        lang_code=LANG_CODE,
        lang=LANG_ID,
        charset=CHARSET_ID,
        company="Mosslight",
        description=APP_DISPLAY_NAME,
        # 文件版本用纯数字（有些工具会解析它比较大小），
        # 产品版本原样用 tag，好跟 GitHub 上的 Release 对上
        file_version="%d.%d.%d.%d" % nums,
        internal=exe_name,
        copyright="© %d Mosslight · GPL-3.0" % date.today().year,
        filename=exe_name + ".exe",
        product=APP_DISPLAY_NAME,
        product_version=version,
        comments=REPO_URL,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Windows 版本资源文件")
    parser.add_argument("output", help="写到哪个文件（一般叫 version_info.txt）")
    parser.add_argument("--version", default=APP_VERSION,
                        help=f"版本号，默认取 core/app_info.py 里的（{APP_VERSION}）")
    parser.add_argument("--exe-name", default="Mosslight-Launcher",
                        help="exe 文件名（不含 .exe），要跟 pyinstaller --name 一致")
    args = parser.parse_args()

    text = render(args.version, args.exe_name)
    Path(args.output).write_text(text, encoding="utf-8", newline="\n")
    print(f"已写入 {args.output}：{args.exe_name}.exe，"
          f"文件版本 {'.'.join(str(n) for n in version_tuple(args.version))}，"
          f"产品版本 {args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
