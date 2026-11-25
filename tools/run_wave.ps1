# Запуск волны adapt одной командой (dry-run по умолчанию).
# Примеры:
#   .\tools\run_wave.ps1 -Ids "2248,2250,2252"
#   .\tools\run_wave.ps1 -IdsFile wave_a.txt -Live
#   .\tools\run_wave.ps1 -Limit 10 -Scale 6 -Exclude "1239"
param(
    [string]$Ids = "",
    [string]$IdsFile = "",
    [int]$Limit = 0,
    [switch]$Force,
    [switch]$Live,
    [string]$Exclude = "",
    [int]$Scale = 0,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$helper = Join-Path $PSScriptRoot "wave_helper.py"
$excludeFile = Join-Path $repoRoot "wave_exclude.txt"

$prepArgs = @($helper, "prepare", "--exclude-file", $excludeFile)
if ($Ids)     { $prepArgs += @("--ids", $Ids) }
if ($IdsFile) { $prepArgs += @("--ids-file", $IdsFile) }
if ($Limit -gt 0) { $prepArgs += @("--limit", "$Limit") }
if ($Force)   { $prepArgs += "--force" }
if ($Live)    { $prepArgs += "--live" }
if ($Exclude) { $prepArgs += @("--exclude", $Exclude) }
if ($Scale -gt 0) { $prepArgs += @("--scale", "$Scale") }

$prepJson = python @prepArgs
if ($LASTEXITCODE -ne 0) { throw "Не удалось подготовить параметры волны." }
$prep = $prepJson | ConvertFrom-Json

$modeText = if ($Live) { "LIVE" } else { "DRY_RUN" }
$idCount = if ($prep.ids.Count -gt 0) { "$($prep.ids.Count) ID" } else { "limit=$($prep.env.ADAPT_LIMIT)" }
$scaleText = if ($Scale -gt 0) { "$Scale" } else { "default" }
$exclText = if ($prep.exclude.Count -gt 0) { ($prep.exclude -join ",") } else { "-" }
Write-Host "Волна: $modeText | $idCount | force=$([int]$Force.IsPresent) | scale=$scaleText | exclude=$exclText"

$wavesRoot = Join-Path $repoRoot "logs\waves"
New-Item -ItemType Directory -Force $wavesRoot | Out-Null
$dryMarker = Join-Path $wavesRoot ".last_dry_run_$($prep.signature)"
$cacheDir = Join-Path $repoRoot "gemini_cache"
$cacheEmpty = (-not (Test-Path $cacheDir)) -or ((Get-ChildItem -Path $cacheDir -Filter "*.json" -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0)

if ($Live -and (-not (Test-Path $dryMarker) -or $cacheEmpty) -and (-not $Yes)) {
    $ans = Read-Host "Dry-run с этими параметрами не найден или кэш пуст (будут платные вызовы API). Продолжить? (y/N)"
    if ($ans -notin @("y", "Y", "yes", "YES")) {
        Write-Host "Отменено пользователем."
        exit 0
    }
}

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$waveDir = Join-Path $wavesRoot $ts
New-Item -ItemType Directory -Force $waveDir | Out-Null
$runLog = Join-Path $waveDir "run.log"
$summaryMd = Join-Path $waveDir "summary.md"

# Сохраняем и выставляем env только для дочернего запуска (.env не трогаем)
$savedEnv = @{}
$envMap = @{}
$prep.env.PSObject.Properties | ForEach-Object {
    $k = $_.Name
    $v = [string]$_.Value
    $savedEnv[$k] = [Environment]::GetEnvironmentVariable($k, "Process")
    $envMap[$k] = $v
    [Environment]::SetEnvironmentVariable($k, $v, "Process")
}
# В run_wave.ps1 полный лог идёт в run.log, а консоль фильтруется в ForEach-Object
foreach ($extraK in @("CONSOLE_VERBOSE", "LOG_FILE")) {
    $savedEnv[$extraK] = [Environment]::GetEnvironmentVariable($extraK, "Process")
}
[Environment]::SetEnvironmentVariable("CONSOLE_VERBOSE", "1", "Process")
[Environment]::SetEnvironmentVariable("LOG_FILE", "", "Process")

$exitCode = 0
try {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    Push-Location $repoRoot
    try {
        if (Test-Path $runLog) { Remove-Item $runLog -Force }
        python -u rewr-transl.py 2>&1 | ForEach-Object {
            $line = $_.ToString()
            $line | Out-File -FilePath $runLog -Append -Encoding utf8
            if ($line -match "\| ERROR \|" -or $line -match "Прогресс:\s*\d+/\d+\s+рецептов" -or $line -match "Остановка волны:") {
                Write-Host $line
            }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
        $ErrorActionPreference = $prevEAP
    }
} finally {
    foreach ($k in $savedEnv.Keys) {
        [Environment]::SetEnvironmentVariable($k, $savedEnv[$k], "Process")
    }
}

if (-not $Live -and $exitCode -eq 0) {
    Set-Content -Path $dryMarker -Value $ts -Encoding utf8
}

$envJsonPath = Join-Path $waveDir "env.json"
$envJsonStr = ($envMap | ConvertTo-Json -Compress)
[System.IO.File]::WriteAllText($envJsonPath, $envJsonStr, (New-Object System.Text.UTF8Encoding($false)))

$prevEAPSum = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$sumOutput = python $helper summarize --summary $summaryMd --log $runLog --env-file $envJsonPath --exit-code $exitCode --ts $ts 2>&1
$sumExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAPSum

if ($sumExit -ne 0) {
    $errReason = (($sumOutput | ForEach-Object { $_.ToString() }) -join " ").Trim()
    Write-Host "Сводка не создана: $errReason"
    if ($exitCode -eq 0) { $exitCode = $sumExit }
    exit $exitCode
}

$shortSummary = (($sumOutput | ForEach-Object { $_.ToString() }) -join "`n").Trim()
if ($shortSummary) {
    Write-Host $shortSummary
} else {
    Write-Host "Готово (exit $exitCode). Сводка: $summaryMd"
}
exit $exitCode
