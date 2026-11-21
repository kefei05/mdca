# -*- coding: utf-8 -*-
"""从 Motor-CAD 教程 PDF 中查找热网络节点编号与名称的对应关系。"""
import re
import sys
from pathlib import Path

import pdfplumber

TUT = Path(r"<MOTORCAD_ROOT>\Tutorials")
OUT = Path(r"E:\deepseek\mdca\refs")
OUT.mkdir(parents=True, exist_ok=True)

TARGETS = [
    TUT / "Reduced_Node_Thermal_Model" / "Reduced_Node_Model_Tutorial.pdf",
    TUT / "Winding_Temperature_Model.pdf",
    TUT / "Thermal_Circuit_Editor" / "Thermal_Circuit_Editor.pdf",
]

KEYWORDS = ["node", "Node", "NODE", "Stator_Yoke", "Tooth", "Winding",
            "Magnet", "Housing", "Rotor", "Endcap"]

for pdf_path in TARGETS:
    if not pdf_path.exists():
        print(f"[跳过] 不存在: {pdf_path.name}")
        continue
    print("=" * 70)
    print(f"文件: {pdf_path.name}  ({pdf_path.stat().st_size/1e6:.1f} MB)")
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            npages = len(pdf.pages)
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception as e:
        print(f"  提取失败: {type(e).__name__}: {e}")
        continue
    print(f"  页数 {npages}，字符 {len(text)}")
    txt_out = OUT / f"{pdf_path.stem}.txt"
    txt_out.write_text(text, encoding="utf-8")

    # 找含 "node" 且带数字的行
    hits = []
    for line in text.splitlines():
        s = line.strip()
        if not s or len(s) > 200:
            continue
        if re.search(r"\bnode\b", s, re.I) and re.search(r"\d", s):
            hits.append(s)
    print(f"  含 node+数字 的行: {len(hits)}")
    for h in hits[:18]:
        print("    " + h[:150])

    # 找节点名集中出现的段落
    named = [l.strip() for l in text.splitlines()
             if sum(1 for k in KEYWORDS if k in l) >= 2]
    print(f"  含 >=2 个节点名的行: {len(named)}")
    for h in named[:12]:
        print("    " + h[:150])
    print()
