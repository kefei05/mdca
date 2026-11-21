# -*- coding: utf-8 -*-
"""
Motor-CAD 温度读取方式探测（不跑计算，秒级）。
目标：找到能返回有意义值的节点/温度读取入口，避免"算了 11 分钟却读不到"。
脚本自带 Quit，不会留下僵尸实例。
"""
import sys
import traceback

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as pymotorcad

MOT = r"<MOTORCAD_ROOT>\Tutorials\BPM_Modelling\EV_5_Thermal.mot"


def cv(res):
    """Motor-CAD 兼容层返回 (code, value)。"""
    if isinstance(res, tuple) and len(res) == 2:
        return res
    return (None, res)


mc = pymotorcad.MotorCADCompatibility()
print("[1] 已连接")
try:
    mc.LoadFromFile(MOT)
    print("[2] 模型已加载")
except Exception:
    print("[2] 加载失败")
    traceback.print_exc()

try:
    # --- A. 节点存在性：编号体系从 0 还是 1 开始、共几个 ---
    valid = []
    for i in range(0, 80):
        try:
            c, v = cv(mc.GetNodeExists(i))
        except Exception:
            break
        if c == 0 and v:
            valid.append(i)
    print(f"\n[A] GetNodeExists 有效节点 {len(valid)} 个")
    if valid:
        print(f"    编号范围 {valid[0]}..{valid[-1]}，前 20 个：{valid[:20]}")

    # --- B. 节点温度：返回 code=0 的才算接口可用 ---
    got = []
    for i in range(0, 80):
        try:
            c, v = cv(mc.GetNodeTemperature(i))
        except Exception:
            break
        if c == 0:
            got.append((i, v))
    print(f"\n[B] GetNodeTemperature 返回 code=0 的节点 {len(got)} 个")
    for i, v in got[:10]:
        print(f"    node {i:<3} = {v}")

    # --- C. 轴向温度图：OutputSheets 里 48 个图的读法 ---
    print("\n[C] GetTemperatureGraphPoint 尝试")
    try:
        for gi in range(0, 3):
            row = []
            for pi in range(0, 3):
                c, v = cv(mc.GetTemperatureGraphPoint(gi, pi))
                row.append(f"({c},{v})")
            print(f"    graph {gi}: " + " ".join(row))
    except Exception as e:
        print(f"    失败: {type(e).__name__}: {e}")

    # --- D. 用节点名去读变量 ---
    print("\n[D] 按节点名读变量")
    for name in ("Housing", "Stator_Yoke", "Tooth(C1)", "Stator_Surface",
                 "HousingWJ_A", "Endcap_F"):
        for suffix in (" Temp", "_Temp", "Temperature"):
            try:
                c, v = cv(mc.GetVariable(name + suffix))
                if c == 0:
                    print(f"    OK  {name + suffix:<28} = {v}")
                    break
            except Exception:
                continue
        else:
            print(f"    --  {name:<28} 三种后缀均未命中")

finally:
    try:
        mc.Quit()
        print("\n[9] 已发送 Quit，实例清理完毕")
    except Exception as e:
        print(f"\n[9] Quit 失败: {type(e).__name__}: {e}")
