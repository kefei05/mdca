# -*- coding: utf-8 -*-
"""
任务 2 第一步：在 BM3（Motor-CAD EV_5）参数空间内生成候选，审计并分级。

按 FREEZE 4.3 预注册：
  扰动 Pcu 与 Ta，共约 30 个候选。
  实际执行见 §偏离说明：Motor-CAD 无法分离铜损与铁损（阶段 E 已确认），
  故**损耗保持 BM3 原值不动，只扰动边界温度 Ta**，以保证两边喂的是同一份损耗。
  这样既满足「同一份损耗喂两边」，又不虚构 Motor-CAD 做不到的操作。

温度定义统一为 hotspot（Motor-CAD 取全场最高节点）。
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mdca_grading as G  # noqa: E402
from mdca import audit, parse_case_doc  # noqa: E402

OUT = HERE / "cases_task2"
OUT.mkdir(exist_ok=True)
RES = HERE / "out" / "task2_candidates.json"

# BM3 的热阻与损耗（由 Motor-CAD 65℃ 工况反推，fitted）
RWS, RSA, RRS = 0.110515, 0.038786, 0.664453
P0, PFE, PEDDY = 372.913, 32.197, 101.152      # P0 已折算到 20℃
TW_MAX, TR_MAX = 180.0, 180.0
DELTA_CAL = 33.0

TA_LIST = [45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 105, 110]


def build(i, ta):
    def prm(v, unit, note, tb=None):
        pal = {"method": "analytic", "load_case": "rated"}
        if tb:
            pal["temp_basis"] = tb
        return {"value": v, "unit": unit, "palette": pal,
                "verified": False, "conservatism": "conservative",
                "source": f"BM3 反推值（fitted）：{note}"}

    return {
        "schema": "1.0",
        "case_id": f"T2-{i:02d}-Ta{ta}",
        "description": f"任务2 候选：BM3 参数空间，冷却液 {ta}℃",
        "source_type": "external",
        "params": {
            "Rws": prm(RWS, "K/W", "绕组→定子"),
            "Rsa": prm(RSA, "K/W", "定子→环境"),
            "Rrs": prm(RRS, "K/W", "转子→定子"),
            "Pcu": prm(P0, "W", "20℃ 基准铜损", tb="20C"),
            "Pfe": prm(PFE, "W", "铁损+杂散"),
            "Peddy": prm(PEDDY, "W", "转子侧损耗"),
            "Ta": {"value": float(ta), "unit": "C",
                   "palette": {"method": "measured"},
                   "verified": True, "source": "设计变量（本次扫描量）"},
            "alpha": {"value": 0.00393, "unit": "1/K", "palette": {"method": "literature"}, "verified": True},
            "Tref": {"value": 20.0, "unit": "C", "palette": {"method": "convention"}, "verified": True},
        },
        "limits": {"Tw_max": TW_MAX, "Tr_max": TR_MAX, "temperature_type": "hotspot"},
        "calibration": {
            "delta_cal_K": DELTA_CAL, "source_type": "simulation_hifi",
            "fitted_on_same_points": False, "model_similarity_assumed": False, "gamma": 2.0,
        },
        "domain": {"Ta": [45.0, 110.0]},
    }


cfg = G.load_config()
rows = []
for i, ta in enumerate(TA_LIST, 1):
    raw = build(i, ta)
    (OUT / f"{raw['case_id']}.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    o = audit(parse_case_doc(raw, raw["case_id"]))
    g = G.grade(o, cfg)
    t = o.get("temperature") or {}
    rows.append({
        "case_id": raw["case_id"], "ta": ta,
        "tw_point": t.get("winding_point_C"), "tw_corr": t.get("winding_corrected_C"),
        "tr_point": t.get("rotor_point_C"),
        "margin": (TW_MAX - t["winding_corrected_C"]) if t.get("winding_corrected_C") is not None else None,
        "status": o["audit"]["overall_status"], "tier": g["tier"], "label": g["tier_label"],
        "rule": g["rule"], "reasons": o["audit"].get("reasons") or [],
    })

print("=" * 104)
print("任务 2：BM3 参数空间候选（Tw_max=180 hotspot，Δ_cal=33）")
print("=" * 104)
print(f"{'Ta':>6}{'点值Tw':>10}{'加裕度':>10}{'裕度':>9}{'状态':>16}{'档位':>10}  原因")
print("-" * 104)
for r in rows:
    tw = f"{r['tw_point']:.1f}" if r["tw_point"] is not None else "-"
    tc = f"{r['tw_corr']:.1f}" if r["tw_corr"] is not None else "-"
    mg = f"{r['margin']:+.1f}" if r["margin"] is not None else "-"
    print(f"{r['ta']:>6}{tw:>10}{tc:>10}{mg:>9}{r['status']:>16}{r['label']:>10}  "
          + " ".join(r["reasons"][:2]))

from collections import Counter
print()
print(f"档位分布：{dict(Counter(r['label'] for r in rows))}")
print(f"状态分布：{dict(Counter(r['status'] for r in rows))}")
RES.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n明细 -> {RES}")
print(f"候选 -> {OUT}")
