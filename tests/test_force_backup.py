"""Тесты резервного копирования, отката и отчёта FORCE (без БД, API и .env).
Запуск: python tests/test_force_backup.py [путь_к_скрипту]
"""
import csv
import difflib
import json
import logging
import subprocess
import sys
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent.parent
SCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "rewr-transl.py"
ROLLBACK_SCRIPT = HERE / "tools" / "rollback_force.py"

src = SCRIPT.read_text(encoding="utf-8")


class FakeMariaDBError(Exception):
    pass


class FakeMariaDB:
    Error = FakeMariaDBError


_test_logger = logging.getLogger("test_force_backup")
_test_logger.addHandler(logging.StreamHandler(sys.stdout))

ns: dict[str, Any] = {
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
}
exec(src[src.index("def _json_default"): src.index("def build_adapt_insert_sql")], ns)

sys.path.insert(0, str(HERE / "tools"))
import rollback_force as rb  # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("OK   " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None, fail_sql: bool = False):
        self._rows = rows or []
        self.fail_sql = fail_sql
        self.executed: list[tuple[str, Any]] = []
        self.closed = False

    def execute(self, sql: str, params: Any = None) -> None:
        if self.fail_sql:
            raise FakeMariaDBError("Unknown column 'used_before'")
        self.executed.append((sql, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, cursor_obj: FakeCursor):
        self._cursor = cursor_obj
        self.committed = False
        self.rolled_back = False

    def cursor(self, dictionary: bool = False) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


long_text = "line\n" * 500
sample_row = {
    "id": 4781,
    "recipe_id": 2247,
    "market": "US",
    "locale": "en_US",
    "variant_type": "rewrite",
    "status": "active",
    "dish_name_variant": "Keto Pie",
    "ingredients_list_variant": long_text,
    "cooking_instructions_variant": "Bake.",
    "serves": 8,
    "calories_per_person": Decimal("320.50"),
    "dish_cost_local": None,
    "created_at": datetime(2026, 11, 24, 12, 0, 0),
    "updated_at": date(2026, 11, 24),
}

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    # 1. Backup serialization (datetime, Decimal, None, long text, all columns)
    cur = FakeCursor(rows=[sample_row])
    conn = FakeConnection(cur)
    jsonl_p, meta_p, before_map = ns["create_force_backup"](
        conn, [2247, 9999], "US", "en_US", tmp_path / "backups", "20261124_120000",
    )
    loaded = json.loads(jsonl_p.read_text(encoding="utf-8").splitlines()[0])
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    check("backup serializes all columns, datetime, Decimal, None, long text",
          set(loaded.keys()) == set(sample_row.keys())
          and loaded["created_at"] == "2026-11-24T12:00:00"
          and loaded["updated_at"] == "2026-11-24"
          and loaded["calories_per_person"] == "320.50"
          and loaded["dish_cost_local"] is None
          and loaded["ingredients_list_variant"] == long_text)
    check("backup meta contains recipe_rewrite_ids and row_count",
          meta["recipe_rewrite_ids"] == [4781] and meta["row_count"] == 1 and 2247 in before_map)

    # 2. update_force_backup_meta
    ns["update_force_backup_meta"](meta_p, [4781, 9500], [9500])
    meta2 = json.loads(meta_p.read_text(encoding="utf-8"))
    check("update_force_backup_meta stores touched_ids and inserted_ids",
          meta2["touched_ids"] == [4781, 9500] and meta2["inserted_ids"] == [9500])

    # 3. Unwritable backup path stops before API
    blocking_file = tmp_path / "not_a_dir"
    blocking_file.write_text("x", encoding="utf-8")
    stopped = False
    try:
        ns["create_force_backup"](conn, [2247], "US", "en_US", blocking_file, "20261124_120001")
    except OSError:
        stopped = True
    check("create_force_backup raises OSError when path cannot be written", stopped)

    # 4. Rollback dry-run (no --apply) does not write
    rb_cur = FakeCursor()
    rb_conn = FakeConnection(rb_cur)
    res_dry = rb.run_rollback(jsonl_p, connection=rb_conn, apply=False, delete_inserted=True)
    check("rollback dry-run does not execute SQL or commit",
          res_dry["applied"] is False and len(rb_cur.executed) == 0 and not rb_conn.committed)

    # 5. Rollback --apply builds parameterized UPDATE and does not touch other ids
    res_apply = rb.run_rollback(jsonl_p, connection=rb_conn, apply=True, delete_inserted=False)
    check("rollback --apply runs parameterized UPDATE for backup id only",
          res_apply["applied"] is True
          and rb_conn.committed is True
          and len(rb_cur.executed) == 1
          and rb_cur.executed[0][0].startswith("UPDATE recipe_rewrite SET ")
          and rb_cur.executed[0][0].endswith(" WHERE id = ?")
          and rb_cur.executed[0][1][-1] == 4781)

    # 6. Rollback --apply --delete-inserted deletes only meta.inserted_ids
    rb_cur2 = FakeCursor()
    rb_conn2 = FakeConnection(rb_cur2)
    res_del = rb.run_rollback(jsonl_p, connection=rb_conn2, apply=True, delete_inserted=True)
    check("rollback --delete-inserted deletes only inserted_ids",
          res_del["deleted"] == 1
          and len(rb_cur2.executed) == 2
          and rb_cur2.executed[1][0] == "DELETE FROM recipe_rewrite WHERE id IN (?)"
          and rb_cur2.executed[1][1] == (9500,))

    # 7. Force report (DRY_RUN + used_before + diff)
    rep_md, rep_csv = ns["write_force_report"](
        tmp_path / "logs",
        "20261124_120000",
        [{
            "recipe_id": 2247,
            "rewrite_id": 4781,
            "used_before": 11,
            "dish_name_before": "Keto Pie",
            "dish_name_after": "Keto Pie",
            "serves_before": 8,
            "serves_after": 6,
            "ingredients_before": "almond flour\nbutter",
            "ingredients_after": "1 cup almond flour\n1/2 cup butter",
        }],
        {3801: "нет количеств"},
        dry_run=True,
    )
    md_text = rep_md.read_text(encoding="utf-8")
    csv_text = rep_csv.read_text(encoding="utf-8-sig")
    check("force report md marks DRY_RUN, servings change, diff, and ИСПОЛЬЗОВАН В КНИГЕ",
          "DRY_RUN" in md_text and "8` -> `6`" in md_text and "ИСПОЛЬЗОВАН В КНИГЕ" in md_text
          and "+1 cup almond flour" in md_text and "3801" in md_text)
    check("force report csv contains before/after ingredients and book flag",
          "1 cup almond flour" in csv_text and "ИСПОЛЬЗОВАН В КНИГЕ" in csv_text)

    # 8. fetch_used_before_map tolerates missing column
    bad_cur = FakeCursor(fail_sql=True)
    check("fetch_used_before_map returns {} on DB error",
          ns["fetch_used_before_map"](FakeConnection(bad_cur), [2247]) == {})

# 9. backups/ in .gitignore
ign = subprocess.run(["git", "check-ignore", "backups/test.jsonl"], cwd=HERE, capture_output=True, text=True)
check("backups/ is ignored by git", ign.returncode == 0)

print(f"\nИТОГО force_backup: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
