"""Тесты нормализации названий блюд tools/name_normalize.py (r18, без БД и API).
Запуск: python tests/test_name_normalize.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from tools.name_normalize import load_dish_name_config, normalize_dish_name  # noqa: E402

fails: list[str] = []


def check(name: str, got: object, want: object) -> None:
    ok = got == want
    print(("OK   " if ok else "FAIL ") + name + ("" if ok else f" (got={got!r}, want={want!r})"))
    if not ok:
        fails.append(name)


CASES: list[tuple[str, str, str]] = [
    ("1. Low Carb with space -> Low-Carb", "Low Carb Bagel", "Low-Carb Bagel"),
    ("2. ALL CAPS LOW CARB -> Low-Carb + Title Case", "LOW CARB CORNBREAD", "Low-Carb Cornbread"),
    ("3. lowercase (low-carb) in parens", "Beef Stew (low-carb)", "Beef Stew (Low-Carb)"),
    ("4. ALL CAPS with ampersand", "KETO MUSHROOM & THYME BRUSCHETTA", "Keto Mushroom & Thyme Bruschetta"),
    ("5. ALL CAPS with small words to/and", "HOW TO MAKE AND COOK CAULIFLOWER RICE", "How to Make and Cook Cauliflower Rice"),
    ("6. ALL CAPS with BBQ abbreviation", "BUNLESS BBQ GUACBURGERS", "Bunless BBQ Guacburgers"),
    ("7. ALL CAPS hyphenated NO-BAKE", "NO-BAKE KETO NEW YORK CHEESECAKE JARS", "No-Bake Keto New York Cheesecake Jars"),
    ("8. Quotes and braces with ALL CAPS", 'Low-Carb "YOGURT" {KETO-FRIENDLY}', 'Low-Carb "Yogurt" {Keto-Friendly}'),
    ("9. ALL CAPS BEST in mixed title -> Title Case", "The BEST Keto Burger Recipe", "The Best Keto Burger Recipe"),
    ("10. Possessive ALL CAPS CHICKEN'S -> Chicken's", "GRANDMA'S CHICKEN'S SOUP", "Grandma's Chicken's Soup"),
    ("11. NBSP and U+202F in Low Carb", "Low\u00a0Carb & Low\u202fCarb Bread", "Low-Carb & Low-Carb Bread"),
    ("12. Small word after colon/paren/dash capitalized", "Keto: A Guide (for Beginners) — The Basics", "Keto: A Guide (For Beginners) — The Basics"),
    ("13. Small words in middle lowercased", "Cream Of Mushroom Soup With Herbs In A Bowl", "Cream of Mushroom Soup with Herbs in a Bowl"),
    ("14. Abbreviations BLT, MCT, PB, NYC, USA, UK, DIY, TV", "NYC BLT WITH MCT & PB (USA/UK DIY TV)", "NYC BLT with MCT & PB (USA/UK DIY TV)"),
    ("15. Hyphenated lowercase parts capitalized", "Keto ice-cream no-bake Bars", "Keto Ice-Cream No-Bake Bars"),
    ("16. Mixed-case brand/word preserved", "Keto McDonald's Style iPhone Timer Cake", "Keto McDonald's Style iPhone Timer Cake"),
    ("17. French 'de' small word lowercased", "Crème De La Crème", "Crème de La Crème"),
    ("18. Empty and whitespace-only string", "   \u00a0  ", ""),
]

for label, raw, expected in CASES:
    got = normalize_dish_name(raw)
    check(label, got, expected)
    # Проверка идемпотентности для каждого кейса
    got2 = normalize_dish_name(got)
    check(f"idempotent: {label}", got2, got)

# Проверка загрузки словаря из locales/dish_name.yaml
abbrs, smalls = load_dish_name_config()
check("dish_name.yaml loads required abbreviations",
      {"BBQ", "BLT", "MCT", "PB", "DIY", "USA", "UK", "TV", "NYC"}.issubset(abbrs), True)
check("dish_name.yaml loads required small words",
      {"a", "an", "the", "and", "of", "with", "from", "de"}.issubset(smalls), True)

# Проверка подключения в rewr-transl.py при записи dish_name_variant (adapt/FORCE и translate)
src = (HERE / "rewr-transl.py").read_text(encoding="utf-8")
check("rewr-transl.py imports normalize_dish_name",
      "from tools.name_normalize import normalize_dish_name" in src, True)
check("save_adapted_recipe normalizes dish_name_variant on write",
      "save_adapted_recipe" in src and "_norm_dn(raw_dish_name)" in src, True)
check("save_rewritten_recipe (translate) normalizes dish_name_variant on write",
      "_norm_dn(data.dish_name)" in src, True)

print(f"\nИТОГО name_normalize: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
