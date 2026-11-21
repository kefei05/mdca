# -*- coding: utf-8 -*-
"""列出 MotorCADCompatibility 对象的可用方法，定位读/写变量的正确入口。"""
import sys
import traceback

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

print("=== 探针 2：API 方法清单 ===")
mcad = pymotorcad.MotorCADCompatibility()
print("对象类型:", type(mcad).__name__)

names = [n for n in dir(mcad) if not n.startswith("_")]
print(f"公开方法/属性共 {len(names)} 个")

want = ("variable", "value", "open", "load", "save", "calc", "solve",
        "thermal", "temperature", "run", "get", "set")
print("\n--- 与读写/计算相关的 ---")
for n in names:
    if any(w in n.lower() for w in want):
        print("  ", n)

print("\n--- 尝试常见读法 ---")
for meth in ("get_variable", "get_motorcad_variable", "get_value"):
    fn = getattr(mcad, meth, None)
    if fn is None:
        print(f"  {meth}: 不存在")
        continue
    try:
        v = fn("Stator Lam Slot Number")
        print(f"  {meth}: OK -> {v}")
    except Exception as e:
        print(f"  {meth}: {type(e).__name__}: {e}")
