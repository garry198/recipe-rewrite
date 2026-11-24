"""Тесты защиты от дефектов FORCE: FORCE_KEEP_DISH_NAME и RESCALE_SKIP_PAREN_QTY (без БД, API и .env).
Запуск: python tests/test_force_defects.py [путь_к_скрипту]
"""
import csv
import difflib
import json
import logging
import os
import re
import sys
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent.parent
SCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "rewr-transl.py"

src = SCRIPT.read_text(encoding="utf-8")


class FakeMariaDB:
    Error = Exception
    IntegrityError = Exception


warnings_log: list[str] = []


class ListHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        warnings_log.append(record.getMessage())


_test_logger = logging.getLogger("test_force_defects")
_test_logger.setLevel(logging.WARNING)
_test_logger.addHandler(ListHandler())

ns: dict[str, Any] = {
    "os": os,
    "re": re,
    "csv": csv,
    "difflib": difflib,
    "json": json,
    "logging": logging,
    "Path": Path,
    "Any": Any,
    "Sequence": Sequence,
    "date": date,
    "datetime": datetime,
    "Decimal": Decimal,
    "mariadb": FakeMariaDB,
    "LOGGER": _test_logger,
    "RESCALE_TARGET_SERVINGS": 4,
    "RESCALE_ONLY_IF_MORE_THAN": 8,
    "RESCALE_MAX_DIVISOR": 2.5,
    "RESCALE_SMART": True,
    "RESCALE_SKIP_PAREN_QTY": True,
    "RESCALE_CANDIDATES": [4, 6, 8, 12, 16, 24, 32],
    "RESCALE_COUNT_RE": re.compile(
        r"\b(?:large|medium|small|extra[- ]large|whole)?\s*(?:eggs?|egg\s+yolks?|egg\s+whites?)\b",
        re.IGNORECASE,
    ),
    "_UNI_FRAC": {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3", "⅛": "1/8"},
}
exec(src[src.index("def _to_float"): src.index("def _fr")], ns)
exec(src[src.index("def _json_default"): src.index("def save_adapted_recipe")], ns)
exec(src[src.index("def target_servings_for"): src.index("def build_sent_ingredients")], ns)

choose = ns["choose_target_servings"]
build_adapt_insert_sql = ns["build_adapt_insert_sql"]
write_force_report = ns["write_force_report"]

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("OK   " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# 1. 3813-подобный рецепт: 24 порции, количество только в скобках -> 24 + WARNING
warnings_log.clear()
ing_3813 = "pork skins, trimmed and cut into pieces (900 g/ 2 lbs)\n1 tsp sea salt"
got_3813 = choose(24, ing_3813, row_id=3813)
check("3813: paren-only qty disables scaling (24 -> 24) and logs warning",
      got_3813 == 24 and any("ID 3813: количество только в скобках" in w for w in warnings_log),
      f"got={got_3813}, warnings={warnings_log}")

# 2. При RESCALE_SKIP_PAREN_QTY=0 масштабирование работает
ns["RESCALE_SKIP_PAREN_QTY"] = False
check("RESCALE_SKIP_PAREN_QTY=0 scales 3813 normally (24 -> 16)", choose(24, ing_3813) == 16)
ns["RESCALE_SKIP_PAREN_QTY"] = True

# 3. Рецепт без строк со скобками (12 порций, яйца) -> 6 как раньше
check("normal 12 srv with 4 eggs -> 6", choose(12, "4 large eggs\n1 cup almond flour") == 6)

# 4. Строки с optional / to taste / for serving / for garnish / for coating не отключают масштабирование
ing_opt = (
    "2 cups almond flour\n"
    "sea salt, to taste (about 5 g)\n"
    "fresh herbs, optional (15 g)\n"
    "sour cream, for serving (100 g)\n"
    "chopped parsley, for garnish (10 g)\n"
    "coconut flakes, for coating (50 g)"
)
check("optional/to taste/for serving/garnish/coating do not disable scaling (24 -> 16)",
      choose(24, ing_opt) == 16)

# 5. FORCE_KEEP_DISH_NAME: сохраняет старое название при UPDATE, не влияет на INSERT
sql_keep = build_adapt_insert_sql(force=True, keep_dish_name=True)
ins_part, upd_part = sql_keep.split("ON DUPLICATE KEY UPDATE", 1)
check("FORCE_KEEP_DISH_NAME=1 keeps dish_name_variant in INSERT, omits from UPDATE",
      "dish_name_variant" in ins_part and "dish_name_variant" not in upd_part)

sql_overwrite = build_adapt_insert_sql(force=True, keep_dish_name=False)
check("FORCE_KEEP_DISH_NAME=0 includes dish_name_variant in UPDATE",
      "dish_name_variant = VALUES(dish_name_variant)" in sql_overwrite)

sql_plain = build_adapt_insert_sql(force=False, keep_dish_name=True)
check("keep_dish_name does not change plain INSERT",
      "dish_name_variant" in sql_plain and "ON DUPLICATE KEY UPDATE" not in sql_plain)

# 6. force_report: показывает 'сохранено' и предупреждение 'модель предложила'
with tempfile.TemporaryDirectory() as tmp:
    md_p, csv_p = write_force_report(
        Path(tmp),
        "20261124_203000",
        [{
            "recipe_id": 3801,
            "rewrite_id": 8432,
            "used_before": 2,
            "dish_name_before": "Chicken Cracklings",
            "dish_name_after": "сохранено",
            "dish_name_kept": True,
            "model_dish_name": "HOW TO MAKE CHICKEN CRACKLINGS",
            "serves_before": 6,
            "serves_after": 6,
            "ingredients_before": "chicken skin",
            "ingredients_after": "1 lb chicken skin",
        }],
        {},
        dry_run=True,
    )
    md_text = md_p.read_text(encoding="utf-8")
    csv_text = csv_p.read_text(encoding="utf-8-sig")
    check("force_report shows 'сохранено' and model proposal warning",
          "`Chicken Cracklings` -> `сохранено`" in md_text
          and "модель предложила: `HOW TO MAKE CHICKEN CRACKLINGS`" in md_text
          and ";сохранено;" in csv_text)

print(f"\nИТОГО force_defects: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
