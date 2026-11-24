# -*- coding: utf-8 -*-
"""
扰动响应实验：冷却液 +20℃，看各节点温度如何响应
================================================
目的：检验「温度对边界条件是否线性」。这是 MDCA 线性热网络的核心假设
      （第 11.4 节）。若 Motor-CAD 的响应也是 +20℃，说明线性假设在该工况成立；
     若是 +18 或 +23，说明有温度相关效应，MDCA 需要知道这个偏差量级。

产物：out/mcad_perturb.csv（节点, T@65, T@85, ΔT）
"""
import csv
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "mcad_perturb.log"
_f = open(LOG, "w", encoding="utf-8")


def log(msg=""):
    print(msg, flush=True)
    _f.write(str(msg) + "\n")
    _f.flush()


def cv(res):
    if isinstance(res, tuple) and len(res) == 2:
        return res
    return (None, res)


INLET_VARS = ["HousingWJ_Inlet_Temperature", "Wj_Fluid_Inlet_Temperature"]

mc = pymotorcad.MotorCADCompatibility()
log("[1] 已连接")
mc.LoadFromFile(MOT)
log("[2] 已加载")


def set_inlet(val):
    got = []
    for v in INLET_VARS:
        try:
            mc.SetVariable(v, val)
            c, cur = cv(mc.GetVariable(v))
            got.append((v, c, cur))
        except Exception as e:
            got.append((v, "EXC", str(e)[:40]))
    return got


def snapshot():
    temps, powers = {}, {}
    for i in range(0, 90):
        try:
            _, t = cv(mc.GetNodeTemperature(i))
            _, p = cv(mc.GetNodePower(i))
        except Exception:
            break
        temps[i] = t if isinstance(t, (int, float)) else None
        powers[i] = p if isinstance(p, (int, float)) else None
    return temps, powers


results = {}
for inlet in (65, 85):
    log(f"\n[3] 设定冷却液 {inlet}℃")
    for v, c, cur in set_inlet(inlet):
        log(f"    {v:<34} code={c} -> {cur}")

    t0 = time.time()
    try:
        mc.DoMagneticThermalCalculation()
        log(f"    计算完成 {time.time()-t0:.0f}s")
    except Exception as e:
        log(f"    计算失败: {type(e).__name__}: {e}")
        traceback.print_exc(file=_f)

    temps, powers = snapshot()
    results[inlet] = (temps, powers)
    tot = sum(p for p in powers.values() if p)
    hot = max((i for i, t in temps.items() if t is not None),
              key=lambda i: temps[i], default=None)
    log(f"    总损耗 {tot:.2f} W，最高温 node {hot} = {temps.get(hot):.3f}℃")

# 对比
t65, p65 = results.get(65, ({}, {}))
t85, p85 = results.get(85, ({}, {}))

log("\n[4] 温度响应 ΔT = T@85 - T@65")
rows = []
for i in sorted(t65):
    a, b = t65.get(i), t85.get(i)
    if a is None or b is None:
        continue
    rows.append((i, a, b, b - a))

print_rows = [r for r in rows if abs(r[3]) > 1e-9]
log(f"    参与对比节点 {len(rows)}，ΔT 非零 {len(print_rows)}")
if rows:
    dts = [r[3] for r in rows]
    log(f"    ΔT 范围 {min(dts):.3f} ~ {max(dts):.3f}℃，均值 {sum(dts)/len(dts):.3f}℃")

log("\n[5] 关键节点")
for i in (9, 62, 18, 11, 0):
    if i in t65 and i in t85:
        log(f"    node {i:<3} T65={t65[i]:8.3f}  T85={t85[i]:8.3f}  ΔT={t85[i]-t65[i]:+7.3f}")

# 损耗是否随温度变化
log("\n[6] 损耗对比（检验热-电耦合强度）")
for i in sorted(p65):
    a, b = p65.get(i), p85.get(i)
    if a is None or b is None:
        continue
    if abs(a) > 1 or abs(b) > 1:
        log(f"    node {i:<3} P65={a:9.3f}  P85={b:9.3f}  变化 {b-a:+.3f} W")

csv_path = OUT / "mcad_perturb.csv"
with open(csv_path, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["node", "T_at_65C", "T_at_85C", "delta_T", "P_at_65C", "P_at_85C"])
    for i, a, b, d in rows:
        w.writerow([i, a, b, d, p65.get(i), p85.get(i)])
log(f"\n[7] 已写 {csv_path}")

log("\n[8] Quit")
try:
    mc.Quit()
    log("    已退出")
except Exception as e:
    log(f"    失败: {type(e).__name__}")

log("\n[DONE]")
_f.close()
