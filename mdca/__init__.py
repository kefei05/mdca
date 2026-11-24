"""MDCA —— 电机设计约束审计器。

审的是「这批数据自己能不能立住」，不是「这台电机好不好」。
不替代仿真、不替代实测，只在数据进入下一步之前加一道独立闸门。

两个入口：

    python -m mdca audit <case>        单个或一批算例审计（cli）
    python -m mdca.review <目录>       批量审查外部候选（review）

Python 里也可以用：

    from mdca import audit, load_case
    result = audit(load_case("cases/joint_A_body_conduction.json"))
"""

from __future__ import annotations

from .cli import (
    AUDIT_BLOCKED,
    AUDIT_PASS,
    AUDIT_REVIEW,
    Case,
    audit,
    load_case,
    parse_case_doc,
)

__version__ = "0.4.0"

__all__ = [
    "audit",
    "load_case",
    "parse_case_doc",
    "Case",
    "AUDIT_PASS",
    "AUDIT_REVIEW",
    "AUDIT_BLOCKED",
    "__version__",
]
