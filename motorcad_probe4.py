# -*- coding: utf-8 -*-
"""探针 4：按 .mot 里的内部变量名读取，确认 GetVariable 的返回约定。"""
import sys
import traceback

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"

VARS = ["Stator_Lam_Dia", "Stator_Lam_Length", "Airgap", "Slot_Number",
        "Slot_Depth", "Tooth_Width", "Housing_Dia", "Shaft_Dia"]

print("=== 探针 4：按内部名读变量 ===")
mcad = pymotorcad.MotorCADCompatibility()
try:
    mcad.LoadFromFile(MOT)
    print("[1] 模型已加载")
except Exception:
    print("[1] 加载失败")
    traceback.print_exc()

print("\n[2] GetVariable 返回：")
good = 0
for v in VARS:
    try:
        r = mcad.GetVariable(v)
        code, val = (r if isinstance(r, tuple) and len(r) == 2 else (None, r))
        flag = "OK " if code == 0 else "ERR"
        if code == 0:
            good += 1
        print(f"    {flag} {v:<22} code={code:<4} value={val}")
    except Exception as e:
        print(f"    EXC {v:<22} {type(e).__name__}: {e}")

print(f"\n[3] 成功 {good}/{len(VARS)}")
