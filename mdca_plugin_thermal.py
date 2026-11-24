#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
B 路插件：温度计算（两热阻 / 三节点 / 区间 / 压力测试）
=====================================================

**默认关闭。** 只有显式 `--plugins thermal`、case 里写 `"plugins": {"thermal": true}`，
或 `MDCA_PLUGINS=thermal` 时才运行。

为什么默认关：

    这个插件里的热阻是「借来的」——Rws/Rsa/Rrs 三个数没有一个是算出来的，
    没有一个对应某台真实电机。用它算出的温度能自洽，但不是验证。
    A 路不产生数据，所以不需要它。

本模块是**原 mdca.py 里温度计算段的搬迁**，逻辑逐行保持等价：

    · to_cas / to_cas3 / to_boxcas / uses_three_node / has_intervals
    · 点值求解 → 区间顶点求解 → 压力测试 → 合成判定状态

搬家的目的是让 mdca.py 不再在模块顶部 import mdca_thermal——
A 路因此可以在没有 thermal 模块的情况下独立跑通全流程。

返回（`run(case, opts) -> dict`）：

    {
      "ok": bool,
      "reason": str,                       # ok=False 时
      "answer": {
        "model": {...},                    # 节点数 / 三节点参数
        "coupling": {...},                 # 求解器状态
        "temperature": {...},              # 温度字段（含区间）
        "verdict_point": {...},            # 点值判定
        "verdict_box": {...} | None,       # 区间判定
        "box_coupling": {...} | None,
        "stress_tests": {...},
        "verdict_status": str,             # 纯温度层的合成状态
        "verdict_reasons": [str, ...],
      },
      "issues": [...],
    }

`verdict_status` 只含温度层的结论（COUPLING_FAILED / MODEL_FAIL / MODEL_REVIEW /
POINT_PASS / BOX_PASS）。「无校准不得推荐」「超适用域不得推荐」这两条降级由
mdca.py 统一施加——它们是审计层的规则，A 路也要用。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdca_thermal import (  # noqa: E402
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

NAME = "thermal"
ROAD = "B"

# 启用本插件后，这些参数必须存在（A 路不需要它们，B 路需要）
NEED = ("Rws", "Rsa", "Pcu", "Pfe", "Ta")

# 可以给区间的参数（盒工况的上界/下界）
INTERVAL_CAPABLE = ("Rws", "Rsa", "Pcu", "Pfe", "Ta", "alpha")

STATUS_RANK = {
    "POINT_PASS": 0, "BOX_PASS": 0,
    "MODEL_REVIEW": 1, "SOLVER_UNKNOWN": 1,
    "MODEL_FAIL": 2, "COUPLING_FAILED": 2,
}


# --------------------------------------------------------------------------
# Case → 求解器入参
# --------------------------------------------------------------------------


def to_cas(case: Any, which: str = "point") -> Cas:
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


def uses_three_node(case: Any) -> bool:
    p = case.params.get("Peddy")
    return bool(p and p.value > 0)


def to_cas3(case: Any) -> Cas3:
    base = to_cas(case, "point")
    return Cas3(
        name=base.name, Rws=base.Rws, Rsa=base.Rsa, P0=base.P0, Pfe=base.Pfe,
        Ta=base.Ta, Tref=base.Tref, alpha=base.alpha, Tw_max=base.Tw_max,
        temperature_type=base.temperature_type,
        Peddy=case.val("Peddy", 0.0),
        Rrs=case.val("Rrs", 3.0),
        Tr_max=float(case.limits.get("Tr_max", case.val("Tr_max", 150.0))),
    )


def to_boxcas(case: Any, peddy: float = 0.0) -> Any:
    def rng(key: str, default: float) -> Tuple[float, float]:
        p = case.params.get(key)
        if p is None:
            return (default, default)
        return p.interval if p.interval else (p.value, p.value)

    pfe_lo, pfe_hi = rng("Pfe", 0.0)
    from mdca_thermal import BoxCas
    return BoxCas(
        name=case.case_id,
        Rws=rng("Rws", 1.0), Rsa=rng("Rsa", 1.0),
        P0=rng("Pcu", 0.0), Pfe=(pfe_lo + peddy, pfe_hi + peddy), Ta=rng("Ta", 25.0),
        alpha=rng("alpha", 0.00393), Tref=float(case.val("Tref", 20.0)),
        Tw_max=float(case.limits.get("Tw_max", 155.0)),
        temperature_type=str(case.limits.get("temperature_type", "mean")),
        gamma=float(case.calibration.get("gamma", 2.0)),
    )


def has_intervals(case: Any) -> bool:
    return any(case.params[k].interval for k in INTERVAL_CAPABLE
               if k in case.params and case.params[k].interval)


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def run(case: Any, opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    opts = dict(opts or {})

    missing = [k for k in NEED if k not in case.params]
    if missing:
        return {
            "ok": False,
            "reason": f"温度计算缺少参数：{', '.join(missing)}",
            "issues": [{
                "code": "V1_plugin_missing", "severity": "BLOCK",
                "message": (f"已启用 thermal 插件，但缺少它必需的参数：{', '.join(missing)}"
                            f"（A 路不需要这些；要么补参数，要么去掉 --plugins thermal）"),
                "params": tuple(missing), "detail": {"plugin": NAME},
            }],
        }

    delta_cal = float(case.calibration.get("delta_cal_K", 0.0) or 0.0)
    three = uses_three_node(case)

    answer: Dict[str, Any] = {}

    # ---- 点值 ----
    if three:
        cas3 = to_cas3(case)
        r = solve_three(cas3)
        cas: Any = cas3
        answer["model"] = {
            "nodes": 3,
            "Peddy_W": cas3.Peddy, "Rrs_KpW": cas3.Rrs, "Tr_max_C": cas3.Tr_max,
        }
    else:
        cas = to_cas(case, "point")
        r = solve_point(cas)
        answer["model"] = {"nodes": 2}

    answer["coupling"] = {
        "status": r["status"], "g": round(cas.g, 4),
        "iterations": r.get("iterations"),
        "error_bound_K": r.get("error_bound_K"),
    }

    if r["status"] != "CONVERGED":
        answer["temperature"] = {"winding_point_C": None}
        answer["verdict_status"] = "COUPLING_FAILED"
        answer["verdict_reasons"] = [r.get("reason", "divergence")]
        return {"ok": True, "answer": answer, "issues": []}

    # ---- 点值判定 ----
    if three:
        vp = judge_three(r, cas3, delta_cal)
        answer["temperature"] = {
            "winding_point_C": round(r["Tw"], 3),
            "winding_corrected_C": round(r["Tw"] + delta_cal, 3),
            "winding_corrected_interval_C": None,
            "rotor_point_C": round(r["Tr"], 3),
            "stator_point_C": round(r["Ts"], 3),
            "total_loss_W": round(r["total_loss"], 3),
        }
    else:
        vp = judge_point(r, cas, delta_cal)
        answer["temperature"] = {
            "winding_point_C": round(r["Tw"], 3),
            "winding_corrected_C": round(r["Tw"] + delta_cal, 3),
            "winding_corrected_interval_C": None,
        }
    answer["verdict_point"] = vp

    overall = vp["status"]
    reasons: List[str] = list(vp.get("reasons", []))

    # ---- 区间 ----
    if has_intervals(case):
        box = to_boxcas(case, case.val("Peddy", 0.0) if three else 0.0)
        rb = solve_box(box)
        answer["box_coupling"] = rb
        if rb["status"] == "CONVERGED":
            vb = judge_box(rb, box, delta_cal)
            answer["temperature"]["winding_corrected_interval_C"] = [
                round(rb["Tw_lo"] + delta_cal, 3),
                round(rb["Tw_hi"] + delta_cal, 3),
            ]
            answer["verdict_box"] = vb
            if STATUS_RANK[vb["status"]] > STATUS_RANK[overall]:
                overall = vb["status"]
                reasons.extend(vb.get("reasons", []))
        else:
            answer["verdict_box"] = rb
            if STATUS_RANK[rb["status"]] > STATUS_RANK[overall]:
                overall = rb["status"]
                reasons.append(rb.get("reason", "box_solver_failed"))

    # ---- 压力测试（只对点值做） ----
    st = stress_test(cas, vp["status"], delta_cal)
    answer["stress_tests"] = st
    if st["decision_flipped"]:
        overall = "MODEL_REVIEW"
        reasons.append("decision_flipped_under_stress")

    answer["verdict_status"] = overall
    answer["verdict_reasons"] = reasons
    return {"ok": True, "answer": answer, "issues": []}


# --------------------------------------------------------------------------
# 自测：只验「插件能跑、返回结构齐全」，不重复 thermal 模块的 13 项
# --------------------------------------------------------------------------


class _P:
    """最小 Param 替身，避免 import mdca（那会成环）。"""

    def __init__(self, value: float, unit: str = "", interval=None):
        self.value = value
        self.unit = unit
        self.interval = interval
        self.palette = None
        self.verified = True
        self.source = "selftest"


class _Case:
    def __init__(self, params: Dict[str, _P], limits=None, calibration=None):
        self.case_id = "selftest"
        self.params = params
        self.limits = limits or {"Tw_max": 155.0}
        self.calibration = calibration or {}

    def val(self, key: str, default: float = 0.0) -> float:
        p = self.params.get(key)
        return p.value if p else default


def selftest() -> bool:
    ok = True

    def check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {label:<46}{'PASS' if good else 'FAIL'}"
              + (f"  {detail}" if detail else ""))
        ok = ok and good

    print("=== thermal 插件自测 ===")

    base = {
        "Rws": _P(1.16), "Rsa": _P(2.03), "Pcu": _P(13.6),
        "Pfe": _P(5.33), "Ta": _P(40.0),
    }
    o = run(_Case(dict(base)), {})
    check("两热阻：跑通且给温度", o["ok"] and o["answer"]["temperature"]["winding_point_C"] is not None)
    check("两热阻：节点数 = 2", o["answer"]["model"]["nodes"] == 2)

    three = dict(base)
    three["Peddy"] = _P(15.24)
    three["Rrs"] = _P(3.0)
    o = run(_Case(three), {})
    check("三节点：节点数 = 3", o["answer"]["model"]["nodes"] == 3)
    check("三节点：给转子温度", o["answer"]["temperature"].get("rotor_point_C") is not None)

    no_rws = {k: v for k, v in base.items() if k != "Rws"}
    o = run(_Case(no_rws), {})
    check("缺 Rws → ok=False 且指出来", (not o["ok"]) and "Rws" in o["reason"])
    check("缺 Rws → 带 V1_plugin_missing",
          any(i["code"] == "V1_plugin_missing" for i in o["issues"]))

    # 区间：给 interval 就应产出区间字段
    boxed = {
        "Rws": _P(1.16, interval=(0.9, 1.45)),
        "Rsa": _P(2.03, interval=(1.5, 2.6)),
        "Pcu": _P(13.6, interval=(13.6, 13.8)),
        "Pfe": _P(5.33, interval=(4.0, 6.5)),
        "Ta": _P(40.0, interval=(25.0, 45.0)),
    }
    o = run(_Case(dict(boxed)), {})
    iv = o["answer"]["temperature"].get("winding_corrected_interval_C")
    check("区间：产出上下界", isinstance(iv, list) and len(iv) == 2)

    # 不传 calibration 时 delta_cal=0，不应崩
    o = run(_Case(dict(base), calibration={}), {})
    check("无校准：delta_cal 按 0 处理", o["ok"])

    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if selftest() else 1)
