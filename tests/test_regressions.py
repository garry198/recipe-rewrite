"""Регрессионные тесты на реальных случаях из логов. Запуск:  python tests/test_regressions.py [путь_к_скрипту]
Не требует БД, Gemini и .env: функции вырезаются из скрипта по маркерам."""
import os, re, sys
from fractions import Fraction

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корень репозитория
SCRIPT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "rewr-transl.py")
src = open(SCRIPT, encoding="utf-8").read()
import math
import hashlib
from collections import Counter
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
ns = {
    "re": re, "Fraction": Fraction, "os": os, "Counter": Counter, "math": math,
    "hashlib": hashlib, "Path": Path, "BASE_DIR": Path(HERE), "yaml": yaml,
    "BaseModel": BaseModel, "ConfigDict": ConfigDict, "Field": Field, "ValidationError": ValidationError,
    "APP_MODE": "translate", "TARGET_LOCALE": "fr_FR", "THINKING_LEVEL": "low",
    "UNITS_VERSION": "u2", "SCALE_VERSION": "s20", "CHECKS_VERSION": "c9",
}
exec(src[src.index("_METRIC_LEAD_RE"): src.index("# ================= UNITS_FR")], ns)           # math + merge
exec(src[src.index("_BRAND_RE ="): src.index("def build_sent_ingredients")], ns)                   # multi-variant, no-qty
exec("RESCALE_TARGET_SERVINGS=4\nRESCALE_ONLY_IF_MORE_THAN=8\nRESCALE_MAX_DIVISOR=2.5\n"
     + src[src.index("def target_servings_for"): src.index("def build_sent_ingredients")], ns)
exec(src[src.index('AUTO_FIX_PREP ='): src.index("def validate_and_fix_rewrite")], ns)                       # авто-подготовка
exec(src[src.index("# ================= UNITS_FR"): src.index("# ================= СХЕМЫ ДАННЫХ")], ns)      # units (перевод)
exec(src[src.index("def generate_run_hash"): src.index("def row_fingerprint")], ns)                         # run_hash
sys.path.insert(0, HERE)
import checks as C

sc, mb = ns["scale_recipe"], ns["merge_broken_lines"]
fails = []

def check(name, got, want):
    ok = (want in got) if isinstance(want, str) else want(got)
    print(("OK   " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name); print("     got:", repr(got))

# --- масштабирование: единицы и составные меры (4947, 4964, 4983)
check("tsps -> 3/4 tsp (4947)", sc("1 1/2 tsps ground cumin", "", 0.5)[0], "3/4 tsp ground cumin")
check("C. -> cup", sc("1 C. chicken broth", "", 0.5)[0], "1/2 cup chicken broth")
check("cup plus tbsp (4964)", sc("3/4 cup plus 2 tbsp low carb sugar substitute", "", 0.5714)[0], "1/2 cup low carb")
check("grams + US в скобке (4983)", mb("7-9 grams grass-fed gelatin (about 2 tsp or a bit more)"), "2 tsp grass-fed gelatin")
# --- юникод-дроби (5009, 5014, 4781)
check("¼ cup", sc("¼ cup sliced almonds", "", 0.5)[0], "2 tbsp sliced almonds")
check("1½ tsp", sc("1½ tsp vanilla", "", 0.5)[0], "3/4 tsp vanilla")
# --- яйца и скобки (4640)
egg = sc("1 large Egg (or ~2 tbsp additional butter, ghee, or coconut oil)", "", 0.5)[0]
check("яйцо в ложки, скобка цела", egg, lambda t: t.startswith("1 1/2 tbsp beaten egg (or") and t.count("(") == t.count(")"))
# --- масло для фритюра (4507) и не-фритюр (4485)
check("масло фритюра не масштабируется", sc("1 1/2 cups avocado oil", "Heat oil to 1 1/2 inches deep.", 0.4)[0], "1 1/2 cups avocado oil")
check("fry the bacon не замораживает масло", sc("1/4 cup olive oil", "Fry the bacon.", 0.5)[0], "2 tbsp olive oil")
# --- граммы без US-эквивалента (5382), щепотка не делится (5396)
check("60 grams -> 2 oz (5382)", mb("60 grams macadamia nuts"), "2 oz macadamia nuts")
check("500 grams -> 1 lb", mb("500 grams ground beef"), "1 lb ground beef")
check("4 grams не трогаем", mb("4 grams gelatin"), "4 grams gelatin")
check("1 pinch salt не делится", sc("1 pinch salt", "", 0.5)[0], "1 pinch salt")
# --- нормы на штуку не масштабируются
check("per-portion в методе", sc("2 cups beef", "Top each taco with 2 tbsp salsa.", 0.5)[1], "2 tbsp salsa")
check("общее количество в методе", sc("2 cups beef", "Stir in 1 cup beef.", 0.5)[1], "1/2 cup beef")
# --- склейка строк (4482 / 4497)
check("склейка обрыва скобки", mb("3 lb chuck roast (or beef brisket\ntrimmed and cut)\n1 tbsp oil"), "(or beef brisket trimmed and cut)\n1 tbsp oil")
check("не склеивать со строкой-количеством (4497)", mb("1.5 lb Pork ( (tenderloin\n1/3 cup olive oil\n3 tbsp lemon juice").count("\n"), lambda n: n == 2)
# --- авто-добавление шага подготовки вместо повторного запроса (8044)
_ing = "1 medium cucumber, peeled and seeded\n2 cloves garlic, minced\n1 can artichoke hearts, drained"
_new, _ = ns["auto_fix_prep"](_ing, "Mix well.", ["drain", "peel", "seed", "mince"])
check("auto_fix_prep закрывает проверки", (C.prep_check(_ing, _new), C.form_check(_ing, _new)), lambda x: x[0] == [] and "mince" not in x[1])
check("зубчик чеснока не делится (8045)", sc("1 clove garlic, minced", "", 0.5)[0], "1 clove garlic, minced")
check("3.5 oz -> 3 1/2 oz", mb("100 g ricotta cheese (3.5 oz)"), "3 1/2 oz ricotta cheese")
# --- мл/кг/десятичные в источнике и в результате (8515, 8552, 8584, 8658)
check("500 ml -> 2 cups (8515)", mb("500 ml heavy cream"), "2 cups heavy cream")
check("1 kg -> 2 1/4 lb (8653)", mb("1 kg raw pork belly"), "2 1/4 lb raw pork belly")
check("250 g each -> 9 oz each (8658)", ns["fix_metric_leftovers"]("4 salmon fillets, 250 g each")[0], "9 oz each")
check("2.2 lb -> 2 1/4 lb (8552)", ns["fix_decimal_measures"]("2.2 lb chicken")[0], "2 1/4 lb chicken")
check("13.5 oz -> 13 1/2 oz (8584)", ns["fix_decimal_measures"]("1 can milk, 13.5 oz")[0], "13 1/2 oz")
check("'Optional:' не даёт ложного fidelity", C.fidelity_check("Optional: 4 tbsp butter", "4 tbsp butter, optional"), lambda x: x == [])
# --- v21: упаковки canned, «+», бренды, ссылки (8824, 8902, 8809, 8930, 8948)
check("canned не считается упаковкой (8824)", sc("1 cup canned diced green chiles", "", 0.5)[0], "1/2 cup canned diced green chiles")
check("1/4 cup + 2 tbsp (8902)", sc("1/4 cup + 2 tbsp olive oil", "", 0.5)[0], "3 tbsp olive oil")
check("2 tbsp + 1 tsp не превращается в 1 tbsp", sc("2 tbsp + 1 tsp cacao butter", "", 0.5)[0], "3 1/2 tsp cacao butter")
_b = mb("1/2 cup granulated Swerve (100 g)\n1 tbsp Sukrin Gold, Swerve or erythritol (10 g)\n1/4 cup oil [https://amzn.to/1ikukTk] (75 ml)")
check("бренды и ссылки убраны", _b, lambda x: "Swerve" not in x and "Sukrin" not in x and "amzn" not in x)
check("fidelity понимает сумму строк (8787)", C.fidelity_check("1/2 cup coconut butter\n1/2 cup coconut butter", "1 cup coconut butter"), lambda x: x == [])
check("fidelity понимает stick -> cup", C.fidelity_check("1 stick butter", "1/2 cup butter"), lambda x: x == [])
# --- v22: устойчивая проверка количеств и варианты рецепта (8911, 8973, 4563, 9034)
nq = ns["no_quantity_ratio"]
check("HTML <li> считается с количеством", nq("<li>1 cup flour</li>\n<li><span>2</span> eggs</li>")[0], lambda r: r == 0)
check("чекбоксы ▢ ☐ • не мешают", nq("▢ 1 cup flour\n☐ 2 eggs\n• 1/2 tsp salt")[0], lambda r: r == 0)
check("about/(8 oz)/словесные числа", nq("about 1 cup flour\n(8 oz) cream cheese\ntwo eggs\na pinch of salt")[0], lambda r: r == 0)
check("список без количеств", nq("flour\neggs\nsalt\nbutter")[0], lambda r: r == 1.0)
d = ns["multi_variant_dups"]
check("слои (8911) не варианты", len(d("1/2 cup butter\n1 cup flour\n1/4 cup sweetener\n1 tsp cinnamon\n2 tbsp butter\n2 tbsp sweetener\n1/2 tsp cinnamon")), lambda n: n < 2)
check("чизкейк (8973) не варианты", len(d("16 oz cream cheese\n1/2 cup allulose\n1 tsp vanilla\n8 oz cream cheese\n1/4 cup allulose\n1/2 tsp vanilla")), lambda n: n < 2)
check("бейглы (4563) — варианты", len(d("1 1/2 cups almond flour\n2 large eggs\n2 1/2 cups mozzarella cheese (must be\n2 oz cream cheese\n1/2 cup coconut flour\n3 large eggs\n2 1/2 cups shredded mozzarella cheese\n2 oz cream cheese")), lambda n: n >= 2)
# --- порции
t = ns["target_servings_for"]
check("порции 12->8, 8->8, 9->4, 6->6, 20->8", (t(12), t(8), t(9), t(6), t(20)), lambda x: x == (8, 8, 4, 6, 8))

def test_target_servings_threshold8():
    _saved = (ns["RESCALE_TARGET_SERVINGS"], ns["RESCALE_ONLY_IF_MORE_THAN"], ns["RESCALE_MAX_DIVISOR"])
    try:
        ns["RESCALE_TARGET_SERVINGS"] = 4
        ns["RESCALE_ONLY_IF_MORE_THAN"] = 8
        ns["RESCALE_MAX_DIVISOR"] = 2.5
        got = {n: t(n) for n in (6, 7, 8, 9, 10, 11, 16, 20, 24, 32)}
        want = {6: 6, 7: 7, 8: 8, 9: 4, 10: 4, 11: 8, 16: 8, 20: 8, 24: 16, 32: 16}
        check("test_target_servings_threshold8", got, lambda g: g == want)
    finally:
        ns["RESCALE_TARGET_SERVINGS"], ns["RESCALE_ONLY_IF_MORE_THAN"], ns["RESCALE_MAX_DIVISOR"] = _saved

test_target_servings_threshold8()
# --- источник без количеств / варианты
check("источник без количеств", ns["no_quantity_ratio"]("olive oil\nground beef\nsalt\nonion\nbroth")[0], lambda r: r >= 0.7)
check("варианты в одном списке (4563)", len(ns["multi_variant_dups"]("1 cup almond flour\n2 eggs\n2 cups mozzarella\n2 oz cream cheese\n1 tbsp baking powder\n1/2 cup coconut flour\n3 eggs\n2 cups mozzarella cheese\n2 oz cream cheese\n1 tbsp baking powder")), lambda n: n >= 3)
# --- перевод (units)
def tr(x):
    m = {}; return ns["restore_units"](ns["protect_units"](x, m), m)
check("350°F -> 180°C", tr("Bake at 350°F."), "180°C")
check("8 oz -> 225 g", tr("8 oz cream cheese"), "225 g")
check("1/2 inch -> 1 cm", tr("1/2 inch thick"), "1 cm")
check("5- to 6-quart", tr("a 5- to 6-quart pot"), "l pot")
# --- checks.py
check("fidelity понимает tsps/¼", C.fidelity_check("1 1/2 tsps cumin\n¼ cup nuts", "1 1/2 tsp cumin\n1/4 cup nuts"), lambda x: x == [])
check("безопасная температура курицы", C.fix_safety_temps("1 lb ground chicken", "Cook until the internal temperature reaches 160°F.")[0], "165°F")
check("ссылка на рецепт в списке", C.strip_recipe_refs("1/2 recipe fresh salsa"), "prepared fresh salsa, for serving")
check("style_check poultry", C.style_check("1 lb chicken", "Cook the poultry.", ""), lambda x: len(x) == 1)
check("run_hash unchanged (r04.3)", ns["generate_run_hash"]("TEST_PROMPT", "gemini-3.8-flash", "4/8/2.5"), lambda h: h == "c31fc268")
# --- r06.12: prep_check 'seed' в перечислении (1053)
check("prep_check seed с запятой (1053)", C.prep_check("1/4 cup roasted red pepper, peeled, seeded, and chopped", "Peel, seed, and chop 1/4 cup roasted red pepper."), lambda x: x == [])
check("prep_check seed отсутствует", C.prep_check("1/4 cup roasted red pepper, peeled, seeded, and chopped", "Peel and chop 1/4 cup roasted red pepper."), lambda x: "seed" in x)
check("prep_check sesame seeds не ложное", C.prep_check("1 tbsp sesame seeds", "Sprinkle on top."), lambda x: x == [])
check("prep_check seed без запятой", C.prep_check("1/4 cup roasted red pepper, peeled, seeded, and chopped", "Peel, seed and chop 1/4 cup roasted red pepper."), lambda x: x == [])

print("\nИТОГО:", "все тесты пройдены" if not fails else f"упало {len(fails)}: {fails}")
sys.exit(1 if fails else 0)
