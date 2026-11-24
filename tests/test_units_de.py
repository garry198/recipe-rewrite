"""Тесты конвертации единиц и температур для немецкой локали de_DE.
Запуск: python tests/test_units_de.py [путь_к_скрипту]
Не требует БД, Gemini и .env.
"""
import math
import re
import sys
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

HERE = Path(__file__).resolve().parent.parent
SCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "rewr-transl.py"

src = SCRIPT.read_text(encoding="utf-8")
ns = {
    "re": re, "math": math, "Path": Path, "BASE_DIR": HERE, "TARGET_LOCALE": "de_DE",
    "yaml": yaml, "BaseModel": BaseModel, "ConfigDict": ConfigDict, "Field": Field, "ValidationError": ValidationError,
}
exec(src[src.index("# ================= UNITS_FR"): src.index("# ================= СХЕМЫ ДАННЫХ")], ns)


def tr(text: str) -> str:
    mapping = {}
    prot = ns["protect_units"](text, mapping)
    return ns["restore_units"](prot, mapping)


# Ручные пары (вход -> ожидание) по правилам de_DE
CASES = [
    ("1 tsp vanilla extract", "1 TL vanilla extract"),
    ("1/2 tsp sea salt", "1/2 TL sea salt"),
    ("1-2 tsp cinnamon", "1–2 TL cinnamon"),
    ("2 tbsp olive oil", "2 EL olive oil"),
    ("1.5 tbsp butter", "1,5 EL butter"),
    ("1/4 to 1/2 tbsp lemon juice", "1/4–1/2 EL lemon juice"),
    ("1 cup almond flour", "95 g almond flour"),
    ("1/2 cup coconut flour", "55 g coconut flour"),
    ("1 1/2 cups butter", "340 g butter"),
    ("2 cups cream cheese", "460 g cream cheese"),
    ("1 cup shredded cheddar", "110 g shredded cheddar"),
    ("1 cup water", "240 ml water"),
    ("1/2 cup chicken broth", "120 ml chicken broth"),
    ("5 cups beef broth", "1,2 l beef broth"),
    ("12 fl oz heavy cream", "355 ml heavy cream"),
    ("2 fl oz espresso", "60 ml espresso"),
    ("1 quart chicken broth", "945 ml chicken broth"),
    ("2 quarts water", "2 l water"),
    ("5- to 6-quart pot", "4,5–5,5 l pot"),
    ("8 oz cream cheese", "225 g cream cheese"),
    ("0.5 oz butter", "14 g butter"),
    ("4 oz ramekins", "120 ml ramekins"),
    ("8 oz glass", "235 ml glass"),
    ("1 lb bacon", "450 g bacon"),
    ("2.5 lb pork shoulder", "1,15 kg pork shoulder"),
    ("9x13 inch baking pan", "23 x 33 cm baking pan"),
    ("1/2 inch thick", "1 cm thick"),
    ("1/4 inch slices", "6 mm slices"),
    ("1-2 inches apart", "2,5–5 cm apart"),
    ("Bake at 350°F.", "Bake at 180 °C Ober-/Unterhitze (160 °C Umluft)."),
    ("Preheat oven to 400°F (205°C).", "Preheat oven to 200 °C Ober-/Unterhitze (180 °C Umluft)."),
    ("Bake at 350-375°F.", "Bake at 180–190 °C Ober-/Unterhitze (160–170 °C Umluft)."),
    ("Roast at 325 to 350°F.", "Roast at 160–180 °C Ober-/Unterhitze (140–160 °C Umluft)."),
    ("Warm to 190°F.", "Warm to 88 °C Ober-/Unterhitze (68 °C Umluft)."),
    ("½ cup blueberries", "75 g blueberries"),
]

assert len(CASES) >= 30
fails = []
for src_text, want in CASES:
    got = tr(src_text)
    if got == want:
        print(f"OK   {src_text} -> {got}")
    else:
        print(f"FAIL {src_text}\n     want: {want!r}\n     got:  {got!r}")
        fails.append(src_text)

print(f"\nИТОГО de_DE: {len(CASES) - len(fails)}/{len(CASES)} пройдено")
sys.exit(1 if fails else 0)
