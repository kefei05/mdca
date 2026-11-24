# -*- coding: utf-8 -*-
"""试 IEEE / IET 直链。之前未实际尝试就判为付费墙，是判断失误。"""
import ssl
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "refs"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

TARGETS = [
    ("ieee_lptn_8241358.pdf",
     "https://ieeexplore.ieee.org/ielx7/7873789/8241352/08241358.pdf",
     "IEEE: Lumped-Parameter Thermal Network Model（摘要已见此文的节点表）"),
    ("iet_axflux_hierarchical.pdf",
     "https://digital-library.theiet.org/doi/pdf/10.1049/iet-epa.2017.0719?download=true",
     "IET EPA: Hierarchical thermal network, axial-flux PMSM"),
    ("mdpi_energies_11_0774_yasa.pdf",
     "https://www.mdpi.com/1996-1073/11/4/774/pdf",
     "MDPI Energies 11(774): 3D dynamic LPTN of air-cooled YASA"),
    ("mdpi_energies_14_7455.pdf",
     "https://www.mdpi.com/1996-1073/14/22/7455/pdf",
     "MDPI Energies 14(7455): Fast thermal model for IPMSM"),
]

for fname, url, desc in TARGETS:
    dest = OUT / fname
    if dest.exists() and dest.stat().st_size > 50_000:
        print(f"[跳过] {fname} 已存在")
        continue
    print(f"[试] {desc}")
    print(f"     {url[:88]}")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "application/pdf,*/*",
            "Referer": "https://www.google.com/",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            data = r.read()
            ctype = r.headers.get("Content-Type", "?")
            status = getattr(r, "status", "?")
        if data[:4] == b"%PDF":
            dest.write_bytes(data)
            print(f"     ✓ PDF {len(data)/1e6:.2f} MB 已保存")
        else:
            snippet = data[:200].decode("utf-8", "replace").replace("\n", " ")
            print(f"     ✗ 非 PDF（status={status} type={ctype} {len(data)} B）")
            print(f"       内容开头：{snippet[:110]}")
    except Exception as e:
        print(f"     ✗ {type(e).__name__}: {str(e)[:130]}")
    print()
