# -*- coding: utf-8 -*-
"""任务 2 第三步：把 MDCA 判定与 Motor-CAD 实算并列，统计漏报率与数值误差。"""
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

mc = {int(r["Ta"]): r for r in csv.DictReader(open(OUT / "task2_motorcad.csv", encoding="utf-8"))}
cands = {r["ta"]: r for r in json.loads((OUT / "task2_candidates.json").read_text(encoding="utf-8"))}

# 扩展组（Ta 20/25/30/35）的 MDCA 判定：用同一构造重算
import sys
sys.path.insert(0, str(HERE))
import mdca_grading as G
from mdca import audit, parse_case_doc
from task2_candidates import build

cfg = G.load_config()
for ta in (20, 25, 30, 35):
    raw = build(99, ta)
    raw["case_id"] = f"T2X-Ta{ta}"
    raw["domain"] = {"Ta": [15.0, 110.0]}
    o = audit(parse_case_doc(raw, raw["case_id"]))
    g = G.grade(o, cfg)
    t = o.get("temperature") or {}
    cands[ta] = {
        "ta": ta, "tw_point": t.get("winding_point_C"),
        "tw_corr": t.get("winding_corrected_C"),
        "status": o["audit"]["overall_status"], "tier": g["tier"], "label": g["tier_label"],
    }

print("=" * 110)
print("MDCA vs Motor-CAD（温度定义统一为 hotspot；Motor-CAD 取全场最高节点）")
print("=" * 110)
print(f"{'Ta':>5}{'MDCA点值':>10}{'MDCA+Δcal':>11}{'MDCA档位':>10}"
      f"{'MC最高温':>10}{'数值差':>9}{'MC<155':>8}{'MC<180':>8}")
print("-" * 110)
rows = []
for ta in sorted(cands):
    c, m = cands[ta], mc.get(ta)
    if not m:
        continue
    tw = c["tw_point"]; tc = c["tw_corr"]
    tmax = float(m["Tmax"])
    rows.append({
        "ta": ta, "mdca_point": tw, "mdca_corr": tc, "tier": c["tier"], "label": c["label"],
        "status": c["status"], "mc_tmax": tmax, "err_point": (tmax - tw) if tw else None,
        "err_corr": (tmax - tc) if tc else None,
        "mc_ok_155": tmax <= 155.0, "mc_ok_180": tmax <= 180.0,
    })
    print(f"{ta:>5}{tw:>10.1f}{tc:>11.1f}{c['label']:>10}{tmax:>10.1f}"
          f"{tmax-tw:>+9.1f}{('通过' if tmax<=155 else '超限'):>8}"
          f"{('通过' if tmax<=180 else '超限'):>8}")

print()
print("=" * 110)
print("判定比对（分档 × 分限值）")
print("=" * 110)
for limit, key in ((155.0, "mc_ok_155"), (180.0, "mc_ok_180")):
    print(f"\n【假设 Tw_max = {limit:.0f}℃】")
    for tier, name in (("external", "送外部"), ("manual", "人工复核")):
        sub = [r for r in rows if r["tier"] == tier]
        if not sub:
            print(f"  {name}：0 个（无可比对样本）")
            continue
        bad = [r for r in sub if not r[key]]
        print(f"  {name}：{len(sub)} 个，其中 Motor-CAD 判不通过 {len(bad)} 个"
              f" → {'漏报率' if tier=='external' else '外部一致性问题'} "
              f"{len(bad)}/{len(sub)} = {100*len(bad)/len(sub):.0f}%")
        for r in bad:
            print(f"       Ta={r['ta']}  MDCA 点值 {r['mdca_point']:.1f}（判 {r['label']}）"
                  f"  MC 实测 {r['mc_tmax']:.1f}")

print()
print("=" * 110)
print("数值误差（不看判定，只看温度差）")
print("=" * 110)
errs = [r["err_point"] for r in rows if r["err_point"] is not None]
print(f"  MDCA 点值 vs MC 实测：{min(errs):+.1f} ~ {max(errs):+.1f} K，中位 "
      f"{sorted(errs)[len(errs)//2]:+.1f} K（正 = MDCA 低估）")
errs2 = [r["err_corr"] for r in rows if r["err_corr"] is not None]
print(f"  MDCA 加 Δ_cal vs MC 实测：{min(errs2):+.1f} ~ {max(errs2):+.1f} K，中位 "
      f"{sorted(errs2)[len(errs2)//2]:+.1f} K")
print()
print("  注：MC 最高温随 Ta 非单调（Ta=35 最高 174.7℃，Ta=55 只有 159.0℃），")
print("      因为 Rsa 随冷却液温度剧烈变化（阶段 E 已实测降 63%）。")
print("      MDCA 用固定 Rsa，无法表达这个非单调性。")

(OUT / "task2_compare.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
print(f"\n明细 -> {OUT / 'task2_compare.json'}")
