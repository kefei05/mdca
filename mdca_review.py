#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 批量审查外部候选（A 路）
==============================

用途：
    输入：外部给的一批候选（AI / 优化器 / 论文表格）
    输出：每条的自洽性判断 + 矛盾点

mdca_review.py   审查外部候选：没有标签、来源混杂、字段可能不全，产出人可读报告

核心不是「再跑一遍 audit」，而是**把一堆审计结果变成可行动的分类**：

    格式不对      输入本身不合契约（P0 / V1 / V5 / V6 / P1 / P2）→ 让对方按契约重交
    物理不可能    电磁链或热守恒自洽性被证伪（C1~C3 / I1~I4）→ 直接淘汰
    数据没来源    未验证且未声明保守方向（V2）→ 找来源，不是找错
    待人工确认    超域 / 外推 / 基准豁免（REVIEW）→ 人看一眼
    全干净        AUDIT_PASS → 可以进下一轮

用法：
    python mdca_review.py cases_ai/                  # 控制台报告 + out/review.json
    python mdca_review.py cases_ai/ --md out/r.md    # 额外写 Markdown 报告
    python mdca_review.py cases/ cases_ai/ --plugins thermal,scaling
    python mdca_review.py 某个候选.jsonl             # 单文件（支持 JSONL）
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from mdca import audit, load_case, parse_case_doc  # noqa: E402
from mdca_grading import grade, load_config  # noqa: E402
from mdca_plugins import PluginError, split_plugin_args  # noqa: E402
from mdca_schema import read_jsonl  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT_DIR = HERE / "out"

# 自洽性判据的码前缀：命中即「物理上不可能」，不是「格式问题」也不是「缺来源」
SELF_CONSISTENCY_PREFIXES = ("C1_", "C2_", "C3_", "I1_", "I2_", "I3_", "I4_")

# 分类标签与显示名
CLASSES: List[Tuple[str, str]] = [
    ("physically_impossible", "物理不可能"),
    ("format_error", "格式不对"),
    ("unverifiable", "数据没来源"),
    ("needs_review", "待人工确认"),
    ("clean", "全干净"),
    ("error", "读取或审计失败"),
]
CLASS_LABEL = dict(CLASSES)


# --------------------------------------------------------------------------
# 收集
# --------------------------------------------------------------------------


def collect_cases(paths: Sequence[str]) -> Tuple[List[Tuple[str, Any]], List[Dict[str, str]]]:
    """把目录 / 单文件 / JSON 数组 展开成 [(来源标签, Case 或失败记录)]。"""
    found: List[Tuple[str, Any]] = []
    failures: List[Dict[str, str]] = []

    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            files = sorted(list(pp.glob("*.json")) + list(pp.glob("*.jsonl")))
        elif pp.exists():
            files = [pp]
        else:
            failures.append({"source": p, "reason": "路径不存在"})
            continue

        for f in files:
            if f.suffix == ".jsonl":
                for ln, doc, err in read_jsonl(f):
                    label = f"{f.name}:{ln}"
                    if err:
                        failures.append({"source": label, "reason": f"JSONL 解析失败 {err}"})
                        continue
                    try:
                        found.append((label, parse_case_doc(doc, label)))
                    except Exception as e:  # noqa: BLE001
                        failures.append({"source": label,
                                         "reason": f"{type(e).__name__}: {e}"})
                continue

            try:
                raw = json.loads(f.read_text(encoding="utf-8-sig"))
            except Exception as e:  # noqa: BLE001
                failures.append({"source": f.name, "reason": f"JSON 解析失败：{e}"})
                continue

            # 一个 .json 里装多条：顶层是数组，或 {"cases": [...]}
            if isinstance(raw, list):
                for i, doc in enumerate(raw):
                    label = f"{f.name}#{i}"
                    try:
                        found.append((label, parse_case_doc(doc, label)))
                    except Exception as e:  # noqa: BLE001
                        failures.append({"source": label,
                                         "reason": f"{type(e).__name__}: {e}"})
            elif isinstance(raw, dict) and isinstance(raw.get("cases"), list):
                for i, doc in enumerate(raw["cases"]):
                    label = f"{f.name}#{i}"
                    try:
                        found.append((label, parse_case_doc(doc, label)))
                    except Exception as e:  # noqa: BLE001
                        failures.append({"source": label,
                                         "reason": f"{type(e).__name__}: {e}"})
            else:
                try:
                    found.append((f.name, load_case(f)))
                except Exception as e:  # noqa: BLE001
                    failures.append({"source": f.name,
                                     "reason": f"{type(e).__name__}: {e}"})
    return found, failures


# --------------------------------------------------------------------------
# 单条审查
# --------------------------------------------------------------------------


def classify(block_codes: Iterable[str], review_codes: Iterable[str],
             status: str) -> str:
    """把一条候选归入五类之一。顺序即优先级。

    优先级的设计依据：
        物理不可能 > 格式不对 > 数据没来源 > 待人工确认 > 全干净
    理由：「物理上不成立」是终局，「连比较都做不了（格式/口径）」比
    「数据能用但不知真伪」更根本——前者要先修，后者才轮到找来源。
    所以只要存在任何非 V2 的 BLOCK，就不能归入「数据没来源」。
    """
    blocks = [c for c in block_codes if c]
    if any(c.startswith(SELF_CONSISTENCY_PREFIXES) for c in blocks):
        return "physically_impossible"
    if any(not c.startswith("V2_") for c in blocks):
        return "format_error"
    if blocks:                      # 剩下的全是 V2_*：数据能用，只是没来源
        return "unverifiable"
    if status == "AUDIT_PASS":
        return "clean"
    if review_codes or status in ("AUDIT_REVIEW", "MODEL_REVIEW", "SOLVER_UNKNOWN"):
        return "needs_review"
    return "needs_review"


def review_one(label: str, case: Any, cfg: Dict[str, Any],
               plugins: Optional[str]) -> Dict[str, Any]:
    o = audit(case, plugins=plugins)
    issues = (o.get("validation") or {}).get("issues") or []
    blocks = sorted({i["code"] for i in issues if i.get("severity") == "BLOCK"})
    reviews = sorted({i["code"] for i in issues if i.get("severity") == "REVIEW"})
    status = str((o.get("audit") or {}).get("overall_status"))

    # 指纹：判据可能把指纹放在 detail.fingerprint，也可能放在 Tw_fingerprint /
    # Tr_fingerprint 这类分节点字段里（I1 就是这样）。两种都要抓，否则
    # 「错在哪一步」这个最有价值的信息会在汇总层丢掉。
    fingerprints: List[str] = []
    for i in issues:
        det = i.get("detail") or {}
        code = i.get("code", "")
        got: List[str] = []
        if det.get("fingerprint"):
            got.append(str(det["fingerprint"]))
        for k, v in det.items():
            if k.endswith("_fingerprint") and v:
                node = k[: -len("_fingerprint")]
                item = f"{node}={v}"
                if item not in got:
                    got.append(item)
        if got:
            fingerprints.append(f"{code}[{', '.join(got)}]")

    g = grade(o, cfg)
    cls = classify(blocks, reviews, status)
    active = list((o.get("plugins") or {}).get("active") or [])

    # 盲区提示：A 路看不见的东西，必须在报告里说出来，
    # 否则「全干净」会被读成「没问题」，而实际只是「没审」。
    warns: List[str] = []
    if getattr(case, "scaling", None) and "scaling" not in active:
        warns.append("有 scaling 段但缩放插件未启用：几何外推未审，结论可能偏乐观")
    if getattr(case, "operating_points", None) and "I3" not in " ".join(
            (o.get("consistency") or {}).get("checks_run") or []):
        warns.append("有 operating_points 但多工况判据未运行")
    cov = (o.get("consistency") or {}).get("coverage") or {}
    if cls == "clean" and cov.get("audited_count", 0) == 0:
        warns.append(f"AUDIT_PASS 但自洽性判据 0/{cov.get('total', '?')} 条被触发"
                     f"（字段不足），该 PASS 只覆盖口径层")
    elif cls == "clean" and cov.get("skipped_count", 0) > 0:
        warns.append(f"AUDIT_PASS 但仍有 {cov['skipped_count']} 条判据因缺字段未审")

    # 一句可读的主要矛盾
    why = "—"
    if fingerprints:
        why = fingerprints[0]
    elif blocks:
        why = blocks[0]
    elif reviews:
        why = reviews[0]

    return {
        "source": label,
        "case_id": getattr(case, "case_id", label),
        "source_type": getattr(case, "source_type", "") or "(未声明)",
        "status": status,
        "tier": g["tier"],
        "tier_label": g["tier_label"],
        "rule": g["rule"],
        "class": cls,
        "class_label": CLASS_LABEL[cls],
        "block_codes": blocks,
        "review_codes": reviews,
        "fingerprints": fingerprints,
        "why": why,
        "plugins": active,
        "checks_run": list((o.get("consistency") or {}).get("checks_run") or []),
        "warnings": warns,
    }


# --------------------------------------------------------------------------
# 聚合
# --------------------------------------------------------------------------


def aggregate(rows: List[Dict[str, Any]], failures: List[Dict[str, str]]) -> Dict[str, Any]:
    n = len(rows)
    class_cnt = Counter(r["class"] for r in rows)
    status_cnt = Counter(r["status"] for r in rows)
    tier_cnt = Counter(r["tier_label"] for r in rows)

    # 矛盾码 / 指纹频次（含 BLOCK 与 REVIEW）
    code_cnt: Counter = Counter()
    fp_cnt: Counter = Counter()
    for r in rows:
        for c in r["block_codes"] + r["review_codes"]:
            code_cnt[c] += 1
        for fp in r["fingerprints"]:
            fp_cnt[fp] += 1

    # 按来源交叉
    by_src: Dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_src[r["source_type"]][r["tier_label"]] += 1
        by_src[r["source_type"]]["__total__"] += 1

    # 判据触发次数（A 路自洽性）
    check_cnt: Counter = Counter()
    for r in rows:
        for c in r["checks_run"]:
            check_cnt[c] += 1

    # 盲区提示（同一句话可能命中多条候选，只报「哪句话 + 命中几条」）
    warn_cnt: Counter = Counter()
    for r in rows:
        for w in r.get("warnings") or []:
            warn_cnt[w] += 1

    return {
        "total": n,
        "failed": len(failures),
        "warnings": [{"message": k, "count": v} for k, v in warn_cnt.most_common()],
        "classes": [{"key": k, "label": v, "count": class_cnt.get(k, 0),
                     "pct": round(100.0 * class_cnt.get(k, 0) / n, 1) if n else 0.0}
                    for k, v in CLASSES],
        "status": [{"key": k, "count": v} for k, v in status_cnt.most_common()],
        "tiers": [{"key": k, "count": v} for k, v in tier_cnt.most_common()],
        "top_codes": [{"code": k, "count": v} for k, v in code_cnt.most_common(12)],
        "top_fingerprints": [{"fingerprint": k, "count": v}
                             for k, v in fp_cnt.most_common(12)],
        "by_source_type": {
            s: {"total": c.get("__total__", 0),
                "tiers": {k: v for k, v in c.items() if k != "__total__"}}
            for s, c in sorted(by_src.items())
        },
        "checks_run": [{"check": k, "count": v} for k, v in check_cnt.most_common()],
    }


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------


def print_console(stats: Dict[str, Any], rows: List[Dict[str, Any]],
                  failures: List[Dict[str, str]], title: str) -> None:
    print("=" * 78)
    print(f"MDCA 批量审查：{title}")
    print("=" * 78)
    print(f"候选 {stats['total']} 条，读取失败 {stats['failed']} 条")
    if stats["total"] == 0:
        print("\n没有可审查的候选。")
        for f in failures:
            print(f"  [失败] {f['source']}: {f['reason']}")
        return

    print("\n【分类】")
    for c in stats["classes"]:
        if c["count"] == 0:
            continue
        bar = "█" * max(1, round(c["pct"] / 4))
        print(f"  {c['label']:<12}{c['count']:>4}  {c['pct']:>5.1f}%  {bar}")

    print("\n【状态】")
    for s in stats["status"]:
        print(f"  {s['key']:<16}{s['count']:>4}")

    print("\n【四档】")
    for t in stats["tiers"]:
        print(f"  {t['key']:<10}{t['count']:>4}")

    if stats["checks_run"]:
        print("\n【A 路自洽性判据触发次数】")
        for c in stats["checks_run"]:
            print(f"  {c['check']:<28}{c['count']:>4}")

    if stats["top_fingerprints"]:
        print("\n【错法指纹（具体到错在哪一步）】")
        for f in stats["top_fingerprints"]:
            mark = "  ← 系统性" if f["count"] >= 3 else ""
            print(f"  {f['fingerprint']:<52}{f['count']:>4}{mark}")

    print("\n【矛盾码频次】")
    for c in stats["top_codes"]:
        print(f"  {c['code']:<28}{c['count']:>4}")

    print("\n【按来源交叉】")
    if True:
        tiers = ["淘汰", "人工复核", "继续优化", "送外部"]
        hdr = f"  {'来源':<14}{'条数':>5}" + "".join(f"{t:>10}" for t in tiers)
        print(hdr)
        for s, d in stats["by_source_type"].items():
            line = f"  {s:<14}{d['total']:>5}" + "".join(
                f"{d['tiers'].get(t, 0):>10}" for t in tiers)
            print(line)

    if stats.get("warnings"):
        print("\n【盲区提示（这些 case 的结论只是「没审」，不是「没问题」）】")
        for w in stats["warnings"]:
            print(f"  {w['count']:>3} 条  {w['message']}")

    print("\n【逐条明细】")
    for r in rows:
        print(f"  {str(r['case_id'])[:24]:<26}{r['status']:<14}{r['tier_label']:<8}"
              f"{r['class_label']:<12}{r['why'][:34]}")

    if failures:
        print("\n【读取失败】")
        for f in failures:
            print(f"  {f['source']}: {f['reason']}")


def render_markdown(stats: Dict[str, Any], rows: List[Dict[str, Any]],
                    failures: List[Dict[str, str]], title: str,
                    plugins: Optional[str]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L: List[str] = []
    L.append("# MDCA 批量审查报告")
    L.append("")
    L.append(f"> 目标：`{title}`")
    L.append(f"> 生成时间：{now}")
    L.append(f"> 插件：{plugins or '(无，A 路)'}")
    L.append(f"> 候选 {stats['total']} 条，读取失败 {stats['failed']} 条")
    L.append("")
    L.append("> ⚠️ 本报告只说明「这批数据自己能不能立住」，不说明电机的真实性能。"
             "`物理不可能`以外的分类都不等于「电机合格」。")
    L.append("")

    L.append("## 一、分类摘要")
    L.append("")
    L.append("| 分类 | 条数 | 占比 | 该怎么处理 |")
    L.append("|---|---:|---:|---|")
    handle = {
        "physically_impossible": "直接淘汰；已证伪，不是待确认项",
        "format_error": "让对方按契约重交",
        "unverifiable": "找来源或补 conservatism 声明，不是找错",
        "needs_review": "人看一眼（超域/外推/基准豁免）",
        "clean": "可进下一轮",
        "error": "人工排查",
    }
    for c in stats["classes"]:
        if c["count"] == 0:
            continue
        L.append(f"| {c['label']} | {c['count']} | {c['pct']}% | {handle.get(c['key'], '')} |")
    L.append("")

    L.append("## 二、状态与档位")
    L.append("")
    L.append("| 状态 | 条数 |")
    L.append("|---|---:|")
    for s in stats["status"]:
        L.append(f"| `{s['key']}` | {s['count']} |")
    L.append("")
    L.append("| 档位 | 条数 |")
    L.append("|---|---:|")
    for t in stats["tiers"]:
        L.append(f"| {t['key']} | {t['count']} |")
    L.append("")

    if stats["checks_run"]:
        L.append("## 三、A 路自洽性判据触发情况")
        L.append("")
        L.append("| 判据 | 触发次数 |")
        L.append("|---|---:|")
        for c in stats["checks_run"]:
            L.append(f"| `{c['check']}` | {c['count']} |")
        L.append("")

    if stats["top_fingerprints"]:
        L.append("## 四、错法指纹")
        L.append("")
        L.append("指纹说明「错在哪一步」，比只报一个原因码更有用。")
        L.append("出现 3 次及以上的标为「系统性」——同一批数据里反复出现同一个指纹，")
        L.append("说明是生成器的一致缺陷，不是个别笔误。")
        L.append("")
        L.append("| 指纹 | 出现次数 | 判读 |")
        L.append("|---|---:|---|")
        for f in stats["top_fingerprints"]:
            tag = "**系统性错法**" if f["count"] >= 3 else "个别"
            L.append(f"| `{f['fingerprint']}` | {f['count']} | {tag} |")
        L.append("")

    if stats["by_source_type"]:
        L.append("## 五、按来源交叉")
        L.append("")
        tiers = ["淘汰", "人工复核", "继续优化", "送外部"]
        L.append("| 来源 | 条数 | " + " | ".join(tiers) + " |")
        L.append("|---|---:|" + "---:|" * len(tiers))
        for s, d in stats["by_source_type"].items():
            L.append(f"| {s} | {d['total']} | "
                     + " | ".join(str(d["tiers"].get(t, 0)) for t in tiers) + " |")
        L.append("")

    if stats.get("warnings"):
        L.append("## 六、盲区提示")
        L.append("")
        L.append("**这些 case 的结论是「没审」，不是「没问题」。**"
                 "要看完整结论需按提示启用对应能力。")
        L.append("")
        L.append("| 提示 | 命中条数 |")
        L.append("|---|---:|")
        for w in stats["warnings"]:
            L.append(f"| {w['message']} | {w['count']} |")
        L.append("")

    L.append("## 七、逐条明细")
    L.append("")
    L.append("| case_id | 来源类型 | 状态 | 档位 | 分类 | 主要矛盾 |")
    L.append("|---|---|---|---|---|---|")
    for r in rows:
        L.append(f"| `{r['case_id']}` | {r['source_type']} | `{r['status']}` | "
                 f"{r['tier_label']} | {r['class_label']} | `{r['why']}` |")
    L.append("")

    if failures:
        L.append("## 八、读取失败")
        L.append("")
        for f in failures:
            L.append(f"- `{f['source']}`：{f['reason']}")
        L.append("")

    return "\n".join(L)


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------


def selftest() -> bool:
    ok = True

    def check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {label:<48}{'PASS' if good else 'FAIL'}"
              + (f"  {detail}" if detail and not good else ""))
        ok = ok and good

    print("=== mdca_review.py 自测 ===")

    # 1) 分类优先级：物理不可能 > 格式问题 > 缺来源
    check("物理不可能优先于格式问题",
          classify(["P0_contract", "I1_kcl_violation"], [], "AUDIT_BLOCKED")
          == "physically_impossible")
    check("只有 V2 → 数据没来源",
          classify(["V2_optimistic"], [], "AUDIT_BLOCKED") == "unverifiable")
    check("其他 BLOCK → 格式不对",
          classify(["V1_missing"], [], "AUDIT_BLOCKED") == "format_error")
    check("口径错 + 缺来源 → 先修口径（格式不对）",
          classify(["P2_temp_basis", "V2_unverified"], [], "AUDIT_BLOCKED")
          == "format_error")
    check("AUDIT_PASS → 全干净",
          classify([], [], "AUDIT_PASS") == "clean")
    check("AUDIT_REVIEW → 待人工确认",
          classify([], ["D1_out_of_domain"], "AUDIT_REVIEW") == "needs_review")

    # 2) 端到端：AI 目录能被展开，且分类与指纹都对
    ai_dir = HERE / "cases_ai"
    if ai_dir.exists():
        found, failures = collect_cases([str(ai_dir)])
        check("收集 cases_ai/ 得到 10 条且无失败",
              len(found) == 10 and not failures, f"{len(found)}/{len(failures)}")
        if found:
            cfg = load_config()
            r = review_one(found[0][0], found[0][1], cfg, None)
            check("AI01 归入物理不可能", r["class"] == "physically_impossible", r["class"])
            check("指纹抓到 Tw= 与 Tr= 两个分节点字段",
                  any("Tw=" in f and "Tr=" in f for f in r["fingerprints"]),
                  str(r["fingerprints"]))
    else:
        check("cases_ai/ 存在", False, "目录不存在")

    # 3) 盲区提示：带 scaling 段但未启用缩放插件时要提醒
    sc_case = (HERE / "cases" / "joint_C_stretched_45mm.json")
    if sc_case.exists():
        cfg = load_config()
        r = review_one("joint_C", load_case(sc_case), cfg, None)
        check("joint_C 未启用 scaling 时给出盲区提示",
              any("scaling" in w for w in r["warnings"]), str(r["warnings"]))
    else:
        check("joint_C 算例存在", False, "文件不存在")

    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "selftest":
        return 0 if selftest() else 1
    if argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    targets: List[str] = []
    md_path: Optional[str] = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--md":
            if i + 1 >= len(argv):
                print("--md 需要一个输出路径")
                return 2
            md_path = argv[i + 1]
            i += 2
            continue
        if a.startswith("--md="):
            md_path = a.split("=", 1)[1]
            i += 1
            continue
        targets.append(a)
        i += 1

    try:
        targets, plugins = split_plugin_args(targets)
    except PluginError as e:
        print(f"参数错误：{e}")
        return 2

    if not targets:
        print("请给出要审查的目录或文件")
        return 2

    try:
        cfg = load_config()
    except Exception as e:  # noqa: BLE001
        print(f"分级配置读取失败：{e}")
        return 2

    found, failures = collect_cases(targets)
    rows: List[Dict[str, Any]] = []
    for label, case in found:
        try:
            rows.append(review_one(label, case, cfg, plugins))
        except Exception as e:  # noqa: BLE001
            failures.append({"source": label, "reason": f"审计失败：{type(e).__name__}: {e}"})

    stats = aggregate(rows, failures)
    title = "、".join(targets)
    print_console(stats, rows, failures, title)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "1.0",
        "title": title,
        "generated_by": "mdca_review.py",
        "plugins": plugins,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stats": stats,
        "rows": rows,
        "failures": failures,
    }
    jpath = OUT_DIR / "review.json"
    jpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON：{jpath}")

    if md_path:
        mpath = Path(md_path)
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(render_markdown(stats, rows, failures, title, plugins),
                         encoding="utf-8")
        print(f"Markdown：{mpath}")

    # 退出码只表示「审查本身是否完成」：0 = 完成，2 = 有读取/审计失败。
    # 「发现了物理不可能」是审查的正常产出，不该让管道报错——
    # 需要按结果门禁时请读 out/review.json 的 stats.classes。
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
