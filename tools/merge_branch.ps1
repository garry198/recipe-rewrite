# Merge a worker branch into main locally (run by the owner after Claude's approval).
# Usage:  .\tools\merge_branch.ps1 worker/rNN [-DryRun] [-Offline]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Branch,
    [switch]$DryRun,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param([string]$Label, [scriptblock]$Action)
    if ($DryRun) {
        Write-Host "[DRY-RUN] $Label"
        return
    }
    & $Action
}

$origBranch = (git rev-parse --abbrev-ref HEAD).Trim()

# 1. Check clean working tree
$dirty = git status --porcelain
if ($dirty) {
    Write-Error "Рабочее дерево не чистое. Закоммитьте или уберите изменения перед слиянием."
    exit 1
}

if ($Branch -eq "main") {
    Write-Error "Нельзя сливать main сам в себя."
    exit 1
}

if (-not $DryRun) {
    git show-ref --verify --quiet "refs/heads/$Branch"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Локальная ветка '$Branch' не найдена."
        exit 1
    }
}

# 2. Checkout main and pull origin/main (continue locally if origin is unreachable)
Invoke-Step "git checkout main" {
    git checkout main
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось переключиться на main."
    }
}

if ($Offline) {
    if ($DryRun) {
        Write-Host "[DRY-RUN][OFFLINE] Пропуск git pull origin main"
    } else {
        Write-Host "[OFFLINE] Пропуск git pull origin main"
    }
} else {
    Invoke-Step "git pull origin main (при недоступности origin — предупреждение и продолжение локально)" {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        git pull origin main 2>&1 | ForEach-Object { Write-Host $_ }
        $pullExit = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($pullExit -ne 0) {
            Write-Warning "origin недоступен или git pull origin main не удался — продолжаем локально."
        }
    }
}

# 3. Merge --no-ff
Invoke-Step "git merge --no-ff $Branch -m `"Merge $Branch`"" {
    git merge --no-ff $Branch -m "Merge $Branch"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Конфликт или ошибка при слиянии. Выполняю git merge --abort..."
        git merge --abort 2>$null
        if ($origBranch -and $origBranch -ne "main" -and $origBranch -ne "HEAD") {
            git checkout $origBranch 2>$null
        }
        Write-Error "Слияние '$Branch' прервано."
        exit 1
    }
}

# 4. Run regression tests and py_compile
$testSummary = "(dry-run)"
Invoke-Step "python tests\test_regressions.py; python -m py_compile rewr-transl.py checks.py" {
    $testOutput = python tests\test_regressions.py 2>&1
    $testExit = $LASTEXITCODE
    $testOutput | ForEach-Object { Write-Host $_ }

    python -m py_compile rewr-transl.py checks.py 2>&1 | ForEach-Object { Write-Host $_ }
    $compileExit = $LASTEXITCODE

    if ($testExit -ne 0 -or $compileExit -ne 0) {
        Write-Warning "Проверки не пройдены. Откатываю main к ORIG_HEAD..."
        git reset --hard ORIG_HEAD
        if ($origBranch -and $origBranch -ne "main" -and $origBranch -ne "HEAD") {
            git checkout $origBranch 2>$null
        }
        Write-Error "Тесты или py_compile завершились с ошибкой. Изменения откачены."
        exit 1
    }
    $script:testSummary = ($testOutput | Select-String -Pattern "ИТОГО:" | Select-Object -Last 1).ToString().Trim()
    if (-not $script:testSummary) { $script:testSummary = "тесты пройдены (exit 0)" }
}

# 5. Push main
if ($Offline) {
    if ($DryRun) {
        Write-Host "[DRY-RUN][OFFLINE] Пропуск git push origin main"
    } else {
        Write-Host "[OFFLINE] Пропуск git push origin main (слияние только локальное)"
    }
} else {
    Invoke-Step "git push origin main" {
        git push origin main
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Не удалось отправить main в origin. Откатываю main к ORIG_HEAD..."
            git reset --hard ORIG_HEAD
            if ($origBranch -and $origBranch -ne "main" -and $origBranch -ne "HEAD") {
                git checkout $origBranch 2>$null
            }
            Write-Error "git push origin main не удался."
            exit 1
        }
    }
}

# 6. Delete branch locally and on origin if present
if ($Offline) {
    Invoke-Step "git branch -d $Branch (офлайн: удаление на origin пропущено)" {
        git branch -d $Branch
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Не удалось удалить локальную ветку '$Branch'."
        }
    }
} else {
    Invoke-Step "git branch -d $Branch; удаление на origin если есть" {
        git branch -d $Branch
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Не удалось удалить локальную ветку '$Branch'."
        }
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $remoteRef = git ls-remote --heads origin $Branch 2>$null
        $ErrorActionPreference = $prevEAP
        if ($remoteRef) {
            git push origin --delete $Branch
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Не удалось удалить удалённую ветку '$Branch' на origin."
            }
        }
    }
}

# 7. Summary
if ($DryRun) {
    Write-Host "[DRY-RUN] Итог: merge_branch.ps1 завершён без изменений репозитория."
} else {
    $mergeHash = (git rev-parse --short HEAD).Trim()
    Write-Host "Готово. Хэш слияния: $mergeHash | Тесты: $testSummary"
}
