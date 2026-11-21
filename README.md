# MDCA — 电机设计约束审计器

> 不回答「电机的真实性能是多少」，只回答：
> **这批候选方案里，哪些存在明确的约束违规、哪些模型超出了适用范围、哪些结论值得进入下一轮计算。**

**它坐在 AI / 代理模型 / 优化器 与 Motor-CAD / Maxwell 之间，是一道独立闸门。**

```text
输入   任意来源的一批候选（ai / optimizer / human / external），单个或批量
输出   每个候选的审计状态、原因码、四档分级、可追溯报告
价值   减少明显违规与不值得计算的候选，让人工检查与外部仿真集中到更值得的方案上
不做   替代仿真、承诺真实性能、证明电机安全
```

**所有 PASS 只是模型层结论，不代表真实电机安全。**

---

## 一、命令

```bash
python mdca.py audit cases/          # 审计（目录 / .json / .jsonl）
python mdca.py check 500             # 自检：随机回代 + 16 条边界算例
python mdca.py validate case.json    # 只做校验，不算温度
python mdca.py palette <md或目录>     # 口径审计（扫 Markdown 里的数字）
python mdca.py selftest              # 冒烟测试
```

底层模块可独立运行：

```bash
python mdca_schema.py selftest       # 契约校验器（14 条）
python mdca_grading.py selftest      # 四档分级（12 条）
python mdca_thermal.py --selftest    # 两热阻 / 三节点（13 条）
python mdca_provenance.py            # 溯源层（9 条）
python mdca_scaling.py               # 缩放律（5 条）
python mdca_palette.py --selftest    # 口径规则（5 条）
python b5_rule_matrix.py             # 规则适用性矩阵
```

---

## 二、一次审计经过什么

```text
candidate.json / .jsonl
   ↓
① 契约校验       schema 1.0 结构、枚举、必需字段            → P0_contract
② 溯源校验       外部来源 sha256 不符或缺失                  → P1_source_hash
③ 硬拦截         未验证数据无 conservatism 声明              → V2_unverified
④ 口径冲突       8 个可混字段（匝数/维度/含端部/工况…）冲突   → V3_palette_mix
⑤ 适用域         候选超出校准域                              → D1_out_of_domain
   ↓ 通过才继续
⑥ 物理计算       两热阻 / 三节点 + 电磁热耦合迭代
⑦ 区间包络       单调顶点求值（含 N≥0、g<1 前提检查）
⑧ 校准裕度       Δ_cal = γ × e_under
⑨ 压力测试       A（放宽 10%/20%）/ B（alpha）/ C（gamma×2）
   ↓
⑩ 判定 + 四档分级 + JSON 报告 + 追加账本
```

**关键行为**：校验失败**拒绝计算**（`SOLVER_UNKNOWN`），不是"报个 FAIL 但照样往下算"。这是方案 7.2 要求的「代码层面禁止」。

---

## 三、候选契约（schema 1.0）

完整定义见 [`mdca_schema.json`](mdca_schema.json)，校验器 [`mdca_schema.py`](mdca_schema.py)。

```jsonc
{
  "schema": "1.0",
  "case_id": "BM1-joint-300W-conduction",
  "source_type": "ai | optimizer | human | external",
  "benchmark": "BM1",                          // 非空表示这是测试基准
  "params": {
    "Rws": {
      "value": 1.16,
      "interval": [0.90, 1.45],                // 给了才跑区间
      "unit": "K/W",                           // 必需，不符即拦
      "palette": { "turns": "6", "method": "analytic", "end_winding": "included" },
      "verified": true,
      "conservatism": "conservative",          // 未验证时必填
      "source": "基线档案 2.2c ⑧"
    }
    // Rsa / Pcu / Pfe / Ta 必需；Peddy / Rrs / alpha / Tref 可选
  },
  "limits": { "Tw_max": 155.0, "Tr_max": 150.0, "temperature_type": "mean" },
  "calibration": { "delta_cal_K": 33.0, "source_type": "external_model_mismatch", … },
  "domain": { "Ta": [10, 60], "Pcu": [5, 30] },
  "scaling": { "reference_geometry": {…}, "reference_R": {…}, "geometry": {…} },
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

## 四、六种状态

```text
POINT_PASS       点估计 + 校准通过 + 未超限
BOX_PASS         区间上界 + 校准通过 + 未超限
MODEL_FAIL       明确超限
MODEL_REVIEW     外推 / 高不确定度 / 贴边 / 缺校准 / 超适用域 / 压力翻转 / 模型失效
COUPLING_FAILED  热反馈不收敛（g≥1）或超迭代上限
SOLVER_UNKNOWN   输入被硬拦截，未进入计算
```

## 五、四档分级

规则在 [`grading.json`](grading.json)（v1.2，带 version 与 changelog），实现 [`mdca_grading.py`](mdca_grading.py)。

```text
淘汰      模型层明确违规、耦合失稳、模型失效、或硬拦截属明确违规
继续优化  通过但裕度 < 10 K 或贴边（距限值 < 5 K）
人工复核  REVIEW / UNKNOWN / 缺校准 / 超域 / 外推 / 压力翻转 / 数据未验证
送外部    通过 + 校准有效 + 在域内 + 裕度 ≥ 25 K
```

**硬约束**：分级只能比原状态更保守。`MODEL_REVIEW` 与 `SOLVER_UNKNOWN` 永不能升到「送外部」。

**阈值依据**（存于 `grading.json` 的 `thresholds_basis`）：

```text
margin_small_K = 10 K
  阶段 E 实测残差下限 7.70 K（跨工况结构 4.34 + 边界响应 3.36），取整并留 30% 余量
margin_ample_K = 25 K
  = 10 K + 压力测试 P0+20% 的典型温升影响（约 15 K，g≈0.2 时估算）
near_limit_band_K = 5 K
  ≈ 2× 热电偶不确定度（±2K）+ 1K
```

证据等级：**工程约定（派生自实测残差，非文献值，非实测标定）**。局限：残差来自单台水冷电机，自然对流场景未测。

---

## 六、四层机制

```text
① 口径层   每个数字带口径指纹，8 个字段混用即报错
② 物理层   两热阻 / 三节点 + 电磁热耦合 + 区间顶点求值
③ 缩放层   热阻随几何变化，让「改设计」可算
④ 溯源层   哈希 + 版本 + append-only 账本
```

### 物理层的两条公式

```text
两热阻：Tw = Ta + Req·Pcu + Rsa·Pfe
三节点：Tw = Ta + Rsa·(Pfe+Peddy) + Req·Pcu
        Tr = Ts + Rrs·Peddy
热反馈：Pcu(Tw) = P0·[1 + α·(Tw − Tref)]
        g = α·Req·P0          g ≥ 1 ⇒ COUPLING_FAILED
解析解：θ = N/(1−g)
```

区间处理**不用逐式传播**（Rsa 与 P0 在公式里多次出现会造成依赖性问题、上界爆炸），而是用单调顶点求值：在 `N≥0、g<1` 前提下 θ 对每个参数单调不减。

### 缩放层

```text
Rws ∝ L_ins / (λ_eff · A_slot)     A_slot = Z · P_slot · L
Rsa ∝ 1 / A_case
Rrs ∝ g / (λ_air · A_gap)          A_gap = π · D_gap · L
```

缩放律未校准，自动附 ±25% 区间；偏离基准超 30% 判 `S2_extrapolation → MODEL_REVIEW`。

### 溯源层

```text
· 未验证数据无声明 → 代码层面拒绝计算
· 外部来源声明 sha256，不符或缺失 → 拒绝计算
· out/ledger.jsonl 只追加；输入变化打印 [变更] 并保留旧记录
· 报告带 code_digest 与 constraint_version
· case_hash 基于规范化审计输入（不用文件字节），同内容不同格式指纹一致
```

---

## 七、Benchmark（三台）

| 基准 | 电机 | 量级 | 冷却 | 节点 | 结论 |
|---|---|---|---|---|---|
| BM1 | joint-module 关节电机 | 300 W | 机身导热 | 3 | MODEL_FAIL |
| BM2 | Energies 2021 感应电机 | 4 kW | 风冷 TEFC | 2 | MODEL_REVIEW |
| BM3 | Motor-CAD EV_5 | ~100 kW | 水套 | 3 | MODEL_REVIEW |

**关节电机只是三台之一**，与另两台并列。BM2 无磁钢 → 无 `Peddy` → 自动退化为两节点。

BM2/BM3 是**假设数据，仅测试接口通用性**（Ta / Tw_max 由推断，热阻与损耗由温度反推）。

### 规则适用性（`b5_rule_matrix.py` 产出）

```text
23 条规则 → 三台通用 0、部分触发 5、空转 18

空转的 18 条全是输入层规则（契约/单位/口径/区间/哈希），
需要故意构造错误输入才能触发 → 由 cases_negative/ 覆盖
结论层规则（超温/转子超限/压力翻转/缺校准）→ 由 benchmark + cases_edge/ 覆盖
```

**因此验收标准是**：输入层规则由负例集覆盖，结论层规则由 3 个以上 benchmark 覆盖——不是"所有规则都要在 benchmark 上触发"。

**一条结构限制**：`rotor_over_magnet_limit` 仅在 `Peddy > 0` 时适用，感应电机上恒不触发。

---

## 八、测试

```text
八套自测          见第一节命令
边界算例          16 条（cases_negative 12 + cases_edge 4），全部命中预期
随机数值回代      800 组：解析 vs 迭代 / 两热阻 vs 三节点 / 缩放律基准恒等
主算例            4 条（cases/）
基准              3 条（benchmarks/）
```

### 措辞纪律

```text
「16 条边界算例全部命中预期」= 植入的 16 类错误都被相应规则拦下或降级。
这 ≠ 方案 10.2 定义的漏报率（判 PASS 而外部复核不通过）。
后者的样本量目前为 0 —— 从未做过真实外部复核，不能声称漏报率。
```

---

## 九、明确没做的

```text
✓ 转子/磁钢节点     已支持（Peddy>0 自动三节点）
✓ Rws/Rsa 缩放律    已支持（scaling 段）
✗ Z3 后端           方案 5 自述「MVP 不预设 Z3 必要」，成本 3~4 周
✗ pint / SymPy      公式不足 10 个且已人工核过两遍
✗ Lean 引理         若答辩需要，用 LeanCert 做 1 条即可（约 1 周）
✗ 转子温度的区间    只给点值，不给包络
✗ 转子温度的校准裕度 Δ_cal 只加在绕组上（来自绕组残差，对转子无依据）
✗ 绝对温度残差      卡在损耗分离与独立热阻两个前置
✗ 暂态 / 负载谱     仅稳态
✗ 退磁判定          仅一维磁路裕度，无 FEM
✗ GUI               仅 CLI
```

## 十、已知限制

**以下每条都有实测或文档依据，不是免责声明。**

```text
① 校准只有外部模型对照，没有本机实测。source_type=external_model_mismatch 意味着
   残差来自「别人的模型 vs 实测」，model_similarity_assumed=true 是未验证的让步。

② Rsa 随温度剧烈变化（40~125℃ 内降 63%，实测于 Motor-CAD EV_5），
   而 Rws 只变 15%。因此「热阻是常数」只在窄温度窗内成立，
   校准点与目标工作温度差不得超过 ±15 K，否则外推可错 20 K 以上。

③ 「绕组温度」在真实电机里是分布而非单值。Motor-CAD EV_5 上槽内 115℃、
   热点 159℃，差 40 K。temperature_type（mean/hotspot）必须写明，否则残差无意义。

④ 线性热网络对边界温度变化的传递率是 83%~91%（实测），不是 1:1。
   约 0.13×ΔTa 的偏差是结构性的，不能靠常数 Δ_cal 消掉。

⑤ 三节点结构的残差为 2~4 K（单台水冷电机的跨工况预测）。
   自然对流 / 机身导热电机的残差未测。

⑥ 分级阈值与模型残差都只来自单台水冷电机；缩放律只标定过自然对流档。
```

---

## 十一、证据等级约定

文档与报告中的数字按四档标注：

```text
A  从 PDF / .mot 文本直接提取，可用 refs/ 原件复核
B  人工读点或截图，标 ±1℃ 等不确定度
C  由 A/B 推算，原文未直接给出（不得称为「文献结论」）
U  来自用户工程文件，待提供原件（当前 11 项未提供）
```

核实记录见 [`verification_log.md`](verification_log.md)（52 条，0 条与来源不符）。

---

## 十二、文件

```text
mdca.py               928 行   编排层（校验→拦截→缩放→计算→判定→分级→账本）
mdca_palette.py      1020 行   口径层（11 字段 / 11 规则 / Markdown 扫描）
mdca_thermal.py       685 行   物理层（两热阻 / 三节点 / 区间 / 压力测试）
mdca_batch.py         469 行   候选生成与度量
mdca_schema.py        321 行   契约校验器
mdca_checks.py        318 行   随机数值回代
mdca_grading.py       273 行   四档分级
mdca_provenance.py    244 行   溯源层
mdca_scaling.py       215 行   缩放层
b5_rule_matrix.py     118 行   规则适用性矩阵

mdca_schema.json              候选契约（1.0）
grading.json                  分级规则与阈值依据（v1.2）

cases/                        4 条主算例
benchmarks/                   3 台基准
cases_negative/               12 条输入层负例
cases_edge/                   4 条结论层边界
refs/                         4 篇文献 PDF + 提取文本
out/                          audit.json / ledger.jsonl / checks.json …

MDCA_纠偏基线.md               纠偏阶段唯一基准（先读这个）
verification_log.md           52 条数据核实
CHANGES_纠偏阶段.md            纠偏全过程记录
阶段D_实验报告.md              植入候选集实验
阶段BC_报告.md                 契约 / 批量 / 规则矩阵
阶段C_阈值与覆盖盘点.md         阈值依据与测试覆盖
阶段E_总结.md                  Motor-CAD 对照总结
阶段E_扰动实验.md / 阶段E_跨工况验证.md / 阶段E_温度相关热阻.md
```

---

## 十三、这条线踩过的坑（会复发）

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
```
