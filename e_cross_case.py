# -*- coding: utf-8 -*-
"""
跨工况验证：用 65℃ 工况反推三节点热阻，预测 85℃ 工况
=====================================================
这不是同义反复：同一工况下 3 温度反推 3 热阻、再算回 3 温度是恒等式；
但用它预测另一个边界工况，残差就反映「三节点结构能否描述 90 节点网络」。

温度分区（按 65℃ 工况）：
  冷却液   65.00℃   67 节点   0 W
  低温区   92.34℃    7 节点  15.00 W   node 1,2,3,4,6,7,8       → 定子轭/齿
  中温区  114.95℃    3 节点 571.49 W   node 9,65,66             → 槽内铜（热源）
  较高温  136.91℃    3 节点   7.50 W   node 17,26,27
  高温区  155.95℃    9 节点 110.85 W   node 11,12,13,15,16,18,23,24,62

判读：铜损注入点（114.95℃）不是最高温点（155.95℃）。
      说明「绕组温度」在这台电机里不是一个数，而是分布。
"""
import csv
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
rows = list(csv.DictReader(open(OUT / "mcad_perturb.csv", encoding="utf-8")))
DATA = {int(r["node"]): (float(r["T_at_65C"]), float(r["T_at_85C"]),
                         float(r["P_at_65C"]), float(r["P_at_85C"]))
        for r in rows}

COLD = [1, 2, 3, 4, 6, 7, 8]                     # 定子轭/齿
SLOT = [9, 65, 66]                                # 槽内铜（热源）
HOT = [11, 12, 13, 15, 16, 18, 23, 24]            # 绕组/端部（不含 62）
MAG = [62]                                        # 独立热源，疑似磁钢/端部


def mean_of(nodes, idx):
    vals = [DATA[n][idx] for n in nodes if n in DATA]
    return sum(vals) / len(vals) if vals else float("nan")


def sum_of(nodes, idx):
    return sum(DATA[n][idx] for n in nodes if n in DATA)


TA1, TA2 = 65.0, 85.0

# 三个代表温度
Ts1, Ts2 = mean_of(COLD, 0), mean_of(COLD, 1)
Tw1, Tw2 = mean_of(HOT, 0), mean_of(HOT, 1)
Tr1, Tr2 = mean_of(MAG, 0), mean_of(MAG, 1)

# 三个损耗（65℃ 工况）
Pcu1 = sum_of(SLOT, 2)
Peddy1 = sum_of(MAG, 2)
Pfe1 = sum_of(list(DATA), 2) - Pcu1 - Peddy1
Ptotal1 = Pcu1 + Pfe1 + Peddy1

print("=" * 74)
print("工况 1（冷却液 65℃）—— 用于反推热阻")
print("=" * 74)
print(f"  Ts = {Ts1:8.3f} ℃   （{len(COLD)} 个定子节点均值）")
print(f"  Tw = {Tw1:8.3f} ℃   （{len(HOT)} 个绕组节点均值）")
print(f"  Tr = {Tr1:8.3f} ℃   （node 62，独立热源 {Peddy1:.2f} W）")
print(f"  Ta = {TA1:8.3f} ℃")
print()
print(f"  Pcu  = {Pcu1:9.3f} W   （槽内铜，node 9/65/66）")
print(f"  Pfe  = {Pfe1:9.3f} W   （其余节点合计）")
print(f"  Peddy= {Peddy1:9.3f} W   （node 62）")
print(f"  合计 = {Ptotal1:9.3f} W")

# 反推热阻
Rws = (Tw1 - Ts1) / Pcu1
Rrs = (Tr1 - Ts1) / Peddy1
Rsa = (Ts1 - TA1) / Ptotal1
Req = Rws + Rsa

print()
print("=" * 74)
print("反推的三节点热阻（fitted_on_same_points = true）")
print("=" * 74)
print(f"  Rws（绕组→定子）= {Rws:.6f} K/W")
print(f"  Rrs（转子→定子）= {Rrs:.6f} K/W")
print(f"  Rsa（定子→环境）= {Rsa:.6f} K/W")
print(f"  Req = {Req:.6f} K/W")
print(f"  Rws 占比 = {100*Rws/Req:.1f}%")

# 预测工况 2
Pcu2 = sum_of(SLOT, 3)
Peddy2 = sum_of(MAG, 3)
Pfe2 = sum_of(list(DATA), 3) - Pcu2 - Peddy2

Tw2_pred = TA2 + Rsa * (Pfe2 + Peddy2) + Req * Pcu2
Tr2_pred = (TA2 + Rsa * (Pcu2 + Pfe2 + Peddy2)) + Rrs * Peddy2
Ts2_pred = TA2 + Rsa * (Pcu2 + Pfe2 + Peddy2)

print()
print("=" * 74)
print("工况 2（冷却液 85℃）—— 预测 vs 实测")
print("=" * 74)
print(f"  损耗：Pcu={Pcu2:.3f}  Pfe={Pfe2:.3f}  Peddy={Peddy2:.3f} W")
print()
print(f"{'量':<8}{'实测':>12}{'三节点预测':>14}{'残差':>12}")
print("-" * 46)
for name, real, pred in (("Ts", Ts2, Ts2_pred), ("Tw", Tw2, Tw2_pred), ("Tr", Tr2, Tr2_pred)):
    print(f"{name:<8}{real:12.3f}{pred:14.3f}{pred-real:+12.3f}")

print()
print("=" * 74)
print("线性假设的直接检验")
print("=" * 74)
print(f"  边界温度变化 ΔTa = {TA2 - TA1:+.1f} K")
print(f"  三节点线性模型必然预测 ΔTw = {TA2 - TA1:+.1f} K")
print(f"  实测 ΔTw = {Tw2 - Tw1:+.3f} K")
print(f"  传递率 = {(Tw2-Tw1)/(TA2-TA1)*100:.1f}%")
print(f"  偏差 = {(TA2-TA1) - (Tw2-Tw1):+.3f} K")
