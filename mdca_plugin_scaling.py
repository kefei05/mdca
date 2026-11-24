#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
B 路插件：几何缩放律（从候选几何推算 Rws/Rsa/Rrs）
=================================================

**默认关闭。** 只有显式 `--plugins scaling`、case 里写 `"plugins": {"scaling": true}`，
或 `MDCA_PLUGINS=scaling` 时才运行。

为什么默认关：

    缩放律是把基准电机的热阻按几何比例推给候选。它给出的是「可算的数」，
    不是「审出来的数」——基准热阻本身还是借的。A 路不产生热阻，所以不需要它。

只有 case 里带 `scaling` 段时本插件才会实际做事；没有该段时返回 `ok=True` 且
`answer.derived = {}`（等价于「无事可做」，不报错）。

返回：

    {
      "ok": bool,
      "reason": str,
      "answer": {
        "derived": { "Rws": <原始 param dict>, ... },   # 交给 mdca.py 转 Param
        "result": {...} | None,                          # 缩放比值与外推判断
      },
      "issues": [...],
    }

`derived` 用**原始 param dict** 而不是 mdca.Param：插件不该反向依赖编排层，
否则 mdca.py 与本模块会成环。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdca_scaling import Geometry, scale  # noqa: E402

NAME = "scaling"
ROAD = "B"

PALETTE = {"method": "analytic_scaled", "scope": "motor"}
SOURCE = "几何缩放律（未校准）"


def _derived_param(value: float, lo: float, hi: float) -> Dict[str, Any]:
    return {
        "value": round(value, 4),
        "unit": "K/W",
        "palette": dict(PALETTE),
        "verified": True,
        "source": SOURCE,
        "interval": [round(lo, 4), round(hi, 4)],
    }


def run(case: Any, opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    sc = case.scaling or {}
    if not sc:
        return {"ok": True, "answer": {"derived": {}, "result": None}, "issues": []}

    try:
        ref_geom = Geometry(**sc["reference_geometry"])
        geom = Geometry(**sc["geometry"])
        ref_R = sc["reference_R"]
    except (KeyError, TypeError) as e:
        return {
            "ok": False,
            "reason": f"scaling 段字段不全：{e}",
            "answer": {"derived": {}, "result": None},
            "issues": [{
                "code": "S0_scaling_input", "severity": "BLOCK",
                "message": f"scaling 段字段不全：{e}", "params": (),
                "detail": {"plugin": NAME},
            }],
        }

    rel_unc = float(sc.get("relative_uncertainty", 0.25))
    max_dev = float(sc.get("max_deviation", 0.30))
    res = scale(ref_geom, ref_R, geom, rel_unc=rel_unc, max_dev=max_dev)

    derived = {
        "Rws": _derived_param(res.Rws, *res.Rws_interval),
        "Rsa": _derived_param(res.Rsa, *res.Rsa_interval),
        "Rrs": _derived_param(res.Rrs, *res.Rrs_interval),
    }

    issues: List[Dict[str, Any]] = [{
        "code": "S1_scaling_applied", "severity": "INFO",
        "message": (f"Rws/Rsa/Rrs 由几何缩放律给出（±{rel_unc * 100:.0f}%），未校准"),
        "params": ("Rws", "Rsa", "Rrs"),
        "detail": res.as_dict(),
    }]
    if not res.within_reference:
        issues.append({
            "code": "S2_extrapolation", "severity": "REVIEW",
            "message": (f"候选几何偏离基准 {res.max_deviation * 100:.1f}%"
                        f"（阈值 {max_dev * 100:.0f}%）：属外推，缩放律不保证成立"),
            "params": ("Rws", "Rsa", "Rrs"),
            "detail": {"ratios": res.as_dict()["ratios"]},
        })

    return {"ok": True, "answer": {"derived": derived, "result": res.as_dict()},
            "issues": issues}


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------


class _P:
    def __init__(self, value: float):
        self.value = value
        self.interval = None
        self.unit = "K/W"
        self.palette = None
        self.verified = True
        self.source = "selftest"


class _Case:
    def __init__(self, scaling: Optional[Dict[str, Any]] = None):
        self.case_id = "selftest"
        self.params: Dict[str, _P] = {}
        self.scaling = scaling or {}
        self.limits: Dict[str, Any] = {}
        self.calibration: Dict[str, Any] = {}


def selftest() -> bool:
    ok = True

    def check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {label:<46}{'PASS' if good else 'FAIL'}"
              + (f"  {detail}" if detail else ""))
        ok = ok and good

    print("=== scaling 插件自测 ===")

    o = run(_Case(), {})
    check("无 scaling 段 → 正常返回空", o["ok"] and o["answer"]["derived"] == {})

    g = {"Z": 48, "P_slot": 12, "L": 0.05, "D_gap": 0.07, "g": 0.0006,
         "A_case": 0.017, "L_ins": 0.0002}
    sc = {"reference_geometry": dict(g), "reference_R": {"Rws": 1.16, "Rsa": 2.03, "Rrs": 3.0},
          "geometry": dict(g)}
    o = run(_Case(sc), {})
    check("同几何缩回基准点", o["ok"] and abs(o["answer"]["derived"]["Rws"]["value"] - 1.16) < 1e-3,
          str(o["answer"]["derived"].get("Rws")))

    o = run(_Case({"reference_geometry": {}, "reference_R": {}, "geometry": {}}), {})
    check("字段不全 → ok=False", not o["ok"])
    check("字段不全 → S0_scaling_input",
          any(i["code"] == "S0_scaling_input" for i in o.get("issues", [])))

    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if selftest() else 1)
