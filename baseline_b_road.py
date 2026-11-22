#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B 路回归基线：证明「温度计算搬进插件」没有改变任何一条结论。

为什么需要它：
    第 2 步把 mdca.py 里的温度计算段整体搬进了 mdca_plugin_thermal.py。
    「搬家没改行为」不能靠嘴说——必须有一条可以随时重跑的证据。

基线 `baselines/v0.2_b_road.json` 是**改前**（c691c49）跑出来的，含 34 个算例
（cases 4 / benchmarks 3 / cases_negative 13 / cases_edge 4 / cases_ai 10）
各 14 个字段。改后逐字段对比，差异必须为 0。

它证明的是「搬家没改行为」，**不证明模型对**。后者的问题见项目复盘 §2：
热阻是借的，用温度反推热阻再用热阻算温度是同义反复。

用法：
    python baseline_b_road.py --diff        # 与基线对比（改后应输出「差异 0 个」）
    python baseline_b_road.py --write       # 重新生成基线（只在有意改行为时用！）
    python baseline_b_road.py --write --force-since <标明为什么重写>
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mdca import audit, load_case  # noqa: E402

DIRS = ["cases", "benchmarks", "cases_negative", "cases_edge", "cases_ai"]
BASELINE = ROOT / "baselines" / "v0.2_b_road.json"
B_ROAD = ("thermal", "scaling")

# 改前的 audit() 没有 plugins 参数；同一份脚本要能跑在改前与改后的代码上。
_ACCEPTS_PLUGINS = "plugins" in inspect.signature(audit).parameters


def snap(o: dict) -> dict:
    """只取与物理结论相关的字段；provenance 里的 code_digest 会随代码变，不入快照。"""
    t = o.get("temperature") or {}
    c = o.get("coupling") or {}
    v = o.get("validation") or {}
    issues = v.get("issues") or []
    return {
        "status": (o.get("audit") or {}).get("overall_status"),
        "reasons": sorted((o.get("audit") or {}).get("reasons") or []),
        "coupling": c.get("status"),
        "Tw": t.get("winding_point_C"),
        "Twc": t.get("winding_corrected_C"),
        "Tr": t.get("rotor_point_C"),
        "Ts": t.get("stator_point_C"),
        "interval": t.get("winding_corrected_interval_C"),
        "blocks": sorted({i["code"] for i in issues if i["severity"] == "BLOCK"}),
        "reviews": sorted({i["code"] for i in issues if i["severity"] == "REVIEW"}),
        "warns": sorted({i["code"] for i in issues if i["severity"] == "WARN"}),
        "infos": sorted({i["code"] for i in issues if i["severity"] == "INFO"}),
        "stress_flipped": (o.get("stress_tests") or {}).get("decision_flipped"),
        "model_nodes": (o.get("model") or {}).get("nodes"),
    }


def collect() -> dict:
    rows = {}
    for d in DIRS:
        for f in sorted((ROOT / d).glob("*.json")):
            key = f"{d}/{f.name}"
            case = load_case(f)
            try:
                o = audit(case, plugins=B_ROAD) if _ACCEPTS_PLUGINS else audit(case)
                rows[key] = snap(o)
            except Exception as e:  # noqa: BLE001
                rows[key] = {"error": f"{type(e).__name__}: {e}"}
    return rows


def main(argv: list[str]) -> int:
    rows = collect()

    if "--write" in argv:
        reason = ""
        if "--force-since" in argv:
            i = argv.index("--force-since")
            reason = " ".join(argv[i + 1:])
        if BASELINE.exists() and not reason:
            print("基线已存在。若是有意改行为，请带 --force-since <理由> 重写，")
            print("否则请用 --diff 确认差异为 0。")
            return 2
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(rows, ensure_ascii=False, indent=1, sort_keys=True),
                            encoding="utf-8")
        print(f"{len(rows)} 个算例 -> {BASELINE.relative_to(ROOT)}"
              + (f"（理由：{reason}）" if reason else ""))
        return 0

    if not BASELINE.exists():
        print(f"找不到基线：{BASELINE}")
        return 2

    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    diff = 0
    for k in sorted(set(base) | set(rows)):
        if base.get(k) != rows.get(k):
            diff += 1
            print(f"[DIFF] {k}")
            b, n = base.get(k) or {}, rows.get(k) or {}
            for fld in sorted(set(b) | set(n)):
                if b.get(fld) != n.get(fld):
                    print(f"    {fld}: {b.get(fld)!r} -> {n.get(fld)!r}")
    print(f"\n共 {len(rows)} 个算例，差异 {diff} 个")
    if diff:
        print("差异不为 0：要么搬家改了行为（必须修），要么基线该按 --force-since 重写。")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
