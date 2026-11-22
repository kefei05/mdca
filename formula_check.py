# -*- coding: utf-8 -*-
"""
公式层检查：退化、极限、手算对照、单调性
==========================================
B 层只证明「代码按写的方式运行」。若公式本身错了，B 层与「解析 vs 迭代」都查不出来
（因为两种实现可能共用同一个错公式）。本脚本查的是另一类错误：

  ① 退化：某个参数取极限时，解必须退化成已知的简单式
  ② 极限：g→1 必须发散；α=0 必须无反馈
  ③ 手算：独立手算若干个点，与代码逐位比
  ④ 单调：每个参数增大，θ 必须不减（区间顶点求值的前提）

每一条都给出「应该等于什么」，不依赖代码自己的说法。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from mdca_thermal import Cas, Cas3, solve_analytic, solve_point, solve_three  # noqa: E402

ALPHA = 0.00393
ok_n = 0
fail_n = 0


def check(label, got, want, tol=1e-9):
    global ok_n, fail_n
    good = abs(got - want) <= tol * max(1.0, abs(want))
    if good:
        ok_n += 1
    else:
        fail_n += 1
    mark = "PASS" if good else "FAIL"
    print(f"  {label:<52}{mark}   实得 {got:>14.6f}  应为 {want:>14.6f}")


def analytic(Rws, Rsa, P0, Pfe, Ta, Tref=20.0, alpha=ALPHA):
    c = Cas(name="t", Rws=Rws, Rsa=Rsa, P0=P0, Pfe=Pfe, Ta=Ta, Tref=Tref, alpha=alpha)
    return solve_analytic(c)


print("=" * 100)
print("① 退化检验：某个参数取极限，解必须退化成已知的简单式")
print("=" * 100)

# 1. α=0 ⇒ 无热反馈 ⇒ Tw = Ta + Rsa·Pfe + Req·P0
Rws, Rsa, P0, Pfe, Ta = 1.16, 2.03, 13.6, 5.33, 40.0
want = Ta + Rsa * Pfe + (Rws + Rsa) * P0
check("α=0 ⇒ Tw = Ta + Rsa·Pfe + (Rws+Rsa)·P0",
      analytic(Rws, Rsa, P0, Pfe, Ta, alpha=0.0), want)

# 2. α=0 时 Tref 不应再起作用（换个 Tref 结果不变）
check("α=0 ⇒ 结果与 Tref 无关（Tref 改 -50）",
      analytic(Rws, Rsa, P0, Pfe, Ta, Tref=-50.0, alpha=0.0), want)

# 3. P0=0 ⇒ 无铜损 ⇒ 无热反馈 ⇒ Tw = Ta + Rsa·Pfe
check("P0=0 ⇒ Tw = Ta + Rsa·Pfe",
      analytic(Rws, Rsa, 0.0, Pfe, Ta), Ta + Rsa * Pfe)

# 4. Pfe=0 且 P0=0 ⇒ Tw = Ta（无损耗）
check("P0=Pfe=0 ⇒ Tw = Ta", analytic(Rws, Rsa, 0.0, 0.0, Ta), Ta)

# 5. Rsa=0 ⇒ 定子温度等于环境 ⇒ Tw = Ta + Rws·Pcu（Pcu 为热态值）
c = Cas(name="t", Rws=Rws, Rsa=0.0, P0=P0, Pfe=Pfe, Ta=Ta, Tref=20.0, alpha=ALPHA)
tw = solve_analytic(c)
Pcu_hot = P0 * (1 + ALPHA * (tw - 20.0))
check("Rsa=0 ⇒ Tw = Ta + Rws·Pcu(Tw)（自洽解）", tw, Ta + Rws * Pcu_hot)

# 6. Ta=Tref ⇒ N 里 (Ta−Tref) 项消失
tw_tref = analytic(Rws, Rsa, P0, Pfe, 20.0)
N = Rsa * Pfe + (Rws + Rsa) * P0
g = ALPHA * (Rws + Rsa) * P0
check("Ta=Tref=20 ⇒ θ = (Rsa·Pfe+Req·P0)/(1−g)",
      tw_tref, 20.0 + N / (1 - g))

print()
print("=" * 100)
print("② 极限检验：g→1 必须发散")
print("=" * 100)
for frac in (0.5, 0.9, 0.99, 0.999):
    Rreq = 3.19
    P0x = (frac / ALPHA) / Rreq
    c = Cas(name="t", Rws=1.16, Rsa=2.03, P0=P0x, Pfe=5.33, Ta=40.0, alpha=ALPHA)
    tw = solve_analytic(c)
    expect = 20.0 + c.N / (1 - frac)
    good = abs(tw - expect) < 1e-6 * abs(expect)
    ok_n += 1 if good else 0
    fail_n += 0 if good else 1
    print(f"  g={frac:<6} P0={P0x:>9.2f}  Tw={tw:>12.3f}  解析式一致  "
          f"{'PASS' if good else 'FAIL'}")

for gv in (1.0, 2.0):
    c = Cas(name="t", Rws=1.16, Rsa=2.03, P0=(gv / ALPHA) / 3.19, Pfe=5.33, Ta=40.0)
    got = solve_analytic(c)
    good = got is None
    ok_n += 1 if good else 0
    fail_n += 0 if good else 1
    print(f"  g={gv:<6} 解析解          {got}   应为 None（发散）  "
          f"{'PASS' if good else 'FAIL'}")

print()
print("=" * 100)
print("③ 三节点退化到两热阻（Peddy=0 时两者必须完全相同）")
print("=" * 100)
for kw in (dict(Rws=1.16, Rsa=2.03, P0=13.6, Pfe=5.33, Ta=40.0),
           dict(Rws=0.5, Rsa=1.0, P0=30.0, Pfe=10.0, Ta=25.0),
           dict(Rws=3.0, Rsa=6.0, P0=5.0, Pfe=2.0, Ta=60.0)):
    t2 = analytic(**kw)
    c3 = Cas3(name="t", Rws=kw["Rws"], Rsa=kw["Rsa"], P0=kw["P0"], Pfe=kw["Pfe"],
              Peddy=0.0, Rrs=3.0, Ta=kw["Ta"], alpha=ALPHA, Tref=20.0)
    r3 = solve_three(c3)
    check(f"Peddy=0 ⇒ 三节点 Tw == 两热阻 Tw   {kw['Rws']}/{kw['Rsa']}/{kw['P0']}",
          r3["Tw"], t2, tol=1e-6)

print()
print("=" * 100)
print("④ 三节点拓扑检验：Rrs 是「转子→定子」而非「转子→绕组」")
print("=" * 100)
# 拓扑：Ta --Rsa-- Ts --Rws-- Tw，Rrs 从 Ts 分叉到 Tr（与 Rws 并联）
# Peddy 注入 Tr 节点，全部经 Rrs 流向 Ts ⇒ Tr = Ts + Rrs·Peddy
c3 = Cas3(name="t", Rws=1.16, Rsa=2.03, P0=13.6, Pfe=5.33, Peddy=15.24,
          Rrs=0.0, Ta=40.0, alpha=ALPHA, Tref=20.0)
r = solve_three(c3)
check("Rrs=0 ⇒ Tr == Ts（转子与定子同温）", r["Tr"], r["Ts"], tol=1e-9)
check("Rrs=0 ⇒ Tw == Ts + Rws·Pcu（绕组仍高于定子）", r["Tw"], r["Ts"] + 1.16 * r["Pcu"], tol=1e-6)

c3 = Cas3(name="t", Rws=1.16, Rsa=2.03, P0=13.6, Pfe=5.33, Peddy=15.24,
          Rrs=3.0, Ta=40.0, alpha=ALPHA, Tref=20.0)
r = solve_three(c3)
check("一般情形 Tr == Ts + Rrs·Peddy", r["Tr"], r["Ts"] + 3.0 * 15.24, tol=1e-6)
check("一般情形 Tw == Ts + Rws·Pcu", r["Tw"], r["Ts"] + 1.16 * r["Pcu"], tol=1e-6)

print()
print("=" * 100)
print("⑤ Peddy 只影响 Tr，不影响 Tw")
print("=" * 100)
# 注意：Peddy 会经 Rsa 出去，从而抬高 Ts，进而抬高 Tw。所以「不影响 Tw」是错的期待！
# 正确的期待：Peddy 增大，Tw 增大但增量小于 Tr 的增量
a = solve_three(Cas3(name="a", Rws=1.16, Rsa=2.03, P0=13.6, Pfe=5.33, Peddy=5.0,
                     Rrs=3.0, Ta=40.0, alpha=0.0))
b = solve_three(Cas3(name="b", Rws=1.16, Rsa=2.03, P0=13.6, Pfe=5.33, Peddy=15.0,
                     Rrs=3.0, Ta=40.0, alpha=0.0))
d_tw = b["Tw"] - a["Tw"]
d_tr = b["Tr"] - a["Tr"]
print(f"  α=0，Peddy 5→15 W：ΔTw = {d_tw:+.4f} K，ΔTr = {d_tr:+.4f} K")
# 严格推导：ΔTw = Rsa·ΔPeddy = 2.03×10 = 20.3；ΔTr = (Rsa+Rrs)·ΔPeddy = 5.03×10 = 50.3
check("ΔTw = Rsa·ΔPeddy", d_tw, 2.03 * 10.0, tol=1e-6)
check("ΔTr = (Rsa+Rrs)·ΔPeddy", d_tr, (2.03 + 3.0) * 10.0, tol=1e-6)

print()
print("=" * 100)
print("⑥ 手算对照（独立算，不看代码）")
print("=" * 100)
CASES = [
    (1.16, 2.03, 13.6, 5.33, 40.0),
    (0.50, 1.00, 30.0, 10.0, 25.0),
    (3.00, 6.00, 5.0, 2.0, 60.0),
    (0.10, 0.04, 372.9, 32.2, 65.0),
]
for Rws_, Rsa_, P0_, Pfe_, Ta_ in CASES:
    Req_ = Rws_ + Rsa_
    g_ = ALPHA * Req_ * P0_
    N_ = (Ta_ - 20.0) + Rsa_ * Pfe_ + Req_ * P0_
    want_ = 20.0 + N_ / (1 - g_)
    check(f"手算 Rws={Rws_} Rsa={Rsa_} P0={P0_} Ta={Ta_}", analytic(Rws_, Rsa_, P0_, Pfe_, Ta_), want_)

print()
print("=" * 100)
print("⑦ 单调性：每个参数增大，θ 必须不减（区间顶点求值的前提）")
print("=" * 100)
base = dict(Rws=1.16, Rsa=2.03, P0=13.6, Pfe=5.33, Ta=40.0)
t0 = analytic(**base)
for key, step in (("Rws", 0.1), ("Rsa", 0.1), ("P0", 1.0), ("Pfe", 1.0), ("Ta", 5.0)):
    kw = dict(base)
    kw[key] += step
    t1 = analytic(**kw)
    mono = t1 >= t0 - 1e-12
    print(f"  {key:<5} +{step:<5} → Tw {t0:.4f} → {t1:.4f}  "
          f"{'单调不减 PASS' if mono else '不单调 FAIL'}")
    if mono:
        ok_n += 1
    else:
        fail_n += 1

print()
print("=" * 100)
print(f"结果：{ok_n} 项通过，{fail_n} 项失败")
print("=" * 100)
