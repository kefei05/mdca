# -*- coding: utf-8 -*-
"""
节点拓扑探测：用 GetNodeToNodeResistance 找出关键节点连到谁
============================================================
思路：node 9 有 571 W 功耗、温度只有 116.5℃；node 62 有 101 W、温度却 159.5℃。
这不合常理，除非它们不在同一条传热路径的同一位置。

用节点间热阻反推：
  · node 9 连到哪些节点、热阻多大
  · node 62 连到哪些节点
  · 再看温度梯度方向，判断谁是"注入点"谁是"观测点"

不跑热计算（热阻是几何+材料属性），加载模型即可。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"
OUT = Path(__file__).resolve().parent.parent / "out"
LOG = OUT / "mcad_topology.log"
_f = open(LOG, "w", encoding="utf-8")


def log(msg=""):
    print(msg, flush=True)
    _f.write(str(msg) + "\n")
    _f.flush()


def cv(res):
    if isinstance(res, tuple) and len(res) == 2:
        return res
    return (None, res)


mc = pymotorcad.MotorCADCompatibility()
log("[1] 已连接")
mc.LoadFromFile(MOT)
log("[2] 已加载")


def neighbours(src, threshold=1e-9):
    out = []
    for j in range(0, 90):
        if j == src:
            continue
        try:
            c, r = cv(mc.GetNodeToNodeResistance(src, j))
        except Exception:
            continue
        if c == 0 and isinstance(r, (int, float)) and r != 0 and abs(r) < 1e6:
            out.append((j, r))
    return out


for src, note in ((9, "571W / 116.5C"), (62, "101W / 159.5C"), (18, "7.5W / 150.8C")):
    t0 = time.time()
    nb = neighbours(src)
    log(f"\n[3] node {src}（{note}）的邻居：{len(nb)} 个，耗时 {time.time()-t0:.1f}s")
    for j, r in sorted(nb, key=lambda z: z[1]):
        log(f"     -> node {j:<3} R = {r:.6g} K/W")

# 全局：统计每个节点的连接数，找枢纽
log("\n[4] 逐节点连接数（0..89）")
deg = {}
for i in range(0, 90):
    nb = neighbours(i)
    deg[i] = len(nb)
log("    连接数分布：")
from collections import Counter
for cnt, n in sorted(Counter(deg.values()).items()):
    log(f"      连 {cnt} 个节点的：{n} 个")
hub = sorted(deg.items(), key=lambda kv: -kv[1])[:8]
log(f"    枢纽节点（连接最多）：{hub}")

log("\n[DONE]")
try:
    mc.Quit()
    log("已退出")
except Exception:
    pass
_f.close()
