# Рабочий цикл

Claude → задание `tasks/rNN.md` (код `rNN` + мини-заголовок; подзадачи `rNN.1…`) → владелец кладёт файл в репозиторий →
Antigravity делает в ветке `worker/rNN` (коммитит локально, в `main` не коммитит и не пушит) → `.\tools\make_review_pack.ps1 rNN` и останавливается → владелец отправляет Claude `diff.patch`, `stat.txt`,
`tests.txt`, `worker_notes.md` (+ нужные куски логов) → Claude проверяет: соответствие заданию, регрессии, стыки с другими
пайплайнами, стоимость → вердикт: «слить» (владелец запускает `.\tools\merge_branch.ps1 worker/rNN`, файл задания в `tasks/done/`) или «доработать» (воркер дополняет ту же ветку `worker/rNN` подзадачами `rNN.1`, `rNN.2`… и пересобирает пакет).

Claude проверяет не только код, но и: не выросла ли цена одного рецепта, не сломан ли контракт из `CONTRACTS.md`,
не инвалидируется ли кэш без причины.

## Ветки и слияние
- Репозиторий **приватный, отдельный**: `recipe-rewrite` (как `recipe-ingest`). Не смешиваем с ветками Book Pipeline и
  cost-nutrients: у них свои циклы релизов, а здесь живут промпты, логи и кэш с коммерческим содержимым.
- `main` — только проверенное Claude. Слияние в `main` выполняет только владелец локально через `.\tools\merge_branch.ps1 worker/rNN` (без GitHub PR).
- Воркер работает только в ветке `worker/rNN`, коммитит локально, в `main` не коммитит и не пушит; при доработках дополняет ту же ветку (`rNN.1`, `rNN.2`…).
- Теги на значимые состояния: `v22-baseline`, далее `v23`… (совпадают с номером в имени старых скриптов).
- Стыки с другими пайплайнами описываются в `CONTRACTS.md`; чужой код здесь не хранится, только читается по ссылке.
- Порядок запуска волны (`DRY_RUN=1` → проверка `logs/force_report_*.md` → `DRY_RUN=0` → передача `logs/rewrite_ids_adapt_<ts>.txt` в `cost-nutrients` через `_hub` → откат через `tools/rollback_force.py`) описан в [docs/STATE.md](STATE.md) («Как проходит волна»).
- **Учёт расходов (`api_usage.csv`)**: к каждой строке добавляются `account_label` (дефолт `"main"`, согласовано с `cost-nutrients (q41.8)` и `Book Pipeline`), `key_fp` (первые 8 символов SHA-256 ключа, сам ключ никогда не логируется), `run_hash`, `app_mode`, `recipe_ids`, `dry_run`, `run_ts`. При смене заголовка старый файл автоматически переименовывается в `api_usage_legacy_<ts>.csv`.
  Сводка по аккаунтам: `python tools/api_usage_report.py [--since YYYY-MM-DD] [--by account|day|run_hash]` или в PowerShell:
  `Import-Csv -Delimiter ';' api_usage.csv | Group-Object account_label | ForEach-Object { [PSCustomObject]@{ account = if ($_.Name) { $_.Name } else { 'legacy' }; requests = $_.Count; cost_usd = ($_.Group | Measure-Object -Property cost_usd -Sum).Sum } }`
- **Проверка при слиянии (`tools/merge_branch.ps1`)**: перед `git merge` скрипт переключается на проверяемую ветку и запускает все наборы тестов из `tools/test_suites.ps1` + `python -m py_compile` (а затем повторно после слияния на `main`). При любой ошибке слияние отменяется; опция `-SkipTests` пропускает проверки, `-DryRun` печатает шаги без изменений, `-Offline` работает без `origin`.
- **Логи в UTF-8 в Windows PowerShell 5.1**:
  - Используйте `python rewr-transl.py 2>&1 | Out-File logs\run.log -Encoding utf8` (и при необходимости `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $env:PYTHONUTF8 = "1"`).
  - **Внимание:** оператор `>` и `Tee-Object` в PowerShell 5.1 по умолчанию пишут в **UTF-16LE**, что ломает UTF-8-инструменты и diff — для UTF-8 используйте `Out-File -Encoding utf8`.
- **Пакет сдачи `reviews/rNN/`**: собирается `.\tools\make_review_pack.ps1 rNN` и содержит `diff.patch`, `stat.txt`, `commits.txt`, `tests.txt`, `compile.txt`, `worker_notes.md` (и при разрешении конфликта слияния — `merge_resolution.patch`).
- **Эталонный прогон**: эталонный набор `recipes3.id` и порядок сравнения до/после описаны в [docs/REFERENCE_IDS.md](REFERENCE_IDS.md) (прогон с `DRY_RUN=1` запускает владелец; воркер API не запускает).
- **Обмен между проектами**: через `C:\Мои документы\Py\_hub\inbox\<кому>\` по `PROTOCOL.md` (см. [AGENTS.md](../AGENTS.md)).
