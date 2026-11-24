# -*- coding: utf-8 -*-
"""探针 8：只测连接，不做加载/计算。判断残留实例是否还可用。"""
import sys
import time

sys.path.insert(0, r"<MOTORCAD_ROOT>\Python\Python\Lib\site-packages")
import ansys.motorcad.core as m

for attempt in (1, 2):
    print(f"--- 第 {attempt} 次尝试 ---")
    t0 = time.time()
    try:
        mc = m.MotorCADCompatibility()
        print(f"    连接成功，{time.time()-t0:.1f}s")
        # 立刻验证一次通信
        try:
            r = mc.GetVariable("Airgap")
            print(f"    GetVariable('Airgap') -> {r}")
        except Exception as e:
            print(f"    通信失败: {type(e).__name__}: {e}")
        try:
            mc.Quit()
            print("    已发送 Quit")
        except Exception as e:
            print(f"    Quit 失败: {type(e).__name__}")
        break
    except Exception as e:
        print(f"    失败，{time.time()-t0:.1f}s：{type(e).__name__}: {e}")
        if attempt == 1:
            print("    等 10 秒重试...")
            time.sleep(10)
