# -*- coding: utf-8 -*-
"""
Motor-CAD 电磁+热一次算全（路 B）
==================================
DoMagneticThermalCalculation() 之后，逐节点读「功率 + 温度」，
这样就能从节点功率反推绕组铜损 / 铁芯铁损 / 磁钢涡流，与 MDCA 的口径对应。

产物：
  out/mcad_full.log            全流程日志
  out/mcad_nodes.csv           节点编号, 功率(W), 温度(℃)
  out/EV5_full_calculated.mot  算完的模型（可复用，避免重算）
"""
import csv
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"
OUT = Path(__file__).resolve().parent.parent / "out"
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "mcad_full.log"
_f = open(LOG, "w", encoding="utf-8")


def log(msg=""):
    print(msg, flush=True)
    _f.write(str(msg) + "\n")
    _f.flush()


def cv(res):
    if isinstance(res, tuple) and len(res) == 2:
        return res
    return (None, res)


log("=" * 70)
log("Motor-CAD 电磁+热（路 B）")
log("=" * 70)

mc = pymotorcad.MotorCADCompatibility()
log("[1] 已连接")
mc.LoadFromFile(MOT)
log("[2] 已加载 EV_5_Thermal.mot")

log("\n[3] 计算前工况：")
for v in ("ShaftSpeed", "InitialWindingTemperature", "AmbientTemperatureSetting",
          "Stator_Lam_Dia", "Stator_Lam_Length", "Slot_Number"):
    try:
        c, val = cv(mc.GetVariable(v))
        log(f"    {v:<30} code={c} val={val}")
    except Exception:
        pass

log("\n[4] DoMagneticThermalCalculation() 开始 ...")
t0 = time.time()
ok = False
try:
    mc.DoMagneticThermalCalculation()
    ok = True
    log(f"    完成，耗时 {time.time()-t0:.0f}s")
except Exception as e:
    log(f"    失败（{time.time()-t0:.0f}s）：{type(e).__name__}: {e}")
    traceback.print_exc(file=_f)

if not ok:
    log("\n尝试退化为只算热 ...")
    t0 = time.time()
    try:
        mc.CalculateThermal_Lab()
        log(f"    热计算完成，耗时 {time.time()-t0:.0f}s")
    except Exception as e:
        log(f"    也失败: {type(e).__name__}: {e}")

# 逐节点读功率与温度
log("\n[5] 节点功率 + 温度")
rows = []
for i in range(0, 90):
    try:
        _, pw = cv(mc.GetNodePower(i))
        _, tp = cv(mc.GetNodeTemperature(i))
    except Exception:
        break
    if isinstance(pw, (int, float)) or isinstance(tp, (int, float)):
        rows.append((i, pw, tp))

for i, pw, tp in rows:
    log(f"    node {i:<3} P={pw!s:<22} T={tp}")

csv_path = OUT / "mcad_nodes.csv"
with open(csv_path, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["node", "power_W", "temp_C"])
    for i, pw, tp in rows:
        w.writerow([i, pw, tp])
log(f"\n    已写 {csv_path}（{len(rows)} 行）")

# 损耗变量（各种可能名）
log("\n[6] 损耗变量尝试")
LOSS_NAMES = ["StatorCopperLosses", "RotorCopperLosses", "StatorIronLosses",
              "RotorIronLosses", "MagnetLosses", "TotalLosses",
              "WindingLoss", "CopperLoss", "IronLoss", "EddyLoss",
              "StatorWindingLoss", "Efficiency", "Torque", "Torque_Magnetic"]
for n in LOSS_NAMES:
    try:
        c, val = cv(mc.GetVariable(n))
        if c == 0:
            log(f"    OK  {n:<24} = {val}")
    except Exception:
        continue

# 存盘
log("\n[7] 存盘")
try:
    sp = str(OUT / "EV5_full_calculated.mot")
    mc.SaveToFile(sp)
    log(f"    -> {sp}")
except Exception as e:
    log(f"    失败: {type(e).__name__}: {e}")

log("\n[8] Quit")
try:
    mc.Quit()
    log("    已退出")
except Exception as e:
    log(f"    Quit 失败: {type(e).__name__}")

log("\n[DONE]")
_f.close()
