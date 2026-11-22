# MDCA 一键自测
#   A 路（默认，不启用任何插件）：口径 / 契约 / 溯源 / 分级 / 插件开关
#   B 路（--plugins thermal,scaling）：温度计算 / 缩放律 / 随机回代
# 第 2 步之后 B 路默认关闭，所以 B 路的每一行都必须显式带 --plugins。
$ErrorActionPreference = "Continue"
$fail = 0
$results = @()

function Step($name, $cmd) {
    Write-Host ("--- " + $name) -ForegroundColor Cyan
    & powershell -NoProfile -Command $cmd
    if ($LASTEXITCODE -ne 0) { $script:fail++; $results += "$name : FAIL" }
    else { $results += "$name : OK" }
}

Write-Host ""
Write-Host "########## A 路（默认主线，不产生温度）##########" -ForegroundColor Magenta
Step "plugins 15"      "python mdca_plugins.py selftest"
Step "provenance 9"    "python mdca_provenance.py"
Step "scaling 5"       "python mdca_scaling.py"
Step "schema 14"       "python mdca_schema.py selftest"
Step "grading 17"      "python mdca_grading.py selftest"
Step "palette 5"       "python mdca_palette.py --selftest"
Step "mdca A 路 7"     "python mdca.py selftest"
Step "check A 路 17"   "python mdca.py check 300"

Write-Host ""
Write-Host "########## B 路（显式启用插件）##########" -ForegroundColor Magenta
Step "thermal 13"      "python mdca_thermal.py --selftest"
Step "thermal 插件 8"  "python mdca_plugin_thermal.py"
Step "scaling 插件 4"  "python mdca_plugin_scaling.py"
Step "mdca B 路 10"    "python mdca.py selftest --plugins thermal,scaling"
Step "check B 路 17+MC" "python mdca.py check 300 --plugins thermal,scaling"
Step "B 路基线 34"     "python baseline_b_road.py --diff"

Write-Host ""
Write-Host "===== 汇总 =====" -ForegroundColor Yellow
$results | ForEach-Object { Write-Host $_ }
if ($fail -eq 0) { Write-Host "全部通过" -ForegroundColor Green; exit 0 }
else { Write-Host ("有 " + $fail + " 项失败") -ForegroundColor Red; exit 1 }
