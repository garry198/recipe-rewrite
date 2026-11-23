# One-time: first commit + push to the existing private repo. Run from project root.
$ErrorActionPreference = "Stop"
$remote = "https://github.com/garry198/recipe-rewrite.git"

if (Test-Path .git) { Write-Host "git repo exists, skipping init" } else { git init -b main }

python tests\test_regressions.py
if ($LASTEXITCODE -ne 0) { throw "Tests failed, commit cancelled" }

git add -A
$bad = git diff --cached --name-only | Select-String -Pattern '(^|/)\.env$|\.csv$|\.sqlite3|gemini_cache'
if ($bad) { git reset; throw "Forbidden files staged: $bad" }

git commit -m "r00: baseline (rewr-transl v22, checks c9, regression tests, worker docs)"
if (-not (git remote)) { git remote add origin $remote }
git push -u origin main
git tag v22-baseline
git push origin v22-baseline
git checkout -b worker/r01
Write-Host "Done. Next: protect main on GitHub, give Antigravity tasks/r01.md (branch worker/r01 is ready)."
