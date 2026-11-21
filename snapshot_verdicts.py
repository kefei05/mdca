# -*- coding: utf-8 -*-
"""快照全部算例的结论，用于「任务 3 前后结论一致」的逐条对比。

直接调 audit()，不经过 cmd_audit，因此不写账本、不受 input_changed 干扰。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from mdca import audit, load_case  # noqa: E402

DIRS = ["cases", "benchmarks", "cases_negative", "cases_edge"]
rows = []
for d in DIRS:
    p = HERE / d
    if not p.exists():
        continue
    for f in sorted(p.glob("*.json")):
        try:
            case = load_case(f)
            o = audit(case)
            status = o["audit"]["overall_status"]
            reasons = ",".join(o["audit"]["reasons"] or [])
            rows.append((f"{d}/{f.name}", status, reasons))
        except Exception as e:
            rows.append((f"{d}/{f.name}", "ERROR", f"{type(e).__name__}: {e}"))

for name, status, reasons in rows:
    print(f"{name}\t{status}\t{reasons}")
print(f"# total={len(rows)}")

# 显式写 UTF-8，避免被 shell 重定向成 UTF-16
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(
        "\n".join(f"{n}\t{s}\t{r}" for n, s, r in rows) + f"\n# total={len(rows)}\n",
        encoding="utf-8")
    print(f"# written -> {sys.argv[1]}")
