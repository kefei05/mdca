#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抓取 HTML 全文（含反爬尝试）并做关键词定位。"""
import re
import sys
import urllib.request
from pathlib import Path

REF = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
HDR = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}


def strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = (html.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
                .replace("&quot;", '"'))
    return re.sub(r"[ \t]+", " ", html)


def get(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        ctype = r.headers.get("Content-Type", "")
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc), ctype
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), ctype


def main(argv):
    if not argv:
        print("用法：python fetch_html.py <url> [label] [--grep p1|p2]")
        return 2
    url = argv[0]
    label = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else "page"
    try:
        html, ctype = get(url)
    except Exception as e:
        print(f"[FAIL] {url}\n       {type(e).__name__}: {e}")
        return 1
    text = strip_html(html)
    out = REF / f"{label}.txt"
    REF.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"[OK] {url}\n     ctype={ctype}  html={len(html)}  text={len(text)} -> {out}")

    pats = [r"热阻", r"温升", r"K/W", r"实测", r"试验"]
    if "--grep" in argv:
        i = argv.index("--grep")
        if i + 1 < len(argv):
            pats = argv[i + 1].split("|")
    for pat in pats:
        hits = list(re.finditer(pat, text))
        print(f"  /{pat}/ 命中 {len(hits)}")
        for m in hits[:3]:
            a = max(0, m.start() - 60)
            b = min(len(text), m.end() + 120)
            print("    ..." + re.sub(r"\s+", " ", text[a:b]) + "...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
