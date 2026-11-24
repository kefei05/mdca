# -*- coding: utf-8 -*-
"""
暴力试节点名：用 <Name>_Temperature 的形式扫一批 Motor-CAD 常见节点名。
命中即可建立「节点名 ↔ 温度」的映射，绕开 GetNodeTemperature 的编号问题。
"""
import sys
from pathlib import Path

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"
OUT = Path(__file__).resolve().parent.parent / "out"
LOG = OUT / "mcad_nodenames.log"
_f = open(LOG, "w", encoding="utf-8")


def log(msg=""):
    print(msg, flush=True)
    _f.write(str(msg) + "\n")
    _f.flush()


def cv(res):
    if isinstance(res, tuple) and len(res) == 2:
        return res
    return (None, res)


# 来自 OutputSheets 的 48 个图名 + 常见部件名
NAMES = [
    "Housing", "Housing_F", "Housing_R", "Housing_OH_F", "Housing_OH_R",
    "HousingWJ_A", "HousingWJ_B", "Endcap_F", "Endcap_R",
    "Shaft_F", "Shaft_R", "Shaft",
    "Stator_Yoke", "Stator_Tooth", "Stator_Surface", "Stator_Winding",
    "Winding", "Winding_F", "Winding_R", "Winding_Slot", "Winding_EWdg",
    "Rotor", "Rotor_ES_F", "Rotor_ES_R", "Rotor_Yoke", "Rotor_BackIron",
    "Magnet", "Magnet_F", "Magnet_R", "Magnet_N", "Magnet_S",
    "Bearing_Front", "Bearing_Rear", "Bearing_F", "Bearing_R",
    "Tooth(C1)", "Tooth(C2)", "Tooth(B1)", "Tooth(B2)",
    "Slot(C1)", "Slot(C2)", "Slot(A1)",
    "Airgap", "Airgap_F", "Airgap_R",
    "Coolant", "Fluid", "Ambient",
    "Interface_Gap_F", "Interface_Gap_R", "Contact_Resistance",
    "Stator", "Rotor_Lam", "Frame",
]

SUFFIXES = ["_Temperature", " Temperature", "_Temp", " Temperature [C]"]

mc = pymotorcad.MotorCADCompatibility()
log("[1] 已连接")
mc.LoadFromFile(MOT)
log("[2] 已加载\n")

log("[3] 逐名试读：")
hits = {}
for n in NAMES:
    for suf in SUFFIXES:
        try:
            c, v = cv(mc.GetVariable(n + suf))
        except Exception:
            continue
        if c == 0:
            hits[n + suf] = v
            log(f"    OK  {(n + suf):<34} = {v}")
            break

log(f"\n[4] 命中 {len(hits)} 个")

# 同时试「无后缀」的部件温度变量
log("\n[5] 试无后缀 + 常见量名：")
ALT = ["WindingTemp", "StatorTemp", "RotorTemp", "MagnetTemp", "HousingTemp",
       "StatorWindingTemperature", "WindingTemperature", "RotorTemperature",
       "MagnetTemperature", "HousingTemperature", "EndcapTemperature",
       "StatorCopperLosses", "RotorCopperLosses", "StatorIronLosses",
       "RotorIronLosses", "MagnetLosses", "TotalLosses", "Efficiency"]
for a in ALT:
    try:
        c, v = cv(mc.GetVariable(a))
        if c == 0:
            log(f"    OK  {a:<30} = {v}")
    except Exception:
        continue

log("\n[DONE]")
try:
    mc.Quit()
    log("已退出")
except Exception:
    pass
_f.close()
