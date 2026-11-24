# Review pack for Claude: diff + stat + tests + worker notes.
# Usage:  .\tools\make_review_pack.ps1 r01
param([Parameter(Mandatory=$true)][string]$Task)
$ErrorActionPreference = "Stop"
$out = "reviews\$Task"; New-Item -ItemType Directory -Force $out | Out-Null
git diff main...HEAD --stat           | Out-File "$out\stat.txt" -Encoding utf8
git diff main...HEAD -U3 -- . ':!*.csv' ':!logs' | Out-File "$out\diff.patch" -Encoding utf8
git log main..HEAD --format="%h %s" | Out-File "$out\commits.txt" -Encoding utf8
python tests\test_regressions.py 2>&1 | Out-File "$out\tests.txt" -Encoding utf8
python tests\test_units_golden.py 2>&1 | Out-File "$out\tests.txt" -Append -Encoding utf8
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
