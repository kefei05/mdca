#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据码 → 电机部位映射（可视化的地基）。

为什么单独一个模块：
    可视化要回答「错在哪」，判据码要回答「错在什么物理环节」。两者之间需要
    一张稳定的映射表。把它独立出来，判据改代码时不用动它，画图改样式时也不用
    动判据——而且可以脱离审计器单独测试。

两条纪律（来自高亮原型 v2 的教训：第一版满屏红等于没高亮）：
    1. 红只给最重的一处，且只亮对应部位，不涂整圈
    2. 数据层（单位错 / 哈希不符 / 口径混用 / 没来源）没有几何位置，
       只做旁标签，禁止硬涂到零件上——假定位违背「只审不算」的诚实原则
"""

from __future__ import annotations

import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional

# --------------------------------------------------------------------------
# 部位定义
# --------------------------------------------------------------------------

PART_NAMES: Dict[str, str] = {
    "winding": "绕组槽",
    "magnet": "磁钢 / 气隙",
    "stator_thermal": "定子→机壳热路",
    "rotor": "转子支路",
    "bus": "母线 / 反电势",
    "data": "数据层（无几何位置）",
}

# 数据层不可上图：强行给几何位置会造成假定位。
PART_DRAWABLE: Dict[str, bool] = {
    "winding": True,
    "magnet": True,
    "stator_thermal": True,
    "rotor": True,
    "bus": True,
    "data": False,
}

SEVERITY_RANK: Dict[str, int] = {"BLOCK": 3, "REVIEW": 2, "INFO": 1}

# 配色纪律：红只给 primary，橙给 secondary（只描边），其余淡灰透明。
PART_COLOR: Dict[str, str] = {
    "BLOCK": "#e5484d",
    "REVIEW": "#e8862e",
    "INFO": "#8b93a1",
    "muted": "#c9cfda",
}

# --------------------------------------------------------------------------
# 判据码 → 部位
# --------------------------------------------------------------------------

CODE_TO_PART: Dict[str, str] = {
    # ---- 数据层：契约 / 单位 / 口径 / 来源，无几何位置 ----
    "P0_contract": "data",
    "P1_source_hash": "data",
    "P2_temp_basis": "data",
    "V1_missing": "data",
    "V1_unit": "data",
    "V2_unverified": "data",
    "V2_optimistic": "data",
    "V3_palette_mix": "data",
    "V5_interval": "data",
    "V6_positive": "data",
    "S0_scaling_input": "data",
    "V1_plugin_missing": "data",
    "B0_plugin_failed": "data",
    "B0_plugin_name": "data",
    "B0_plugin_bad_param": "data",
    "C3_efficiency_mismatch": "data",   # 分母口径问题，不是某个零件错了
    "C3_efficiency_denom_bug": "data",

    # ---- 磁钢 / 气隙 ----
    "C1_flux_mismatch": "magnet",
    "C1_kt_basis_mismatch": "magnet",
    "C1_kt_mismatch": "magnet",
    "C1_flux_turns_trap": "magnet",

    # ---- 母线 / 反电势 ----
    "C1_emf_mismatch": "bus",
    "C1_bus_voltage_exceeded": "bus",
    "C1_bus_voltage_tight": "bus",

    # ---- 绕组槽 ----
    "C2_copper_loss_mismatch": "winding",
    "C2_turn_scaling_linear_bug": "winding",
    "C2_slot_fill_unmatched": "winding",

    # ---- 转子支路 ----
    "C3_mech_power_mismatch": "rotor",
    "I2_negative_implied_Rrs": "rotor",

    # ---- 定子 → 机壳热路 ----
    "I1_feedback_unstable": "stator_thermal",
    "I1_kcl_violation": "stator_thermal",   # 默认落点，按指纹细分
    "I2_non_positive_temp_rise": "stator_thermal",
    "I2_negative_implied_R": "stator_thermal",
    "I2_negative_implied_Rws": "stator_thermal",
    "I3_missed_violation_detected": "stator_thermal",
    "I3_speed_dependent_resistance": "stator_thermal",
    "I3_rsa_non_constant": "stator_thermal",
    "I3_non_monotonic_response": "stator_thermal",
    "I3_systematic_underestimation": "stator_thermal",
    "I4_non_positive_branch": "stator_thermal",
    "I4_topology_reduction_mismatch": "stator_thermal",
}

# 同一判据下，不同错误指纹落在不同部位：I1 就是这么一种。
# 按指纹细分，比按判据码一刀切准确得多。
# 值可以是多个部位——一个错法常常跨两处（铜损在绕组，漏掉的却是 Rsa 那一段）。
FINGERPRINT_TO_PART: Dict[str, tuple] = {
    "kcl_missing_pcu_in_rsa": ("winding", "stator_thermal"),  # 铜损只乘 Rws，漏了流经 Rsa 的压降
    "kcl_pfe_through_rws": ("stator_thermal",),                # 铁损错乘了绕组热阻
    "topo_rotor_on_winding": ("rotor",),                       # 转子热阻挂到了绕组上
    "kcl_missing_peddy": ("rotor",),                           # 漏计转子涡流损耗
    "unconverged_cold": ("data",),                             # 迭代策略问题，不是零件错
    "unconverged_1step": ("data",),
}


def _fingerprints_of(issue: Mapping[str, Any]) -> List[str]:
    """从 issue 里挖出指纹名。I1 把它们存在 params 或 detail 的 *_fingerprint 键。"""
    out: List[str] = []
    for holder in (issue, issue.get("params") or {}, issue.get("detail") or {}):
        if not isinstance(holder, Mapping):
            continue
        for k, v in holder.items():
            if k.endswith("_fingerprint") and isinstance(v, str) and v:
                out.append(v)
    return out


def parts_of(issue: Mapping[str, Any]) -> List[str]:
    """单条 issue 落在哪些部位。指纹优先于判据码；一条可落多处。"""
    out: List[str] = []
    for fp in _fingerprints_of(issue):
        for part in FINGERPRINT_TO_PART.get(fp, ()):
            if part not in out:
                out.append(part)
    if out:
        return out
    code = str(issue.get("code") or "")
    # 未登记的判据码一律归数据层：宁可不上图，也不猜位置。
    return [CODE_TO_PART.get(code, "data")]


def part_of(issue: Mapping[str, Any]) -> str:
    """单条 issue 的主部位（多处时取第一个），给只需要一个答案的调用方。"""
    ps = parts_of(issue)
    return ps[0] if ps else "data"


def locate(issues: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """把一批 issue 归到部位上，并定出「红给谁、橙给谁」。

    返回：
      {
        "parts": {part_id: {name, severity, color, drawable, codes, count}},
        "primary": part_id | None,        # 红只给它
        "secondary": [part_id, ...],      # 橙，只描边
        "data_layer": [{code, severity}], # 有错但没有几何位置
        "drawable": bool,                 # 是否能画出任何东西
      }
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    data_layer: List[Dict[str, Any]] = []

    for it in issues:
        sev = str(it.get("severity") or "INFO").upper()
        parts_here = parts_of(it)
        drawable_parts = [p for p in parts_here if p != "data"]
        if not drawable_parts:
            data_layer.append({"code": it.get("code"), "severity": sev})
            continue
        for part in drawable_parts:
            b = buckets.setdefault(part, {"codes": set(), "count": 0, "severity": "INFO"})
            b["codes"].add(str(it.get("code")))
            b["count"] += 1
            if SEVERITY_RANK.get(sev, 0) > SEVERITY_RANK.get(b["severity"], 0):
                b["severity"] = sev

    parts: Dict[str, Dict[str, Any]] = {}
    for part, b in buckets.items():
        parts[part] = {
            "name": PART_NAMES.get(part, part),
            "severity": b["severity"],
            "color": PART_COLOR.get(b["severity"], PART_COLOR["INFO"]),
            "drawable": PART_DRAWABLE.get(part, False),
            "codes": sorted(b["codes"]),
            "count": b["count"],
        }

    # 红只给一处：最重的部位里，出错条目最多的那个。
    ranked = sorted(
        parts.items(),
        key=lambda kv: (-SEVERITY_RANK.get(kv[1]["severity"], 0), -kv[1]["count"]),
    )
    primary: Optional[str] = ranked[0][0] if ranked else None
    secondary = [
        p for p, info in ranked[1:]
        if SEVERITY_RANK.get(info["severity"], 0) >= SEVERITY_RANK["REVIEW"]
    ]

    for p in parts:
        if p == primary:
            parts[p]["role"] = "primary"
            # 红只给主错
            parts[p]["paint"] = "fill"
        elif p in secondary:
            parts[p]["role"] = "secondary"
            # 次重一律橙、只描边——与自己原本的 severity 无关，层级比颜色重要
            parts[p]["color"] = PART_COLOR["REVIEW"]
            parts[p]["paint"] = "stroke"
        else:
            parts[p]["role"] = "muted"
            parts[p]["paint"] = "muted"

    return {
        "parts": parts,
        "primary": primary,
        "secondary": secondary,
        "data_layer": data_layer,
        "drawable": bool(parts),
        "note": _note(primary, secondary, data_layer),
    }


def _note(primary: Optional[str], secondary: List[str], data_layer: List[Dict[str, Any]]) -> str:
    if not primary and not data_layer:
        return "无问题可标注"
    bits: List[str] = []
    if primary:
        bits.append(f"主错在「{PART_NAMES.get(primary, primary)}」")
    if secondary:
        bits.append("次重：" + "、".join(PART_NAMES.get(s, s) for s in secondary))
    if data_layer:
        bits.append(f"{len(data_layer)} 条数据层问题无几何位置，只做旁标签")
    return "；".join(bits)


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------


def selftest() -> bool:
    ok = True

    def check(label: str, cond: bool, detail: Any = "") -> None:
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'} {label}"
              + (f"  {detail}" if detail != "" else ""))

    # ① 单条 BLOCK：算出主错部位
    r = locate([{"code": "C2_copper_loss_mismatch", "severity": "BLOCK"}])
    check("① 铜损不闭合 → 主错落在绕组槽", r["primary"] == "winding", r["primary"])

    # ② 指纹优先于判据码，且一条 issue 可落多处
    r2 = locate([{"code": "I1_kcl_violation", "severity": "BLOCK",
                  "detail": {"Tw_fingerprint": "kcl_missing_pcu_in_rsa",
                             "Tr_fingerprint": "topo_rotor_on_winding"}}])
    check("② 一条 I1 同时落「绕组槽 + 定子热路」与「转子支路」",
          set(r2["parts"]) == {"winding", "stator_thermal", "rotor"},
          sorted(r2["parts"]))

    # ③ 数据层不上图
    r3 = locate([{"code": "V1_unit", "severity": "BLOCK"},
                 {"code": "P1_source_hash", "severity": "BLOCK"}])
    check("③ 单位错 / 哈希不符 → 数据层，不可上图",
          r3["primary"] is None and r3["drawable"] is False and len(r3["data_layer"]) == 2,
          r3["note"])

    # ④ 红只给一处：两个部位都有错时，只有最重的一个是 primary
    r4 = locate([
        {"code": "C1_flux_mismatch", "severity": "BLOCK"},
        {"code": "I1_kcl_violation", "severity": "REVIEW"},
    ])
    check("④ 红只给一处，其余降为 secondary",
          r4["primary"] == "magnet" and "stator_thermal" in r4["secondary"],
          f"primary={r4['primary']} secondary={r4['secondary']}")

    # ⑤ 同强度时按条目数决出战者
    r5 = locate([
        {"code": "C1_kt_mismatch", "severity": "BLOCK"},
        {"code": "C2_copper_loss_mismatch", "severity": "BLOCK"},
        {"code": "C2_turn_scaling_linear_bug", "severity": "BLOCK"},
    ])
    check("⑤ 同强度按出错条目数选主错", r5["primary"] == "winding", r5["primary"])

    # ⑥ 未登记的判据码归数据层（宁可不上图，也不猜位置）
    r6 = locate([{"code": "ZZ_not_registered", "severity": "BLOCK"}])
    check("⑥ 未登记判据码 → 数据层，不猜几何位置",
          r6["primary"] is None and r6["drawable"] is False, r6["note"])

    # ⑦ 空输入不崩
    r7 = locate([])
    check("⑦ 空输入返回空结论", r7["primary"] is None and r7["note"] == "无问题可标注")

    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(0 if selftest() else 1)
    print(__doc__)
