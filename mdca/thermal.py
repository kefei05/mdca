#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 第二层：两热阻稳态热网络 + 电磁—热耦合
============================================

拓扑：

    Pcu → [W 绕组] --Rws-- [S 定子铁心] --Rsa-- [A 环境]
                              ↑
                            Pfe

稳态解：

    Ts = Ta + Rsa*(Pcu + Pfe)
    Tw = Ts + Rws*Pcu
    ⇒ Tw = Ta + Req*Pcu + Rsa*Pfe,   Req = Rws + Rsa

铜耗温度反馈（线性的，可解析）：

    Pcu(Tw) = P0*[1 + alpha*(Tw - Tref)]
    g = alpha*Req*P0                       热反馈因子
    theta = N/(1-g),  N = (Ta-Tref) + Rsa*Pfe + Req*P0
    Tw = Tref + theta                      当 g < 1

g >= 1 ⇒ 损耗随温度增长超过散热能力 ⇒ COUPLING_FAILED，不输出温度。

区间处理用单调顶点求值：theta 对每个参数单调不减（在 N>=0、g<1 时），
所以最不利顶点给出 Tw_hi、最有利顶点给出 Tw_lo。前提不成立时降级 REVIEW。

用法：
    python -m mdca.thermal            # 跑两个内置算例（joint-module A/B）
    python -m mdca.thermal --selftest # 方案 11.13 的单元测试
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

# 方案 11.8：Tw 超过限值的这个倍数时，线性电阻模型视为失效
LINEAR_MODEL_RATIO = 1.5


@dataclass
class Cas:
    """一个点工况。"""
    name: str
    Rws: float          # 绕组 → 铁芯  K/W
    Rsa: float          # 铁芯 → 环境  K/W
    P0: float           # 参考温度下的铜损 W
    Pfe: float          # 铁损 + 其它注入铁芯的损耗 W
    Ta: float           # 环境温度 ℃
    Tref: float = 20.0  # 铜损参考温度 ℃
    alpha: float = 0.00393  # 铜电阻温度系数 1/K
    Tw_max: float = 155.0   # 绕组温度上限 ℃（F 级）
    temperature_type: str = "mean"
    max_iter: int = 20000

    @property
    def Req(self) -> float:
        return self.Rws + self.Rsa

    @property
    def g(self) -> float:
        return self.alpha * self.Req * self.P0

    @property
    def N(self) -> float:
        return (self.Ta - self.Tref) + self.Rsa * self.Pfe + self.Req * self.P0


@dataclass
class BoxCas:
    """一个盒工况：每个参数给 [lo, hi]。"""
    name: str
    Rws: Tuple[float, float]
    Rsa: Tuple[float, float]
    P0: Tuple[float, float]
    Pfe: Tuple[float, float]
    Ta: Tuple[float, float]
    alpha: Tuple[float, float]
    Tref: float = 20.0
    Tw_max: float = 155.0
    temperature_type: str = "mean"
    gamma: float = 2.0

    def corner(self, which: str) -> Cas:
        pick = (lambda t: t[1]) if which == "hi" else (lambda t: t[0])
        return Cas(
            name=f"{self.name}[{which}]",
            Rws=pick(self.Rws), Rsa=pick(self.Rsa), P0=pick(self.P0),
            Pfe=pick(self.Pfe), Ta=pick(self.Ta), alpha=pick(self.alpha),
            Tref=self.Tref, Tw_max=self.Tw_max,
            temperature_type=self.temperature_type,
        )


# --------------------------------------------------------------------------
# 求解
# --------------------------------------------------------------------------


def solve_analytic(c: Cas) -> Optional[float]:
    """解析解，仅用于交叉验证与单元测试。"""
    if c.g >= 1.0:
        return None
    return c.Tref + c.N / (1.0 - c.g)


def solve_point(c: Cas, tol_T: float = 1e-3, tol_P: float = 1e-6) -> Dict[str, Any]:
    g = c.g
    if g >= 1.0:
        return {
            "status": "COUPLING_FAILED",
            "reason": "thermal_feedback_divergence",
            "g": g,
            "iterations": 0,
        }

    Tw = c.Tref
    Pcu_prev: Optional[float] = None
    iterations = 0
    err_bound = float("inf")
    rel = float("inf")

    for iterations in range(1, c.max_iter + 1):
        Pcu = c.P0 * (1.0 + c.alpha * (Tw - c.Tref))
        Ts = c.Ta + c.Rsa * (Pcu + c.Pfe)
        Tw_new = Ts + c.Rws * Pcu

        err_bound = (g / (1.0 - g)) * abs(Tw_new - Tw)
        rel = abs(Pcu - Pcu_prev) / max(abs(Pcu), 1.0) if Pcu_prev is not None else float("inf")

        Tw = Tw_new
        Pcu_prev = Pcu

        if err_bound <= tol_T and rel <= tol_P:
            break
    else:
        return {
            "status": "COUPLING_FAILED",
            "reason": "iteration_limit",
            "g": g,
            "iterations": iterations,
        }

    return {
        "status": "CONVERGED",
        "Tw": Tw,
        "Ts": Ts,
        "Pcu": Pcu,
        "g": g,
        "iterations": iterations,
        "error_bound_K": err_bound,
        "loss_relative_residual": rel,
    }


def calibrate(residuals: List[float], gamma: float = 2.0) -> Dict[str, Any]:
    """MDCA 11.9：只取正残差的最大值，负残差既不能降低预测也不能互相抵消。"""
    if not residuals:
        return {"source_count": 0, "delta_cal": 0.0, "valid": False,
                "reason": "no_calibration_point"}
    e_under = max([0.0] + [r for r in residuals if r > 0])
    return {
        "source_count": len(residuals),
        "underestimation_residuals_K": [round(r, 4) for r in residuals],
        "e_under": round(e_under, 4),
        "gamma": gamma,
        "delta_cal": round(gamma * e_under, 4),
        "valid": True,
        "sample_size_warning": len(residuals) < 3,
    }


def judge_point(r: Dict[str, Any], c: Cas, delta_cal: float = 0.0) -> Dict[str, Any]:
    if r["status"] != "CONVERGED":
        return {"status": r["status"], "reasons": [r.get("reason", "")]}
    Tw = r["Tw"] + delta_cal

    # 方案 11.8：Tw 远超绝缘等级上限时，线性电阻模型已失效
    limit = LINEAR_MODEL_RATIO * c.Tw_max
    if Tw > limit:
        return {
            "status": "MODEL_REVIEW",
            "Tw_corrected": round(Tw, 3),
            "reasons": ["linear_model_out_of_validity", "temperature_exceeds_limit"],
            "note": (f"Tw={Tw:.1f}℃ 达限值的 {Tw / c.Tw_max:.2f}×"
                     f"（触发线 {limit:.0f}℃）：线性电阻模型与绝缘/材料假设均已失效，"
                     f"该数值不可用于任何定量结论"),
        }

    if Tw <= c.Tw_max:
        return {"status": "POINT_PASS", "Tw_corrected": round(Tw, 3), "reasons": []}
    return {"status": "MODEL_FAIL", "Tw_corrected": round(Tw, 3),
            "reasons": ["temperature_exceeds_limit"]}


def solve_box(b: BoxCas) -> Dict[str, Any]:
    hi = b.corner("hi")
    lo = b.corner("lo")

    # 前提检查（顺序重要：先判失稳，再判单调性前提）
    if lo.g >= 1.0:
        return {"status": "COUPLING_FAILED", "reason": "thermal_feedback_divergence",
                "g_lo": round(lo.g, 4), "g_hi": round(hi.g, 4)}
    if hi.g >= 1.0:
        return {"status": "MODEL_REVIEW", "reason": "g_interval_crosses_instability",
                "g_lo": round(lo.g, 4), "g_hi": round(hi.g, 4)}
    if lo.N < 0.0:
        return {"status": "MODEL_REVIEW", "reason": "monotonicity_precondition_failed",
                "N_lo": round(lo.N, 4)}

    r_hi = solve_point(hi)
    r_lo = solve_point(lo)
    if r_hi["status"] != "CONVERGED" or r_lo["status"] != "CONVERGED":
        return {"status": "COUPLING_FAILED", "reason": "corner_solve_failed"}

    return {
        "status": "CONVERGED",
        "Tw_lo": round(r_lo["Tw"], 3),
        "Tw_hi": round(r_hi["Tw"], 3),
        "g_lo": round(lo.g, 4),
        "g_hi": round(hi.g, 4),
        "N_lo": round(lo.N, 4),
        "iterations_hi": r_hi["iterations"],
    }


def judge_box(r: Dict[str, Any], b: BoxCas, delta_cal: float = 0.0) -> Dict[str, Any]:
    if r["status"] != "CONVERGED":
        return {"status": r["status"], "reasons": [r.get("reason", "")]}
    lo = r["Tw_lo"] + delta_cal
    hi = r["Tw_hi"] + delta_cal

    limit = LINEAR_MODEL_RATIO * b.Tw_max
    if hi > limit:
        return {
            "status": "MODEL_REVIEW",
            "Tw_corrected": [round(lo, 3), round(hi, 3)],
            "reasons": ["linear_model_out_of_validity", "temperature_exceeds_limit"],
            "note": (f"区间上界 {hi:.1f}℃ 达限值的 {hi / b.Tw_max:.2f}×"
                     f"（触发线 {limit:.0f}℃）：已超出线性模型有效范围"),
        }

    if hi <= b.Tw_max:
        return {"status": "BOX_PASS", "Tw_corrected": [round(lo, 3), round(hi, 3)], "reasons": []}
    if lo > b.Tw_max:
        return {"status": "MODEL_FAIL", "Tw_corrected": [round(lo, 3), round(hi, 3)],
                "reasons": ["temperature_exceeds_limit"]}
    return {"status": "MODEL_REVIEW", "Tw_corrected": [round(lo, 3), round(hi, 3)],
            "reasons": ["temperature_bound_crosses_limit"]}


# --------------------------------------------------------------------------
# 三节点扩展：加转子（磁钢）节点，纳入转子损耗
# --------------------------------------------------------------------------
#
#   Pcu → [W 绕组] --Rws-- [S 定子铁心] --Rsa-- [A 环境]
#                              ↑   ↑
#                            Pfe  [R 转子] --Rrs--
#                                  ↑
#                              Peddy
#
# 稳态：
#   Pcu               = (Tw - Ts)/Rws
#   Pcu + Pfe + Peddy = (Ts - Ta)/Rsa
#   Peddy             = (Tr - Ts)/Rrs
#
# 转子节点只与定子相连（无独立转子散热支路），所以转子损耗最终也经 Rsa 出去。
# 于是 Tw 的方程与两热阻**同形**，只是 Pfe 换成 (Pfe+Peddy)；热反馈因子 g 不变：
#   Tw = Ta + Rsa*(Pfe+Peddy) + Req*Pcu
#   Tr = Ts + Rrs*Peddy


@dataclass
class Cas3(Cas):
    Peddy: float = 0.0     # 转子侧损耗（磁钢涡流等）W
    Rrs: float = 3.0       # 转子 → 定子 K/W
    Tr_max: float = 150.0  # 磁钢温度上限 ℃（N42SH 约 150）


def solve_three(c: Cas3, tol_T: float = 1e-3, tol_P: float = 1e-6) -> Dict[str, Any]:
    g = c.alpha * c.Req * c.P0
    if g >= 1.0:
        return {"status": "COUPLING_FAILED",
                "reason": "thermal_feedback_divergence", "g": g, "iterations": 0}

    Tw = c.Tref
    Pcu_prev: Optional[float] = None
    err_bound = float("inf")
    rel = float("inf")
    Ts = c.Ta
    Pcu = c.P0
    iterations = 0

    for iterations in range(1, c.max_iter + 1):
        Pcu = c.P0 * (1.0 + c.alpha * (Tw - c.Tref))
        Ts = c.Ta + c.Rsa * (Pcu + c.Pfe + c.Peddy)
        Tw_new = Ts + c.Rws * Pcu

        err_bound = (g / (1.0 - g)) * abs(Tw_new - Tw)
        rel = abs(Pcu - Pcu_prev) / max(abs(Pcu), 1.0) if Pcu_prev is not None else float("inf")

        Tw = Tw_new
        Pcu_prev = Pcu
        if err_bound <= tol_T and rel <= tol_P:
            break
    else:
        return {"status": "COUPLING_FAILED", "reason": "iteration_limit",
                "g": g, "iterations": iterations}

    Tr = Ts + c.Rrs * c.Peddy
    return {
        "status": "CONVERGED", "Tw": Tw, "Ts": Ts, "Tr": Tr, "Pcu": Pcu,
        "g": g, "iterations": iterations,
        "error_bound_K": err_bound, "loss_relative_residual": rel,
        "total_loss": Pcu + c.Pfe + c.Peddy,
    }


def judge_three(r: Dict[str, Any], c: Cas3, delta_cal: float = 0.0) -> Dict[str, Any]:
    """同时判绕组（绝缘上限）与转子（磁钢上限）。
    Δ_cal 只加在绕组上——它来自外部对照的绕组温度残差，对转子没有依据。"""
    if r["status"] != "CONVERGED":
        return {"status": r["status"], "reasons": [r.get("reason", "")]}

    Tw = r["Tw"] + delta_cal
    Tr = r["Tr"]

    limit = LINEAR_MODEL_RATIO * c.Tw_max
    if Tw > limit:
        return {
            "status": "MODEL_REVIEW",
            "Tw_corrected": round(Tw, 3), "Tr": round(Tr, 3),
            "reasons": ["linear_model_out_of_validity", "temperature_exceeds_limit"],
            "note": f"Tw={Tw:.1f}℃ 达限值 {Tw / c.Tw_max:.2f}×，线性模型已失效",
        }

    reasons: List[str] = []
    # 同时给通用码与具体码：前者供分级规则统一匹配，后者说明是哪一头超限
    if Tw > c.Tw_max:
        reasons.append("temperature_exceeds_limit")
        reasons.append("winding_over_insulation_limit")
    if Tr > c.Tr_max:
        reasons.append("temperature_exceeds_limit")
        reasons.append("rotor_over_magnet_limit")

    return {"status": "MODEL_FAIL" if reasons else "POINT_PASS",
            "Tw_corrected": round(Tw, 3), "Tr": round(Tr, 3),
            "reasons": sorted(set(reasons))}


def stress_test(c: Cas, base_status: str, delta_cal: float = 0.0) -> Dict[str, Any]:
    """MDCA 11.11：超出盒子的扰动，检查结论是否翻转。"""
    out: Dict[str, Any] = {"performed": True, "decision_flipped": False, "flipped_by": {}}

    def run(label: str, **over) -> Optional[bool]:
        d = dict(name=c.name, Rws=c.Rws, Rsa=c.Rsa, P0=c.P0, Pfe=c.Pfe, Ta=c.Ta,
                 Tref=c.Tref, alpha=c.alpha, Tw_max=c.Tw_max,
                 temperature_type=c.temperature_type)
        d.update(over)
        if isinstance(c, Cas3):
            d.setdefault("Peddy", c.Peddy)
            d.setdefault("Rrs", c.Rrs)
            d.setdefault("Tr_max", c.Tr_max)
            cc = Cas3(**d)
            r = solve_three(cc)
            if r["status"] != "CONVERGED":
                out["flipped_by"][label] = "COUPLING_FAILED"
                return True
            over_limit = (r["Tw"] + delta_cal > cc.Tw_max) or (r["Tr"] > cc.Tr_max)
            return over_limit
        cc = Cas(**d)
        r = solve_point(cc)
        if r["status"] != "CONVERGED":
            out["flipped_by"][label] = "COUPLING_FAILED"
            return True
        return r["Tw"] + delta_cal > cc.Tw_max

    tests: List[Tuple[str, Dict[str, Any]]] = []
    for factor, fname in ((1.10, "10%"), (1.20, "20%")):
        tests.append((f"A-Rws+{fname}", {"Rws": c.Rws * factor}))
        tests.append((f"A-Rsa+{fname}", {"Rsa": c.Rsa * factor}))
        tests.append((f"A-P0+{fname}", {"P0": c.P0 * factor}))
        tests.append((f"A-Pfe+{fname}", {"Pfe": c.Pfe * factor}))
    tests.append(("B-alpha=0.004", {"alpha": 0.004}))

    was_pass = base_status in ("POINT_PASS", "BOX_PASS")
    results: Dict[str, Any] = {}
    for label, over in tests:
        over_limit = run(label, **over)
        results[label] = "over_limit" if over_limit else "ok"
        if was_pass and over_limit:
            out["decision_flipped"] = True
            out["flipped_by"][label] = "temperature_exceeds_limit"

    # C: gamma 加倍
    c_double = delta_cal * 2.0
    r = solve_three(c) if isinstance(c, Cas3) else solve_point(c)
    if r["status"] == "CONVERGED":
        over = r["Tw"] + c_double > c.Tw_max
        if isinstance(c, Cas3):
            over = over or (r["Tr"] > c.Tr_max)
        results["C-gamma×2"] = "over_limit" if over else "ok"
        if was_pass and over:
            out["decision_flipped"] = True
            out["flipped_by"]["C-gamma×2"] = "calibration_margin_doubled"

    out["results"] = results
    return out


# --------------------------------------------------------------------------
# 内置算例：joint-module 真实参数
# --------------------------------------------------------------------------


def joint_module_cases() -> List[Tuple[str, Cas, Optional[BoxCas]]]:
    """R1 端部加权 1.16 / R2 0.03 / R3 2.0(导热) 或 5.9(自然对流)。"""
    common = dict(P0=13.6, Pfe=5.33, Ta=40.0, Tref=20.0, alpha=0.00393, Tw_max=155.0)
    case_a = Cas(name="A 导热到机身", Rws=1.16, Rsa=0.03 + 2.0, **common)
    box_a = BoxCas(
        name="A 导热到机身（区间）",
        Rws=(0.90, 1.45), Rsa=(1.50, 2.60), P0=(13.6, 13.8), Pfe=(4.0, 6.5),
        Ta=(25.0, 45.0), alpha=(0.00393, 0.00400), Tw_max=155.0,
    )
    case_b = Cas(name="B 纯自然对流", Rws=1.16, Rsa=0.03 + 5.9, **common)
    box_b = BoxCas(
        name="B 纯自然对流（区间）",
        Rws=(0.90, 1.45), Rsa=(4.5, 7.0), P0=(13.6, 13.8), Pfe=(4.0, 6.5),
        Ta=(25.0, 45.0), alpha=(0.00393, 0.00400), Tw_max=155.0,
    )
    return [("A", case_a, box_a), ("B", case_b, box_b)]


# --------------------------------------------------------------------------
# 自测（对应方案 11.13）
# --------------------------------------------------------------------------


def run_selftest() -> bool:
    print("=== 两热阻单元测试 ===")
    ok = True

    def check(label: str, cond: bool, extra: str = "") -> None:
        nonlocal ok
        print(f"  {label:<52}{'PASS' if cond else 'FAIL'} {extra}")
        ok = ok and cond

    base = dict(name="t", Rws=1.16, Rsa=2.03, P0=13.6, Pfe=5.33, Ta=40.0,
                Tref=20.0, alpha=0.00393, Tw_max=155.0)

    # 1. alpha=0 退化为无反馈
    c = Cas(**{**base, "alpha": 0.0})
    r = solve_point(c)
    expect = c.Ta + c.Req * c.P0 + c.Rsa * c.Pfe
    check("① alpha=0 退化为无温度反馈", abs(r["Tw"] - expect) < 1e-9,
          f"Tw={r['Tw']:.4f} expect={expect:.4f}")

    # 2. 迭代解 == 解析解（误差界 tol_T=1e-3 是保证，不是 1e-6）
    c = Cas(**base)
    r = solve_point(c)
    a = solve_analytic(c)
    check("② 迭代解与解析解一致（≤收敛容差 1e-3）",
          a is not None and abs(r["Tw"] - a) < 1e-3,
          f"iter={r['Tw']:.6f} analytic={a:.6f} 差={abs(r['Tw'] - a):.2e}K")

    # 3. g 接近 1 仍收敛
    c99 = Cas(**{**base, "P0": 0.99 / (0.00393 * (1.16 + 2.03))})
    r99 = solve_point(c99)
    check("③ g=0.99 收敛且不误报 COUPLING_FAILED",
          r99["status"] == "CONVERGED",
          f"g={c99.g:.4f} it={r99.get('iterations')} Tw={r99.get('Tw', float('nan')):.2f}")

    # 4. g>=1 返回 COUPLING_FAILED
    c1 = Cas(**{**base, "P0": 1.05 / (0.00393 * (1.16 + 2.03))})
    check("④ g>=1 返回 COUPLING_FAILED",
          solve_point(c1)["status"] == "COUPLING_FAILED", f"g={c1.g:.4f}")

    # 5. g 区间跨越 1 → MODEL_REVIEW；下界已 >=1 → COUPLING_FAILED
    gcrit = 1.0 / (0.00393 * (1.16 + 2.03))
    b1 = BoxCas(name="x", Rws=(1.16, 1.16), Rsa=(2.03, 2.03),
                P0=(gcrit * 0.9, gcrit * 1.1), Pfe=(5.33, 5.33),
                Ta=(40.0, 40.0), alpha=(0.00393, 0.00393))
    check("⑤a g 区间跨 1 → MODEL_REVIEW",
          solve_box(b1)["status"] == "MODEL_REVIEW", solve_box(b1).get("reason", ""))

    b2 = BoxCas(name="x", Rws=(1.16, 1.16), Rsa=(2.03, 2.03),
                P0=(gcrit * 1.1, gcrit * 1.2), Pfe=(5.33, 5.33),
                Ta=(40.0, 40.0), alpha=(0.00393, 0.00393))
    check("⑤b g 下界 >=1 → COUPLING_FAILED",
          solve_box(b2)["status"] == "COUPLING_FAILED", solve_box(b2).get("reason", ""))

    # 6. 区间跨越限值 → MODEL_REVIEW
    b3 = BoxCas(name="x", Rws=(0.9, 1.6), Rsa=(1.8, 2.4), P0=(13.0, 17.0),
                Pfe=(5.0, 6.0), Ta=(38.0, 42.0), alpha=(0.00393, 0.004),
                Tw_max=110.0)
    jb = judge_box(solve_box(b3), b3)
    check("⑥ 区间跨越温度限值 → MODEL_REVIEW",
          jb["status"] == "MODEL_REVIEW", jb["status"])

    # 7. N 在最有利顶点为负 → MODEL_REVIEW
    b4 = BoxCas(name="x", Rws=(1.16, 1.16), Rsa=(2.03, 2.03),
                P0=(0.001, 0.002), Pfe=(0.0, 0.0),
                Ta=(-40.0, -30.0), alpha=(0.00393, 0.00393), Tref=20.0)
    check("⑦ N 最有利顶点为负 → MODEL_REVIEW",
          solve_box(b4)["status"] == "MODEL_REVIEW", solve_box(b4).get("reason", ""))

    # 8. 负残差不抵消正残差
    cal = calibrate([3.0, -10.0, 1.0], gamma=2.0)
    check("⑧ 负残差不抵消正残差（Δcal=6）", abs(cal["delta_cal"] - 6.0) < 1e-9,
          f"delta_cal={cal['delta_cal']}")

    # 9. 顶点求值与蒙特卡洛对比
    import random
    random.seed(7)
    b5 = BoxCas(name="x", Rws=(0.9, 1.45), Rsa=(1.5, 2.6), P0=(13.0, 17.0),
                Pfe=(4.0, 6.5), Ta=(25.0, 45.0), alpha=(0.00393, 0.004))
    r5 = solve_box(b5)
    inside = True
    for _ in range(3000):
        s = Cas(name="mc",
                Rws=random.uniform(*b5.Rws), Rsa=random.uniform(*b5.Rsa),
                P0=random.uniform(*b5.P0), Pfe=random.uniform(*b5.Pfe),
                Ta=random.uniform(*b5.Ta), alpha=random.uniform(*b5.alpha))
        rr = solve_point(s)
        if rr["status"] == "CONVERGED":
            if not (r5["Tw_lo"] - 1e-6 <= rr["Tw"] <= r5["Tw_hi"] + 1e-6):
                inside = False
                break
    check("⑨ 蒙特卡洛抽样落在 [Tw_lo, Tw_hi] 内", inside,
          f"[{r5['Tw_lo']:.2f}, {r5['Tw_hi']:.2f}]")

    # 10-13：三节点扩展与模型失效阈值
    c2 = Cas(**base)
    r2 = solve_point(c2)
    c3_0 = Cas3(**base, Peddy=0.0)
    r3_0 = solve_three(c3_0)
    check("⑩ 三节点 Peddy=0 退化为两热阻",
          abs(r3_0["Tw"] - r2["Tw"]) < 1e-9,
          f"{r2['Tw']:.6f} vs {r3_0['Tw']:.6f}")

    c3_p = Cas3(**base, Peddy=15.24)
    r3_p = solve_three(c3_p)
    check("⑪ 转子损耗抬高绕组温度",
          r3_p["Tw"] > r2["Tw"] + 10.0,
          f"{r2['Tw']:.2f} → {r3_p['Tw']:.2f}℃ (+{r3_p['Tw'] - r2['Tw']:.2f}K)")

    check("⑫ Tr = Ts + Rrs·Peddy",
          abs(r3_p["Tr"] - (r3_p["Ts"] + c3_p.Rrs * c3_p.Peddy)) < 1e-9,
          f"Tr={r3_p['Tr']:.2f}℃ Ts={r3_p['Ts']:.2f}℃")

    c3_big = Cas3(**base, Peddy=200.0)
    j_big = judge_three(solve_three(c3_big), c3_big)
    check("⑬ 超 1.5× 限值 → linear_model_out_of_validity",
          "linear_model_out_of_validity" in j_big["reasons"],
          f"Tw={j_big.get('Tw_corrected')}℃ → {j_big['status']}")

    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------


def run_case(tag: str, c: Cas, b: Optional[BoxCas]) -> Dict[str, Any]:
    r = solve_point(c)
    a = solve_analytic(c)
    cal = calibrate([], gamma=2.0)      # M1：尚无外部校准点
    verdict = judge_point(r, c, cal["delta_cal"])
    str_res = stress_test(c, verdict["status"], cal["delta_cal"])

    out: Dict[str, Any] = {
        "case": c.name,
        "inputs": {"Rws": c.Rws, "Rsa": c.Rsa, "Req": round(c.Req, 4),
                   "P0": c.P0, "Pfe": c.Pfe, "Ta": c.Ta, "Tref": c.Tref,
                   "alpha": c.alpha, "Tw_max": c.Tw_max,
                   "temperature_type": c.temperature_type},
        "coupling": {"status": r["status"], "g": round(c.g, 4),
                     "iterations": r.get("iterations"),
                     "error_bound_K": r.get("error_bound_K")},
        "temperature": {"Tw_point": round(r["Tw"], 3) if r["status"] == "CONVERGED" else None,
                        "Tw_analytic": round(a, 3) if a is not None else None},
        "calibration": cal,
        "verdict": verdict,
        "stress_tests": str_res,
    }

    if b is not None:
        rb = solve_box(b)
        vb = judge_box(rb, b, cal["delta_cal"])
        out["box"] = {
            "inputs": {"Rws": b.Rws, "Rsa": b.Rsa, "P0": b.P0, "Pfe": b.Pfe,
                       "Ta": b.Ta, "alpha": b.alpha},
            "coupling": rb,
            "verdict": vb,
        }

    # 未完成外部校准时，总体结论必须降级（MDCA 6.1）
    overall = verdict["status"]
    reasons = list(verdict.get("reasons", []))
    if not cal["valid"]:
        if overall in ("POINT_PASS", "BOX_PASS"):
            overall = "MODEL_REVIEW"
            reasons.append("calibration_missing")
    if str_res["decision_flipped"]:
        overall = "MODEL_REVIEW"
        reasons.append("decision_flipped_under_stress")
    out["audit"] = {"overall_status": overall, "reasons": reasons}
    out["disclaimer"] = "Model-level audit only; not physical validation."
    return out


def print_case(o: Dict[str, Any]) -> None:
    c = o["inputs"]
    print("-" * 78)
    print(f"算例：{o['case']}")
    print(f"  输入：Rws={c['Rws']}  Rsa={c['Rsa']}  Req={c['Req']}  "
          f"P0={c['P0']}W  Pfe={c['Pfe']}W  Ta={c['Ta']}℃  Tw_max={c['Tw_max']}℃")
    cp = o["coupling"]
    print(f"  耦合：{cp['status']}  g={cp['g']}  iters={cp['iterations']}  "
          f"err_bound={cp['error_bound_K']:.2e}K" if cp.get("error_bound_K") is not None
          else f"  耦合：{cp['status']}  g={cp['g']}")
    t = o["temperature"]
    print(f"  温度：迭代 {t['Tw_point']}℃ ／ 解析 {t['Tw_analytic']}℃"
          f"  （差 {abs(t['Tw_point'] - t['Tw_analytic']):.2e}K）")
    v = o["verdict"]
    print(f"  判定：{v['status']}  {v.get('reasons', '')}")
    st = o["stress_tests"]
    bad = [k for k, val in st["results"].items() if val != "ok"]
    print(f"  压力测试：{len(st['results'])} 项，超限 {len(bad)} 项"
          + (f" → {bad}" if bad else ""))
    if "box" in o:
        bx = o["box"]
        if bx["coupling"]["status"] == "CONVERGED":
            print(f"  区间：Tw ∈ [{bx['coupling']['Tw_lo']}, {bx['coupling']['Tw_hi']}]℃ "
                  f"（g ∈ [{bx['coupling']['g_lo']}, {bx['coupling']['g_hi']}]）")
        else:
            print(f"  区间：{bx['coupling']['status']} — {bx['coupling'].get('reason','')}")
        print(f"  区间判定：{bx['verdict']['status']}")
    print(f"  总体结论：{o['audit']['overall_status']}  {o['audit']['reasons']}")


def main(argv: List[str]) -> int:
    if "--selftest" in argv:
        return 0 if run_selftest() else 1

    print("=" * 78)
    print("MDCA 两热阻审计报告  v0.1")
    print("=" * 78)
    results = []
    for tag, c, b in joint_module_cases():
        o = run_case(tag, c, b)
        print_case(o)
        results.append(o)
    print("-" * 78)

    out_dir = Path(__file__).resolve().parent.parent / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "thermal_audit.json"
    path.write_text(json.dumps(
        {"model_version": "two-resistance-steady-v0.1",
         "calibration": results[0]["calibration"],
         "cases": results},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON 报告：{path}")
    print("提示：本报告为模型层审计；无外部校准点时，PASS 一律降级为 MODEL_REVIEW。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
