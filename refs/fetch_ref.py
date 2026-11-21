#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""下载开放获取 PDF 并提取文本，用于 LPTN 校准点采集。"""
import re
import sys
import urllib.request
from pathlib import Path

import pdfplumber

REF = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def fetch(url: str, out: Path) -> int:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/pdf,text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return len(data)


def text_of(path: Path) -> str:
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def grep(text: str, patterns, window: int = 160, limit: int = 8):
    for pat in patterns:
        hits = list(re.finditer(pat, text, re.I))
        print(f"\n### /{pat}/  命中 {len(hits)}")
        for m in hits[:limit]:
            a = max(0, m.start() - window // 2)
            b = min(len(text), m.end() + window)
            snippet = re.sub(r"\s+", " ", text[a:b])
            print(f"  …{snippet}…")


def main(argv):
    if len(argv) < 2:
        print("用法：python fetch_ref.py <url> [label] [--grep pat1|pat2]")
        return 2
    url = argv[0]
    label = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else "ref"
    out = REF / f"{label}.pdf"
    try:
        n = fetch(url, out)
        print(f"下载成功 {n} 字节 → {out}")
    except Exception as e:
        print(f"下载失败：{type(e).__name__}: {e}")
        return 1
    try:
        text = text_of(out)
    except Exception as e:
        print(f"提取失败：{type(e).__name__}: {e}")
        return 1
    txt = REF / f"{label}.txt"
    txt.write_text(text, encoding="utf-8")
    print(f"文本 {len(text)} 字符 → {txt}")

    pats = [r"K/W", r"thermal resistance", r"copper loss", r"measured",
            r"experimental", r"temperature rise", r"steady[- ]state"]
    if "--grep" in argv:
        i = argv.index("--grep")
        if i + 1 < len(argv):
            pats = argv[i + 1].split("|")
    grep(text, pats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
