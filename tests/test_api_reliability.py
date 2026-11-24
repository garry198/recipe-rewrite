"""Тесты повторов API, разбиения батча при провале и лимита расхода MAX_RUN_COST_USD (без сети, БД и .env).
Запуск: python tests/test_api_reliability.py [путь_к_скрипту]
"""
import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent.parent
SCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "rewr-transl.py"

src = SCRIPT.read_text(encoding="utf-8")

_test_logger = logging.getLogger("test_api_reliability")
_test_logger.addHandler(logging.StreamHandler(sys.stdout))

ns: dict[str, Any] = {
    "Any": Any,
    "Sequence": Sequence,
    "time": time,
    "LOGGER": _test_logger,
}
exec(src[src.index("def check_cost_limit"): src.index("def main()")], ns)

call_api_with_retries = ns["call_api_with_retries"]
check_cost_limit = ns["check_cost_limit"]

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("OK   " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def fake_build_prompt(rows: Sequence[dict[str, Any]]) -> str:
    return ",".join(str(r["recipe_id"]) for r in rows)


rows_2 = [{"rewrite_row_id": None, "recipe_id": 101}, {"rewrite_row_id": None, "recipe_id": 102}]

# 1. Повтор при исключении и успех со 2-й попытки
tracker1 = SimpleNamespace(failed_calls=0)
sleeps1: list[float] = []
attempts1 = {"n": 0}


def flaky_once(prompt_text: str) -> str:
    attempts1["n"] += 1
    if attempts1["n"] == 1:
        raise TimeoutError("429 rate limit")
    return f"OK:{prompt_text}"


res1 = call_api_with_retries(
    flaky_once, rows_2, fake_build_prompt,
    retries=2, backoff=[5.0, 20.0], split_on_fail=True,
    tracker=tracker1, sleep_fn=sleeps1.append, logger=_test_logger,
)
check("retry succeeds on 2nd attempt with backoff and counts failed_calls",
      res1 == [([101, 102], "OK:101,102")] and tracker1.failed_calls == 1 and sleeps1 == [5.0])

# 2. Исчерпание попыток на батче и разбиение на одиночные запросы (101 ок, 102 падает)
tracker2 = SimpleNamespace(failed_calls=0)
sleeps2: list[float] = []


def fail_batch_and_102(prompt_text: str) -> str:
    if "," in prompt_text or prompt_text == "102":
        raise RuntimeError("503 unavailable")
    return f"OK:{prompt_text}"


res2 = call_api_with_retries(
    fail_batch_and_102, rows_2, fake_build_prompt,
    retries=2, backoff=[5.0, 20.0], split_on_fail=True,
    tracker=tracker2, sleep_fn=sleeps2.append, logger=_test_logger,
)
check("exhausted batch splits into singles (101 ok, 102 None)",
      res2 == [([101], "OK:101"), ([102], None)]
      and tracker2.failed_calls == 6  # 3 (batch) + 3 (single 102)
      and sleeps2 == [5.0, 20.0, 5.0, 20.0])

# 3. Выключенные настройки (API_RETRIES=0, API_SPLIT_ON_FAIL=0) -> поведение как раньше
tracker3 = SimpleNamespace(failed_calls=0)
sleeps3: list[float] = []
raised3 = False
try:
    call_api_with_retries(
        fail_batch_and_102, rows_2, fake_build_prompt,
        retries=0, backoff=[5.0, 20.0], split_on_fail=False,
        tracker=tracker3, sleep_fn=sleeps3.append, logger=_test_logger,
    )
except RuntimeError:
    raised3 = True
check("disabled retries & split raises immediately after 1 attempt",
      raised3 and tracker3.failed_calls == 1 and sleeps3 == [])

# 4. Лимит расхода MAX_RUN_COST_USD
check("MAX_RUN_COST_USD=0 disables cost limit",
      check_cost_limit(99.0, 0.0) is False)
check("MAX_RUN_COST_USD stops when total_cost_usd >= limit",
      check_cost_limit(0.049, 0.05) is False and check_cost_limit(0.05, 0.05) is True and check_cost_limit(0.08, 0.05) is True)

print(f"\nИТОГО api_reliability: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
