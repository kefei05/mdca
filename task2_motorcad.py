# -*- coding: utf-8 -*-
"""
任务 2 第二步：用 Motor-CAD 实算候选，与 MDCA 的判定比对。

路径纪律：本文件不写死任何本机路径，由环境变量 MOTORCAD_ROOT 指定。
         加载后**必须校验模型指纹**（槽数/定子外径），否则 LoadFromFile 静默失败
         会让后续计算跑在默认模型上（已踩过这个坑）。

复核对象（分两组，不合并）：
  A 组（预注册）  Ta 45~110 内「送外部」档 —— 实测为空，如实记录
  A' 组（扩展）   Ta 20~35 补出的「送外部」档候选，用于完成漏报率测量
  B 组            「人工复核」档中裕度最小的几个

损耗纪律：Motor-CAD 无法分离铜损与铁损（阶段 E 已确认），
故**只改冷却液温度，损耗保持原样**，这样两边喂的是同一份损耗。

温度定义：hotspot —— Motor-CAD 取全场最高节点温度。
"""
import csv
import os
import sys
import time
from pathlib import Path

ROOT = os.environ.get("MOTORCAD_ROOT", "")
if not ROOT or not Path(ROOT).is_dir():
    raise SystemExit(
        "未设置 MOTORCAD_ROOT。请先执行（PowerShell）：\n"
        '  $env:MOTORCAD_ROOT = "<你的 Motor-CAD 安装目录>"\n'
        "该目录下应含 Python/ 与 Tutorials/ 两个子目录。")

sys.path.insert(0, os.path.join(ROOT, "Python", "Python", "Lib", "site-packages"))
import ansys.motorcad.core as pymotorcad  # noqa: E402

MOT = os.path.join(ROOT, "Tutorials", "BPM_Modelling", "EV_5_Thermal.mot")
HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
LOG = OUT / "task2_motorcad.log"
_f = open(LOG, "w", encoding="utf-8")

# 模型指纹：EV_5 应为 48 槽 / 定子外径 198 mm
EXPECT_SLOT = 48
EXPECT_DIA = 198.0

TA_LIST = [20, 25, 30, 35, 45, 50, 55]


def log(m=""):
    print(m, flush=True)
    _f.write(str(m) + "\n")
    _f.flush()


def cv(r):
    return r if isinstance(r, tuple) and len(r) == 2 else (None, r)


log(f"[0] MOTORCAD_ROOT = {ROOT}")
if not Path(MOT).is_file():
    raise SystemExit(f"模型文件不存在：{MOT}")

mc = pymotorcad.MotorCADCompatibility()
log("[1] 已连接")
mc.LoadFromFile(MOT)
log("[2] 已加载 EV_5_Thermal.mot")

# ---- 模型指纹校验（关键：LoadFromFile 失败不抛异常）----
slt = cv(mc.GetVariable("Slot_Number"))[1]
dia = cv(mc.GetVariable("Stator_Lam_Dia"))[1]
log(f"[3] 模型指纹：Slot_Number={slt}（应 {EXPECT_SLOT}），"
    f"Stator_Lam_Dia={dia}（应 {EXPECT_DIA}）")
if slt != EXPECT_SLOT or abs(float(dia) - EXPECT_DIA) > 1e-6:
    raise SystemExit("模型指纹不符 —— LoadFromFile 可能静默失败，拒绝在错误模型上出数据")

rows = []
for ta in TA_LIST:
    for v in ("HousingWJ_Inlet_Temperature", "Wj_Fluid_Inlet_Temperature"):
        try:
            mc.SetVariable(v, ta)
        except Exception:
            pass
    back = cv(mc.GetVariable("HousingWJ_Inlet_Temperature"))[1]
    t0 = time.time()
    try:
        mc.DoMagneticThermalCalculation()
    except Exception as e:
        log(f"[{ta}℃] 计算失败 {type(e).__name__}: {e}")
        continue

    temps, powers = {}, {}
    for i in range(90):
        _, tt = cv(mc.GetNodeTemperature(i))
        _, pp = cv(mc.GetNodePower(i))
        temps[i] = tt if isinstance(tt, (int, float)) else None
        powers[i] = pp if isinstance(pp, (int, float)) else None

    valid = {i: t for i, t in temps.items() if t is not None}
    hot_node = max(valid, key=valid.get)
    hot = valid[hot_node]
    total = sum(p for p in powers.values() if isinstance(p, (int, float)))
    rows.append({"Ta": ta, "set_back": back, "Tmax": round(hot, 3), "hot_node": hot_node,
                 "total_loss_W": round(total, 3), "seconds": round(time.time() - t0, 1)})
    log(f"[{ta:>3}℃] 回读={back}  {time.time()-t0:4.0f}s  最高 node{hot_node} = "
        f"{hot:8.3f}℃  总损耗 {total:8.2f} W")

log("\n[9] Quit")
try:
    mc.Quit()
    log("    已退出")
except Exception as e:
    log(f"    失败: {type(e).__name__}")

p = OUT / "task2_motorcad.csv"
with open(p, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["Ta", "set_back", "Tmax", "hot_node",
                                       "total_loss_W", "seconds"])
    w.writeheader()
    w.writerows(rows)
log(f"\n已写 {p}")
_f.close()
