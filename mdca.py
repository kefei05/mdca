#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 统一入口：口径审计 → 硬拦截 → 适用域 → 两热阻 → 判定
=============================================================

设计原则（对应方案条文）：

  · 方案 5     Python 算温度，区间算术给包络，审计器汇总
  · 方案 6     六种状态；PASS 只是模型层结论
  · 方案 6.1   未校准时总体结论必须降级为 MODEL_REVIEW
  · 方案 7.2   验证状态非"已验证"的数据，**代码层面禁止**进入计算与校准依据
  · 方案 11.7  单调顶点求值 + 前提检查
  · 方案 11.10 候选超出校准适用域 ⇒ MODEL_REVIEW，不得因算出数值就输出 BOX_PASS

与两个独立脚本的关系：

    mdca_palette.py   口径层（字段定义、冲突规则、Markdown 扫描）
    mdca_thermal.py   物理层（两热阻、耦合迭代、区间、压力测试）
    mdca.py           编排层（校验 → 拦截 → 计算 → 判定 → 报告）  ← 本文件

用法：

    python mdca.py audit cases/joint_A.json       # 审计一个算例
    python mdca.py audit cases/                   # 审计目录下全部 .json
    python mdca.py validate cases/joint_A.json    # 只做校验，不计算
    python mdca.py palette <md文件或目录>          # 口径审计（转发到 palette）
    python mdca.py selftest                       # 冒烟测试
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdca_palette import MIX_BLOCKING, PALETTE_FIELDS, Palette  # noqa: E402

from mdca_thermal import (  # noqa: E402
    BoxCas,
    Cas,
    Cas3,
    judge_box,
    judge_point,
    judge_three,
    solve_box,
    solve_point,
    solve_three,
    stress_test,
)
from mdca_scaling import Geometry, scale  # noqa: E402
from mdca_schema import read_jsonl, validate_document  # noqa: E402
from mdca_provenance import (  # noqa: E402
    CONSTRAINT_VERSION,
    LEDGER_NAME,
    Ledger,
    LedgerEntry,
    check_sources,
    code_fingerprint,
    fingerprint_digest,
    now_iso,
    sha256_bytes,
    sha256_file,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


SCHEMA_VERSION = "1.0"
OUT_DIR = Path(__file__).resolve().parent / "out"

# 必需参数与单位（方案 7.1：数值 + 单位 + 来源 + 验证状态）
REQUIRED: Dict[str, str] = {
    "Rws": "K/W",     # 绕组 → 铁芯
    "Rsa": "K/W",     # 铁芯 → 环境
    "Pcu": "W",       # 参考温度下铜损
    "Pfe": "W",       # 注入铁芯的其它损耗
    "Ta": "C",        # 环境温度
}
OPTIONAL: Dict[str, str] = {
    "alpha": "1/K",
    "Tref": "C",
    "Tw_max": "C",
    # 三节点扩展：给了 Peddy>0 就自动走三节点
    "Peddy": "W",
    "Rrs": "K/W",
    "Tr_max": "C",
}
INTERVAL_CAPABLE = ("Rws", "Rsa", "Pcu", "Pfe", "Ta", "alpha")

STATUS_RANK = {
    "POINT_PASS": 0, "BOX_PASS": 0,
    "MODEL_REVIEW": 1, "SOLVER_UNKNOWN": 1,
    "MODEL_FAIL": 2, "COUPLING_FAILED": 2,
}


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class Param:
    key: str
    value: float
    unit: str
    palette: Palette
    verified: bool = True
    conservatism: Optional[str] = None
    source: str = ""
    interval: Optional[Tuple[float, float]] = None

    def as_dict(self) -> Dict[str, Any]:
        out = {"value": self.value, "unit": self.unit,
               "palette": self.palette.as_dict(),
               "verified": self.verified, "source": self.source}
        if self.conservatism:
            out["conservatism"] = self.conservatism
        if self.interval:
            out["interval"] = list(self.interval)
        return out


@dataclass
class Issue:
    code: str
    severity: str          # BLOCK / REVIEW / WARN / INFO
    message: str
    params: Tuple[str, ...] = ()
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Case:
    case_id: str
    description: str
    params: Dict[str, Param]
    limits: Dict[str, Any]
    calibration: Dict[str, Any]
    domain: Dict[str, Any]
    scaling: Dict[str, Any] = field(default_factory=dict)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    source_file: str = ""
    contract_errors: List[str] = field(default_factory=list)
    source_type: str = ""
    benchmark: str = ""

    def get(self, key: str) -> Optional[Param]:
        return self.params.get(key)

    def val(self, key: str, default: float = 0.0) -> float:
        p = self.params.get(key)
        return p.value if p else default


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------


def _mk_param(key: str, raw: Dict[str, Any]) -> Param:
    pal_raw = raw.get("palette", {}) or {}
    pal = Palette(**{k: v for k, v in pal_raw.items() if k in PALETTE_FIELDS})
    interval = raw.get("interval")
    interval_t = None
    if isinstance(interval, (list, tuple)) and len(interval) == 2:
        interval_t = (float(interval[0]), float(interval[1]))
    return Param(
        key=key,
        value=float(raw["value"]),
        unit=str(raw.get("unit", "")),
        palette=pal,
        verified=bool(raw.get("verified", True)),
        conservatism=raw.get("conservatism"),
        source=str(raw.get("source", "")),
        interval=interval_t,
    )


def load_case(path: Path) -> Case:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    return parse_case_doc(raw, str(path))


def parse_case_doc(raw: Dict[str, Any], source: str) -> Case:
    """从 dict 构造 Case。JSONL 与单文件共用此入口。"""
    contract = validate_document(raw)
    params: Dict[str, Param] = {}
    for k, v in (raw.get("params") or {}).items():
        try:
            params[k] = _mk_param(k, v)
        except Exception as e:
            contract.append(f"params.{k} 解析失败: {type(e).__name__}")
    return Case(
        case_id=str(raw.get("case_id") or Path(source).stem),
        description=str(raw.get("description", "")),
        params=params,
        limits=raw.get("limits") or {},
        calibration=raw.get("calibration") or {},
        domain=raw.get("domain") or {},
        scaling=raw.get("scaling") or {},
        sources=raw.get("sources") or [],
        source_file=source,
        contract_errors=contract,
        source_type=str(raw.get("source_type") or ""),
        benchmark=str(raw.get("benchmark") or ""),
    )


# --------------------------------------------------------------------------
# 几何缩放（结果覆盖 Rws/Rsa/Rrs，并附 ±不确定度区间）
# --------------------------------------------------------------------------


def apply_scaling(case: Case) -> Tuple[Dict[str, Param], List[Issue], Optional[Dict[str, Any]]]:
    sc = case.scaling
    if not sc:
        return {}, [], None

    try:
        ref_geom = Geometry(**sc["reference_geometry"])
        geom = Geometry(**sc["geometry"])
        ref_R = sc["reference_R"]
    except (KeyError, TypeError) as e:
        return {}, [Issue("S0_scaling_input", "BLOCK",
                          f"scaling 段字段不全：{e}", ())], None

    res = scale(
        ref_geom, ref_R, geom,
        rel_unc=float(sc.get("relative_uncertainty", 0.25)),
        max_dev=float(sc.get("max_deviation", 0.30)),
    )

    derived: Dict[str, Param] = {}
    pal = Palette(method="analytic_scaled", scope="motor")
    derived["Rws"] = Param("Rws", round(res.Rws, 4), "K/W", pal, verified=True,
                           source="几何缩放律（未校准）",
                           interval=(round(res.Rws_interval[0], 4),
                                     round(res.Rws_interval[1], 4)))
    derived["Rsa"] = Param("Rsa", round(res.Rsa, 4), "K/W", pal, verified=True,
                           source="几何缩放律（未校准）",
                           interval=(round(res.Rsa_interval[0], 4),
                                     round(res.Rsa_interval[1], 4)))
    derived["Rrs"] = Param("Rrs", round(res.Rrs, 4), "K/W", pal, verified=True,
                           source="几何缩放律（未校准）",
                           interval=(round(res.Rrs_interval[0], 4),
                                     round(res.Rrs_interval[1], 4)))

    issues = [Issue("S1_scaling_applied", "INFO",
                    f"Rws/Rsa/Rrs 由几何缩放律给出（±{sc.get('relative_uncertainty', 0.25) * 100:.0f}%），未校准",
                    ("Rws", "Rsa", "Rrs"), res.as_dict())]
    if not res.within_reference:
        issues.append(Issue(
            "S2_extrapolation", "REVIEW",
            f"候选几何偏离基准 {res.max_deviation * 100:.1f}%"
            f"（阈值 {sc.get('max_deviation', 0.30) * 100:.0f}%）：属外推，缩放律不保证成立",
            ("Rws", "Rsa", "Rrs"),
            {"ratios": res.as_dict()["ratios"]}))
    return derived, issues, res.as_dict()


# --------------------------------------------------------------------------
# 校验与硬拦截（方案 7.2）
# --------------------------------------------------------------------------


def validate(case: Case) -> List[Issue]:
    issues: List[Issue] = []

    # V1 必需参数缺失 / 单位不符
    for key, unit in REQUIRED.items():
        p = case.params.get(key)
        if p is None:
            issues.append(Issue("V1_missing", "BLOCK", f"缺少必需参数 {key}", (key,)))
            continue
        if p.unit != unit:
            issues.append(Issue(
                "V1_unit", "BLOCK",
                f"{key} 单位应为 {unit}，实际 {p.unit or '(空)'}", (key,)))

    # V2 未验证数据硬拦截（方案 7.2 的核心）
    # benchmark 例外：基准数据的用途是测试规则，不是产生推荐。若没有这个例外，
    # 任何由文献反推的基准都会在第一步就被拦死，其余规则根本没机会被测试。
    # 例外只放宽「是否允许计算」，不放宽「结论等级」——benchmark 的结论仍强制 REVIEW。
    is_benchmark = bool(case.benchmark)
    for key, p in case.params.items():
        if p.verified:
            continue
        if p.conservatism == "conservative":
            issues.append(Issue(
                "V2_conservative", "INFO",
                f"{key} 是未验证数据，已声明按保守方向使用（结论偏安全，须在报告注明）",
                (key,), {"source": p.source}))
        elif p.conservatism == "optimistic":
            issues.append(Issue(
                "V2_optimistic", "BLOCK",
                f"{key} 是未验证数据且声明为乐观方向：不得进入合格性判断",
                (key,), {"source": p.source}))
        elif is_benchmark:
            issues.append(Issue(
                "V2_benchmark_exempt", "REVIEW",
                f"{key} 未验证（benchmark 基准数据）：允许计算，但结论强制 REVIEW",
                (key,), {"source": p.source}))
        else:
            issues.append(Issue(
                "V2_unverified", "BLOCK",
                f"{key} 未验证且未声明 conservatism：按方案 7.2 拒绝进入计算",
                (key,), {"source": p.source}))

    # V3 参数间口径冲突
    keys = sorted(case.params)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = case.params[keys[i]], case.params[keys[j]]
            for f, av, bv in a.palette.conflicts_with(b.palette, MIX_BLOCKING):
                issues.append(Issue(
                    "V3_palette_mix", "BLOCK",
                    f"参数 {a.key}({f}={av}) 与 {b.key}({f}={bv}) 口径冲突",
                    (a.key, b.key), {"field": f}))

    # V4 未标记口径
    for key, p in case.params.items():
        if not p.palette.is_tagged():
            issues.append(Issue(
                "V4_untagged", "WARN",
                f"{key} 未声明任何口径字段", (key,)))

    # V5 区间合法性
    for key in INTERVAL_CAPABLE:
        p = case.params.get(key)
        if p is None or p.interval is None:
            continue
        lo, hi = p.interval
        if lo > hi:
            issues.append(Issue("V5_interval", "BLOCK",
                                f"{key} 区间下界 {lo} 大于上界 {hi}", (key,)))
        elif not (lo <= p.value <= hi):
            issues.append(Issue("V5_interval", "WARN",
                                f"{key} 点值 {p.value} 不在区间 [{lo},{hi}] 内", (key,)))

    # V6 物理正值
    for key in ("Rws", "Rsa"):
        p = case.params.get(key)
        if p is not None and p.value <= 0:
            issues.append(Issue("V6_positive", "BLOCK",
                                f"{key} 必须为正，实际 {p.value}", (key,)))
    for key in ("Pcu", "Pfe"):
        p = case.params.get(key)
        if p is not None and p.value < 0:
            issues.append(Issue("V6_positive", "BLOCK",
                                f"{key} 不得为负，实际 {p.value}", (key,)))
    return issues


def check_domain(case: Case) -> List[Issue]:
    """方案 11.10：候选超出校准适用域 ⇒ MODEL_REVIEW。"""
    issues: List[Issue] = []
    domain = case.domain or {}
    if not domain:
        return issues
    for key, rng in domain.items():
        if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
            continue
        p = case.params.get(key)
        if p is None:
            continue
        # 用 value 与 interval 的并集：只看 interval 会漏掉「点值越界但区间没改」的情况，
        # 只看 value 会漏掉「区间端点越界」。两者都要覆盖。
        lo_t, hi_t = p.value, p.value
        if p.interval:
            lo_t = min(lo_t, p.interval[0])
            hi_t = max(hi_t, p.interval[1])
        target = (lo_t, hi_t)
        lo, hi = float(rng[0]), float(rng[1])
        if target[0] < lo or target[1] > hi:
            issues.append(Issue(
                "D1_out_of_domain", "REVIEW",
                f"{key} 取值 [{target[0]:g},{target[1]:g}] 超出校准域 [{lo:g},{hi:g}]",
                (key,), {"domain": [lo, hi], "target": list(target)}))
    return issues


# --------------------------------------------------------------------------
# 计算（把 Case 翻译成 thermal 的入参）
# --------------------------------------------------------------------------


def to_cas(case: Case, which: str = "point") -> Cas:
    def pick(key: str, default: float) -> float:
        p = case.params.get(key)
        if p is None:
            return default
        if which == "point":
            return p.value
        lo, hi = p.interval if p.interval else (p.value, p.value)
        return hi if which == "hi" else lo

    return Cas(
        name=f"{case.case_id}[{which}]",
        Rws=pick("Rws", 1.0),
        Rsa=pick("Rsa", 1.0),
        P0=pick("Pcu", 0.0),
        Pfe=pick("Pfe", 0.0),
        Ta=pick("Ta", 25.0),
        Tref=pick("Tref", 20.0),
        alpha=pick("alpha", 0.00393),
        Tw_max=float(case.limits.get("Tw_max", 155.0)),
        temperature_type=str(case.limits.get("temperature_type", "mean")),
    )


def has_intervals(case: Case) -> bool:
    return any(case.params[k].interval for k in INTERVAL_CAPABLE
               if k in case.params and case.params[k].interval)


def uses_three_node(case: Case) -> bool:
    p = case.params.get("Peddy")
    return bool(p and p.value > 0)


def to_cas3(case: Case) -> Cas3:
    base = to_cas(case, "point")
    return Cas3(
        name=base.name, Rws=base.Rws, Rsa=base.Rsa, P0=base.P0, Pfe=base.Pfe,
        Ta=base.Ta, Tref=base.Tref, alpha=base.alpha, Tw_max=base.Tw_max,
        temperature_type=base.temperature_type,
        Peddy=case.val("Peddy", 0.0),
        Rrs=case.val("Rrs", 3.0),
        Tr_max=float(case.limits.get("Tr_max", case.val("Tr_max", 150.0))),
    )


def to_boxcas(case: Case, peddy: float = 0.0) -> BoxCas:
    def rng(key: str, default: float) -> Tuple[float, float]:
        p = case.params.get(key)
        if p is None:
            return (default, default)
        return p.interval if p.interval else (p.value, p.value)

    pfe_lo, pfe_hi = rng("Pfe", 0.0)
    return BoxCas(
        name=case.case_id,
        Rws=rng("Rws", 1.0), Rsa=rng("Rsa", 1.0),
        P0=rng("Pcu", 0.0), Pfe=(pfe_lo + peddy, pfe_hi + peddy), Ta=rng("Ta", 25.0),
        alpha=rng("alpha", 0.00393), Tref=float(case.val("Tref", 20.0)),
        Tw_max=float(case.limits.get("Tw_max", 155.0)),
        temperature_type=str(case.limits.get("temperature_type", "mean")),
        gamma=float(case.calibration.get("gamma", 2.0)),
    )


def audit(case: Case) -> Dict[str, Any]:
    derived, s_issues, s_result = apply_scaling(case)
    if derived:
        case.params.update(derived)

    # 溯源：case 哈希 + 外部来源哈希校验（方案 7.2）
    base_dir = Path(case.source_file).parent if case.source_file else Path.cwd()
    src_checks = check_sources(case.sources, base_dir)
    src_bad = [s for s in src_checks if not s.ok]
    # 哈希基于「规范化审计输入」而非文件字节：
    # 同样的内容无论是 .json 还是 .jsonl 的一行，指纹必须相同，
    # 否则账本会把"换了个文件格式"误报成"输入被修改"。
    payload = json.dumps({
        "case_id": case.case_id,
        "params": {k: v.as_dict() for k, v in sorted(case.params.items())},
        "limits": case.limits,
        "calibration": case.calibration,
        "domain": case.domain,
        "scaling": case.scaling,
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")
    case_hash = sha256_bytes(payload)
    fp = code_fingerprint()
    code_digest = fingerprint_digest(fp)

    issues = validate(case) + check_domain(case) + s_issues
    if case.contract_errors:
        issues.append(Issue(
            "P0_contract", "BLOCK",
            "不符合候选契约（schema 1.0）：" + "；".join(case.contract_errors[:4]),
            (), {"errors": case.contract_errors}))
    if src_bad:
        issues.append(Issue(
            "P1_source_hash", "BLOCK",
            "外部来源哈希不符或文件缺失：" + "；".join(f"{s.path}（{s.reason}）" for s in src_bad),
            (), {"sources": [s.__dict__ for s in src_bad]}))
    blocked = [i for i in issues if i.severity == "BLOCK"]
    review = [i for i in issues if i.severity == "REVIEW"]

    delta_cal = float(case.calibration.get("delta_cal_K", 0.0) or 0.0)
    cal_present = bool(case.calibration.get("source_type"))

    result: Dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "case_id": case.case_id,
        "description": case.description,
        "source_file": case.source_file,
        "params": {k: v.as_dict() for k, v in case.params.items()},
        "limits": case.limits,
        "calibration": {
            "delta_cal_K": delta_cal,
            "source_type": case.calibration.get("source_type"),
            "fitted_on_same_points": case.calibration.get("fitted_on_same_points"),
            "model_similarity_assumed": case.calibration.get("model_similarity_assumed"),
            "present": cal_present,
        },
        "validation": {
            "blocked": bool(blocked),
            "issues": [i.__dict__ for i in issues],
            "block_count": len(blocked),
            "review_count": len(review),
        },
        "scaling": s_result,
        "provenance": {
            "case_hash": case_hash,
            "code_digest": code_digest,
            "code_fingerprint": fp,
            "constraint_version": CONSTRAINT_VERSION,
            "sources": [
                {"path": s.path, "expected": s.expected, "actual": s.actual,
                 "ok": s.ok, "reason": s.reason}
                for s in src_checks
            ],
            "audited_at": now_iso(),
        },
        "disclaimer": "Model-level constraint audit only; not physical validation.",
    }

    # 硬拦截：不计算温度
    if blocked:
        result["coupling"] = {"status": "NOT_RUN",
                              "reason": "blocked_by_validation"}
        result["temperature"] = None
        result["audit"] = {
            "overall_status": "SOLVER_UNKNOWN",
            "reasons": ["input_blocked: " + ", ".join(sorted({i.code for i in blocked}))],
        }
        return result

    # 计算
    three = uses_three_node(case)
    if three:
        cas3 = to_cas3(case)
        r = solve_three(cas3)
        cas = cas3          # 供压力测试复用（公式与两热阻同形）
        result["model"] = {
            "nodes": 3,
            "Peddy_W": cas3.Peddy, "Rrs_KpW": cas3.Rrs, "Tr_max_C": cas3.Tr_max,
        }
    else:
        cas = to_cas(case, "point")
        r = solve_point(cas)
        result["model"] = {"nodes": 2}

    result["coupling"] = {
        "status": r["status"], "g": round(cas.g, 4),
        "iterations": r.get("iterations"),
        "error_bound_K": r.get("error_bound_K"),
    }

    if r["status"] != "CONVERGED":
        result["temperature"] = {"winding_point_C": None}
        result["audit"] = {"overall_status": "COUPLING_FAILED",
                           "reasons": [r.get("reason", "divergence")]}
        return result

    if three:
        vp = judge_three(r, cas3, delta_cal)
        result["temperature"] = {
            "winding_point_C": round(r["Tw"], 3),
            "winding_corrected_C": round(r["Tw"] + delta_cal, 3),
            "winding_corrected_interval_C": None,
            "rotor_point_C": round(r["Tr"], 3),
            "stator_point_C": round(r["Ts"], 3),
            "total_loss_W": round(r["total_loss"], 3),
        }
    else:
        vp = judge_point(r, cas, delta_cal)
        result["temperature"] = {
            "winding_point_C": round(r["Tw"], 3),
            "winding_corrected_C": round(r["Tw"] + delta_cal, 3),
            "winding_corrected_interval_C": None,
        }
    result["verdict_point"] = vp

    overall = vp["status"]
    reasons = list(vp.get("reasons", []))

    # 区间
    if has_intervals(case):
        box = to_boxcas(case, case.val("Peddy", 0.0) if three else 0.0)
        rb = solve_box(box)
        result["box_coupling"] = rb
        if rb["status"] == "CONVERGED":
            vb = judge_box(rb, box, delta_cal)
            result["temperature"]["winding_corrected_interval_C"] = [
                round(rb["Tw_lo"] + delta_cal, 3),
                round(rb["Tw_hi"] + delta_cal, 3),
            ]
            result["verdict_box"] = vb
            if STATUS_RANK[vb["status"]] > STATUS_RANK[overall]:
                overall = vb["status"]
                reasons.extend(vb.get("reasons", []))
        else:
            result["verdict_box"] = rb
            if STATUS_RANK[rb["status"]] > STATUS_RANK[overall]:
                overall = rb["status"]
                reasons.append(rb.get("reason", "box_solver_failed"))

    # 压力测试（只对点值做）
    st = stress_test(cas, vp["status"], delta_cal)
    result["stress_tests"] = st
    if st["decision_flipped"]:
        overall = "MODEL_REVIEW"
        reasons.append("decision_flipped_under_stress")

    # 方案 6.1：无校准 ⇒ 不得推荐
    if not cal_present and overall in ("POINT_PASS", "BOX_PASS"):
        overall = "MODEL_REVIEW"
        reasons.append("calibration_missing")

    # 方案 11.10：超适用域 ⇒ 不得推荐
    if review:
        if overall in ("POINT_PASS", "BOX_PASS"):
            overall = "MODEL_REVIEW"
        reasons.extend(sorted({i.code for i in review}))

    result["audit"] = {
        "overall_status": overall,
        "reasons": sorted(set(reasons), key=str),
    }
    return result


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------


def print_report(o: Dict[str, Any]) -> None:
    c = o.get("coupling") or {}
    t = o.get("temperature") or {}
    a = o.get("audit") or {}
    v = o.get("validation") or {}
    print("-" * 78)
    print(f"算例：{o['case_id']}   {o.get('description','')}")
    print(f"  校验：{'BLOCKED' if v.get('blocked') else 'OK'}"
          f"（block {v.get('block_count',0)} / review {v.get('review_count',0)}）")
    for i in v.get("issues", []):
        if i["severity"] in ("BLOCK", "REVIEW"):
            print(f"    [{i['severity']}] {i['code']}: {i['message']}")
    if c.get("status") == "NOT_RUN":
        print(f"  耦合：未运行（{c.get('reason')}）")
        print(f"  总体结论：{a.get('overall_status')}  {a.get('reasons')}")
        return
    print(f"  耦合：{c.get('status')}  g={c.get('g')}  iters={c.get('iterations')}")
    if t.get("winding_point_C") is not None:
        print(f"  温度：点值 {t['winding_point_C']}℃"
              f"  加裕度 {t.get('winding_corrected_C')}℃", end="")
        iv = t.get("winding_corrected_interval_C")
        if iv:
            print(f"  区间 [{iv[0]}, {iv[1]}]℃")
        else:
            print()
    st = o.get("stress_tests") or {}
    if st.get("results"):
        bad = [k for k, val in st["results"].items() if val != "ok"]
        print(f"  压力测试：{len(st['results'])} 项，超限 {len(bad)} 项"
              + (f" → {bad}" if bad else ""))
    vp = o.get("verdict_point") or {}
    if vp.get("note"):
        print(f"  注：{vp['note']}")
    if t.get("rotor_point_C") is not None:
        print(f"  转子：{t['rotor_point_C']}℃（磁钢上限 "
              f"{(o.get('model') or {}).get('Tr_max_C', 150)}℃）")
    print(f"  总体结论：{a.get('overall_status')}  {a.get('reasons')}")


def _load_cases(f: Path) -> List[Case]:
    """一个文件 → 一批 Case。.json 一文件一例；.jsonl 每行一例（单行失败不影响其他行）。"""
    if f.suffix == ".jsonl":
        out: List[Case] = []
        for ln, doc, err in read_jsonl(f):
            if err:
                print(f"[跳过] {f.name}:{ln} 解析失败 {err}")
                continue
            out.append(parse_case_doc(doc, f"{f.name}:{ln}"))
        return out
    return [load_case(f)]


def cmd_audit(paths: List[str]) -> int:
    files: List[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            files.extend(sorted(pp.glob("*.json")))
            files.extend(sorted(pp.glob("*.jsonl")))
        elif pp.exists():
            files.append(pp)
        else:
            print(f"[跳过] 不存在：{p}")
    if not files:
        print("没有找到算例文件（*.json）")
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(OUT_DIR / LEDGER_NAME)
    reports = []
    print("=" * 78)
    print("MDCA 审计报告  v1.0")
    print("=" * 78)
    all_cases: List[Case] = []
    for f in files:
        try:
            all_cases.extend(_load_cases(f))
        except Exception as e:
            print(f"[失败] {f.name}: {type(e).__name__}: {e}")
    for case in all_cases:
        o = audit(case)

        # 账本：只追加，改过的输入会留下"变更"痕迹（方案 7.2）
        prov = o["provenance"]
        prev = ledger.detect_change(o["case_id"], prov["case_hash"])
        if prev:
            print(f"  [变更] {o['case_id']}：输入哈希与上次不同"
                  f"（上次 {str(prev.get('case_hash'))[:12]} → 本次 {prov['case_hash'][:12]}），"
                  f"旧记录已保留")
            o["audit"]["reasons"] = sorted(
                set(o["audit"]["reasons"]) | {"input_changed_since_last_audit"}, key=str)
        ledger.append(LedgerEntry(
            ts=prov["audited_at"], case_id=o["case_id"], case_hash=prov["case_hash"],
            status=o["audit"]["overall_status"], code_digest=prov["code_digest"],
            constraint_version=CONSTRAINT_VERSION,
            delta_cal_K=float((o.get("calibration") or {}).get("delta_cal_K") or 0.0),
        ))

        print_report(o)
        reports.append(o)

    out = OUT_DIR / "audit.json"
    payload = {"schema": SCHEMA_VERSION, "reports": reports} if len(reports) > 1 else reports[0]
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("-" * 78)
    if reports:
        print("汇总：")
        for o in reports:
            print(f"  {o['case_id']:<28}{o['audit']['overall_status']}")
    print(f"JSON：{out}")
    bad = [o for o in reports if o["audit"]["overall_status"] in ("MODEL_FAIL", "COUPLING_FAILED")]
    return 0 if not bad else 1


def cmd_validate(paths: List[str]) -> int:
    files = [Path(p) for p in paths]
    rc = 0
    for f in files:
        case = load_case(f)
        issues = validate(case) + check_domain(case)
        blocks = [i for i in issues if i.severity == "BLOCK"]
        print("=" * 78)
        print(f"校验：{f.name}  ->  {'BLOCKED' if blocks else 'PASS'}")
        for i in issues:
            print(f"  [{i.severity}] {i.code}: {i.message}")
        if blocks:
            rc = 1
    return rc


def cmd_palette(args: List[str]) -> int:
    import subprocess
    here = Path(__file__).resolve().parent
    return subprocess.call([sys.executable, str(here / "mdca_palette.py"), "--scan"] + args)


def cmd_selftest() -> int:
    here = Path(__file__).resolve().parent
    ok = True
    print("=== mdca.py 冒烟测试 ===")

    # 1) 缺参 → BLOCK
    c = Case("t1", "", {k: Param(k, 1.0, u, Palette(method="analytic"))
                        for k, u in list(REQUIRED.items())[:-1]},
             {}, {}, {})
    o = audit(c)
    r = o["audit"]["overall_status"] == "SOLVER_UNKNOWN"
    print(f"  ① 缺必需参数 → 拒绝计算          {'PASS' if r else 'FAIL'}")
    ok &= r

    # 2) 未验证且无声明 → BLOCK
    ps = {k: Param(k, v, u, Palette(method="analytic"))
          for k, (v, u) in {"Rws": (1.16, "K/W"), "Rsa": (2.03, "K/W"),
                            "Pcu": (13.6, "W"), "Pfe": (5.33, "W"), "Ta": (40.0, "C")}.items()}
    ps["Pcu"].verified = False
    o = audit(Case("t2", "", ps, {"Tw_max": 155.0}, {}, {}))
    r = o["audit"]["overall_status"] == "SOLVER_UNKNOWN"
    print(f"  ② 未验证数据无声明 → 拒绝计算    {'PASS' if r else 'FAIL'}")
    ok &= r

    # 3) 未验证但声明保守 → 放行
    ps2 = {k: Param(k, v, u, Palette(method="analytic"))
           for k, (v, u) in {"Rws": (1.16, "K/W"), "Rsa": (2.03, "K/W"),
                             "Pcu": (13.6, "W"), "Pfe": (5.33, "W"), "Ta": (40.0, "C")}.items()}
    ps2["Pfe"].verified = False
    ps2["Pfe"].conservatism = "conservative"
    o = audit(Case("t3", "", ps2, {"Tw_max": 155.0}, {}, {}))
    r = o["coupling"]["status"] == "CONVERGED"
    print(f"  ③ 未验证但声明保守 → 放行        {'PASS' if r else 'FAIL'}")
    ok &= r

    # 4) 口径冲突 → BLOCK
    ps3 = {k: Param(k, v, u, Palette(method="analytic"))
           for k, (v, u) in {"Rws": (1.16, "K/W"), "Rsa": (2.03, "K/W"),
                             "Pcu": (13.6, "W"), "Pfe": (5.33, "W"), "Ta": (40.0, "C")}.items()}
    ps3["Pcu"].palette = Palette(turns="6")
    ps3["Pfe"].palette = Palette(turns="8")
    o = audit(Case("t4", "", ps3, {"Tw_max": 155.0}, {}, {}))
    r = o["validation"]["blocked"]
    print(f"  ④ 口径冲突（6匝 vs 8匝）→ 拦截   {'PASS' if r else 'FAIL'}")
    ok &= r

    # 5) 超适用域 → REVIEW
    ps4 = {k: Param(k, v, u, Palette(method="analytic"))
           for k, (v, u) in {"Rws": (1.16, "K/W"), "Rsa": (2.03, "K/W"),
                             "Pcu": (13.6, "W"), "Pfe": (5.33, "W"), "Ta": (40.0, "C")}.items()}
    o = audit(Case("t5", "", ps4, {"Tw_max": 155.0},
                   {"delta_cal_K": 10.0, "source_type": "external_model_mismatch"},
                   {"Ta": [20.0, 30.0]}))
    r = "D1_out_of_domain" in o["audit"]["reasons"]
    print(f"  ⑤ 环境温度超适用域 → REVIEW      {'PASS' if r else 'FAIL'}")
    ok &= r

    # 6) 正常算例（有校准）→ PASS
    o = audit(Case("t6", "", ps4, {"Tw_max": 155.0},
                   {"delta_cal_K": 5.0, "source_type": "external_model_mismatch"}, {}))
    r = o["audit"]["overall_status"] in ("POINT_PASS", "MODEL_FAIL")
    print(f"  ⑥ 有校准且未超限 → 给出判定      {'PASS' if r else 'FAIL'}"
          f"（{o['audit']['overall_status']}）")
    ok &= r

    print(f"冒烟测试：{'全部通过' if ok else '有失败项'}")
    return 0 if ok else 1


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def cmd_check(args: List[str]) -> int:
    """自检：随机数值回代 + 负例测试集（方案 7.3 + 10.1）。"""
    import mdca_checks

    here = Path(__file__).resolve().parent
    n = 500
    if args:
        try:
            n = int(args[0])
        except ValueError:
            print(f"样本数无效：{args[0]}")
            return 2

    rep = mdca_checks.run(n)
    mdca_checks.print_report(rep)
    ok_all = rep["ok"]

    print()
    print("=" * 78)
    print("边界算例：输入层负例 + 结论层边界，每一条都必须命中预期")
    print("=" * 78)
    dirs = [here / "cases_negative", here / "cases_edge"]
    files: List[Path] = []
    for d in dirs:
        if d.exists():
            files.extend(sorted(d.glob("*.json")))
    if not files:
        print(f"未找到算例目录：{dirs}")
        return 1

    hdr = f"{'文件':<34}{'期望':>17}{'实际':>16}{'结果':>7}"
    print(hdr)
    print("-" * len(hdr))
    for f in files:
        raw = json.loads(f.read_text(encoding="utf-8-sig"))
        expect = raw.get("_expect") or {}
        case = load_case(f)
        o = audit(case)
        status = o["audit"]["overall_status"]
        reasons = " ".join(o["audit"].get("reasons") or [])
        issues = " ".join(i["code"] for i in (o.get("validation") or {}).get("issues", []))
        blob = reasons + " " + issues

        want_status = expect.get("status")
        want_reason = expect.get("reason_contains")
        hit = (status == want_status)
        if want_reason:
            hit = hit and (want_reason in blob)
        ok_all = ok_all and hit
        print(f"{f.stem[:32]:<34}{want_status or '-':>17}{status:>16}"
              f"{'PASS' if hit else 'FAIL':>7}")

    print()
    print(f"自检总体：{'全部通过' if ok_all else '有失败项（存在漏报）'}")
    return 0 if ok_all else 1


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "audit":
        return cmd_audit(rest or ["cases"])
    if cmd == "validate":
        return cmd_validate(rest)
    if cmd == "palette":
        return cmd_palette(rest)
    if cmd == "selftest":
        return cmd_selftest()
    if cmd == "check":
        return cmd_check(rest)
    print(f"未知命令：{cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
