import re
from collections import Counter
from fractions import Fraction

# Версия самих проверок — участвует в run_hash скрипта, чтобы при изменении
# логики кэш инвалидировался. Поднимайте при любой правке этого файла.
CHECKS_VERSION = "c9"


# ---------- 1. Проверка количеств в ингредиентах (рерайт) ----------
_QN = r"\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?"
_QU = r"tsps?|tbsps?|teaspoons?|tablespoons?|cups?|c\.|oz|ounces?|lbs?|pounds?|fl\s*oz|sticks?|pints?|pt|quarts?|qt"
_LEAD = re.compile(
    rf"^\s*(?P<n>{_QN})(?:\s*(?:-|to)\s*(?P<n2>{_QN}))?(?![\d%/])\s*(?P<u>{_QU})?(?:\b|(?<=\.))",
    re.I,
)

_BASE = {
    "tsp": ("v", 1), "tbsp": ("v", 3), "cup": ("v", 48), "cups": ("v", 48),
    "oz": ("w", 1), "lb": ("w", 16), "lbs": ("w", 16), "floz": ("v", 6), "": ("n", 1),
    "stick": ("v", 24), "pint": ("v", 96), "quart": ("v", 192),
}


_UNIT_ALIAS = {"tsps": "tsp", "teaspoon": "tsp", "teaspoons": "tsp", "tbsps": "tbsp", "tablespoon": "tbsp",
               "tablespoons": "tbsp", "c.": "cup", "ounce": "oz", "ounces": "oz", "pound": "lb", "pounds": "lb",
               "sticks": "stick", "pints": "pint", "pt": "pint", "quarts": "quart", "qt": "quart"}


def _fr(s: str) -> Fraction:
    s = s.strip()
    if " " in s:
        w, f = s.split(None, 1)
        return Fraction(w) + Fraction(f)
    return Fraction(s)


def _lead_qty(line: str) -> str | None:
    """'1 1/2 cups x' и '1.5 cup x' дают одинаковый ключ; 32 oz == 2 lb."""
    m = _LEAD.match(line)
    if not m:
        return None
    u = (m.group("u") or "").lower().replace(" ", "")
    u = _UNIT_ALIAS.get(u, u)
    kind, k = _BASE[u]
    vals = [str(_fr(re.sub(r"\s+", " ", x)) * k) for x in (m.group("n"), m.group("n2")) if x]
    return f"{kind}:" + "-".join(vals)


_UF = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3", "⅛": "1/8"}


def _nf(text: str) -> str:
    text = re.sub(r"(\d)([½¼¾⅓⅔⅛])", lambda m: f"{m.group(1)} {_UF[m.group(2)]}", text or "")
    return re.sub(r"[½¼¾⅓⅔⅛]", lambda m: _UF[m.group(0)], text)


def _explain_merges(missing: list[str], added: list[str]) -> tuple[list[str], list[str]]:
    """Склейка одинаковых ингредиентов: два-четыре пропавших количества одного вида суммируются в одно появившееся."""
    from itertools import combinations
    miss, add = list(missing), list(added)

    def val(k: str):
        kind, _, v = k.partition(":")
        if "-" in v:
            return None
        return kind, Fraction(v)

    changed = True
    while changed:
        changed = False
        for a in list(add):
            va = val(a)
            if va is None:
                continue
            cand = [m for m in miss if val(m) and val(m)[0] == va[0]]
            for n in (2, 3, 4):
                hit = next((combo for combo in combinations(cand, n)
                            if sum(val(m)[1] for m in combo) == va[1]), None)
                if hit:
                    for m in hit:
                        miss.remove(m)
                    add.remove(a)
                    changed = True
                    break
            if changed:
                break
    return miss, add


def fidelity_check(src_ing: str, out_ing: str) -> list[str]:
    """Сравнивает ведущие количества строк ингредиентов до/после; законные склейки строк (сумма) не считаются."""
    _opt = lambda t: re.sub(r"(?im)^\s*optional:\s*", "", t)  # 'Optional: 4 tbsp butter' = обычная строка
    a = Counter(q for q in map(_lead_qty, _opt(_nf(src_ing)).splitlines()) if q)
    b = Counter(q for q in map(_lead_qty, _opt(_nf(out_ing)).splitlines()) if q)
    missing, added = _explain_merges(sorted((a - b).elements()), sorted((b - a).elements()))
    probs = []
    if missing:
        probs.append(f"пропали/изменились (база): {missing}")
    if added:
        probs.append(f"появились/изменились (база): {added}")
    return probs


# ---------- 2. Безопасные температуры ----------
_POULTRY = re.compile(
    r"\b(?:ground\s+(?:chicken|turkey)|"
    r"(?:chicken|turkey|duck)\s+(?:breasts?|thighs?|wings?|drumsticks?|tenders?|legs?|pieces|cutlets)|"
    r"whole\s+(?:chicken|turkey)|poultry)\b",
    re.I,
)
_GROUND = re.compile(r"\bground\s+(?:beef|pork|lamb|veal)\b|\bsausage\b|\bmeatballs?\b", re.I)
# Расширенный контекст: ловим °F, ° F, degrees F, degree Fahrenheit, Fahrenheit, и «F» после числа.
_TEMP_CTX = re.compile(
    r"((?:internal\s+temperature|registers?|reaches?|reading|hits|thermometer)[^.\n]{0,60}?)"
    r"(\d{3})"
    r"(\s*(?:°\s*F(?:ahrenheit)?|degrees?\s*F(?:ahrenheit)?|Fahrenheit))",
    re.I,
)


def fix_safety_temps(ingredients: str, instructions: str) -> tuple[str, list[str]]:
    """Поднимает температуру готовности до безопасного минимума: птица 165°F, фарш/колбаса 160°F."""
    minimum = 165 if _POULTRY.search(ingredients) else (160 if _GROUND.search(ingredients) else 0)
    notes = []
    if not minimum:
        return instructions, notes

    def sub(m: re.Match) -> str:
        t = int(m.group(2))
        if 100 <= t < minimum:
            notes.append(f"{t}°F -> {minimum}°F")
            return f"{m.group(1)}{minimum}{m.group(3)}"
        return m.group(0)

    return _TEMP_CTX.sub(sub, instructions), notes


# ---------- 3. Шаги подготовки из ингредиентов должны быть в методе ----------
# Каждый ключ — «ярлык» для лога, значение — регулярка форм подготовки.
# Важно: ищем осмысленные формы, а не просто первое слово (иначе 'pat dry'
# ловится на 'chicken patties' и т.п.).
_PREP_STEMS: dict[str, str] = {
    "drain":  r"\bdrain",
    "squeez": r"\bsqueez",
    "peel":   r"\bpeel",
    "soften": r"\bsoft",
    "thaw":   r"\bthaw",
    "rinse":  r"\brins",
    # 'seeded', 'seeds removed', 'remove the seeds', но НЕ просто 'seeds'
    # (иначе 'sesame seeds' даст ложное срабатывание).
    "seed":   r"\bseeded\b|\bseeds? removed\b|\bremov(?:e|ing) (?:the )?seeds?\b|\bde-?seed|\bseed(?:ing)?\s+(?:the|and|each|it|them)\b",
    "husk":   r"\bhusk",
    "trim":   r"\btrim",
    # 'pat dry', 'patted dry', 'pat the X dry', 'blot dry', 'dry the X' и т.д.
    # \bpat(?:ted|ting)?\b не ловит 'patties' (после 'pat' нет границы слова).
    "pat dry": (
        r"\bpat(?:ted|ting)?\b[^.\n]{0,40}\bdry"
        r"|\bblot(?:ted|ting)?\b[^.\n]{0,40}\bdry"
        r"|\bdry\s+(?:the|them|it|each|both)\b"
    ),
    "zest":   r"\bzest",
    "devein": r"\bdevein",
}


def prep_check(ingredients: str, instructions: str) -> list[str]:
    """Слова-подготовки из строк ингредиентов, которых нет в методе (по реальным формам)."""
    ing, ins = ingredients.lower(), instructions.lower()
    missing = []
    for label, pattern in _PREP_STEMS.items():
        if re.search(pattern, ing) and not re.search(pattern, ins):
            missing.append(label)
    return missing


# ---------- 4. Ссылки на чужой рецепт в тексте ----------
_RECIPE_REF = re.compile(
    r"\b(?:\d+(?:\s+\d+/\d+|/\d+)?|half|one|a)\s+(?:of\s+)?(?:the\s+)?recipe\b"
    r"|\bsee\s+(?:my|the)\s+(?:post|blog|recipe|page)\b"
    r"|\b(?:recipe|post)\s+(?:here|below|above)\b"
    r"|\blink(?:ed)?\s+(?:to|below|above|here)\b"
    r"|\baccording\s+to\s+the\s+(?:instructions?|recipe)\b",
    re.I,
)


def recipe_ref_check(ingredients: str, instructions: str) -> list[str]:
    hits = {m.group(0).lower() for m in _RECIPE_REF.finditer(ingredients + "\n" + instructions)}
    return sorted(hits)


# ---------- 5. Ссылка на чужой рецепт в строке ингредиента -> обычный ингредиент ----------
_RECIPE_LINE = re.compile(
    r"^\s*(?:\d+(?:\s+\d+/\d+|/\d+)?|half(?:\s+of)?(?:\s+a)?|one)\s+(?:of\s+)?(?:the\s+)?recipe\s+(?:of\s+)?(?P<name>.+?)\s*$",
    re.I,
)


def strip_recipe_refs(ingredients: str) -> str:
    """'1/2 recipe fresh salsa' -> 'prepared fresh salsa, for serving'. Вызывать ДО отправки в ИИ
    (и в build_sent_ingredients, чтобы fidelity_check видел ту же строку)."""
    out = []
    for line in ingredients.splitlines():
        m = _RECIPE_LINE.match(line)
        out.append(f"prepared {m.group('name')}, for serving" if m else line)
    return "\n".join(out)


# ---------- 6. Форма нарезки из ингредиента должна быть в методе (мягкая проверка) ----------
_FORM_STEMS = {"mince": r"\bminc", "dice": r"\bdic", "shred": r"\bshred",
               "grate": r"\bgrat", "chop": r"\bchop", "crumble": r"\bcrumb"}


def form_check(ingredients: str, instructions: str) -> list[str]:
    """Предупреждение: в списке 'minced garlic', а в методе 'garlic' без формы. Шумит слегка, только в лог."""
    ing, ins = ingredients.lower(), instructions.lower()
    return [lab for lab, p in _FORM_STEMS.items() if re.search(p, ing) and not re.search(p, ins)]


# ---------- 7. Стиль (только предупреждения в лог) ----------
_TOTAL_RE = re.compile(r"\busing\s+[\d/ ]+\s*\w+\s+(?:\w+\s+)?total\b|\bacross all (?:the )?(?:four|three|two|\d+)\b", re.I)


def style_check(ingredients: str, instructions: str, src_instructions: str = "") -> list[str]:
    """Стилевые дефекты результата: 'poultry' вместо конкретной птицы, хвосты 'using X total',
    выдуманный microwave (его нет ни в исходной инструкции, ни в ингредиентах)."""
    out: list[str] = []
    if re.search(r"\bpoultry\b", instructions, re.I) and not re.search(r"\bpoultry\b", ingredients, re.I):
        out.append("слово 'poultry' в методе")
    if _TOTAL_RE.search(instructions) and not _TOTAL_RE.search(src_instructions or ""):
        out.append("хвост 'using X total' / 'across all N'")
    if re.search(r"microwave", instructions, re.I) and not re.search(
            r"microwave", (src_instructions or "") + "\n" + ingredients, re.I):
        out.append("microwave нет в источнике")
    return out
