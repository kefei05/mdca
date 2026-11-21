# MDCA 一键自测：八套自测 + 16 条边界算例
$ErrorActionPreference = "Continue"
$fail = 0
$results = @()

function Step($name, $cmd) {
    Write-Host ("--- " + $name) -ForegroundColor Cyan
    & powershell -NoProfile -Command $cmd
    if ($LASTEXITCODE -ne 0) { $script:fail++; $results += "$name : FAIL" }
    else { $results += "$name : OK" }
}

Step "provenance 9"  "python mdca_provenance.py"
Step "scaling 5"     "python mdca_scaling.py"
Step "schema 14"     "python mdca_schema.py selftest"
Step "grading 12"    "python mdca_grading.py selftest"
Step "palette 5"     "python mdca_palette.py --selftest"
Step "thermal 13"    "python mdca_thermal.py --selftest"
Step "mdca smoke 6"  "python mdca.py selftest"
Step "check 16+MC"   "python mdca.py check 300"

Write-Host ""
Write-Host "===== 汇总 =====" -ForegroundColor Yellow
$results | ForEach-Object { Write-Host $_ }
if ($fail -eq 0) { Write-Host "全部通过" -ForegroundColor Green; exit 0 }
else { Write-Host ("有 " + $fail + " 项失败") -ForegroundColor Red; exit 1 }
