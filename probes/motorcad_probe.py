# -*- coding: utf-8 -*-
"""Motor-CAD 连接探针：确认 PyMotorCAD 能否启动并连上本机 Motor-CAD。"""
import sys
import traceback

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")

print("=== Motor-CAD 连接探针 ===")
try:
    import ansys.motorcad.core as pymotorcad
    print("[1] import ansys.motorcad.core  OK")
    print("    版本:", getattr(pymotorcad, "__version__", "?"))
except Exception:
    print("[1] import 失败")
    traceback.print_exc()
    raise SystemExit(1)

try:
    mcad = pymotorcad.MotorCADCompatibility()
    print("[2] MotorCADCompatibility()  OK —— 已连接")
except Exception:
    print("[2] 连接失败")
    traceback.print_exc()
    raise SystemExit(2)

try:
    for var in ("Motor-CAD Version", "Machine Type", "Drive Type",
                "Stator Lam Slot Number", "Rotor Pole Number"):
        try:
            print(f"     {var:<26} = {mcad.get_variable(var)}")
        except Exception as e:
            print(f"     {var:<26} = <读不到: {type(e).__name__}>")
except Exception:
    traceback.print_exc()

print("[3] 完成")
