"""Тесты очистки названий брендов на нейтральные в recipe_rewrite (r17, без БД и API).
Запуск: python tests/test_brand_cleanup.py
"""
from __future__ import annotations

import csv
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tools"))
import brand_cleanup as bc  # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("OK   " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def first_qty_token(line: str) -> str:
    s = line.lstrip("* \t")
    m = re.match(r"^(?:Optional:\s*)?(\d+(?:\s+\d+/\d+|/\d+|-\d+|\.\d+)?|[½¼¾⅓⅔⅛])", s, re.IGNORECASE)
    return m.group(1) if m else ""


dictionary = bc.load_brand_dictionary()

# === 1. Все правила словаря на реальных примерах из БД / задания ===
cases: list[tuple[str, str, str]] = [
    # Besti
    (
        "Besti Powdered (4420)",
        "1/4 cup Besti Powdered monk fruit allulose blend",
        "1/4 cup powdered monk fruit allulose blend",
    ),
    (
        "Besti Brown (4422)",
        "1 tbsp Besti Brown monk fruit allulose blend",
        "1 tbsp brown monk fruit allulose blend",
    ),
    (
        "Besti plain blend (4457)",
        "1 tbsp Besti monk fruit allulose blend",
        "1 tbsp monk fruit allulose blend",
    ),
    (
        "Besti cook powdered (4643)",
        "Combine diced strawberries, water, and powdered Besti sweetener in a small saucepan.",
        "Combine diced strawberries, water, and powdered monk fruit allulose blend in a small saucepan.",
    ),
    (
        "Besti cook brown (4820)",
        "5. Add Besti brown sweetener, cinnamon, and salt.",
        "5. Add brown monk fruit allulose blend, cinnamon, and salt.",
    ),
    (
        "Besti cook standalone (4705)",
        "2. In a large bowl, beat together the Besti and butter, until fluffy.",
        "2. In a large bowl, beat together the monk fruit allulose blend and butter, until fluffy.",
    ),
    # Wholesome Yum
    (
        "Wholesome Yum almond flour (4422)",
        "1 1/2 cups Wholesome Yum blanched almond flour",
        "1 1/2 cups blanched almond flour",
    ),
    (
        "Wholesome Yum coconut flour (4457)",
        "3 tbsp Wholesome Yum Coconut Flour",
        "3 tbsp coconut flour",
    ),
    (
        "Wholesome Yum maple syrup (4427)",
        "1/2 cup Wholesome Yum Zero Sugar Maple Syrup",
        "1/2 cup sugar-free maple syrup",
    ),
    (
        "Wholesome Yum honey (4860)",
        "1/3 cup Wholesome Yum Zero Sugar Honey Substitute",
        "1/3 cup sugar-free honey substitute",
    ),
    (
        "Wholesome Yum fallback prefix",
        "1 tsp Wholesome Yum vanilla extract",
        "1 tsp vanilla extract",
    ),
    # Sukrin
    (
        "Sukrin Melis (4967)",
        "1/2 cup Sukrin Melis (120 ml)",
        "1/2 cup powdered erythritol (120 ml)",
    ),
    (
        "Sukrin Gold brown sugar substitute (5322)",
        "1/3 cup Sukrin Gold brown sugar substitute, packed",
        "1/3 cup brown sugar substitute, packed",
    ),
    (
        "Sukrin Gold standalone (8463)",
        "1/4 cup Sukrin Gold",
        "1/4 cup brown sugar substitute",
    ),
    (
        "Sukrin Gold sweetener (8202)",
        "3 tbsp Sukrin Gold sweetener",
        "3 tbsp brown sugar substitute",
    ),
    (
        "Sukrin Gold Fiber Syrup (5321)",
        "2 1/4 tsp Sukrin Gold Fiber Syrup",
        "2 1/4 tsp fiber syrup",
    ),
    (
        "Sukrin:1 (4967)",
        "2/3 cup Sukrin:1 (160 ml)",
        "2/3 cup low carb sugar substitute (160 ml)",
    ),
    (
        "Sukrin:1 paren or (5129)",
        "2 tbsp low carb sugar substitute (or Sukrin:1)",
        "2 tbsp low carb sugar substitute",
    ),
    (
        "Sukrin standalone cook (4967)",
        "5. Combine the egg yolks, whole eggs, cream and the Sukrin in a large bowl.",
        "5. Combine the egg yolks, whole eggs, cream and the low carb sugar substitute in a large bowl.",
    ),
    (
        "Sukrin Gold or Swerve such as (8514)",
        "* 2/3 cup brown sugar substitute such as Sukrin Gold or Swerve",
        "* 2/3 cup brown sugar substitute",
    ),
    (
        "Swerve or Sukrin Gold such as (9146)",
        "1/3 cup brown sugar substitute such as Swerve or Sukrin Gold",
        "1/3 cup brown sugar substitute",
    ),
    # Swerve
    (
        "Swerve erythritol or Swerve (8350)",
        "* 1/2 cup granulated erythritol or Swerve (100 g/ 3.5 oz)",
        "* 1/2 cup granulated erythritol (100 g/ 3.5 oz)",
    ),
    (
        "Swerve or erythritol (9132)",
        "1 tbsp powdered Swerve or erythritol (10 g/ 0.4 oz)",
        "1 tbsp powdered erythritol (10 g/ 0.4 oz)",
    ),
    (
        "Swerve paren (5297)",
        "1/4 cup powdered erythritol (or Swerve)",
        "1/4 cup powdered erythritol",
    ),
    (
        "Swerve comma or Allulose (8086)",
        "* 1 cup powdered erythritol, Swerve or Allulose (160 g/ 5.6 oz)",
        "* 1 cup powdered erythritol or Allulose (160 g/ 5.6 oz)",
    ),
    (
        "Swerve confectioners + Sukrin Melis (5458)",
        "1/4 cup Swerve confectioners powdered sweetener (or Sukrin Melis)",
        "1/4 cup powdered erythritol sweetener",
    ),
    (
        "confectioners Swerve (8202)",
        "2/3 cup confectioners Swerve, sifted",
        "2/3 cup powdered erythritol sweetener, sifted",
    ),
    (
        "Swerve brown sugar substitute (4964)",
        "1/4 cup Swerve brown sugar substitute",
        "1/4 cup brown sugar substitute",
    ),
    (
        "powdered Swerve (8790)",
        "3/4 cup powdered Swerve (108 g/ 3.8 oz)",
        "3/4 cup powdered erythritol sweetener (108 g/ 3.8 oz)",
    ),
    (
        "Swerve standalone",
        "2 tbsp Swerve",
        "2 tbsp erythritol sweetener",
    ),
    (
        "Swerve + amzn links (8602)",
        "1-2 tbsp granulated erythritol [https://amzn.to/1MeWd9t] or Swerve [https://amzn.to/2Jt1fUt], divided (10-20 g/ 0.3-0.7 oz)",
        "1-2 tbsp granulated erythritol, divided (10-20 g/ 0.3-0.7 oz)",
    ),
    # Lakanto, Truvia, Monin, Vegemite
    (
        "Lakanto chocolate bar (5460)",
        "3 oz low carb dark chocolate (or 3 oz Lakanto chocolate bar)",
        "3 oz low carb dark chocolate (or 3 oz sugar-free dark chocolate bar)",
    ),
    (
        "Lakanto monk fruit (5593)",
        "⅓ cup Lakanto powdered monk fruit sweetener",
        "⅓ cup powdered monk fruit sweetener",
    ),
    (
        "Truvia (5343)",
        "1 1/4 tsp Truvia",
        "1 1/4 tsp stevia-erythritol sweetener",
    ),
    (
        "Monin",
        "2 tbsp Monin",
        "2 tbsp sugar-free syrup",
    ),
    (
        "Vegemite + Marmite (1131)",
        "1 tsp yeast extract spread (such as Marmite or Vegemite)",
        "1 tsp yeast extract spread",
    ),
    (
        "Vegemite only (1126)",
        "2 teaspoons yeast extract spread (such as Vegemite)",
        "2 teaspoons yeast extract spread",
    ),
]

for name, before, expected in cases:
    after, rules, brands, warn = bc.clean_line(before, dictionary)
    check(f"rule: {name}", after == expected and warn is None, f"got={after!r}, expected={expected!r}, warn={warn!r}")
    # Проверка сохранности первого токена-количества
    q_before = first_qty_token(before)
    q_after = first_qty_token(after)
    check(f"qty preserved: {name}", q_before == q_after, f"{q_before!r} != {q_after!r}")
    # Проверка идемпотентности
    after2, rules2, _, warn2 = bc.clean_line(after, dictionary)
    check(f"idempotent: {name}", after2 == after and not rules2 and warn2 is None, f"second={after2!r}")

# === 2. Ложные срабатывания не меняются ===
for fp_line in (
    "1 tsp cayenne pepper or ground red hot pepper",
    "1/2 red hot chili pepper, minced",
    "1 cup almond flour [https://amzn.to/noBrandHere]",
):
    got, rules, _, warn = bc.clean_line(fp_line, dictionary)
    check(f"no false positive: {fp_line[:40]}", got == fp_line and not rules and warn is None, f"got={got!r}")

# === 3. Защита от пустой строки и потери числа ===
empty_after, _, _, empty_warn = bc.clean_line("(such as Vegemite)", dictionary)
check("empty result triggers WARNING and keeps original",
      empty_after == "(such as Vegemite)" and empty_warn is not None and empty_warn.startswith("WARNING:"),
      f"after={empty_after!r}, warn={empty_warn!r}")

# === 4. Dry-run и --apply на fake connection ===
class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]], events: list[str]):
        self._rows = rows
        self.events = events
        self.executed: list[tuple[str, Any]] = []
        self.closed = False

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))
        op = sql.strip().split()[0].upper()
        self.events.append(f"SQL:{op}")

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]):
        self.events: list[str] = []
        self.rows = rows
        self.cursors: list[FakeCursor] = []
        self.committed = False
        self.rolled_back = False

    def cursor(self, dictionary: bool = False) -> FakeCursor:
        cur = FakeCursor(self.rows, self.events)
        self.cursors.append(cur)
        return cur

    def commit(self) -> None:
        self.committed = True
        self.events.append("COMMIT")

    def rollback(self) -> None:
        self.rolled_back = True
        self.events.append("ROLLBACK")


sample_rows = [
    {
        "id": 4420,
        "ingredients_list_variant": "1/2 cup Besti Powdered monk fruit allulose blend\n1/4 tsp sea salt",
        "cooking_instructions_variant": "Stir 1/2 cup Besti Powdered monk fruit allulose blend into cream.",
        "needs_reprice": 0,
    },
    {
        "id": 9999,
        "ingredients_list_variant": "1 tsp cayenne pepper or ground red hot pepper",
        "cooking_instructions_variant": "Mix well.",
        "needs_reprice": 0,
    },
]

with tempfile.TemporaryDirectory() as tmp:
    tmp_logs = Path(tmp) / "logs"

    # 4a. Dry-run не выполняет UPDATE и commit, пишет CSV-отчёт
    conn_dry = FakeConnection(sample_rows)
    res_dry = bc.run_brand_cleanup(
        connection=conn_dry,
        dictionary=dictionary,
        apply=False,
        logs_dir=tmp_logs,
        timestamp="20261126_120000",
    )
    all_sql_dry = [sql for c in conn_dry.cursors for sql, _ in c.executed]
    check("dry-run executes no UPDATE and no commit",
          not any("UPDATE" in s.upper() for s in all_sql_dry)
          and not conn_dry.committed
          and res_dry["applied"] is False
          and res_dry["affected_ids"] == [4420],
          f"sql={all_sql_dry}, res={res_dry}")
    with res_dry["report_path"].open("r", encoding="utf-8", newline="") as fh:
        report_rows = list(csv.reader(fh, delimiter=";"))
    check("dry-run CSV report has expected header and 2 changes",
          report_rows[0] == ["id", "поле", "было", "стало", "правило"] and len(report_rows) == 3,
          f"rows={report_rows}")

    # 4b. Apply пишет бэкап и файл id ДО commit, обновляет только текстовые поля и needs_reprice=1
    conn_apply = FakeConnection(sample_rows)
    orig_write_backup = bc.write_backup_csv
    orig_write_ids = bc.write_ids_file

    def traced_backup(path: Path, rows: Any) -> None:
        conn_apply.events.append("WRITE_BACKUP")
        orig_write_backup(path, rows)

    def traced_ids(path: Path, ids: Any) -> None:
        conn_apply.events.append("WRITE_IDS")
        orig_write_ids(path, ids)

    bc.write_backup_csv = traced_backup  # type: ignore[assignment]
    bc.write_ids_file = traced_ids  # type: ignore[assignment]
    try:
        res_apply = bc.run_brand_cleanup(
            connection=conn_apply,
            dictionary=dictionary,
            apply=True,
            logs_dir=tmp_logs,
            timestamp="20261126_120001",
        )
    finally:
        bc.write_backup_csv = orig_write_backup  # type: ignore[assignment]
        bc.write_ids_file = orig_write_ids  # type: ignore[assignment]

    ev = conn_apply.events
    check("apply writes backup and ids file BEFORE commit",
          conn_apply.committed
          and not conn_apply.rolled_back
          and "WRITE_BACKUP" in ev
          and "WRITE_IDS" in ev
          and ev.index("WRITE_BACKUP") < ev.index("COMMIT")
          and ev.index("WRITE_IDS") < ev.index("COMMIT"),
          f"events={ev}")

    update_calls = [
        (sql, params)
        for c in conn_apply.cursors
        for sql, params in c.executed
        if sql.strip().upper().startswith("UPDATE")
    ]
    check("apply runs single UPDATE for affected id only with needs_reprice=1",
          len(update_calls) == 1
          and "needs_reprice = 1" in update_calls[0][0]
          and "calories_per_person" not in update_calls[0][0]
          and "status" not in update_calls[0][0]
          and "serves" not in update_calls[0][0]
          and update_calls[0][1][2] == 4420,
          f"updates={update_calls}")

    ids_bytes = res_apply["ids_out_path"].read_bytes()
    check("ids file is UTF-8 without BOM and LF line endings",
          ids_bytes == b"4420\n",
          f"bytes={ids_bytes!r}")

    with res_apply["backup_path"].open("r", encoding="utf-8", newline="") as fh:
        bk_rows = list(csv.reader(fh, delimiter=";"))
    check("backup csv contains only affected row and 4 columns",
          bk_rows[0] == ["id", "ingredients_list_variant", "cooking_instructions_variant", "needs_reprice"]
          and len(bk_rows) == 2
          and bk_rows[1][0] == "4420",
          f"bk={bk_rows}")

    # 4c. При ошибке записи файла id commit не вызывается, вызывается rollback
    conn_fail = FakeConnection(sample_rows)

    def failing_write_ids(path: Path, ids: Any) -> None:
        raise OSError("disk full")

    bc.write_ids_file = failing_write_ids  # type: ignore[assignment]
    raised = False
    try:
        bc.run_brand_cleanup(
            connection=conn_fail,
            dictionary=dictionary,
            apply=True,
            logs_dir=tmp_logs,
            timestamp="20261126_120002",
        )
    except OSError:
        raised = True
    finally:
        bc.write_ids_file = orig_write_ids  # type: ignore[assignment]

    check("write_ids failure rolls back and does not commit",
          raised and conn_fail.rolled_back and not conn_fail.committed,
          f"raised={raised}, rolled_back={conn_fail.rolled_back}, committed={conn_fail.committed}")

    # 4d. parse_ids_arg и build_select_query (--ids, --ids-file, --limit)
    ids_f = Path(tmp) / "ids.txt"
    ids_f.write_text("# comment\n4420\n5343, 4420\n", encoding="utf-8")
    parsed = bc.parse_ids_arg("1126, 4420", ids_f)
    check("parse_ids_arg merges and deduplicates preserving order",
          parsed == [1126, 4420, 5343], f"parsed={parsed}")
    q_sql, q_params = bc.build_select_query(dictionary, ids=[1126, 4420], limit=5)
    check("build_select_query with ids and limit",
          "WHERE id IN (?, ?)" in q_sql and "LIMIT ?" in q_sql and q_params == (1126, 4420, 5),
          f"sql={q_sql}, params={q_params}")

print(f"\nИТОГО brand_cleanup: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
