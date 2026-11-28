# Текущее состояние (baseline = rewr-transl_22, CHECKS c11, Python 3.14)

## Что работает
- Окружение: **Python 3.14** (`.venv`), зависимости закреплены в `requirements.txt`.
- Рерайт EN: промпт v3_6 + детерминированная пред/постобработка (масштабирование к 4 порциям при >8, склейка строк,
  бренды, ссылки на рецепты, безопасные температуры 165/160°F, проверки prep/form/style, строгий режим + автоисправление).
- Перевод FR/DE: плейсхолдеры `[[Q#]]/[[T#]]`, конвертация единиц, термостат духовки, валидация перевода.
- Кэш по хэшу (промпт, модель, порции, режим, локаль, версии UNITS/SCALE/CHECKS + отклонения политики масштабирования), учёт токенов `api_usage.csv`.
- 14 наборов тестов (`tools/test_suites.ps1`), без БД и API.

## Известные проблемы
1. Расчёт стоимости не сходится с биллингом Google (≈$0.0022/рецепт в логе). Нужны токены и сумма из консоли Google.
2. Перевод только FR; немецкого нет. Локаль зашита в код (словарь, `OVEN_MAP`, `CUP_GRAMS`), не в данных.
3. Кэш — JSON с ключом по хэшу промпта: любая правка текста промпта обнуляет кэш.

## Дорожная карта (порядок)
- r01 — инвентаризация БД и контрактов (без кода) (**слита** в `main`)
- r02 — промпт v3_5 как дефолт (дубликат v3_6 удалён), порог масштабирования 8, тесты (**слита** в `main`)
- r03 — дизайн-документ «локали как данные» (`locales/fr_FR.yaml`, `de_DE.yaml`): словарь, меры, духовка, форматы записи (**слита** в `main`)
- r04 — реализация слоя локалей, FR переносится без изменения результата (регресс по логам) (**слита** в `main`)
- r05 — DE: промпт, меры (г/мл, EL/TL, Prise), духовка (Ober-/Unterhitze, Umluft), тесты (**слита** в `main`)
- r06 — режим `adapt`: `recipes3` → рерайт → `INSERT` в `recipe_rewrite` + контракт `cost-nutrients` (**слита** в `main`); `CHECKS c10`
- r07 — выбор целевого числа порций без дробных яиц (`choose_target_servings`, `SCALE_VERSION s21` — сброс кэша) (**слита** в `main`)
- r08 — пропуск рецептов со ссылкой на другой рецепт (`SKIP_RECIPE_REFS=1`, `has_recipe_ref`) (**слита** в `main`)
- r09 — переделка рерайтов с потерянными количествами: backup, `rollback_force.py`, отчёт `force_report` (**слита** в `main`)
- r10 — учёт аккаунта и контекста запуска в `api_usage.csv` (`GEMINI_ACCOUNT_LABEL`, дефолт `"main"` — согласовано с `cost-nutrients (q41.8)` и `Book Pipeline`, `key_fp`, `api_usage_report.py`; **слита** в `main`)
- r11 — надёжность процесса: тесты перед слиянием (`tools/test_suites.ps1`), повтор API + split, лимит `MAX_RUN_COST_USD` (**слита** в `main`)
- r11b — регламент сдачи (`AGENTS.md`, шаблон `worker_notes.md`, `merge_resolution.patch`) и эталонный набор (`docs/REFERENCE_IDS.md`) (**слита** в `main`)
- r11c — защита FORCE: `FORCE_KEEP_DISH_NAME=1`, `RESCALE_SKIP_PAREN_QTY=1`, `SCALE_VERSION s22` (сброс кэша) (**слита** в `main`)
- r11d — обновление стартовых файлов для ИИ (`STATE.md`, `AGENTS.md`, `WORKFLOW.md`) (**слита** в `main`)
- r12 — расплывчатые единицы без веса (`locales/vague_units.yaml`, `v3_6`, `UNITS u3`, `CHECKS c11`) и защита выпечки в форме (`RESCALE_SKIP_BAKEWARE=1`, `SCALE s23`) (**слита** в `main`)
- r13 — запуск волны одной командой (`tools/run_wave.ps1`, `wave_exclude.txt`, `logs/waves/<ts>/summary.md`) (**слита** в `main`)
- r14 — прогресс волны в консоли (`run_wave.ps1` + строка прогресса по рецептам) и быстрый отказ при исчерпании месячного лимита Google / 3 подряд неудавшихся батчах (**слита** в `main`)
- r15 — тихая консоль волны (фильтр в `run_wave.ps1`, `ConsoleLogFilter` и файловый лог в `rewr-transl.py`) и передача `env` через `env.json` (`--env-file`) в `summarize` (**слита** в `main`)
- r16 — правки по независимому ревью без смены версий кэша: точные дроби в скобках, меры упаковок/банок, конфликт метрики и US (>15%), нормализация NBSP + защита парсинга, запись ID-файла до `commit()`, политика масштабирования в `run_hash` без инвалидации, закрепление `requirements.txt` и аудиты БД (**слита** в `main`)
- r17 — очистка брендов (`tools/brand_cleanup.py`, `locales/brand_neutral.yaml`, тесты) (**слита** в `main`)
- r18 — нормализация названия блюда при записи в БД (`tools/name_normalize.py`, `locales/dish_name.yaml`: `Low-Carb`, Title Case для ALL CAPS, малые слова, аббревиатуры) без смены версий кэша (в `worker/r18`)
- r19 (отложено) — кэш: SQLite с `policy-version` (идея из ветки Book Pipeline, `rescale_cache.py`)
- r20 (отложено) — таксономия единиц в YAML (идея из `units.yaml`/`measures.py`), строгие проверки дробных штучных позиций

## Оперативный статус (на 2026-11-25)

**Адаптация `recipes3` → `recipe_rewrite` (волны A–F):**
- Волны **A–F** проведены через `tools/run_wave.ps1` (`r13–r15` слиты в `main`); кэш волн A–F в `gemini_cache/` сохранён и остаётся валидным (версии `u3/s23/c11` не менялись).
- **Очередь ручных ID** (`wave_exclude.txt` / требуют ручной обработки): `1570, 2261, 4865, 4877, 4897, 4920, 4967, 5025, 5067, 5102`.
- Проведены read-only аудиты по БД: `logs/audit_paren_measures.csv`, `logs/audit_vague_with_measure.csv`, `logs/audit_metric_conflicts.csv` (данные не правились).

**Переделка старых рерайтов (`FORCE`, источники без количеств):**
- Первая волна 23 строки: 22 `active`, `1622` `draft` (нужен продукт «keto bread mix» в `cost-nutrients`).
- Волна B (`recipes3.id` `1796, 2408, 2499, 2505, 3801, 3813` → `recipe_rewrite.id` `4364, 4938, 5031, 5037, 8432, 8444`): записана.

**Открыто / отложено:**
- Отложено: SQLite-кэш (`r17`), таксономия единиц (`r18`), правки `rollback_force.py`, `translate serves`, учёт упавших запросов, проверка идентичности ингредиентов в `fidelity`, округление сетки дробей.
- Расхождение биллинга Google с оценкой (в `api_usage.csv` пишутся `account_label`/`key_fp`).
- DE на живом API не пробовали.

## Как проходит волна
1. Dry-run (по умолчанию `DRY_RUN=1`, `.env` не правится, исключения берутся из `wave_exclude.txt`):
   `.\tools\run_wave.ps1 -Ids "2248,2250,..."` (или `-IdsFile wave_a.txt` / `-Limit 10`, при переделке добавить `-Force`, при другом размере порций `-Scale N`).
2. Проверить `logs\waves\<ts>\summary.md` (и `logs\force_report_*.md` при `-Force`), затем выполнить боевой запуск из кэша:
   `.\tools\run_wave.ps1 -Ids "2248,2250,..." -Live` (при отсутствии предшествующего dry-run скрипт запросит подтверждение `y/N`).
3. Записанные `recipe_rewrite.id` взять из `logs/rewrite_ids_adapt_<ts>.txt` (путь указан в `summary.md`) и отправить в `cost-nutrients` через `_hub`.
4. `cost-nutrients` считает КБЖУ и цену (`needs_reprice` → `0`).
5. Claude проверяет КБЖУ и цены; активацию (`UPDATE status='active'`) и правки текста выполняет только владелец.
6. Откат `FORCE`: `tools/rollback_force.py` по `backups/recipe_rewrite_force_<ts>.jsonl` (`dry-run` по умолчанию, `--apply` для записи).

Процесс: воркер в `worker/rNN` (локальные коммиты) → `make_review_pack.ps1` → приёмка Claude → слияние владельцем через `.\tools\merge_branch.ps1 worker/rNN` (без GitHub PR).

Источник идей для r06–r07: файлы ветки Book Pipeline (читать как референс, не копировать целиком: там US-ориентированные меры).
