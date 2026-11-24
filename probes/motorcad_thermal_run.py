# -*- coding: utf-8 -*-
"""
Motor-CAD 热计算 + 全方法温度探测（一次跑完，避免反复等 11 分钟）。

流程：连接 → 加载 → 记工况 → 计算 → 用所有已知入口读温度 → 存盘 → Quit
日志同时写 out/motorcad_thermal_probe.log，后台跑也能取结果。
"""
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"
OUT = Path(__file__).resolve().parent.parent / "out"
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "motorcad_thermal_probe.log"
_logf = open(LOG, "w", encoding="utf-8")


def log(msg=""):
    print(msg, flush=True)
    _logf.write(str(msg) + "\n")
    _logf.flush()


def cv2(res):
    if isinstance(res, tuple) and len(res) == 2:
        return res
    return (None, res)


def cv3(res):
    """三元组 (code, x, y)"""
    if isinstance(res, tuple) and len(res) == 3:
        return res
    if isinstance(res, tuple) and len(res) == 2:
        return (res[0], res[1], None)
    return (None, res, None)


log("=" * 70)
log("Motor-CAD 热计算 + 全方法探测")
log("=" * 70)

mc = pymotorcad.MotorCADCompatibility()
log("[1] 已连接")
mc.LoadFromFile(MOT)
log("[2] 模型已加载")

# 记录工况
log("\n[3] 工况：")
for v in ("ShaftSpeed", "Stator_Lam_Dia", "Stator_Lam_Length", "Airgap",
          "Slot_Number", "InitialWindingTemperature", "AmbientTemperatureSetting"):
    try:
        c, val = cv2(mc.GetVariable(v))
        log(f"    {v:<30} code={c} val={val}")
    except Exception:
        pass

# 计算
log("\n[4] CalculateThermal_Lab() 开始 ...")
t0 = time.time()
calc_ok = False
try:
    mc.CalculateThermal_Lab()
    calc_ok = True
    log(f"    完成，耗时 {time.time() - t0:.0f}s")
except Exception as e:
    log(f"    失败（{time.time() - t0:.0f}s）：{type(e).__name__}: {e}")
    traceback.print_exc(file=_logf)

# 读温度：方法 1 节点
log("\n[5] 方法1 GetNodeTemperature")
found = []
for i in range(0, 90):
    try:
        c, val = cv2(mc.GetNodeTemperature(i))
    except Exception:
        break
    if c == 0 and isinstance(val, (int, float)) and val not in (0, -1):
        found.append((i, val))
        log(f"    node {i:<3} = {val}")
log(f"    非零/非负一 节点数：{len(found)}")

# 读温度：方法 2 轴向图（三元组）
log("\n[6] 方法2 GetTemperatureGraphPoint")
gfound = 0
try:
    for gi in range(0, 6):
        for pi in range(0, 4):
            c, x, y = cv3(mc.GetTemperatureGraphPoint(gi, pi))
            if c == 0:
                gfound += 1
                log(f"    graph[{gi}] point[{pi}] -> code={c} x={x} y={y}")
except Exception as e:
    log(f"    异常: {type(e).__name__}: {e}")
log(f"    code=0 的点：{gfound}")

# 读温度：方法 3 变量名（含 OutputSheets 里的节点名）
log("\n[7] 方法3 按节点名读变量")
NODES = ["Housing", "Housing_OH_F", "Endcap_F", "Shaft_F", "Stator_Yoke",
         "HousingWJ_A", "Tooth(C1)", "Stator_Surface", "Rotor_ES_F", "Bearing_Front"]
SUFFIX = [" Temp", "_Temp", " Temperature", "", "_Temperature"]
hit = 0
for n in NODES:
    for s in SUFFIX:
        try:
            c, val = cv2(mc.GetVariable(n + s))
            if c == 0:
                hit += 1
                log(f"    OK  {n + s:<32} = {val}")
                break
        except Exception:
            continue
log(f"    变量命中：{hit}/{len(NODES)}")

# 存盘（便于复用，避免再算一次）
log("\n[8] 存盘")
try:
    SP = str(OUT / "EV5_thermal_calculated.mot")
    mc.SaveToFile(SP)
    log(f"    已保存 -> {SP}")
except Exception as e:
    log(f"    保存失败: {type(e).__name__}: {e}")

log("\n[9] Quit")
try:
    mc.Quit()
    log("    已退出")
except Exception as e:
    log(f"    Quit 失败: {type(e).__name__}")

log("\n[DONE]")
_logf.close()
