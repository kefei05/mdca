# -*- coding: utf-8 -*-
"""下载方法对照所需的开放获取文献。本机 curl/git 的 Schannel 失效，用 urllib。"""
import ssl
import sys
import urllib.request
from pathlib import Path

OUT = Path(r"E:\deepseek\mdca\refs")
OUT.mkdir(exist_ok=True)

TARGETS = [
    ("energies_14_5652_automotive.pdf",
     "https://mdpi-res.com/d_attachment/energies/energies-14-05652/article_deploy/energies-14-05652-v2.pdf?version=1631238076",
     "MDPI Energies 14(5652): LPTN of PMSM for automotive brake-by-wire"),
    ("evs30_improved_thermal_model.pdf",
     "https://www.citelec.org/EVS30/download.php?f=papers%2FEVS30-5510123.pdf",
     "EVS30: An Improved Thermal Model for Electric Motors"),
    ("rwth_oil_cooled_pmsm.pdf",
     "https://publications.rwth-aachen.de/record/1004879/files/1004879.pdf",
     "RWTH: Thermal Modeling and Control of an Oil-Cooled PMSM"),
    ("chalmers2013_lptn_thesis.pdf",
     "http://studentarbeten.chalmers.se/publication/185192-lumped-parameter-thermal-modelling-of-electric-machines",
     "Chalmers 2013 thesis (landing page, may not be PDF)"),
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

for fname, url, desc in TARGETS:
    dest = OUT / fname
    if dest.exists() and dest.stat().st_size > 50_000:
        print(f"[跳过] {fname} 已存在 {dest.stat().st_size/1e6:.2f} MB")
        continue
    print(f"[下载] {desc}")
    print(f"       {url[:90]}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept": "*/*",
                                                   "Referer": "https://www.google.com/"})
        with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
            data = r.read()
        ctype = r.headers.get("Content-Type", "?")
        if data[:4] == b"%PDF":
            dest.write_bytes(data)
            print(f"       OK  {len(data)/1e6:.2f} MB -> {fname}")
        else:
            print(f"       非 PDF（Content-Type={ctype}，{len(data)} B），未保存")
    except Exception as e:
        print(f"       失败 {type(e).__name__}: {str(e)[:120]}")
    print()

print("=" * 70)
print("refs/ 现有 PDF：")
for p in sorted(OUT.glob("*.pdf")):
    print(f"  {p.name:<44}{p.stat().st_size/1e6:>8.2f} MB")
