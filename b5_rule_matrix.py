# -*- coding: utf-8 -*-
"""
B5：规则适用性矩阵
==================
把同一套规则跑在三个 benchmark 上，统计每条规则在每台上的触发情况，
并判断哪些规则依赖特定电机结构（不是普适的）。

产物：out/b5_rule_matrix.txt（同屏输出）
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from mdca import audit, load_case  # noqa: E402

BMS = sorted((HERE / "benchmarks").glob("*.json"))

rows = []
all_codes = set()
for f in BMS:
    case = load_case(f)
    # v0.2 的实验结论依赖温度；第 2 步把 B 路改成默认关后，脚本必须自己声明。
    o = audit(case, plugins=("thermal", "scaling"))
    codes = set()
    for i in (o.get("validation") or {}).get("issues", []):
        if i.get("severity") in ("BLOCK", "REVIEW"):
            codes.add(i["code"])
    for r in (o["audit"].get("reasons") or []):
        codes.add(r if ":" not in r else r.split(":")[0].strip())
    all_codes |= codes
    rows.append({
        "id": case.case_id,
        "benchmark": case.benchmark,
        "status": o["audit"]["overall_status"],
        "codes": codes,
        "model_nodes": (o.get("model") or {}).get("nodes"),
        "Tw": (o.get("temperature") or {}).get("winding_corrected_C"),
        "Tr": (o.get("temperature") or {}).get("rotor_point_C"),
    })

# 规则清单（含 benchmark 里没触发的，用于说明"哪条规则在这三台上空转"）
ALL_RULES = [
    "P0_contract", "P1_source_hash", "V1_missing", "V1_unit",
    "V2_unverified", "V2_optimistic", "V2_conservative", "V2_benchmark_exempt",
    "V3_palette_mix", "V5_interval", "V6_positive",
    "D1_out_of_domain", "S0_scaling_input", "S1_scaling_applied", "S2_extrapolation",
    "calibration_missing", "linear_model_out_of_validity",
    "temperature_exceeds_limit", "temperature_bound_crosses_limit",
    "winding_over_insulation_limit", "rotor_over_magnet_limit",
    "decision_flipped_under_stress", "thermal_feedback_divergence",
]

print("=" * 88)
print("B5 规则适用性矩阵")
print("=" * 88)
print(f"{'benchmark':<32}{'状态':>16}{'节点数':>7}{'Tw':>10}{'Tr':>10}")
print("-" * 88)
for r in rows:
    tw = f"{r['Tw']:.1f}" if isinstance(r["Tw"], (int, float)) else "-"
    tr = f"{r['Tr']:.1f}" if isinstance(r["Tr"], (int, float)) else "-"
    print(f"{r['id'][:30]:<32}{r['status']:>16}{str(r['model_nodes']):>7}{tw:>10}{tr:>10}")

print()
print("=" * 88)
print("规则触发矩阵（✓=触发  ·=未触发）")
print("=" * 88)
hdr = f"{'规则':<32}" + "".join(f"{r['benchmark']:>8}" for r in rows) + "   判定"
print(hdr)
print("-" * len(hdr))
nontrivial = []
for rule in ALL_RULES:
    marks = "".join(f"{'  ✓' if rule in r['codes'] else '  ·':>8}" for r in rows)
    hit = sum(1 for r in rows if rule in r["codes"])
    if hit == 0:
        verdict = "三台均空转"
    elif hit == len(rows):
        verdict = "三台通用"
    else:
        verdict = f"仅 {hit}/{len(rows)} 台"
        nontrivial.append((rule, [r["benchmark"] for r in rows if rule in r["codes"]]))
    print(f"{rule:<32}{marks}   {verdict}")

print()
print("=" * 88)
print("只在部分 benchmark 上触发的规则（需要标注适用前提）")
print("=" * 88)
for rule, where in nontrivial:
    print(f"  {rule:<32} 仅 {', '.join(where)}")

print()
print("三台均空转的规则：" + ", ".join(
    [r for r in ALL_RULES if not any(r in x["codes"] for x in rows)]) or "（无）")

# 结构差异说明
print()
print("=" * 88)
print("三台的参数结构差异（解释规则为何不通用）")
print("=" * 88)
for f in BMS:
    case = load_case(f)
    has_peddy = "Peddy" in case.params and case.val("Peddy") > 0
    has_rrs = "Rrs" in case.params and case.val("Rrs") > 0
    nodes = 3 if has_peddy else 2
    print(f"  {case.benchmark:<6} Peddy={'有' if has_peddy else '无':<2} "
          f"Rrs={'有' if has_rrs else '无':<2} → {nodes} 节点  "
          f"Tr={'适用' if has_peddy else '不适用（无转子热源）'}")

out = HERE / "out" / "b5_rule_matrix.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({
    "benchmarks": [{**r, "codes": sorted(r["codes"])} for r in rows],
    "all_rules": ALL_RULES,
    "partial": [{"rule": k, "benchmarks": v} for k, v in nontrivial],
    "empty": [r for r in ALL_RULES if not any(r in x["codes"] for x in rows)],
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n明细 → {out}")
