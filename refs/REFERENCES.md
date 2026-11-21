# 引用的文献与数据来源

> **本仓库不包含论文原文**。PDF 与提取文本已排除在版本控制之外（见 `.gitignore`）。
> 本文件只记录：引用信息、本项目用到的具体位置、以及在 `../verification_log.md` 中的核实条目号。

---

## 1. Chalmers University of Technology (2005)

```text
标题    Thermal Modelling of Electrical Machines（硕士论文，报告编号 2005:22）
机构    Chalmers University of Technology, Göteborg, Sweden
获取    机构库公开 PDF（publications.lib.chalmers.se）
用途    部件级热阻的典型值（Table 17）
```

| 用到的内容 | 位置 | 核实条目 |
|---|---|---|
| Slot → Tooth 190 mK/W | Table 17 | C-01 |
| Slot → Yoke 290 mK/W | Table 17 | C-02 |
| Tooth → Yoke 16 mK/W | Table 17 | C-03 |
| Yoke → Frame 3.2 mK/W | Table 17 | C-04 |
| Frame → Coolant 0.14 mK/W | Table 17 | C-05 |

**派生用途**：`Rws 占比 90.8%` 由上述前四条串联/并联折算得出（`verification_log.md` P-01，**C 档：推算值，非论文结论**）。

---

## 2. Energies 2021

```text
标题    Stator Winding Second-Order Thermal Model including End-Winding Thermal Effects
期刊    Energies, 2021, 14(20), 6578
DOI     10.3390/en14206578
许可    CC-BY（可在引用后转载，但本仓库仍不收录全文）
用途    4 kW TEFC 感应电机的热模型参数与实测校验数据
```

| 用到的内容 | 位置 | 核实条目 |
|---|---|---|
| 4 kW / 400 V / 8.8 A / 50 Hz / 4 极 / 1410 rpm | Table 1 | E-01 ~ E-06 |
| Req,w = 0.07 K/W | Table 2 | E-07 |
| Req,sr = 0.382 K/W | Table 2 | E-08 |
| Rfc = 0.167 K/W（提出模型列） | Table 2 | E-09（**符号与数值在 PDF 中分离，经人工对齐列序**） |
| Rew,a = 0.446 K/W | Table 2 | E-10 |
| 50% 转矩稳态误差 0.44℃ / 0.67% | 正文 §3 | E-13 |
| 100% 转矩稳态误差 2.15℃ / 1.81% | 正文 §3 | E-14 |

**派生用途**：`Rws 占比 37.6%`（`verification_log.md` P-02，**C 档**，且「Rfc 与 Req,sr 并联」是推断的拓扑）。

---

## 3. AIP Advances 2023

```text
标题    Thermal analysis and experimental verification of permanent magnet
        synchronous motor by combining lumped-parameter thermal networks
        with analytical method
期刊    AIP Advances, 2023, 13(2), 025140
DOI     10.1063/9.0000464
许可    CC-BY
用途    24/6 极 SPM 的损耗参数、几何与「计算 vs 实验」曲线
```

| 用到的内容 | 位置 | 核实条目 |
|---|---|---|
| P_Cu = 110 W，P_Fe = 281 W | Table I | A-01（原文 `Copper-steelloss 110-281`） |
| 轴 / 定子外径 / 内定子 = 10 / 96 / 28 mm | Table I | A-02 |
| 气隙 2.5 mm | Table I | A-03 |
| 齿宽 6 / 槽口 3 mm | Table I | A-04 |
| 24/6 极原型 | Abstract | A-08 |
| 最大工作点 8.5 kW | Fig.4 图注 | A-09 |
| 6.3 kW 点误差约 10% | 正文 §IV | A-10 |
| Fig.4(b) 五个功率点的三条曲线 | Fig.4(b) | **B 档：人工读点，±1℃** |

**派生用途**：

```text
Δ_cal = 33 K   ← e_under=16.5 K（6.4 kW 点的实验−计算）× γ=2
                  verification_log.md P-05，C 档
最大正残差 16.5 K / 8.8 kW 点漏报 7.5 K  ← B 档读数派生，不确定度 ±2 K
```

---

## 4. arXiv 2111.07711

```text
标题    Energy-optimal Design and Control of Electric Powertrains
        under Motor Thermal Constraints
会议    ECC 2022
用途    仅作为「LPTN + Motor-CAD 验证」的方法参考，未引用具体数值
```

---

## 5. Motor-CAD 教程模型

```text
来源    Ansys Motor-CAD 2026R1 自带教程
模型    Tutorials/BPM_Modelling/EV_5_Thermal.mot
许可    随 Motor-CAD 安装；**本仓库不收录 .mot 文件**（见 .gitignore）
用途    阶段 E 的对照对象；BM3 benchmark 的数据来源
```

| 用到的内容 | 核实条目 |
|---|---|
| 定子外径 198 / 叠长 160 / 气隙 1 / 48 槽 | A 档（.mot 文本直接解析） |
| 90 个节点的功率与温度 | A 档（PyMotorCAD 读取，见 `阶段E_总结.md`） |
| Rsa 随温度降 63% | A 档（五点扫描实测） |

**注意**：阶段 E 的对照对象是 Motor-CAD 的**集总参数热网络**，不是台架实测。

---

## 6. 复现说明

要复现这些数据，需自行获取上述文献，并按 `verification_log.md` 记录的位置核对。

`refs/` 下的 `fetch_ref.py` 与 `fetch_html.py` 是抓取脚本，仅用于合法获取开放获取（OA）文献。
