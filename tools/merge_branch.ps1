# Merge a worker branch into main locally (run by the owner after Claude's approval).
# Usage:  .\tools\merge_branch.ps1 worker/rNN [-DryRun] [-Offline] [-SkipTests]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Branch,
    [switch]$DryRun,
    [switch]$Offline,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

function Invoke-Step {
    param([string]$Label, [scriptblock]$Action)
    if ($DryRun) {
        Write-Host "[DRY-RUN] $Label"
        return
    }
    & $Action
}

function Run-AllChecks {
    param([string]$ContextLabel)
    $suites = & "$PSScriptRoot\test_suites.ps1"
    $summaries = @()
    $allOk = $true

    foreach ($suite in $suites) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $out = python $suite 2>&1
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP

        $lines = $out | ForEach-Object { $_.ToString() }
        $important = $lines | Where-Object { $_ -match "ИТОГО|OK   golden|FAIL|Traceback|Error|Exception" }
        foreach ($ln in $important) { Write-Host "  [$suite] $ln" }

        if ($code -ne 0) {
            Write-Warning "Провал ($ContextLabel): $suite (exit $code)"
            $allOk = $false
            break
        }
        $lastSummary = ($lines | Where-Object { $_ -match "ИТОГО|OK   golden" } | Select-Object -Last 1)
        if ($lastSummary) { $summaries += $lastSummary.Trim() }
    }

    if ($allOk) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $compOut = python -m py_compile rewr-transl.py checks.py 2>&1
        $compCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($compCode -ne 0) {
            $compOut | ForEach-Object { Write-Host $_ }
            Write-Warning "Провал py_compile ($ContextLabel)."
            $allOk = $false
        }
    }

    return @{ Ok = $allOk; Summary = ($summaries -join " | ") }
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

# 1.5. Pre-merge tests on target branch
if ($SkipTests) {
    Write-Host "[SKIP-TESTS] Проверка тестов перед слиянием пропущена (-SkipTests)."
} else {
    Invoke-Step "Pre-merge проверка всех тестов из tools/test_suites.ps1 и py_compile на ветке $Branch" {
        if ($origBranch -ne $Branch) {
            git checkout $Branch
            if ($LASTEXITCODE -ne 0) { throw "Не удалось переключиться на $Branch." }
        }
        $pre = Run-AllChecks -ContextLabel "pre-merge:$Branch"
        if (-not $pre.Ok) {
            if ($origBranch -and $origBranch -ne $Branch -and $origBranch -ne "HEAD") {
                git checkout $origBranch 2>$null
            }
            Write-Error "Тесты на ветке '$Branch' не пройдены. Слияние отменено."
            exit 1
        }
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

# 4. Post-merge tests and py_compile
$testSummary = "(dry-run)"
if ($SkipTests) {
    $testSummary = "пропущено (-SkipTests)"
} else {
    Invoke-Step "Post-merge проверка всех тестов из tools/test_suites.ps1 и py_compile на main" {
        $post = Run-AllChecks -ContextLabel "post-merge:main"
        if (-not $post.Ok) {
            Write-Warning "Проверки после слияния не пройдены. Откатываю main к ORIG_HEAD..."
            git reset --hard ORIG_HEAD
            if ($origBranch -and $origBranch -ne "main" -and $origBranch -ne "HEAD") {
                git checkout $origBranch 2>$null
            }
            Write-Error "Тесты или py_compile после слияния завершились с ошибкой. Изменения откачены."
            exit 1
        }
        $script:testSummary = if ($post.Summary) { $post.Summary } else { "все тесты пройдены" }
    }
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
