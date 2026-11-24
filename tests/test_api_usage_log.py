"""Тесты учёта аккаунта/ключа в api_usage.csv и отчёта api_usage_report.py (без API, БД и .env).
Запуск: python tests/test_api_usage_log.py [путь_к_скрипту]
"""
import csv
import hashlib
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent.parent
SCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "rewr-transl.py"

src = SCRIPT.read_text(encoding="utf-8")

_test_logger = logging.getLogger("test_api_usage_log")
_test_logger.addHandler(logging.StreamHandler(sys.stdout))

ns: dict[str, Any] = {
    "csv": csv,
    "hashlib": hashlib,
    "logging": logging,
    "Path": Path,
    "Sequence": Sequence,
    "datetime": datetime,
    "BASE_DIR": HERE,
    "GEMINI_ACCOUNT_LABEL": "main",
    "APP_MODE": "adapt",
    "DRY_RUN": False,
    "LOGGER": _test_logger,
    "MODEL_PRICING_USD_PER_MILLION": {"gemini-3.8-flash": (0.75, 3.75)},
}
exec(src[src.index("def api_key_fingerprint"): src.index("SESSION_TRACKER =")], ns)

sys.path.insert(0, str(HERE / "tools"))
import api_usage_report as rep  # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("OK   " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


fake_key = "AIzaSySecretKeyForUnitTestOnly12345"
expected_fp = hashlib.sha256(fake_key.encode("utf-8")).hexdigest()[:8]
check("api_key_fingerprint returns 8-char sha256 prefix without key",
      ns["api_key_fingerprint"](fake_key) == expected_fp and fake_key not in expected_fp)

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    usage_csv = tmp_path / "api_usage.csv"

    # 1. Старый файл с 13 колонками -> переименовывается в api_usage_legacy_<ts>.csv, новый создаётся с 20 колонками
    old_headers = ["time", "model", "prompt", "cached", "candidates", "thoughts", "tool_use",
                   "total", "billed_output", "cost_usd", "price_in", "price_out", "price_known"]
    with usage_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(old_headers)
        w.writerow(["2026-11-20T10:00:00", "gemini-3.8-flash", 1000, 0, 200, 50, 0, 1250, 250, "0.001688", 0.75, 3.75, 1])

    tracker = ns["SessionCostTracker"]()
    check("SessionCostTracker default account_label is 'main'", tracker.account_label == "main")
    tracker.set_run_context("", ns["api_key_fingerprint"](fake_key), "abcd1234", "adapt", False, "20261124_164000")
    check("set_run_context empty label falls back to 'main'", tracker.account_label == "main")
    tracker.USAGE_LOG = usage_csv
    tracker.set_run_context("backup", ns["api_key_fingerprint"](fake_key), "abcd1234", "adapt", False, "20261124_164000")

    fake_usage = SimpleNamespace(
        prompt_token_count=2000,
        cached_content_token_count=0,
        candidates_token_count=400,
        thoughts_token_count=100,
        tool_use_prompt_token_count=0,
        total_token_count=2500,
    )
    tracker.add_usage(fake_usage, "gemini-3.8-flash", recipe_ids=[1239, 1240])

    legacy_files = list(tmp_path.glob("api_usage_legacy_*.csv"))
    check("old header rotates to api_usage_legacy_*.csv without data loss",
          len(legacy_files) == 1 and "2026-11-20T10:00:00" in legacy_files[0].read_text(encoding="utf-8-sig"))

    raw_new = usage_csv.read_text(encoding="utf-8-sig")
    check("secret API key never appears in api_usage.csv", fake_key not in raw_new)

    rows = list(csv.DictReader(usage_csv.open("r", encoding="utf-8-sig"), delimiter=";"))
    check("new row has all 20 columns and exact context",
          len(rows) == 1
          and list(rows[0].keys())[:13] == old_headers
          and rows[0]["account_label"] == "backup"
          and rows[0]["key_fp"] == expected_fp
          and rows[0]["run_hash"] == "abcd1234"
          and rows[0]["app_mode"] == "adapt"
          and rows[0]["recipe_ids"] == "1239,1240"
          and rows[0]["dry_run"] == "0"
          and rows[0]["run_ts"] == "20261124_164000")

    # 2. Отчёт api_usage_report.py: группирует по account (legacy + backup), day, run_hash, и --since
    all_rows = rep.load_usage_rows(usage_csv)
    by_acct = rep.aggregate_usage(all_rows, by="account")
    check("report groups by account_label including legacy",
          set(by_acct.keys()) == {"legacy", "backup"}
          and by_acct["legacy"]["requests"] == 1
          and by_acct["backup"]["requests"] == 1)

    since_rows = rep.load_usage_rows(usage_csv, since="2026-11-24")
    by_day = rep.aggregate_usage(since_rows, by="day")
    check("report --since filters out older legacy rows",
          len(since_rows) == 1 and len(by_day) == 1)

    # 3. Строка нового формата с пустой account_label остаётся "legacy", не "main"
    with usage_csv.open("a", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh, delimiter=";").writerow([
            "2026-11-24T18:00:00", "gemini-3.8-flash", 100, 0, 50, 0, 0, 150, 50, "0.000263", 0.75, 3.75, 1,
            "", expected_fp, "abcd1234", "adapt", "1240", 0, "20261124_180000",
        ])
    by_acct2 = rep.aggregate_usage(rep.load_usage_rows(usage_csv), by="account")
    check("empty account_label maps to 'legacy', not 'main'",
          "main" not in by_acct2 and by_acct2["legacy"]["requests"] == 2)

print(f"\nИТОГО api_usage_log: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
