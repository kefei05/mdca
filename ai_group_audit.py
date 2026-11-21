# -*- coding: utf-8 -*-
"""
任务 1.2 AI 组：把外部模型给出的 10 个改进方案入档、审计、并量化其温度预测误差。

关键纪律：
  · AI 自己算的 Tw/Tr 原样存档，不修正 —— 它就是被审计的对象
  · 按真实可信度填 conservatism（AI 低估温度 ⇒ 乐观方向 ⇒ 按 7.2 拒绝进入合格性判断）
  · 审计结论之外，另做一路「旁路计算」（直接调物理层、跳过校验），
    给出真正收敛的温度作为参照，用于量化误差，不作为审计结论
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mdca_grading as G  # noqa: E402
from mdca import audit, parse_case_doc  # noqa: E402
from mdca_thermal import Cas3, solve_three  # noqa: E402

OUT = HERE / "cases_ai"
OUT.mkdir(exist_ok=True)
ALPHA = 0.00393
TW_MAX, TR_MAX, TA = 155.0, 150.0, 40.0
DELTA_CAL = 33.0

# 原文照抄（含 AI 自算的 Tw/Tr，不修正）
AI_RAW = [
    (1, 1.16, 2.03, 1.2, 13.6, 5.33, 15.24, 102.66, 120.95, "转子导热灌封，Rrs 3.0→1.2"),
    (2, 1.16, 2.03, 3.0, 13.6, 5.33, 5.5, 81.58, 98.08, "磁钢轴向分块+斜极，Peddy 15.24→5.5"),
    (3, 1.16, 0.8, 3.0, 13.6, 5.33, 15.24, 75.68, 121.4, "散热筋+强风冷/水冷，Rsa 2.03→0.8"),
    (4, 1.16, 2.03, 1.6, 13.6, 5.33, 9.0, 89.15, 103.55, "Peddy→9.0 + Rrs→1.6"),
    (5, 1.16, 1.2, 1.5, 13.6, 5.33, 15.24, 84.46, 107.32, "Rsa→1.2 + Rrs→1.5"),
    (6, 0.7, 2.03, 1.5, 13.6, 5.33, 15.24, 94.05, 116.91, "真空灌封 Rws 1.16→0.7 + Rrs→1.5"),
    (7, 1.16, 2.03, 1.3, 9.5, 5.33, 15.24, 96.07, 115.88, "提槽满率 Pcu 13.6→9.5 + Rrs→1.3"),
    (8, 1.16, 1.4, 3.0, 13.6, 3.2, 8.0, 74.86, 98.86, "0.2mm 硅钢 Pfe→3.2 + Peddy→8.0 + Rsa→1.4"),
    (9, 0.85, 1.3, 1.6, 11.0, 4.2, 10.0, 69.63, 85.63, "全链路中等优化"),
    (10, 0.6, 0.7, 0.9, 9.0, 3.0, 5.5, 52.03, 56.98, "全参数极限优化"),
]


def bypass_solve(rws, rsa, rrs, pcu, pfe, peddy):
    """旁路：直接调物理层拿收敛温度（不做校验、不加 Δ_cal）。"""
    c = Cas3(name="bypass", Rws=rws, Rsa=rsa, P0=pcu, Pfe=pfe, Peddy=peddy,
             Rrs=rrs, Ta=TA, Tw_max=TW_MAX, Tr_max=TR_MAX,
             alpha=ALPHA, Tref=20.0)
    r = solve_three(c)
    return float(r["Tw"]), float(r["Tr"])


def build(i, rws, rsa, rrs, pcu, pfe, peddy, tw_ai, tr_ai, how):
    """按真实可信度入档：AI 低估温度 ⇒ 乐观方向。

    注意：temp_basis 只给 Pcu（唯一有参考温度语义的量）。
    给所有参数都填 temp_basis 会造成 V3_palette_mix 误报——那是建模错误，不是 AI 的问题。
    """
    def prm(v, unit, note, temp_basis=None):
        pal = {"method": "design_target", "load_case": "continuous"}
        if temp_basis:
            pal["temp_basis"] = temp_basis
        return {
            "value": v, "unit": unit,
            "palette": pal,
            "verified": False,
            "conservatism": "optimistic",
            "source": f"AI 生成，无独立验证：{note}",
        }

    return {
        "schema": "1.0",
        "case_id": f"AI{i:02d}",
        "description": f"【AI 组方案 {i}】{how}",
        "source_type": "ai",
        "generator_id": "external LLM (user-provided output)",
        "generator_version": "unknown",
        "params": {
            "Rws": prm(rws, "K/W", "热阻"),
            "Rsa": prm(rsa, "K/W", "热阻"),
            "Rrs": prm(rrs, "K/W", "热阻"),
            "Pcu": prm(pcu, "W", "铜损", temp_basis="20C"),
            "Pfe": prm(pfe, "W", "铁损"),
            "Peddy": prm(peddy, "W", "磁钢涡流"),
            "Ta": {"value": TA, "unit": "C", "palette": {"method": "design_target"},
                   "verified": False, "conservatism": "optimistic", "source": "AI 生成，无独立验证"},
            "alpha": {"value": ALPHA, "unit": "1/K", "palette": {"method": "literature"}, "verified": True},
            "Tref": {"value": 20.0, "unit": "C", "palette": {"method": "convention"}, "verified": True},
        },
        "limits": {"Tw_max": TW_MAX, "Tr_max": TR_MAX, "temperature_type": "mean"},
        "calibration": {
            "delta_cal_K": DELTA_CAL,
            "source_type": "external_model_mismatch",
            "fitted_on_same_points": False,
            "model_similarity_assumed": True,
            "gamma": 2.0,
        },
        "domain": {"Ta": [10.0, 60.0]},
        "_ai_reported": {"Tw": tw_ai, "Tr": tr_ai, "how": how},
    }


cfg = G.load_config()
rows = []
for rec in AI_RAW:
    i, rws, rsa, rrs, pcu, pfe, peddy, tw_ai, tr_ai, how = rec
    raw = build(*rec)
    (OUT / f"AI{i:02d}.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    o = audit(parse_case_doc(raw, f"AI{i:02d}"))
    g = G.grade(o, cfg)
    tw_real, tr_real = bypass_solve(rws, rsa, rrs, pcu, pfe, peddy)

    rows.append({
        "id": i, "how": how,
        "ai_tw": tw_ai, "ai_tr": tr_ai,
        "real_tw": tw_real, "real_tr": tr_real,
        "err_tw": tw_ai - tw_real, "err_tr": tr_ai - tr_real,
        "status": o["audit"]["overall_status"],
        "tier": g["tier"], "label": g["tier_label"], "rule": g["rule"],
        "blocks": [x["code"] for x in (o.get("validation") or {}).get("issues", [])
                   if x.get("severity") == "BLOCK"],
    })

print("=" * 108)
print("AI 组：10 个改进方案（原文照抄，未修正其温度）")
print("=" * 108)
print(f"{'id':>3}{'AI的Tw':>8}{'真实Tw':>8}{'误差':>8}{'AI的Tr':>8}{'真实Tr':>8}{'误差':>8}"
      f"{'状态':>15}{'档位':>10}")
print("-" * 108)
for r in rows:
    print(f"{r['id']:>3}{r['ai_tw']:>8.2f}{r['real_tw']:>8.2f}{r['err_tw']:>+8.2f}"
          f"{r['ai_tr']:>8.2f}{r['real_tr']:>8.2f}{r['err_tr']:>+8.2f}"
          f"{r['status']:>15}{r['label']:>10}")

print()
print("=" * 108)
print("统计")
print("=" * 108)
errs = [r["err_tw"] for r in rows]
under = [e for e in errs if e < 0]
print(f"  AI 的 Tw 误差范围：{min(errs):+.2f} ~ {max(errs):+.2f} K，中位 {sorted(errs)[len(errs)//2]:+.2f} K")
print(f"  低估（AI < 真实）的：{len(under)}/{len(rows)} 个")
from collections import Counter
print(f"  审计状态分布：{dict(Counter(r['status'] for r in rows))}")
print(f"  档位分布：{dict(Counter(r['label'] for r in rows))}")
blk = Counter(c for r in rows for c in r["blocks"])
print(f"  拦截原因：{dict(blk)}")

print()
print("=" * 108)
print("参照：若忽略「数据不可信」、直接用真实温度判断可行性的结果")
print("=" * 108)
feasible = [r for r in rows if r["real_tw"] + DELTA_CAL <= TW_MAX and r["real_tr"] <= TR_MAX]
print(f"  加 Δ_cal={DELTA_CAL:.0f}K 后仍合格：{len(feasible)}/{len(rows)} 个")
for r in rows:
    m = TW_MAX - (r["real_tw"] + DELTA_CAL)
    print(f"    AI{r['id']:>2}  真实Tw {r['real_tw']:>7.2f}  加裕度 {r['real_tw']+DELTA_CAL:>7.2f}"
          f"  裕度 {m:>+7.2f}  真实Tr {r['real_tr']:>7.2f}  {'合格' if m >= 0 and r['real_tr'] <= TR_MAX else '不合格'}")

Path(HERE / "out" / "ai_group.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n明细 -> out/ai_group.json")
print(f"候选 -> {OUT}")
