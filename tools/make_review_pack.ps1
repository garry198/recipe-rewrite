# Review pack for Claude: diff + stat + tests + worker notes.
# Usage:  .\tools\make_review_pack.ps1 r01
param([Parameter(Mandatory=$true)][string]$Task)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$out = "reviews\$Task"; New-Item -ItemType Directory -Force $out | Out-Null
git diff main...HEAD --stat           | Out-File "$out\stat.txt" -Encoding utf8
git diff main...HEAD -U3 -- . ':!*.csv' ':!logs' | Out-File "$out\diff.patch" -Encoding utf8
git log main..HEAD --format="%h %s" | Out-File "$out\commits.txt" -Encoding utf8
$testSuites = & "$PSScriptRoot\test_suites.ps1"
$first = $true
foreach ($suite in $testSuites) {
    if ($first) {
        python $suite 2>&1 | Out-File "$out\tests.txt" -Encoding utf8
        $first = $false
    } else {
        python $suite 2>&1 | Out-File "$out\tests.txt" -Append -Encoding utf8
    }
    if ($LASTEXITCODE -ne 0) { throw "Тест упал: $suite" }
}
python -m py_compile rewr-transl.py checks.py 2>&1 | Out-File "$out\compile.txt" -Encoding utf8
if (-not (Test-Path "$out\worker_notes.md")) {
@"
# $Task - worker notes
## Done
## NOT done / deviations
## Questions for Claude
## How to verify
"@ | Out-File "$out\worker_notes.md" -Encoding utf8
}
Write-Host "Pack: $out. Fill worker_notes.md, then send diff.patch, stat.txt, tests.txt, worker_notes.md to Claude"
