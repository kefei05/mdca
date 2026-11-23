#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
路线图第 4 步：A 路审计器五大真实数据集回测
============================================

回测对象是 `mdca.py` 的 A 路（默认，不启用任何 B 路插件）+ `mdca_inverter.py`
的自洽性判据集。每个回测集都用**原始文献 / 实验 / 工程记录里的真实数字**，
断言审计器必须命中预期的判据（或必须保持沉默）。

    集 1  joint-module 电磁-热全链路真实事故（6 个子算例）
          λpm 4 倍抵消陷阱 / 8 匝母线余量 / 匝数-电阻 N² / 2D 铜损移交 /
          三种效率分母 / 铁损误乘 Req
    集 2  AI 组 10 个外部模型方案（cases_ai/AI01~AI10）
          10/10 必须命中「铜损漏乘 Rsa 违反 KCL + 转子挂在 Tw 上」
    集 3  Energies 2021（4 kW TEFC）
          三热阻并联合成 / 错误串联反例 / Rfc 随频率单调下降
    集 4  AIP Advances 2023（24/6 极 SPM 五功率点）
          最大正残差 16.5 K / 8.8 kW 漏报点
    集 5  Motor-CAD EV_5 液冷电机
          5A 五温度点节点反推（Rws 稳定 / Rsa 降 63% / 非单调）
          5B 七个环境温度点（固定 Rsa 模型低估 62.7 K / 7 点全漏报）

用法：
    python road_a_backtest.py            # 跑全部，写 out/road_a_backtest.json
    python road_a_backtest.py -v         # 同时打印每条断言的细节
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from mdca import audit, load_case, parse_case_doc  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT = HERE / "out"
RESULT_PATH = OUT / "road_a_backtest.json"


# --------------------------------------------------------------------------
# 断言收集
# --------------------------------------------------------------------------


class Group:
    """一个回测集。"""

    def __init__(self, key: str, title: str):
        self.key = key
        self.title = title
        self.checks: List[Dict[str, Any]] = []
        self.numbers: Dict[str, Any] = {}

    def check(self, label: str, ok: bool, detail: Any = "") -> bool:
        self.checks.append({"label": label, "ok": bool(ok),
                            "detail": str(detail)[:400]})
        return bool(ok)

    def near(self, label: str, got: Optional[float], want: float,
             tol: float, unit: str = "") -> bool:
        ok = got is not None and abs(float(got) - want) <= tol
        return self.check(label, ok, f"got={got}{unit} want={want}±{tol}")

    def record(self, **kw: Any) -> None:
        self.numbers.update(kw)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c["ok"] for c in self.checks)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c["ok"])

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "ok": self.ok,
            "passed": self.passed,
            "total": len(self.checks),
            "checks": self.checks,
            "numbers": self.numbers,
        }


# --------------------------------------------------------------------------
# 构造 case 的辅助
# --------------------------------------------------------------------------


def P(v: float, unit: str, method: str = "analytic", **extra: Any) -> Dict[str, Any]:
    pal: Dict[str, Any] = {"method": method}
    pal.update(extra)
    return {"value": v, "unit": unit, "palette": pal, "verified": True,
            "source": "回测输入（见脚本注释）"}


def make_case(
    case_id: str,
    params: Dict[str, Any],
    *,
    limits: Optional[Dict[str, Any]] = None,
    reported: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
    operating_points: Optional[Sequence[Dict[str, Any]]] = None,
    topology_spec: Optional[Dict[str, Any]] = None,
) -> Any:
    """构造最小合法 case：Pcu/Pfe/Ta 三个必需参数若没给就补上（不影响被测判据）。"""
    full = {
        "Pcu": P(13.6, "W"),
        "Pfe": P(5.33, "W"),
        "Ta": P(40.0, "C", "design_target"),
    }
    full.update(params)
    raw: Dict[str, Any] = {
        "schema": "1.0",
        "case_id": case_id,
        "description": f"回测 {case_id}",
        "params": full,
        "limits": limits or {"Tw_max": 155.0},
    }
    if reported:
        raw["reported"] = dict(reported)
    if meta:
        raw["meta"] = dict(meta)
    if operating_points:
        raw["operating_points"] = [dict(p) for p in operating_points]
    if topology_spec:
        raw["topology_spec"] = dict(topology_spec)
    return parse_case_doc(raw, f"<backtest>/{case_id}")


def codes_of(out: Dict[str, Any], severity: Optional[str] = None) -> List[str]:
    iss = (out.get("validation") or {}).get("issues") or []
    return sorted({
        str(i.get("code")) for i in iss
        if severity is None or i.get("severity") == severity
    })


def cons_of(out: Dict[str, Any]) -> Dict[str, Any]:
    return out.get("consistency") or {}


def find_issue(out: Dict[str, Any], code: str) -> Optional[Dict[str, Any]]:
    for i in (out.get("validation") or {}).get("issues") or []:
        if i.get("code") == code:
            return i
    return None


def status(out: Dict[str, Any]) -> str:
    return str((out.get("audit") or {}).get("overall_status"))


# --------------------------------------------------------------------------
# 集 1：joint-module 电磁-热全链路真实事故（6 个子算例）
# 数据来源：E:\deepseek\个人基线档案.md §2.2 / §2.2b / §2.2c
# --------------------------------------------------------------------------


def run_joint_module(verbose: bool = False) -> Group:
    g = Group("joint_module", "joint-module 电磁-热全链路真实事故（6 例）")

    # --- JM-1：课设报告 λpm = 8 × 0.933 × 1.8685 的 4 倍抵消陷阱 ---
    case = make_case("JM-1_flux_4x_trap", {
        "N_coil": P(8, "turns"),
        "kw": P(0.933, "1"),
        "Phi_pole": P(1.8685, "mWb"),
        "lambda_pm": P(13.9465, "mWb"),
        "B_delta": P(0.85, "T", "design_target"),
        "alpha_p": P(0.82, "1", "design_target"),
        "tau_p": P(22.18, "mm"),
        "L_stack": P(30.0, "mm"),
    })
    o = audit(case)
    trap = find_issue(o, "C1_flux_turns_trap")
    ratio = (trap or {}).get("detail", {}).get("phi_ratio_to_single_pole")
    g.check("JM-1 命中 C1_flux_turns_trap", trap is not None, codes_of(o))
    g.check("JM-1 指纹 em_flux_turns_cancellation_trap",
            (trap or {}).get("detail", {}).get("fingerprint") == "em_flux_turns_cancellation_trap",
            (trap or {}).get("detail"))
    g.check("JM-1 磁通比值 ≈ 4（3.9~4.2）",
            ratio is not None and 3.9 <= float(ratio) <= 4.2, ratio)
    g.check("JM-1 总体状态 AUDIT_REVIEW", status(o) == "AUDIT_REVIEW", status(o))
    g.record(JM1_phi_ratio=ratio, JM1_status=status(o))

    # --- JM-2：8 匝 @1800 rpm 线反电势占 24 V 母线 97.9%；6 匝 73.4% ---
    o8 = audit(make_case("JM-2a_8turns_bus", {
        "p": P(5, "1"),
        "n_rpm": P(1800, "rpm"),
        "lambda_pm": P(13.9465, "mWb"),
        "E_line_pk": P(23.49, "V"),
        "U_bus": P(24.0, "V", "design_target"),
    }))
    tight = find_issue(o8, "C1_bus_voltage_tight")
    util8 = (tight or {}).get("detail", {}).get("voltage_utilization")
    g.check("JM-2a 8 匝命中 C1_bus_voltage_tight", tight is not None, codes_of(o8))
    g.near("JM-2a 母线占比 97.88%", util8, 0.9788, 0.002)
    g.check("JM-2a 总体状态 AUDIT_REVIEW", status(o8) == "AUDIT_REVIEW", status(o8))

    o6 = audit(make_case("JM-2b_6turns_bus", {
        "p": P(5, "1"),
        "n_rpm": P(1800, "rpm"),
        "lambda_pm": P(10.4599, "mWb"),
        "E_line_pk": P(17.62, "V"),
        "U_bus": P(24.0, "V", "design_target"),
    }))
    g.check("JM-2b 6 匝无母线余量告警",
            "C1_bus_voltage_tight" not in codes_of(o6), codes_of(o6))
    c1d = (cons_of(o6).get("electromagnetic") or {}).get("C1d_bus_voltage_margin") or {}
    g.near("JM-2b 母线占比 73.42%", c1d.get("voltage_utilization"), 0.7342, 0.002)
    g.check("JM-2b 总体状态 AUDIT_PASS", status(o6) == "AUDIT_PASS", status(o6))

    # --- JM-3：8 匝 0.032Ω → 6 匝，按一次方误缩到 0.024Ω（正确 N² 应为 0.018）---
    o = audit(make_case("JM-3_turn_scaling_R", {
        "N_coil": P(6, "turns"),
        "N_ref": P(8, "turns"),
        "R_ref": P(0.032, "Ohm"),
        "R_phase": P(0.024, "Ohm"),
    }))
    bug = find_issue(o, "C2_turn_scaling_linear_bug")
    g.check("JM-3 命中 C2_turn_scaling_linear_bug", bug is not None, codes_of(o))
    g.check("JM-3 指纹 winding_resistance_linear_turn_scaling",
            (bug or {}).get("detail", {}).get("fingerprint") == "winding_resistance_linear_turn_scaling",
            (bug or {}).get("detail"))
    g.near("JM-3 N² 正确值 0.018 Ω",
           (bug or {}).get("detail", {}).get("R_quadratic_theory_Ohm"), 0.018, 1e-4)
    g.check("JM-3 总体状态 AUDIT_BLOCKED", status(o) == "AUDIT_BLOCKED", status(o))

    # --- JM-4：把 2D 不含端部的 7.67 W 当全机铜损移交 ---
    o = audit(make_case("JM-4_2d_copper_handover", {
        "I_rms": P(15.9, "A", "analytic", current_basis="rms"),
        "R_phase": P(0.018, "Ohm"),
        "Pcu": P(7.67, "W", "fem", end_winding="excluded"),
    }))
    mism = find_issue(o, "C2_copper_loss_mismatch")
    g.check("JM-4 命中 C2_copper_loss_mismatch", mism is not None, codes_of(o))
    g.check("JM-4 指纹 copper_loss_2d_excluding_end_winding",
            (mism or {}).get("detail", {}).get("fingerprint") == "copper_loss_2d_excluding_end_winding",
            (mism or {}).get("detail"))
    g.near("JM-4 3I²R 理论值 13.65 W",
           (mism or {}).get("detail", {}).get("Pcu_calc_3I2R_W"), 13.65, 0.05)
    g.check("JM-4 总体状态 AUDIT_BLOCKED", status(o) == "AUDIT_BLOCKED", status(o))

    # --- JM-5：三个效率用了两种分母 ---
    base5 = {"T_out": P(1.774, "N.m"), "n_rpm": P(1800, "rpm"),
             "P_out": P(334.4, "W")}
    o959 = audit(make_case("JM-5a_eta95.9", {
        **base5, "Pcu": P(13.6, "W"), "Pfe": P(0.0, "W"),
        "P_loss": P(13.6, "W"), "eta": P(95.93, "%"),
    }))
    e1 = find_issue(o959, "C3_efficiency_denom_bug")
    g.check("JM-5a 95.9% 命中 C3_efficiency_denom_bug", e1 is not None, codes_of(o959))
    g.check("JM-5a 指纹 efficiency_output_power_as_denominator",
            (e1 or {}).get("detail", {}).get("fingerprint") == "efficiency_output_power_as_denominator",
            (e1 or {}).get("detail"))
    g.near("JM-5a 严格定义真值 96.09%",
           (e1 or {}).get("detail", {}).get("eta_input_denom_pct"), 96.09, 0.03)

    o943 = audit(make_case("JM-5b_eta94.3", {
        **base5, "Pcu": P(13.6, "W"), "Pfe": P(5.33, "W"),
        "P_loss": P(18.93, "W"), "eta": P(94.34, "%"),
    }))
    e2 = find_issue(o943, "C3_efficiency_denom_bug")
    g.check("JM-5b 94.3% 命中 C3_efficiency_denom_bug", e2 is not None, codes_of(o943))
    g.near("JM-5b 严格定义真值 94.64%",
           (e2 or {}).get("detail", {}).get("eta_input_denom_pct"), 94.64, 0.03)

    o907 = audit(make_case("JM-5c_eta90.7", {
        "Pcu": P(13.6, "W"), "Pfe": P(5.33, "W"), "Peddy": P(15.24, "W"),
        "P_out": P(334.4, "W"), "P_loss": P(34.17, "W"), "eta": P(90.73, "%"),
    }))
    g.check("JM-5c 90.7% 用输入功率分母，无 C3 误报",
            "C3_efficiency_denom_bug" not in codes_of(o907), codes_of(o907))
    g.check("JM-5c 总体状态 AUDIT_PASS", status(o907) == "AUDIT_PASS", status(o907))

    # --- JM-6：早期热算把 Pfe 也乘了 Req，算得 116.91℃（真值 109.46℃）---
    o = audit(make_case("JM-6_pfe_through_rws", {
        "Rws": P(1.16, "K/W"),
        "Rsa": P(2.03, "K/W"),
    }, reported={"Tw": 116.91}))
    kcl = find_issue(o, "I1_kcl_violation")
    i1 = (cons_of(o).get("thermal_single") or {}).get("I1_kcl") or {}
    g.check("JM-6 命中 I1_kcl_violation", kcl is not None, codes_of(o))
    g.check("JM-6 指纹 kcl_pfe_through_rws",
            i1.get("Tw_fingerprint") == "kcl_pfe_through_rws", i1.get("Tw_fingerprint"))
    g.near("JM-6 KCL 真解 109.46℃", i1.get("Tw_kcl_true_C"), 109.46, 0.03, "℃")
    g.near("JM-6 残差 +7.45 K", i1.get("Tw_residual_K"), 7.45, 0.03, "K")

    if verbose:
        for c in g.checks:
            print(f"      [{'ok' if c['ok'] else 'FAIL'}] {c['label']}  {c['detail']}")
    return g


# --------------------------------------------------------------------------
# 集 2：AI 组 10 个外部模型方案
# --------------------------------------------------------------------------


def run_ai_group(verbose: bool = False) -> Group:
    g = Group("ai_group", "AI 组 10 个外部模型方案（cases_ai/AI01~AI10）")
    files = sorted((HERE / "cases_ai").glob("AI*.json"))
    if not files:
        g.check("找到 cases_ai/AI*.json", False, "目录为空")
        return g

    g.check(f"找到 {len(files)} 个 AI 方案", len(files) == 10, len(files))

    hit_status, hit_kcl, hit_tw_fp, hit_tr_fp = 0, 0, 0, 0
    tw_diffs: List[float] = []
    tr_diffs: List[float] = []
    rows: List[Dict[str, Any]] = []

    for f in files:
        o = audit(load_case(f))                       # 默认 A 路
        i1 = (cons_of(o).get("thermal_single") or {}).get("I1_kcl") or {}
        s = status(o)
        has_kcl = "I1_kcl_violation" in codes_of(o)
        tw_fp = i1.get("Tw_fingerprint")
        tr_fp = i1.get("Tr_fingerprint")
        tw_d = i1.get("Tw_fingerprint_diff_K")
        tr_d = i1.get("Tr_fingerprint_diff_K")

        hit_status += int(s == "AUDIT_BLOCKED")
        hit_kcl += int(has_kcl)
        hit_tw_fp += int(tw_fp == "kcl_missing_pcu_in_rsa")
        hit_tr_fp += int(tr_fp == "topo_rotor_on_winding")
        if isinstance(tw_d, (int, float)):
            tw_diffs.append(float(tw_d))
        if isinstance(tr_d, (int, float)):
            tr_diffs.append(float(tr_d))
        rows.append({
            "case": f.stem, "status": s,
            "I1_kcl": has_kcl, "Tw_fingerprint": tw_fp, "Tr_fingerprint": tr_fp,
            "Tw_reported": i1.get("Tw_reported_C"),
            "Tw_kcl_true": i1.get("Tw_kcl_true_C"),
            "Tw_residual_K": i1.get("Tw_residual_K"),
            "tw_fp_diff_K": tw_d, "tr_fp_diff_K": tr_d,
        })

    n = len(files)
    g.check(f"{n}/{n} 状态 AUDIT_BLOCKED", hit_status == n, hit_status)
    g.check(f"{n}/{n} 命中 I1_kcl_violation", hit_kcl == n, hit_kcl)
    g.check(f"{n}/{n} 绕组指纹 kcl_missing_pcu_in_rsa", hit_tw_fp == n, hit_tw_fp)
    g.check(f"{n}/{n} 转子指纹 topo_rotor_on_winding", hit_tr_fp == n, hit_tr_fp)
    g.check("绕组指纹匹配残差全部 ≤ 0.005 K",
            bool(tw_diffs) and max(tw_diffs) <= 0.005,
            f"max={max(tw_diffs) if tw_diffs else None}")
    g.check("转子指纹匹配残差全部 ≤ 0.005 K",
            bool(tr_diffs) and max(tr_diffs) <= 0.005,
            f"max={max(tr_diffs) if tr_diffs else None}")
    g.record(rows=rows,
             max_tw_fp_diff_K=max(tw_diffs) if tw_diffs else None,
             max_tr_fp_diff_K=max(tr_diffs) if tr_diffs else None)

    if verbose:
        for r in rows:
            print(f"      {r['case']} {r['status']:<14} "
                  f"Tw {r['Tw_reported']}→真解 {r['Tw_kcl_true']} "
                  f"残差 {r['Tw_residual_K']}  指纹 {r['Tw_fingerprint']} / {r['Tr_fingerprint']}")
    return g


# --------------------------------------------------------------------------
# 集 3：Energies 2021（4 kW TEFC）
# --------------------------------------------------------------------------


def run_energies(verbose: bool = False) -> Group:
    g = Group("energies_2021", "Energies 2021（4 kW TEFC）三热阻与频率相关性")

    # --- 3.1 三热阻并联合成 ---
    spec = {"Req_w": 0.07, "Req_sr": 0.382, "Rfc": 0.167, "Rew_a": 0.446,
            "claimed_Rsa": 0.1162, "Np": 4, "Ks": 0.785, "alpha_ew": 0.50}
    o = audit(make_case("EN-1_topology", {}, topology_spec=spec))
    topo = cons_of(o).get("topology") or {}
    g.check("EN-1 无 BLOCK（拓扑合成自洽）",
            not [c for c in codes_of(o, "BLOCK")], codes_of(o, "BLOCK"))
    g.near("EN-1 R_eq,sr ∥ R_fc = 0.1162 K/W",
           topo.get("Rsa_parallel_sr_fc_KpW"), 0.1162, 5e-4, "K/W")
    g.near("EN-1 Rws 占比 = 37.6%（风冷档指纹）",
           topo.get("Rws_share_in_rotating"), 0.376, 1e-3)
    g.near("EN-1 端部铜损占比 α(Eq.4) = 0.50",
           topo.get("alpha_ew_eq4"), 0.50, 2e-3)
    g.record(EN1_topology=topo)

    # --- 3.2 错误拓扑反例：把并联误当串联 0.382+0.167=0.549 ---
    o = audit(make_case("EN-2_wrong_series", {}, topology_spec={
        "Req_w": 0.07, "Req_sr": 0.382, "Rfc": 0.167, "claimed_Rsa": 0.549}))
    bad = find_issue(o, "I4_topology_reduction_mismatch")
    g.check("EN-2 串联误用命中 I4_topology_reduction_mismatch", bad is not None, codes_of(o))
    g.check("EN-2 总体状态 AUDIT_BLOCKED", status(o) == "AUDIT_BLOCKED", status(o))

    # --- 3.3 R_fc 随频率单调下降（Table 3）---
    pts = [{"x": 10, "R": 0.960}, {"x": 20, "R": 0.334},
           {"x": 30, "R": 0.226}, {"x": 50, "R": 0.167}]
    o = audit(make_case("EN-3_rfc_vs_freq", {}, operating_points=pts))
    mp = cons_of(o).get("multipoint") or {}
    ser = mp.get("resistance_series") or {}
    g.check("EN-3 R_fc 常值交集为空（5.75×）",
            ser.get("set_intersection_non_empty") is False, ser)
    g.near("EN-3 最大/最小 = 5.75×", ser.get("ratio_max_to_min"), 5.75, 0.02)
    g.near("EN-3 Spearman ρ = -1.0（严格单调）", ser.get("spearman_vs_x"), -1.0, 1e-6)
    g.check("EN-3 命中 I3_speed_dependent_resistance",
            "I3_speed_dependent_resistance" in codes_of(o), codes_of(o))
    g.record(EN3_series=ser)

    if verbose:
        for c in g.checks:
            print(f"      [{'ok' if c['ok'] else 'FAIL'}] {c['label']}  {c['detail']}")
    return g


# --------------------------------------------------------------------------
# 集 4：AIP Advances 2023 五个功率点
# --------------------------------------------------------------------------


def run_aip(verbose: bool = False) -> Group:
    g = Group("aip_2023", "AIP Advances 2023（24/6 极 SPM）五功率点计算 vs 实验")
    pts = [{"P_kW": 5.5, "Tw_calc": 106.5, "Tw_exp": 112.0},
           {"P_kW": 6.4, "Tw_calc": 118.0, "Tw_exp": 134.5},
           {"P_kW": 7.4, "Tw_calc": 133.0, "Tw_exp": 141.0},
           {"P_kW": 8.4, "Tw_calc": 147.0, "Tw_exp": 152.0},
           {"P_kW": 8.8, "Tw_calc": 151.0, "Tw_exp": 158.5}]
    o = audit(make_case("AIP-1_five_points", {},
                        limits={"Tw_max": 155.0}, operating_points=pts))
    mvr = (cons_of(o).get("multipoint") or {}).get("model_vs_reference") or {}

    g.near("AIP 最大正残差 16.5 K", mvr.get("max_underestimation_K"), 16.5, 0.01, "K")
    worst = mvr.get("worst_point") or {}
    g.check("AIP 最不利点出现在 6.4 kW",
            abs(float(worst.get("x", 0)) - 6.4) < 1e-6, worst.get("x"))
    g.near("AIP 按 γ=2 应取 Δ_cal = 33 K",
           mvr.get("required_delta_cal_gamma2_K"), 33.0, 0.02, "K")
    g.check("AIP 残差非单调（方向翻转 ≥ 1 次）",
            int(mvr.get("residual_direction_flips") or 0) >= 1,
            mvr.get("residual_direction_flips"))
    g.check("AIP 检出 1 个漏报点（8.8 kW）",
            mvr.get("missed_violation_count") == 1, mvr.get("missed_violation_count"))
    g.check("AIP 命中 I3_missed_violation_detected",
            "I3_missed_violation_detected" in codes_of(o), codes_of(o))
    g.check("AIP 命中 I3_systematic_underestimation",
            "I3_systematic_underestimation" in codes_of(o), codes_of(o))
    g.check("AIP 总体状态 AUDIT_BLOCKED", status(o) == "AUDIT_BLOCKED", status(o))
    g.record(AIP_model_vs_ref=mvr)

    if verbose:
        for c in g.checks:
            print(f"      [{'ok' if c['ok'] else 'FAIL'}] {c['label']}  {c['detail']}")
    return g


# --------------------------------------------------------------------------
# 集 5：Motor-CAD EV_5 液冷电机
# --------------------------------------------------------------------------


def run_motorcad(verbose: bool = False) -> Group:
    g = Group("motorcad_ev5", "Motor-CAD EV_5 液冷电机（节点扫描 + 环境温度盲测）")

    # --- 5A：五温度点节点反推 ---
    pts5 = [
        {"Ta": 45, "Ts": 96.82, "Tw": 160.43, "Pcu": 572.17, "Ptot": 705.42},
        {"Ta": 65, "Ts": 92.34, "Tw": 155.50, "Pcu": 571.49, "Ptot": 704.84},
        {"Ta": 85, "Ts": 108.08, "Tw": 172.81, "Pcu": 573.97, "Ptot": 707.00},
        {"Ta": 105, "Ts": 126.93, "Tw": 193.52, "Pcu": 576.59, "Ptot": 709.31},
        {"Ta": 125, "Ts": 147.90, "Tw": 218.94, "Pcu": 707.69, "Ptot": 853.03},
    ]
    o4 = audit(make_case("MC-1a_four_points", {}, operating_points=pts5[:4]))
    mp4 = cons_of(o4).get("multipoint") or {}
    rws4 = mp4.get("Rws_summary") or {}
    rsa4 = mp4.get("Rsa_summary") or {}
    g.check("MC-5A 四点：Rws 可行区间交集非空（可当常数）",
            rws4.get("set_intersection_non_empty") is True, rws4)
    g.check("MC-5A 四点：Rws 极差仅 4.4%", float(rws4.get("relative_span") or 1) < 0.06,
            rws4.get("relative_span"))
    g.check("MC-5A 四点：Rsa 可行区间交集为空（不可当常数）",
            rsa4.get("set_intersection_non_empty") is False, rsa4)

    o5 = audit(make_case("MC-1b_five_points", {}, operating_points=pts5))
    mp5 = cons_of(o5).get("multipoint") or {}
    rsa5 = mp5.get("Rsa_summary") or {}
    pts_rows = mp5.get("points") or []
    rsa_45 = (pts_rows[0] or {}).get("Rsa_KpW") if pts_rows else None
    rsa_125 = (pts_rows[-1] or {}).get("Rsa_KpW") if pts_rows else None
    g.near("MC-5A 五点：Rsa@45℃ = 0.07346", rsa_45, 0.07346, 5e-5, "K/W")
    g.near("MC-5A 五点：Rsa@125℃ = 0.02685", rsa_125, 0.02685, 5e-5, "K/W")
    if rsa_45 and rsa_125:
        g.check("MC-5A 五点：Rsa 下降 63%",
                abs((rsa_125 / rsa_45) - 0.3655) < 0.01,
                f"ratio={rsa_125 / rsa_45:.4f}")
    g.near("MC-5A 五点：Rsa 与 Ta 的 Spearman ρ = -1.0",
           rsa5.get("spearman_vs_Ta"), -1.0, 1e-6)
    g.check("MC-5A 命中 I3_rsa_non_constant",
            "I3_rsa_non_constant" in codes_of(o5), codes_of(o5))
    g.check("MC-5A 检出非单调温升（45→65℃ 冷却液升温而器件降温）",
            mp5.get("non_monotonic_temperature_response") is True,
            mp5.get("non_monotonic_temperature_response"))
    g.check("MC-5A 命中 I3_non_monotonic_response",
            "I3_non_monotonic_response" in codes_of(o5), codes_of(o5))
    g.record(MC5A_four_rws=rws4, MC5A_five_rsa=rsa5)

    # --- 5B：七个环境温度点（固定 Rsa 模型 vs Motor-CAD 实测）---
    pts7 = [{"Ta": 20, "Tw_calc": 97.9, "Tw_exp": 160.6},
            {"Ta": 25, "Tw_calc": 104.3, "Tw_exp": 165.9},
            {"Ta": 30, "Tw_calc": 110.7, "Tw_exp": 171.3},
            {"Ta": 35, "Tw_calc": 117.1, "Tw_exp": 174.7},
            {"Ta": 45, "Tw_calc": 129.9, "Tw_exp": 164.6},
            {"Ta": 50, "Tw_calc": 136.3, "Tw_exp": 161.5},
            {"Ta": 55, "Tw_calc": 142.7, "Tw_exp": 159.0}]
    o = audit(make_case("MC-2_seven_ta", {},
                        limits={"Tw_max": 155.0}, operating_points=pts7))
    mvr = (cons_of(o).get("multipoint") or {}).get("model_vs_reference") or {}
    g.near("MC-5B 最大正残差 62.7 K（Ta=20℃）",
           mvr.get("max_underestimation_K"), 62.7, 0.01, "K")
    worst = mvr.get("worst_point") or {}
    g.check("MC-5B 最不利点出现在 Ta=20℃",
            abs(float(worst.get("x", 0)) - 20) < 1e-6, worst.get("x"))
    g.near("MC-5B 残差随 Ta 的 Spearman ρ = -1.0（越远校准点越严重）",
           mvr.get("residual_spearman_vs_x"), -1.0, 1e-6)
    g.check("MC-5B 7/7 点全部漏报（点值 ≤155℃ 而实测 >155℃）",
            mvr.get("missed_violation_count") == 7, mvr.get("missed_violation_count"))
    g.check("MC-5B 命中 I3_missed_violation_detected",
            "I3_missed_violation_detected" in codes_of(o), codes_of(o))

    # +33 K 校准裕度后仍有 4 点漏报
    moved = [{"Ta": p["Ta"], "Tw_calc": p["Tw_calc"] + 33.0, "Tw_exp": p["Tw_exp"]}
             for p in pts7]
    o2 = audit(make_case("MC-2b_seven_ta_plus33", {},
                         limits={"Tw_max": 155.0}, operating_points=moved))
    mvr2 = (cons_of(o2).get("multipoint") or {}).get("model_vs_reference") or {}
    g.check("MC-5B +33 K 裕度后仍剩 4 点漏报",
            mvr2.get("missed_violation_count") == 4, mvr2.get("missed_violation_count"))
    g.record(MC5B_seven=mvr, MC5B_plus33=mvr2)

    if verbose:
        for c in g.checks:
            print(f"      [{'ok' if c['ok'] else 'FAIL'}] {c['label']}  {c['detail']}")
    return g


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def main(argv: List[str]) -> int:
    verbose = "-v" in argv or "--verbose" in argv
    OUT.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("MDCA A 路回测（路线图第 4 步）：五大真实数据集")
    print("=" * 78)

    builders = [
        ("集 1", run_joint_module),
        ("集 2", run_ai_group),
        ("集 3", run_energies),
        ("集 4", run_aip),
        ("集 5", run_motorcad),
    ]

    groups: List[Group] = []
    for tag, fn in builders:
        print()
        print(f"--- {tag} ---")
        try:
            g = fn(verbose=verbose)
        except Exception as e:  # noqa: BLE001
            g = Group(tag, f"{tag} 执行异常")
            g.check(f"{tag} 执行未抛异常", False, f"{type(e).__name__}: {e}")
        groups.append(g)
        for c in g.checks:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['label']}")
        print(f"  → {g.passed}/{len(g.checks)} 通过")

    print()
    print("=" * 78)
    total = sum(len(g.checks) for g in groups)
    passed = sum(g.passed for g in groups)
    all_ok = all(g.ok for g in groups)
    print(f"汇总：{passed}/{total} 条断言通过，{len(groups)} 个回测集"
          f"{'全部通过' if all_ok else '有失败项'}")
    for g in groups:
        print(f"  {g.key:<16}{g.passed}/{len(g.checks):<4}"
              f"{'OK' if g.ok else 'FAIL'}   {g.title}")
    print("=" * 78)

    payload = {
        "schema": "1.0",
        "title": "MDCA A 路五大真实数据集回测",
        "generated_by": "road_a_backtest.py",
        "all_ok": all_ok,
        "passed": passed,
        "total": total,
        "groups": [g.as_dict() for g in groups],
    }
    RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"JSON：{RESULT_PATH}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
