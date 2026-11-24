"""Тесты детектора ссылок на другие рецепты has_recipe_ref и SKIP_RECIPE_REFS (без БД, API и .env).
Запуск: python tests/test_recipe_refs.py [путь_к_скрипту]
"""
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent.parent
SCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "rewr-transl.py"

src = SCRIPT.read_text(encoding="utf-8")
ns: dict[str, Any] = {"os": os, "re": re, "Sequence": Sequence}
exec(src[src.index("SKIP_NO_QUANTITIES ="): src.index("def build_sent_ingredients")], ns)
exec(src[src.index("def format_adapt_exclude_line"): src.index("def main()")], ns)

has_recipe_ref = ns["has_recipe_ref"]
format_adapt_exclude_line = ns["format_adapt_exclude_line"]
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("OK   " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# 1. Положительные случаи (ссылка найдена)
check("4423: 1 recipe Low-Carb Apple butter",
      has_recipe_ref("1 recipe Low-Carb Apple butter\n1 cup almond flour") == ["1 recipe Low-Carb Apple butter"])
check("1 batch of Basic Keto Dough recipe",
      has_recipe_ref("1 batch of Basic Keto Dough recipe") == ["1 batch of Basic Keto Dough recipe"])
check("see recipe notes",
      has_recipe_ref("1 cup broth\nsee recipe notes") == ["see recipe notes"])
check("(see ... recipe)",
      has_recipe_ref("1 cup cream (see keto caramel recipe)") == ["1 cup cream (see keto caramel recipe)"])
check("recipe for ...",
      has_recipe_ref("recipe for keto gravy") == ["recipe for keto gravy"])
check("2 servings of ... recipe",
      has_recipe_ref("2 servings of keto frosting recipe") == ["2 servings of keto frosting recipe"])
check("1 cup of the sauce from the recipe above",
      has_recipe_ref("1 cup of the sauce from the recipe above") == ["1 cup of the sauce from the recipe above"])

# 2. Отрицательные случаи (пусто)
check("1 cup almond flour -> empty", has_recipe_ref("1 cup almond flour") == [])
check("normal ingredients -> empty",
      has_recipe_ref("2 large eggs\n1/2 cup butter\n1/4 tsp salt\n1 tbsp olive oil") == [])

# 3. format_adapt_exclude_line с ref_ids и обратная совместимость
check("format_adapt_exclude_line backward compatible",
      format_adapt_exclude_line([1], [2], [], []) == "ADAPT_EXCLUDE_IDS=1,2")
check("format_adapt_exclude_line includes ref_ids",
      format_adapt_exclude_line([], [], [], [], ref_ids=[4423, 10]) == "ADAPT_EXCLUDE_IDS=10,4423")

# 4. SKIP_RECIPE_REFS=0 отключает пропуск
old_env = os.environ.get("SKIP_RECIPE_REFS")
try:
    os.environ["SKIP_RECIPE_REFS"] = "0"
    ns_off: dict[str, Any] = {"os": os, "re": re, "Sequence": Sequence}
    exec(src[src.index("SKIP_NO_QUANTITIES ="): src.index("def build_sent_ingredients")], ns_off)
    check("SKIP_RECIPE_REFS=0 disables skip flag", ns_off["SKIP_RECIPE_REFS"] is False)
finally:
    if old_env is None:
        os.environ.pop("SKIP_RECIPE_REFS", None)
    else:
        os.environ["SKIP_RECIPE_REFS"] = old_env

print(f"\nИТОГО recipe_refs: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
