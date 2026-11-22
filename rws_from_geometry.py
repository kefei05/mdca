# -*- coding: utf-8 -*-
"""
用几何算 Rws（修正版）
=====================
上一版把 Chalmers 的 R_x0 = b/(h·λ) 当成 K/W 直接用了，量纲错了一个米，
导致结果差 24 倍。正确的是：

    R = 路径长 / (λ × 截面积)
    x 方向（切向，往齿传）：截面积 = h × L_stack
    y 方向（径向，往轭传）：截面积 = w × L_stack

所以：R_x0 = w / (λ_sMix · h · L_stack)      ← 分母必须有 L_stack
       R_ix = d_i/(λ_i·h·L) + d_a/(λ_a·h·L)
       R_x  = 0.5·(R_ix + R_x0/6)
    整机   Rws = R_x / Z                       （Z 条并联路径）

几何来源（逐条标注）
    Bs0/Bs1/Bs2 = 2.2/9.5/6 mm        Maxwell建模输入参数.md:18
    Hs0/Hs1/Hs2 = 1/1/8 mm            设计基准_传动与电磁.md:262
    A_slot      = 70.0996 mm²         Maxwell3D仿真交付说明.md:45（AEDT 实测）
    叠厚 30 mm、12 槽                   验收报告_ZZ_route_3D空载场_v1.md:137
"""
# ---- 几何（mm）----
A_SLOT = 70.0996
HS1, HS2 = 1.0, 8.0
Z = 12
L_STACK = 30.0            # mm

# ---- 材料 ----
LAM_I, LAM_A = 0.20, 0.026
LAM_SMIX = 0.22

D_I, D_A = 0.30, 0.05     # mm

A = A_SLOT
d = D_I + D_A
h_eq = HS2 + HS1 / 2.0 - 2 * d
w_eq = A / (h_eq + 2 * d) - 2 * d

print("=" * 92)
print("等效槽尺寸")
print("=" * 92)
print(f"  A = {A:.4f} mm²,  d_total = {d:.3f} mm,  HS1={HS1}, HS2={HS2}")
print(f"  等效槽高 h = {h_eq:.4f} mm")
print(f"  等效槽宽 w = {w_eq:.4f} mm")
print(f"  叠厚 L     = {L_STACK:.1f} mm")


def rws(lam_smix=LAM_SMIX, d_i=D_I, d_a=D_A, lam_i=LAM_I, lam_a=LAM_A, verbose=False):
    """返回 (R_x, R_y) 整机等效热阻 K/W。"""
    h = h_eq / 1000.0
    w = w_eq / 1000.0
    L = L_STACK / 1000.0
    di, da = d_i / 1000.0, d_a / 1000.0
    # 截面积：x 方向穿过 h×L，y 方向穿过 w×L
    A_x = h * L
    A_y = w * L
    R_ix = di / (lam_i * A_x) + da / (lam_a * A_x)
    R_iy = di / (lam_i * A_y) + da / (lam_a * A_y)
    R_x0 = w / (lam_smix * A_x)          # 路径长 w，穿过 h×L
    R_y0 = h / (lam_smix * A_y)          # 路径长 h，穿过 w×L
    R_x = 0.5 * (R_ix + R_x0 / 6.0)
    R_y = 0.5 * (R_iy + R_y0 / 6.0)
    if verbose:
        print()
        print("=" * 92)
        print(f"分解（λ_sMix={lam_smix}）")
        print("=" * 92)
        print(f"  截面积 A_x = h·L = {A_x*1e6:.3f} mm²,  A_y = w·L = {A_y*1e6:.3f} mm²")
        print(f"  R_ix（绝缘+空隙，切向）= {R_ix:8.3f} K/W")
        print(f"  R_iy（绝缘+空隙，径向）= {R_iy:8.3f} K/W")
        print(f"  R_x0（槽内混合物，切向）= {R_x0:8.3f} K/W")
        print(f"  R_y0（槽内混合物，径向）= {R_y0:8.3f} K/W")
        print(f"  R_x = 0.5·(R_ix + R_x0/6) = {R_x:8.3f} K/W  （单槽）")
        print(f"  R_y = 0.5·(R_iy + R_y0/6) = {R_y:8.3f} K/W  （单槽）")
        print(f"  整机 R_x/Z = {R_x/Z:8.4f} K/W,  R_y/Z = {R_y/Z:8.4f} K/W")
    return R_x / Z, R_y / Z


rx, ry = rws(verbose=True)

print()
print("=" * 92)
print("与现用的 Rws = 1.16 K/W 对比")
print("=" * 92)
print(f"  几何算（切向→齿，/Z）  = {rx:.4f} K/W")
print(f"  几何算（径向→轭，/Z）  = {ry:.4f} K/W")
print(f"  现在用的               = 1.1600 K/W")
print(f"  比值（现用/几何切向）  = {1.16/rx:.2f} 倍")

print()
print("=" * 92)
print("λ_sMix 敏感度（我最没依据的输入）")
print("=" * 92)
print(f"{'λ_sMix':>8}{'R_x/Z':>10}{'R_y/Z':>10}   备注")
print("-" * 52)
for lam in (0.10, 0.15, 0.22, 0.30, 0.50, 0.80):
    a, b = rws(lam)
    note = "← 现用" if abs(lam - 0.22) < 1e-9 else ("← Chalmers" if abs(lam - 0.5) < 1e-9 else "")
    print(f"{lam:>8.2f}{a:>10.4f}{b:>10.4f}   {note}")

print()
print("=" * 92)
print("反算：几何算得 Rws 下，温度是多少")
print("=" * 92)
ALPHA, Ta, P0, Pfe, Peddy = 0.00393, 40.0, 13.6, 5.33, 15.24
Rsa, Rrs = 2.03, 3.0
for rws_v, tag in ((1.16, "现用"), (rx, "几何切向"), (ry, "几何径向")):
    tw = 20.0
    for _ in range(300):
        p = P0 * (1 + ALPHA * (tw - 20))
        ts = Ta + Rsa * (p + Pfe + Peddy)
        tw = ts + rws_v * p
    p = P0 * (1 + ALPHA * (tw - 20))
    ts = Ta + Rsa * (p + Pfe + Peddy)
    tr = ts + Rrs * Peddy
    print(f"  Rws={rws_v:6.3f} ({tag:<8})  Tw={tw:7.2f}  Tr={tr:7.2f}  "
          f"{'FAIL' if tw>155 or tr>150 else 'PASS'}")
