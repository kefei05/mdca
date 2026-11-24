#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 几何缩放律：让 Rws / Rsa / Rrs 随几何变化
================================================

问题：现在三个热阻都是人工填的常数。候选方案一旦改了气隙、槽型、叠长、冷却面，
热阻就不再成立——而"批量筛选候选方案"正是 MDCA 的卖点。

做法：用 R = L / (λ·A) 的一阶形式，把热阻表达成几何的函数，以一台**已验证过的
基准电机**定标：

    Rws ∝ L_ins / (λ_eff · A_slot)      A_slot = Z · P_slot · L      （槽壁总面积）
    Rsa ∝ 1 / A_case                    （壳体/机身散热面积）
    Rrs ∝ g / (λ_air · A_gap)           A_gap  = π · D_gap · L      （气隙面积）

三条缩放律的物理含义：

  · 叠长 L 变长 ⇒ 槽壁面积与气隙面积同比增大 ⇒ Rws、Rrs 下降，Rsa 按机壳面积下降
  · 气隙 g 变宽 ⇒ Rrs 线性上升
  · 外径/散热面积变大 ⇒ Rsa 下降

**这套缩放律没有校准过。** 所以它输出的是中心值 + 一个固定的相对不确定度区间，
并且当候选几何偏离基准超过阈值时，必须降级为 MODEL_REVIEW（方案 11.10 的外推条款）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class Geometry:
    """几何输入。长度单位 mm，面积单位 m²。"""
    Z: float          # 槽数
    P_slot: float     # 单槽周长 mm
    L: float          # 叠长 mm
    D_gap: float      # 气隙直径 mm
    g: float          # 气隙 mm
    A_case: float     # 壳体/安装面散热面积 m²
    L_ins: float      # 绕组到槽壁的等效厚度 mm

    @property
    def A_slot(self) -> float:
        """槽壁总面积 m²。"""
        return self.Z * self.P_slot * self.L / 1e6

    @property
    def A_gap(self) -> float:
        """气隙圆柱面积 m²。"""
        return math.pi * self.D_gap * self.L / 1e6

    def ratios(self, ref: "Geometry") -> Dict[str, float]:
        return {
            "L": self.L / ref.L,
            "g": self.g / ref.g,
            "D_gap": self.D_gap / ref.D_gap,
            "A_case": self.A_case / ref.A_case,
            "A_slot": self.A_slot / ref.A_slot,
            "A_gap": self.A_gap / ref.A_gap,
            "L_ins": self.L_ins / ref.L_ins,
            "Z": self.Z / ref.Z,
        }


@dataclass
class ScaleResult:
    Rws: float
    Rsa: float
    Rrs: float
    Rws_interval: Tuple[float, float]
    Rsa_interval: Tuple[float, float]
    Rrs_interval: Tuple[float, float]
    deviations: Dict[str, float]
    max_deviation: float
    within_reference: bool
    notes: list = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "Rws": round(self.Rws, 4),
            "Rsa": round(self.Rsa, 4),
            "Rrs": round(self.Rrs, 4),
            "intervals": {
                "Rws": [round(v, 4) for v in self.Rws_interval],
                "Rsa": [round(v, 4) for v in self.Rsa_interval],
                "Rrs": [round(v, 4) for v in self.Rrs_interval],
            },
            "ratios": {k: round(v, 4) for k, v in self.deviations.items()},
            "max_deviation": round(self.max_deviation, 4),
            "within_reference": self.within_reference,
            "notes": self.notes,
        }


# 参与"外推判定"的几何量（L_ins 跟随线径，不单独判）
_DEVIATION_KEYS = ("L", "g", "D_gap", "A_case")


def scale(ref_geom: Geometry, ref_R: Dict[str, float],
          geom: Geometry, rel_unc: float = 0.25,
          max_dev: float = 0.30) -> ScaleResult:
    """以基准电机定标，把三个热阻按几何缩放。

    ref_R: {"Rws":..., "Rsa":..., "Rrs":...} —— 基准几何下的热阻（已验证）
    rel_unc: 缩放律自身的相对不确定度（默认 ±25%，未校准的经验值）
    max_dev: 允许的几何偏离阈值（默认 ±30%）
    """
    r = geom.ratios(ref_geom)

    # Rws ∝ L_ins / (λ·A_slot)  ⇒  Rws_new = Rws_ref × (A_slot_ref/A_slot) × (L_ins/L_ins_ref)
    Rws = ref_R["Rws"] * (1.0 / r["A_slot"]) * r["L_ins"]
    # Rsa ∝ 1/A_case
    Rsa = ref_R["Rsa"] * (1.0 / r["A_case"])
    # Rrs ∝ g / A_gap
    Rrs = ref_R["Rrs"] * r["g"] * (1.0 / r["A_gap"])

    devs = {k: r[k] - 1.0 for k in _DEVIATION_KEYS}
    max_deviation = max(abs(v) for v in devs.values())

    notes = [
        "缩放律未校准：不确定度取 ±%.0f%%，不是统计区间" % (rel_unc * 100),
        "Rws ∝ L_ins/(λ·A_slot)，Rsa ∝ 1/A_case，Rrs ∝ g/A_gap",
    ]
    if max_deviation > max_dev:
        notes.append(
            f"几何偏离基准最大 {max_deviation * 100:.1f}%（阈值 {max_dev * 100:.0f}%）"
            "：属外推，应降级为 MODEL_REVIEW")

    return ScaleResult(
        Rws=Rws, Rsa=Rsa, Rrs=Rrs,
        Rws_interval=(Rws * (1 - rel_unc), Rws * (1 + rel_unc)),
        Rsa_interval=(Rsa * (1 - rel_unc), Rsa * (1 + rel_unc)),
        Rrs_interval=(Rrs * (1 - rel_unc), Rrs * (1 + rel_unc)),
        deviations=devs,
        max_deviation=max_deviation,
        within_reference=max_deviation <= max_dev,
        notes=notes,
    )


# --------------------------------------------------------------------------
# joint-module 基准（唯一有数据的一台）
# --------------------------------------------------------------------------

JOINT_REF_GEOM = Geometry(
    Z=12, P_slot=35.0, L=30.0, D_gap=70.6, g=0.6,
    A_case=0.017, L_ins=1.5,
)
JOINT_REF_R = {"Rws": 1.16, "Rsa": 2.03, "Rrs": 3.0}


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------


def selftest() -> bool:
    ok = True
    print("=== 缩放律自测 ===")

    def check(label: str, cond: bool, extra: str = "") -> None:
        nonlocal ok
        print(f"  {label:<44}{'PASS' if cond else 'FAIL'} {extra}")
        ok = ok and cond

    ref = JOINT_REF_GEOM

    # ① 几何相同 ⇒ 热阻不变
    s = scale(ref, JOINT_REF_R, ref, max_dev=10.0)
    same = (abs(s.Rws - JOINT_REF_R["Rws"]) < 1e-9
            and abs(s.Rsa - JOINT_REF_R["Rsa"]) < 1e-9
            and abs(s.Rrs - JOINT_REF_R["Rrs"]) < 1e-9)
    check("① 与基准几何相同 → 热阻不变", same,
          f"Rws={s.Rws:.4f} Rsa={s.Rsa:.4f} Rrs={s.Rrs:.4f}")

    # ② 叠长加倍 ⇒ 三个热阻都下降
    g2 = Geometry(**{**ref.__dict__, "L": 60.0, "A_case": 0.034})
    s2 = scale(ref, JOINT_REF_R, g2, max_dev=10.0)
    check("② 叠长与散热面积加倍 → 三个热阻均下降",
          s2.Rws < s.Rws and s2.Rsa < s.Rsa and s2.Rrs < s.Rrs,
          f"Rws {s.Rws:.3f}→{s2.Rws:.3f} Rsa {s.Rsa:.3f}→{s2.Rsa:.3f} Rrs {s.Rrs:.3f}→{s2.Rrs:.3f}")

    # ③ 气隙变宽 ⇒ 只有 Rrs 上升（线性）
    g3 = Geometry(**{**ref.__dict__, "g": 0.9})
    s3 = scale(ref, JOINT_REF_R, g3, max_dev=10.0)
    check("③ 气隙 ×1.5 → Rrs ×1.5，其余不变",
          abs(s3.Rrs - JOINT_REF_R["Rrs"] * 1.5) < 1e-6
          and abs(s3.Rws - s.Rws) < 1e-9,
          f"Rrs {s3.Rrs:.3f}")

    # ④ 偏离超阈值 → 标记外推
    g4 = Geometry(**{**ref.__dict__, "L": 45.0})
    s4 = scale(ref, JOINT_REF_R, g4, max_dev=0.30)
    check("④ 叠长 +50% 超阈值 → within_reference=False",
          not s4.within_reference, f"max_dev={s4.max_deviation:.3f}")

    # ⑤ 区间包含中心值
    check("⑤ 区间包含中心值",
          s4.Rws_interval[0] < s4.Rws < s4.Rws_interval[1],
          f"[{s4.Rws_interval[0]:.3f}, {s4.Rws_interval[1]:.3f}] ∋ {s4.Rws:.3f}")

    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(0 if selftest() else 1)
