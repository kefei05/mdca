# -*- coding: utf-8 -*-
"""探针 3：加载教程热模型并读取关键变量，确认数据链路。"""
import sys
import traceback

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"

print("=== 探针 3：加载模型 + 读变量 ===")
mcad = pymotorcad.MotorCADCompatibility()

print("[1] 加载模型 ...")
try:
    mcad.LoadFromFile(MOT)
    print("    OK")
except Exception:
    print("    失败")
    traceback.print_exc()

# 尝试一堆常见变量名，能读到哪个算哪个
CANDIDATES = [
    "Stator Lam Slot Number", "Rotor Pole Number",
    "Stator Lam Diameter", "Stator Lam Bore", "Stator Lam Thickness",
    "Airgap", "Air Gap",
    "Stator Winding Coil Pitch",
    "Machine Type", "Motor-CAD Version",
    "Rated Power", "Rated Speed", "Rated Torque", "Rated Current",
]
print("\n[2] 读变量：")
ok = []
for v in CANDIDATES:
    try:
        val = mcad.GetVariable(v)
        ok.append((v, val))
        print(f"    OK  {v:<28} = {val}")
    except Exception as e:
        print(f"    --  {v:<28} <{type(e).__name__}>")

# 有效节点与节点温度
print("\n[3] 热网络节点：")
try:
    n = mcad.GetNodeCount() if hasattr(mcad, "GetNodeCount") else None
    print("    GetNodeCount ->", n)
except Exception as e:
    print("    GetNodeCount 不可用:", type(e).__name__)

for i in range(1, 12):
    try:
        t = mcad.GetNodeTemperature(i)
        print(f"    Node {i:<3} T = {t}")
    except Exception as e:
        if i == 1:
            print(f"    Node 读取失败: {type(e).__name__}: {e}")
        break

print(f"\n[4] 成功读到 {len(ok)} 个变量")
