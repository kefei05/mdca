#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 批量候选与植入问题实验（纠偏基线 6.4 节 D）
=================================================

回答初心的问题：**这套东西能不能减少人工检查量，而不把问题漏到下一轮。**

两阶段严格分离（需求 D2「审计过程不得读取标签」）：

    阶段 1  只读 cases_batch/*.json → 审计 → 分级 → 得到每条候选的档位
    阶段 2  才读 labels.json → 比对 → 算拦截率、漏报、误报

代码上 evaluate() 的第 1 段完全不接触 labels，第 2 段才开始，顺序不可交换。

命令：
    python mdca_batch.py generate 120            # 生成候选 + 隐藏标签
    python mdca_batch.py evaluate                # 跑审计 + 分级 + 度量
"""

from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CASES_DIR = HERE / "cases_batch"
LABELS = HERE / "labels" / "labels.json"

# 参数空间（宽范围，目的是覆盖合格 / 贴边 / 超温三种结果）
WIDE: Dict[str, Tuple[float, float]] = {
    "Rws": (0.40, 3.00),
    "Rsa": (1.00, 7.00),
    "Pcu": (8.0, 24.0),
    "Pfe": (3.0, 12.0),
    "Peddy": (0.0, 20.0),
    "Rrs": (2.0, 4.0),
    "Ta": (20.0, 50.0),
}

TW_MAX = 155.0
TR_MAX = 150.0
DELTA_CAL = 33.0

# 植入类型 → 可接受的档位集合（不含 external：任何植入都不该被送外部）
PLANT_ACCEPT: Dict[str, Tuple[str, ...]] = {
    "no_source": ("manual",),
    "unit_error": ("eliminate",),
    "palette_conflict": ("eliminate",),
    "unstable": ("eliminate",),
    "over_temperature": ("eliminate",),
    "near_limit": ("refine", "manual"),
    "out_of_domain": ("manual",),
    "needs_zero_dcal": ("eliminate", "manual"),
}


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------


def _tw_estimate(p: Dict[str, float]) -> float:
    """冷态估算，用于反解 Rsa 把温度打到目标值。"""
    return p["Ta"] + p["Rsa"] * (p["Pcu"] + 5.33 + p["Peddy"]) + p["Rws"] * p["Pcu"]


def _solve_rsa_for(p: Dict[str, float], target: float) -> Optional[float]:
    """给定其余参数，反解使 Tw ≈ target 的 Rsa。"""
    ptot = p["Pcu"] + 5.33 + p["Peddy"]
    if ptot <= 0:
        return None
    rsa = (target - p["Ta"] - p["Rws"] * p["Pcu"]) / ptot
    return rsa if rsa > 0 else None


def _sample_wide(rng: random.Random) -> Dict[str, float]:
    p = {k: rng.uniform(lo, hi) for k, (lo, hi) in WIDE.items()}
    p["Pfe"] = round(p["Pfe"], 2)
    return p


def _sample_near(rng: random.Random, target_hot: float) -> Dict[str, float]:
    """反解 Rsa，使**热态**温度打到 target_hot（冷态公式会系统偏低）。"""
    p = _sample_wide(rng)
    for _ in range(80):
        p = _sample_wide(rng)
        ptot = p["Pcu"] + 5.33 + p["Peddy"]
        if ptot <= 0:
            continue
        rsa = (target_hot - p["Ta"] - p["Rws"] * p["Pcu"]) / ptot
        if not (0.5 <= rsa <= 12.0):
            continue
        p["Rsa"] = rsa
        for _ in range(8):
            tw = _tw_hot(p)
            if abs(tw - target_hot) < 1.5:
                break
            p["Rsa"] = max(0.3, p["Rsa"] - (tw - target_hot) / ptot)
        if abs(_tw_hot(p) - target_hot) < 3.0:
            return p
    return p


def _tw_hot(p: Dict[str, float], iters: int = 8) -> float:
    """迭代估算热态绕组温度，用于判断候选是否真的「干净」。"""
    tw = 20.0
    for _ in range(iters):
        pcu = p["Pcu"] * (1 + 0.00393 * (tw - 20.0))
        tw = p["Ta"] + p["Rsa"] * (pcu + 5.33 + p["Peddy"]) + p["Rws"] * pcu
    return tw


def _is_clean(p: Dict[str, float], target_max: float = 130.0) -> bool:
    """干净候选的判据：热态温度留出裕度，且不在失稳区。"""
    if 0.00393 * (p["Rws"] + p["Rsa"]) * p["Pcu"] >= 1.0:
        return False
    return _tw_hot(p) <= target_max


def build_case(cid: str, p: Dict[str, float], *, source_type: str,
               palette_override: Optional[Dict[str, str]] = None,
               palette_target: Optional[str] = None,
               unit_override: Optional[Dict[str, str]] = None,
               drop_conservatism: bool = False,
               domain: Optional[Dict[str, List[float]]] = None,
               delta_cal: float = DELTA_CAL) -> Dict[str, Any]:
    """把参数拼成一份 case JSON。"""

    def pal(key: str, **kw) -> Dict[str, str]:
        base = {"turns": "6", "method": "analytic", "load_case": "continuous"}
        base.update(kw)
        # 口径覆盖只作用于指定参数，否则全表统一改不会产生冲突
        if palette_override and palette_target == key:
            base.update(palette_override)
        return base

    units = {"Rws": "K/W", "Rsa": "K/W", "Pcu": "W", "Pfe": "W", "Ta": "C",
             "Peddy": "W", "Rrs": "K/W"}
    if unit_override:
        units.update(unit_override)

    params: Dict[str, Any] = {}
    for key in ("Rws", "Rsa", "Pcu", "Pfe", "Ta", "Peddy", "Rrs"):
        entry = {
            "value": round(p[key], 4),
            "unit": units[key],
            "palette": pal(key, end_winding="included") if key == "Pcu" else pal(key),
            "verified": True,
            "source": "batch generator",
        }
        if drop_conservatism and key == "Peddy":
            entry["verified"] = False
            entry["source"] = ""          # 无来源
        params[key] = entry

    params["alpha"] = {"value": 0.00393, "unit": "1/K",
                       "palette": {"method": "literature"}, "verified": True}
    params["Tref"] = {"value": 20.0, "unit": "C",
                      "palette": {"method": "convention"}, "verified": True}

    case: Dict[str, Any] = {
        "schema": "1.0",
        "case_id": cid,
        "description": f"batch candidate ({source_type})",
        "source_type": source_type,
        "generator_id": "mdca_batch.random_search",
        "generator_version": "1.0",
        "params": params,
        "limits": {"Tw_max": TW_MAX, "Tr_max": TR_MAX, "temperature_type": "mean"},
        "calibration": {
            "delta_cal_K": delta_cal,
            "source_type": "external_model_mismatch",
            "fitted_on_same_points": False,
            "model_similarity_assumed": True,
            "gamma": 2.0,
        },
        "domain": domain or {"Ta": [10.0, 60.0], "Pcu": [5.0, 30.0]},
        "_planted": [],      # 审计会忽略未识别字段，但这里留空以免泄露
    }
    return case


# --------------------------------------------------------------------------
# 生成指令
# --------------------------------------------------------------------------


def _sample_clean(rng: random.Random, target_max: float) -> Dict[str, float]:
    """拒绝采样出真正干净的候选。

    注意：判定用的是「点值 + Δ_cal」，所以干净候选的点值必须远低于 Tw_max。
    Δ_cal=33K 时，要留出 25K 充足裕度，点值须 ≤ 155-33-25 = 97℃。
    """
    p = _sample_wide(rng)
    for _ in range(300):
        p = _sample_wide(rng)
        if 0.00393 * (p["Rws"] + p["Rsa"]) * p["Pcu"] >= 1.0:
            continue
        p["Pcu"] = min(p["Pcu"], 14.0)
        p["Peddy"] = min(p["Peddy"], 8.0)
        p["Rsa"] = min(p["Rsa"], 2.4)
        if _tw_hot(p) <= target_max:
            return p
    return p


def _sample_low_temp(rng: random.Random, ta: float, target: float) -> Dict[str, float]:
    """固定 Ta，压低其余参数使热态温度 ≤ target（用于只植入单一问题的场景）。"""
    p = _sample_wide(rng)
    for _ in range(400):
        p = _sample_wide(rng)
        p["Ta"] = ta
        p["Pcu"] = min(p["Pcu"], 9.0)
        p["Peddy"] = min(p["Peddy"], 2.0)
        p["Rsa"] = min(p["Rsa"], 1.1)
        p["Rws"] = min(p["Rws"], 1.2)
        if 0.00393 * (p["Rws"] + p["Rsa"]) * p["Pcu"] >= 1.0:
            continue
        if _tw_hot(p) <= target:
            return p
    return p


def cmd_generate(n: int = 120, seed: int = 20261121, preset: str = "") -> int:
    """生成候选与隐藏标签。

    preset="blind" 时按 FREEZE.md 4.1 预注册的精确构成：
        ample 62 / tight 18 / 植入 70 = 150
    其他情况按 n 的比例分配（默认 40% / 12% / 48%）。
    """
    if CASES_DIR.exists():
        shutil.rmtree(CASES_DIR)
    CASES_DIR.mkdir(parents=True)
    LABELS.parent.mkdir(parents=True, exist_ok=True)

    if preset == "blind":
        n_clean, n_tight, n_plant = 62, 18, 70
    else:
        n_clean = int(n * 0.40)
        n_tight = int(n * 0.12)
        n_plant = n - n_clean - n_tight   # 修正：此前漏减 n_tight，总数会超出 n

    rng = random.Random(seed)
    labels: Dict[str, Any] = {}
    idx = 0

    def emit(case: Dict[str, Any], planted: List[str], source: str) -> None:
        nonlocal idx
        cid = case["case_id"]
        (CASES_DIR / f"{cid}.json").write_text(
            json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
        labels[cid] = {
            "planted": planted,
            "source_type": source,
            "acceptable_tiers": (list(PLANT_ACCEPT[planted[0]]) if planted else
                                 ["external", "refine"]),
        }

    # 干净候选：点值 + Δ_cal 才是判定值，所以点值必须远低于限值
    for i in range(n_clean):
        idx += 1
        p = _sample_clean(rng, 80.0)          # 80+33=113℃，再经 P0+20% 压力测试仍应合格
        emit(build_case(f"C{idx:03d}", p, source_type="optimizer"), [], "optimizer")

    # 裕度小的候选：点值 112~122 → +33 后 145~155，应进「继续优化」
    for i in range(n_tight):
        idx += 1
        p = _sample_clean(rng, 122.0)
        c = build_case(f"C{idx:03d}", p, source_type="optimizer")
        emit(c, [], "optimizer")
        labels[c["case_id"]]["acceptable_tiers"] = ["refine", "manual"]
        labels[c["case_id"]]["kind"] = "tight_margin"

    # 植入候选
    planters: List[str] = list(PLANT_ACCEPT.keys())
    for i in range(n_plant):
        idx += 1
        kind = planters[i % len(planters)]
        p = _sample_wide(rng)
        kw: Dict[str, Any] = {"source_type": "ai"}
        planted = [kind]

        if kind == "no_source":
            p = _sample_near(rng, rng.uniform(110.0, 140.0))
            kw["drop_conservatism"] = True
        elif kind == "unit_error":
            kw["unit_override"] = {"Pfe": "J"}
        elif kind == "palette_conflict":
            kw["palette_override"] = {"turns": "8"}
            kw["palette_target"] = "Pcu"
        elif kind == "unstable":
            p["Rws"] = 3.0
            p["Rsa"] = 7.0
            p["Pcu"] = 24.0     # g = 0.00393*10*24 ≈ 0.94 → 再高一点跨 1
            p["Pcu"] = 28.0
        elif kind == "over_temperature":
            p["Rsa"] = 6.5
            p["Pcu"] = 22.0
            p["Peddy"] = 18.0
            p["Ta"] = 48.0
        elif kind == "near_limit":
            # 判定值是「点值 + Δ_cal」，所以要贴近 155 须让点值落在 117~127
            p = _sample_near(rng, rng.uniform(116.0, 121.0))
        elif kind == "out_of_domain":
            # 只植入「超域」一个变量：Ta 超界，但温度压低到不触发超温
            p = _sample_low_temp(rng, 70.0, 92.0)
        elif kind == "needs_zero_dcal":
            # 点值 < 155 但 +33K 超限
            p = _sample_near(rng, rng.uniform(128.0, 148.0))

        emit(build_case(f"C{idx:03d}", p, **kw), planted, "ai")

    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"生成候选 {len(labels)} 条 → {CASES_DIR}")
    print(f"隐藏标签 → {LABELS}")
    print()
    print("类型分布：")
    from collections import Counter
    c = Counter(v["planted"][0] if v["planted"] else "clean" for v in labels.values())
    for k, v in c.items():
        print(f"  {k:<20}{v}")
    return 0


# --------------------------------------------------------------------------
# 评估
# --------------------------------------------------------------------------


def cmd_evaluate(max_show: int = 6) -> int:
    import mdca_grading as G
    from mdca import audit, load_case

    files = sorted(CASES_DIR.glob("*.json"))
    if not files:
        print(f"没有候选：{CASES_DIR}\n先跑 generate")
        return 2

    cfg = G.load_config()

    # ---------- 阶段 1：完全不接触 labels ----------
    results: Dict[str, Dict[str, Any]] = {}
    for f in files:
        case = load_case(f)
        rep = audit(case)                       # 输入只有 case，无标签信息
        g = G.grade(rep, cfg)
        results[case.case_id] = {
            "status": (rep.get("audit") or {}).get("overall_status"),
            "tier": g["tier"],
            "tier_label": g["tier_label"],
            "rule": g["rule"],
            "reasons": (rep.get("audit") or {}).get("reasons") or [],
            "margin_K": g["features"].get("margin_K"),
        }

    # ---------- 阶段 2：才读标签 ----------
    if not LABELS.exists():
        print(f"找不到标签：{LABELS}")
        return 2
    labels = json.loads(LABELS.read_text(encoding="utf-8"))

    total = len(results)
    tiers = {"eliminate": 0, "refine": 0, "manual": 0, "external": 0}
    for r in results.values():
        tiers[r["tier"]] += 1

    # 人工检查量：分级前全看，分级后看 manual + refine
    manual_before = total
    manual_after = tiers["manual"] + tiers["refine"]

    # 分类型统计
    by_kind: Dict[str, Dict[str, Any]] = {}
    leaks: List[Tuple[str, str, str]] = []
    for cid, lab in labels.items():
        if cid not in results:
            continue
        planted = lab["planted"]
        kind = planted[0] if planted else "clean"
        d = by_kind.setdefault(kind, {"n": 0, "hits": 0, "tiers": {}})
        d["n"] += 1
        tier = results[cid]["tier"]
        d["tiers"][tier] = d["tiers"].get(tier, 0) + 1
        acceptable = lab.get("acceptable_tiers") or []
        if tier in acceptable:
            d["hits"] += 1
        # 漏报：任何植入问题进了「送外部」
        if planted and tier == "external":
            leaks.append((cid, kind, results[cid]["status"]))

    clean = by_kind.pop("clean", {"n": 0, "hits": 0, "tiers": {}})

    # 误报：干净候选落入 acceptable_tiers 之外的档位才算
    clean_total = 0
    false_alarm = 0
    fa_detail: Dict[str, int] = {}
    for cid, lab in labels.items():
        if cid not in results or lab["planted"]:
            continue
        clean_total += 1
        ok_tiers = lab.get("acceptable_tiers") or ["external"]
        if results[cid]["tier"] not in ok_tiers:
            false_alarm += 1
            fa_detail[results[cid]["tier"]] = fa_detail.get(results[cid]["tier"], 0) + 1

    print("=" * 78)
    print("MDCA 批量候选实验（阶段 D）")
    print("=" * 78)
    print(f"候选总数 {total}（干净 {clean['n']}，植入 {total - clean['n']}）")
    print()
    print("四档分布：")
    for k in ("eliminate", "refine", "manual", "external"):
        print(f"  {G.TIER_LABEL[k]:<8}{tiers[k]:>5}")
    print()
    print(f"人工检查量：{manual_before} → {manual_after}"
          f"（降 {100 * (1 - manual_after / max(manual_before, 1)):.1f}%）")
    print()
    print("分类型拦截率：")
    print(f"  {'类型':<20}{'数量':>6}{'命中':>6}{'命中率':>9}   四档分布")
    for kind, d in sorted(by_kind.items()):
        rate = 100.0 * d["hits"] / max(d["n"], 1)
        dist = " ".join(f"{G.TIER_LABEL[t]}{n}" for t, n in sorted(d["tiers"].items()))
        print(f"  {kind:<20}{d['n']:>6}{d['hits']:>6}{rate:>8.1f}%   {dist}")
    print()
    print(f"漏报（植入问题进入「送外部」档）：{len(leaks)} 条")
    for cid, kind, st in leaks[:max_show]:
        print(f"    {cid}  {kind}  状态={st}")
    print()
    print(f"误报（干净候选落入期望档之外）：{false_alarm}/{clean_total}"
          f"（{100.0 * false_alarm / max(clean_total, 1):.1f}%）")
    if fa_detail:
        print("   " + "  ".join(f"{G.TIER_LABEL.get(t, t)} {n}"
                                for t, n in sorted(fa_detail.items())))
    print(f"   干净候选四档分布：淘汰 {clean['tiers'].get('eliminate', 0)}、"
          f"人工复核 {clean['tiers'].get('manual', 0)}、"
          f"继续优化 {clean['tiers'].get('refine', 0)}、"
          f"送外部 {clean['tiers'].get('external', 0)}")

    out = HERE / "out" / "batch_eval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "total": total, "tiers": tiers,
        "manual_before": manual_before, "manual_after": manual_after,
        "by_kind": by_kind, "clean": clean,
        "leaks": leaks, "false_alarm": false_alarm,
        "results": results,
        "warning": "候选由本模块随机生成，非真实工程数据；结论只说明工具在合成样本上的行为。",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"明细 → {out}")
    print()
    print("⚠️ 候选由随机搜索生成，非真实工程数据；以上比例不构成对真实设计问题的可靠性声明。")
    return 0


def main(argv: List[str]) -> int:
    cmd = argv[0] if argv else "evaluate"
    if cmd == "generate":
        n = 120
        seed = 20261121
        preset = ""
        rest = argv[1:]
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--preset" and i + 1 < len(rest):
                preset = rest[i + 1]; i += 2
            elif a == "--seed" and i + 1 < len(rest):
                seed = int(rest[i + 1]); i += 2
            else:
                try:
                    n = int(a)
                except ValueError:
                    pass
                i += 1
        return cmd_generate(n, seed, preset)
    if cmd == "evaluate":
        return cmd_evaluate()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
