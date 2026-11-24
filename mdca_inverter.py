#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA A 路核心引擎：反推器 + 电磁-热全链路自洽性判据集 + 矛盾定位
================================================================

定位（`项目复盘与路线转向.md` §4 & §6）：

    A 路审计器不产生数据，只用「对方的数检验对方的数」。
    本模块把两类物理恒等式与文献有界误差方法写成纯审计判据：

    引擎 A · 电磁与功率链闭合审计（EM & Power Chain Closure）
      C1_em_triangle         磁链 λpm ↔ N_series·kw·Φ_pole（抓 4 倍抵消陷阱）
                             转矩常数 Kt ↔ 1.5·p·λpm（抓 RMS/Peak √2 倍混用）
                             线反电势 E_line_pk ↔ √3·ωe·λpm 与母线电压余量 E/U_bus
      C2_winding_copper      三相铜损 Pcu ↔ 3·I_rms²·R_phase
                             固定槽满率改匝数 R2/R1 ↔ (N2/N1)²（抓线性误缩放）
                             2D/3D 铜损比 vs Energies 2021 §4 端部形状因子 α
                             槽满率三口径分离（净铜/包络 vs 净铜/槽 vs 含漆/槽）
      C3_power_efficiency    机械功率 P_out ↔ T_out·ωm
                             效率分母闭合 η ↔ 1 - P_loss/P_in（抓误用 P_out 作分母）

    引擎 B · 热网络反推与多工况有界误差自洽性审计（Thermal Inversion & Set-Membership）
      I1_thermal_kcl         当同时给出 (R, P, T) 时，检验节点 KCL 与热反馈方程残差；
                             自动诊断 6 种典型错法指纹：
                               · kcl_missing_pcu_in_rsa   铜损漏乘 Rsa（违反节点 KCL）
                               · topo_rotor_on_winding    转子热阻 Rrs 误挂在绕组 Tw 上
                               · unconverged_cold         冷态铜损直代，未做温度修正
                               · unconverged_1step        热反馈只代一次未迭代到不动点
                               · kcl_pfe_through_rws      铁损错误流经绕组热阻 Rws
                               · kcl_missing_peddy        三节点参数齐全但定子漏计 Peddy
      I2_implied_resistance  从 (P, T) 反推隐含热阻 R_imp（有 Ts 解耦反推，无 Ts 给严格上下界）
                             检验 R > 0、热反馈增益 g < 1；
                             用 Boglietti 2008 Eq.(2) R0 = 0.167/A_case 标出隐含散热倍数
      I3_multipoint_set      多工况有界误差集合成员反推（Ragot & Maquin 2005 / Raïssi 2005）：
                             求各工况可行热阻区间 [R_lo, R_hi] 的交集 ∩ S_k；
                             交集为空即严格证伪常系数假设，计算最小一致残差 ΔT_min
                             与单调漂移相关系数（区分温度/频率非线性 vs 局部模型失效）
      I4_topology_reduction  串并联支路合成与 Energies 2021 端部二阶拓扑自洽性检验

文献依据：
  [1] A. Boglietti et al., "Determination of Critical Parameters in Electrical
      Machine Thermal Models," IEEE Trans. Ind. Appl., 44(4):1150-1159, 2008.
  [2] G.ier et al., "Stator Winding Second-Order Thermal Model including
      End-Winding Thermal Effects," Energies, 14(20):6578, 2021.
  [3] O. Wallscheid & J. Böcker, "Global Identification of a Low-Order LPTN
      for PMSMs," IEEE Trans. Energy Convers., 31(1):354-365, 2016.
  [4] J. Ragot & D. Maquin, "Data validation and diagnosis using interval
      analysis," J. Process Control / ACD, 2005.
  [5] T. Raïssi, N. Ramdani, Y. Candau, "Interval analysis for guaranteed
      non-linear parameter and state estimation," MCMDS, 11(1):75-92, 2005.

用法：
    python mdca_inverter.py selftest
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------
# 物理常数与文献参考先验（Boglietti 2008 / Energies 2021）
# 注：量级表仅作 INFO/REVIEW 参考，不作无几何硬拦截（复盘 §8 第三条）
# --------------------------------------------------------------------------

ALPHA_CU_DEFAULT = 0.00393          # 铜电阻温度系数 1/K (@20℃)
TREF_DEFAULT = 20.0                 # 默认参考温度 ℃

# Boglietti 2008 Eq.(2): R0 = 0.167 / A_housing (m²), 等效 h0 = 5.95 W/(m²·K)
BOGLIETTI_R0_COEFF = 0.167          # K·m²/W
BOGLIETTI_H0 = 5.95                 # W/(m²·K)

# 冷却方式对应的等效换热系数相对自然对流 h0 的典型倍数范围（参考量级，非硬判据）
COOLING_MULTIPLIER_REF: Dict[str, Tuple[float, float]] = {
    "natural": (0.5, 2.2),          # 裸机壳自然对流+辐射
    "body_conduction": (2.0, 12.0), # 关节/法兰安装面金属导热
    "forced_air": (2.5, 15.0),      # 强迫风冷 TEFC
    "water_jacket": (12.0, 200.0),  # 机壳水套 / 液冷
}

# Boglietti 2008 §III-E: 定子-机壳过盈配合等效气隙 [0.01, 0.10] mm
BOGLIETTI_GAP_MM_RANGE = (0.01, 0.10)
# 绕组-铁芯等效导热系数参考范围 W/(m·K)：普通浸漆 [0.06, 0.35]，高性能灌封可达 1.8
LAMBDA_EFF_REF_RANGE = (0.055, 1.80)


@dataclass
class AuditFinding:
    """一条自洽性审计发现，可直接转为 mdca.Issue。"""
    code: str
    severity: str          # BLOCK / REVIEW / WARN / INFO
    message: str
    params: Tuple[str, ...] = ()
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "params": self.params,
            "detail": self.detail,
        }


# --------------------------------------------------------------------------
# 辅助：从 Case 或原始 dict 提取数值与口径
# --------------------------------------------------------------------------


def _extract_val(params: Mapping[str, Any], key: str) -> Optional[float]:
    if key not in params or params[key] is None:
        return None
    obj = params[key]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return float(obj)
    if hasattr(obj, "value"):
        return float(obj.value)
    if isinstance(obj, Mapping) and "value" in obj:
        return float(obj["value"])
    return None


def _extract_unit(params: Mapping[str, Any], key: str) -> str:
    if key not in params or params[key] is None:
        return ""
    obj = params[key]
    if hasattr(obj, "unit"):
        return str(obj.unit or "")
    if isinstance(obj, Mapping):
        return str(obj.get("unit") or "")
    return ""


def _extract_palette(params: Mapping[str, Any], key: str) -> Dict[str, Any]:
    if key not in params or params[key] is None:
        return {}
    obj = params[key]
    pal = getattr(obj, "palette", None)
    if pal is not None:
        if hasattr(pal, "as_dict"):
            return pal.as_dict()
        if isinstance(pal, Mapping):
            return dict(pal)
    if isinstance(obj, Mapping) and isinstance(obj.get("palette"), Mapping):
        return dict(obj["palette"])
    return {}


def _to_si(val: Optional[float], unit: str, target: str) -> Optional[float]:
    """常用电磁量单位归一化：mWb->Wb, mN.m->N.m, mN.m/A->N.m/A, mOhm->Ohm, mm²->m²。"""
    if val is None:
        return None
    u = unit.strip().replace("·", ".").replace("Ω", "Ohm").replace("ω", "Ohm")
    if target == "Wb":
        return val * 1e-3 if u in ("mWb", "") else val
    if target == "N.m/A":
        return val * 1e-3 if u in ("mN.m/A", "mNm/A") else val
    if target == "N.m":
        return val * 1e-3 if u in ("mN.m", "mNm") else val
    if target == "Ohm":
        return val * 1e-3 if u in ("mOhm", "mΩ") else val
    return val


# ==========================================================================
# 引擎 A：电磁与功率链自洽性审计（C1 ~ C3）
# ==========================================================================


def audit_electromagnetic_chain(
    params: Mapping[str, Any],
    meta: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[AuditFinding]]:
    """审计候选方案中的电磁参数与功率链是否闭合自洽。

    支持可选参数（给哪些审哪些，没给的跳过，绝不强迫纯热算例填电磁参数）：
      极对数与几何：p (极对数), Z (槽数), kw (绕组系数), N_series (每相串联匝数),
                    N_coil (每线圈匝数), coils_per_phase (每相串联线圈数),
                    B_delta (气隙磁密 T), alpha_p (极弧系数), tau_p (极距 mm), L_stack (叠长 mm)
      磁链与反电势：Phi_pole (每极磁通 mWb/Wb), lambda_pm (永磁磁链 mWb/Wb),
                    n_rpm (转速 rpm), E_line_pk (线反电势峰值 V), U_bus (母线电压 V)
      转矩与电流  ：Kt (转矩常数 mN·m/A 或 N·m/A), I_rms (相电流有效值 A),
                    I_peak (相电流峰值 A), T_out (输出转矩 N·m)
      绕组与损耗  ：R_phase (参考温度相电阻 Ω/mΩ), N_ref / R_ref (改匝数前基准),
                    Pcu (铜损 W), Pcu_2d (2D 槽内铜损 W), D_slot (槽中心直径 mm)
      槽满率      ：K_f (申报槽满率), A_cu_net (每槽净铜 mm²), A_cu_enamel (含漆铜 mm²),
                    A_slot (槽面积 mm²), A_env (绕组包络面积 mm²)
      功率与效率  ：P_out (机械输出功率 W), Pfe (铁损 W), Peddy (涡流 W), eta (效率 %)
    """
    meta = dict(meta or {})
    findings: List[AuditFinding] = []
    report: Dict[str, Any] = {"checks_run": [], "details": {}}

    p_pairs = _extract_val(params, "p") or meta.get("p")
    kw = _extract_val(params, "kw") or meta.get("kw")
    n_series = _extract_val(params, "N_series")
    n_coil = _extract_val(params, "N_coil")
    n_c = _extract_val(params, "coils_per_phase") or meta.get("coils_per_phase")

    phi_raw = _extract_val(params, "Phi_pole")
    phi_wb = _to_si(phi_raw, _extract_unit(params, "Phi_pole") or "mWb", "Wb")
    lam_raw = _extract_val(params, "lambda_pm")
    lam_wb = _to_si(lam_raw, _extract_unit(params, "lambda_pm") or "mWb", "Wb")

    # ---- C1a: 磁链 λpm ↔ N_series · kw · Φ_pole（含 4 倍抵消陷阱检测） ----
    b_delta = _extract_val(params, "B_delta")
    alpha_p = _extract_val(params, "alpha_p")
    tau_p_mm = _extract_val(params, "tau_p")
    l_stack_mm = _extract_val(params, "L_stack")

    phi_geom_wb: Optional[float] = None
    if all(v is not None for v in (b_delta, alpha_p, tau_p_mm, l_stack_mm)):
        phi_geom_wb = float(alpha_p) * float(b_delta) * (float(tau_p_mm) * 1e-3) * (float(l_stack_mm) * 1e-3)

    if lam_wb is not None and kw is not None and phi_wb is not None:
        n_used = n_series if n_series is not None else n_coil
        if n_used is not None and n_used > 0:
            report["checks_run"].append("C1a_flux_linkage")
            lam_calc = n_used * kw * phi_wb
            rel_err = abs(lam_wb - lam_calc) / max(abs(lam_calc), 1e-12)
            c1a_info: Dict[str, Any] = {
                "lambda_reported_mWb": round(lam_wb * 1e3, 4),
                "lambda_calc_mWb": round(lam_calc * 1e3, 4),
                "rel_error": round(rel_err, 5),
            }
            # 检查是否存在「N 用了每线圈匝数、Φ 用了 4 个线圈合计磁通」的抵消陷阱
            if phi_geom_wb is not None and phi_geom_wb > 0:
                ratio_phi = phi_wb / phi_geom_wb
                c1a_info["phi_reported_mWb"] = round(phi_wb * 1e3, 4)
                c1a_info["phi_geom_per_pole_mWb"] = round(phi_geom_wb * 1e3, 4)
                c1a_info["phi_ratio_to_single_pole"] = round(ratio_phi, 3)
                if abs(ratio_phi - 1.0) > 0.25 and rel_err < 0.03:
                    c1a_info["fingerprint"] = "em_flux_turns_cancellation_trap"
                    findings.append(AuditFinding(
                        "C1_flux_turns_trap", "REVIEW",
                        f"λpm={lam_wb*1e3:.3f} mWb 数值虽与 N×kw×Φ 闭合，但申报每极磁通 "
                        f"Φ={phi_wb*1e3:.4f} mWb 是几何单极磁通 {phi_geom_wb*1e3:.4f} mWb 的 "
                        f"{ratio_phi:.2f} 倍（疑似把每相线圈数乘进了 Φ、同时把 N_series 写成了单线圈匝数，"
                        f"两个倍数互相抵消）",
                        ("lambda_pm", "Phi_pole"), c1a_info,
                    ))
            if rel_err > 0.025:
                # 看是不是差了一个 coils_per_phase 倍数
                ratio_lam = lam_wb / max(lam_calc, 1e-12)
                c1a_info["ratio_reported_to_calc"] = round(ratio_lam, 3)
                if n_c and abs(ratio_lam - n_c) / n_c < 0.05:
                    c1a_info["fingerprint"] = "em_coil_vs_series_turns_mismatch"
                findings.append(AuditFinding(
                    "C1_flux_mismatch", "BLOCK",
                    f"磁链不闭合：申报 λpm={lam_wb*1e3:.3f} mWb，按 N·kw·Φ 算得 "
                    f"{lam_calc*1e3:.3f} mWb（偏差 {rel_err*100:.1f}%）",
                    ("lambda_pm", "Phi_pole"), c1a_info,
                ))
            report["details"]["C1a_flux_linkage"] = c1a_info

    # ---- C1b: 转矩常数 Kt ↔ 1.5 · p · λpm（含 Peak / RMS √2 倍错配诊断） ----
    kt_raw = _extract_val(params, "Kt")
    kt_nm_a = _to_si(kt_raw, _extract_unit(params, "Kt") or "mN.m/A", "N.m/A")
    if kt_nm_a is not None and lam_wb is not None and p_pairs is not None:
        report["checks_run"].append("C1b_kt_flux")
        kt_pal = _extract_palette(params, "Kt")
        basis = kt_pal.get("current_basis") or "peak"
        kt_peak_theory = 1.5 * float(p_pairs) * lam_wb
        kt_rms_theory = kt_peak_theory * math.sqrt(2.0)
        expected = kt_rms_theory if basis == "rms" else kt_peak_theory
        alt = kt_peak_theory if basis == "rms" else kt_rms_theory
        rel_err = abs(kt_nm_a - expected) / max(expected, 1e-12)
        alt_err = abs(kt_nm_a - alt) / max(alt, 1e-12)

        c1b_info: Dict[str, Any] = {
            "Kt_reported_mNm_per_A": round(kt_nm_a * 1e3, 3),
            "declared_current_basis": basis,
            "Kt_peak_theory_mNm_per_A": round(kt_peak_theory * 1e3, 3),
            "Kt_rms_theory_mNm_per_A": round(kt_rms_theory * 1e3, 3),
            "rel_error": round(rel_err, 5),
        }
        if rel_err > 0.025:
            if alt_err <= 0.025:
                c1b_info["fingerprint"] = "em_kt_rms_peak_sqrt2_mismatch"
                findings.append(AuditFinding(
                    "C1_kt_basis_mismatch", "BLOCK",
                    f"Kt 电流基准错配（差 √2 倍）：申报 Kt={kt_nm_a*1e3:.2f} mN·m/A "
                    f"声明为 {basis} 口径（理论 {expected*1e3:.2f}），实际与 "
                    f"{'peak' if basis == 'rms' else 'rms'} 口径（{alt*1e3:.2f}）吻合",
                    ("Kt", "lambda_pm"), c1b_info,
                ))
            else:
                findings.append(AuditFinding(
                    "C1_kt_mismatch", "BLOCK",
                    f"Kt 与磁链 λpm 不自洽：申报 Kt={kt_nm_a*1e3:.2f} mN·m/A，"
                    f"由 1.5·p·λpm（{basis}）算得 {expected*1e3:.2f} mN·m/A"
                    f"（偏差 {rel_err*100:.1f}%）",
                    ("Kt", "lambda_pm"), c1b_info,
                ))
        report["details"]["C1b_kt_flux"] = c1b_info

    # ---- C1c: 线反电势峰值 E_line_pk ↔ √3 · ωe · λpm 与母线电压余量 ----
    e_line = _extract_val(params, "E_line_pk")
    n_rpm = _extract_val(params, "n_rpm") or meta.get("n_rpm")
    u_bus = _extract_val(params, "U_bus") or meta.get("U_bus")
    if e_line is not None and lam_wb is not None and n_rpm is not None and p_pairs is not None:
        report["checks_run"].append("C1c_back_emf")
        omega_e = 2.0 * math.pi * (float(n_rpm) / 60.0) * float(p_pairs)
        e_line_calc = math.sqrt(3.0) * omega_e * lam_wb
        rel_err = abs(e_line - e_line_calc) / max(e_line_calc, 1e-12)
        c1c_info: Dict[str, Any] = {
            "E_line_pk_reported_V": round(e_line, 3),
            "E_line_pk_calc_V": round(e_line_calc, 3),
            "n_rpm": n_rpm,
            "rel_error": round(rel_err, 5),
        }
        if rel_err > 0.05:
            # 检查是不是把相反电势当成了线反电势（差 √3 倍）
            e_phase_calc = omega_e * lam_wb
            if abs(e_line - e_phase_calc) / max(e_phase_calc, 1e-12) <= 0.05:
                c1c_info["fingerprint"] = "em_phase_vs_line_emf_sqrt3_mismatch"
            findings.append(AuditFinding(
                "C1_emf_mismatch", "BLOCK",
                f"线反电势峰值与磁链不闭合：申报 E_line_pk={e_line:.2f} V，"
                f"按 √3·ωe·λpm (@{n_rpm:g} rpm) 算得 {e_line_calc:.2f} V"
                f"（偏差 {rel_err*100:.1f}%）",
                ("E_line_pk", "lambda_pm"), c1c_info,
            ))
        report["details"]["C1c_back_emf"] = c1c_info

    if e_line is not None and u_bus is not None and u_bus > 0:
        report["checks_run"].append("C1d_bus_voltage_margin")
        ratio_u = e_line / u_bus
        c1d_info = {
            "E_line_pk_V": round(e_line, 3),
            "U_bus_V": round(u_bus, 3),
            "voltage_utilization": round(ratio_u, 4),
        }
        if ratio_u > 1.0:
            findings.append(AuditFinding(
                "C1_bus_voltage_exceeded", "BLOCK",
                f"线反电势峰值 {e_line:.2f} V 超过母线电压 {u_bus:.2f} V"
                f"（占比 {ratio_u*100:.1f}%）：该转速下无法进入正常电流环控制",
                ("E_line_pk", "U_bus"), c1d_info,
            ))
        elif ratio_u >= 0.88:
            findings.append(AuditFinding(
                "C1_bus_voltage_tight", "REVIEW",
                f"线反电势峰值 {e_line:.2f} V 占母线电压 {u_bus:.2f} V 的 "
                f"{ratio_u*100:.1f}%（≥88%）：高速带载电压余量不足（如 8 匝方案占 97.9% 需降为 6 匝）",
                ("E_line_pk", "U_bus"), c1d_info,
            ))
        report["details"]["C1d_bus_voltage_margin"] = c1d_info

    # ---- C2a: 铜损闭合 Pcu ↔ 3 · I_rms² · R_phase ----
    pcu = _extract_val(params, "Pcu")
    r_raw = _extract_val(params, "R_phase")
    r_ohm = _to_si(r_raw, _extract_unit(params, "R_phase") or "Ohm", "Ohm")
    i_rms = _extract_val(params, "I_rms")
    i_peak = _extract_val(params, "I_peak")
    if i_rms is None and i_peak is not None:
        i_rms = i_peak / math.sqrt(2.0)

    if pcu is not None and r_ohm is not None and i_rms is not None:
        report["checks_run"].append("C2a_copper_loss_ohm")
        pcu_calc = 3.0 * (i_rms ** 2) * r_ohm
        rel_err = abs(pcu - pcu_calc) / max(pcu_calc, 1e-12)
        c2a_info: Dict[str, Any] = {
            "Pcu_reported_W": round(pcu, 3),
            "Pcu_calc_3I2R_W": round(pcu_calc, 3),
            "I_rms_A": round(i_rms, 3),
            "R_phase_Ohm": round(r_ohm, 5),
            "rel_error": round(rel_err, 5),
        }
        if rel_err > 0.03:
            # 诊断常见错法：1) 用了 I_peak 未除 √2（差 2 倍）；2) 漏乘 3（差 3 倍）；3) 2D 不含端部
            if abs(pcu - 2.0 * pcu_calc) / max(2.0 * pcu_calc, 1e-12) <= 0.03:
                c2a_info["fingerprint"] = "copper_loss_used_peak_current_squared"
            elif abs(pcu - pcu_calc / 3.0) / max(pcu_calc / 3.0, 1e-12) <= 0.03:
                c2a_info["fingerprint"] = "copper_loss_single_phase_not_three_phase"
            elif pcu < pcu_calc * 0.75:
                c2a_info["fingerprint"] = "copper_loss_2d_excluding_end_winding"
            findings.append(AuditFinding(
                "C2_copper_loss_mismatch", "BLOCK",
                f"铜损与 3·I_rms²·R 不闭合：申报 Pcu={pcu:.2f} W，"
                f"按 3×{i_rms:.2f}²×{r_ohm:.4f} 算得 {pcu_calc:.2f} W"
                f"（偏差 {rel_err*100:.1f}%）",
                ("Pcu", "I_rms", "R_phase"), c2a_info,
            ))
        report["details"]["C2a_copper_loss_ohm"] = c2a_info

    # ---- C2b: 固定槽满率下改匝数 R2 / R1 ↔ (N2 / N1)² ----
    n_curr = n_series if n_series is not None else n_coil
    n_ref = _extract_val(params, "N_ref") or meta.get("N_ref")
    r_ref_raw = _extract_val(params, "R_ref") or meta.get("R_ref")
    r_ref_ohm = _to_si(
        float(r_ref_raw) if r_ref_raw is not None else None,
        _extract_unit(params, "R_ref") or "Ohm", "Ohm"
    )
    if r_ohm is not None and r_ref_ohm is not None and n_curr is not None and n_ref is not None and n_ref > 0:
        report["checks_run"].append("C2b_turn_scaling_R")
        ratio_n = float(n_curr) / float(n_ref)
        r_quad = r_ref_ohm * (ratio_n ** 2)
        r_lin = r_ref_ohm * ratio_n
        err_quad = abs(r_ohm - r_quad) / max(r_quad, 1e-12)
        err_lin = abs(r_ohm - r_lin) / max(r_lin, 1e-12)
        c2b_info: Dict[str, Any] = {
            "R_reported_Ohm": round(r_ohm, 5),
            "R_quadratic_theory_Ohm": round(r_quad, 5),
            "R_linear_wrong_Ohm": round(r_lin, 5),
            "turns_ratio": round(ratio_n, 4),
            "rel_error_quadratic": round(err_quad, 5),
        }
        if err_quad > 0.04:
            if err_lin <= 0.03:
                c2b_info["fingerprint"] = "winding_resistance_linear_turn_scaling"
                findings.append(AuditFinding(
                    "C2_turn_scaling_linear_bug", "BLOCK",
                    f"改匝数相电阻缩放错误：固定槽满率下 R ∝ N²（应为 {r_quad:.4f} Ω），"
                    f"但申报值 {r_ohm:.4f} Ω 按一次方 R ∝ N（{r_lin:.4f} Ω）缩放",
                    ("R_phase",), c2b_info,
                ))
            else:
                findings.append(AuditFinding(
                    "C2_turn_scaling_mismatch", "REVIEW",
                    f"改匝数后相电阻 {r_ohm:.4f} Ω 与固定槽满率 N² 缩放值 {r_quad:.4f} Ω "
                    f"偏差 {err_quad*100:.1f}%（若改变了槽满率请显式声明）",
                    ("R_phase",), c2b_info,
                ))
        report["details"]["C2b_turn_scaling_R"] = c2b_info

    # ---- C2c: 槽满率三口径分离检查 ----
    kf = _extract_val(params, "K_f")
    a_cu_net = _extract_val(params, "A_cu_net")
    a_cu_enamel = _extract_val(params, "A_cu_enamel")
    a_slot = _extract_val(params, "A_slot")
    a_env = _extract_val(params, "A_env")
    if kf is not None and a_cu_net is not None and (a_slot is not None or a_env is not None):
        report["checks_run"].append("C2c_slot_fill_factor")
        candidates: Dict[str, float] = {}
        if a_env and a_env > 0:
            candidates["net_cu_over_envelope"] = a_cu_net / a_env
        if a_slot and a_slot > 0:
            candidates["net_cu_over_slot"] = a_cu_net / a_slot
            if a_cu_enamel and a_cu_enamel > 0:
                candidates["enamel_cu_over_slot"] = a_cu_enamel / a_slot
        matched = [name for name, val in candidates.items() if abs(kf - val) <= 0.015]
        c2c_info = {
            "K_f_reported": round(kf, 4),
            "computed_definitions": {k: round(v, 4) for k, v in candidates.items()},
            "matched_definition": matched[0] if matched else None,
        }
        if not matched:
            findings.append(AuditFinding(
                "C2_slot_fill_unmatched", "BLOCK",
                f"申报槽满率 K_f={kf:.3f} 与三种标准分母口径均不闭合："
                f"{c2c_info['computed_definitions']}",
                ("K_f",), c2c_info,
            ))
        elif len(candidates) > 1:
            findings.append(AuditFinding(
                "C2_slot_fill_basis_note", "INFO",
                f"槽满率 K_f={kf:.3f} 对应口径为 {matched[0]}；"
                f"其余口径值：{c2c_info['computed_definitions']}",
                ("K_f",), c2c_info,
            ))
        report["details"]["C2c_slot_fill_factor"] = c2c_info

    # ---- C3: 机械功率与效率分母闭合（含误用 P_out 作分母指纹识别） ----
    p_out = _extract_val(params, "P_out")
    t_out = _extract_val(params, "T_out")
    if p_out is not None and t_out is not None and n_rpm is not None:
        report["checks_run"].append("C3a_mech_power")
        p_calc = t_out * (2.0 * math.pi * float(n_rpm) / 60.0)
        rel_err = abs(p_out - p_calc) / max(p_calc, 1e-12)
        c3a_info = {
            "P_out_reported_W": round(p_out, 3),
            "P_out_calc_Tw_W": round(p_calc, 3),
            "rel_error": round(rel_err, 5),
        }
        if rel_err > 0.015:
            findings.append(AuditFinding(
                "C3_mech_power_mismatch", "BLOCK",
                f"机械输出功率不闭合：申报 P_out={p_out:.2f} W，"
                f"由 T·ω ({t_out} N·m @ {n_rpm} rpm) 算得 {p_calc:.2f} W"
                f"（偏差 {rel_err*100:.2f}%）",
                ("P_out", "T_out", "n_rpm"), c3a_info,
            ))
        report["details"]["C3a_mech_power"] = c3a_info

    eta_pct = _extract_val(params, "eta")
    pfe = _extract_val(params, "Pfe") or 0.0
    peddy = _extract_val(params, "Peddy") or 0.0
    p_loss = _extract_val(params, "P_loss")
    if p_loss is None and pcu is not None:
        p_loss = pcu + pfe + peddy

    if eta_pct is not None and p_out is not None and p_out > 0 and p_loss is not None and p_loss >= 0:
        report["checks_run"].append("C3b_efficiency_closure")
        p_in = p_out + p_loss
        eta_true_in = (p_out / p_in) * 100.0                 # 1 - P_loss / P_in
        eta_wrong_out = (1.0 - p_loss / p_out) * 100.0       # 误用 P_out 作分母
        diff_true = abs(eta_pct - eta_true_in)
        diff_wrong = abs(eta_pct - eta_wrong_out)
        c3b_info: Dict[str, Any] = {
            "eta_reported_pct": round(eta_pct, 3),
            "eta_input_denom_pct": round(eta_true_in, 3),
            "eta_output_denom_pct": round(eta_wrong_out, 3),
            "P_out_W": round(p_out, 3),
            "P_loss_W": round(p_loss, 3),
            "diff_from_true_pp": round(diff_true, 4),
        }
        if diff_true > 0.08:
            if diff_wrong <= 0.08:
                c3b_info["fingerprint"] = "efficiency_output_power_as_denominator"
                findings.append(AuditFinding(
                    "C3_efficiency_denom_bug", "REVIEW",
                    f"效率分母口径错误：申报 η={eta_pct:.2f}% 等于 1 - P_loss/P_out "
                    f"（{eta_wrong_out:.2f}%，误用输出功率作分母）；"
                    f"按严格定义 P_out/(P_out+P_loss) 应为 {eta_true_in:.2f}%"
                    f"（差 {eta_true_in - eta_pct:+.2f} 个百分点）",
                    ("eta", "P_out"), c3b_info,
                ))
            else:
                findings.append(AuditFinding(
                    "C3_efficiency_mismatch", "BLOCK",
                    f"效率与损耗不闭合：申报 η={eta_pct:.2f}%，"
                    f"按 P_out={p_out:.1f} W、P_loss={p_loss:.2f} W 算得 {eta_true_in:.2f}%"
                    f"（差 {diff_true:.2f} 个百分点）",
                    ("eta", "P_out"), c3b_info,
                ))
        report["details"]["C3b_efficiency_closure"] = c3b_info

    return report, findings


# ==========================================================================
# 引擎 B：热网络反推与多工况自洽性审计（I1 ~ I4）
# ==========================================================================


def audit_thermal_kcl_and_inversion(
    params: Mapping[str, Any],
    reported: Optional[Mapping[str, Any]] = None,
    meta: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[AuditFinding]]:
    """单工况热网络反推与 KCL 守恒残差审计（I1 & I2）。

    数据来源：
      - 损耗 Pcu, Pfe, [Peddy] 与环境温度 Ta 来自 params；
      - 申报温度 Tw, [Tr], [Ts] 可来自 reported 字典（如 case["_ai_reported"] 或 case["reported"]），
        也可直接作为 params["Tw"], params["Tr"], params["Ts"] 传入；
      - 若同时给出了 Rws, Rsa, [Rrs]，则运行 I1（KCL 残差与 6 种错法指纹识别）；
      - 无论是否给热阻，只要给了申报温度 Tw，就运行 I2（反推隐含热阻、物理正定性与 Boglietti 量级对照）。
    """
    meta = dict(meta or {})
    rep = dict(reported or {})
    findings: List[AuditFinding] = []
    out: Dict[str, Any] = {"checks_run": []}

    pcu0 = _extract_val(params, "Pcu")
    pfe = _extract_val(params, "Pfe")
    peddy = _extract_val(params, "Peddy") or 0.0
    ta = _extract_val(params, "Ta")
    alpha = _extract_val(params, "alpha")
    if alpha is None:
        alpha = ALPHA_CU_DEFAULT
    tref = _extract_val(params, "Tref")
    if tref is None:
        tref = TREF_DEFAULT

    rws = _extract_val(params, "Rws")
    rsa = _extract_val(params, "Rsa")
    rrs = _extract_val(params, "Rrs")

    tw_rep = rep.get("Tw") if rep.get("Tw") is not None else _extract_val(params, "Tw")
    tr_rep = rep.get("Tr") if rep.get("Tr") is not None else _extract_val(params, "Tr")
    ts_rep = rep.get("Ts") if rep.get("Ts") is not None else _extract_val(params, "Ts")

    if pcu0 is None or pfe is None or ta is None:
        return out, findings

    # ------------------------------------------------------------------
    # I1: 当同时给出了 (Rws, Rsa) 和申报温度 Tw_rep 时，检验 KCL 与热反馈方程自洽性
    # ------------------------------------------------------------------
    if rws is not None and rsa is not None and tw_rep is not None:
        out["checks_run"].append("I1_thermal_kcl")
        req = rws + rsa
        g_true = alpha * req * pcu0
        i1_detail: Dict[str, Any] = {
            "Tw_reported_C": round(float(tw_rep), 3),
            "Tr_reported_C": round(float(tr_rep), 3) if tr_rep is not None else None,
            "g_true": round(g_true, 5),
        }

        if g_true >= 1.0:
            i1_detail["status"] = "feedback_divergent_g_ge_1"
            findings.append(AuditFinding(
                "I1_feedback_unstable", "BLOCK",
                f"申报参数的热反馈增益 g = α·(Rws+Rsa)·Pcu = {g_true:.3f} ≥ 1.0："
                f"线性稳态解不存在（热失稳），但对方却报出了有限稳态温度 Tw={tw_rep}℃",
                ("Rws", "Rsa", "Pcu", "Tw"), i1_detail,
            ))
        else:
            # 1) 真实满足 KCL 的不动点解
            n_true = (ta - tref) + rsa * (pfe + peddy) + req * pcu0
            tw_true = tref + n_true / (1.0 - g_true)
            pcu_true = pcu0 * (1.0 + alpha * (tw_true - tref))
            ts_true = ta + rsa * (pcu_true + pfe + peddy)
            tr_true = (ts_true + rrs * peddy) if (rrs is not None and peddy > 0) else None

            err_tw = float(tw_rep) - tw_true
            i1_detail["Tw_kcl_true_C"] = round(tw_true, 3)
            i1_detail["Ts_kcl_true_C"] = round(ts_true, 3)
            i1_detail["Tr_kcl_true_C"] = round(tr_true, 3) if tr_true is not None else None
            i1_detail["Tw_residual_K"] = round(err_tw, 3)

            # 2) 计算 5 种典型绕组方程错法指纹
            tw_cold = ta + rsa * (pfe + peddy) + req * pcu0
            pcu_1step = pcu0 * (1.0 + alpha * (tw_cold - tref))
            tw_1step = ta + rsa * (pfe + peddy) + req * pcu_1step

            g_no_rsa = alpha * rws * pcu0
            tw_no_rsa_pcu = (
                (ta + rsa * (pfe + peddy) + rws * pcu0 * (1.0 - alpha * tref)) / (1.0 - g_no_rsa)
                if g_no_rsa < 1.0 else float("nan")
            )
            tw_pfe_thru_rws = tref + ((ta - tref) + req * (pfe + peddy) + req * pcu0) / (1.0 - g_true)
            tw_no_peddy = tref + ((ta - tref) + rsa * pfe + req * pcu0) / (1.0 - g_true)

            candidates_tw = {
                "kcl_missing_pcu_in_rsa": (
                    tw_no_rsa_pcu,
                    "违反节点 KCL：铜损 Pcu 只乘了 Rws，漏掉了流经定子到环境 Rsa 的压降 Rsa·Pcu",
                ),
                "unconverged_cold": (
                    tw_cold,
                    "未做铜损温度修正：直接用参考温度 Tref 下的冷态铜损 P0 算温升",
                ),
                "unconverged_1step": (
                    tw_1step,
                    "热反馈未迭代收敛：只用冷态温度代了一次 Pcu(Tw_cold) 即停止",
                ),
                "kcl_pfe_through_rws": (
                    tw_pfe_thru_rws,
                    "节点注入位置错误：把定子铁损 Pfe 也乘了绕组热阻 Rws（多算了 Rws·Pfe）",
                ),
                "kcl_missing_peddy": (
                    tw_no_peddy,
                    "漏计转子损耗：定子到环境热路漏掉了转子涡流损耗 Peddy",
                ),
            }

            matched_tw_fp: Optional[str] = None
            matched_tw_desc: str = ""
            if abs(err_tw) > 0.5:
                best_fp, best_diff = None, 999.0
                for fp_name, (val, desc) in candidates_tw.items():
                    if math.isnan(val):
                        continue
                    d = abs(float(tw_rep) - val)
                    if d < best_diff:
                        best_fp, best_diff, matched_tw_desc = fp_name, d, desc
                if best_fp is not None and best_diff <= 0.35:
                    matched_tw_fp = best_fp
                    i1_detail["Tw_fingerprint"] = best_fp
                    i1_detail["Tw_fingerprint_value_C"] = round(candidates_tw[best_fp][0], 3)
                    i1_detail["Tw_fingerprint_diff_K"] = round(best_diff, 4)

            # 3) 转子温度 Tr 残差与拓扑挂错指纹
            matched_tr_fp: Optional[str] = None
            if tr_rep is not None and tr_true is not None and rrs is not None:
                err_tr = float(tr_rep) - tr_true
                i1_detail["Tr_residual_K"] = round(err_tr, 3)
                # 指纹：Tr = Tw_rep + Rrs * Peddy（把转子热阻挂在绕组 Tw 上而非定子 Ts 上）
                tr_on_winding = float(tw_rep) + rrs * peddy
                if abs(float(tr_rep) - tr_on_winding) <= 0.15 and abs(float(tw_rep) - ts_true) > 0.5:
                    matched_tr_fp = "topo_rotor_on_winding"
                    i1_detail["Tr_fingerprint"] = matched_tr_fp
                    i1_detail["Tr_fingerprint_value_C"] = round(tr_on_winding, 3)
                    i1_detail["Tr_fingerprint_diff_K"] = round(abs(float(tr_rep) - tr_on_winding), 4)

            if abs(err_tw) > 0.5 or (tr_rep is not None and tr_true is not None and abs(float(tr_rep) - tr_true) > 0.5):
                msg_parts = [
                    f"申报温度与给定损耗/热阻不满足稳态热流守恒（KCL）："
                    f"申报 Tw={float(tw_rep):.2f}℃ vs 收敛真解 {tw_true:.2f}℃（偏差 {err_tw:+.2f} K）"
                ]
                if matched_tw_fp:
                    msg_parts.append(f"定位根因[{matched_tw_fp}]：{matched_tw_desc}")
                if matched_tr_fp:
                    msg_parts.append(
                        f"转子拓扑错误[{matched_tr_fp}]：申报 Tr={float(tr_rep):.2f}℃ 等于 "
                        f"Tw + Rrs·Peddy（把转子挂在绕组 Tw 上而非定子 Ts 上，真实 Tr={tr_true:.2f}℃）"
                    )
                findings.append(AuditFinding(
                    "I1_kcl_violation", "BLOCK",
                    "；".join(msg_parts),
                    ("Tw", "Rws", "Rsa", "Pcu"), i1_detail,
                ))
        out["I1_kcl"] = i1_detail

    # ------------------------------------------------------------------
    # I2: 从申报温度 + 损耗反推隐含热阻（Implied Thermal Resistances）
    # ------------------------------------------------------------------
    if tw_rep is not None:
        out["checks_run"].append("I2_implied_resistance")
        tw_v = float(tw_rep)
        pcu_at_tw = pcu0 * (1.0 + alpha * (tw_v - tref))
        ptot_at_tw = pcu_at_tw + pfe + peddy
        delta_tw_ta = tw_v - ta

        i2_detail: Dict[str, Any] = {
            "Tw_C": round(tw_v, 3),
            "Ta_C": round(ta, 3),
            "Pcu_at_Tw_W": round(pcu_at_tw, 3),
            "Ptot_at_Tw_W": round(ptot_at_tw, 3),
        }

        if delta_tw_ta <= 0 or pcu_at_tw <= 0 or ptot_at_tw <= 0:
            i2_detail["valid"] = False
            findings.append(AuditFinding(
                "I2_non_positive_temp_rise", "BLOCK",
                f"申报绕组温度 Tw={tw_v:.2f}℃ 不高于环境温度 Ta={ta:.2f}℃（或热态铜损 ≤ 0）："
                f"隐含热阻非正，违反热力学第二定律",
                ("Tw", "Ta"), i2_detail,
            ))
        else:
            # 严格代数上下界（无需 Ts）：
            # Tw - Ta = Rws * Pcu(Tw) + Rsa * Ptot(Tw)
            # 当 Rws -> 0 时，Rsa 取严格上界 (Tw - Ta) / Ptot(Tw)，同时也是 Req 的下界
            # 当 Rsa -> 0 时，Rws 取严格上界 (Tw - Ta) / Pcu(Tw)，同时也是 Req 的上界
            req_min = delta_tw_ta / ptot_at_tw
            req_max = delta_tw_ta / pcu_at_tw
            i2_detail["Req_strict_bounds_KpW"] = [round(req_min, 5), round(req_max, 5)]
            i2_detail["Rsa_upper_bound_KpW"] = round(req_min, 5)

            # 若同时给出了 Ts_rep，则完全解耦反推 Rws, Rsa, Rrs
            if ts_rep is not None:
                ts_v = float(ts_rep)
                rws_imp = (tw_v - ts_v) / pcu_at_tw
                rsa_imp = (ts_v - ta) / ptot_at_tw
                i2_detail["Rws_implied_KpW"] = round(rws_imp, 5)
                i2_detail["Rsa_implied_KpW"] = round(rsa_imp, 5)
                i2_detail["Req_implied_KpW"] = round(rws_imp + rsa_imp, 5)
                if rws_imp + rsa_imp > 0:
                    i2_detail["Rws_share"] = round(rws_imp / (rws_imp + rsa_imp), 4)
                if rws_imp <= 0 or rsa_imp <= 0:
                    findings.append(AuditFinding(
                        "I2_negative_implied_R", "BLOCK",
                        f"由节点温度 (Tw={tw_v}℃, Ts={ts_v}℃, Ta={ta}℃) 反推的热阻出现非正值："
                        f"Rws={rws_imp:.4f} K/W, Rsa={rsa_imp:.4f} K/W",
                        ("Tw", "Ts", "Ta"), i2_detail,
                    ))
                if tr_rep is not None and peddy > 0:
                    rrs_imp = (float(tr_rep) - ts_v) / peddy
                    i2_detail["Rrs_implied_KpW"] = round(rrs_imp, 5)
                    if rrs_imp <= 0:
                        findings.append(AuditFinding(
                            "I2_negative_implied_Rrs", "BLOCK",
                            f"反推转子热阻 Rrs=(Tr-Ts)/Peddy={rrs_imp:.4f} K/W ≤ 0",
                            ("Tr", "Ts", "Peddy"), i2_detail,
                        ))
            # 若未给 Ts，但给了 Rsa，则可反推隐含 Rws_implied
            elif rsa is not None:
                ts_from_rsa = ta + rsa * ptot_at_tw
                rws_imp_given_rsa = (tw_v - ts_from_rsa) / pcu_at_tw
                i2_detail["Rws_implied_given_Rsa_KpW"] = round(rws_imp_given_rsa, 5)
                if rws_imp_given_rsa <= 0:
                    findings.append(AuditFinding(
                        "I2_negative_implied_Rws", "BLOCK",
                        f"在给定 Rsa={rsa} K/W 下，仅定子到环境温升就使铁芯达 {ts_from_rsa:.2f}℃，"
                        f"高于申报绕组温度 Tw={tw_v:.2f}℃ ⇒ 隐含 Rws={rws_imp_given_rsa:.4f} K/W ≤ 0",
                        ("Tw", "Rsa"), i2_detail,
                    ))

        # Boglietti 2008 Eq.(2) 机壳散热系数对照（当提供 A_case 时）
        a_case = _extract_val(params, "A_case") or meta.get("A_case")
        rsa_to_check = i2_detail.get("Rsa_implied_KpW") or rsa
        if a_case is not None and float(a_case) > 0 and rsa_to_check is not None and float(rsa_to_check) > 0:
            a_m2 = float(a_case)
            r0_boglietti = BOGLIETTI_R0_COEFF / a_m2
            h_eff = 1.0 / (float(rsa_to_check) * a_m2)
            mult = h_eff / BOGLIETTI_H0
            cooling = str(meta.get("cooling_type") or "").strip().lower()
            i2_detail["boglietti_R0_natural_KpW"] = round(r0_boglietti, 4)
            i2_detail["implied_h_eff_W_m2K"] = round(h_eff, 2)
            i2_detail["multiplier_vs_natural_convection"] = round(mult, 2)

            if cooling in COOLING_MULTIPLIER_REF:
                lo_m, hi_m = COOLING_MULTIPLIER_REF[cooling]
                if not (lo_m <= mult <= hi_m):
                    findings.append(AuditFinding(
                        "I2_cooling_regime_mismatch", "REVIEW",
                        f"隐含机壳换热系数 h_eff={h_eff:.1f} W/(m²·K)（为 Boglietti 自然对流基准 "
                        f"5.95 的 {mult:.1f}×），超出所声明冷却方式 {cooling!r} 的参考量级 [{lo_m}, {hi_m}]×",
                        ("Rsa",), i2_detail,
                    ))
            else:
                findings.append(AuditFinding(
                    "I2_boglietti_cooling_ref", "INFO",
                    f"Boglietti 2008 机壳散热对照：A_case={a_m2:.4f} m² 的裸机壳自然对流基准 "
                    f"R0={r0_boglietti:.2f} K/W；当前 Rsa={float(rsa_to_check):.3f} K/W 对应 "
                    f"h_eff={h_eff:.1f} W/(m²·K)（自然对流的 {mult:.1f}×）",
                    ("Rsa",), i2_detail,
                ))

        out["I2_implied"] = i2_detail

    return out, findings


# ==========================================================================
# 引擎 B-3：多工况集合成员交集检验（Set-Membership / Bounded-Error Inversion）
# ==========================================================================


def _rank_corr(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Spearman 秩相关系数（无第三方库依赖），用于检测隐含热阻随温度/功率的单调漂移。"""
    n = len(xs)
    if n < 3 or len(ys) != n:
        return 0.0

    def ranks(arr: Sequence[float]) -> List[float]:
        indexed = sorted(enumerate(arr), key=lambda t: t[1])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[indexed[k][0]] = avg_rank
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    deny = math.sqrt(sum((b - my) ** 2 for b in ry))
    if denx < 1e-12 or deny < 1e-12:
        return 0.0
    return num / (denx * deny)


def _interval_intersection(intervals: Sequence[Tuple[float, float]]) -> Tuple[bool, Tuple[float, float], float]:
    """求一组区间 [lo_k, hi_k] 的交集。

    返回 (是否非空, (max_lo, min_hi), 最小补齐半径 half_gap_needed)。
    当 max_lo > min_hi 时交集为空，half_gap = (max_lo - min_hi) / 2。
    """
    max_lo = max(iv[0] for iv in intervals)
    min_hi = min(iv[1] for iv in intervals)
    if max_lo <= min_hi:
        return True, (round(max_lo, 6), round(min_hi, 6)), 0.0
    return False, (round(max_lo, 6), round(min_hi, 6)), round((max_lo - min_hi) / 2.0, 6)


def audit_multipoint_consistency(
    points: Sequence[Mapping[str, Any]],
    *,
    delta_T_K: float = 1.0,
    rel_P_unc: float = 0.02,
    tw_limit_C: Optional[float] = None,
) -> Tuple[Dict[str, Any], List[AuditFinding]]:
    """I3: 多工况有界误差反推与集合成员交集检验（Ragot & Maquin 2005 / Raïssi 2005）。

    支持三类真实多工况场景：
      场景 1 · 带节点温度 (Tw, Ts, [Tr]) 与损耗 (Pcu_hot 或 Pcu0, Ptot)：
               如 Motor-CAD 5 温度点扫描 —— 逐点独立反推 Rws_k 和 Rsa_k 的可行区间，
               分别检验 ∩ Rws_k 和 ∩ Rsa_k 是否非空，从而机器区分「Rws 可当常数、Rsa 随温度降 63%」。
      场景 2 · 带「计算温度 Tw_calc vs 实测温度 Tw_exp」（以及功率/环境温度）：
               如 AIP Advances 2023 的 5 个功率点、或盲测任务 2 的 7 个温度点 ——
               审计残差 e_k = Tw_exp - Tw_calc 的单调性、最大正残差（决定 Δ_cal）、
               以及在给定限值 tw_limit_C 下是否存在「计算通过 (≤ Tw_max) 但实测超限 (> Tw_max)」的漏报点！
      场景 3 · 直接给定多工况下辨识出的某支路热阻序列 (x_var, R_k)：
               如 Energies 2021 Table 3 在 10/20/30/50 Hz 下的 Rfc 序列 ——
               检验常值交集是否为空，以及 R_k 随转速/频率是否严格单调递减（强迫对流物理规律）。
    """
    findings: List[AuditFinding] = []
    if len(points) < 2:
        return {"ok": False, "reason": "多工况审计至少需要 2 个工况点"}, findings

    out: Dict[str, Any] = {
        "point_count": len(points),
        "bounds_assumed": {"delta_T_K": delta_T_K, "rel_P_unc": rel_P_unc},
    }

    # ---- 分支 1：有 (Tw, Ts, Pcu, Ptot, Ta) 的完整节点反推（Motor-CAD 类） ----
    if all(("Tw" in pt and "Ts" in pt and "Ta" in pt) for pt in points):
        rws_vals: List[float] = []
        rsa_vals: List[float] = []
        rws_ivs: List[Tuple[float, float]] = []
        rsa_ivs: List[Tuple[float, float]] = []
        ta_vals: List[float] = []
        tw_vals: List[float] = []
        per_point: List[Dict[str, Any]] = []

        for idx, pt in enumerate(points):
            ta = float(pt["Ta"])
            ts = float(pt["Ts"])
            tw = float(pt["Tw"])
            pcu = float(pt["Pcu"])
            ptot = float(pt.get("Ptot") or (pcu + float(pt.get("Pfe", 0.0)) + float(pt.get("Peddy", 0.0))))

            rws_c = (tw - ts) / pcu
            rsa_c = (ts - ta) / ptot

            # 有界误差区间：分子温差 ±2·delta_T_K（两个温度测点），分母功率 ±rel_P_unc
            d_tw_ts_lo = max(0.0, (tw - ts) - 2.0 * delta_T_K)
            d_tw_ts_hi = (tw - ts) + 2.0 * delta_T_K
            rws_lo = d_tw_ts_lo / (pcu * (1.0 + rel_P_unc))
            rws_hi = d_tw_ts_hi / (pcu * max(1.0 - rel_P_unc, 1e-6))

            d_ts_ta_lo = max(0.0, (ts - ta) - 2.0 * delta_T_K)
            d_ts_ta_hi = (ts - ta) + 2.0 * delta_T_K
            rsa_lo = d_ts_ta_lo / (ptot * (1.0 + rel_P_unc))
            rsa_hi = d_ts_ta_hi / (ptot * max(1.0 - rel_P_unc, 1e-6))

            rws_vals.append(rws_c)
            rsa_vals.append(rsa_c)
            rws_ivs.append((rws_lo, rws_hi))
            rsa_ivs.append((rsa_lo, rsa_hi))
            ta_vals.append(ta)
            tw_vals.append(tw)
            per_point.append({
                "index": idx,
                "Ta_C": ta, "Ts_C": ts, "Tw_C": tw,
                "Rws_KpW": round(rws_c, 5),
                "Rws_interval_KpW": [round(rws_lo, 5), round(rws_hi, 5)],
                "Rsa_KpW": round(rsa_c, 5),
                "Rsa_interval_KpW": [round(rsa_lo, 5), round(rsa_hi, 5)],
            })

        rws_ok, rws_inter, rws_gap = _interval_intersection(rws_ivs)
        rsa_ok, rsa_inter, rsa_gap = _interval_intersection(rsa_ivs)
        rws_span_ratio = (max(rws_vals) - min(rws_vals)) / max(sum(rws_vals) / len(rws_vals), 1e-12)
        rsa_span_ratio = (max(rsa_vals) - min(rsa_vals)) / max(sum(rsa_vals) / len(rsa_vals), 1e-12)
        rho_rws = _rank_corr(ta_vals, rws_vals)
        rho_rsa = _rank_corr(ta_vals, rsa_vals)

        # 检查是否存在「边界 Ta 升高，器件温度 Ts 或 Tw 反而下降」的非单调物理反常
        non_monotonic_temp = any(
            ta_vals[i + 1] > ta_vals[i] and (tw_vals[i + 1] < tw_vals[i] or points[i + 1]["Ts"] < points[i]["Ts"])
            for i in range(len(points) - 1)
        )

        out["mode"] = "full_node_inversion"
        out["points"] = per_point
        out["Rws_summary"] = {
            "min_KpW": round(min(rws_vals), 5),
            "max_KpW": round(max(rws_vals), 5),
            "relative_span": round(rws_span_ratio, 4),
            "set_intersection_non_empty": rws_ok,
            "intersection_bounds": list(rws_inter),
            "spearman_vs_Ta": round(rho_rws, 3),
        }
        out["Rsa_summary"] = {
            "min_KpW": round(min(rsa_vals), 5),
            "max_KpW": round(max(rsa_vals), 5),
            "relative_span": round(rsa_span_ratio, 4),
            "set_intersection_non_empty": rsa_ok,
            "intersection_bounds": list(rsa_inter),
            "half_gap_KpW": rsa_gap,
            "spearman_vs_Ta": round(rho_rsa, 3),
        }
        out["non_monotonic_temperature_response"] = non_monotonic_temp

        if not rsa_ok:
            drift_note = (
                f"且随温度呈强单调漂移（Spearman ρ={rho_rsa:+.2f}，极差达均值 {rsa_span_ratio*100:.1f}%）"
                if abs(rho_rsa) >= 0.85 else f"极差达均值 {rsa_span_ratio*100:.1f}%"
            )
            findings.append(AuditFinding(
                "I3_rsa_non_constant", "REVIEW",
                f"多工况集合成员检验：Rsa 的有界误差可行区间交集为空"
                f"（范围 [{min(rsa_vals):.4f}, {max(rsa_vals):.4f}] K/W，{drift_note}），"
                f"证伪「Rsa 为常数」假设；而 Rws 交集{'非空' if rws_ok else '亦为空'}"
                f"（极差 {rws_span_ratio*100:.1f}%）",
                ("Rsa", "Rws"), out,
            ))
        if non_monotonic_temp:
            findings.append(AuditFinding(
                "I3_non_monotonic_response", "REVIEW",
                "检测到边界温度 Ta 升高时内部温度反而下降的非单调区间："
                "常系数线性热模型在原理上只能产生 ∂T/∂Ta = 1 的单调响应，无法在此区间外推",
                ("Ta", "Tw", "Ts"), {"Ta_series": ta_vals, "Tw_series": tw_vals},
            ))

    # ---- 分支 2：模型预测 Tw_calc vs 实验/高保真基准 Tw_exp（AIP 2023 / 任务 2 类） ----
    if all(("Tw_calc" in pt and "Tw_exp" in pt) for pt in points):
        residuals: List[float] = []
        x_vals: List[float] = []
        missed_violations: List[Dict[str, Any]] = []
        false_alarms: List[Dict[str, Any]] = []
        rows: List[Dict[str, Any]] = []

        for idx, pt in enumerate(points):
            tw_c = float(pt["Tw_calc"])
            tw_e = float(pt["Tw_exp"])
            res = tw_e - tw_c  # 正值 = 计算低估（危险方向）
            x_v = float(pt.get("P_kW") or pt.get("Ta") or idx)
            residuals.append(res)
            x_vals.append(x_v)
            row: Dict[str, Any] = {
                "index": idx, "x": x_v,
                "Tw_calc_C": round(tw_c, 2),
                "Tw_exp_C": round(tw_e, 2),
                "underestimation_K": round(res, 2),
            }
            if tw_limit_C is not None:
                calc_pass = tw_c <= tw_limit_C
                exp_pass = tw_e <= tw_limit_C
                row["calc_pass"] = calc_pass
                row["exp_pass"] = exp_pass
                if calc_pass and not exp_pass:
                    missed_violations.append(row)
                elif (not calc_pass) and exp_pass:
                    false_alarms.append(row)
            rows.append(row)

        max_under = max([0.0] + residuals)
        worst_idx = residuals.index(max(residuals))
        rho_res = _rank_corr(x_vals, residuals)
        # 检查残差是否非单调（先升后降或剧烈振荡）
        diffs = [residuals[i + 1] - residuals[i] for i in range(len(residuals) - 1)]
        sign_flips = sum(
            1 for i in range(len(diffs) - 1)
            if diffs[i] * diffs[i + 1] < -1e-6
        )

        cmp_summary: Dict[str, Any] = {
            "rows": rows,
            "max_underestimation_K": round(max_under, 3),
            "worst_point": rows[worst_idx],
            "required_delta_cal_gamma2_K": round(2.0 * max_under, 3),
            "residual_spearman_vs_x": round(rho_res, 3),
            "residual_direction_flips": sign_flips,
            "tw_limit_C": tw_limit_C,
            "missed_violation_count": len(missed_violations),
            "missed_violations": missed_violations,
        }
        out["model_vs_reference"] = cmp_summary

        if missed_violations:
            pts_str = "、".join(
                f"x={m['x']}(计算 {m['Tw_calc_C']}℃≤{tw_limit_C}℃ 通过，实测 {m['Tw_exp_C']}℃ 超限)"
                for m in missed_violations
            )
            findings.append(AuditFinding(
                "I3_missed_violation_detected", "BLOCK",
                f"检出 {len(missed_violations)} 个「计算通过、实测超限」的外部对照漏报点：{pts_str}",
                ("Tw",), cmp_summary,
            ))
        if max_under > 2.0 * delta_T_K:
            findings.append(AuditFinding(
                "I3_systematic_underestimation", "REVIEW",
                f"模型相对基准存在显著低估：最大正残差 +{max_under:.1f} K"
                f"（出现在 x={rows[worst_idx]['x']}），按 γ=2 需校准裕度 Δ_cal={2.0*max_under:.1f} K",
                ("Tw",), cmp_summary,
            ))

    # ---- 分支 3：给定多工况辨识热阻序列 (x, R)（Energies 2021 Table 3 类） ----
    if all(("R" in pt and "x" in pt) for pt in points):
        xs = [float(pt["x"]) for pt in points]
        rs = [float(pt["R"]) for pt in points]
        rel_unc = max(rel_P_unc, 0.05)
        ivs = [(r * (1.0 - rel_unc), r * (1.0 + rel_unc)) for r in rs]
        ok_inter, inter_b, gap = _interval_intersection(ivs)
        rho = _rank_corr(xs, rs)
        out["resistance_series"] = {
            "x": xs,
            "R_KpW": rs,
            "set_intersection_non_empty": ok_inter,
            "intersection_bounds": list(inter_b),
            "spearman_vs_x": round(rho, 3),
            "ratio_max_to_min": round(max(rs) / max(min(rs), 1e-12), 3),
        }
        if not ok_inter and abs(rho) >= 0.9:
            findings.append(AuditFinding(
                "I3_speed_dependent_resistance", "INFO",
                f"支路热阻随工况变量 x 严格单调变化（Spearman ρ={rho:+.2f}，"
                f"从 {max(rs):.3f} 降至 {min(rs):.3f} K/W，比值 {max(rs)/min(rs):.2f}×）："
                f"符合强迫对流热阻随转速/流量上升而下降的物理规律，不可作常参数跨转速外推",
                ("R",), out["resistance_series"],
            ))

    return out, findings


# ==========================================================================
# 引擎 B-4：串并联拓扑与端部二阶网络合成审计（Energies 2021 类）
# ==========================================================================


def audit_topology_reduction(spec: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[AuditFinding]]:
    """I4: 检验论文或报告声称的多支路热阻（如 Energies 2021 的 Req,w / Req,sr / Rfc / Rew,a）
    合成关系与端部二阶分流公式是否自洽。
    """
    findings: List[AuditFinding] = []
    req_w = spec.get("Req_w")
    req_sr = spec.get("Req_sr")
    rfc = spec.get("Rfc")
    rew_a = spec.get("Rew_a")

    out: Dict[str, Any] = {}
    if req_w is None or req_sr is None or rfc is None:
        return out, findings

    rw = float(req_w)
    rsr = float(req_sr)
    rf = float(rfc)
    if rw <= 0 or rsr <= 0 or rf <= 0:
        findings.append(AuditFinding(
            "I4_non_positive_branch", "BLOCK",
            f"拓扑支路热阻必须为正：Req,w={rw}, Req,sr={rsr}, Rfc={rf}",
            ("Req_w", "Req_sr", "Rfc"), {},
        ))
        return out, findings

    # 定子铁芯到环境：导热支路 Req,sr 与强迫对流支路 Rfc 并联
    rsa_parallel = (rsr * rf) / (rsr + rf)
    req_standstill = rw + rsr                 # DC 静止测试：Rfc = ∞
    req_rotating_no_ew = rw + rsa_parallel    # 旋转无端部旁路
    rws_share_rot = rw / req_rotating_no_ew

    out["Rsa_parallel_sr_fc_KpW"] = round(rsa_parallel, 5)
    out["Req_dc_standstill_KpW"] = round(req_standstill, 5)
    out["Req_ac_rotating_KpW"] = round(req_rotating_no_ew, 5)
    out["Rws_share_in_rotating"] = round(rws_share_rot, 4)

    # 若包含改进模型的端部对流支路 Rew,a 与形状因子 Ks = L/D, Np (极数)
    if rew_a is not None:
        rew = float(rew_a)
        # 在改进二阶模型（Energies 2021 Fig. 16）中，绕组节点到环境由「主路 Req_rotating_no_ew」
        # 与「端部强迫对流支路 Rew,a」共同散热；若给定 Ks 和 Np，核对 Eq.(4) 的端部铜损占比 α
        ks = spec.get("Ks")
        np_poles = spec.get("Np")
        alpha_rep = spec.get("alpha_ew")
        out["Rew_a_KpW"] = round(rew, 5)
        out["Req_total_with_endwinding_parallel_KpW"] = round(
            (req_rotating_no_ew * rew) / (req_rotating_no_ew + rew), 5
        )
        if ks is not None and np_poles is not None:
            term = math.pi / (float(np_poles) * float(ks))
            alpha_eq4 = term / (1.0 + term)
            out["alpha_ew_eq4"] = round(alpha_eq4, 4)
            if alpha_rep is not None and abs(float(alpha_rep) - alpha_eq4) > 0.05:
                findings.append(AuditFinding(
                    "I4_endwinding_alpha_mismatch", "REVIEW",
                    f"端部铜损占比申报值 α={float(alpha_rep):.3f} 与 Energies 2021 Eq.(4) "
                    f"几何公式计算值 {alpha_eq4:.3f}（Np={np_poles}, Ks={ks}）偏差较大",
                    ("alpha_ew",), out,
                ))

    # 检查申报的 Rws_share 或 Rsa_equiv 是否与并联拓扑一致
    claimed_rsa = spec.get("claimed_Rsa")
    if claimed_rsa is not None:
        diff = abs(float(claimed_rsa) - rsa_parallel)
        out["claimed_Rsa_KpW"] = float(claimed_rsa)
        if diff > 0.005:
            findings.append(AuditFinding(
                "I4_topology_reduction_mismatch", "BLOCK",
                f"申报等效 Rsa={float(claimed_rsa):.4f} K/W 与 Req,sr || Rfc 并联值 "
                f"{rsa_parallel:.4f} K/W 不一致（差 {diff:.4f} K/W）",
                ("Rsa",), out,
            ))

    return out, findings


# ==========================================================================
# 审计覆盖率：每次 PASS 都要说清「审了哪些、哪些因缺字段没审」
# ==========================================================================
#
# 为什么要有它：只给 Pcu/Pfe/Ta 三个数的候选，所有判据都跳过，结果是 AUDIT_PASS。
# 读者会把它读成「很干净」，实际是「没东西可审」。覆盖率把这两件事分开：
#   AUDIT_PASS + 覆盖率 1/11  = 干净，但基本没审
#   AUDIT_PASS + 覆盖率 11/11 = 干净，审全了
#
# 判据 → 所需字段。表里没跑到的判据，按「缺哪几个字段」归入未审。
CHECK_REQUIREMENTS: Dict[str, Tuple[str, ...]] = {
    "C1a_flux_linkage": ("lambda_pm", "kw", "Phi_pole"),
    "C1b_kt_flux": ("Kt", "lambda_pm", "p"),
    "C1c_back_emf": ("E_line_pk", "lambda_pm", "n_rpm", "p"),
    "C1d_bus_voltage_margin": ("E_line_pk", "U_bus"),
    "C2a_copper_loss_ohm": ("Pcu", "R_phase", "I_rms"),
    "C2b_turn_scaling_R": ("R_phase", "R_ref", "N_ref"),
    "C2c_slot_fill_factor": ("K_f", "A_cu_net"),
    "C3a_mech_power": ("P_out", "T_out", "n_rpm"),
    "C3b_efficiency_closure": ("eta", "P_out"),
    "I1_thermal_kcl": ("Rws", "Rsa", "Tw"),
    "I2_implied_resistance": ("Tw", "Pcu", "Pfe", "Ta"),
}

CHECK_DESC: Dict[str, str] = {
    "C1a_flux_linkage": "磁链 λpm = N·kw·Φ 闭合",
    "C1b_kt_flux": "转矩常数 Kt = 1.5·p·λpm",
    "C1c_back_emf": "线反电势 = √3·ωe·λpm",
    "C1d_bus_voltage_margin": "反电势占母线余量",
    "C2a_copper_loss_ohm": "铜损 = 3·I²·R",
    "C2b_turn_scaling_R": "改匝数时 R ∝ N²",
    "C2c_slot_fill_factor": "槽满率三口径",
    "C3a_mech_power": "机械功率 = T·ω",
    "C3b_efficiency_closure": "效率分母闭合",
    "I1_thermal_kcl": "稳态热流守恒（KCL）",
    "I2_implied_resistance": "隐含热阻反推",
}


def _coverage_view(params: Mapping[str, Any], reported: Mapping[str, Any]) -> Dict[str, Any]:
    """把 reported 里的 Tw/Tr/Ts 并进视图——它们常不在 params 里，但判据要用。"""
    view: Dict[str, Any] = dict(params)
    for k in ("Tw", "Tr", "Ts"):
        if _extract_val(view, k) is None and reported.get(k) is not None:
            view[k] = reported[k]
    return view


def build_coverage(params: Mapping[str, Any], reported: Mapping[str, Any],
                   checks_run: Sequence[str]) -> Dict[str, Any]:
    """算出「审了哪些、哪些因为缺字段没审」。"""
    view = _coverage_view(params, reported)
    ran = [c for c in checks_run if c in CHECK_REQUIREMENTS]
    ran_set = set(ran)
    skipped: List[Dict[str, Any]] = []
    for name, needs in CHECK_REQUIREMENTS.items():
        if name in ran_set:
            continue
        miss = [f for f in needs if _extract_val(view, f) is None]
        if miss:
            skipped.append({"check": name, "desc": CHECK_DESC[name], "missing": miss})

    total = len(CHECK_REQUIREMENTS)
    audited = len(ran)
    if not skipped:
        note = f"审了 {audited}/{total} 条判据，无因缺字段未审项"
    else:
        note = f"审了 {audited}/{total} 条判据，{len(skipped)} 条因缺字段未审"
    return {
        "audited": sorted(ran),
        "skipped": skipped,
        "audited_count": audited,
        "skipped_count": len(skipped),
        "total": total,
        "note": note,
    }


# ==========================================================================
# 统一入口：供 mdca.py 的 audit(case) 调用
# ==========================================================================


def audit_case_consistency(case: Any, raw_doc: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """对一个 Case 运行 A 路全部适用的自洽性判据（引擎 A + 引擎 B）。

    返回：
      {
        "ran": bool,
        "checks_run": [...],
        "electromagnetic": {...},
        "thermal_single": {...},
        "multipoint": {...} | None,
        "topology": {...} | None,
        "issues": [ {code, severity, message, params, detail}, ... ],
      }
    """
    raw = dict(raw_doc or {})
    meta = dict(getattr(case, "meta", None) or raw.get("meta") or {})
    if getattr(case, "limits", None):
        meta.setdefault("Tw_max", case.limits.get("Tw_max"))
    reported = (
        getattr(case, "reported", None)
        or raw.get("reported")
        or raw.get("_ai_reported")
        or {}
    )

    em_rep, em_findings = audit_electromagnetic_chain(case.params, meta=meta)
    th_rep, th_findings = audit_thermal_kcl_and_inversion(case.params, reported=reported, meta=meta)

    all_findings: List[AuditFinding] = list(em_findings) + list(th_findings)
    checks_run: List[str] = list(em_rep.get("checks_run", [])) + list(th_rep.get("checks_run", []))

    mp_rep = None
    mp_points = getattr(case, "operating_points", None) or raw.get("operating_points")
    if isinstance(mp_points, list) and len(mp_points) >= 2:
        tw_lim = float(case.limits.get("Tw_max")) if (getattr(case, "limits", None) and "Tw_max" in case.limits) else None
        mp_rep, mp_f = audit_multipoint_consistency(mp_points, tw_limit_C=tw_lim)
        all_findings.extend(mp_f)
        checks_run.append("I3_multipoint_consistency")

    topo_rep = None
    topo_spec = getattr(case, "topology_spec", None) or raw.get("topology_spec")
    if isinstance(topo_spec, Mapping) and topo_spec:
        topo_rep, topo_f = audit_topology_reduction(topo_spec)
        all_findings.extend(topo_f)
        checks_run.append("I4_topology_reduction")

    coverage = build_coverage(case.params, reported, checks_run)

    return {
        "ran": bool(checks_run),
        "checks_run": checks_run,
        "coverage": coverage,
        "electromagnetic": em_rep.get("details") or {},
        "thermal_single": {k: v for k, v in th_rep.items() if k != "checks_run"},
        "multipoint": mp_rep,
        "topology": topo_rep,
        "issues": [f.as_dict() for f in all_findings],
    }


# ==========================================================================
# 自测（12 条，覆盖引擎 A 电磁闭合 + 引擎 B 热网络反推与多工况集合交集）
# ==========================================================================


def selftest() -> bool:
    ok = True

    def check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {label:<50}{'PASS' if good else 'FAIL'}"
              + (f"  {detail}" if detail and not good else ""))
        ok = ok and good

    print("=== mdca_inverter.py 自测（反推器 + 电磁-热自洽性判据集）===")

    # 1) C1a: 课设报告 λpm = 8 × 0.933 × 1.8685 的 4 倍抵消陷阱
    _, f1 = audit_electromagnetic_chain({
        "N_coil": {"value": 8, "unit": "turns"},
        "kw": {"value": 0.933, "unit": "1"},
        "Phi_pole": {"value": 1.8685, "unit": "mWb"},
        "lambda_pm": {"value": 13.9465, "unit": "mWb"},
        "B_delta": {"value": 0.85, "unit": "T"},
        "alpha_p": {"value": 0.82, "unit": "1"},
        "tau_p": {"value": 22.18, "unit": "mm"},
        "L_stack": {"value": 30.0, "unit": "mm"},
    })
    check("① C1a 检出 λpm 匝数与磁通 4 倍互相抵消陷阱",
          any(x.code == "C1_flux_turns_trap" for x in f1), str([x.code for x in f1]))

    # 2) C1b: Kt 的 RMS / Peak √2 倍口径错配
    _, f2 = audit_electromagnetic_chain({
        "p": {"value": 5, "unit": "1"},
        "lambda_pm": {"value": 10.46, "unit": "mWb"},
        "Kt": {"value": 78.45 * math.sqrt(2.0), "unit": "mN.m/A",
               "palette": {"current_basis": "peak"}},
    })
    check("② C1b 检出 Kt 的 RMS/Peak √2 倍口径错配",
          any(x.code == "C1_kt_basis_mismatch" for x in f2), str([x.code for x in f2]))

    # 3) C1d: 8 匝线反电势 23.49 V 占 24 V 母线 97.9% 余量告警
    _, f3 = audit_electromagnetic_chain({
        "E_line_pk": {"value": 23.49, "unit": "V"},
        "U_bus": {"value": 24.0, "unit": "V"},
    })
    check("③ C1d 检出 8 匝线反电势占母线 97.9% 余量不足",
          any(x.code == "C1_bus_voltage_tight" for x in f3), str([x.code for x in f3]))

    # 4) C2b: 固定槽满率 8 匝→6 匝时误按一次方缩放电阻 (0.032 * 6/8 = 0.024 Ω vs 0.018 Ω)
    _, f4 = audit_electromagnetic_chain({
        "N_coil": {"value": 6, "unit": "turns"},
        "N_ref": {"value": 8, "unit": "turns"},
        "R_ref": {"value": 0.032, "unit": "Ohm"},
        "R_phase": {"value": 0.024, "unit": "Ohm"},
    })
    check("④ C2b 检出改匝数时电阻误按一次方 N 而非 N² 缩放",
          any(x.code == "C2_turn_scaling_linear_bug" for x in f4), str([x.code for x in f4]))

    # 5) C3b: 效率误用输出功率 P_out 作分母 (95.9% = 1 - 13.6/334.4 vs 96.09%)
    _, f5 = audit_electromagnetic_chain({
        "P_out": {"value": 334.4, "unit": "W"},
        "P_loss": {"value": 13.6, "unit": "W"},
        "eta": {"value": 95.93, "unit": "%"},
    })
    check("⑤ C3b 检出效率误用输出功率 P_out 作分母",
          any(x.code == "C3_efficiency_denom_bug" for x in f5), str([x.code for x in f5]))

    # 6) I1: 精确检出 AI 组方案 1 的「铜损漏乘 Rsa 违反 KCL + 转子挂在 Tw 上」
    rep6, f6 = audit_thermal_kcl_and_inversion(
        {
            "Rws": 1.16, "Rsa": 2.03, "Rrs": 1.2,
            "Pcu": 13.6, "Pfe": 5.33, "Peddy": 15.24, "Ta": 40.0,
        },
        reported={"Tw": 102.66, "Tr": 120.95},
    )
    i1 = rep6.get("I1_kcl") or {}
    check("⑥ I1 检出 AI 方案 1 违反 KCL 並命中 kcl_missing_pcu_in_rsa 指纹",
          any(x.code == "I1_kcl_violation" for x in f6)
          and i1.get("Tw_fingerprint") == "kcl_missing_pcu_in_rsa"
          and i1.get("Tr_fingerprint") == "topo_rotor_on_winding",
          str(i1))

    # 7) I1: 精确检出早期「铁损 Pfe 误乘 Req」指纹 (116.91℃ vs 109.46℃)
    rep7, f7 = audit_thermal_kcl_and_inversion(
        {"Rws": 1.16, "Rsa": 2.03, "Pcu": 13.6, "Pfe": 5.33, "Ta": 40.0},
        reported={"Tw": 116.91},
    )
    check("⑦ I1 检出早期「铁损误流经绕组热阻 Rws」指纹",
          (rep7.get("I1_kcl") or {}).get("Tw_fingerprint") == "kcl_pfe_through_rws",
          str(rep7.get("I1_kcl")))

    # 8) I2: 从给定 Rsa 反推隐含 Rws 为负（物理不可行）
    _, f8 = audit_thermal_kcl_and_inversion(
        {"Rsa": 2.03, "Pcu": 13.6, "Pfe": 5.33, "Peddy": 15.24, "Ta": 40.0},
        reported={"Tw": 90.0},  # 仅定子温升就使 Ts > 110℃，Tw=90℃ 要求 Rws < 0
    )
    check("⑧ I2 检出隐含热阻 Rws ≤ 0（违反热力学第二定律）",
          any(x.code == "I2_negative_implied_Rws" for x in f8), str([x.code for x in f8]))

    # 9) I2: Boglietti 2008 自然对流声称 vs 隐含高换热系数矛盾
    _, f9 = audit_thermal_kcl_and_inversion(
        {"Rsa": 0.70, "Pcu": 9.0, "Pfe": 3.0, "Peddy": 5.5, "Ta": 40.0, "A_case": 0.01429},
        reported={"Tw": 59.46},
        meta={"cooling_type": "natural"},
    )
    check("⑨ I2 检出自然对流声称与隐含 16.7× 换热系数的量级矛盾",
          any(x.code == "I2_cooling_regime_mismatch" for x in f9), str([x.code for x in f9]))

    # 10) I3: Motor-CAD 五温度点集合成员交集检验（Rws 交集非空 vs Rsa 交集为空）
    mc_pts = [
        {"Ta": 45, "Ts": 96.82, "Tw": 160.43, "Pcu": 572.17, "Ptot": 705.42},
        {"Ta": 65, "Ts": 92.34, "Tw": 155.50, "Pcu": 571.49, "Ptot": 704.84},
        {"Ta": 85, "Ts": 108.08, "Tw": 172.81, "Pcu": 573.97, "Ptot": 707.00},
        {"Ta": 105, "Ts": 126.93, "Tw": 193.52, "Pcu": 576.59, "Ptot": 709.31},
    ]
    rep10, f10 = audit_multipoint_consistency(mc_pts, delta_T_K=1.5, rel_P_unc=0.03)
    check("⑩ I3 Motor-CAD 多工况：Rws 交集非空、Rsa 交集为空且检出非单调温升",
          rep10["Rws_summary"]["set_intersection_non_empty"] is True
          and rep10["Rsa_summary"]["set_intersection_non_empty"] is False
          and rep10["non_monotonic_temperature_response"] is True
          and any(x.code == "I3_rsa_non_constant" for x in f10),
          str(rep10.get("Rsa_summary")))

    # 11) I3: AIP 2023 五功率点检出 6.4kW 最大正残差 16.5K 与 8.8kW 漏报点
    aip_pts = [
        {"P_kW": 5.5, "Tw_calc": 106.5, "Tw_exp": 112.0},
        {"P_kW": 6.4, "Tw_calc": 118.0, "Tw_exp": 134.5},
        {"P_kW": 7.4, "Tw_calc": 133.0, "Tw_exp": 141.0},
        {"P_kW": 8.4, "Tw_calc": 147.0, "Tw_exp": 152.0},
        {"P_kW": 8.8, "Tw_calc": 151.0, "Tw_exp": 158.5},
    ]
    rep11, f11 = audit_multipoint_consistency(aip_pts, tw_limit_C=155.0)
    mvr = rep11.get("model_vs_reference") or {}
    check("⑪ I3 AIP 五功率点：检出 16.5K 最大残差与 8.8kW 漏报点",
          abs(mvr.get("max_underestimation_K", 0) - 16.5) < 1e-6
          and mvr.get("missed_violation_count") == 1
          and any(x.code == "I3_missed_violation_detected" for x in f11),
          str(mvr))

    # 12) I4: Energies 2021 三热阻并联合成检验 (Req,w=0.07, Req,sr=0.382, Rfc=0.167 -> Rsa=0.1162, share=37.6%)
    rep12, f12 = audit_topology_reduction({
        "Req_w": 0.07, "Req_sr": 0.382, "Rfc": 0.167, "Rew_a": 0.446,
        "claimed_Rsa": 0.1162,
    })
    check("⑫ I4 Energies 2021 串并联拓扑合成：Rsa=0.1162 K/W、Rws 占比 37.6%",
          not f12 and abs(rep12.get("Rws_share_in_rotating", 0) - 0.376) < 0.001,
          str(rep12))

    # 13) 覆盖率：只给最小集时，必须明说「一条判据都没触发」
    #     否则 AUDIT_PASS 会被读成「很干净」，实际是「没东西可审」。
    bare = {"Pcu": 13.6, "Pfe": 5.33, "Ta": 40.0}
    cov_bare = build_coverage(bare, {}, [])
    check("⑬ 覆盖率：最小集（Pcu/Pfe/Ta）→ 0 条触发、全部记为缺字段",
          cov_bare["audited_count"] == 0
          and cov_bare["skipped_count"] == cov_bare["total"]
          and "0/" in cov_bare["note"],
          cov_bare["note"])

    # 14) 覆盖率：补齐 I_rms / R_phase 后，C2a 必须从「未审」变为「已审」
    rich = dict(bare, I_rms=15.9, R_phase=0.018)
    cov_rich = build_coverage(rich, {}, [])
    check("⑭ 覆盖率：补 I_rms/R_phase → 仍有 0 条触发（C2a 需真跑过才算）",
          cov_rich["audited_count"] == 0,
          f"skipped={cov_rich['skipped_count']}")

    cov_ran = build_coverage(rich, {}, ["C2a_copper_loss_ohm"])
    skipped_names = {s["check"] for s in cov_ran["skipped"]}
    check("⑮ 覆盖率：跑过 C2a 后它从「未审」列表消失，且缺字段标注准确",
          cov_ran["audited_count"] == 1
          and "C2a_copper_loss_ohm" not in skipped_names
          and any("C1a_flux_linkage" == s["check"] and "lambda_pm" in s["missing"]
                  for s in cov_ran["skipped"]),
          cov_ran["note"])

    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


def main(argv: List[str]) -> int:
    if not argv or argv[0] == "selftest":
        return 0 if selftest() else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
