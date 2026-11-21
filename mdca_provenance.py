#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 溯源层：哈希、版本、append-only 账本
==========================================

对应方案 7.2「防编造要求（须落实到代码机制）」：

    · 原始数据文件保存哈希，导入后不得静默覆盖        → sha256_file / check_sources
    · 外部仿真结果只能来自可追踪的导入文件            → sources 段的哈希校验
    · 修改系数或约束必须生成新版本，不改写历史审计记录  → Ledger（JSONL 只追加）
    · 报告可追溯到原始输入、代码版本、约束版本         → case_hash + code_fingerprint + constraint_version

设计原则：**账本只追加，永不改写**。想改一个数，就产生新的 case_hash，
旧记录留着——这样"这个数什么时候变的、从什么变成什么"永远查得到。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


CONSTRAINT_VERSION = "mdca-constraints-1.0"
"""约束定义的版本。改任何一条规则（MIX_BLOCKING、单位表、阈值）都要升版本号。"""

LEDGER_NAME = "ledger.jsonl"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def code_fingerprint(root: Optional[Path] = None) -> Dict[str, str]:
    """给当前代码拍指纹：任何一个 .py 改动都会改变它。"""
    here = root or Path(__file__).resolve().parent
    out: Dict[str, str] = {}
    for p in sorted(here.glob("*.py")):
        out[p.name] = sha256_file(p)[:12]
    return out


def fingerprint_digest(fp: Dict[str, str]) -> str:
    """把 {文件名: 短哈希} 压成一个总指纹。"""
    payload = "|".join(f"{k}:{v}" for k, v in sorted(fp.items()))
    return sha256_bytes(payload.encode("utf-8"))[:16]


# --------------------------------------------------------------------------
# 外部来源哈希校验
# --------------------------------------------------------------------------


@dataclass
class SourceCheck:
    path: str
    expected: Optional[str]
    actual: Optional[str]
    ok: bool
    reason: str = ""


def check_sources(sources: List[Dict[str, Any]], base: Path) -> List[SourceCheck]:
    """sources: [{"path": "...", "sha256": "..."}]。

    文件缺失或哈希不符 ⇒ ok=False。这是防止"外部结果被静默替换"的唯一机制。
    """
    results: List[SourceCheck] = []
    for s in sources or []:
        raw_path = str(s.get("path", ""))
        expected = s.get("sha256")
        p = Path(raw_path)
        if not p.is_absolute():
            p = base / p
        if not p.exists():
            results.append(SourceCheck(raw_path, expected, None, False, "文件不存在"))
            continue
        actual = sha256_file(p)
        if expected is None:
            results.append(SourceCheck(raw_path, None, actual, True, "未声明哈希，仅记录实际值"))
        elif expected == actual:
            results.append(SourceCheck(raw_path, expected, actual, True, "哈希一致"))
        else:
            results.append(SourceCheck(raw_path, expected, actual, False, "哈希不一致：文件被替换或修改"))
    return results


# --------------------------------------------------------------------------
# append-only 账本
# --------------------------------------------------------------------------


@dataclass
class LedgerEntry:
    ts: str
    case_id: str
    case_hash: str
    status: str
    code_digest: str
    constraint_version: str
    delta_cal_K: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        d = {
            "ts": self.ts, "case_id": self.case_id, "case_hash": self.case_hash,
            "status": self.status, "code_digest": self.code_digest,
            "constraint_version": self.constraint_version,
            "delta_cal_K": self.delta_cal_K,
        }
        d.update(self.extra)
        return d


class Ledger:
    """JSONL 账本：只追加，不改写。

    读旧记录 → 比对 case_hash → 变了就报 case_modified，但**旧记录保留**。
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"_corrupt": line[:120]})
        return out

    def history(self, case_id: str) -> List[Dict[str, Any]]:
        return [e for e in self.read() if e.get("case_id") == case_id]

    def last(self, case_id: str) -> Optional[Dict[str, Any]]:
        h = self.history(case_id)
        return h[-1] if h else None

    def append(self, entry: LedgerEntry) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.as_dict(), ensure_ascii=False) + "\n")

    def detect_change(self, case_id: str, case_hash: str) -> Optional[Dict[str, Any]]:
        """返回最后一次不同哈希的记录（供报告里说明"输入被改过"）。"""
        prev = self.last(case_id)
        if prev and prev.get("case_hash") != case_hash:
            return prev
        return None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------


def selftest() -> bool:
    import tempfile
    ok = True
    print("=== 溯源层自测 ===")

    def check(label: str, cond: bool, extra: str = "") -> None:
        nonlocal ok
        print(f"  {label:<46}{'PASS' if cond else 'FAIL'} {extra}")
        ok = ok and cond

    # ① 哈希稳定
    a = sha256_bytes(b"abc")
    b = sha256_bytes(b"abc")
    check("① 同内容哈希相同", a == b, a[:12])

    # ② 内容变则哈希变
    check("② 内容变则哈希变", sha256_bytes(b"abc") != sha256_bytes(b"abd"))

    # ③ 代码指纹
    fp = code_fingerprint()
    check("③ 代码指纹非空", len(fp) > 0, f"{len(fp)} 个文件 → {fingerprint_digest(fp)}")

    # ④ 来源哈希校验：正确 / 错误 / 缺失
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        f = d / "x.txt"
        f.write_text("hello", encoding="utf-8")
        good = sha256_file(f)
        r1 = check_sources([{"path": "x.txt", "sha256": good}], d)
        r2 = check_sources([{"path": "x.txt", "sha256": "0" * 64}], d)
        r3 = check_sources([{"path": "nope.txt", "sha256": good}], d)
        check("④a 哈希一致 → ok", r1[0].ok)
        check("④b 哈希不符 → 拒绝", not r2[0].ok, r2[0].reason)
        check("④c 文件缺失 → 拒绝", not r3[0].ok, r3[0].reason)

        # ⑤ 账本追加与变更检测
        led = Ledger(d / LEDGER_NAME)
        e1 = LedgerEntry(now_iso(), "case-1", "aaa", "POINT_PASS",
                         "digest1", CONSTRAINT_VERSION, 33.0)
        led.append(e1)
        check("⑤a 追加后可读回", len(led.history("case-1")) == 1)
        check("⑤b 哈希未变 → 不报变更",
              led.detect_change("case-1", "aaa") is None)
        changed = led.detect_change("case-1", "bbb")
        check("⑤c 哈希变了 → 报变更", changed is not None and changed["case_hash"] == "aaa")

        # ⑥ 追加不覆盖旧记录
        e2 = LedgerEntry(now_iso(), "case-1", "bbb", "MODEL_FAIL",
                         "digest1", CONSTRAINT_VERSION, 33.0)
        led.append(e2)
        hist = led.history("case-1")
        check("⑥ 追加不覆盖（两条都在）",
              len(hist) == 2 and hist[0]["case_hash"] == "aaa" and hist[1]["case_hash"] == "bbb",
              f"{[h['case_hash'] for h in hist]}")

    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(0 if selftest() else 1)
