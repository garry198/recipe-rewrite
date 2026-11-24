"""Тесты режима APP_MODE=adapt (без БД и API).
Запуск: python tests/test_adapt.py [путь_к_скрипту]
"""
import csv
import hashlib
import logging
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent.parent
SCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "rewr-transl.py"

src = SCRIPT.read_text(encoding="utf-8")


class FakeIntegrityError(Exception):
    pass


class FakeMariaDB:
    IntegrityError = FakeIntegrityError


_test_logger = logging.getLogger("test_adapt")
_test_logger.addHandler(logging.StreamHandler(sys.stdout))
ns: dict[str, Any] = {
    "re": re,
    "csv": csv,
    "hashlib": hashlib,
    "logging": logging,
    "Path": Path,
    "Any": Any,
    "Sequence": Sequence,
    "BaseModel": BaseModel,
    "Field": Field,
    "mariadb": FakeMariaDB,
    "LOGGER": _test_logger,
    "VALID_ADAPT_STATUSES": ("draft", "active", "archived"),
}
ns["EXPECTED_CSV_HEADER"] = [
    "Rewrite Row ID", "Recipe ID", "Previous Dish Name", "Rewritten Dish Name",
    "Previous Servings", "Rewritten Servings",
    "Prep Time (min)", "Cook Time (min)",
    "Previous Ingredients", "Rewritten Ingredients",
    "Previous Instructions", "Rewritten Instructions",
    "recipe_id", "recipe_rewrite_id", "Status",
]
exec(src[src.index("class RewrittenRecipe"): src.index("class SessionCostTracker")], ns)
exec(src[src.index("def append_csv_batch"): src.index("def parse_rewrite_ids")], ns)
exec(src[src.index("def normalize_servings"): src.index("def target_servings_for")], ns)

build_adapt_select = ns["build_adapt_select"]
build_adapt_insert_sql = ns["build_adapt_insert_sql"]
build_rewrite_update_sql = ns["build_rewrite_update_sql"]
build_translate_insert_sql = ns["build_translate_insert_sql"]
validate_adapt_config = ns["validate_adapt_config"]
save_adapted_recipe = ns["save_adapted_recipe"]
append_csv_batch = ns["append_csv_batch"]
append_ids_file = ns["append_ids_file"]
normalize_servings = ns["normalize_servings"]
RewrittenRecipe = ns["RewrittenRecipe"]

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("OK   " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# 1. build_adapt_select
sql, params = build_adapt_select("US", "en_US", 10)
check("select default: w.id IS NULL and LIMIT", "w.id IS NULL" in sql and "ORDER BY r.id LIMIT ?" in sql)
check("select default params", params == ("US", "en_US", 10), repr(params))

sql, params = build_adapt_select("US", "en_US", 5, recipe_ids=[1, 2, 3])
check("select with ids", "w.id IS NULL" in sql and "r.id IN (?, ?, ?)" in sql)
check("select with ids params", params == ("US", "en_US", 1, 2, 3, 5), repr(params))

sql, params = build_adapt_select("US", "en_US", 7, dish_types=["dessert", "snack"])
check("select with dish_types", "r.dish_type IN (?, ?)" in sql)
check("select with dish_types params", params == ("US", "en_US", "dessert", "snack", 7), repr(params))

sql, params = build_adapt_select("US", "en_US", 3, recipe_ids=[5], dish_types=["bread"], force=True)
check("select force=True drops w.id IS NULL", "w.id IS NULL" not in sql and "r.id IN (?)" in sql and "r.dish_type IN (?)" in sql)
check("select force=True params order", params == ("US", "en_US", 5, "bread", 3), repr(params))

sql, params = build_adapt_select("US", "en_US", 2, force=True)
check("select force=True without filters", "WHERE" not in sql and params == ("US", "en_US", 2), repr(params))

# 2. INSERT SQL (adapt, rewrite, translate)
ins_sql = build_adapt_insert_sql(force=False)
cols_part = ins_sql.split("(", 1)[1].split(")", 1)[0]
vals_part = ins_sql.split("VALUES", 1)[1].split("(", 1)[1].split(")", 1)[0]
cols = [c.strip() for c in cols_part.split(",")]
vals = [v.strip() for v in vals_part.split(",")]
check("INSERT cols == vals count", len(cols) == len(vals) == 16, f"{len(cols)} vs {len(vals)}")
check("INSERT has needs_reprice and dish_cost_serves", "needs_reprice" in cols and "dish_cost_serves" in cols)
forbidden = {"calories_per_person", "proteins_per_person", "fats_per_person", "carbohydrates_per_person",
             "netcarbs_per_person", "dish_cost_local", "nutrients_recalc_version"}
check("INSERT excludes nutrition/cost cols", forbidden.isdisjoint(set(cols)))

force_sql = build_adapt_insert_sql(force=True)
check(
    "INSERT force=True resets nutrition/cost/version and sets needs_reprice/dish_cost_serves",
    "ON DUPLICATE KEY UPDATE" in force_sql
    and "updated_at = NOW()" in force_sql
    and "dish_cost_serves = VALUES(dish_cost_serves)" in force_sql
    and "needs_reprice = 1" in force_sql
    and all(f"{col} = NULL" in force_sql for col in forbidden),
)

rew_sql = build_rewrite_update_sql()
check(
    "rewrite UPDATE has needs_reprice=1, dish_cost_serves=?, no nutrition/cost",
    "needs_reprice = 1" in rew_sql and "dish_cost_serves = ?" in rew_sql
    and all(col not in rew_sql for col in forbidden),
)

tr_sql = build_translate_insert_sql()
check(
    "translate INSERT/UPDATE has needs_reprice and dish_cost_serves, no dish_cost_local",
    "needs_reprice" in tr_sql and "dish_cost_serves" in tr_sql
    and "needs_reprice = 1" in tr_sql and "dish_cost_serves = VALUES(dish_cost_serves)" in tr_sql
    and "dish_cost_local" not in tr_sql,
)

# 3. Validation
for st in ("draft", "active", "archived"):
    validate_adapt_config(st, 1)
bad_status = False
try:
    validate_adapt_config("deleted", 10)
except ValueError:
    bad_status = True
check("validate_adapt_config rejects bad status", bad_status)

bad_limit = False
try:
    validate_adapt_config("active", 0)
except ValueError:
    bad_limit = True
check("validate_adapt_config rejects limit <= 0", bad_limit)


# 4. Fake cursor: duplicate skip, success lastrowid, DRY_RUN & no serves CSV
class FakeCursor:
    def __init__(self, raise_dup: bool = False, lastrowid: int = 777):
        self.raise_dup = raise_dup
        self.lastrowid = lastrowid
        self.executed: list[tuple[str, Any]] = []
        self.closed = False

    def execute(self, sql: str, params: Any = None) -> None:
        if self.raise_dup:
            raise FakeIntegrityError("Duplicate entry")
        self.executed.append((sql, params))

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, cursor_obj: FakeCursor):
        self._cursor = cursor_obj

    def cursor(self) -> FakeCursor:
        return self._cursor


src_row = {
    "recipe_id": 42,
    "dish_name": "Original Keto Cake",
    "serves": 4,
    "prep_time_minutes": 10,
    "cook_time_minutes": 25,
    "ingredients_list": "1 cup almond flour",
    "cooking_instructions": "Bake.",
}
item = RewrittenRecipe(
    rewrite_row_id=42,
    dish_name="Ignored Model Name",
    servings=4,
    ingredients_list="1 cup almond flour",
    cooking_instructions="Bake at 350°F.",
)

dup_cur = FakeCursor(raise_dup=True)
rid_out, status_out = save_adapted_recipe(
    FakeConnection(dup_cur), src_row, item, "gemini-3.8-flash", "US", "en_US", "active", force=False,
)
check("duplicate key skipped on force=False", rid_out is None and status_out == "exists, skipped" and dup_cur.closed)

ok_cur = FakeCursor(raise_dup=False, lastrowid=999)
rid_out, status_out = save_adapted_recipe(
    FakeConnection(ok_cur), src_row, item, "gemini-3.8-flash", "US", "en_US", "active", force=False,
)
check("insert returns lastrowid and keeps original dish_name", rid_out == 999 and status_out == "OK" and ok_cur.executed[0][1][4] == "Original Keto Cake")
check("insert passes dish_cost_serves == servings", ok_cur.executed[0][1][8] == 4 and ok_cur.executed[0][1][9] == 4)

# DRY_RUN + no serves + ids file
check("no serves detected for NULL/0", normalize_servings(None) is None and normalize_servings(0) is None)
with tempfile.TemporaryDirectory() as tmp:
    csv_path = Path(tmp) / "adapt_log.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh, delimiter=";").writerow(ns["EXPECTED_CSV_HEADER"])
    append_csv_batch(csv_path, [
        ({"recipe_id": 10, "dish_name": "Bad", "serves": 0}, None, None, "no serves"),
        (src_row, item, None, "DRY_RUN"),
    ])
    rows = list(csv.reader(csv_path.open("r", encoding="utf-8-sig"), delimiter=";"))
    check("CSV logs no serves and DRY_RUN with recipe_id/recipe_rewrite_id",
          len(rows) == 3 and rows[1][-3:] == ["10", "", "no serves"] and rows[2][-3:] == ["42", "", "DRY_RUN"])

    dry_ids_file = Path(tmp) / "dry_ids.txt"
    n_dry = append_ids_file(dry_ids_file, [])
    check("append_ids_file does not create file for empty list (DRY_RUN/skipped)", n_dry == 0 and not dry_ids_file.exists())

    live_ids_file = Path(tmp) / "rewrite_ids_adapt_test.txt"
    n1 = append_ids_file(live_ids_file, [101, 102])
    n2 = append_ids_file(live_ids_file, [103])
    raw_bytes = live_ids_file.read_bytes()
    check("append_ids_file writes UTF-8 LF without header or CRLF",
          n1 == 2 and n2 == 1 and raw_bytes == b"101\n102\n103\n" and b"\r\n" not in raw_bytes)

print(f"\nИТОГО adapt: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
