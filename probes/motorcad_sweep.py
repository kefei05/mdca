# -*- coding: utf-8 -*-
"""
多点边界扰动：45 / 65 / 85 / 105 / 125 ℃，拟合温度响应的线性度
================================================================
目的：判断 MDCA 的「线性热网络 + 常数 Δ_cal」假设是否成立，
     以及偏差是否随 ΔTa 增长（→ 需要斜率项）。

固定观测节点（沿用第一次实验的分区）：
  COLD = 定子轭/齿       → Ts
  HOT  = 绕组/端部       → Tw
  MAG  = node 62         → Tr
"""
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"
OUT = Path(__file__).resolve().parent.parent / "out"
LOG = OUT / "mcad_sweep.log"
_f = open(LOG, "w", encoding="utf-8")


def log(msg=""):
    print(msg, flush=True)
    _f.write(str(msg) + "\n")
    _f.flush()


def cv(res):
    if isinstance(res, tuple) and len(res) == 2:
        return res
    return (None, res)


COLD = [1, 2, 3, 4, 6, 7, 8]
SLOT = [9, 65, 66]
HOT = [11, 12, 13, 15, 16, 18, 23, 24]
MAG = [62]
INLETS = [45, 65, 85, 105, 125]

mc = pymotorcad.MotorCADCompatibility()
log("[1] 已连接")
mc.LoadFromFile(MOT)
log("[2] 已加载\n")

rows = []
for ta in INLETS:
    for v in ("HousingWJ_Inlet_Temperature", "Wj_Fluid_Inlet_Temperature"):
        try:
            mc.SetVariable(v, ta)
        except Exception:
            pass

    t0 = time.time()
    try:
        mc.DoMagneticThermalCalculation()
    except Exception as e:
        log(f"[{ta}℃] 计算失败: {type(e).__name__}: {e}")
        continue

    temps, powers = {}, {}
    for i in range(0, 90):
        try:
            _, t = cv(mc.GetNodeTemperature(i))
            _, p = cv(mc.GetNodePower(i))
        except Exception:
            break
        temps[i] = t if isinstance(t, (int, float)) else None
        powers[i] = p if isinstance(p, (int, float)) else None

    def avg(nodes):
        vals = [temps[n] for n in nodes if temps.get(n) is not None]
        return sum(vals) / len(vals) if vals else float("nan")

    def total(nodes):
        return sum(powers[n] for n in nodes if isinstance(powers.get(n), (int, float)))

    Ts, Tw, Tr = avg(COLD), avg(HOT), avg(MAG)
    Pcu = total(SLOT)
    Ptot = sum(p for p in powers.values() if isinstance(p, (int, float)))
    hot_node = max((i for i, t in temps.items() if t is not None), key=lambda i: temps[i])

    rows.append({"Ta": ta, "Ts": Ts, "Tw": Tw, "Tr": Tr,
                 "Pcu": Pcu, "Ptot": Ptot, "Thot": temps[hot_node], "hot_node": hot_node})
    log(f"[{ta:>4}℃] {time.time()-t0:4.0f}s  Ts={Ts:7.2f}  Tw={Tw:7.2f}  Tr={Tr:7.2f}"
        f"  Pcu={Pcu:7.2f}  Ptot={Ptot:7.2f}  最高 node{hot_node}={temps[hot_node]:.2f}")

# 拟合
log("\n=== 以 65℃ 为基准的响应 ===")
base = next((r for r in rows if r["Ta"] == 65), None)
if base:
    print(f"{'Ta':>6}{'ΔTa':>7}{'ΔTs':>9}{'ΔTw':>9}{'ΔTr':>9}{'ΔTw/ΔTa':>10}{'ΔTs/ΔTa':>10}")
    log(f"{'Ta':>6}{'ΔTa':>7}{'ΔTs':>9}{'ΔTw':>9}{'ΔTr':>9}{'ΔTw/ΔTa':>10}{'ΔTs/ΔTa':>10}")
    for r in rows:
        dta = r["Ta"] - base["Ta"]
        if dta == 0:
            continue
        dts, dtw, dtr = r["Ts"] - base["Ts"], r["Tw"] - base["Tw"], r["Tr"] - base["Tr"]
        line = (f"{r['Ta']:>6}{dta:>+7}{dts:>9.2f}{dtw:>9.2f}{dtr:>9.2f}"
                f"{dtw/dta:>10.3f}{dts/dta:>10.3f}")
        print(line)
        log(line)

# 损耗随温度
log("\n=== 损耗随温度变化 ===")
if base:
    for r in rows:
        if r["Ta"] == base["Ta"]:
            continue
        log(f"  Ta={r['Ta']:>4}  Pcu {base['Pcu']:7.2f} -> {r['Pcu']:7.2f}"
            f"  ({100*(r['Pcu']-base['Pcu'])/base['Pcu']:+6.2f}%)"
            f"   Ptot {base['Ptot']:7.2f} -> {r['Ptot']:7.2f}")

csv_path = OUT / "mcad_sweep.csv"
with open(csv_path, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["Ta", "Ts", "Tw", "Tr", "Pcu", "Ptot", "Thot", "hot_node"])
    w.writeheader()
    w.writerows(rows)
log(f"\n已写 {csv_path}")

log("\n[9] Quit")
try:
    mc.Quit()
    log("    已退出")
except Exception as e:
    log(f"    失败: {type(e).__name__}")

log("\n[DONE]")
_f.close()
