#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 候选契约校验器（轻量，无外部依赖）
=======================================
契约定义见 mdca_schema.json（版本 1.0）。

本模块只做**结构校验**：字段存在性、类型、枚举、取值范围。
不校验物理合理性（那是 mdca.py 的 validate() 干的）。

用法：
    python mdca_schema.py selftest
    python mdca_schema.py cases/joint_A_body_conduction.json
    python mdca_schema.py cases/            # 校验目录
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCHEMA_VERSION = "1.0"
CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

REQUIRED_TOP = ("schema", "case_id", "params", "limits")
# A 路契约：只要求「有损耗、有环境」。
# Rws/Rsa 的必需性由 thermal 插件在运行时检查（code=V1_plugin_missing），
# 因为 A 路不产生温度，就不该强迫上游先交出热阻——那正是「借热阻」的入口
# （这就是「审数据能不能用」与「算电机性能」的分界）。
REQUIRED_PARAMS = ("Pcu", "Pfe", "Ta")
SOURCE_TYPES = ("ai", "optimizer", "human", "external")
TEMP_TYPES = ("mean", "hotspot")
CONSERVATISM = ("conservative", "optimistic")
PALETTE_ENUMS = {
    "dim": ("2D", "3D"),
    "end_winding": ("included", "excluded"),
    "scope": ("motor", "module"),
    "load_case": ("continuous", "peak", "rated", "short_circuit"),
    "current_basis": ("rms", "peak"),
    "convergence": ("converged", "not_converged"),
    "method": ("analytic", "analytic_scaled", "fem", "fem_transient",
               "measured", "design_target", "literature", "convention"),
}
GEOM_FIELDS = ("Z", "P_slot", "L", "D_gap", "g", "A_case", "L_ins")


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_param(name: str, p: Any) -> List[str]:
    errs: List[str] = []
    if not isinstance(p, dict):
        return [f"params.{name} 必须是对象"]
    if "value" not in p:
        errs.append(f"params.{name} 缺少 value")
    elif not _is_num(p["value"]):
        errs.append(f"params.{name}.value 必须是数字")
    if "unit" not in p:
        errs.append(f"params.{name} 缺少 unit")
    elif not isinstance(p["unit"], str) or not p["unit"]:
        errs.append(f"params.{name}.unit 必须是非空字符串")

    iv = p.get("interval")
    if iv is not None:
        if not (isinstance(iv, list) and len(iv) == 2 and all(_is_num(v) for v in iv)):
            errs.append(f"params.{name}.interval 必须是两个数字")
        elif iv[0] > iv[1]:
            errs.append(f"params.{name}.interval 下界大于上界")

    pal = p.get("palette")
    if pal is not None:
        if not isinstance(pal, dict):
            errs.append(f"params.{name}.palette 必须是对象")
        else:
            for k, allowed in PALETTE_ENUMS.items():
                if k in pal and pal[k] not in allowed:
                    errs.append(f"params.{name}.palette.{k} 取值 {pal[k]!r} 不在 {allowed}")

    cons = p.get("conservatism")
    if cons is not None and cons not in CONSERVATISM:
        errs.append(f"params.{name}.conservatism 取值 {cons!r} 不合法")

    ver = p.get("verified")
    if ver is not None and not isinstance(ver, bool):
        errs.append(f"params.{name}.verified 必须是布尔")
    return errs


def validate_document(doc: Any) -> List[str]:
    """返回错误列表，空列表表示通过。"""
    errs: List[str] = []
    if not isinstance(doc, dict):
        return ["顶层必须是 JSON 对象"]

    for k in REQUIRED_TOP:
        if k not in doc:
            errs.append(f"缺少顶层字段 {k}")
    if errs:
        return errs

    if doc.get("schema") != SCHEMA_VERSION:
        errs.append(f"schema 必须为 {SCHEMA_VERSION!r}，实际 {doc.get('schema')!r}")

    cid = doc.get("case_id")
    if not isinstance(cid, str) or not CASE_ID_RE.match(cid):
        errs.append(f"case_id 不合法：{cid!r}")

    st = doc.get("source_type")
    if st is not None and st not in SOURCE_TYPES:
        errs.append(f"source_type 取值 {st!r} 不在 {SOURCE_TYPES}")

    params = doc.get("params")
    if not isinstance(params, dict):
        errs.append("params 必须是对象")
    else:
        for k in REQUIRED_PARAMS:
            if k not in params:
                errs.append(f"params 缺少必需参数 {k}")
        for name, p in params.items():
            errs.extend(validate_param(name, p))

    limits = doc.get("limits")
    if not isinstance(limits, dict):
        errs.append("limits 必须是对象")
    else:
        if "Tw_max" not in limits:
            errs.append("limits 缺少 Tw_max")
        elif not _is_num(limits["Tw_max"]) or limits["Tw_max"] <= 0:
            errs.append("limits.Tw_max 必须是正数")
        tt = limits.get("temperature_type")
        if tt is not None and tt not in TEMP_TYPES:
            errs.append(f"limits.temperature_type 取值 {tt!r} 不在 {TEMP_TYPES}")
        tr = limits.get("Tr_max")
        if tr is not None and (not _is_num(tr) or tr <= 0):
            errs.append("limits.Tr_max 必须是正数")

    cal = doc.get("calibration")
    if cal is not None:
        if not isinstance(cal, dict):
            errs.append("calibration 必须是对象")
        else:
            d = cal.get("delta_cal_K")
            if d is not None and (not _is_num(d) or d < 0):
                errs.append("calibration.delta_cal_K 必须是非负数")

    dom = doc.get("domain")
    if dom is not None:
        if not isinstance(dom, dict):
            errs.append("domain 必须是对象")
        else:
            for k, spec in dom.items():
                # 两种写法：[lo, hi] 或 {"interval": [lo, hi], "temp_basis": "20C"}
                if isinstance(spec, dict):
                    rng = spec.get("interval")
                    tb = spec.get("temp_basis")
                    if not (isinstance(rng, list) and len(rng) == 2 and all(_is_num(v) for v in rng)):
                        errs.append(f"domain.{k}.interval 必须是 [lo, hi] 两个数字")
                    elif rng[0] > rng[1]:
                        errs.append(f"domain.{k}.interval 下界大于上界")
                    if tb is not None and not isinstance(tb, str):
                        errs.append(f"domain.{k}.temp_basis 必须是字符串")
                elif not (isinstance(spec, list) and len(spec) == 2 and all(_is_num(v) for v in spec)):
                    errs.append(f"domain.{k} 必须是 [lo, hi] 或 {{interval, temp_basis}}")

    sc = doc.get("scaling")
    if sc is not None:
        if not isinstance(sc, dict):
            errs.append("scaling 必须是对象")
        else:
            for k in ("reference_geometry", "reference_R", "geometry"):
                if k not in sc:
                    errs.append(f"scaling 缺少 {k}")
            for gk in ("reference_geometry", "geometry"):
                g = sc.get(gk)
                if isinstance(g, dict):
                    for f in GEOM_FIELDS:
                        if f not in g or not _is_num(g[f]) or g[f] <= 0:
                            errs.append(f"scaling.{gk}.{f} 缺失或非正数")

    srcs = doc.get("sources")
    if srcs is not None:
        if not isinstance(srcs, list):
            errs.append("sources 必须是数组")
        else:
            for i, s in enumerate(srcs):
                if not isinstance(s, dict) or "path" not in s:
                    errs.append(f"sources[{i}] 缺少 path")
    return errs


# --------------------------------------------------------------------------
# JSONL 支持
# --------------------------------------------------------------------------


def read_jsonl(path: Path) -> List[Tuple[int, Any, Optional[str]]]:
    """读 JSONL：每行一个候选。返回 [(行号, 对象, 错误信息)]。
    单行解析失败不影响其他行（纠偏基线 B2 要求）。"""
    out: List[Tuple[int, Any, Optional[str]]] = []
    text = path.read_text(encoding="utf-8-sig")
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("//") or s.startswith("#"):
            continue
        try:
            out.append((i, json.loads(s), None))
        except json.JSONDecodeError as e:
            out.append((i, None, f"{type(e).__name__}: {e}"))
    return out


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------


def selftest() -> bool:
    ok = True
    print("=== 契约校验器自测 ===")

    def check(label: str, doc: Any, expect_ok: bool, hint: str = "") -> None:
        nonlocal ok
        errs = validate_document(doc)
        good = (not errs) if expect_ok else bool(errs)
        print(f"  {label:<40}{'PASS' if good else 'FAIL'}  {errs[:2] if errs else ''}")
        ok = ok and good

    def base() -> Dict[str, Any]:
        return {
            "schema": "1.0", "case_id": "T01",
            "params": {k: {"value": 1.0, "unit": "W"}
                       for k in ("Rws", "Rsa", "Pcu", "Pfe", "Ta")},
            "limits": {"Tw_max": 155.0},
        }

    check("最小合法文档", base(), True)

    d = base(); del d["case_id"]
    check("缺 case_id → 拒", d, False)

    d = base(); d["schema"] = "2.0"
    check("版本不符 → 拒", d, False)

    d = base(); d["case_id"] = "有中文 空格"
    check("case_id 含非法字符 → 拒", d, False)

    d = base(); del d["params"]["Pcu"]
    check("缺必需参数 Pcu → 拒", d, False)

    d = base(); del d["params"]["Rws"]; del d["params"]["Rsa"]
    check("缺 Rws/Rsa（A 路可选）→ 通过", d, True)

    d = base(); d["params"]["Rws"]["interval"] = [2.0, 1.0]
    check("区间倒置 → 拒", d, False)

    d = base(); d["params"]["Rws"]["palette"] = {"dim": "4D"}
    check("palette 枚举非法 → 拒", d, False)

    d = base(); d["params"]["Rws"]["unit"] = ""
    check("空 unit → 拒", d, False)

    d = base(); d["limits"] = {}
    check("缺 Tw_max → 拒", d, False)

    d = base(); d["source_type"] = "robot"
    check("source_type 非法 → 拒", d, False)

    d = base(); d["calibration"] = {"delta_cal_K": -5}
    check("负校准裕度 → 拒", d, False)

    d = base(); d["domain"] = {"Ta": [10, 60]}
    check("合法 domain → 通过", d, True)

    d = base(); d["domain"] = {"Ta": 60}
    check("domain 非数组 → 拒", d, False)

    d = base()
    d["scaling"] = {"reference_geometry": {}, "reference_R": {}, "geometry": {}}
    check("scaling 几何字段缺失 → 拒", d, False)

    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


def main(argv: List[str]) -> int:
    if not argv or argv[0] == "selftest":
        return 0 if selftest() else 1

    target = Path(argv[0])
    files: List[Path] = []
    if target.is_dir():
        files = sorted(list(target.glob("*.json")) + list(target.glob("*.jsonl")))
    elif target.exists():
        files = [target]
    else:
        print(f"不存在：{target}")
        return 2

    bad = 0
    print("=" * 70)
    print("契约校验（schema 1.0）")
    print("=" * 70)
    for f in files:
        if f.suffix == ".jsonl":
            rows = read_jsonl(f)
            for lineno, doc, perr in rows:
                if perr:
                    print(f"  {f.name}:{lineno}  解析失败 {perr}")
                    bad += 1
                    continue
                errs = validate_document(doc)
                if errs:
                    print(f"  {f.name}:{lineno}  {len(errs)} 个错误：{errs[:3]}")
                    bad += 1
            print(f"  {f.name}: {len(rows)} 行")
        else:
            doc = json.loads(f.read_text(encoding="utf-8-sig"))
            errs = validate_document(doc)
            if errs:
                print(f"  {f.name}  {len(errs)} 个错误：{errs[:3]}")
                bad += 1
            else:
                print(f"  {f.name}  PASS")
    print(f"\n共 {len(files)} 个文件，{bad} 个有问题")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
