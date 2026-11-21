#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 第一层：口径审计（palette audit）
=====================================

核心原则：一个数字，如果不知道它的口径，就不能进入任何运算。

每个量不是 value，而是一条记录：
    value + unit + quantity + palette（口径指纹）+ source + verified

判定规则只有一条硬的：
    同一个运算里出现两个 palette 冲突的量，就报错。

八条规则，每条对应一处真实事故（joint-module 2026-09 ~ 2026-11）：
    R1 口径混用      —— 2D 铜损 7.67 W 与解析 13.6 W 混用
    R2 匝数并存      —— 「13.82 W 不是 8 匝专属」
    R3 分母不闭合    —— 效率 334.4 vs 333.6「差异原因查不到」
    R4 上界当结论    —— 磁钢涡流 15.24 W（未轴向分段上界）
    R5 未收敛引用    —— 3D 1 pass、ee=1.43% 仍被引用
    R6 基准不唯一    —— 齿槽 279.499 / 270.497 / 220.75
    R7 温度定义不一  —— 平均温度与热点混用
    R8 量纲不一致    —— 单位冲突

另加三条工程规则：
    R9  引用断裂      —— derived_from 指向不存在的量
    R10 未标记口径    —— 参与结论但 palette 全空
    R11 同量多值      —— 同名同单位同范围但数值不同（>0.1%）

用法：
    python mdca_palette.py                # 跑内置 joint-module 数据集
    python mdca_palette.py --selftest     # 只跑规则自测
    python mdca_palette.py --scan <md>    # 扫描 Markdown 中的 @{...} 标记
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------
# 口径指纹
# --------------------------------------------------------------------------

PALETTE_FIELDS = (
    "turns",          # 匝数版本：8 / 6
    "dim",            # 维度：2D / 3D
    "method",         # 方法：analytic / analytic_scaled / fem / fem_transient / measured / design_target
    "end_winding",    # 端部绕组：included / excluded
    "scope",          # 范围：motor / module
    "load_case",      # 工况：continuous / peak / rated / short_circuit
    "current_basis",  # 电流口径：rms / peak
    "temp_basis",     # 温度口径：mean / hotspot / 具体℃
    "baseline",       # 基准：降幅基准 / 分母基准
    "convergence",    # 收敛：converged / not_converged
    "verified",       # 是否经过外部校准或实测
)

# 只有这些字段"混了就一定是错"。method 不在此列：仿真的齿槽转矩除以解析的
# 连续转矩（占比）、解析转矩除以设计目标质量（扭矩密度）都是正常工程做法，
# 把 method 当冲突项会制造大量误报。
MIX_BLOCKING = (
    "turns",          # 8 匝的磁链不能和 6 匝的转矩混算
    "dim",            # 2D 的量与 3D 的量不可直接合并
    "end_winding",    # 含端部与不含端部的铜损不可相加
    "scope",          # 本体与模组不可混算
    "load_case",      # 连续与峰值工况不可混算
    "current_basis",  # rms 与 peak 不可直接相乘
    "temp_basis",     # 平均温度与热点不可比
    "baseline",       # 降幅/占比基准必须同一
)


@dataclass(frozen=True)
class Palette:
    turns: Optional[str] = None
    dim: Optional[str] = None
    method: Optional[str] = None
    end_winding: Optional[str] = None
    scope: Optional[str] = None
    load_case: Optional[str] = None
    current_basis: Optional[str] = None
    temp_basis: Optional[str] = None
    baseline: Optional[str] = None
    convergence: Optional[str] = None
    verified: Optional[bool] = None

    def conflicts_with(self, other: "Palette",
                       fields: Optional[Tuple[str, ...]] = None) -> List[Tuple[str, Any, Any]]:
        """只比较双方都非 None 的字段；一侧为 None 表示该口径未声明，不算冲突。"""
        keys = fields if fields is not None else PALETTE_FIELDS
        hits = []
        for k in keys:
            a = getattr(self, k)
            b = getattr(other, k)
            if a is None or b is None:
                continue
            if a != b:
                hits.append((k, a, b))
        return hits

    def is_tagged(self) -> bool:
        return any(getattr(self, k) is not None for k in PALETTE_FIELDS)

    def as_dict(self) -> Dict[str, Any]:
        return {
            k: getattr(self, k)
            for k in PALETTE_FIELDS
            if getattr(self, k) is not None
        }


# --------------------------------------------------------------------------
# 记录
# --------------------------------------------------------------------------


@dataclass
class Quantity:
    id: str
    name: str                      # 物理量名（同 name 才可能互相替代）
    value: float
    unit: str
    palette: Palette = field(default_factory=Palette)
    source: str = ""               # 出处（文件:行 / 章节）
    in_use: bool = False           # 是否被当作结论引用
    derived_from: Tuple[str, ...] = ()
    allowed_changes: Tuple[str, ...] = ()   # 本派生有意改变的 palette 字段
    closure: Optional[Dict[str, str]] = None  # 闭合检查：{"loss": id, "denom": id, "denom_kind": "output"/"input"}
    conservatism: Optional[str] = None  # conservative / optimistic / None=未声明
    note: str = ""


@dataclass
class Issue:
    rule: str
    severity: str                  # FAIL / WARN / INFO
    message: str
    ids: Tuple[str, ...] = ()
    detail: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# 审计器
# --------------------------------------------------------------------------


class Auditor:
    def __init__(self, quantities: List[Quantity]):
        self.qs: Dict[str, Quantity] = {}
        self.dup_ids: List[str] = []
        for q in quantities:
            if q.id in self.qs:
                self.dup_ids.append(q.id)
            self.qs[q.id] = q
        self.issues: List[Issue] = []
        self.closure_rows: List[Dict[str, Any]] = []

    # -- 工具 ---------------------------------------------------------------

    def add(self, rule: str, sev: str, msg: str,
            ids: Tuple[str, ...] = (), detail: Optional[Dict[str, Any]] = None) -> None:
        self.issues.append(Issue(rule, sev, msg, tuple(ids), detail or {}))

    def ins(self, q: Quantity) -> List[Quantity]:
        return [self.qs[i] for i in q.derived_from if i in self.qs]

    # -- R1 口径混用 --------------------------------------------------------

    def r1_palette_mix(self) -> None:
        for q in self.qs.values():
            if not q.derived_from:
                continue
            inputs = self.ins(q)
            # 输入之间：不同口径的量放进同一个运算，几乎必然是错的
            for i in range(len(inputs)):
                for j in range(i + 1, len(inputs)):
                    a, b = inputs[i], inputs[j]
                    for k, av, bv in a.palette.conflicts_with(b.palette, MIX_BLOCKING):
                        self.add(
                            "R1", "FAIL",
                            f"{q.id} 的两个输入口径冲突：{a.id}({k}={av}) vs {b.id}({k}={bv})",
                            (q.id, a.id, b.id),
                            {"field": k, "a": av, "b": bv},
                        )
            # 输入与结果：跳过 allowed_changes 里声明过的字段
            for a in inputs:
                for k, av, rv in a.palette.conflicts_with(q.palette, MIX_BLOCKING):
                    if k in q.allowed_changes:
                        continue
                    self.add(
                        "R1", "WARN",
                        f"{q.id}({k}={rv}) 由 {a.id}({k}={av}) 派生，未声明 allowed_changes",
                        (q.id, a.id),
                        {"field": k, "input": av, "result": rv},
                    )

    # -- R2 匝数/口径版本并存 ----------------------------------------------

    def r2_multi_version(self) -> None:
        """同一个量存在多种口径版本（匝数 / 电流基准 / 维度）时提示对照。
        这本身正常且必要，但引用时必须显式给出对照表，否则读者会自行配对。"""
        groups: Dict[str, List[Quantity]] = {}
        for q in self.qs.values():
            if q.palette.turns is not None or q.palette.current_basis is not None:
                groups.setdefault(q.name, []).append(q)
        for name, qs in groups.items():
            tags = set()
            for q in qs:
                parts = []
                if q.palette.turns:
                    parts.append(f"{q.palette.turns}匝")
                if q.palette.current_basis:
                    parts.append(q.palette.current_basis)
                if q.palette.dim:
                    parts.append(q.palette.dim)
                tags.add("/".join(parts) if parts else "未标")
            if len(tags) > 1:
                self.add(
                    "R2", "INFO",
                    f"量 {name} 存在多种口径版本 {sorted(tags)}："
                    + "、".join(q.id for q in qs),
                    tuple(q.id for q in qs),
                    {"versions": sorted(tags)},
                )

    # -- R3 效率/占比分母闭合 -----------------------------------------------

    def r3_closure(self) -> None:
        for q in self.qs.values():
            if not q.closure:
                continue
            loss = self.qs.get(q.closure.get("loss", ""))
            den = self.qs.get(q.closure.get("denom", ""))
            kind = q.closure.get("denom_kind", "output")
            if loss is None or den is None:
                self.add("R3", "FAIL",
                         f"{q.id} 的闭合检查引用了不存在的量",
                         (q.id,), {"closure": q.closure})
                continue
            if den.value == 0:
                continue
            calc = 1.0 - loss.value / den.value
            declared = q.value / 100.0
            diff_pp = (calc - declared) * 100.0

            if kind == "output":
                alt_den = den.value + loss.value
                alt_kind = "input"
            else:
                alt_den = den.value - loss.value
                alt_kind = "output"
            alt = 1.0 - loss.value / alt_den if alt_den > 0 else float("nan")

            row = {
                "id": q.id,
                "declared_pct": q.value,
                "loss_id": loss.id,
                "loss_w": loss.value,
                "denom_id": den.id,
                "denom_w": den.value,
                "denom_kind": kind,
                "calc_pct": round(calc * 100, 4),
                "diff_pp": round(diff_pp, 4),
                "alt_denom_kind": alt_kind,
                "alt_pct": round(alt * 100, 4) if alt == alt else None,
            }
            self.closure_rows.append(row)

            if abs(diff_pp) > 0.05:
                self.add(
                    "R3", "FAIL",
                    f"{q.id}：声明 {q.value}%，按 {kind} 分母算得 {calc * 100:.2f}%"
                    f"（差 {diff_pp:+.2f} pp）",
                    (q.id, loss.id, den.id),
                    {"closure": row},
                )

        # 同一组效率里分母定义是否统一
        kinds = {r["denom_kind"] for r in self.closure_rows}
        if len(kinds) > 1:
            by_kind: Dict[str, List[str]] = {}
            for r in self.closure_rows:
                by_kind.setdefault(r["denom_kind"], []).append(r["id"])
            self.add(
                "R3", "WARN",
                "同一组效率使用了不同的分母定义：" +
                "；".join(f"{k} → {', '.join(v)}" for k, v in by_kind.items()),
                tuple(r["id"] for r in self.closure_rows),
                {"by_kind": by_kind, "rows": self.closure_rows},
            )

    # -- R4 上界/未验证当结论 -----------------------------------------------

    def _grade_bound(self, q: Quantity) -> Tuple[str, str]:
        """未验证/未收敛数据能否使用，取决于它往哪边偏：
        上界用于算最坏情况（保守）是合法工程做法；用于证明合格则是错的。"""
        if q.conservatism == "conservative":
            return "INFO", "（已声明按保守方向使用，允许，但需在结论中注明）"
        if q.conservatism == "optimistic":
            return "FAIL", "（声明为乐观方向：会高估性能，不得用于合格性判断）"
        return "WARN", "（未声明保守方向，必须补 conservatism 字段）"

    def r4_unverified_as_result(self) -> None:
        for q in self.qs.values():
            if q.palette.verified is False and q.in_use:
                sev, tag = self._grade_bound(q)
                self.add(
                    "R4", sev,
                    f"{q.id} = {q.value} {q.unit} 是未验证/上界数据，但被引用{tag}"
                    + (f"；{q.note}" if q.note else ""),
                    (q.id,),
                    {"source": q.source, "conservatism": q.conservatism},
                )

    # -- R5 未收敛引用 ------------------------------------------------------

    def r5_unconverged_in_use(self) -> None:
        for q in self.qs.values():
            if q.palette.convergence == "not_converged" and q.in_use:
                sev, tag = self._grade_bound(q)
                self.add(
                    "R5", sev,
                    f"{q.id} = {q.value} {q.unit} 来自未收敛的求解，但被引用{tag}"
                    + (f"；{q.note}" if q.note else ""),
                    (q.id,),
                    {"source": q.source, "conservatism": q.conservatism},
                )

    # -- R6 基准不唯一 ------------------------------------------------------

    def r6_baseline_versions(self) -> None:
        groups: Dict[str, List[Quantity]] = {}
        for q in self.qs.values():
            if q.palette.baseline is not None:
                groups.setdefault(q.name, []).append(q)
        for name, qs in groups.items():
            bases = sorted({q.palette.baseline for q in qs})
            if len(bases) > 1:
                self.add(
                    "R6", "WARN",
                    f"量 {name} 存在多个基准 {bases}，任何降幅/占比必须显式声明基准",
                    tuple(q.id for q in qs),
                    {"baselines": bases},
                )

    # -- R7 温度定义 --------------------------------------------------------

    def r7_temp_basis(self) -> None:
        groups: Dict[str, List[Quantity]] = {}
        for q in self.qs.values():
            if q.palette.temp_basis is not None:
                groups.setdefault(q.name, []).append(q)
        for name, qs in groups.items():
            bases = sorted({q.palette.temp_basis for q in qs})
            if len(bases) > 1:
                self.add(
                    "R7", "FAIL",
                    f"量 {name} 混用温度定义 {bases}（平均温度与热点不可比）",
                    tuple(q.id for q in qs),
                    {"temp_bases": bases},
                )

    # -- R8 量纲 ------------------------------------------------------------

    def r8_unit_consistency(self) -> None:
        # 同一 derivation 的输入之间
        for q in self.qs.values():
            inputs = self.ins(q)
            for i in range(len(inputs)):
                for j in range(i + 1, len(inputs)):
                    a, b = inputs[i], inputs[j]
                    if a.name == b.name and a.unit != b.unit:
                        self.add(
                            "R8", "FAIL",
                            f"{q.id} 的两个输入同名不同单位：{a.id}={a.value}{a.unit} vs {b.id}={b.value}{b.unit}",
                            (q.id, a.id, b.id),
                            {"a_unit": a.unit, "b_unit": b.unit},
                        )
        # 同 name 且无派生关系
        groups: Dict[str, List[Quantity]] = {}
        for q in self.qs.values():
            groups.setdefault(q.name, []).append(q)
        for name, qs in groups.items():
            units = sorted({q.unit for q in qs})
            if len(units) <= 1:
                continue
            for i in range(len(qs)):
                for j in range(i + 1, len(qs)):
                    a, b = qs[i], qs[j]
                    if a.unit == b.unit:
                        continue
                    if a.palette.scope != b.palette.scope:
                        continue          # 本体与模组是两个对象，单位不同不算冲突
                    related = (b.id in a.derived_from) or (a.id in b.derived_from)
                    if related:
                        continue
                    self.add(
                        "R8", "WARN",
                        f"同名量 {name} 存在不同单位：{a.id}={a.unit} vs {b.id}={b.unit}",
                        (a.id, b.id),
                        {"units": units},
                    )

    # -- R9 引用断裂 --------------------------------------------------------

    def r9_dangling(self) -> None:
        for q in self.qs.values():
            for ref in q.derived_from:
                if ref not in self.qs:
                    self.add("R9", "FAIL",
                             f"{q.id} 引用了不存在的量 {ref}",
                             (q.id,), {"missing": ref})

    # -- R10 未标记口径 -----------------------------------------------------

    def r10_untagged(self) -> None:
        for q in self.qs.values():
            if q.in_use and not q.palette.is_tagged():
                self.add("R10", "WARN",
                         f"{q.id} 参与了结论但未声明任何口径字段",
                         (q.id,), {"source": q.source})

    # -- R11 同量多值 -------------------------------------------------------

    def r11_same_quantity_multi_value(self) -> None:
        """真正的"同量多值"：名称、单位、全部口径字段都相同，只能是同一个数。
        任何一个口径字段不同（匝数/方法/工况/基准…），就是两个可区分的量，不报。"""
        groups: Dict[Tuple[str, str], List[Quantity]] = {}
        for q in self.qs.values():
            groups.setdefault((q.name, q.unit), []).append(q)
        for (name, unit), qs in groups.items():
            if len(qs) < 2:
                continue
            for i in range(len(qs)):
                for j in range(i + 1, len(qs)):
                    a, b = qs[i], qs[j]
                    related = (b.id in a.derived_from) or (a.id in b.derived_from)
                    if related:
                        continue
                    if a.palette != b.palette:
                        continue          # 口径可区分 ⇒ 不是同一个数
                    base = max(abs(a.value), abs(b.value), 1e-12)
                    rel = abs(a.value - b.value) / base
                    if rel > 1e-3:
                        self.add(
                            "R11", "WARN",
                            f"同名同口径的量有两个值：{a.id}={a.value}{unit} "
                            f"vs {b.id}={b.value}{unit}（相对差 {rel * 100:.2f}%）",
                            (a.id, b.id),
                            {"name": name, "rel_diff_pct": round(rel * 100, 4)},
                        )

    # -- 入口 ---------------------------------------------------------------

    def run(self) -> List[Issue]:
        if self.dup_ids:
            self.add("R0", "FAIL",
                     "重复的记录 id：" + "、".join(sorted(set(self.dup_ids))),
                     tuple(sorted(set(self.dup_ids))))
        self.r1_palette_mix()
        self.r2_multi_version()
        self.r3_closure()
        self.r4_unverified_as_result()
        self.r5_unconverged_in_use()
        self.r6_baseline_versions()
        self.r7_temp_basis()
        self.r8_unit_consistency()
        self.r9_dangling()
        self.r10_untagged()
        self.r11_same_quantity_multi_value()
        return self.issues


# --------------------------------------------------------------------------
# 数据集：joint-module 基线（档案 2.2 + 台账已知问题）
# --------------------------------------------------------------------------


def build_joint_module_dataset() -> List[Quantity]:
    P = Palette
    Q = Quantity
    return [
        # ---- 反电势 ----
        Q("emf_line_8t", "line_emf_peak", 23.49, "V",
          P(turns="8", dim="2D", method="fem"), "档案2.2 行1", in_use=True),
        Q("emf_line_6t", "line_emf_peak", 17.62, "V",
          P(turns="6", method="analytic_scaled"), "档案2.2 行1（按6/8折算，非6匝重跑）",
          in_use=True, derived_from=("emf_line_8t",),
          allowed_changes=("turns", "dim", "method")),

        Q("ph_emf_fund_8t", "phase_emf_fundamental", 13.31, "V",
          P(turns="8", dim="2D", method="fem"), "档案2.2 行2", in_use=True),
        Q("ph_emf_peak_8t", "phase_emf_peak", 14.44, "V",
          P(turns="8", dim="2D", method="fem"), "档案2.2 行2", in_use=True),

        # ---- Kt ----
        Q("kt_8t_sweep", "kt", 105.0, "mN.m/A",
          P(turns="8", dim="2D", method="fem", current_basis="peak"), "档案2.2 行3（@10A 扫描）"),
        Q("kt_8t_ana", "kt", 104.6, "mN.m/A",
          P(turns="8", method="analytic", current_basis="peak"), "档案2.2 行3（解析）"),
        Q("kt_6t", "kt", 78.5, "mN.m/A",
          P(turns="6", method="analytic_scaled", current_basis="peak"), "档案2.2 行3（6匝折算）",
          in_use=True, derived_from=("kt_8t_ana",),
          allowed_changes=("turns", "method")),
        Q("kt_3d", "kt", 83.98, "mN.m/A",
          P(turns="6", dim="3D", method="fem", current_basis="peak", convergence="not_converged"),
          "档案2.2 行3（3D，1 pass ee=1.43%）", in_use=True,
          conservatism="optimistic",
          note="3D 未收敛；且比 2D 6匝基线 78.5 高 7%，用于算转矩余量会高估"),

        # ---- 电流 ----
        Q("i_cont_rms", "current", 16.0, "A",
          P(turns="6", method="analytic", load_case="continuous", current_basis="rms"),
          "档案2.2 行4", in_use=True),
        Q("i_peak_rms", "current", 32.2, "A",
          P(turns="6", method="analytic", load_case="peak", current_basis="rms"),
          "档案2.2 行4", in_use=True, note="峰值工况下的有效值"),
        Q("i_peak_pk", "current", 45.54, "A",
          P(turns="6", load_case="peak", current_basis="peak"), "由 32.2×√2 推得", in_use=True,
          derived_from=("i_peak_rms",),
          allowed_changes=("current_basis", "method")),

        # ---- 损耗 ----
        Q("p_cu_ana", "copper_loss", 13.6, "W",
          P(turns="6", method="analytic", load_case="continuous", end_winding="included"),
          "档案2.2 行5（热设计口径）",
          in_use=True, derived_from=("i_cont_rms",),
          allowed_changes=("method", "end_winding")),
        Q("p_cu_2d", "copper_loss", 7.67, "W",
          P(turns="6", dim="2D", method="fem", load_case="continuous", end_winding="excluded"),
          "档案2.2 行5（2D 不含端部）"),
        Q("p_fe", "iron_loss", 5.33, "W",
          P(method="analytic"), "档案2.2 行6", in_use=True, note="FEM 未产出，只有解析值"),
        Q("p_eddy", "magnet_eddy_loss", 15.24, "W",
          P(dim="2D", method="fem", verified=False), "档案2.2 行7", in_use=True,
          conservatism="conservative",
          note="未轴向分段的**上界**；用于算最坏效率是保守的，但不得当热载荷"),

        # ---- 机械功率（用于效率闭合）----
        Q("p_mech_out", "mechanical_power", 334.4, "W",
          P(scope="motor", method="derived"), "课设报告_正文_v2.md:539（1.774×188.5）"),
        Q("p_mech_out_ledger", "mechanical_power", 333.6, "W",
          P(scope="motor", method="derived"), "设计指标差距台账.md:584",
          note="与正文差 0.24%，差异原因至今未查明"),
        Q("p_in_motor", "input_power", 368.6, "W",
          P(scope="motor", method="derived"), "334.4 + 34.2"),

        Q("loss_cu_fe", "total_loss_cu_fe", 18.93, "W",
          P(scope="motor", method="analytic"), "13.6 + 5.33"),
        Q("loss_total", "total_loss_all_eddy", 34.17, "W",
          P(scope="motor", method="analytic"), "13.6 + 5.33 + 15.24（含涡流上界）",
          note="含未验证的涡流上界"),

        # ---- 效率（三种损耗范围，各自独立定义，闭合检查）----
        Q("eta_cu_only", "efficiency_cu_only", 95.9, "%",
          P(scope="motor", method="analytic"), "档案2.2 行8（仅铜损）",
          closure={"loss": "p_cu_ana", "denom": "p_mech_out", "denom_kind": "output"}),
        Q("eta_with_fe", "efficiency_cu_fe", 94.3, "%",
          P(scope="motor", method="analytic"), "档案2.2 行8（含铁损）",
          closure={"loss": "loss_cu_fe", "denom": "p_mech_out", "denom_kind": "output"}),
        Q("eta_with_eddy", "efficiency_all", 90.7, "%",
          P(scope="motor", method="analytic"), "档案2.2 行8（含涡流）",
          closure={"loss": "loss_total", "denom": "p_in_motor", "denom_kind": "input"}),
        Q("eta_joint", "efficiency_joint", 85.3, "%",
          P(scope="module", method="analytic"), "档案2.2 行8（关节总 = 电机 × 0.94）",
          in_use=True, derived_from=("eta_with_eddy",), allowed_changes=("scope", "method")),

        # ---- 齿槽转矩 ----
        Q("cog_base_ch5", "cogging_torque", 279.499, "mN.m",
          P(method="fem", baseline="ch5_L9_scan"), "档案2.2 行9（第5章同批扫描）", in_use=True),
        Q("cog_base_ch4", "cogging_torque", 270.497, "mN.m",
          P(method="fem", baseline="ch4_static"), "档案2.2 行9（第4章磁静）"),
        Q("cog_base_trans", "cogging_torque", 220.75, "mN.m",
          P(dim="2D", method="fem", baseline="transient_last_cycle"), "档案2.2 行9（瞬态末周期）"),
        Q("cog_opt", "cogging_torque", 44.452, "mN.m",
          P(method="fem", baseline="ch5_L9_scan"), "档案2.2 行9（最优组合）", in_use=True,
          derived_from=("cog_base_ch5",), allowed_changes=("baseline",)),
        Q("cog_red_84", "cogging_reduction", 84.1, "%",
          P(baseline="ch5_L9_scan"), "档案2.2 行9（降幅）", in_use=True,
          derived_from=("cog_base_ch5", "cog_opt")),
        Q("cog_ratio_251", "cogging_ratio", 2.51, "%",
          P(baseline="1.774N.m_cont"), "档案2.2 行9（占连续转矩）", in_use=True,
          derived_from=("cog_opt", "t_motor_cont"), allowed_changes=("baseline", "method")),

        # ---- 退磁（一阶磁路，三个工况分开命名）----
        Q("demag_margin_120c_44a", "demag_margin_120c_44a", 2.45, "x",
          P(method="analytic", temp_basis="120C"), "档案2.2 行10", in_use=True,
          note="一阶磁路解析，非 FEM"),
        Q("demag_margin_short", "demag_margin_short", 1.14, "x",
          P(method="analytic", temp_basis="120C"), "档案2.2 行10",
          note="三相短路 261 A，临界"),
        Q("demag_margin_neg20c", "demag_margin_neg20c", 6.78, "x",
          P(method="analytic", temp_basis="-20C"), "档案2.2 行10"),

        # ---- 3D 校核 ----
        Q("lambda_3d", "pm_flux_linkage", 11.1975, "mWb",
          P(turns="6", dim="3D", method="fem", convergence="not_converged"),
          "档案2.2 行11", in_use=True, conservatism="optimistic",
          note="3D 1 pass ee=1.4302% 未收敛，且比 2D 6匝解析 10.46 高 7.05%"),

        # ---- 质量与扭矩密度 ----
        Q("m_iron_mag", "mass", 961.9, "g",
          P(scope="motor", method="design_target"), "档案2.2 行12（几何反算，非称重）"),
        Q("m_motor", "mass", 1.06, "kg",
          P(scope="motor", method="design_target"), "档案2.2 行12（本体估）", in_use=True,
          derived_from=("m_iron_mag",), allowed_changes=("method",)),
        Q("m_module", "mass", 1.45, "kg",
          P(scope="module", method="design_target"), "档案2.2 行12（模组目标 1.4~1.6）"),

        Q("t_motor_cont", "torque", 1.774, "N.m",
          P(scope="motor", method="analytic", load_case="continuous"),
          "档案2.1（=20/(12×0.94)）", in_use=True),
        Q("t_module_peak", "torque", 40.0, "N.m",
          P(scope="module", method="design_target", load_case="peak"), "档案2.1"),

        Q("td_motor_cont", "torque_density", 1.7, "N.m/kg",
          P(scope="motor"), "档案2.2 行13", in_use=True,
          derived_from=("t_motor_cont", "m_motor")),
        Q("td_module_peak", "torque_density", 28.0, "N.m/kg",
          P(scope="module"), "档案2.2 行13",
          derived_from=("t_module_peak", "m_module")),
    ]


# --------------------------------------------------------------------------
# Markdown 扫描：① 已标记的 @{...}   ② 裸数字盘点
# --------------------------------------------------------------------------

TAG_RE = re.compile(
    r"(?P<val>-?\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z%°℃·/]+)?[^\n]{0,24}?`@\{(?P<body>[^}]*)\}`"
)

# 长单位必须排在短单位之前，否则 mN·m/A 会被切成 mN·m
UNIT_ORDER = (
    "mN·m/A", "N·m/kg", "A/mm²", "mN·m", "N·m", "mWb", "kW", "K/W",
    "mΩ", "MPa", "kPa", "rpm", "mm", "kg", "mT", "Hz",
    "W", "V", "A", "Ω", "℃", "K", "g", "%", "T", "Pa",
)
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(UNIT_ORDER, key=len, reverse=True))
NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(" + _UNIT_ALT + r")(?![A-Za-z0-9])")

# 上下文 → 口径字段的推断规则。只用于"盘点"，不用于判定对错。
INFER_RULES = (
    ("dim", re.compile(r"2D|二维|2-D"), "2D"),
    ("dim", re.compile(r"3D|三维|3-D"), "3D"),
    ("method", re.compile(r"解析|理论计算|analytical?"), "analytic"),
    ("method", re.compile(r"仿真|FEM|Maxwell|有限元"), "fem"),
    ("end_winding", re.compile(r"含端部"), "included"),
    ("end_winding", re.compile(r"不含端部|端部不在"), "excluded"),
    ("scope", re.compile(r"模组"), "module"),
    ("scope", re.compile(r"本体"), "motor"),
    ("load_case", re.compile(r"峰值|peak"), "peak"),
    ("load_case", re.compile(r"连续|额定"), "continuous"),
    ("current_basis", re.compile(r"A rms|A\(rms\)|有效值"), "rms"),
    ("convergence", re.compile(r"未收敛|不收敛"), "not_converged"),
    ("verified", re.compile(r"上界|未验证|估算值"), False),
    ("temp_basis", re.compile(r"热点"), "hotspot"),
)
TURNS_RE = re.compile(r"(\d+)\s*匝")


@dataclass
class RawNumber:
    value: float
    unit: str
    line: int
    context: str
    inferred: Dict[str, Any]


def parse_palette_body(body: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for part in body.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip()
        v = v.strip().strip("'\"")
        if k not in PALETTE_FIELDS:
            continue
        if k == "verified":
            v = v.lower() in ("true", "1", "yes")
        out[k] = v
    return out


def scan_markdown(path: Path) -> List[Quantity]:
    """路径一：读 @{...} 显式标记，直接进入审计。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    found: List[Quantity] = []
    lines = text.splitlines()
    for i, m in enumerate(TAG_RE.finditer(text)):
        body = parse_palette_body(m.group("body"))
        value = float(m.group("val"))
        unit = (m.group("unit") or "").strip()
        line = text[: m.start()].count("\n") + 1
        line_text = lines[line - 1] if line - 1 < len(lines) else ""
        found.append(
            Quantity(
                id=f"scan_{i:03d}",
                name=(unit or "unnamed").lower(),
                value=value,
                unit=unit,
                palette=Palette(**{k: v for k, v in body.items() if k in PALETTE_FIELDS}),
                source=f"{path.name}:{line}",
                in_use=True,
                note=line_text.strip()[:80],
            )
        )
    return found


def _infer(context: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field, pat, val in INFER_RULES:
        if field in out:
            continue
        if pat.search(context):
            out[field] = val
    m = TURNS_RE.search(context)
    if "turns" not in out and m:
        out["turns"] = m.group(1)
    return out


def extract_numbers(text: str) -> List[RawNumber]:
    lines = text.splitlines()
    out: List[RawNumber] = []
    for idx, line in enumerate(lines):
        context = " ".join(lines[max(0, idx - 1): idx + 2])
        for m in NUM_RE.finditer(line):
            try:
                value = float(m.group(1))
            except ValueError:
                continue
            out.append(
                RawNumber(
                    value=value,
                    unit=m.group(2),
                    line=idx + 1,
                    context=line.strip()[:110],
                    inferred=_infer(context),
                )
            )
    return out


def inventory(path: Path) -> Dict[str, Any]:
    """路径二：盘点文件里所有带单位的数字，看有多少能查到口径。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    nums = extract_numbers(text)
    marked = len(TAG_RE.findall(text))

    by_unit: Dict[str, int] = {}
    field_counts: Dict[str, int] = {}
    bare: List[RawNumber] = []
    partial: List[RawNumber] = []
    for n in nums:
        by_unit[n.unit] = by_unit.get(n.unit, 0) + 1
        if not n.inferred:
            bare.append(n)
        else:
            partial.append(n)
            for k in n.inferred:
                field_counts[k] = field_counts.get(k, 0) + 1

    # 同单位多值：提示"可能是同一个量的多个版本"
    clusters: Dict[str, List[float]] = {}
    for n in nums:
        clusters.setdefault(n.unit, [])
        if n.value not in clusters[n.unit]:
            clusters[n.unit].append(n.value)

    return {
        "file": str(path),
        "total": len(nums),
        "marked": marked,
        "bare": len(bare),
        "partial": len(partial),
        "bare_pct": round(100.0 * len(bare) / max(len(nums), 1), 1),
        "by_unit": dict(sorted(by_unit.items(), key=lambda kv: -kv[1])),
        "field_counts": dict(sorted(field_counts.items(), key=lambda kv: -kv[1])),
        "clusters": {k: v for k, v in clusters.items() if 3 <= len(v) <= 14},
        "bare_samples": bare[:25],
    }


def print_inventory(inv: Dict[str, Any]) -> None:
    name = Path(inv["file"]).name
    print("=" * 78)
    print(f"数字盘点：{name}")
    print("=" * 78)
    print(f"带单位的数字 ：{inv['total']}")
    print(f"已显式标记   ：{inv['marked']}")
    print(f"可从上下文推断：{inv['partial']}")
    print(f"完全裸数字   ：{inv['bare']}（{inv['bare_pct']}%）")
    print()

    if inv["by_unit"]:
        print("按单位（前 12）：")
        for u, c in list(inv["by_unit"].items())[:12]:
            print(f"  {u:<8}{c:>5}")
        print()

    if inv["field_counts"]:
        print("上下文能推断出的口径字段：")
        for k, c in inv["field_counts"].items():
            print(f"  {k:<14}{c:>5}")
        print()

    if inv["clusters"]:
        print("同单位出现多个值（可能是同一个量的多个版本，需逐一标口径）：")
        for u, vals in list(inv["clusters"].items())[:8]:
            shown = ", ".join(f"{v:g}" for v in sorted(vals)[:10])
            print(f"  {u:<8}{shown}")
        print()

    if inv["bare_samples"]:
        print("完全裸数字抽样（前 15 条）：")
        for n in inv["bare_samples"][:15]:
            print(f"  行{n.line:<5}{n.value:g} {n.unit}   {n.context[:70]}")
        print()


# --------------------------------------------------------------------------
# 规则自测
# --------------------------------------------------------------------------


def run_selftest() -> bool:
    P = Palette
    Q = Quantity
    cases = {}

    # R1 口径混用：2D 铜损与解析电阻进同一个热计算
    qs = [
        Q("cu_2d", "copper_loss", 7.67, "W", P(dim="2D", end_winding="excluded")),
        Q("r_ana", "resistance", 0.018, "ohm", P(method="analytic", end_winding="included")),
        Q("t_rise", "temperature_rise", 60.0, "K", P(method="analytic", end_winding="included"),
          derived_from=("cu_2d", "r_ana")),
    ]
    cases["R1"] = {i.rule for i in Auditor(qs).run()}

    # R2 匝数并存
    qs = [
        Q("k8", "kt", 104.6, "mN.m/A", P(turns="8")),
        Q("k6", "kt", 78.5, "mN.m/A", P(turns="6"), derived_from=("k8",), allowed_changes=("turns",)),
    ]
    cases["R2"] = {i.rule for i in Auditor(qs).run()}

    # R4 上界当结论
    qs = [Q("eddy", "magnet_eddy_loss", 15.24, "W", P(verified=False), in_use=True)]
    cases["R4"] = {i.rule for i in Auditor(qs).run()}

    # R5 未收敛引用
    qs = [Q("lam3d", "pm_flux_linkage", 11.1975, "mWb",
            P(dim="3D", convergence="not_converged"), in_use=True)]
    cases["R5"] = {i.rule for i in Auditor(qs).run()}

    # R3 分母不闭合：95.9% 用 output 分母，90.7% 用 input 分母
    qs = [
        Q("loss_cu", "total_loss", 13.6, "W", P()),
        Q("p_out", "mechanical_power", 334.4, "W", P()),
        Q("eta", "efficiency", 90.7, "%", P(),
          closure={"loss": "loss_cu", "denom": "p_out", "denom_kind": "output"}),
    ]
    cases["R3"] = {i.rule for i in Auditor(qs).run()}

    expect = {
        "R1": "R1",
        "R2": "R2",
        "R3": "R3",
        "R4": "R4",
        "R5": "R5",
    }
    ok = True
    print("=== 规则自测 ===")
    for rule, hit in expect.items():
        got = rule in cases[rule]
        print(f"  {rule}: {'PASS' if got else 'FAIL'} （命中={got}）")
        ok = ok and got
    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------


def summarize(issues: List[Issue], quantities: List[Quantity]) -> Dict[str, Any]:
    def bucket(sev: str) -> List[Dict[str, Any]]:
        return [
            {"rule": i.rule, "severity": i.severity, "message": i.message,
             "ids": list(i.ids), "detail": i.detail}
            for i in issues if i.severity == sev
        ]

    by_rule: Dict[str, int] = {}
    for i in issues:
        by_rule[i.rule] = by_rule.get(i.rule, 0) + 1

    return {
        "model_version": "palette-audit-v0.1",
        "record_count": len(quantities),
        "tagged_count": sum(1 for q in quantities if q.palette.is_tagged()),
        "issue_count": {
            "FAIL": sum(1 for i in issues if i.severity == "FAIL"),
            "WARN": sum(1 for i in issues if i.severity == "WARN"),
            "INFO": sum(1 for i in issues if i.severity == "INFO"),
        },
        "by_rule": by_rule,
        "fail": bucket("FAIL"),
        "warn": bucket("WARN"),
        "info": bucket("INFO"),
        "disclaimer": "仅口径一致性审计，不判断物理正确性，不代表真实电机安全。",
    }


def main(argv: List[str]) -> int:
    if "--selftest" in argv:
        return 0 if run_selftest() else 1

    if "--scan" in argv:
        idx = argv.index("--scan")
        if idx + 1 >= len(argv):
            print("用法：--scan <markdown 文件或目录>")
            return 2
        target = Path(argv[idx + 1])
        is_dir = target.is_dir()
        files = sorted(target.glob("*.md")) if is_dir else [target]
        if not files:
            print(f"没有找到 md 文件：{target}")
            return 2
        invs = []
        for f in files:
            inv = inventory(f)
            invs.append(inv)
            if not is_dir:
                print_inventory(inv)
        if len(invs) > 1 or is_dir:
            print("=" * 78)
            print("汇总（按裸数字率排序）")
            print("=" * 78)
            print(f"{'文件':<38}{'数字':>6}{'可推断':>8}{'裸':>6}{'裸率':>8}")
            for inv in sorted(invs, key=lambda x: -x["bare_pct"]):
                print(f"{Path(inv['file']).name[:36]:<38}{inv['total']:>6}"
                      f"{inv['partial']:>8}{inv['bare']:>6}{inv['bare_pct']:>7.1f}%")
            out_dir = Path(__file__).resolve().parent / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            slim = [{k: v for k, v in i.items() if k != "bare_samples"} for i in invs]
            (out_dir / "inventory.json").write_text(
                json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n盘点 JSON：{out_dir / 'inventory.json'}")
        return 0
    else:
        quantities = build_joint_module_dataset()
        label = "joint-module 基线数据集（档案 2.2 + 台账已知问题）"

    auditor = Auditor(quantities)
    issues = auditor.run()
    report = summarize(issues, quantities)
    report["dataset"] = label
    report["closure_rows"] = auditor.closure_rows

    out_dir = Path(__file__).resolve().parent / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "palette_audit.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 78)
    print("MDCA 口径审计报告  v0.1")
    print("=" * 78)
    print(f"数据集   ：{label}")
    print(f"记录数   ：{report['record_count']}（其中带口径标记 {report['tagged_count']}）")
    c = report["issue_count"]
    print(f"问题数   ：FAIL {c['FAIL']} / WARN {c['WARN']} / INFO {c['INFO']}")
    print()

    for sev, title in (("FAIL", "阻塞项（口径不一致，结论不可用）"),
                       ("WARN", "警告项（需要声明或隔离）")):
        rows = report[sev.lower()]
        if not rows:
            continue
        print(f"--- {title} ---")
        for r in rows:
            ids = ", ".join(r["ids"])
            print(f"[{r['severity']}] {r['rule']}  {r['message']}")
            if ids:
                print(f"          涉及：{ids}")
        print()

    if auditor.closure_rows:
        print("--- 效率闭合明细（R3）---")
        print(f"{'id':<16}{'声明':>8}{'分母类型':>10}{'算得':>9}{'差(pp)':>9}{'换分母后':>10}")
        for r in auditor.closure_rows:
            alt = "—" if r["alt_pct"] is None else f"{r['alt_pct']:.2f}"
            print(f"{r['id']:<16}{r['declared_pct']:>8}{r['denom_kind']:>10}"
                  f"{r['calc_pct']:>9.2f}{r['diff_pp']:>+9.2f}{alt:>10}")
        print()

    print(f"JSON 报告：{out_path}")
    print("提示：本报告只查口径一致性，不判断物理正确性。")
    return 0 if c["FAIL"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
