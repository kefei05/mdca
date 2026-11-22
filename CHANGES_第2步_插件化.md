# 第 2 步变更记录：温度计算摘成可插拔，默认关闭

> 日期：2026-11-22
> 分支：`road-a/step2-plugins`（基于 `c691c49`）
> 上游依据：`项目复盘与路线转向.md` §7 路线图第 2 步

---

## 1. 一句话

**温度计算与几何缩放从 `mdca.py` 的模块顶部依赖搬进了两个默认关闭的插件。A 路（口径 / 契约 / 溯源 / 分级）现在不 import 任何热学模块就能跑完整个流程；B 路行为与 v0.2 逐字段一致（34 个算例零差异）。**

---

## 2. 改了什么

### 2.1 新增

| 文件 | 行数 | 职责 |
|---|---|---|
| `mdca_plugins.py` | 268 | 插件注册表、四级开关解析、延迟加载、异常兜底。**不含任何物理逻辑，也不 import 业务模块**（否则与 `mdca.py` 成环） |
| `mdca_plugin_thermal.py` | 280 | 原 `mdca.py` 的温度计算段：`to_cas` / `to_cas3` / `to_boxcas` / `uses_three_node` / `has_intervals` + 点值 → 区间 → 压力测试的合成 |
| `mdca_plugin_scaling.py` | 131 | 原 `mdca.py` 的 `apply_scaling`：几何缩放律 → `Rws/Rsa/Rrs` + 外推检查 |
| `baseline_b_road.py` | 140 | B 路回归基线工具（§5 的证据） |
| `baselines/v0.2_b_road.json` | — | 改前（`c691c49`）34 个算例的结论快照 |

### 2.2 改动

| 文件 | 改动 |
|---|---|
| `mdca.py` | 删除 `from mdca_thermal import ...` 与 `from mdca_scaling import ...`；`audit()` 加 `plugins` 参数；新增 A 路三状态；CLI 加 `--plugins`；`selftest` 按路拆成 7 + 3 项；`check` 支持 `--plugins` 与 `_expect_a`；新增 `plugins` 命令 |
| `mdca_schema.py` / `.json` | `params.required` 从 5 项收窄为 `Pcu/Pfe/Ta`（见 §6） |
| `grading.json` | 升 `grading-1.3`，新增 `A1_audit_pass` / `A2_audit_review` 两条规则；**原有 12 条规则与三个阈值一字未动** |
| `cases_negative/*` `cases_edge/*` | 17 条边界算例补 `_expect_a`（A 路期望值） |
| `run_tests.ps1` | 拆成 A 路 8 项 + B 路 5 项 |
| `b5_rule_matrix.py` 等 7 个实验脚本 | `audit(case)` → `audit(case, plugins=("thermal", "scaling"))` |

### 2.3 没动

```
mdca_palette.py      口径审计（A 路主力）—— 一字未改
mdca_provenance.py   溯源与账本        —— 一字未改
mdca_thermal.py      温度计算本体      —— 一字未改（只被插件 import）
mdca_scaling.py      缩放律本体        —— 一字未改
mdca_checks.py       随机回代          —— 一字未改（只被 B 路自检 import）
```

---

## 3. 开关机制

四级优先，高 → 低：

```text
1. CLI          python mdca.py audit cases/ --plugins thermal,scaling
2. case JSON    "plugins": {"thermal": true}   或   "plugins": ["thermal"]
3. 环境变量      MDCA_PLUGINS=thermal,scaling
4. 默认         B 路插件一律关闭
```

几个刻意的细节：

```text
· --plugins ""  / "none"  = 显式全关，就地生效，不再往下一级落
· 不传 --plugins          = 往下一级落（case → env → 默认）
  「显式关闭」与「没指定」必须分开，否则环境变量会覆盖命令行
· 名字拼错 → PluginError，当场报错
  拼错的插件名静默失效是本项目最忌讳的失败方式（此类问题在本项目反复出现）
· 插件被显式启用却跑不起来 → BLOCK（B0_plugin_failed / V1_plugin_missing）
  绝不静默降级回 A 路：用户要的结论拿不到，必须说出来
· 插件默认关闭时 → 不加载、不提示、不记 issue
```

`Case.plugins` 的默认值是 `None` 而不是 `[]`：`[]` 是「显式关闭全部」，`None` 是「文件里没这个字段」，两者混用会让环境变量那一级永远走不到（这个 bug 在实现过程中真的出现了，见 §8.2）。

---

## 4. A 路三状态

温度算不出来，就不该有 `POINT_PASS` / `MODEL_FAIL`。A 路的结论词汇：

| 状态 | 判据 | 含义 |
|---|---|---|
| `AUDIT_PASS` | 无 BLOCK、无 REVIEW | 口径、契约、溯源过审 |
| `AUDIT_REVIEW` | 有 REVIEW（超域 / 外推 / 未验证但声明保守 / 基准豁免） | 有需人工确认项 |
| `AUDIT_BLOCKED` | 有 BLOCK（契约 / 单位 / 口径冲突 / 哈希 / 负值 / 区间 / 参考温度） | 这批数据有硬伤 |

**`AUDIT_PASS` 只表示「这批数据没有明显的口径毛病」。它不表示数据可信，更不表示任何电机合格。** 这句话写在每份 A 路报告的 `audit.note` 里，不靠文档约定。

### 4.1 A 路看不见什么（能力边界，实测确认）

| 算例 | B 路结论 | A 路结论 | 为什么 |
|---|---|---|---|
| `E1_over_temperature` | `MODEL_FAIL` | `AUDIT_PASS` | A 路不算温度，不知道它超温 |
| `E2_model_invalid` | `MODEL_REVIEW` | `AUDIT_PASS` | 同上 |
| `E3_interval_crosses` | `MODEL_REVIEW` | `AUDIT_PASS` | 同上 |
| `N12_extrapolation` | `MODEL_REVIEW` | `AUDIT_PASS` | 缩放律没跑，不知道它外推 |

**这四条不是 bug，是定位的代价**：审计器只能说「这些数据互相之间有没有毛病」，不能说「这些数据对不对」。要补上这一块，得等反推器（路线图第 3 步）。

---

## 5. B 路逐行等价的证据

改前先跑了一份基线（`baselines/v0.2_b_road.json`，34 个算例 × 14 个字段），改后逐字段对比：

```text
python baseline_b_road.py --diff
→ 共 34 个算例，差异 0 个
```

对比的字段：

```text
status / reasons / coupling / Tw / Twc / Tr / Ts / interval /
blocks / reviews / warns / infos / stress_flipped / model_nodes
```

**这份等价证明的是「搬家没改行为」，不是「模型对」。** 后者的问题复盘里已经说清楚：热阻是借的，验证是同义反复。

对比覆盖的算例：`cases/` 4 + `benchmarks/` 3 + `cases_negative/` 13 + `cases_edge/` 4 + `cases_ai/` 10。

### 5.1 这条证据的强度（不要高估）

34 条记录零差异，但真正走到温度计算路径的只有 11 条：

| 路径 | 算例数 |
|---|---|
| `CONVERGED`（真算了温度） | 11 |
| 其中走三节点 | 9 |
| 其中产出了区间 | 9 |
| 其中压力测试发生翻转 | 2 |
| 其余在 `BLOCK` 或 `COUPLING_FAILED` 处早退 | 23 |

意思是：`solve` / `judge` / `stress_test` 的核心逻辑确实被覆盖了，但**状态叠加的组合覆盖有限**——§7.2 那个「`COUPLING_FAILED` 吞掉 `D1`」的问题，正是这类组合从来没被看见的结果。

### 5.2 顺带发现的静默覆盖（已修）

`joint-C-stretched-45mm` 文件里手填 `Rws = 1.16`，同时又带 `scaling` 段。缩放律把

```text
Rws  1.16  → 0.7733 K/W
Rsa  2.03  → 1.3533 K/W
Rrs  3.0   → 2.0   K/W
```

读 JSON 的人会以为 1.16 生效，实际算温度用的是 0.77。**报告里也没有任何提示。**

修复：新增 `S3_params_overwritten`（INFO）与结果字段 `params_overwritten`，报告里独立显示一行：

```text
缩放覆盖：Rws 手填 1.16 → 缩放 0.7733 K/W；Rsa 手填 2.03 → 缩放 1.3533 K/W
```

这是本次唯一改变输出的地方（其余 33 个算例一字未动），基线已按

```text
python baseline_b_road.py --write --force-since "新增 S3_params_overwritten 提示..."
```

重写并附了理由。**同类的第二个**在 `N12_extrapolation`，行为一致。

---

## 6. 契约变更：热阻从「必需」改为「可选」

### 6.1 改了什么

```text
改前  params.required = [Rws, Rsa, Pcu, Pfe, Ta]
改后  params.required = [Pcu, Pfe, Ta]
      Rws/Rsa 的必需性由 thermal 插件在运行时检查（code = V1_plugin_missing）
```

理由：A 路不产生温度，就不该强迫上游先交出热阻——**那正是「借热阻」这个漂移的入口**（复盘 §2 第一层）。而第 3 步的反推器，输入恰好是 `Pcu + Pfe + Ta + Tw`，热阻是要被反推的对象，不是输入前提。

### 6.2 一个真 bug（实现过程中出现并被修掉）

第一版的 A 路冒烟测试 ⑥ 是**手工构造 `Case` 对象**，没走 `parse_case_doc`，于是绕过了契约校验——测试绿了，但真实 JSON 文件缺 `Rws` 仍会被 `mdca_schema.py` 拦成 `P0_contract`。

改成从原始 dict 走 `parse_case_doc` → `audit` 之后，才暴露出 `REQUIRED_PARAMS` 还是 5 项。**测试绕过了真实入口，就是假绿。** 这条已写进 §8。

### 6.3 版本号：留在 1.0，没有升

`schema` 字段仍是 `"1.0"`，`mdca_schema.json` 里也仍是 `"const": "1.0"`。

```text
理由   改的是「参数是否必需」，不是「字段存在与否」；
       Rws/Rsa 仍在 properties 里，只是不再 required。
       且 A/B 两路共用同一份契约，升版本会让 34 个算例 + 5 个目录的
       JSON 全部要改 "schema" 字段，diff 掩盖真正要看的东西。

代价   契约文件的自我描述「任何字段变更必须升 version」在这里没有严格执行。

这条留给作者裁决：若认为该升，改动是机械的（改 34 个文件的 "schema" 字段 +
mdca_schema.py 的 SCHEMA_VERSION 与 const），我可以做。
```

---

## 7. 三处刻意的不一致（都知道，都留了理由）

### 7.1 B 路被拦仍报 `SOLVER_UNKNOWN`，A 路报 `AUDIT_BLOCKED`

```text
原因   v0.2 的 16 条边界算例 _expect 写死了 SOLVER_UNKNOWN。
       第 2 步承诺「搬家不改行为」，所以 B 路原样保留。
代价   同一件事两个名字，读者会问「为什么」。
处置   第 3 步重写分级时一并统一。见 §9。
```

### 7.2 `COUPLING_FAILED` 时早退，不叠加「输入超域」

```text
v0.2 行为  耦合失败 → 直接 return，reasons 只有 [thermal_feedback_divergence]
           E4 的 Pcu=100 W 明明超出 domain [5,30]，这个 D1 被吞掉了
本次       保持一致（早退），未顺手修
理由       第 2 步只搬家。顺手改会让「零差异」这个证据失效
建议       第 3 步把「早退」改成「先记域检查再早退」——这是信息遗漏，不是判定逻辑问题
```

### 7.3 `case_hash` 现在包含插件列表

```text
原因   A 路与 B 路的审计输入本来就不同，共用一个 hash 会让账本
       把「换了运行模式」误报成「输入被修改」
代价   升级后 out/ledger.jsonl 里的旧记录 hash 对不上，所有算例第一次审计
       都会显示一次「[变更] input_changed_since_last_audit」
处置   一次性噪音。想清干净就删 out/ledger.jsonl 重跑（out/ 是 gitignore 的）
```

---

## 8. 自测

### 8.1 现在的清单（13 项，全绿）

```text
A 路（不装插件）
  plugins 15          mdca_plugins.py selftest
  provenance 9        mdca_provenance.py
  scaling 5           mdca_scaling.py
  schema 15           mdca_schema.py selftest      （14 → 15，新增「缺 Rws/Rsa → 通过」）
  grading 17          mdca_grading.py selftest     （12 → 17，新增 A 路五条）
  palette 5           mdca_palette.py --selftest
  mdca A 路 7         mdca.py selftest
  check A 路 17       mdca.py check 300            ← 只跑边界算例，不跑随机回代

B 路（--plugins thermal,scaling）
  thermal 13          mdca_thermal.py --selftest
  thermal 插件 8      mdca_plugin_thermal.py
  scaling 插件 4      mdca_plugin_scaling.py
  mdca B 路 10        mdca.py selftest --plugins thermal,scaling
  check B 路 17+MC    mdca.py check 300 --plugins thermal,scaling  ← 回代 + 边界
```

### 8.2 本次自测抓到的三个真问题

```text
① Case.plugins 默认 [] 被当成「显式声明空」
   → 来源永远显示 case，环境变量与默认两级走不到
   → 修：默认改 None，区分「没写」与「写了空」

② A 路冒烟 ⑥ 用手工 Case 绕过契约校验 → 假绿
   → 修：改成从原始 dict 走 parse_case_doc

③ mdca_schema.py 的 REQUIRED_PARAMS 仍是 5 项
   → 与「A 路不要求热阻」直接矛盾，真实 JSON 会被拦
   → 修：收窄为 3 项，并加自测锁定
```

### 8.3 边界算例的 A 路期望值怎么来的

**先按规则推导、再跑一遍核对**，不是照着运行结果抄：

```text
BLOCK  ⇒ AUDIT_BLOCKED
REVIEW ⇒ AUDIT_REVIEW
其余   ⇒ AUDIT_PASS
```

推导与实际逐条一致。其中四条「推导认为看不见」的（§4.1）恰好是最有价值的部分：它们标出了 A 路的口径边界。

---

## 9. 已知代价与没做的事

```text
未做  ① A 路没有反推器——现在的 AUDIT_PASS 只覆盖口径层，不含任何自洽性判断
未做  ② B 路 blocked 仍用 SOLVER_UNKNOWN（§7.1），两套状态词表并存
未做  ③ README 与 MDCA_项目说明.md 仍以 B 路为主线（路线图第 5 步才重写）
未做  ④ mdca_batch.py 仍只完成了「显式传插件」这一步，
       还没改造成「批量审查外部候选」（路线图第 3 步的 ④）
欠账  ⑤ schema 版本号没升（§6.3），等作者裁决
遗留  ⑥ COUPLING_FAILED 时吞掉 D1（§7.2），已记录，建议第 3 步顺手修
```

---

## 10. 复现

```powershell
cd E:\deepseek\mdca

# A 路：不产生温度
python mdca.py audit cases/
python mdca.py plugins
python mdca.py selftest
python mdca.py check 300

# B 路：显式叠加温度与缩放
python mdca.py audit cases/ --plugins thermal,scaling
python mdca.py selftest --plugins thermal,scaling
python mdca.py check 300 --plugins thermal,scaling

# 零差异证明
python baseline_b_road.py --diff      # 期望：差异 0 个

# 一键全量
powershell -NoProfile -ExecutionPolicy Bypass -File run_tests.ps1
```
