#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档数字一致性校验：让机器守住文档里的行数 / 模块数 / 测试项数。

为什么需要它：
    项目主张「数据不自洽就是错的」，但自己的 README 里曾同时写着
    「16 项」和「17 项」，mdca_inverter.py 写 900 行而实际 1250 行。
    手写的数字一定会漂，所以让脚本在每次跑测试时抓它。

检查三类：
    ① 文档里的 `<file>.py  N 行` 与 `<file>.py | ~N |` 是否等于实际行数
    ② 文档里的「N 个 mdca 模块」是否等于实际 mdca_*.py 数
    ③ 文档里的「run_tests.ps1 N 项」与脚本实际 Step 数、各 Step 标签里的条数

用法：
    python doc_consistency.py           # 报告差异，有差异则退出码 1
    python doc_consistency.py --fix-hint # 只打印该怎么改
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

HERE = Path(__file__).resolve().parent

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 要校验的仓库内公开文档
DOCS = ["README.md", "DESIGN.md"]

# 参与行数校验的 .py（其余脚本不写行数）
LINE_CHECKED = {
    "mdca/cli.py", "mdca/inverter.py", "mdca/palette.py", "mdca/schema.py",
    "mdca/provenance.py", "mdca/grading.py", "mdca/plugins.py",
    "mdca/plugin_thermal.py", "mdca/plugin_scaling.py", "mdca/thermal.py",
    "mdca/scaling.py", "mdca/checks.py", "mdca/review.py", "mdca/anatomy.py",
    "road_a_backtest.py", "baseline_b_road.py",
}

# 行数容差：文档写整数，实际按行数统计，允许 ±0
TOL = 0


def count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def actual_lines() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for name in LINE_CHECKED:
        p = HERE / name
        if p.exists():
            out[name] = count_lines(p)
    return out


# 两种写法：
#   "  mdca.py              1200 行  编排（...）"
#   "| `mdca_inverter.py` | ~900 | A 路核心..."
PAT_A = re.compile(r"([A-Za-z0-9_]+\.py)\s+(~?\d+)\s*行")
PAT_B = re.compile(r"\|\s*`?([A-Za-z0-9_]+\.py)`?\s*\|\s*~?(\d+)\s*\|")


def check_line_claims() -> List[Tuple[str, int, str, int, int]]:
    """返回 (文档, 行号, 文件名, 文档值, 实际值)。"""
    actual = actual_lines()
    bad: List[Tuple[str, int, str, int, int]] = []
    for doc in DOCS:
        p = HERE / doc
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for pat in (PAT_A, PAT_B):
                for m in pat.finditer(line):
                    name, claimed = m.group(1), int(m.group(2))
                    if name not in actual:
                        continue
                    if abs(claimed - actual[name]) > TOL:
                        bad.append((doc, i, name, claimed, actual[name]))
    return bad


def check_module_count() -> List[Tuple[str, int, int, int]]:
    """「N 个 mdca 模块」vs 实际 mdca_*.py 数量。"""
    real = len(list(HERE.glob("mdca*.py")))
    pat = re.compile(r"(\d+)\s*个\s*mdca\s*模块")
    bad: List[Tuple[str, int, int, int]] = []
    for doc in DOCS:
        p = HERE / doc
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in pat.finditer(line):
                claimed = int(m.group(1))
                if claimed != real:
                    bad.append((doc, i, claimed, real))
    return bad


def check_test_steps() -> List[Tuple[str, str, str, str]]:
    """「run_tests.ps1 N 项」vs 实际 Step 数。"""
    script = HERE / "run_tests.ps1"
    if not script.exists():
        return []
    text = script.read_text(encoding="utf-8")
    real = len(re.findall(r"^Step\s+", text, flags=re.M))

    pat = re.compile(r"run_tests\.ps1\s*(?:#.*?)?(\d+)\s*项")
    bad: List[Tuple[str, str, str, str]] = []
    for doc in DOCS:
        p = HERE / doc
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in pat.finditer(line):
                claimed = m.group(1)
                if int(claimed) != real:
                    bad.append((doc, f"{i}", claimed, str(real)))
    # 表格里单独写「run_tests.ps1   N 项一键自测」也算
    return bad


def apply_fix(bad_lines, bad_mod, bad_steps) -> int:
    """按行号精确替换文档里的错误数字，并同步「约 N 行」的总量。"""
    from collections import defaultdict
    by_doc: Dict[str, list] = defaultdict(list)
    for doc, ln, name, claimed, real in bad_lines:
        by_doc[doc].append((ln, name, claimed, real))

    for doc, items in by_doc.items():
        p = HERE / doc
        lines = p.read_text(encoding="utf-8").splitlines()
        for ln, name, claimed, real in items:
            s = lines[ln - 1]
            before = s
            s = re.sub(rf"({re.escape(name)}\s+)(~?)({claimed})(\s*行)",
                       rf"\g<1>\g<2>{real}\g<4>", s, count=1)
            if s == before:
                s = re.sub(rf"(\|\s*`?{re.escape(name)}`?\s*\|\s*)~?{claimed}(\s*\|)",
                           rf"\g<1>{real}\g<2>", s, count=1)
            if s == before:
                print(f"  [跳过] {doc}:{ln} 无法定位 {name} 的数字")
                continue
            lines[ln - 1] = s
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 模块数
    real_mod = len(list(HERE.glob("mdca*.py")))
    total_lines = sum(count_lines(HERE / n) for n in LINE_CHECKED if (HERE / n).exists())
    for doc, ln, claimed, real in bad_mod:
        p = HERE / doc
        lines = p.read_text(encoding="utf-8").splitlines()
        s = lines[ln - 1]
        s = re.sub(rf"(\d+)(\s*个\s*mdca\s*模块)", rf"{real_mod}\g<2>", s, count=1)
        # 同一行里的总量
        s = re.sub(r"约\s*\d+\s*行", f"约 {total_lines} 行", s, count=1)
        lines[ln - 1] = s
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 测试项数
    real_steps = len(re.findall(r"^Step\s+", (HERE / "run_tests.ps1").read_text(encoding="utf-8"),
                                flags=re.M))
    for doc, ln, claimed, real in bad_steps:
        p = HERE / doc
        lines = p.read_text(encoding="utf-8").splitlines()
        i = int(ln) - 1
        lines[i] = re.sub(rf"{claimed}(\s*项)", rf"{real_steps}\g<1>", lines[i], count=1)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n已修：行数 {len(bad_lines)} 处、模块数 {len(bad_mod)} 处、测试项数 {len(bad_steps)} 处")
    return 0


def main(argv: List[str]) -> int:
    hint = "--fix-hint" in argv
    problems = 0

    print("=" * 78)
    print("文档数字一致性校验")
    print("=" * 78)

    bad_lines = check_line_claims()
    if bad_lines:
        problems += len(bad_lines)
        print(f"\n① 行数不符（{len(bad_lines)} 处）")
        for doc, ln, name, claimed, real in bad_lines:
            print(f"  {doc}:{ln}  {name}  文档 {claimed} → 实际 {real}")
    else:
        print("\n① 行数：全部一致")

    bad_mod = check_module_count()
    if bad_mod:
        problems += len(bad_mod)
        print(f"\n② 模块数不符（{len(bad_mod)} 处）")
        for doc, ln, claimed, real in bad_mod:
            print(f"  {doc}:{ln}  文档 {claimed} 个 → 实际 {real} 个")
    else:
        print("② 模块数：一致")

    bad_steps = check_test_steps()
    if bad_steps:
        problems += len(bad_steps)
        print(f"\n③ 测试项数不符（{len(bad_steps)} 处）")
        for doc, ln, claimed, real in bad_steps:
            print(f"  {doc}:{ln}  文档 {claimed} 项 → 实际 {real} 项")
    else:
        print("③ 测试项数：一致")

    print()
    if problems:
        if "--fix" in argv:
            apply_fix(bad_lines, bad_mod, bad_steps)
            print("已写入。建议重跑一次确认，并 git diff 复核。")
            return 0
        print(f"共 {problems} 处不一致。按上面的「实际值」改文档，或加 --fix 自动改。")
        return 1
    print("全部一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
