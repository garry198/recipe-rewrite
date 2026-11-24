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

Скрипт `rewr-transl.py` работает **только с таблицей `recipe_rewrite`** (таблицы `recipes3` и `price_history` в коде не читаются и не изменяются).

1. `get_db_connection()` (`rewr-transl.py:924–929`) — подключение к MariaDB (`autocommit=False`).
2. `fetch_rewrite_rows()` (`rewr-transl.py:932–945`, вызов в `rewr-transl.py:1357`) — чтение исходных строк:
   - `SELECT id AS rewrite_row_id, recipe_id, variant_type, dish_name_variant AS dish_name, ingredients_list_variant AS ingredients_list, cooking_instructions_variant AS cooking_instructions, serves, prep_time_minutes, cook_time_minutes FROM recipe_rewrite WHERE id IN (...) ORDER BY id ASC`
3. `save_rewritten_recipe()` (`rewr-transl.py:966–1015`, вызов в `rewr-transl.py:1614`):
   - при `APP_MODE == "rewrite"` (`rewr-transl.py:971–980`):
     `UPDATE recipe_rewrite SET ingredients_list_variant = ?, cooking_instructions_variant = ?, instructions_hash = ?, serves = ?, ai_model = ?, updated_at = NOW() WHERE id = ?`
   - при `APP_MODE == "translate"` (`rewr-transl.py:982–1013`):
     `INSERT INTO recipe_rewrite (recipe_id, market, locale, variant_type, dish_name_variant, ingredients_list_variant, cooking_instructions_variant, instructions_hash, serves, prep_time_minutes, cook_time_minutes, currency, calories_per_person, proteins_per_person, fats_per_person, carbohydrates_per_person, netcarbs_per_person, ai_model, created_at, updated_at) SELECT recipe_id, ?, ?, variant_type, ?, ?, ?, ?, serves, prep_time_minutes, cook_time_minutes, ?, calories_per_person, proteins_per_person, fats_per_person, carbohydrates_per_person, netcarbs_per_person, ?, NOW(), NOW() FROM recipe_rewrite WHERE id = ? ON DUPLICATE KEY UPDATE dish_name_variant = VALUES(dish_name_variant), ingredients_list_variant = VALUES(ingredients_list_variant), cooking_instructions_variant = VALUES(cooking_instructions_variant), instructions_hash = VALUES(instructions_hash), currency = VALUES(currency), ai_model = VALUES(ai_model), updated_at = NOW()`
4. Транзакции: `connection.commit()` (`rewr-transl.py:1619`), `connection.rollback()` (`rewr-transl.py:1632`, `1641`).

---

## 4. Таблица «поле — кто пишет — кто читает (по коду этого репозитория)»

> Для смежных пайплайнов (вне этого репозитория) указано «неизвестно».

### `recipe_rewrite`

| Поле | Кто пишет (по коду этого репозитория) | Кто читает (по коду этого репозитория) |
|---|---|---|
| `id` | БД (`AUTO_INCREMENT`); внешние пайплайны: неизвестно | `rewr-transl.py` (`fetch_rewrite_rows`, `WHERE id = ?` в `save_rewritten_recipe`) |
| `recipe_id` | `rewr-transl.py` (`translate`: `INSERT` из исходной строки); иначе неизвестно | `rewr-transl.py` (`fetch_rewrite_rows`, `translate`: `SELECT` из исходной строки) |
| `market` | `rewr-transl.py` (`translate`: `INSERT` = `TARGET_MARKET`); иначе неизвестно | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `locale` | `rewr-transl.py` (`translate`: `INSERT` = `TARGET_LOCALE`); иначе неизвестно | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `variant_type` | `rewr-transl.py` (`translate`: `INSERT` из исходной строки); иначе неизвестно | `rewr-transl.py` (`fetch_rewrite_rows`, `translate`: `SELECT` из исходной строки) |
| `cooking_instructions_variant` | `rewr-transl.py` (`rewrite`: `UPDATE`; `translate`: `INSERT` / `ON DUPLICATE KEY UPDATE`) | `rewr-transl.py` (`fetch_rewrite_rows`) |
| `search_text` | в этом репозитории не пишется; внешние пайплайны: неизвестно | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `instructions_hash` | `rewr-transl.py` (`rewrite`: `UPDATE`; `translate`: `INSERT` / `ON DUPLICATE KEY UPDATE`) | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `status` | в этом репозитории явно не пишется (при `INSERT` получает `DEFAULT 'active'`); внешние пайплайны: неизвестно | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `created_at` | `rewr-transl.py` (`translate`: `INSERT` = `NOW()`); БД `DEFAULT current_timestamp()` | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `updated_at` | `rewr-transl.py` (`rewrite`: `UPDATE` = `NOW()`; `translate`: `INSERT` / `ON DUPLICATE KEY UPDATE` = `NOW()`); БД `ON UPDATE` | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `ai_model` | `rewr-transl.py` (`rewrite`: `UPDATE`; `translate`: `INSERT` / `ON DUPLICATE KEY UPDATE`) | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `dish_name_variant` | `rewr-transl.py` (`translate`: `INSERT` / `ON DUPLICATE KEY UPDATE`); иначе неизвестно | `rewr-transl.py` (`fetch_rewrite_rows`) |
| `ingredients_list_variant` | `rewr-transl.py` (`rewrite`: `UPDATE`; `translate`: `INSERT` / `ON DUPLICATE KEY UPDATE`) | `rewr-transl.py` (`fetch_rewrite_rows`) |
| `calories_per_person` | `rewr-transl.py` (`translate`: `INSERT` копированием из исходной строки); иначе неизвестно | `rewr-transl.py` (`translate`: `SELECT` из исходной строки) |
| `proteins_per_person` | `rewr-transl.py` (`translate`: `INSERT` копированием из исходной строки); иначе неизвестно | `rewr-transl.py` (`translate`: `SELECT` из исходной строки) |
| `fats_per_person` | `rewr-transl.py` (`translate`: `INSERT` копированием из исходной строки); иначе неизвестно | `rewr-transl.py` (`translate`: `SELECT` из исходной строки) |
| `carbohydrates_per_person` | `rewr-transl.py` (`translate`: `INSERT` копированием из исходной строки); иначе неизвестно | `rewr-transl.py` (`translate`: `SELECT` из исходной строки) |
| `netcarbs_per_person` | `rewr-transl.py` (`translate`: `INSERT` копированием из исходной строки); иначе неизвестно | `rewr-transl.py` (`translate`: `SELECT` из исходной строки) |
| `serves` | `rewr-transl.py` (`rewrite`: `UPDATE`; `translate`: `INSERT` копированием из исходной строки) | `rewr-transl.py` (`fetch_rewrite_rows`, `translate`: `SELECT` из исходной строки) |
| `prep_time_minutes` | `rewr-transl.py` (`translate`: `INSERT` копированием из исходной строки); иначе неизвестно | `rewr-transl.py` (`fetch_rewrite_rows`, `translate`: `SELECT` из исходной строки) |
| `cook_time_minutes` | `rewr-transl.py` (`translate`: `INSERT` копированием из исходной строки); иначе неизвестно | `rewr-transl.py` (`fetch_rewrite_rows`, `translate`: `SELECT` из исходной строки) |
| `dish_cost_local` | в этом репозитории не пишется; внешние пайплайны: неизвестно | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `currency` | `rewr-transl.py` (`translate`: `INSERT` / `ON DUPLICATE KEY UPDATE` = `TARGET_CURRENCY`); иначе неизвестно | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `nutrients_recalc_version` | в этом репозитории не пишется; внешние пайплайны: неизвестно | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `needs_reprice` | в этом репозитории явно не пишется (при `INSERT` получает `DEFAULT 0`); внешние пайплайны: неизвестно | в этом репозитории не читается; внешние пайплайны: неизвестно |
| `dish_cost_serves` | в этом репозитории не пишется; внешние пайплайны: неизвестно | в этом репозитории не читается; внешние пайплайны: неизвестно |

### `recipes3`
В коде этого репозитория (`rewr-transl.py`, `checks.py`) таблица `recipes3` не читается и не пишется. Для всех полей (`id`, `recipe_no`, `dish_name`, `dish_icon`, `diet_type`, `dish_type`, `main_ingredient`, `source_url`, `ingredients_list`, `cooking_instructions`, `calories_per_person`, `proteins_per_person`, `fats_per_person`, `carbohydrates_per_person`, `netcarbs_per_person`, `prep_time_minutes`, `cook_time_minutes`, `serves`, `recipe_entry_date`, `dish_cost`, `cost_updated_at`, `used_before`, `created_at`, `updated_at`, `normalized_name`, `ingredients_hash`, `ingredients_signature`):
- **Кто пишет:** неизвестно (в этом репозитории не пишется).
- **Кто читает:** неизвестно (в этом репозитории не читается).

### `price_history`
В коде этого репозитория (`rewr-transl.py`, `checks.py`) таблица `price_history` не читается и не пишется. Для всех полей (`id`, `rewrite_id`, `price_amount`, `currency`, `country_code`, `city`, `market`, `locale`, `price_date`, `priced_at`, `ai_model`, `price_source`, `ingredients_basis`, `pricing_confidence`, `is_current`, `nutrition_confidence`, `nutrition_readiness`, `nutrition_coverage`, `nutrition_version`, `calories_per_serving`):
- **Кто пишет:** неизвестно (в этом репозитории не пишется).
- **Кто читает:** неизвестно (в этом репозитории не читается).
