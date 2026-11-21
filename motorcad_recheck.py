# -*- coding: utf-8 -*-
"""
异常点复核：45℃ 在扫描中给出比 65℃ 更高的温度（物理上不可能）。
怀疑是「首次计算」的初始条件影响。做法：先算 65℃ 让模型稳定，再算 45℃。

同时复核 125℃：铜损从 571 跳到 708（+23.9%），与 105℃ 的 +0.89% 不连续。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"
OUT = Path(r"E:\deepseek\mdca\out")
LOG = OUT / "mcad_recheck.log"
_f = open(LOG, "w", encoding="utf-8")


def log(m=""):
    print(m, flush=True)
    _f.write(str(m) + "\n")
    _f.flush()


def cv(r):
    return r if isinstance(r, tuple) and len(r) == 2 else (None, r)


COLD, SLOT, HOT, MAG = [1, 2, 3, 4, 6, 7, 8], [9, 65, 66], [11, 12, 13, 15, 16, 18, 23, 24], [62]

mc = pymotorcad.MotorCADCompatibility()
mc.LoadFromFile(MOT)
log("[加载完成]")


def run(ta):
    for v in ("HousingWJ_Inlet_Temperature", "Wj_Fluid_Inlet_Temperature"):
        try:
            mc.SetVariable(v, ta)
        except Exception:
            pass
    t0 = time.time()
    mc.DoMagneticThermalCalculation()
    temps, powers = {}, {}
    for i in range(90):
        _, t = cv(mc.GetNodeTemperature(i))
        _, p = cv(mc.GetNodePower(i))
        temps[i] = t if isinstance(t, (int, float)) else None
        powers[i] = p if isinstance(p, (int, float)) else None

    def avg(ns):
        vs = [temps[n] for n in ns if temps.get(n) is not None]
        return sum(vs) / len(vs) if vs else float("nan")

    def tot(ns):
        return sum(powers[n] for n in ns if isinstance(powers.get(n), (int, float)))

    r = {"Ta": ta, "Ts": avg(COLD), "Tw": avg(HOT), "Tr": avg(MAG),
         "Pcu": tot(SLOT), "Ptot": sum(p for p in powers.values() if isinstance(p, (int, float)))}
    log(f"  Ta={ta:>4}  {time.time()-t0:3.0f}s  Ts={r['Ts']:7.2f}  Tw={r['Tw']:7.2f}"
        f"  Tr={r['Tr']:7.2f}  Pcu={r['Pcu']:7.2f}  Ptot={r['Ptot']:7.2f}")
    return r


log("\n[1] 先跑 65℃ 稳定模型")
run(65)
log("\n[2] 再跑 45℃（若恢复正常，说明原扫描的 45℃ 受首次计算影响）")
r45 = run(45)
log("\n[3] 回到 65℃ 复核一致性")
r65 = run(65)
log("\n[4] 105℃ 复核")
r105 = run(105)
log("\n[5] 125℃ 复核（看铜损是否仍跳变）")
r125 = run(125)

log("\n=== 复核结论 ===")
log(f"  45℃:  Ts={r45['Ts']:.2f}  Tw={r45['Tw']:.2f}  （原扫描：Ts=96.82 Tw=160.43）")
log(f"  65℃:  Ts={r65['Ts']:.2f}  Tw={r65['Tw']:.2f}  （原扫描：Ts=92.34 Tw=155.50）")
log(f" 105℃:  Pcu={r105['Pcu']:.2f}  （原扫描：576.59）")
log(f" 125℃:  Pcu={r125['Pcu']:.2f}  （原扫描：708.10）")

try:
    mc.Quit()
    log("\n已退出")
except Exception:
    pass
_f.close()
