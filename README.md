# MDCA — 电机设计约束审计器

> ## ⚠️ 使用前必读：五条限制
>
> ```text
> ① 它审的是「这批数据自己能不能立住」，不是「这台电机好不好」。
>    所有 PASS 都只是数据层结论，不代表真实电机安全。
>
> ② A 路（默认）不产生温度。它没有自己的热阻、没有自己的实测，
>    所以它不能告诉你电机的真实温度是多少——那是 B 路插件的事，而 B 路默认关闭。
>
> ③ 它发现不了「所有数都错但互相自洽」的情况。
>    例如整批数据都来自同一个错的模型：口径一致、KCL 自洽、效率闭合，
>    审计器会判 AUDIT_PASS。它只能审内部一致性，不能审与真实的一致。
>
> ④ 回测的 72/72 是「断言命中」，不是「判据被证明正确」。
>    每条期望值来自独立物理恒等式或文献数字，但阈值（温度读数不确定度、
>    功率不确定度）仍是工程约定。
>
> ⑤ 电磁链闭合判据只覆盖已登记的错法谱系（4 倍匝数-磁通抵消、
>    √2 电流口径、R∝N 误缩放、2D 铜损移交、效率分母混用…）。
>    新错法会落到「仅报残差、不给指纹」。
> ```

**它坐在 AI / 代理模型 / 优化器 与 Motor-CAD / Maxwell 之间，是一道独立闸门。**

```text
输入   任意来源的一批数据（ai / optimizer / human / external），单个或批量
输出   每条数据的审计状态、原因码、矛盾定位、四档分级、可追溯报告
价值   把「物理上不可能」与「暂时不知道」分开；让人工与外部仿真集中到值得的地方
不做   替代仿真、承诺真实性能、证明电机安全
```

---

## 一、两条路

```text
A 路（主线，默认）  不产生数据，只判断别人给的数能不能用
                   口径 → 契约 → 溯源 → 电磁链闭合 → 热自洽反推 → 分级

B 路（插件，默认关）自己算温度：两热阻 / 三节点 / 区间 / 压力测试 / 几何缩放
                   需要 `--plugins thermal,scaling` 或 case 里显式声明才启用
```

为什么 B 路默认关（[项目复盘与路线转向.md](项目复盘与路线转向.md) §2）：
B 路的热阻是借来的，没有一个对应某台真实电机；用它算出的温度能自洽，但不是验证。
A 路不需要它，也就不会重犯这个错。

---

## 二、命令

```bash
# A 路（默认）
python mdca.py audit cases/           # 审计目录 / .json / .jsonl
python mdca.py audit cases_ai/AI01.json   # 看矛盾定位长什么样
python mdca.py validate case.json     # 只做校验，不计算
python mdca.py palette <md或目录>      # 口径审计（扫 Markdown 里的数字）
python mdca.py plugins                # 列插件与默认状态
python mdca.py selftest               # A 路冒烟（7 条）
python mdca.py check 300              # A 路边界算例（17 条）

# B 路（显式启用）
python mdca.py audit cases/ --plugins thermal,scaling
python mdca.py selftest --plugins thermal,scaling
python mdca.py check 300 --plugins thermal,scaling

# 回测与自测
python road_a_backtest.py             # 五大真实数据集，72 条断言
python mdca_inverter.py selftest      # 反推器判据（12 条）
python baseline_b_road.py --diff      # B 路 34 算例与基线逐字段对比

# 批量审查外部候选（把一堆审计结果变成可行动的分类）
python mdca_review.py cases_ai/                        # 控制台 + out/review.json
python mdca_review.py cases_ai/ --md out/review_ai.md  # 额外写 Markdown 报告
python mdca_review.py cases/ cases_negative/ cases_ai/  # 一次审多批

powershell -File run_tests.ps1        # 全部 18 项
```

---

## 三、一次 A 路审计经过什么

```text
candidate.json / .jsonl
   ↓
① 契约校验      schema 1.0 结构、枚举、必需字段            → P0_contract
② 溯源校验      外部来源 sha256 不符或缺失                  → P1_source_hash
③ 硬拦截        未验证数据无 conservatism 声明              → V2_unverified
④ 口径冲突      8 个可混字段（匝数/维度/含端部/工况…）冲突    → V3_palette_mix
⑤ 适用域        候选超出校准域                              → D1_out_of_domain
⑥ 电磁链闭合    λpm / Kt / 反电势 / 铜损 / 匝数缩放 / 效率   → C1_* / C2_* / C3_*
⑦ 热自洽反推    KCL 残差 / 隐含热阻 / 多工况集合交集        → I1_* / I2_* / I3_* / I4_*
   ↓ B 路插件启用时额外执行
⑧ 温度计算      两热阻 / 三节点 + 热反馈迭代
⑨ 区间包络      单调顶点求值（含 N≥0、g<1 前提检查）
⑩ 压力测试      A（放宽 10%/20%）/ B（alpha）/ C（gamma×2）
   ↓
⑪ 结论 + 四档分级 + JSON 报告 + 追加账本
```

**关键行为**：校验失败**拒绝继续**，不是「报个 FAIL 但照样往下算」。这是代码级禁止，不是报告级提醒。

---

## 四、候选契约（schema 1.0）

完整定义见 [mdca_schema.json](mdca_schema.json)，校验器 [mdca_schema.py](mdca_schema.py)。

```jsonc
{
  "schema": "1.0",
  "case_id": "joint-A-body-conduction",
  "source_type": "ai | optimizer | human | external",
  "benchmark": "BM1",                     // 非空表示这是测试基准
  "params": {
    // ---- 必需（A 路最小集）----
    "Pcu": { "value": 13.6, "unit": "W", "palette": { "temp_basis": "20C" } },
    "Pfe": { "value": 5.33, "unit": "W" },
    "Ta":  { "value": 40.0, "unit": "C" },

    // ---- 热阻：A 路可选（给了就参与反推对照），B 路必需 ----
    "Rws": { "value": 1.16, "interval": [0.90, 1.45], "unit": "K/W",
             "palette": { "turns": "6", "method": "analytic", "end_winding": "included" },
             "verified": true, "source": "基线档案 2.2c ⑧" },
    "Rsa": { "value": 2.03, "unit": "K/W" },
    "Peddy": { "value": 15.24, "unit": "W", "conservatism": "conservative" },

    // ---- 电磁链闭合：全部可选，给了才审 ----
    "N_coil":    { "value": 6,  "unit": "turns" },
    "lambda_pm": { "value": 10.46, "unit": "mWb" },
    "Kt":        { "value": 78.5, "unit": "mN.m/A", "palette": { "current_basis": "peak" } },
    "R_phase":   { "value": 0.018, "unit": "Ohm" },
    "I_rms":     { "value": 15.9, "unit": "A" },
    "eta":       { "value": 90.7, "unit": "%" }
  },

  // ---- A 路反推器专用的三个顶层扩展（都可缺省）----
  "reported": { "Tw": 102.66, "Tr": 120.95 },   // 对方申报的节点温度
  "operating_points": [                          // 多工况点
    { "Ta": 45, "Ts": 96.82, "Tw": 160.43, "Pcu": 572.17, "Ptot": 705.42 }
  ],
  "topology_spec": { "Req_w": 0.07, "Req_sr": 0.382, "Rfc": 0.167 },

  "limits": { "Tw_max": 155.0, "Tr_max": 150.0, "temperature_type": "mean" },
  "calibration": { "delta_cal_K": 33.0, "source_type": "external_model_mismatch" },
  "domain": { "Ta": [10, 60], "Pcu": [5, 30] },
  "scaling": { "reference_geometry": {}, "reference_R": {}, "geometry": {} },
  "sources": [{ "path": "refs/x.pdf", "sha256": "…" }]
}
```

### 口径字段（11 个）

| 字段 | 取值 | 说明 |
|---|---|---|
| `turns` | `"6"` / `"8"` | 匝数版本 |
| `dim` | `2D` / `3D` | 维度 |
| `method` | analytic / fem / measured / design_target | 获取方式（**不参与冲突判定**） |
| `end_winding` | included / excluded | 是否含端部 |
| `scope` | motor / module | 本体 / 模组 |
| `load_case` | continuous / peak / rated | 工况 |
| `current_basis` | rms / peak | 电流口径 |
| `temp_basis` | 如 `"20C"` | 参考温度 |
| `baseline` | 任意 | 降幅/占比基准 |
| `convergence` | converged / not_converged | 收敛状态 |
| `verified` | true / false | 验证状态 |

**8 个字段参与「混用即错」判定**（`MIX_BLOCKING`）。`method` 不参与——仿真的齿槽转矩除以解析的连续转矩、解析转矩除以设计目标质量，都是正常工程做法。

### 两条硬约定

```text
① Pcu 必须声明参考温度（palette.temp_basis）。用工况值当 20℃ 基准，
   热反馈迭代会把温度放大 64 K（真实踩过）。
② domain 的区间必须与 params 同参考温度，否则超域判断无意义。
```

---

## 五、状态与分级

### 5.1 状态（一套词，不再分路）

```text
—— 数据层（A 路与 B 路共用）——
AUDIT_BLOCKED    输入被判死：契约 / 单位 / 口径冲突 / 哈希 / 负值 / 区间 /
                 参考温度 / 电磁链不闭合 / 热 KCL 不自洽 / 隐含热阻为负
AUDIT_REVIEW     数据可用但需人看：超域 / 外推 / 未验证但声明保守 / 基准豁免
AUDIT_PASS       口径、契约、溯源、以及已提供的电磁-热判据全部过审
                 ⚠ 只表示「这批数据没有明显毛病」，不表示可信，更不表示电机合格

—— 温度层（仅 B 路启用时）——
POINT_PASS / BOX_PASS    点值 / 区间上界未超限且校准有效
MODEL_FAIL               明确超限
MODEL_REVIEW             外推 / 贴边 / 缺校准 / 超域 / 压力翻转 / 模型失效
COUPLING_FAILED          热反馈不收敛（g ≥ 1）或超迭代上限

—— 求解器层 ——
SOLVER_UNKNOWN   求解器没给出结论：插件加载失败或插件缺必需参数
                 （注意：这不是「数据不可用」，数据不可用是 AUDIT_BLOCKED）
```

### 5.2 四档分级

规则在 [grading.json](grading.json)（grading-1.4，带 version 与 changelog），实现 [mdca_grading.py](mdca_grading.py)。

```text
淘汰      模型层明确违规、耦合失稳、模型失效，或硬拦截属物理/数学上不可能
继续优化  A 路全干净（AUDIT_PASS）；或温度层通过但裕度 < 10 K / 贴边 < 5 K
人工复核  REVIEW / UNKNOWN / 缺校准 / 超域 / 外推 / 压力翻转 / 数据未验证
送外部    温度层通过 + 校准有效 + 在域内 + 裕度 ≥ 25 K
```

**两条硬约束**：

```text
① 分级只能比原状态更保守。MODEL_REVIEW / SOLVER_UNKNOWN 永不能升到「送外部」。
② AUDIT_PASS 只在「继续优化」档，永不进「送外部」——
   A 路不产生温度结论，没有资格推荐送 Motor-CAD / Maxwell。
```

**阈值依据**（存于 `grading.json` 的 `thresholds_basis`）：

```text
margin_small_K = 10 K   阶段 E 实测残差下限 7.70 K，取整并留 30% 余量
margin_ample_K = 25 K   = 10 K + 压力测试 P0+20% 的典型温升影响（约 15 K）
near_limit_band_K = 5 K ≈ 2× 热电偶不确定度（±2K）+ 1K
```

证据等级：**工程约定（派生自实测残差，非文献值，非实测标定）**。残差来自单台水冷电机。

---

## 六、A 路的判据

实现见 [mdca_inverter.py](mdca_inverter.py)（900 行，12 条自测）。核心方法只有一句：
**不需要自己的热阻与实测，用对方给的数检验对方给的数。**

### 6.1 电磁与功率链闭合（C1~C3）

| 判据 | 检验的恒等式 | 可定位的错法 |
|---|---|---|
| `C1a` | `λpm = N_series · kw · Φ_pole` | 「N 用每线圈匝数、Φ 用 N 个线圈合计磁通」的 4 倍互相抵消 |
| `C1b` | `Kt = 1.5 · p · λpm` | peak / rms 电流基准混用（差 √2） |
| `C1c` | `E_line_pk = √3 · ωe · λpm` | 相电势当线电势（差 √3） |
| `C1d` | `E_line_pk / U_bus` | 母线电压余量不足（>1 拦截，≥0.88 告警） |
| `C2a` | `Pcu = 3 · I_rms² · R_phase` | I_peak 未除 √2 / 漏乘 3 / 2D 不含端部 |
| `C2b` | 固定槽满率下 `R ∝ N²` | 按一次方误缩放 |
| `C2c` | 槽满率三口径 | 净铜/包络、净铜/槽、含漆/槽 用了哪个当分母 |
| `C3a` | `P_out = T_out · ωm` | — |
| `C3b` | `η = 1 − P_loss / P_in` | 误用 `P_out` 作分母 |

### 6.2 热网络反推（I1~I4）

| 判据 | 检验什么 |
|---|---|
| `I1` | 给定 `(R, P)` 与申报 `Tw/Tr`，检验稳态热流守恒（KCL）。能定位 6 种指纹：`kcl_missing_pcu_in_rsa` / `topo_rotor_on_winding` / `unconverged_cold` / `unconverged_1step` / `kcl_pfe_through_rws` / `kcl_missing_peddy` |
| `I2` | 反推隐含热阻：有 `Ts` 时解耦求 `Rws/Rsa/Rrs`，没有时给**严格代数上下界**；检验 `R>0`、`g<1`；用 Boglietti 2008 `R₀ = 0.167/A` 反算隐含换热系数倍数 |
| `I3` | 多工况有界误差反推，求可行区间交集 `∩ S_k`——**空集即严格证伪「热阻是常数」**；Spearman 判单调漂移，**检出漂移即升 REVIEW**（认可物理成因，但不许当常参数外推）；检出「计算通过、实测超限」的漏报点 |
| `I4` | 串并联支路合成、Energies 2021 Eq.(4) 端部铜损占比 |

判据的文献依据：Boglietti 2008（`R₀=0.167/A`、接触间隙 0.01~0.10 mm、绕组等效导热拟合式）、
Energies 2021（Eq.3~7 端部二阶模型）、Wallscheid & Böcker 2016（LPTN 全局辨识）、
Ragot & Maquin 2005 / Raïssi 2005（有界误差集合成员检验）。

### 6.3 一个真实例子

外部模型给的 10 个方案，申报温度看似「裕度极大」（Tw 在 52~103℃）。审计器不需要任何自己的热阻，只做一件事：

```text
用它自己的 Rws、Rsa、Rrs、Pcu、Pfe、Peddy、Tw、Tr
代入稳态热流守恒方程
→ 发现它的 Tw 与自己的损耗/热阻不自洽，残差 −7.4 ~ −44.1 K
→ 进一步定位：铜损只乘了 Rws，漏掉了流经 Rsa 的那一段压降
                且转子热阻挂在了绕组温度上，而不是定子铁芯上
```

10/10 全部命中，指纹匹配残差 ≤ 0.005 K。详见 [CHANGES_第3_4步_反推器与回测.md](CHANGES_第3_4步_反推器与回测.md) §4.2。

### 6.4 审计覆盖率：PASS 必须说清「审了几条」

判据只在字段齐了才会跑。只给 `Pcu`/`Pfe`/`Ta` 三个数的候选，一条判据都触发不了，结果同样是 `AUDIT_PASS`——但那是「没东西可审」，不是「很干净」。

所以每次审计都带一个覆盖率：

```text
审计覆盖率：审了 3/11 条判据，8 条因缺字段未审
  未审：C1a_flux_linkage、C1b_kt_flux、C3a_mech_power…
```

`AUDIT_PASS` 且覆盖率为 0 时，结论里会直接写明：

```text
⚠ 本次自洽性判据 0/11 条被触发（字段不足），该 PASS 只覆盖口径层，
  不代表数据自洽性已被检验。
```

这堵的是一个反直觉的漏洞：**少给字段反而更容易过**。判据表与所需字段见 `mdca_inverter.py` 的 `CHECK_REQUIREMENTS`，自测 ⑬~⑮ 守着它。

---

## 七、B 路插件（默认关）

```text
mdca_plugin_thermal.py   温度计算适配器（两热阻 / 三节点 / 区间 / 压力测试）
mdca_plugin_scaling.py   几何缩放适配器（候选几何 → Rws/Rsa/Rrs）
```

### 开关优先级

```text
1. CLI        --plugins thermal,scaling
2. case JSON  "plugins": {"thermal": true}   或   "plugins": ["thermal"]
3. 环境变量    MDCA_PLUGINS=thermal,scaling
4. 默认       关闭
```

`--plugins ""` 是「显式全关」，与「不传」不同（不传会往下一级落）。
插件名拼错当场报错；插件被显式启用却跑不起来时报 `SOLVER_UNKNOWN`，**绝不静默降级回 A 路**。

### 两条公式

```text
两热阻：Tw = Ta + Rsa·Pfe + Req·Pcu
三节点：Tw = Ta + Rsa·(Pfe+Peddy) + Req·Pcu
        Tr = Ts + Rrs·Peddy
热反馈：Pcu(Tw) = P0·[1 + α·(Tw − Tref)]
        g = α·Req·P0           g ≥ 1 ⇒ COUPLING_FAILED
解析解：θ = N/(1−g)
```

区间处理**不用逐式传播**（Rsa 与 P0 在公式里多次出现会爆炸），而是单调顶点求值：在 `N≥0、g<1` 前提下 θ 对每个参数单调不减。

### 缩放律

```text
Rws ∝ L_ins / (λ_eff · A_slot)     A_slot = Z · P_slot · L
Rsa ∝ 1 / A_case
Rrs ∝ g / (λ_air · A_gap)          A_gap = π · D_gap · L
```

未校准，自动附 ±25% 区间；偏离基准超 30% 判 `S2_extrapolation → REVIEW`。
**缩放会覆盖 case 里手填的热阻**——报告里会显式打印「缩放覆盖：Rws 手填 1.16 → 缩放 0.7733 K/W」。

---

## 八、回测：五大真实数据集

[road_a_backtest.py](road_a_backtest.py) 对**文献 / 实验 / 工程记录里的原始数字**做回测，72 条断言：

| 集 | 数据集 | 断言 | 关键结论 |
|---|---|---|---|
| 1 | joint-module 电磁-热真实事故 | 29/29 | λpm 4 倍陷阱、8 匝母线 97.9%、R∝N²、2D 铜损、效率分母、铁损误乘 Req 全部命中；**90.7% 那条本就正确，未误报** |
| 2 | AI 组 10 个外部模型方案 | 7/7 | 10/10 命中 KCL 违规与拓扑错挂，指纹残差 ≤ 0.005 K |
| 3 | Energies 2021（4 kW TEFC） | 10/10 | `Req,sr ∥ Rfc = 0.1162`、Rws 占比 37.6%、Rfc 四频率交集为空（5.75×） |
| 4 | AIP Advances 2023（五功率点） | 8/8 | 最大正残差 16.5 K → `Δ_cal = 33 K` 由机器算出；8.8 kW 漏报点 |
| 5 | Motor-CAD EV_5 | 16/16 | Rws 区间交集非空（可当常数）/ Rsa 交集为空且降 63% / 非单调温升 |

**最有价值的两条**：

```text
· Δ_cal = 33 K 原本是「人工读点 + γ=2 拍的」，现在是机器从原始五点数算出来的
· 任务 2 的「漏报率 0% 还是 100% 取决于 Tw_max 取 155 还是 180」——
  现在有了不依赖该参数的结论：固定 Rsa 模型外推 7/7 漏报，
  最大低估 62.7 K，加 33 K 裕度后仍剩 4 点
```

### 8.1 批量审查：把审计结果变成可行动的分类

`mdca_review.py` 不是「再跑一遍 audit」，而是把一批结果分成**五类**，每类对应一个动作：

| 分类 | 判据 | 该怎么处理 |
|---|---|---|
| **物理不可能** | 电磁链或热守恒被证伪（`C1~C3` / `I1~I4`） | 直接淘汰；已证伪，不是待确认项 |
| **格式不对** | 任何非 `V2_` 的 BLOCK（契约/单位/口径/哈希/负值） | 让对方按契约重交 |
| **数据没来源** | 只有 `V2_*` | 找来源或补 conservatism 声明，**不是找错** |
| **待人工确认** | REVIEW（超域/外推/基准豁免） | 人看一眼 |
| **全干净** | `AUDIT_PASS` | 可进下一轮 |

优先级是刻意排的：**「连比较都做不了」比「数据能用但不知真伪」更根本**，所以只要存在任何非 `V2_` 的 BLOCK，就不能归入「数据没来源」。

还会做两件 audit 本身不做的事：

```text
① 错法指纹聚合：把 detail 里的 Tw_fingerprint / Tr_fingerprint 一并抽出，
   按「code[Tw=..., Tr=...]」统计。出现 ≥3 次标为「系统性」——
   同一批数据反复出现同一个指纹，是生成器的一致缺陷，不是个别笔误

② 盲区提示：case 带 scaling 段但缩放插件没启用时明确写在报告里。
   这类 case 的结论是「没审」，不是「没问题」——
   不提示的话，「全干净」会被读成「没问题」
```

实际效果（`cases/` + `cases_negative/` + `cases_ai/` 共 27 条）：

```text
物理不可能 10  (37.0%)
格式不对   10  (37.0%)
数据没来源  2  ( 7.4%)
待人工确认  1  ( 3.7%)
全干净      4  (14.8%)

错法指纹：I1_kcl_violation[Tw=kcl_missing_pcu_in_rsa, Tr=topo_rotor_on_winding]  10  ← 系统性
盲区提示：3 条 case 有 scaling 段但缩放插件未启用
```

---

## 九、测试

```text
run_tests.ps1     18 项
  A 路  plugins 15 / provenance 9 / scaling 5 / schema 15 / grading 17 /
        palette 5 / mdca A 路 7 / check A 路 17 / inverter 12 / backtest 5 集
  B 路  thermal 13 / thermal 插件 8 / scaling 插件 4 / mdca B 路 10 /
        check B 路 17+MC / B 路基线 34（差异必须为 0）
```

### 措辞纪律

```text
「17 条边界算例全部命中预期」= 植入的 17 类错误都被相应规则拦下或降级。
这 ≠ 漏报率（判 PASS 而外部复核不通过）。后者样本量为 0。

「72/72 回测通过」= 断言命中，≠ 判据被证明正确。
```

---

## 十、明确没做的

```text
✓ 转子/磁钢节点     已支持（Peddy>0 自动三节点）
✓ Rws/Rsa 缩放律    已支持（scaling 段）
✗ Z3 / Lean 后端    方案 5 自述「MVP 不预设 Z3 必要」，成本 3~4 周
✗ pint / SymPy      公式不足 10 个且已人工核过两遍
✗ 转子温度的区间    只给点值，不给包络
✗ 转子温度的校准裕度 Δ_cal 只加在绕组上（来自绕组残差，对转子无依据）
✗ 绝对温度残差      卡在损耗分离与独立热阻两个前置
✗ 暂态 / 负载谱     仅稳态
✗ 退磁判定          仅一维磁路裕度，无 FEM
✗ GUI               仅 CLI
```

## 十一、已知限制

**以下每条都有实测或文档依据，不是免责声明。**

```text
① 校准只有外部模型对照，没有本机实测。source_type=external_model_mismatch 意味着
   残差来自「别人的模型 vs 实测」，model_similarity_assumed=true 是未验证的让步。

② Rsa 随温度剧烈变化（40~125℃ 内降 63%，实测于 Motor-CAD EV_5），Rws 只变 15%。
   ⇒ 「热阻是常数」只在窄温度窗内成立；校准点与目标工作温度差不得超过 ±15 K。

③ 「绕组温度」在真实电机里是分布而非单值。Motor-CAD EV_5 上槽内 115℃、热点 159℃，
   差 40 K。temperature_type（mean/hotspot）必须写明，否则残差无意义。

④ 线性热网络对边界温度变化的传递率是 83%~91%（实测），不是 1:1。
   约 0.13×ΔTa 的偏差是结构性的，不能靠常数 Δ_cal 消掉。

⑤ 三节点结构的残差 2~4 K 只来自单台水冷电机的跨工况预测；
   自然对流 / 机身导热电机的残差未测。

⑥ A 路的 I3 判据用 delta_T_K=1.0 K、rel_P_unc=2% 两个工程假设构造可行区间。
   它只用来判「交集是否为空」，绝不输出「Rsa = 0.0388 ± 0.002」这类定量区间。
```

---

## 十二、证据等级约定

```text
A  从 PDF / .mot 文本直接提取，可用 refs/ 原件复核
B  人工读点或截图，标 ±1℃ 等不确定度
C  由 A/B 推算，原文未直接给出（不得称为「文献结论」）
U  来自用户工程文件，待提供原件
```

核实记录随内部研发日志保留，未随仓库公开。

---

## 十三、文件

```text
A 路核心
  mdca.py              1247 行  编排（校验→拦截→插件→判定→分级→账本）
  mdca_inverter.py      1508 行  反推器 + 电磁-热自洽性判据集
  mdca_palette.py       1020 行  口径层（11 字段 / 11 规则 / Markdown 扫描）
  mdca_schema.py        338 行  契约校验器
  mdca_provenance.py    244 行  溯源层
  mdca_grading.py       285 行  四档分级（配置驱动）
  mdca_plugins.py       370 行  插件注册表与四级开关

B 路插件（默认关）
  mdca_plugin_thermal.py 346 行  温度计算适配器
  mdca_plugin_scaling.py 169 行  几何缩放适配器
  mdca_thermal.py        689 行  温度计算本体（A 路不 import）
  mdca_scaling.py        215 行  缩放律本体
  mdca_checks.py         318 行  随机回代（仅 B 路自检）

工具
  road_a_backtest.py     627 行  五大真实数据集回测（72 条断言）
  mdca_review.py         636 行  批量审查外部候选（五类分类 + 指纹聚合 + 盲区提示）
  baseline_b_road.py     119 行  B 路回归基线（--diff 必须差异 0）
  mdca_batch.py          495 行  生成候选 + 植入问题 + 度量（阶段 D 实验复现）
  b5_rule_matrix.py      119 行  规则适用性矩阵
  run_tests.ps1                 18 项一键自测

数据
  cases/                        4 条主算例
  benchmarks/                   3 台基准
  cases_negative/               13 条输入层负例（带 _expect 与 _expect_a 两套期望）
  cases_edge/                   4 条结论层边界
  cases_ai/                     10 个外部模型方案
  baselines/v0.2_b_road.json    B 路基线快照
  mdca_schema.json              候选契约（1.0）
  grading.json                  分级规则与阈值依据（grading-1.4）
  refs/                         文献 PDF + 提取文本（gitignore）

文档（随仓库公开）
  项目复盘与路线转向.md           路线与撤回清单（先读这个）
  CHANGES_第3_4步_反推器与回测.md  A 路判据、72 条回测、一个分级语义变化
  CHANGES_第2步_插件化.md        温度计算怎么摘出去的
  方法对照_文献.md               与文献的 8 项差异
```

### 13.1 文档地图

随仓库公开的文档只有四份，按你要回答的问题查表：

| 你想知道 | 读这个 |
|---|---|
| **这工具现在能干什么、怎么用** | 本文件（README） |
| **为什么从「热计算器」转向「审计器」** | [项目复盘与路线转向.md](项目复盘与路线转向.md)（最重要） |
| **A 路具体有哪些判据、靠不靠得住** | [CHANGES_第3_4步_反推器与回测.md](CHANGES_第3_4步_反推器与回测.md) |
| **温度计算是怎么摘成插件的** | [CHANGES_第2步_插件化.md](CHANGES_第2步_插件化.md) |
| **和文献比，方法差在哪** | [方法对照_文献.md](方法对照_文献.md) |

**阅读顺序**：README → 项目复盘 → CHANGES_第3_4步 → 需要时再查其余。

> 研发过程日志（冻结指纹、各阶段实验记录、盲测过程、校准尝试）未随仓库公开。

---

## 十四、踩过的坑（会复发）

```text
· GetVariable 返回 (code, value) 二元组；GetTemperatureGraphPoint 返回三元组
· .mot 是纯文本 INI（124 section），不启动软件也能解析
· Motor-CAD 脚本必须自带 Quit，否则实例变僵尸，下次连接报 RPC 失败
· 后台长任务输出不能 Select-Object -First 截断（BrokenPipe 会杀掉进程）
· Motor-CAD 当前实例是 Lab 模式，无 GUI 窗口是正常的
· Pcu 必须声明参考温度，用工况值当 20℃ 基准会放大 64 K
· domain 必须与 params 同参考温度，否则误判超域
· 要测某分支，必须保证其他分支都不触发，否则被掩盖
· case_hash 不能用文件字节算，否则同内容不同格式误报「输入被修改」
· 自洽性判据只在 A 路并入 issues；B 路并入会破坏 34 算例基线
· 单位比较要先归一化（mm²/mm2、N·m/N.m、℃/C、Ω/Ohm），否则正常写法被误拦
· 指纹匹配容差必须显式收紧（AI 组能吻合到 0.005 K，放到 0.5 K 就失去定位价值）
```
