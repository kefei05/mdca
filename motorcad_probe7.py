# -*- coding: utf-8 -*-
"""探针 7：打印温度/节点相关 API 的签名（不加载模型，秒级返回）。"""
import sys

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as m

mc = m.MotorCADCompatibility()

NAMES = [
    "GetTemperatureGraphPoint", "GetNodeTemperature", "GetNodeExists",
    "GetNodePower", "GetNodeToNodeResistance", "GetNodeCapacitance",
    "CalculateThermal_Lab", "GetRegionLoss", "GetRegionValue",
    "GetArrayVariable", "GetOffsetNodeNumber",
]
for name in NAMES:
    fn = getattr(mc, name, None)
    if fn is None:
        print(f"{name}: 不存在")
        continue
    doc = (fn.__doc__ or "").strip().replace("\n", " ")[:200]
    print(f"{name}:")
    print(f"    {doc if doc else '(无 docstring)'}")

# 再列一遍所有含 Temp/Node 的方法，确认没有漏
print("\n=== 所有含 Temp / Node / Power 的公开方法 ===")
for n in dir(mc):
    if n.startswith("_"):
        continue
    if any(k in n for k in ("Temp", "Node", "Power", "Loss")):
        print("   ", n)
