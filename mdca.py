#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 统一入口：A 路审计（默认）+ B 路插件（可选）
=================================================

定位（`项目复盘与路线转向.md`）：

    A 路（主线）  不产生数据，只判断别人给的数据能不能用
    B 路（插件）  自己算温度，需要独立热阻与实测，**默认关闭**

本文件只做编排，不写物理：

    mdca_palette.py          口径层（字段定义、冲突规则、Markdown 扫描）
    mdca_schema.py           契约层（结构校验）
    mdca_provenance.py       溯源层（sha256、代码指纹、append-only 账本）
    mdca_grading.py          分级层（六状态 → 四档）
    mdca_plugins.py          插件注册表与开关（无业务逻辑）
    mdca_plugin_thermal.py   B 路：两热阻 / 三节点 / 区间 / 压力测试
    mdca_plugin_scaling.py   B 路：几何缩放律 → Rws/Rsa/Rrs
    mdca.py                  编排（校验 → 拦截 → 计算 → 判定 → 报告）  ← 本文件

A 路的结论词汇（与温度无关）：

    AUDIT_PASS     口径、契约、溯源全部过审
    AUDIT_REVIEW   有需人工确认项（超域 / 外推 / 未验证但声明保守 / 基准数据）
    AUDIT_BLOCKED  有硬拦截（契约 / 单位 / 口径冲突 / 哈希 / 负值 / 区间）

**AUDIT_PASS 只表示「这批数据没有明显口径毛病」，不表示「数据可信」，
更不表示「这台电机合格」。** 自洽性判断要等反推器（路线图第 3 步）。

用法：

    python mdca.py audit cases/                          # A 路（默认，不算温度）
    python mdca.py audit cases/ --plugins thermal,scaling  # B 路：叠加温度计算
    python mdca.py validate cases/joint_A_body_conduction.json
    python mdca.py palette <md文件或目录>                  # 口径审计（转发）
    python mdca.py plugins                               # 列插件与开关状态
    python mdca.py selftest                              # A 路冒烟
    python mdca.py selftest --plugins thermal            # A 路 + B 路冒烟
    python mdca.py check 300                             # A 路边界算例
    python mdca.py check 300 --plugins thermal,scaling   # A + B 路全量自检
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdca_palette import MIX_BLOCKING, PALETTE_FIELDS, Palette  # noqa: E402
from mdca_plugins import (  # noqa: E402
    DEFAULT_PLUGINS,
    PluginError,
    all_names as plugin_names,
    describe as describe_plugins,
    from_case as plugins_from_case,
    resolve as resolve_plugins,
    run as run_plugin,
)
from mdca_provenance import (  # noqa: E402
    CONSTRAINT_VERSION,
    LEDGER_NAME,
    Ledger,
    LedgerEntry,
    check_sources,
    code_fingerprint,
    fingerprint_digest,
    now_iso,
    sha256_bytes,
    sha256_file,
)
from mdca_schema import read_jsonl, validate_document  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


SCHEMA_VERSION = "1.0"
OUT_DIR = Path(__file__).resolve().parent / "out"

# A 路必需参数（审计只要求「有损耗、有环境」，不要求热阻）
REQUIRED_A: Dict[str, str] = {
    "Pcu": "W",       # 参考温度下铜损
    "Pfe": "W",       # 注入铁芯的其它损耗
    "Ta": "C",        # 环境温度
}
# 可选参数：不要求存在，但一旦出现，单位必须对（V1_unit）
OPTIONAL: Dict[str, str] = {
    "Rws": "K/W",     # A 路可选：给了就参与反推对照（路线图第 3 步）
    "Rsa": "K/W",
    "Rrs": "K/W",
    "Peddy": "W",
    "alpha": "1/K",
    "Tref": "C",
    "Tw_max": "C",
    "Tr_max": "C",
}
INTERVAL_CAPABLE = ("Rws", "Rsa", "Pcu", "Pfe", "Ta", "alpha")

# A 路三状态
AUDIT_PASS = "AUDIT_PASS"
AUDIT_REVIEW = "AUDIT_REVIEW"
AUDIT_BLOCKED = "AUDIT_BLOCKED"

STATUS_RANK = {
    "POINT_PASS": 0, "BOX_PASS": 0, "AUDIT_PASS": 0,
    "MODEL_REVIEW": 1, "SOLVER_UNKNOWN": 1, "AUDIT_REVIEW": 1,
    "MODEL_FAIL": 2, "COUPLING_FAILED": 2, "AUDIT_BLOCKED": 2,
}


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class Param:
    key: str
    value: float
    unit: str
    palette: Palette
    verified: bool = True
    conservatism: Optional[str] = None
    source: str = ""
    interval: Optional[Tuple[float, float]] = None

    def as_dict(self) -> Dict[str, Any]:
        out = {"value": self.value, "unit": self.unit,
               "palette": self.palette.as_dict(),
               "verified": self.verified, "source": self.source}
        if self.conservatism:
            out["conservatism"] = self.conservatism
        if self.interval:
            out["interval"] = list(self.interval)
        return out


@dataclass
class Issue:
    code: str
    severity: str          # BLOCK / REVIEW / WARN / INFO
    message: str
    params: Tuple[str, ...] = ()
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Case:
    case_id: str
    description: str
    params: Dict[str, Param]
    limits: Dict[str, Any]
    calibration: Dict[str, Any]
    domain: Dict[str, Any]
    scaling: Dict[str, Any] = field(default_factory=dict)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    source_file: str = ""
    contract_errors: List[str] = field(default_factory=list)
    source_type: str = ""
    benchmark: str = ""
    plugins: Optional[List[str]] = None   # case 里显式声明的插件；None = 文件里没这个字段
                                          # 不用 [] 作默认：[] 是「显式关闭全部」，
                                          # 与「没声明」必须区分，否则 env/默认两级永远走不到

    def get(self, key: str) -> Optional[Param]:
        return self.params.get(key)

    def val(self, key: str, default: float = 0.0) -> float:
        p = self.params.get(key)
        return p.value if p else default


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------


def _mk_param(key: str, raw: Dict[str, Any]) -> Param:
    pal_raw = raw.get("palette", {}) or {}
    pal = Palette(**{k: v for k, v in pal_raw.items() if k in PALETTE_FIELDS})
    interval = raw.get("interval")
    interval_t = None
    if isinstance(interval, (list, tuple)) and len(interval) == 2:
        interval_t = (float(interval[0]), float(interval[1]))
    return Param(
        key=key,
        value=float(raw["value"]),
        unit=str(raw.get("unit", "")),
        palette=pal,
        verified=bool(raw.get("verified", True)),
        conservatism=raw.get("conservatism"),
        source=str(raw.get("source", "")),
        interval=interval_t,
    )


def load_case(path: Path) -> Case:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    return parse_case_doc(raw, str(path))


def parse_case_doc(raw: Dict[str, Any], source: str) -> Case:
    """从 dict 构造 Case。JSONL 与单文件共用此入口。"""
    contract = validate_document(raw)
    params: Dict[str, Param] = {}
    for k, v in (raw.get("params") or {}).items():
        try:
            params[k] = _mk_param(k, v)
        except Exception as e:
            contract.append(f"params.{k} 解析失败: {type(e).__name__}")
    decl: Optional[List[str]] = None
    if raw.get("plugins") is not None:
        try:
            decl = plugins_from_case(raw) or []
        except PluginError as e:
            decl = None
            contract.append(f"plugins 字段非法：{e}")
    return Case(
        case_id=str(raw.get("case_id") or Path(source).stem),
        description=str(raw.get("description", "")),
        params=params,
        limits=raw.get("limits") or {},
        calibration=raw.get("calibration") or {},
        domain=raw.get("domain") or {},
        scaling=raw.get("scaling") or {},
        sources=raw.get("sources") or [],
        source_file=source,
        contract_errors=contract,
        source_type=str(raw.get("source_type") or ""),
        benchmark=str(raw.get("benchmark") or ""),
        plugins=decl,
    )


# --------------------------------------------------------------------------
# 插件结果 → 审计 Issue
# --------------------------------------------------------------------------


_SEVERITIES = ("BLOCK", "REVIEW", "WARN", "INFO")


def _to_issues(raw: Iterable[Dict[str, Any]]) -> List[Issue]:
    out: List[Issue] = []
    for d in raw or []:
        sev = str(d.get("severity", "INFO")).upper()
        out.append(Issue(
            code=str(d.get("code") or "B0_plugin_issue"),
            severity=sev if sev in _SEVERITIES else "INFO",
            message=str(d.get("message", "")),
            params=tuple(d.get("params") or ()),
            detail=dict(d.get("detail") or {}),
        ))
    return out


def _run_plugin_into(name: str, case: Case, issues: List[Issue]) -> Dict[str, Any]:
    """跑一个插件，把它的 issues 并入审计 Issue 表。

    约定：插件被**显式启用**却跑不起来，一定记 BLOCK，不静默降级。
    """
    out = run_plugin(name, case)
    got = _to_issues(out.get("issues") or [])
    issues.extend(got)
    if not out.get("ok") and not any(i.severity == "BLOCK" for i in got):
        issues.append(Issue(
            "B0_plugin_failed", "BLOCK",
            f"插件 {name} 运行失败：{out.get('reason')}",
            (), {"plugin": name, "traceback": out.get("traceback")}))
    return out


# --------------------------------------------------------------------------
# 校验与硬拦截（方案 7.2）
# --------------------------------------------------------------------------


def validate(case: Case) -> List[Issue]:
    issues: List[Issue] = []

    # V1 必需参数缺失 / 单位不符（A 路口径：只要损耗与环境）
    for key, unit in REQUIRED_A.items():
        p = case.params.get(key)
        if p is None:
            issues.append(Issue("V1_missing", "BLOCK", f"缺少必需参数 {key}", (key,)))
            continue
        if p.unit != unit:
            issues.append(Issue(
                "V1_unit", "BLOCK",
                f"{key} 单位应为 {unit}，实际 {p.unit or '(空)'}", (key,)))

    # V1b 可选参数若出现，单位也必须对（A 路不要求存在，但不能错）
    # code 仍用 V1_unit：语义是「单位不符」，变的是「是否必须存在」，不是严重性。
    for key, unit in OPTIONAL.items():
        p = case.params.get(key)
        if p is not None and p.unit and p.unit != unit:
            issues.append(Issue(
                "V1_unit", "BLOCK",
                f"{key} 单位应为 {unit}，实际 {p.unit}", (key,)))

    # V2 未验证数据硬拦截（方案 7.2 的核心）
    # benchmark 例外：基准数据的用途是测试规则，不是产生推荐。若没有这个例外，
    # 任何由文献反推的基准都会在第一步就被拦死，其余规则根本没机会被测试。
    # 例外只放宽「是否允许计算」，不放宽「结论等级」——benchmark 的结论仍强制 REVIEW。
    is_benchmark = bool(case.benchmark)
    for key, p in case.params.items():
        if p.verified:
            continue
        if p.conservatism == "conservative":
            issues.append(Issue(
                "V2_conservative", "INFO",
                f"{key} 是未验证数据，已声明按保守方向使用（结论偏安全，须在报告注明）",
                (key,), {"source": p.source}))
        elif p.conservatism == "optimistic":
            issues.append(Issue(
                "V2_optimistic", "BLOCK",
                f"{key} 是未验证数据且声明为乐观方向：不得进入合格性判断",
                (key,), {"source": p.source}))
        elif is_benchmark:
            issues.append(Issue(
                "V2_benchmark_exempt", "REVIEW",
                f"{key} 未验证（benchmark 基准数据）：允许计算，但结论强制 REVIEW",
                (key,), {"source": p.source}))
        else:
            issues.append(Issue(
                "V2_unverified", "BLOCK",
                f"{key} 未验证且未声明 conservatism：按方案 7.2 拒绝进入计算",
                (key,), {"source": p.source}))

    # V3 参数间口径冲突
    keys = sorted(case.params)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = case.params[keys[i]], case.params[keys[j]]
            for f, av, bv in a.palette.conflicts_with(b.palette, MIX_BLOCKING):
                issues.append(Issue(
                    "V3_palette_mix", "BLOCK",
                    f"参数 {a.key}({f}={av}) 与 {b.key}({f}={bv}) 口径冲突",
                    (a.key, b.key), {"field": f}))

    # V4 未标记口径
    for key, p in case.params.items():
        if not p.palette.is_tagged():
            issues.append(Issue(
                "V4_untagged", "WARN",
                f"{key} 未声明任何口径字段", (key,)))

    # V5 区间合法性
    for key in INTERVAL_CAPABLE:
        p = case.params.get(key)
        if p is None or p.interval is None:
            continue
        lo, hi = p.interval
        if lo > hi:
            issues.append(Issue("V5_interval", "BLOCK",
                                f"{key} 区间下界 {lo} 大于上界 {hi}", (key,)))
        elif not (lo <= p.value <= hi):
            issues.append(Issue("V5_interval", "WARN",
                                f"{key} 点值 {p.value} 不在区间 [{lo},{hi}] 内", (key,)))

    # V6 物理正值
    for key in ("Rws", "Rsa"):
        p = case.params.get(key)
        if p is not None and p.value <= 0:
            issues.append(Issue("V6_positive", "BLOCK",
                                f"{key} 必须为正，实际 {p.value}", (key,)))
    for key in ("Pcu", "Pfe"):
        p = case.params.get(key)
        if p is not None and p.value < 0:
            issues.append(Issue("V6_positive", "BLOCK",
                                f"{key} 不得为负，实际 {p.value}", (key,)))
    return issues


def check_domain(case: Case) -> List[Issue]:
    """方案 11.10：候选超出校准适用域 ⇒ REVIEW。

    domain 支持两种写法：
        [lo, hi]                                   旧形式，无参考温度声明
        {"interval": [lo, hi], "temp_basis": "20C"} 新形式，可做机器校验

    参考温度一致性（P2_temp_basis，2026-11-22 新增）：
        domain 的区间与 params 的值若不在同一参考温度下，超域判断无意义。
        曾出现的错误：Pcu 已折算到 20℃（372.9 W），domain 却按工况值写（540~600 W），
        于是把合格候选误判为超域。这条就是把它从文档约定改成机器检查。
    """
    issues: List[Issue] = []
    domain = case.domain or {}
    if not domain:
        return issues
    for key, spec in domain.items():
        if isinstance(spec, dict):
            rng = spec.get("interval")
            dom_tb = spec.get("temp_basis")
        else:
            rng = spec
            dom_tb = None
        if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
            continue
        p = case.params.get(key)
        if p is None:
            continue

        # ---- 参考温度一致性（新） ----
        # palette 是 Palette 数据类，不是 dict —— 用 getattr 读，不要 isinstance(dict)
        prm_tb = getattr(p.palette, "temp_basis", None) if p.palette is not None else None
        if prm_tb or dom_tb:
            if not prm_tb or not dom_tb:
                issues.append(Issue(
                    "P2_temp_basis", "BLOCK",
                    f"{key} 的 domain 与 params 参考温度必须同时声明："
                    f"params={'已声明 ' + prm_tb if prm_tb else '未声明'}，"
                    f"domain={'已声明 ' + dom_tb if dom_tb else '未声明'}",
                    (key,), {"params_temp_basis": prm_tb, "domain_temp_basis": dom_tb}))
            elif prm_tb != dom_tb:
                issues.append(Issue(
                    "P2_temp_basis", "BLOCK",
                    f"{key} 的 domain 参考温度 {dom_tb} 与 params 的 {prm_tb} 不一致，"
                    f"超域判断无意义",
                    (key,), {"params_temp_basis": prm_tb, "domain_temp_basis": dom_tb}))
            continue  # 口径未对齐时不继续做超域判断，避免给出误导性结论

        # 用 value 与 interval 的并集：只看 interval 会漏掉「点值越界但区间没改」的情况，
        # 只看 value 会漏掉「区间端点越界」。两者都要覆盖。
        lo_t, hi_t = p.value, p.value
        if p.interval:
            lo_t = min(lo_t, p.interval[0])
            hi_t = max(hi_t, p.interval[1])
        target = (lo_t, hi_t)
        lo, hi = float(rng[0]), float(rng[1])
        if target[0] < lo or target[1] > hi:
            issues.append(Issue(
                "D1_out_of_domain", "REVIEW",
                f"{key} 取值 [{target[0]:g},{target[1]:g}] 超出校准域 [{lo:g},{hi:g}]",
                (key,), {"domain": [lo, hi], "target": list(target)}))
    return issues


# --------------------------------------------------------------------------
# 审计
# --------------------------------------------------------------------------


def _audit_only_status(issues: List[Issue]) -> Dict[str, Any]:
    """A 路结论：只看口径/契约/溯源，不产生温度。"""
    review = [i for i in issues if i.severity == "REVIEW"]
    if review:
        return {"status": AUDIT_REVIEW,
                "reasons": sorted({i.code for i in review})}
    return {"status": AUDIT_PASS, "reasons": []}


def audit(case: Case, plugins: Any = None) -> Dict[str, Any]:
    """审计一个算例。

    plugins=None 时按 4 级优先级解析（默认一个都不开 = A 路）。
    plugins=("thermal",) 或 "thermal,scaling" 时才运行 B 路能力。
    """
    issues: List[Issue] = []

    # ---- 插件解析 ----
    try:
        active, plugin_source = resolve_plugins(explicit=plugins, case=case)
    except PluginError as e:
        active, plugin_source = [], "error"
        issues.append(Issue("B0_plugin_name", "BLOCK", str(e), ()))

    if plugins is not None and case.plugins:
        # CLI 显式覆盖了 case 的声明：留痕，免得「我明明在文件里写了插件」变成悬案
        missing = [p for p in case.plugins if p not in active]
        if missing:
            issues.append(Issue(
                "B1_plugin_overridden", "INFO",
                f"case 声明了插件 {', '.join(missing)}，被命令行显式覆盖为关闭",
                tuple(missing), {"active": list(active)}))

    # ---- scaling（可选）：先跑，因为它改写 Rws/Rsa/Rrs ----
    scaling_result: Optional[Dict[str, Any]] = None
    if "scaling" in active:
        out = _run_plugin_into("scaling", case, issues)
        if out.get("ok"):
            for k, raw in (out.get("answer", {}).get("derived") or {}).items():
                try:
                    case.params[k] = _mk_param(k, raw)
                except Exception as e:  # noqa: BLE001
                    issues.append(Issue(
                        "B0_plugin_bad_param", "BLOCK",
                        f"scaling 插件给出的 {k} 无法解析：{type(e).__name__}", (k,)))
            scaling_result = out.get("answer", {}).get("result")
    elif case.scaling:
        issues.append(Issue(
            "B1_scaling_skipped", "INFO",
            "case 有 scaling 段但 scaling 插件未启用：Rws/Rsa/Rrs 用 case 里的原值，不缩放",
            ("Rws", "Rsa", "Rrs")))

    # ---- 溯源：case 哈希 + 外部来源哈希校验（方案 7.2） ----
    base_dir = Path(case.source_file).parent if case.source_file else Path.cwd()
    src_checks = check_sources(case.sources, base_dir)
    src_bad = [s for s in src_checks if not s.ok]
    # 哈希基于「规范化审计输入」而非文件字节：
    # 同样的内容无论是 .json 还是 .jsonl 的一行，指纹必须相同，
    # 否则账本会把"换了个文件格式"误报成"输入被修改"。
    # 插件列表也算输入的一部分：A 路与 B 路的审计输入本来就不同，
    # 混用同一个 hash 会让账本把"换了运行模式"误报成"输入被改"。
    payload = json.dumps({
        "case_id": case.case_id,
        "params": {k: v.as_dict() for k, v in sorted(case.params.items())},
        "limits": case.limits,
        "calibration": case.calibration,
        "domain": case.domain,
        "scaling": case.scaling,
        "plugins": sorted(active),
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")
    case_hash = sha256_bytes(payload)
    fp = code_fingerprint()
    code_digest = fingerprint_digest(fp)

    issues = issues + validate(case) + check_domain(case)
    if case.contract_errors:
        issues.append(Issue(
            "P0_contract", "BLOCK",
            "不符合候选契约（schema 1.0）：" + "；".join(case.contract_errors[:4]),
            (), {"errors": case.contract_errors}))
    if src_bad:
        issues.append(Issue(
            "P1_source_hash", "BLOCK",
            "外部来源哈希不符或文件缺失：" + "；".join(f"{s.path}（{s.reason}）" for s in src_bad),
            (), {"sources": [s.__dict__ for s in src_bad]}))
    blocked = [i for i in issues if i.severity == "BLOCK"]
    review = [i for i in issues if i.severity == "REVIEW"]

    delta_cal = float(case.calibration.get("delta_cal_K", 0.0) or 0.0)
    cal_present = bool(case.calibration.get("source_type"))

    result: Dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "case_id": case.case_id,
        "description": case.description,
        "source_file": case.source_file,
        "plugins": {
            "active": list(active),
            "source": plugin_source,
            "declared_in_case": list(case.plugins or []),
            "available": plugin_names(),
        },
        "params": {k: v.as_dict() for k, v in case.params.items()},
        "limits": case.limits,
        "calibration": {
            "delta_cal_K": delta_cal,
            "source_type": case.calibration.get("source_type"),
            "fitted_on_same_points": case.calibration.get("fitted_on_same_points"),
            "model_similarity_assumed": case.calibration.get("model_similarity_assumed"),
            "present": cal_present,
        },
        "validation": {
            "blocked": bool(blocked),
            "issues": [i.__dict__ for i in issues],
            "block_count": len(blocked),
            "review_count": len(review),
        },
        "scaling": scaling_result,
        "provenance": {
            "case_hash": case_hash,
            "code_digest": code_digest,
            "code_fingerprint": fp,
            "constraint_version": CONSTRAINT_VERSION,
            "sources": [
                {"path": s.path, "expected": s.expected, "actual": s.actual,
                 "ok": s.ok, "reason": s.reason}
                for s in src_checks
            ],
            "audited_at": now_iso(),
        },
        "disclaimer": (
            "A-road: palette/contract/provenance audit only; no temperature is produced."
            if "thermal" not in active else
            "Model-level constraint audit only; not physical validation."
        ),
    }

    # ---- 硬拦截：不计算温度 ----
    if blocked:
        result["coupling"] = {
            "status": "NOT_RUN",
            "reason": "blocked_by_validation",
        }
        result["temperature"] = None
        # A 路：输入被审出硬伤，这批数据不可用
        # B 路：沿用旧词 SOLVER_UNKNOWN（热求解因输入被拦而未运行），
        #       以保证 v0.2 的 16 条边界算例期望值仍然成立（见第 2 步变更记录）
        result["audit"] = {
            "overall_status": AUDIT_BLOCKED if "thermal" not in active else "SOLVER_UNKNOWN",
            "reasons": ["input_blocked: " + ", ".join(sorted({i.code for i in blocked}))],
        }
        return result

    # ---- B 路：插件跑温度 ----
    if "thermal" in active:
        tout = run_plugin("thermal", case)
        plugin_issues = _to_issues(tout.get("issues") or [])
        issues.extend(plugin_issues)
        result["validation"]["issues"] = [i.__dict__ for i in issues]

        if plugin_issues:
            pb = [i for i in plugin_issues if i.severity == "BLOCK"]
            if pb:
                result["validation"]["blocked"] = True
                result["validation"]["block_count"] = len(pb)
                result["coupling"] = {"status": "NOT_RUN",
                                      "reason": "plugin_rejected_input"}
                result["temperature"] = None
                result["audit"] = {
                    "overall_status": "SOLVER_UNKNOWN",
                    "reasons": ["plugin_blocked: "
                                + ", ".join(sorted({i.code for i in pb}))],
                }
                return result

        if not tout.get("ok"):
            result["validation"]["blocked"] = True
            result["validation"]["block_count"] = 1
            result["coupling"] = {"status": "NOT_RUN", "reason": "plugin_failed"}
            result["temperature"] = None
            result["audit"] = {
                "overall_status": "SOLVER_UNKNOWN",
                "reasons": [f"plugin_failed: {tout.get('reason')}"],
            }
            return result

        answer = tout.get("answer") or {}
        for k in ("model", "coupling", "temperature", "verdict_point",
                  "verdict_box", "box_coupling", "stress_tests"):
            if k in answer:
                result[k] = answer[k]

        overall = str(answer.get("verdict_status", "SOLVER_UNKNOWN"))
        reasons = list(answer.get("verdict_reasons") or [])

        # 与 v0.2 逐行等价：耦合失败时早退，不叠加域/校准降级。
        # （v0.2 就是这样：早退分支只带 divergence 原因。这里先保持等价，
        #   「耦合失败时是否该同时报出输入超域」留到第 3 步一起改。）
        if overall == "COUPLING_FAILED":
            result["audit"] = {
                "overall_status": overall,
                "reasons": sorted(set(reasons), key=str),
            }
            return result

        # 方案 6.1：无校准 ⇒ 不得推荐
        if not cal_present and overall in ("POINT_PASS", "BOX_PASS"):
            overall = "MODEL_REVIEW"
            reasons.append("calibration_missing")

        # 方案 11.10：超适用域 ⇒ 不得推荐
        if review:
            if overall in ("POINT_PASS", "BOX_PASS"):
                overall = "MODEL_REVIEW"
            reasons.extend(sorted({i.code for i in review}))

        result["audit"] = {
            "overall_status": overall,
            "reasons": sorted(set(reasons), key=str),
        }
        return result

    # ---- A 路：不给温度，只给口径/契约/溯源结论 ----
    result["coupling"] = {
        "status": "DISABLED",
        "reason": "thermal plugin not enabled (A-road mode)",
    }
    result["temperature"] = None
    v = _audit_only_status(issues)
    result["audit"] = {
        "overall_status": v["status"],
        "reasons": v["reasons"],
        "note": ("AUDIT_PASS 表示口径/契约/溯源过审，不表示数据可信，"
                 "也不表示任何电机的合格性；自洽性判断待反推器（路线图第 3 步）。"),
    }
    return result


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------


def print_report(o: Dict[str, Any]) -> None:
    c = o.get("coupling") or {}
    t = o.get("temperature") or {}
    a = o.get("audit") or {}
    v = o.get("validation") or {}
    p = o.get("plugins") or {}
    print("-" * 78)
    print(f"算例：{o['case_id']}   {o.get('description', '')}")
    act = p.get("active") or []
    print(f"  插件：{', '.join(act) if act else '(无，A 路)'}"
          f"   来源={p.get('source')}")
    print(f"  校验：{'BLOCKED' if v.get('blocked') else 'OK'}"
          f"（block {v.get('block_count', 0)} / review {v.get('review_count', 0)}）")
    for i in v.get("issues", []):
        if i["severity"] in ("BLOCK", "REVIEW"):
            print(f"    [{i['severity']}] {i['code']}: {i['message']}")
    if c.get("status") == "NOT_RUN":
        print(f"  耦合：未运行（{c.get('reason')}）")
        print(f"  总体结论：{a.get('overall_status')}  {a.get('reasons')}")
        return
    if c.get("status") == "DISABLED":
        print("  温度：未计算（A 路不产生温度）")
        print(f"  总体结论：{a.get('overall_status')}  {a.get('reasons')}")
        note = a.get("note")
        if note:
            print(f"  注：{note}")
        return
    print(f"  耦合：{c.get('status')}  g={c.get('g')}  iters={c.get('iterations')}")
    if t.get("winding_point_C") is not None:
        print(f"  温度：点值 {t['winding_point_C']}℃"
              f"  加裕度 {t.get('winding_corrected_C')}℃", end="")
        iv = t.get("winding_corrected_interval_C")
        if iv:
            print(f"  区间 [{iv[0]}, {iv[1]}]℃")
        else:
            print()
    st = o.get("stress_tests") or {}
    if st.get("results"):
        bad = [k for k, val in st["results"].items() if val != "ok"]
        print(f"  压力测试：{len(st['results'])} 项，超限 {len(bad)} 项"
              + (f" → {bad}" if bad else ""))
    vp = o.get("verdict_point") or {}
    if vp.get("note"):
        print(f"  注：{vp['note']}")
    if t.get("rotor_point_C") is not None:
        print(f"  转子：{t['rotor_point_C']}℃（磁钢上限 "
              f"{(o.get('model') or {}).get('Tr_max_C', 150)}℃）")
    print(f"  总体结论：{a.get('overall_status')}  {a.get('reasons')}")


def _load_cases(f: Path) -> List[Case]:
    """一个文件 → 一批 Case。.json 一文件一例；.jsonl 每行一例（单行失败不影响其他行）。"""
    if f.suffix == ".jsonl":
        out: List[Case] = []
        for ln, doc, err in read_jsonl(f):
            if err:
                print(f"[跳过] {f.name}:{ln} 解析失败 {err}")
                continue
            out.append(parse_case_doc(doc, f"{f.name}:{ln}"))
        return out
    return [load_case(f)]


# --------------------------------------------------------------------------
# 命令
# --------------------------------------------------------------------------


def cmd_audit(paths: List[str], plugins: Any = None) -> int:
    files: List[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            files.extend(sorted(pp.glob("*.json")))
            files.extend(sorted(pp.glob("*.jsonl")))
        elif pp.exists():
            files.append(pp)
        else:
            print(f"[跳过] 不存在：{p}")
    if not files:
        print("没有找到算例文件（*.json）")
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(OUT_DIR / LEDGER_NAME)
    reports = []
    print("=" * 78)
    print("MDCA 审计报告  v1.0")
    print("=" * 78)
    all_cases: List[Case] = []
    for f in files:
        try:
            all_cases.extend(_load_cases(f))
        except Exception as e:
            print(f"[失败] {f.name}: {type(e).__name__}: {e}")
    for case in all_cases:
        o = audit(case, plugins=plugins)

        # 账本：只追加，改过的输入会留下"变更"痕迹（方案 7.2）
        prov = o["provenance"]
        prev = ledger.detect_change(o["case_id"], prov["case_hash"])
        if prev:
            print(f"  [变更] {o['case_id']}：输入哈希与上次不同"
                  f"（上次 {str(prev.get('case_hash'))[:12]} → 本次 {prov['case_hash'][:12]}），"
                  f"旧记录已保留")
            o["audit"]["reasons"] = sorted(
                set(o["audit"]["reasons"]) | {"input_changed_since_last_audit"}, key=str)
        ledger.append(LedgerEntry(
            ts=prov["audited_at"], case_id=o["case_id"], case_hash=prov["case_hash"],
            status=o["audit"]["overall_status"], code_digest=prov["code_digest"],
            constraint_version=CONSTRAINT_VERSION,
            delta_cal_K=float((o.get("calibration") or {}).get("delta_cal_K") or 0.0),
        ))

        print_report(o)
        reports.append(o)

    out = OUT_DIR / "audit.json"
    payload = {"schema": SCHEMA_VERSION, "reports": reports} if len(reports) > 1 else reports[0]
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("-" * 78)
    if reports:
        print("汇总：")
        for o in reports:
            print(f"  {o['case_id']:<28}{o['audit']['overall_status']}")
    print(f"JSON：{out}")
    bad = [o for o in reports
           if o["audit"]["overall_status"] in ("MODEL_FAIL", "COUPLING_FAILED")]
    return 0 if not bad else 1


def cmd_validate(paths: List[str]) -> int:
    files = [Path(p) for p in paths]
    rc = 0
    for f in files:
        case = load_case(f)
        issues = validate(case) + check_domain(case)
        blocks = [i for i in issues if i.severity == "BLOCK"]
        print("=" * 78)
        print(f"校验：{f.name}  ->  {'BLOCKED' if blocks else 'PASS'}")
        for i in issues:
            print(f"  [{i.severity}] {i.code}: {i.message}")
        if blocks:
            rc = 1
    return rc


def cmd_palette(args: List[str]) -> int:
    import subprocess
    here = Path(__file__).resolve().parent
    return subprocess.call([sys.executable, str(here / "mdca_palette.py"), "--scan"] + args)


def cmd_plugins() -> int:
    print("=" * 78)
    print("MDCA 插件")
    print("=" * 78)
    for d in describe_plugins():
        print(f"  {d['name']:<10}{d['road']} 路  默认{'开' if d['default_enabled'] else '关'}"
              f"  {d['module']:<24}{d['summary']}")
    print()
    print(f"默认启用：{list(DEFAULT_PLUGINS) or '(无，即 A 路)'}")
    print("启用方式：--plugins thermal,scaling  或 case 里 \"plugins\": [\"thermal\"]")
    print("        或环境变量 MDCA_PLUGINS=thermal,scaling")
    return 0


def cmd_selftest(plugins: Any = None) -> int:
    """A 路冒烟；带 --plugins 时追加 B 路冒烟。"""
    ok = True
    try:
        active, psource = resolve_plugins(explicit=plugins)
    except PluginError as e:
        print(f"插件参数错误：{e}")
        return 2

    def base_case(cid: str, *, keep=("Rws", "Rsa", "Pcu", "Pfe", "Ta")) -> Case:
        vals = {"Rws": (1.16, "K/W"), "Rsa": (2.03, "K/W"), "Pcu": (13.6, "W"),
                "Pfe": (5.33, "W"), "Ta": (40.0, "C")}
        ps = {k: Param(k, vals[k][0], vals[k][1], Palette(method="analytic"))
              for k in keep}
        return Case(cid, "", ps, {"Tw_max": 155.0}, {}, {})

    print("=== mdca.py 冒烟测试（A 路）===")

    # B 路被拦时沿用旧词 SOLVER_UNKNOWN，A 路用 AUDIT_BLOCKED。
    # 这条差异是刻意的（见第 2 步变更记录），所以测试要按路断言，不能只断言「被拦」。
    expect_block = "SOLVER_UNKNOWN" if "thermal" in active else AUDIT_BLOCKED

    # 1) 缺必需参数 → BLOCK
    c = Case("t1", "", {k: Param(k, 1.0, u, Palette(method="analytic"))
                        for k, u in list(REQUIRED_A.items())[:-1]},
             {}, {}, {})
    o = audit(c, plugins=plugins)
    r = o["audit"]["overall_status"] == expect_block
    print(f"  ① 缺必需参数(Pfe/Ta) → 拦截        {'PASS' if r else 'FAIL'}"
          f"（{o['audit']['overall_status']}）")
    ok &= r

    # 2) 未验证且无声明 → BLOCK
    ps = {k: Param(k, v, u, Palette(method="analytic"))
          for k, (v, u) in {"Rws": (1.16, "K/W"), "Rsa": (2.03, "K/W"),
                            "Pcu": (13.6, "W"), "Pfe": (5.33, "W"), "Ta": (40.0, "C")}.items()}
    ps["Pcu"].verified = False
    o = audit(Case("t2", "", ps, {"Tw_max": 155.0}, {}, {}), plugins=plugins)
    r = o["audit"]["overall_status"] == expect_block
    print(f"  ② 未验证数据无声明 → 拦截          {'PASS' if r else 'FAIL'}"
          f"（{o['audit']['overall_status']}）")
    ok &= r

    # 3) 未验证但声明保守 → A 路放行（不再要求算温度）
    ps2 = {k: Param(k, v, u, Palette(method="analytic"))
           for k, (v, u) in {"Rws": (1.16, "K/W"), "Rsa": (2.03, "K/W"),
                             "Pcu": (13.6, "W"), "Pfe": (5.33, "W"), "Ta": (40.0, "C")}.items()}
    ps2["Pfe"].verified = False
    ps2["Pfe"].conservatism = "conservative"
    o = audit(Case("t3", "", ps2, {"Tw_max": 155.0}, {}, {}), plugins=plugins)
    if "thermal" in active:
        r = o["coupling"]["status"] == "CONVERGED"
        label = "未验证但声明保守 → 放行(算温度)"
    else:
        r = o["audit"]["overall_status"] == AUDIT_PASS
        label = "未验证但声明保守 → 放行(仅审计)"
    print(f"  ③ {label:<28}{'PASS' if r else 'FAIL'}"
          f"（{o['audit']['overall_status']}）")
    ok &= r

    # 4) 口径冲突 → BLOCK
    ps3 = {k: Param(k, v, u, Palette(method="analytic"))
           for k, (v, u) in {"Rws": (1.16, "K/W"), "Rsa": (2.03, "K/W"),
                             "Pcu": (13.6, "W"), "Pfe": (5.33, "W"), "Ta": (40.0, "C")}.items()}
    ps3["Pcu"].palette = Palette(turns="6")
    ps3["Pfe"].palette = Palette(turns="8")
    o = audit(Case("t4", "", ps3, {"Tw_max": 155.0}, {}, {}), plugins=plugins)
    r = o["validation"]["blocked"]
    print(f"  ④ 口径冲突（6匝 vs 8匝）→ 拦截     {'PASS' if r else 'FAIL'}")
    ok &= r

    # 5) 超适用域 → REVIEW
    ps4 = {k: Param(k, v, u, Palette(method="analytic"))
           for k, (v, u) in {"Rws": (1.16, "K/W"), "Rsa": (2.03, "K/W"),
                             "Pcu": (13.6, "W"), "Pfe": (5.33, "W"), "Ta": (40.0, "C")}.items()}
    o = audit(Case("t5", "", ps4, {"Tw_max": 155.0},
                   {"delta_cal_K": 10.0, "source_type": "external_model_mismatch"},
                   {"Ta": [20.0, 30.0]}), plugins=plugins)
    r = "D1_out_of_domain" in o["audit"]["reasons"]
    print(f"  ⑤ 环境温度超适用域 → REVIEW        {'PASS' if r else 'FAIL'}"
          f"（{o['audit']['overall_status']}）")
    ok &= r

    # 6) A 路核心：没有热阻也能审（走真实 JSON 路径，会过契约校验）
    raw_no_r = {
        "schema": "1.0", "case_id": "t6",
        "params": {k: {"value": v, "unit": u, "palette": {"method": "analytic"}}
                   for k, (v, u) in {"Pcu": (13.6, "W"), "Pfe": (5.33, "W"),
                                     "Ta": (40.0, "C")}.items()},
        "limits": {"Tw_max": 155.0},
    }
    o = audit(parse_case_doc(raw_no_r, "t6"), plugins=plugins)
    if "thermal" in active:
        r = o["validation"]["blocked"] is True
        label = "缺 Rws/Rsa：B 路必须拦"
    else:
        r = (o["audit"]["overall_status"] == AUDIT_PASS
             and o["temperature"] is None
             and not o["validation"]["blocked"])
        label = "无热阻也能过契约 → A 路通"
    print(f"  ⑥ {label:<28}{'PASS' if r else 'FAIL'}"
          f"（{o['audit']['overall_status']}）")
    ok &= r

    # 7) A 路不产生温度
    o = audit(base_case("t7"), plugins=plugins)
    if "thermal" in active:
        r = o["temperature"] is not None
        label = "B 路：确实产出了温度"
    else:
        r = o["temperature"] is None and o["coupling"]["status"] == "DISABLED"
        label = "A 路：temperature 为 None"
    print(f"  ⑦ {label:<28}{'PASS' if r else 'FAIL'}")
    ok &= r

    if "thermal" in active:
        print()
        print("=== mdca.py 冒烟测试（B 路）===")
        # 8) 正常算例（有校准）→ 给出判定
        o = audit(base_case("t8"), plugins=plugins)
        r = o["audit"]["overall_status"] in ("POINT_PASS", "MODEL_FAIL", "MODEL_REVIEW")
        print(f"  ⑧ 有温度结论但缺校准 → 不给 PASS    {'PASS' if r else 'FAIL'}"
              f"（{o['audit']['overall_status']}）")
        ok &= r

        # 9) 带校准 + 三节点 → 节点数 3
        ps5 = {k: Param(k, v, u, Palette(method="analytic"))
               for k, (v, u) in {"Rws": (1.16, "K/W"), "Rsa": (2.03, "K/W"),
                                 "Pcu": (13.6, "W"), "Pfe": (5.33, "W"), "Ta": (40.0, "C")}.items()}
        ps5["Peddy"] = Param("Peddy", 15.24, "W", Palette(method="fem"))
        ps5["Rrs"] = Param("Rrs", 3.0, "K/W", Palette(method="analytic"))
        o = audit(Case("t9", "", ps5, {"Tw_max": 155.0, "Tr_max": 150.0},
                       {"delta_cal_K": 5.0, "source_type": "external_model_mismatch"},
                       {}), plugins=plugins)
        r = (o.get("model") or {}).get("nodes") == 3
        print(f"  ⑨ 带 Peddy → 自动走三节点         {'PASS' if r else 'FAIL'}"
              f"（nodes={(o.get('model') or {}).get('nodes')}）")
        ok &= r

        # 10) 插件被显式要求但缺参数 → 不许静默降级成 A 路
        o = audit(base_case("t10", keep=("Rws", "Pcu", "Pfe", "Ta")), plugins=plugins)
        r = o["validation"]["blocked"] and o["temperature"] is None
        print(f"  ⑩ 插件缺参数 → 拦截而非静默降级     {'PASS' if r else 'FAIL'}"
              f"（{o['audit']['overall_status']}）")
        ok &= r

    print(f"冒烟测试：{'全部通过' if ok else '有失败项'}")
    return 0 if ok else 1


def cmd_check(args: List[str], plugins: Any = None) -> int:
    """自检：随机数值回代（仅 B 路）+ 边界算例（A/B 各自的期望值）。"""
    try:
        active, _src = resolve_plugins(explicit=plugins)
    except PluginError as e:
        print(f"插件参数错误：{e}")
        return 2

    here = Path(__file__).resolve().parent
    n = 500
    if args:
        try:
            n = int(args[0])
        except ValueError:
            print(f"样本数无效：{args[0]}")
            return 2

    ok_all = True

    # 随机数值回代验证的是 thermal/scaling 的实现，属于 B 路自检
    if "thermal" in active:
        import mdca_checks
        rep = mdca_checks.run(n)
        mdca_checks.print_report(rep)
        ok_all = rep["ok"]
    else:
        print("=" * 78)
        print("A 路自检：跳过随机回代（它验证的是温度与缩放实现，属 B 路）")
        print("=" * 78)

    print()
    print("=" * 78)
    print("边界算例：输入层负例 + 结论层边界，每一条都必须命中预期")
    print(f"期望值来源：{'B 路 _expect' if 'thermal' in active else 'A 路 _expect_a'}")
    print("=" * 78)
    dirs = [here / "cases_negative", here / "cases_edge"]
    files: List[Path] = []
    for d in dirs:
        if d.exists():
            files.extend(sorted(d.glob("*.json")))
    if not files:
        print(f"未找到算例目录：{dirs}")
        return 1

    key = "_expect" if "thermal" in active else "_expect_a"
    hdr = f"{'文件':<34}{'期望':>17}{'实际':>16}{'结果':>7}"
    print(hdr)
    print("-" * len(hdr))
    missing_expect = 0
    for f in files:
        raw = json.loads(f.read_text(encoding="utf-8-sig"))
        expect = raw.get(key) or {}
        case = load_case(f)
        o = audit(case, plugins=plugins)
        status = o["audit"]["overall_status"]
        reasons = " ".join(o["audit"].get("reasons") or [])
        issues = " ".join(i["code"] for i in (o.get("validation") or {}).get("issues", []))
        blob = reasons + " " + issues

        want_status = expect.get("status")
        want_reason = expect.get("reason_contains")
        if want_status is None:
            missing_expect += 1
            hit = False
            print(f"{f.stem[:32]:<34}{'(-不在 ' + key + '-)':>17}{status:>16}{'MISS':>7}")
        else:
            hit = (status == want_status)
            if want_reason:
                hit = hit and (want_reason in blob)
            print(f"{f.stem[:32]:<34}{want_status:>17}{status:>16}"
                  f"{'PASS' if hit else 'FAIL':>7}")
        ok_all = ok_all and hit

    if missing_expect:
        print(f"\n有 {missing_expect} 个算例缺少 {key} 字段：无法判定")
        ok_all = False

    print()
    print(f"自检总体：{'全部通过' if ok_all else '有失败项（存在漏报）'}")
    return 0 if ok_all else 1


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def _split_plugins(argv: List[str]) -> Tuple[List[str], Optional[str]]:
    """剥出 --plugins / --plugins=x，其余顺序不变。"""
    out: List[str] = []
    plugins: Optional[str] = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--plugins":
            if i + 1 >= len(argv):
                raise PluginError("--plugins 需要一个参数（如 thermal,scaling 或 none）")
            plugins = argv[i + 1]
            i += 2
            continue
        if a.startswith("--plugins="):
            plugins = a.split("=", 1)[1]
            i += 1
            continue
        out.append(a)
        i += 1
    return out, plugins


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 2

    try:
        argv, plugins = _split_plugins(argv)
    except PluginError as e:
        print(f"参数错误：{e}")
        return 2

    if not argv:
        print(__doc__)
        return 2

    cmd, rest = argv[0], argv[1:]
    if cmd == "audit":
        return cmd_audit(rest or ["cases"], plugins=plugins)
    if cmd == "validate":
        return cmd_validate(rest)
    if cmd == "palette":
        return cmd_palette(rest)
    if cmd == "plugins":
        return cmd_plugins()
    if cmd == "selftest":
        return cmd_selftest(plugins)
    if cmd == "check":
        return cmd_check(rest, plugins)
    print(f"未知命令：{cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
