# -*- coding: utf-8 -*-
"""
定位铜损跳变点。
现象：45~105℃ 铜损几乎不变（572~577 W，+0.89%），但 125℃ 跳到 708（+22.7%）。
理论：按 α·ΔT 应全程增长（45→105 应涨 15.7%）。

若 45~105 段不涨、125 才涨，说明 Motor-CAD 在某温度阈值切换了损耗模型。
本脚本在 105~125 之间细扫，定位阈值。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"
OUT = Path(__file__).resolve().parent.parent / "out"
LOG = OUT / "mcad_jump.log"
_f = open(LOG, "w", encoding="utf-8")


def log(m=""):
    print(m, flush=True)
    _f.write(str(m) + "\n")
    _f.flush()


def cv(r):
    return r if isinstance(r, tuple) and len(r) == 2 else (None, r)


SLOT = [9, 65, 66]
SCAN = [95, 105, 110, 112, 115, 118, 120, 122, 125]

mc = pymotorcad.MotorCADCompatibility()
mc.LoadFromFile(MOT)
log("[加载完成]")

# 先看与损耗温度缩放相关的设置
log("\n=== 相关 Calc_Options ===")
for k in ("ACLossTemperatureScalingMethod", "FixedTemperatureMethod",
          "MinimumTemperatureTolerance", "MaxWindingTemperature",
          "WindingTemperatureLimit", "StatorCopperLosses",
          "LossModelOption", "Copper_Loss_Distribution"):
    try:
        c, v = cv(mc.GetVariable(k))
        log(f"  {k:<36} code={c} val={v}")
    except Exception:
        pass

log("\n=== 细扫 95~125℃ ===")
log(f"{'Ta':>6}{'Pcu':>12}{'相对95℃':>11}{'Ptot':>12}")
rows = []
base = None
for ta in SCAN:
    for v in ("HousingWJ_Inlet_Temperature", "Wj_Fluid_Inlet_Temperature"):
        try:
            mc.SetVariable(v, ta)
        except Exception:
            pass
    t0 = time.time()
    try:
        mc.DoMagneticThermalCalculation()
    except Exception as e:
        log(f"  {ta}: 失败 {type(e).__name__}")
        continue
    pcu = 0.0
    tot = 0.0
    for i in range(90):
        _, p = cv(mc.GetNodePower(i))
        if isinstance(p, (int, float)):
            tot += p
            if i in SLOT:
                pcu += p
    if base is None:
        base = pcu
    rows.append((ta, pcu, tot))
    log(f"{ta:>6}{pcu:>12.3f}{100*(pcu-base)/base:>10.2f}%{tot:>12.3f}  ({time.time()-t0:.0f}s)")

log("\n=== 判定 ===")
if len(rows) >= 2:
    jumps = [(rows[i][0], rows[i + 1][0], rows[i + 1][1] - rows[i][1])
             for i in range(len(rows) - 1)]
    big = [j for j in jumps if abs(j[2]) > 10]
    if big:
        for a, b, d in big:
            log(f"  跳变：{a}℃ → {b}℃   ΔPcu = {d:+.3f} W")
    else:
        log("  未发现 >10W 的突变，铜损随温度连续变化")

try:
    mc.Quit()
    log("\n已退出")
except Exception:
    pass
_f.close()
