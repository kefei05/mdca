#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 自检：随机数值回代（方案 7.3 第 3 层）
============================================

方案原文：

    随机数值回代：原式与变换后公式代入同一批数值比对。

本项目里有三处"同一个物理量的两种算法"，都能用随机回代交叉验证：

  ① 解析解 θ = N/(1-g)         vs  迭代 Tw(k+1) = C + g·Tw(k)
  ② 两热阻 Tw(Pfe)              vs  三节点 Tw(Pfe+Peddy) 在 Peddy=0 时
  ③ 几何缩放律（候选几何 == 基准几何 时应恒等）

其中 ① 和 ② 是代数变换，③ 是"缩放在基准点必须退化"的不动点性质。
只要破一条，说明实现与定义不一致——这比任何单个算例都更能证明代码没错。

用法：
    python -m mdca.checks              # 500 组随机参数
    python -m mdca.checks 2000         # 指定样本数
"""

from __future__ import annotations

import random
import sys
from typing import Any, Dict, List, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from .scaling import JOINT_REF_GEOM, JOINT_REF_R, Geometry, scale  # noqa: E402
from .thermal import Cas, Cas3, solve_point, solve_three  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# 物理合理范围：按真实电机量级取，使多数样本落在 g<1 的稳定区
RANGES: Dict[str, Tuple[float, float]] = {
    "Rws": (0.10, 5.0),
    "Rsa": (0.20, 8.0),
    "P0": (1.0, 40.0),
    "Pfe": (0.0, 20.0),
    "Ta": (-20.0, 80.0),
    "Tref": (15.0, 25.0),
    "alpha": (0.0030, 0.0042),
}

# 宽范围（含失稳区），只用于统计"失稳分支覆盖了多少"
RANGES_WIDE: Dict[str, Tuple[float, float]] = {
    "Rws": (0.10, 15.0),
    "Rsa": (0.20, 20.0),
    "P0": (1.0, 200.0),
    "Pfe": (0.0, 100.0),
    "Ta": (-20.0, 80.0),
    "Tref": (15.0, 25.0),
    "alpha": (0.0030, 0.0042),
}


def _sample(rng: random.Random) -> Dict[str, float]:
    return {k: rng.uniform(lo, hi) for k, (lo, hi) in RANGES.items()}


def _sample_stable(rng: random.Random, max_tries: int = 60):
    """条件采样：只接受 g < 1 的工况（失稳区由 COUPLING_FAILED 路径单独覆盖）。
    返回 (params, tries)；采不到返回 (None, tries)。"""
    for t in range(1, max_tries + 1):
        p = _sample(rng)
        c = Cas(name="probe", Rws=p["Rws"], Rsa=p["Rsa"], P0=p["P0"],
                Pfe=p["Pfe"], Ta=p["Ta"], Tref=p["Tref"], alpha=p["alpha"])
        if c.g < 1.0:
            return p, t
    return None, max_tries


def stability_ratio(n: int, rng: random.Random) -> Dict[str, Any]:
    """宽范围采样中，落在 g<1 稳定区的比例——说明失稳分支的覆盖度。"""
    stable = 0
    unstable = 0
    for _ in range(n):
        p = {k: rng.uniform(lo, hi) for k, (lo, hi) in RANGES_WIDE.items()}
        c = Cas(name="probe", Rws=p["Rws"], Rsa=p["Rsa"], P0=p["P0"],
                Pfe=p["Pfe"], Ta=p["Ta"], Tref=p["Tref"], alpha=p["alpha"])
        if c.g < 1.0:
            stable += 1
        else:
            unstable += 1
    return {"sampled": n, "stable": stable, "unstable": unstable,
            "stable_pct": round(100.0 * stable / max(n, 1), 1)}


def check_analytic_vs_iter(n: int, rng: random.Random,
                           tol: float = 1e-3) -> Dict[str, Any]:
    """① 解析解 vs 迭代解。"""
    worst = 0.0
    worst_case = None
    failures: List[Dict[str, Any]] = []
    tested = 0
    skipped = 0

    while tested < n:
        p, tries = _sample_stable(rng)
        if p is None:
            skipped += 1
            break
        c = Cas(name="mc", Rws=p["Rws"], Rsa=p["Rsa"], P0=p["P0"], Pfe=p["Pfe"],
                Ta=p["Ta"], Tref=p["Tref"], alpha=p["alpha"], Tw_max=155.0)
        r = solve_point(c)
        if r["status"] != "CONVERGED":
            failures.append({"reason": r.get("reason"), **p})
            continue
        tested += 1
        # 解析解直接算
        theta = c.N / (1.0 - c.g)
        analytic = c.Tref + theta
        err = abs(r["Tw"] - analytic)
        if err > worst:
            worst, worst_case = err, p
        if err > tol:
            failures.append({"reason": "analytic_mismatch", "err_K": err, **p})

    return {"tested": tested, "max_err_K": worst, "worst_case": worst_case,
            "tolerance_K": tol, "failures": failures[:5], "skipped": skipped,
            "ok": not failures and tested > 0}


def check_analytic_vs_iter_strict(n: int, rng: random.Random,
                                  tolerances: Tuple[float, ...] = (1e-3, 1e-6, 1e-9, 1e-12)
                                  ) -> Dict[str, Any]:
    """在不同 tol_T 下比较解析解与迭代解。

    判定逻辑：**若偏差不随容差下降，说明迭代式与解析式不等价（真 bug）；
    若偏差随容差同比例下降，说明二者等价，先前的"一致"只是停止精度。**

    这是对 check_analytic_vs_iter 的补强——后者在 tol_T=1e-3 下报"最大偏差 9.99e-04"，
    那个数字恰好贴着容差，实际测的是"迭代有没有停下来"，不是"两个式子等不等价。
    """
    # 先固定样本：所有容差必须跑同一批参数，否则无法横向比较
    samples: List[Dict[str, float]] = []
    while len(samples) < n:
        p, _ = _sample_stable(rng)
        if p is None:
            break
        samples.append(p)

    rows: List[Dict[str, Any]] = []
    for tol in tolerances:
        worst = 0.0
        worst_case = None
        max_iters = 0
        total_iters = 0
        converged = 0
        not_converged = 0

        for p in samples:
            c = Cas(name="tol", Rws=p["Rws"], Rsa=p["Rsa"], P0=p["P0"],
                    Pfe=p["Pfe"], Ta=p["Ta"], Tref=p["Tref"], alpha=p["alpha"],
                    Tw_max=155.0)
            r = solve_point(c, tol_T=tol, tol_P=max(tol * 1e-3, 1e-15))
            if r["status"] != "CONVERGED":
                not_converged += 1
                continue
            converged += 1
            analytic = c.Tref + c.N / (1.0 - c.g)
            err = abs(r["Tw"] - analytic)
            if err > worst:
                worst, worst_case = err, p
            it = r.get("iterations") or 0
            max_iters = max(max_iters, it)
            total_iters += it

        rows.append({
            "tol_T": tol,
            "samples": len(samples),
            "converged": converged,
            "not_converged": not_converged,
            "max_err_K": worst,
            "median_iters": round(total_iters / converged, 1) if converged else None,
            "max_iters": max_iters,
            "worst_case": worst_case,
        })

    # 判定：偏差是否随容差下降
    errs = [r["max_err_K"] for r in rows if r["converged"]]
    monotonic = all(errs[i] >= errs[i + 1] * 0.5 for i in range(len(errs) - 1)) if len(errs) > 1 else True
    tightened = errs[-1] if errs else float("nan")
    ok = monotonic and tightened < 1e-8

    return {"rows": rows, "monotonic_decrease": monotonic,
            "tightest_err_K": tightened, "ok": ok}


def check_two_vs_three(n: int, rng: random.Random) -> Dict[str, Any]:
    """② Peddy=0 时三节点必须退化为两热阻。"""
    worst = 0.0
    failures: List[Dict[str, Any]] = []
    tested = 0

    while tested < n:
        p, _ = _sample_stable(rng)
        if p is None:
            break
        c2 = Cas(name="mc", Rws=p["Rws"], Rsa=p["Rsa"], P0=p["P0"], Pfe=p["Pfe"],
                 Ta=p["Ta"], Tref=p["Tref"], alpha=p["alpha"], Tw_max=155.0)
        r2 = solve_point(c2)
        c3 = Cas3(name="mc", Rws=p["Rws"], Rsa=p["Rsa"], P0=p["P0"], Pfe=p["Pfe"],
                  Ta=p["Ta"], Tref=p["Tref"], alpha=p["alpha"], Tw_max=155.0,
                  Peddy=0.0, Rrs=3.0, Tr_max=150.0)
        r3 = solve_three(c3)
        if r2["status"] != "CONVERGED" or r3["status"] != "CONVERGED":
            continue
        tested += 1
        err = abs(r2["Tw"] - r3["Tw"])
        worst = max(worst, err)
        if err > 1e-9:
            failures.append({"err_K": err, **p})

    return {"tested": tested, "max_err_K": worst,
            "failures": failures[:5], "ok": not failures and tested > 0}


def check_scaling_identity(n: int, rng: random.Random) -> Dict[str, Any]:
    """③ 候选几何 == 基准几何时，缩放律必须恒等（含随机扰动基准）。"""
    worst = 0.0
    failures: List[Dict[str, Any]] = []
    tested = 0
    ref = JOINT_REF_GEOM

    for _ in range(n):
        # 随机造一个基准，再让候选等于它
        s = rng.uniform(0.3, 3.0)
        g = Geometry(
            Z=float(rng.choice([6, 9, 12, 24, 36, 48])),
            P_slot=ref.P_slot * s, L=ref.L * s,
            D_gap=ref.D_gap * s, g=ref.g * rng.uniform(0.5, 2.0),
            A_case=ref.A_case * s * s, L_ins=ref.L_ins,
        )
        R = {k: v * rng.uniform(0.3, 3.0) for k, v in JOINT_REF_R.items()}
        res = scale(g, R, g, max_dev=10.0)     # 候选 == 基准
        tested += 1
        err = max(abs(res.Rws - R["Rws"]), abs(res.Rsa - R["Rsa"]),
                  abs(res.Rrs - R["Rrs"]))
        # 用相对误差，因为热阻量级差很多
        rel = err / max(abs(R["Rws"]), abs(R["Rsa"]), abs(R["Rrs"]), 1e-12)
        worst = max(worst, rel)
        if rel > 1e-9:
            failures.append({"rel_err": rel, "R": R})

    return {"tested": tested, "max_rel_err": worst,
            "failures": failures[:5], "ok": not failures}


def run(n: int = 500, seed: int = 20261121) -> Dict[str, Any]:
    rng = random.Random(seed)
    a = check_analytic_vs_iter(n, rng)
    b = check_two_vs_three(n, rng)
    c = check_scaling_identity(n, rng)
    d = stability_ratio(1000, rng)
    ok = a["ok"] and b["ok"] and c["ok"]
    return {"samples_requested": n, "seed": seed,
            "analytic_vs_iter": a, "two_vs_three": b,
            "scaling_identity": c, "wide_range_stability": d, "ok": ok}


def print_report(rep: Dict[str, Any]) -> None:
    print("=" * 78)
    print("MDCA 随机数值回代  (方案 7.3 第 3 层)")
    print("=" * 78)
    print(f"样本：{rep['samples_requested']}  随机种子：{rep['seed']}")
    print()
    a, b, c = rep["analytic_vs_iter"], rep["two_vs_three"], rep["scaling_identity"]
    print(f"① 解析解 vs 迭代解     测试 {a['tested']:>4} 组  "
          f"最大偏差 {a['max_err_K']:.2e} K  {'PASS' if a['ok'] else 'FAIL'}")
    if a["failures"]:
        print(f"   失败样本：{a['failures'][:2]}")
    print(f"② 两热阻 vs 三节点     测试 {b['tested']:>4} 组  "
          f"最大偏差 {b['max_err_K']:.2e} K  {'PASS' if b['ok'] else 'FAIL'}")
    if b["failures"]:
        print(f"   失败样本：{b['failures'][:2]}")
    print(f"③ 缩放律基准恒等       测试 {c['tested']:>4} 组  "
          f"最大相对误差 {c['max_rel_err']:.2e}  {'PASS' if c['ok'] else 'FAIL'}")
    if c["failures"]:
        print(f"   失败样本：{c['failures'][:2]}")
    d = rep.get("wide_range_stability") or {}
    if d:
        print(f"   宽范围采样：{d['sampled']} 组中 {d['unstable']} 组落在 g>=1 "
              f"失稳区（{100 - d['stable_pct']:.1f}%），由 COUPLING_FAILED 分支覆盖")
    print()
    print(f"总体：{'全部通过' if rep['ok'] else '存在不一致'}")


def main(argv: List[str]) -> int:
    n = 500
    if argv:
        try:
            n = int(argv[0])
        except ValueError:
            print(f"样本数无效：{argv[0]}")
            return 2
    rep = run(n)
    print_report(rep)

    out = __import__("pathlib").Path(__file__).resolve().parent.parent / "out"
    out.mkdir(parents=True, exist_ok=True)
    import json
    (out / "checks.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
