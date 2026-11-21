# -*- coding: utf-8 -*-
"""
任务 1.4：「继续优化」档实测为 0，判断是规则问题还是候选集没覆盖。

初版假设「裕度小 → P0+20% 翻转」，实测发现翻转来自 C-gamma×2（校准裕度翻倍）：
  Δ_cal=33 K 时，裕度不足 10 K 的候选在 gamma×2 下温度必然再涨 33 K ⇒ 必翻转
  ⇒ 该档在 Δ_cal=33 下几乎不可能出现，不是 bug，是 Δ_cal 太大

因此做两组对照：
  A 组 Δ_cal=33（当前冻结值）
  B 组 Δ_cal=5 （E 阶段预期的量级）
若 B 组出现「继续优化」，则证明该档逻辑可用，只是被大 Δ_cal 挤掉了。

本组数据**不计入盲测主结果**，仅用于验证该档逻辑可用。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mdca_grading as G  # noqa: E402
from mdca import audit, parse_case_doc  # noqa: E402
from mdca_batch import build_case  # noqa: E402

cfg = G.load_config()
BASE = {"Rws": 1.16, "Pcu": 13.6, "Pfe": 5.33, "Peddy": 15.24, "Rrs": 3.0, "Ta": 40.0}


def run(rsa, delta_cal):
    p = dict(BASE, Rsa=rsa)
    raw = build_case(f"R{int(rsa*100):04d}_d{int(delta_cal)}", p, source_type="optimizer",
                     delta_cal=delta_cal)
    o = audit(parse_case_doc(raw, "t14"))
    g = G.grade(o, cfg)
    tw = (o.get("temperature") or {}).get("winding_corrected_C")
    st = o.get("stress_tests") or {}
    return {
        "tw": tw, "margin": 155.0 - tw if isinstance(tw, (int, float)) else float("nan"),
        "status": o["audit"]["overall_status"], "tier": g["tier"], "label": g["tier_label"],
        "rule": g["rule"], "flipped": st.get("decision_flipped"),
        "flipped_by": ",".join((st.get("flipped_by") or {}).keys()) or "-",
    }


for tag, dc in (("A 组 Δ_cal=33 K（冻结值）", 33.0), ("B 组 Δ_cal=5 K（E 阶段预期）", 5.0)):
    print("=" * 100)
    print(tag)
    print("=" * 100)
    print(f"{'Rsa':>6}{'判定温度':>10}{'裕度':>8}{'状态':>16}{'档位':>10}{'翻转':>6}{'翻自':>16}  规则")
    print("-" * 100)
    n_refine = 0
    for i in range(10, 41):
        r = run(i / 10.0, dc)
        n_refine += (r["tier"] == "refine")
        print(f"{i/10:>6.1f}{r['tw']:>10.2f}{r['margin']:>8.2f}{r['status']:>16}"
              f"{r['label']:>10}{str(r['flipped']):>6}{r['flipped_by']:>16}  {r['rule']}")
    print(f"\n  → 「继续优化」档命中 {n_refine} 个\n")
