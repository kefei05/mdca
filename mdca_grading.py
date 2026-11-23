#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 四档分级（配置驱动，规则不写死）
=====================================

四档（纠偏基线 6.5 节 C1）：

    淘汰      模型层明确违规、耦合失稳、或硬拦截属明确违规 → 不值得再看
    继续优化  通过但有改进空间（裕度小、贴边）→ 值得再跑一轮设计
    人工复核  结论不确定（REVIEW/UNKNOWN）、缺校准、外推、超域、翻转 → 人必须看
    送外部    通过 + 校准有效 + 在域内 + 裕度充足 → 值得送 Motor-CAD / Maxwell

硬约束（C2）：**分级只能比原状态更保守。MODEL_REVIEW 与 SOLVER_UNKNOWN 永不能升到「送外部」。**

规则来源：`grading.json`，带 version 与 changelog。改规则必须升版本并写理由。

用法：
    python mdca_grading.py selftest
    python mdca_grading.py out/audit.json        # 对已有审计结果分级
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "grading.json"

TIER_ORDER = {"eliminate": 0, "manual": 1, "refine": 2, "external": 3}
# 冲突时取更保守（序号小）的一方
TIER_LABEL = {"eliminate": "淘汰", "manual": "人工复核",
              "refine": "继续优化", "external": "送外部"}


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or CONFIG_PATH
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 从审计报告提取特征
# --------------------------------------------------------------------------


def extract_features(report: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    audit = report.get("audit") or {}
    status = audit.get("overall_status")
    reasons = [str(r) for r in (audit.get("reasons") or [])]

    validation = report.get("validation") or {}
    codes: Set[str] = {i.get("code") for i in (validation.get("issues") or [])
                       if i.get("severity") == "BLOCK"}

    kinds = cfg.get("block_kinds") or {}
    hard = set(kinds.get("hard_violation") or [])
    soft = set(kinds.get("data_issue") or [])

    block_kind = None
    if validation.get("blocked"):
        block_kind = "hard_violation" if (codes & hard) else (
            "data_issue" if (codes & soft) else "data_issue")

    flags: Set[str] = set()
    joined = " ".join(reasons)
    if "D1_out_of_domain" in joined:
        flags.add("out_of_domain")
    if "S2_extrapolation" in joined:
        flags.add("extrapolation")
    if (report.get("stress_tests") or {}).get("decision_flipped"):
        flags.add("stress_flipped")
    if "linear_model_out_of_validity" in joined:
        flags.add("model_invalid")

    # 裕度：优先用区间上界（保守）
    temp = report.get("temperature") or {}
    limits = report.get("limits") or {}
    Tw_max = float(limits.get("Tw_max", 155.0))
    margin = None
    interval = temp.get("winding_corrected_interval_C")
    if interval:
        margin = Tw_max - float(interval[1])
    elif temp.get("winding_corrected_C") is not None:
        margin = Tw_max - float(temp["winding_corrected_C"])

    cal = report.get("calibration") or {}
    return {
        "overall_status": status,
        "block_kind": block_kind,
        "blocked": bool(validation.get("blocked")),
        "block_codes": sorted(c for c in codes if c),
        "flags": sorted(flags),
        "margin_K": None if margin is None else round(margin, 3),
        "distance_to_limit_K": None if margin is None else round(abs(margin), 3),
        "calibration_present": bool(cal.get("present")),
        "delta_cal_K": float(cal.get("delta_cal_K") or 0.0),
    }


# --------------------------------------------------------------------------
# 规则匹配
# --------------------------------------------------------------------------


def _match(when: Dict[str, Any], feat: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    th = cfg.get("thresholds") or {}

    for key, allowed in when.items():
        if key == "overall_status":
            if feat["overall_status"] not in allowed:
                return False
        elif key == "block_kind":
            if feat["block_kind"] not in allowed:
                return False
        elif key == "calibration_present":
            if feat["calibration_present"] not in allowed:
                return False
        elif key == "flags":
            if not all(f in feat["flags"] for f in allowed):
                return False
        elif key == "margin_below":
            limit = float(th.get(allowed, 10.0))
            if feat["margin_K"] is None or feat["margin_K"] >= limit:
                return False
        elif key == "distance_to_limit_below":
            limit = float(th.get(allowed, 5.0))
            if feat["distance_to_limit_K"] is None or feat["distance_to_limit_K"] >= limit:
                return False
        else:
            return False
    return True


def grade(report: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    feat = extract_features(report, cfg)

    hit = None
    for rule in cfg.get("rules", []):
        if _match(rule.get("when") or {}, feat, cfg):
            hit = {"tier": rule["tier"], "rule": rule["id"], "reason": rule["reason"]}
            break

    if hit is None:
        hit = {"tier": "manual", "rule": "fallback",
               "reason": "无规则命中，保守归入人工复核"}

    # C2 硬约束：REVIEW / UNKNOWN 永不能升到「送外部」
    if feat["overall_status"] in ("MODEL_REVIEW", "SOLVER_UNKNOWN") \
            and hit["tier"] == "external":
        hit = {"tier": "manual", "rule": "C2_forced",
               "reason": "原状态为 REVIEW/UNKNOWN，按 C2 强制降为人工复核"}

    return {
        "tier": hit["tier"],
        "tier_label": TIER_LABEL[hit["tier"]],
        "rule": hit["rule"],
        "reason": hit["reason"],
        "grading_version": cfg.get("version"),
        "features": feat,
    }


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------


def _mk(status: str, *, blocked=False, codes=(), margin=None, cal=True,
        reasons=(), flipped=False, interval=None) -> Dict[str, Any]:
    rep = {
        "audit": {"overall_status": status, "reasons": list(reasons)},
        "validation": {
            "blocked": blocked,
            "issues": [{"code": c, "severity": "BLOCK"} for c in codes],
        },
        "calibration": {"present": cal, "delta_cal_K": 33.0},
        "limits": {"Tw_max": 155.0},
        "stress_tests": {"decision_flipped": flipped},
        "temperature": {"winding_corrected_C": None if margin is None else 155.0 - margin,
                        "winding_corrected_interval_C": interval},
    }
    return rep


def selftest() -> bool:
    cfg = load_config()
    ok = True
    print(f"=== 分级自测（配置版本 {cfg.get('version')}）===")

    def check(label: str, rep: Dict[str, Any], expect: str) -> None:
        nonlocal ok
        got = grade(rep, cfg)["tier"]
        good = got == expect
        print(f"  {label:<40}{TIER_LABEL.get(got, got):>8}"
              f"  期望 {TIER_LABEL.get(expect, expect):<8}{'PASS' if good else 'FAIL'}")
        ok = ok and good

    check("MODEL_FAIL → 淘汰", _mk("MODEL_FAIL"), "eliminate")
    check("COUPLING_FAILED → 淘汰", _mk("COUPLING_FAILED"), "eliminate")
    check("口径冲突（hard）→ 淘汰",
          _mk("SOLVER_UNKNOWN", blocked=True, codes=("V3_palette_mix",)), "eliminate")
    check("未验证数据（soft）→ 人工复核",
          _mk("SOLVER_UNKNOWN", blocked=True, codes=("V2_unverified",)), "manual")
    check("MODEL_REVIEW → 人工复核", _mk("MODEL_REVIEW"), "manual")
    check("POINT_PASS 但缺校准 → 人工复核",
          _mk("POINT_PASS", margin=40, cal=False), "manual")
    check("POINT_PASS 超适用域 → 人工复核",
          _mk("POINT_PASS", margin=40, reasons=["D1_out_of_domain"]), "manual")
    check("POINT_PASS 压力翻转 → 人工复核",
          _mk("POINT_PASS", margin=40, flipped=True), "manual")
    check("POINT_PASS 裕度 5K → 继续优化",
          _mk("POINT_PASS", margin=5, cal=True), "refine")
    check("POINT_PASS 裕度 40K → 送外部",
          _mk("POINT_PASS", margin=40, cal=True), "external")
    check("C2：REVIEW 不得升到送外部",
          _mk("MODEL_REVIEW", margin=100, cal=True), "manual")

    # C2 的正面用例：区间上界裕度充足但状态是 BOX_PASS
    check("BOX_PASS 裕度 30K → 送外部",
          _mk("BOX_PASS", margin=30, cal=True), "external")

    # grading-1.3：A 路三状态（无温度结论）
    check("AUDIT_PASS → 继续优化（不送外部）",
          _mk("AUDIT_PASS"), "refine")
    check("AUDIT_PASS 但无校准 → 人工复核",
          _mk("AUDIT_PASS", cal=False), "manual")
    check("AUDIT_REVIEW → 人工复核",
          _mk("AUDIT_REVIEW"), "manual")
    check("AUDIT_BLOCKED + 硬违规 → 淘汰",
          _mk("AUDIT_BLOCKED", blocked=True, codes=("V3_palette_mix",)), "eliminate")
    check("AUDIT_BLOCKED + 数据问题 → 人工复核",
          _mk("AUDIT_BLOCKED", blocked=True, codes=("V2_unverified",)), "manual")

    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def print_grades(path: Path) -> int:
    cfg = load_config()
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    reports = raw.get("reports") if isinstance(raw, dict) else raw
    if not isinstance(reports, list):
        reports = [raw]

    print("=" * 78)
    print(f"四档分级（配置 {cfg.get('version')}）")
    print("=" * 78)
    hdr = f"{'case_id':<34}{'状态':>16}{'分级':>10}{'规则':>18}"
    print(hdr)
    print("-" * len(hdr))
    counts: Dict[str, int] = {}
    for rep in reports:
        g = grade(rep, cfg)
        counts[g["tier"]] = counts.get(g["tier"], 0) + 1
        print(f"{str(rep.get('case_id'))[:32]:<34}"
              f"{str((rep.get('audit') or {}).get('overall_status')):>16}"
              f"{g['tier_label']:>10}{g['rule']:>18}")
    print()
    print("汇总：" + "  ".join(f"{TIER_LABEL[t]} {counts.get(t, 0)}"
                              for t in ("eliminate", "refine", "manual", "external")))
    return 0


def main(argv: List[str]) -> int:
    if not argv or argv[0] == "selftest":
        return 0 if selftest() else 1
    return print_grades(Path(argv[0]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
