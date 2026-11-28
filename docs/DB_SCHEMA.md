# Схема БД и работа с ней в `recipe-rewrite`

Источник: MariaDB через MCP `mysql_ro` (только `SELECT` / `SHOW CREATE TABLE`) и анализ кода `rewr-transl.py`.

---

## 1. DDL таблиц (`SHOW CREATE TABLE`)

### `recipes3`

```sql
CREATE TABLE `recipes3` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `recipe_no` int(10) unsigned DEFAULT NULL,
  `dish_name` varchar(255) CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci NOT NULL,
  `dish_icon` varchar(10) DEFAULT NULL,
  `diet_type` varchar(100) CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci DEFAULT NULL,
  `dish_type` enum('snack','dessert','low carb','smoothie','coffee','cocktail','drink','sauce','spice','salad','bread','main_dish') NOT NULL,
  `main_ingredient` varchar(150) CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci DEFAULT NULL,
  `source_url` text CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci DEFAULT NULL,
  `ingredients_list` longtext CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci NOT NULL,
  `cooking_instructions` longtext CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci NOT NULL,
  `calories_per_person` decimal(8,2) DEFAULT NULL,
  `proteins_per_person` decimal(8,2) DEFAULT NULL,
  `fats_per_person` decimal(8,2) DEFAULT NULL,
  `carbohydrates_per_person` decimal(8,2) DEFAULT NULL,
  `netcarbs_per_person` decimal(8,2) DEFAULT NULL,
  `prep_time_minutes` int(10) unsigned DEFAULT NULL,
  `cook_time_minutes` int(10) unsigned DEFAULT NULL,
  `serves` int(10) unsigned DEFAULT NULL,
  `recipe_entry_date` datetime NOT NULL DEFAULT current_timestamp(),
  `dish_cost` decimal(10,2) DEFAULT NULL,
  `cost_updated_at` datetime DEFAULT NULL,
  `used_before` tinyint(1) NOT NULL DEFAULT 0,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `normalized_name` varchar(255) CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci DEFAULT NULL,
  `ingredients_hash` char(56) CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci DEFAULT NULL,
  `ingredients_signature` text CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `dish_name` (`dish_name`),
  UNIQUE KEY `normalized_name` (`normalized_name`,`ingredients_hash`),
  UNIQUE KEY `source_url` (`source_url`) USING HASH
) ENGINE=InnoDB AUTO_INCREMENT=5304 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
```

### `recipe_rewrite`

```sql
CREATE TABLE `recipe_rewrite` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `recipe_id` int(10) unsigned NOT NULL,
  `market` varchar(50) NOT NULL,
  `locale` varchar(20) DEFAULT NULL,
  `variant_type` varchar(50) NOT NULL DEFAULT 'rewrite',
  `cooking_instructions_variant` longtext NOT NULL,
  `search_text` longtext DEFAULT NULL,
  `instructions_hash` char(64) DEFAULT NULL,
  `status` enum('draft','active','archived') NOT NULL DEFAULT 'active',
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `ai_model` text NOT NULL,
  `dish_name_variant` varchar(255) DEFAULT NULL,
  `ingredients_list_variant` longtext DEFAULT NULL,
  `calories_per_person` decimal(8,2) DEFAULT NULL,
  `proteins_per_person` decimal(8,2) DEFAULT NULL,
  `fats_per_person` decimal(8,2) DEFAULT NULL,
  `carbohydrates_per_person` decimal(8,2) DEFAULT NULL,
  `netcarbs_per_person` decimal(8,2) DEFAULT NULL,
  `serves` int(10) unsigned DEFAULT NULL,
  `prep_time_minutes` int(10) unsigned DEFAULT NULL,
  `cook_time_minutes` int(10) unsigned DEFAULT NULL,
  `dish_cost_local` decimal(10,2) DEFAULT NULL,
  `currency` varchar(10) DEFAULT 'USD',
  `nutrients_recalc_version` varchar(50) DEFAULT NULL,
  `needs_reprice` tinyint(1) NOT NULL DEFAULT 0,
  `dish_cost_serves` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_recipe_market_variant` (`recipe_id`,`market`,`locale`,`variant_type`),
  KEY `idx_recipe_id` (`recipe_id`),
  KEY `idx_market_status` (`market`,`status`),
  KEY `idx_rewrite_nutrients_recalc` (`market`,`status`,`variant_type`,`nutrients_recalc_version`,`id`),
  FULLTEXT KEY `ft_ingredients` (`ingredients_list_variant`),
  CONSTRAINT `fk_recipe_variants_recipe` FOREIGN KEY (`recipe_id`) REFERENCES `recipes3` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=9384 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
```

### `price_history`

```sql
CREATE TABLE `price_history` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `rewrite_id` bigint(20) unsigned NOT NULL,
  `price_amount` decimal(10,2) NOT NULL,
  `currency` varchar(10) NOT NULL DEFAULT 'USD',
  `country_code` char(2) DEFAULT NULL,
  `city` varchar(100) DEFAULT NULL,
  `market` varchar(50) DEFAULT NULL,
  `locale` varchar(20) DEFAULT NULL,
  `price_date` date NOT NULL,
  `priced_at` datetime NOT NULL DEFAULT current_timestamp(),
  `ai_model` varchar(100) DEFAULT NULL,
  `price_source` varchar(50) NOT NULL DEFAULT 'ai_estimate',
  `ingredients_basis` longtext DEFAULT NULL,
  `pricing_confidence` decimal(5,2) DEFAULT NULL,
  `is_current` tinyint(1) NOT NULL DEFAULT 1,
  `nutrition_confidence` decimal(5,2) DEFAULT NULL,
  `nutrition_readiness` enum('READY','READY_WITH_WARNING','REVIEW_REQUIRED','BLOCKED') DEFAULT NULL,
  `nutrition_coverage` decimal(5,2) DEFAULT NULL,
  `nutrition_version` varchar(40) DEFAULT NULL,
  `calories_per_serving` decimal(8,2) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_rewrite_price_snapshot` (`rewrite_id`,`price_date`,`currency`,`city`,`locale`,`price_source`),
  KEY `idx_rewrite_id` (`rewrite_id`),
  KEY `idx_price_date` (`price_date`),
  KEY `idx_city_locale` (`city`,`locale`),
  KEY `ix_ph_nutrition_version` (`nutrition_version`,`is_current`),
  CONSTRAINT `fk_price_history_rewrite` FOREIGN KEY (`rewrite_id`) REFERENCES `recipe_rewrite` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=62241 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
```

---

## 2. Распределение `status`, `market`, `locale` (`GROUP BY`)

### `recipes3`
Колонки `status`, `market`, `locale` в таблице `recipes3` отсутствуют.

### `recipe_rewrite`

| Колонка | Значение | Количество строк |
|---|---|---:|
| `status` | `active` | 4139 |
| `market` | `US` | 4128 |
| `market` | `fr` | 11 |
| `locale` | `en_US` | 4128 |
| `locale` | `fr_FR` | 11 |

### `price_history`
Колонка `status` в таблице `price_history` отсутствует.

| Колонка | Значение | Количество строк |
|---|---|---:|
| `market` | `us` | 30531 |
| `locale` | `en_US` | 30531 |

---

## 3. Места чтения и записи БД в `rewr-transl.py`

Скрипт `rewr-transl.py` работает с таблицами `recipes3` (только `SELECT` в режиме `adapt` и `FORCE`) и `recipe_rewrite` (`SELECT`, `INSERT`, `UPDATE`). Таблица `price_history` в коде не читается и не изменяется.

1. `get_db_connection()` — подключение к MariaDB (`autocommit=False`).
2. `fetch_rewrite_rows()` — чтение исходных строк из `recipe_rewrite` (режимы `rewrite` и `translate`).
3. `fetch_adapt_rows()` / `fetch_used_before_map()` — чтение исходных рецептов из `recipes3` (с `LEFT JOIN recipe_rewrite`) в режиме `adapt` и `ADAPT_FORCE=1`.
4. `fetch_recipe_rewrite_for_backup()` — чтение существующих строк `recipe_rewrite` для резервной копии перед `ADAPT_FORCE=1`.
5. `save_adapted_recipe()` (режим `adapt` и `ADAPT_FORCE=1`):
   - `INSERT INTO recipe_rewrite (...) VALUES (..., needs_reprice=1, dish_cost_serves=serves, ...)` — КБЖУ, цена и `nutrients_recalc_version` не заполняются (владелец — `cost-nutrients`).
   - при `ADAPT_FORCE=1` добавляется `ON DUPLICATE KEY UPDATE`: обновляет текст/порции/`status`/`dish_cost_serves`/`needs_reprice = 1`/`ai_model`/`updated_at = NOW()` (при `FORCE_KEEP_DISH_NAME=1` поле `dish_name_variant` сохраняется прежним) и **сбрасывает в `NULL`** поля `calories_per_person`, `proteins_per_person`, `fats_per_person`, `carbohydrates_per_person`, `netcarbs_per_person`, `dish_cost_local`, `nutrients_recalc_version`.
6. `save_rewritten_recipe()`:
   - при `APP_MODE == "rewrite"`: `UPDATE recipe_rewrite SET ingredients_list_variant, cooking_instructions_variant, instructions_hash, serves, dish_cost_serves, needs_reprice = 1, ai_model, updated_at = NOW() WHERE id = ?`.
   - при `APP_MODE == "translate"`: `INSERT INTO recipe_rewrite (...) ... ON DUPLICATE KEY UPDATE ...` с `needs_reprice = 1`, `dish_cost_serves = serves` (без `dish_cost_local`).
7. Транзакции: в `commit_batch_with_ids()` сначала пишется ID-файл для `cost-nutrients` (`append_ids_file`), затем вызывается `connection.commit()`; при ошибке записи файла или батча выполняется `connection.rollback()`.

---

## 4. Таблица «поле — кто пишет — кто читает (по коду этого репозитория)»

> Для смежных пайплайнов (`cost-nutrients`, `recipe-ingest` и др.) указано владение по контрактам (`docs/CONTRACTS.md`).

### `recipe_rewrite`

| Поле | Кто пишет (по коду этого репозитория) | Кто читает (по коду этого репозитория) |
|---|---|---|
| `id` | БД (`AUTO_INCREMENT`); `LAST_INSERT_ID(id)` при `FORCE`/`translate` | `rewr-transl.py` (`fetch_rewrite_rows`, `fetch_recipe_rewrite_for_backup`, `rollback_force.py`) |
| `recipe_id` | `rewr-transl.py` (`adapt`/`FORCE`: из `recipes3.id`; `translate`: из исходной строки) | `rewr-transl.py` (`fetch_rewrite_rows`, `fetch_adapt_rows`, `fetch_recipe_rewrite_for_backup`) |
| `market` | `rewr-transl.py` (`adapt`: `ADAPT_MARKET`; `translate`: `TARGET_MARKET`) | `rewr-transl.py` (`build_adapt_select`, `fetch_recipe_rewrite_for_backup`) |
| `locale` | `rewr-transl.py` (`adapt`: `ADAPT_LOCALE`; `translate`: `TARGET_LOCALE`) | `rewr-transl.py` (`build_adapt_select`, `fetch_recipe_rewrite_for_backup`) |
| `variant_type` | `rewr-transl.py` (`adapt`: `'rewrite'`; `translate`: из исходной строки) | `rewr-transl.py` (`fetch_rewrite_rows`, `build_adapt_select`) |
| `cooking_instructions_variant` | `rewr-transl.py` (`adapt`/`FORCE`, `rewrite`, `translate`) | `rewr-transl.py` (`fetch_rewrite_rows`, `fetch_recipe_rewrite_for_backup`) |
| `search_text` | не пишется | не читается |
| `instructions_hash` | `rewr-transl.py` (`adapt`/`FORCE`, `rewrite`, `translate`) | `rewr-transl.py` (`fetch_recipe_rewrite_for_backup`) |
| `status` | `rewr-transl.py` (`adapt`/`FORCE`: `ADAPT_STATUS`, по умолчанию `'draft'`/`'active'`) | `rewr-transl.py` (`fetch_recipe_rewrite_for_backup`) |
| `created_at` | БД `DEFAULT current_timestamp()` / `translate` `NOW()` | `rewr-transl.py` (`fetch_recipe_rewrite_for_backup`) |
| `updated_at` | `rewr-transl.py` (`NOW()`) / БД `ON UPDATE` | `rewr-transl.py` (`fetch_recipe_rewrite_for_backup`) |
| `ai_model` | `rewr-transl.py` (`adapt`/`FORCE`, `rewrite`, `translate`) | `rewr-transl.py` (`fetch_recipe_rewrite_for_backup`) |
| `dish_name_variant` | `rewr-transl.py` (`adapt`: из `recipes3.dish_name` с `normalize_dish_name()`; при `FORCE` и `FORCE_KEEP_DISH_NAME=1` не перезаписывается; `translate`: перевод с `normalize_dish_name()`) | `rewr-transl.py` (`fetch_rewrite_rows`, `fetch_recipe_rewrite_for_backup`) |
| `ingredients_list_variant` | `rewr-transl.py` (`adapt`/`FORCE`, `rewrite`, `translate`) | `rewr-transl.py` (`fetch_rewrite_rows`, `fetch_recipe_rewrite_for_backup`) |
| `calories_per_person` | `adapt`: не пишется (`NULL`); `FORCE`: сбрасывается в `NULL`; `translate`: копируется; владелец расчёта — `cost-nutrients` | `rewr-transl.py` (`translate`, `fetch_recipe_rewrite_for_backup`) |
| `proteins_per_person` | `adapt`: не пишется (`NULL`); `FORCE`: сбрасывается в `NULL`; `translate`: копируется; владелец расчёта — `cost-nutrients` | `rewr-transl.py` (`translate`, `fetch_recipe_rewrite_for_backup`) |
| `fats_per_person` | `adapt`: не пишется (`NULL`); `FORCE`: сбрасывается в `NULL`; `translate`: копируется; владелец расчёта — `cost-nutrients` | `rewr-transl.py` (`translate`, `fetch_recipe_rewrite_for_backup`) |
| `carbohydrates_per_person` | `adapt`: не пишется (`NULL`); `FORCE`: сбрасывается в `NULL`; `translate`: копируется; владелец расчёта — `cost-nutrients` | `rewr-transl.py` (`translate`, `fetch_recipe_rewrite_for_backup`) |
| `netcarbs_per_person` | `adapt`: не пишется (`NULL`); `FORCE`: сбрасывается в `NULL`; `translate`: копируется; владелец расчёта — `cost-nutrients` | `rewr-transl.py` (`translate`, `fetch_recipe_rewrite_for_backup`) |
| `serves` | `rewr-transl.py` (`adapt`/`FORCE`, `rewrite`, `translate`) | `rewr-transl.py` (`fetch_rewrite_rows`, `fetch_recipe_rewrite_for_backup`) |
| `prep_time_minutes` | `rewr-transl.py` (`adapt`/`FORCE` из `recipes3`; `translate` из исходной строки) | `rewr-transl.py` (`fetch_rewrite_rows`, `fetch_recipe_rewrite_for_backup`) |
| `cook_time_minutes` | `rewr-transl.py` (`adapt`/`FORCE` из `recipes3`; `translate` из исходной строки) | `rewr-transl.py` (`fetch_rewrite_rows`, `fetch_recipe_rewrite_for_backup`) |
| `dish_cost_local` | `adapt`: не пишется (`NULL`); `FORCE`: сбрасывается в `NULL`; владелец расчёта — `cost-nutrients` | `rewr-transl.py` (`fetch_recipe_rewrite_for_backup`) |
| `currency` | `rewr-transl.py` (`adapt`/`FORCE`: `'USD'`; `translate`: `TARGET_CURRENCY`) | `rewr-transl.py` (`fetch_recipe_rewrite_for_backup`) |
| `nutrients_recalc_version` | `adapt`: не пишется (`NULL`); `FORCE`: сбрасывается в `NULL`; владелец — `cost-nutrients` | `rewr-transl.py` (`fetch_recipe_rewrite_for_backup`) |
| `needs_reprice` | `rewr-transl.py` (`adapt`/`FORCE`, `rewrite`, `translate`: выставляет `1`) | `cost-nutrients` |
| `dish_cost_serves` | `rewr-transl.py` (`adapt`/`FORCE`, `rewrite`, `translate`: `= serves`) | `cost-nutrients` |

### `recipes3`
В `rewr-transl.py` таблица `recipes3` используется **только на чтение (`SELECT`)** в режиме `adapt` / `ADAPT_FORCE=1`:
- **Кто пишет:** внешние пайплайны (`recipe-ingest` и др.); `recipe-rewrite` в `recipes3` никогда не пишет.
- **Кто читает:** `rewr-transl.py` (`fetch_adapt_rows` читает `id`, `dish_name`, `ingredients_list`, `cooking_instructions`, `serves`, `prep_time_minutes`, `cook_time_minutes`, `dish_type`; `fetch_used_before_map` читает `id`, `used_before`).

### `price_history`
В коде этого репозитория (`rewr-transl.py`, `checks.py`) таблица `price_history` не читается и не пишется.
- **Кто пишет:** `cost-nutrients` (в этом репозитории не пишется).
- **Кто читает:** внешние пайплайны (в этом репозитории не читается).
