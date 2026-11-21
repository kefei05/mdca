# -*- coding: utf-8 -*-
"""探针 5：读损耗与温度变量，判断模型里是否已有计算结果。"""
import sys

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"

CAND = [
    # 损耗
    "StatorCopperLosses", "RotorCopperLosses", "StatorIronLosses",
    "RotorIronLosses", "MagnetLosses", "TotalLosses", "Loss_StatorCopper",
    # 温度
    "WindingTemperature", "StatorWindingTemperature", "StatorTemp",
    "WindingTemp", "RotorTemp", "MagnetTemp", "HousingTemp",
    # 工况
    "RPM", "Speed", "ShaftSpeed", "StatorCurrent", "CurrentDemand",
    # 环境/冷却
    "AmbientTemperature", "AirgapTemp", "CoolantTemperature", "HousingWJFluidInletTemp",
]

mcad = pymotorcad.MotorCADCompatibility()
mcad.LoadFromFile(MOT)
print("=== 探针 5：损耗 / 温度 / 工况 ===")
hits = []
for v in CAND:
    try:
        r = mcad.GetVariable(v)
        code, val = r if isinstance(r, tuple) and len(r) == 2 else (None, r)
        if code == 0:
            hits.append((v, val))
            print(f"    OK  {v:<30} = {val}")
        else:
            print(f"    --  {v:<30} code={code}")
    except Exception as e:
        print(f"    EXC {v:<30} {type(e).__name__}")

print(f"\n可用 {len(hits)}/{len(CAND)}")

# 节点温度（模型若已算过，应非零）
print("\n=== 节点温度 ===")
nonzero = 0
for i in range(1, 16):
    try:
        r = mcad.GetNodeTemperature(i)
        code, val = r if isinstance(r, tuple) and len(r) == 2 else (None, r)
        if code == 0 and val:
            nonzero += 1
            print(f"    Node {i:<3} = {val}")
    except Exception:
        break
print(f"    非零节点温度：{nonzero} 个" + ("（模型里已存结果）" if nonzero else "（需重新计算）"))
