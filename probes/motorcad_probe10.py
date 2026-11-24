# -*- coding: utf-8 -*-
"""探针 10：查损耗读取 API 的签名（不加载模型，秒级）。"""
import sys
import inspect

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as m

mc = m.MotorCADCompatibility()

NAMES = [
    "DoMagneticThermalCalculation", "DoMagneticCalculation",
    "GetRegionLoss", "GetRegionValue", "GetArrayVariable",
    "CalculateIronLossCoefficients", "GetWindingCoil",
]
print("=== 方法签名 ===")
for name in NAMES:
    fn = getattr(mc, name, None)
    if fn is None:
        print(f"{name}: 不存在")
        continue
    try:
        sig = str(inspect.signature(fn))
    except (ValueError, TypeError):
        sig = "(签名不可读)"
    doc = (fn.__doc__ or "").strip().replace("\n", " ")[:120]
    print(f"{name}{sig}")
    if doc:
        print(f"      {doc}")

print("\n=== 所有含 Loss / Region / Power 的方法 ===")
for n in dir(mc):
    if n.startswith("_"):
        continue
    if any(k in n for k in ("Loss", "Region", "Power", "Iron")):
        print("   ", n)

try:
    mc.Quit()
    print("\n已退出")
except Exception:
    pass
