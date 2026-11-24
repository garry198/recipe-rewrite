"""Тесты выбора целевого числа порций choose_target_servings без БД, API и .env.
Запуск: python tests/test_scale_choice.py [путь_к_скрипту]
"""
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent.parent
SCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "rewr-transl.py"

src = SCRIPT.read_text(encoding="utf-8")
ns: dict[str, Any] = {
    "os": os,
    "re": re,
    "Sequence": Sequence,
    "RESCALE_TARGET_SERVINGS": 4,
    "RESCALE_ONLY_IF_MORE_THAN": 8,
    "RESCALE_MAX_DIVISOR": 2.5,
    "RESCALE_SMART": True,
    "RESCALE_CANDIDATES": [4, 6, 8, 12, 16, 24, 32],
    "RESCALE_COUNT_RE": re.compile(
        r"\b(?:large|medium|small|extra[- ]large|whole)?\s*(?:eggs?|egg\s+yolks?|egg\s+whites?)\b",
        re.IGNORECASE,
    ),
    "_UNI_FRAC": {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3", "⅛": "1/8"},
}
exec(src[src.index("def _to_float"): src.index("def _fr")], ns)
exec(src[src.index("def target_servings_for"): src.index("def build_sent_ingredients")], ns)

choose = ns["choose_target_servings"]
base_fn = ns["target_servings_for"]
fails: list[str] = []


def check(name: str, got: int, want: int) -> None:
    ok = got == want
    print(("OK   " if ok else "FAIL ") + name + ("" if ok else f" (got={got}, want={want})"))
    if not ok:
        fails.append(name)


# 1. 12 порций, 4 белка + 2 желтка -> 6
check("12 srv, 4 egg whites + 2 egg yolks -> 6",
      choose(12, "4 large egg whites\n2 large egg yolks, divided\n1 cup almond flour"), 6)

# 2. 12 порций, 4 яйца -> 6
check("12 srv, 4 large eggs -> 6",
      choose(12, "4 large eggs\n1/2 cup butter"), 6)

# 3. 12 порций, 1 egg + 3 yolks + 2 whites (4801) -> оба кандидата дробные -> base (8)
check("12 srv, 4801 (1 egg, 3 yolks, 2 whites) -> base 8",
      choose(12, "1 large egg\n3 large egg yolks\n2 large egg whites"), 8)

# 4. 16 порций без яиц -> 8
check("16 srv, no eggs -> 8",
      choose(16, "2 cups almond flour\n1/2 cup butter"), 8)

# 5. 6 и 8 порций -> без изменений
check("6 srv with eggs -> 6", choose(6, "3 large eggs"), 6)
check("8 srv with eggs -> 8", choose(8, "3 large eggs"), 8)

# 6. 50 порций, 2 cups sugar -> 32 (как base)
check("50 srv, 2 cups sugar -> 32", choose(50, "2 cups sugar\n1 cup butter"), 32)

# 7. RESCALE_SMART=0 -> всегда как target_servings_for
ns["RESCALE_SMART"] = False
check("RESCALE_SMART=0 -> base 8 for 12 srv with 4 eggs",
      choose(12, "4 large eggs"), base_fn(12))
ns["RESCALE_SMART"] = True

# 8. Исключения: beaten egg в ложках и диапазоны не считаются дробными
check("12 srv, 2 tbsp beaten egg -> base 8",
      choose(12, "2 tbsp beaten egg\n1 cup flour"), 8)
check("12 srv, 2-3 eggs range -> base 8",
      choose(12, "2-3 large eggs\n1 cup flour"), 8)

print(f"\nИТОГО scale_choice: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
