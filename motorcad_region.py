# -*- coding: utf-8 -*-
"""
最后一次尝试：用 GetRegionLoss / GetRegionValue 读区域级损耗，
判断能否把「铜损」与「铁损」分开。若能，E 阶段的"同一份损耗喂两边"就成立。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"
OUT = Path(r"E:\deepseek\mdca\out")
LOG = OUT / "mcad_region.log"
_f = open(LOG, "w", encoding="utf-8")


def log(m=""):
    print(m, flush=True)
    _f.write(str(m) + "\n")
    _f.flush()


def cv(r):
    return r if isinstance(r, tuple) and len(r) == 2 else (None, r)


mc = pymotorcad.MotorCADCompatibility()
mc.LoadFromFile(MOT)
for v in ("HousingWJ_Inlet_Temperature", "Wj_Fluid_Inlet_Temperature"):
    try:
        mc.SetVariable(v, 65)
    except Exception:
        pass
t0 = time.time()
mc.DoMagneticThermalCalculation()
log(f"[计算完成 {time.time()-t0:.0f}s]\n")

REGIONS = ["Stator", "Rotor", "Winding", "Magnet", "Housing", "Shaft",
           "Stator_Winding", "Rotor_Cage", "End_Winding", "Slot"]
VALUES = ["Copper_Loss", "Iron_Loss", "Magnet_Loss", "Total_Loss",
          "Loss", "Temperature", "Mass", "Volume"]

log("=== GetRegionLoss 探测 ===")
hit = 0
for rg in REGIONS:
    for val in VALUES:
        for call in ("loss", "value"):
            fn = mc.GetRegionLoss if call == "loss" else mc.GetRegionValue
            for args in ((rg, val), (rg,), (val, rg)):
                try:
                    c, v = cv(fn(*args))
                except TypeError:
                    continue
                except Exception:
                    break
                if c == 0 and v is not None:
                    log(f"  OK  {call}({args}) -> {v}")
                    hit += 1
                    break
            else:
                continue
            break

log(f"\n命中 {hit} 个")

log("\n=== 其他可能给出损耗分离的变量 ===")
ALT = ["Pcu", "Pfe", "Peddy", "CopperLoss_Stator", "IronLoss_Stator",
       "IronLoss_Rotor", "MagnetEddyLoss", "StatorWindingLoss",
       "TotalCopperLoss", "TotalIronLoss", "Loss_Copper", "Loss_Iron",
       "Armature_Copper_Loss", "Stator_Iron_Loss", "Rotor_Iron_Loss",
       "Magnet_Iron_Loss", "Windage_Loss", "Friction_Loss"]
for a in ALT:
    try:
        c, v = cv(mc.GetVariable(a))
        if c == 0:
            log(f"  OK  {a:<28} = {v}")
    except Exception:
        continue

log("\n[DONE]")
try:
    mc.Quit()
    log("已退出")
except Exception:
    pass
_f.close()
