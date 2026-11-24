# MDCA 一键自测
#   A 路（默认，不启用任何插件）：口径 / 契约 / 溯源 / 分级 / 插件开关
#   B 路（--plugins thermal,scaling）：温度计算 / 缩放律 / 随机回代
# 第 2 步之后 B 路默认关闭，所以 B 路的每一行都必须显式带 --plugins。
$ErrorActionPreference = "Continue"
$fail = 0
$results = @()

# 跨平台：Windows PowerShell 里这个进程叫 powershell，PowerShell 7 / Linux 上叫 pwsh。
# 用当前进程自己的可执行文件，避免写死其中一个。
$psExe = (Get-Process -Id $PID).Path

function Step($name, $cmd) {
    Write-Host ("--- " + $name) -ForegroundColor Cyan
    & $psExe -NoProfile -Command $cmd
    if ($LASTEXITCODE -ne 0) { $script:fail++; $results += "$name : FAIL" }
    else { $results += "$name : OK" }
}

Write-Host ""
Write-Host "########## A 路（默认主线，不产生温度）##########" -ForegroundColor Magenta
Step "plugins 15"      "python -m mdca.plugins selftest"
Step "provenance 9"    "python -m mdca.provenance"
Step "scaling 5"       "python -m mdca.scaling"
Step "schema 15"       "python -m mdca.schema selftest"
Step "grading 17"      "python -m mdca.grading selftest"
Step "palette 5"       "python -m mdca.palette --selftest"
Step "mdca A 路 7"     "python -m mdca selftest"
Step "check A 路 17"   "python -m mdca check 300"
Step "inverter 18"     "python -m mdca.inverter selftest"
Step "backtest 5 集"   "python road_a_backtest.py"
Step "review 10"       "python -m mdca.review selftest"
Step "doc 一致"        "python doc_consistency.py"
Step "anatomy 7"       "python -m mdca.anatomy selftest"

Write-Host ""
Write-Host "########## B 路（显式启用插件）##########" -ForegroundColor Magenta
Step "thermal 13"      "python -m mdca.thermal --selftest"
Step "thermal 插件 8"  "python -m mdca.plugin_thermal"
Step "scaling 插件 4"  "python -m mdca.plugin_scaling"
Step "mdca B 路 10"    "python -m mdca selftest --plugins thermal,scaling"
Step "check B 路 17+MC" "python -m mdca check 300 --plugins thermal,scaling"
Step "B 路基线 34"     "python baseline_b_road.py --diff"

Write-Host ""
Write-Host "===== 汇总 =====" -ForegroundColor Yellow
$results | ForEach-Object { Write-Host $_ }
if ($fail -eq 0) { Write-Host "全部通过" -ForegroundColor Green; exit 0 }
else { Write-Host ("有 " + $fail + " 项失败") -ForegroundColor Red; exit 1 }
