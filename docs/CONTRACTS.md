# Контракты со смежными пайплайнами

> Статус: частично подтверждено в `r01` (схема БД и код `rewr-transl.py`, см. [DB_SCHEMA.md](DB_SCHEMA.md)).
> Пункты, относящиеся к смежным пайплайнам вне этого репозитория, оставлены с пометкой «ПРОВЕРИТЬ / неизвестно».

## Общая БД (MariaDB)
| Таблица | Кто пишет | Кто читает |
|---|---|---|
| `recipes3` | recipe-ingest (в коде этого репозитория не подтверждено: неизвестно) | **rewrite (`rewr-transl.py`, режим `adapt`)**; перенос в `recipe_rewrite` без ИИ-рерайта (если есть) делает внешний процесс владельца — неизвестно |
| `recipe_rewrite` | **rewrite (этот проект)** (`rewr-transl.py`: `INSERT` / `ON DUPLICATE KEY UPDATE` в режиме `adapt`, `UPDATE` в режиме `rewrite`, `INSERT ... ON DUPLICATE KEY UPDATE` в режиме `translate`) | **rewrite (`rewr-transl.py`)**; cost-nutrients, Book Pipeline — неизвестно по коду этого репозитория |
| `price_history` | в этом репозитории не пишется (cost-nutrients — неизвестно) | в этом репозитории не читается (Book Pipeline — неизвестно) |

Подтверждённые факты по `recipe_rewrite` (подробнее в [DB_SCHEMA.md](DB_SCHEMA.md)):
- Точные колонки: `id`, `recipe_id`, `market`, `locale`, `variant_type`, `cooking_instructions_variant`, `search_text`, `instructions_hash`, `status` (`enum('draft','active','archived')`, `DEFAULT 'active'`), `created_at`, `updated_at`, `ai_model`, `dish_name_variant`, `ingredients_list_variant`, `calories_per_person`, `proteins_per_person`, `fats_per_person`, `carbohydrates_per_person`, `netcarbs_per_person`, `serves`, `prep_time_minutes`, `cook_time_minutes`, `dish_cost_local`, `currency`, `nutrients_recalc_version`, `needs_reprice`, `dish_cost_serves`.
- В БД сейчас все 4139 строк `recipe_rewrite` имеют `status = 'active'`; `market = 'US'` / `locale = 'en_US'` (4128 строк), `market = 'fr'` / `locale = 'fr_FR'` (11 строк). В `price_history`: `market = 'us'` / `locale = 'en_US'` (30531 строка).
- Collation колонки `market` в обеих таблицах (`recipe_rewrite` и `price_history`) — `utf8mb4_unicode_ci` (регистронезависимая). Book Pipeline (`all-in-one2`) фильтрует `rr.market = 'us'` (нижний регистр) и совпадает с `'US'` только за счёт регистронезависимой collation (**риск: при смене collation выборка книги станет пустой**).
- ПРОВЕРИТЬ (неизвестно по коду этого репозитория): какие колонки `recipe_rewrite` читают другие пайплайны и какие значения `status` означают «готово к расчёту цен/КБЖУ».

## Что rewrite обязан гарантировать downstream
1. В режиме `translate` строка в `recipe_rewrite` создаётся/обновляется с `TARGET_MARKET`, `TARGET_LOCALE` и `TARGET_CURRENCY` в принятом регистре (`'US'`/`en_US`, `'fr'`/`fr_FR`, для немецкого — `'de'`/`de_DE`/`EUR`, `market` в нижнем регистре); в режиме `rewrite` обновляется существующая строка по `id` без изменения `market`/`locale`. Значения `market`/`locale` писать строго в том регистре, который уже есть в таблице: `'US'`/`en_US`, `'fr'`/`fr_FR`, для немецкого — `'de'`/`de_DE` (существующие строки не переименовывать).
2. Колонка в БД — `serves` (не `servings`); `servings` существует только в схеме ответа модели (Pydantic). Значение `serves` в `recipe_rewrite` = целевое число порций (задаётся кодом в `rewr-transl.py`, не моделью).
3. Список ингредиентов: одна строка на ингредиент, без маркеров; только US-меры для `en_US`
   (cost-nutrients парсит количества — формат менять нельзя без согласования, ПРОВЕРИТЬ `nutrition_parser_v12`).
4. Инструкции: обычный текст, абзац на шаг, пустая строка между шагами, без нумерации (Book Pipeline сам нумерует
   — ПРОВЕРИТЬ; его промпт пересчёта порций v4.1 ожидает нумерованные шаги, возможный конфликт форматов).
5. Нет КБЖУ в тексте (считает cost-nutrients).
6. В режиме `adapt` (`recipes3` → `recipe_rewrite`): `INSERT INTO recipe_rewrite` с ключом `(recipe_id, market, locale, variant_type)` (`variant_type='rewrite'`, `market='US'`, `locale='en_US'`, `status=ADAPT_STATUS` ∈ `{draft, active, archived}`, `currency='USD'`, `needs_reprice=1`, `dish_cost_serves=serves`); поля КБЖУ, `dish_cost_local`, `nutrients_recalc_version` остаются `NULL` (при `ADAPT_FORCE=1` обнуляются, т.к. после смены порций устарели; `ADAPT_FORCE=1` требует явный `ADAPT_IDS`, а `ADAPT_EXCLUDE_IDS` исключает проблемные/существующие `recipes3.id` из выборки).
7. В режиме `rewrite` (`UPDATE`) и `translate` (`INSERT`/`ON DUPLICATE KEY UPDATE`) также выставляются `needs_reprice=1` и `dish_cost_serves=serves` (в `translate` `dish_cost_local` не копируется).
8. После успешного `commit` батча при `DRY_RUN=false` id вставленных/обновлённых строк `recipe_rewrite.id` пишутся в `logs\rewrite_ids_<mode>_<YYYYmmdd_HHMMSS>.txt` (настройка `IDS_OUT_DIR`, UTF-8, LF).
9. Контракт `cost-nutrients` и цикл запуска:
   - `cost-nutrients` отбирает рецепты по `locale` или списку `id` минус строки со свежей (`<= 21` дня) текущей ценой в `price_history`; по `nutrients_recalc_version IS NULL` и `needs_reprice` не ищет.
   - `needs_reprice` `cost-nutrients` только сбрасывает (`= 0` при записи результата); при выгрузке для книги (`Book Pipeline`) строки с `needs_reprice <> 0` пропускаются.
   - Для изменённых (а не новых) строк нужен явный `--ids-file`.
   - Рабочий цикл: `dry-run`, затем волна записи с `--ids-file` по `procedures/db_write.md`.
10. Перезапись при `ADAPT_FORCE=1` (порядок для владельца):
    1. Убедиться, что исправление источника в `recipes3` (i10/i11) выполнено.
    2. В `.env` задать `APP_MODE=adapt`, `ADAPT_FORCE=1`, `ADAPT_IDS=<список recipes3.id>`, `DRY_RUN=1` → запустить и проверить отчёт `logs/force_report_<ts>.md` (и `.csv`).
    3. Выставить `DRY_RUN=0` → скрипт создаст резервную копию в `backups/recipe_rewrite_force_<ts>.jsonl` (+ `.meta.json`) до первого запроса к API и обновит строки.
    4. Запустить `cost-nutrients` по сгенерированному `logs/rewrite_ids_adapt_<ts>.txt`.
    5. При проблемах откатить: `python tools/rollback_force.py backups/recipe_rewrite_force_<ts>.jsonl` (сначала без `--apply`, затем с `--apply` и при необходимости `--delete-inserted`).

## Чего rewrite не делает
Не считает цены и КБЖУ, не пересчитывает порции для книги (это Book Pipeline), не парсит сайты (recipe-ingest).

## Изменение контракта
Любое изменение формата ингредиентов/инструкций или значений `status/market/locale` — только по заданию Claude после
сверки с владельцем остальных пайплайнов. Воркер такие изменения сам не делает.
