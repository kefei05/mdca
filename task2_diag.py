# -*- coding: utf-8 -*-
"""诊断：为什么 DoMagneticThermalCalculation 只得到 49.5 W 且温度不随冷却液变化。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad  # noqa: E402

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"
OUT = Path(r"E:\deepseek\mdca\out")
_f = open(OUT / "task2_diag.log", "w", encoding="utf-8")


def log(m=""):
    print(m, flush=True)
    _f.write(str(m) + "\n")
    _f.flush()


def cv(r):
    return r if isinstance(r, tuple) and len(r) == 2 else (None, r)


def total_loss(mc):
    t = 0.0
    for i in range(90):
        _, p = cv(mc.GetNodePower(i))
        if isinstance(p, (int, float)):
            t += p
    return t


def tmax(mc):
    best, node = None, None
    for i in range(90):
        _, t = cv(mc.GetNodeTemperature(i))
        if isinstance(t, (int, float)) and (best is None or t > best):
            best, node = t, i
    return node, best


mc = pymotorcad.MotorCADCompatibility()
mc.LoadFromFile(MOT)
log("[加载完成]")

log("\n[1] 关键工况")
for v in ("ShaftSpeed", "HousingWJ_Inlet_Temperature", "InitialWindingTemperature",
          "Stator_Lam_Dia", "Slot_Number"):
    c, x = cv(mc.GetVariable(v))
    log(f"    {v:<32} code={c} val={x}")

log(f"\n[2] 加载后：总损耗 {total_loss(mc):.2f} W，"
    f"最高 node{tmax(mc)[0]} = {tmax(mc)[1]}")

log("\n[3] 显式 DoMagneticCalculation()")
t0 = time.time()
try:
    mc.DoMagneticCalculation()
    log(f"    完成 {time.time()-t0:.0f}s，总损耗 {total_loss(mc):.2f} W")
except Exception as e:
    log(f"    失败 {type(e).__name__}: {e}")

log("\n[4] 显式 CalculateThermal_Lab()")
t0 = time.time()
try:
    mc.CalculateThermal_Lab()
    n, t = tmax(mc)
    log(f"    完成 {time.time()-t0:.0f}s，最高 node{n} = {t}℃，总损耗 {total_loss(mc):.2f} W")
except Exception as e:
    log(f"    失败 {type(e).__name__}: {e}")

log("\n[5] 改冷却液到 85℃ 再算热")
for v in ("HousingWJ_Inlet_Temperature", "Wj_Fluid_Inlet_Temperature"):
    try:
        mc.SetVariable(v, 85)
    except Exception:
        pass
c, x = cv(mc.GetVariable("HousingWJ_Inlet_Temperature"))
log(f"    设置后读回：code={c} val={x}")
t0 = time.time()
try:
    mc.CalculateThermal_Lab()
    n, t = tmax(mc)
    log(f"    完成 {time.time()-t0:.0f}s，最高 node{n} = {t}℃")
except Exception as e:
    log(f"    失败 {type(e).__name__}: {e}")

log("\n[6] 尝试 DoMagneticThermalCalculation()")
t0 = time.time()
try:
    mc.DoMagneticThermalCalculation()
    n, t = tmax(mc)
    log(f"    完成 {time.time()-t0:.0f}s，最高 node{n} = {t}℃，总损耗 {total_loss(mc):.2f} W")
except Exception as e:
    log(f"    失败 {type(e).__name__}: {e}")

log("\n[9] Quit")
try:
    mc.Quit()
    log("    已退出")
except Exception:
    pass
_f.close()
