# -*- coding: utf-8 -*-
"""探针 6：试跑一次热计算，确认能否产出温度。"""
import sys
import time
import traceback

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"

mcad = pymotorcad.MotorCADCompatibility()
mcad.LoadFromFile(MOT)
print("模型已加载")

print("\n尝试 CalculateThermal_Lab() ...")
t0 = time.time()
try:
    mcad.CalculateThermal_Lab()
    print(f"  完成，耗时 {time.time()-t0:.1f}s")
except Exception as e:
    print(f"  失败（{time.time()-t0:.1f}s）：{type(e).__name__}: {e}")

print("\n读节点温度：")
nz = 0
for i in range(1, 21):
    try:
        r = mcad.GetNodeTemperature(i)
        code, val = r if isinstance(r, tuple) and len(r) == 2 else (None, r)
        if code == 0:
            if val:
                nz += 1
            print(f"  Node {i:<3} code={code} val={val}")
    except Exception as e:
        print(f"  Node {i} 异常: {type(e).__name__}")
        break
print(f"\n非零节点：{nz}")

print("\n从 .mot 挖出的候选结果变量名（若 .mot 内有 Results 段）：")
import re
from pathlib import Path
txt = Path(MOT).read_text(encoding="utf-8", errors="replace")
for sec in ("Results", "Output", "Node_Temperatures", "Thermal_Results"):
    if f"[{sec}]" in txt:
        print(f"  存在 section [{sec}]")
