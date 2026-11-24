#!/usr/bin/env python3
"""
Пакетный рерайт/перевод строк recipe_rewrite с кэшированием.
Проверки: fidelity_check, fix_safety_temps, prep_check, recipe_ref_check,
strip_recipe_refs, form_check, style_check из checks.py (c4+).
"""

from __future__ import annotations

import csv
import hashlib
import logging
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

import mariadb
import yaml
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

try:
    from checks import (
        CHECKS_VERSION,
        fidelity_check,
        fix_safety_temps,
        form_check,
        prep_check,
        recipe_ref_check,
        strip_recipe_refs,
        style_check,
    )
except ImportError as _e:
    # ImportError бывает не только «файла нет»: часто в найденном checks.py нет нужной функции (старая версия).
    print(f"ОШИБКА импорта из checks.py: {_e}")
    print("Нужен checks.py версии c4+ (в нём должны быть CHECKS_VERSION, fidelity_check, fix_safety_temps, "
          "form_check, prep_check, recipe_ref_check, strip_recipe_refs, style_check).")
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ================= НАСТРОЙКА ЛОГОВ =================
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
LOGGER = logging.getLogger("desserts_rewriter_update")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("google.auth").setLevel(logging.WARNING)

# ================= НАСТРОЙКИ =================
APP_MODE = os.getenv("APP_MODE", "rewrite").lower()
if APP_MODE not in ("rewrite", "translate", "adapt"):
    raise ValueError("APP_MODE должен быть 'rewrite', 'translate' или 'adapt'")

FETCH_SIZE = int(os.getenv("FETCH_SIZE", "3"))
SLEEP_BETWEEN_REQUESTS = float(os.getenv("SLEEP_BETWEEN_REQUESTS", "2.0"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}

REWRITE_IDS_RAW = os.getenv("REWRITE_IDS", "").strip()
TRANSLATE_IDS_RAW = os.getenv("TRANSLATE_IDS", "").strip()
ADAPT_IDS_RAW = os.getenv("ADAPT_IDS", "").strip()
ADAPT_EXCLUDE_IDS_RAW = os.getenv("ADAPT_EXCLUDE_IDS", "").strip()
ADAPT_LIMIT = int(os.getenv("ADAPT_LIMIT", "10"))
ADAPT_DISH_TYPES_RAW = os.getenv("ADAPT_DISH_TYPES", "").strip()
ADAPT_MARKET = os.getenv("ADAPT_MARKET", "US")
ADAPT_LOCALE = os.getenv("ADAPT_LOCALE", "en_US")
ADAPT_STATUS = os.getenv("ADAPT_STATUS", "active").lower()
ADAPT_FORCE = os.getenv("ADAPT_FORCE", "0").lower() in {"1", "true", "yes", "on"}
VALID_ADAPT_STATUSES = ("draft", "active", "archived")

RESCALE_TARGET_SERVINGS = int(os.getenv("RESCALE_TARGET_SERVINGS", "4"))
# Если делитель больше этого значения (16 порций -> 4 = x4), цель удваивается (16 -> 8), чтобы не получать 1/4 яйца.
RESCALE_MAX_DIVISOR = float(os.getenv("RESCALE_MAX_DIVISOR", "3"))
RESCALE_ONLY_IF_MORE_THAN = int(os.getenv("RESCALE_ONLY_IF_MORE_THAN", "8"))
RESCALE_SMART = os.getenv("RESCALE_SMART", "1").lower() in {"1", "true", "yes", "on"}
RESCALE_CANDIDATES = [
    int(x.strip()) for x in os.getenv("RESCALE_CANDIDATES", "4,6,8,12,16,24,32").split(",") if x.strip()
]
RESCALE_COUNT_RE = re.compile(
    os.getenv("RESCALE_COUNT_RE", r"\b(?:large|medium|small|extra[- ]large|whole)?\s*(?:eggs?|egg\s+yolks?|egg\s+whites?)\b"),
    re.IGNORECASE,
)
# Строгие проверки рерайта: результат с этими дефектами НЕ сохраняется и НЕ кэшируется,
# ID попадает в список повтора (REWRITE_IDS=...). Модель недетерминирована: повтор обычно проходит.
STRICT_REWRITE_CHECKS = os.getenv("STRICT_REWRITE_CHECKS", "1") == "1"
HARD_FORM = {"mince"}  # «minced garlic» в списке, а в методе целые зубчики — строгая проверка
HARD_PREP = {"drain", "squeez", "peel", "husk", "trim", "seed", "thaw"}  # 'soften'/'rinse' не строгие (много ложных)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
THINKING_LEVEL = os.getenv("THINKING_LEVEL", "low").lower()
CACHE_TTL_DAYS = int(os.getenv("CACHE_TTL_DAYS", "30"))

TARGET_LANG = os.getenv("TARGET_LANG", "fr").lower()
TARGET_LOCALE = os.getenv("TARGET_LOCALE", "fr_FR")
TARGET_MARKET = os.getenv("TARGET_MARKET", "fr")
TARGET_CURRENCY = os.getenv("TARGET_CURRENCY", "EUR")

if APP_MODE == "translate":
    SYSTEM_INSTRUCTION_FILE = BASE_DIR / os.getenv("PROMPT_FILE_TRANSLATE", "prompts/desserts_translate_prompt_fr_v4.txt")
else:
    SYSTEM_INSTRUCTION_FILE = BASE_DIR / os.getenv("PROMPT_FILE", "prompts/desserts_rewrite_prompt_v3_5.txt")

CSV_LOG_FILE = BASE_DIR / os.getenv("CSV_LOG_FILE", "rewrite_update_log.csv")
IDS_OUT_DIR = BASE_DIR / os.getenv("IDS_OUT_DIR", "logs")
CACHE_DIR = BASE_DIR / "gemini_cache"
CACHE_DIR.mkdir(exist_ok=True)

UNITS_VERSION = "u2"
SCALE_VERSION = "s21"

EXPECTED_CSV_HEADER = [
    "Rewrite Row ID", "Recipe ID", "Previous Dish Name", "Rewritten Dish Name",
    "Previous Servings", "Rewritten Servings",
    "Prep Time (min)", "Cook Time (min)",
    "Previous Ingredients", "Rewritten Ingredients",
    "Previous Instructions", "Rewritten Instructions",
    "recipe_id", "recipe_rewrite_id", "Status",
]

MODEL_PRICING_USD_PER_MILLION = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
}


# ================= МАТЕМАТИКА РЕЦЕПТА =================
_METRIC_LEAD_RE = re.compile(
    r"^\s*\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*(?:g|grams?|ml)\b\s+(?P<name>[^(]*?)\s*"
    r"\((?:about|approx\.?|approximately|~)?\s*(?P<us>\d+(?:\s+\d+/\d+|/\d+|\.\d+)?(?:\s*[-–]\s*\d+)?\s*"
    r"(?:tsps?|tbsps?|cups?|oz|lbs?)\b)(?P<tail>[^)]*)\)", re.IGNORECASE)


_METRIC_ONLY_RE = re.compile(
    r"^(?P<ws>\s*)(?P<n>\d+(?:\.\d+)?)(?:\s*[-–]\s*(?P<n2>\d+(?:\.\d+)?))?\s*"
    r"(?P<u>g|grams?|kg|kilograms?|ml|milliliters?|l|liters?|litres?)\b\s+(?P<name>.+)$",
    re.IGNORECASE)


def _metric_to_us(value: float, unit: str) -> str | None:
    """Метрика -> практичная US-мера. Слишком малые количества (<14 g, <5 ml) не трогаем."""
    u = unit.lower()
    if u.startswith("k"):
        return _grams_to_us(value * 1000)
    if u in ("g", "gram", "grams"):
        return _grams_to_us(value)
    ml = value if u.startswith("m") else value * 1000
    if ml < 5:
        return None
    if ml >= 60:
        v = _snap(Fraction(ml / 240).limit_denominator(1000), _CUP_GRID)
        return f"{_sfmt(v)} {'cup' if v <= 1 else 'cups'}"
    if ml >= 15:
        return f"{_sfmt(_snap(Fraction(ml / 15).limit_denominator(1000), _HALVES))} tbsp"
    return f"{_sfmt(_nearest(Fraction(ml / 5).limit_denominator(1000), [Fraction(1, 4), Fraction(1, 2), Fraction(3, 4), Fraction(1)]))} tsp"


def _grams_to_us(g: float) -> str | None:
    """Граммы -> практичные унции/фунты. Меньше 14 g не трогаем (слишком мелко, нужен человек)."""
    if g < 14:
        return None
    oz = Fraction(g / 28.35).limit_denominator(1000)
    if oz >= 16:
        return f"{_sfmt(_snap(oz / 16, _QUARTERS))} lb"
    v = _snap(oz, _QUARTERS) if oz < 2 else (_snap(oz, _HALVES) if oz < 8 else Fraction(round(oz)))
    return f"{_sfmt(v)} oz"


def _dec_to_fraction_text(us: str) -> str:
    """'3.5 oz' -> '3 1/2 oz'; '0.8 oz' -> '3/4 oz' (до четверти)."""
    m = re.match(r"^(\d+\.\d+)(\s*[-–].*|\s+.*)$", us)
    if not m:
        return us
    v = Fraction(round(float(m.group(1)) * 4), 4)
    return f"{_sfmt(v)}{m.group(2)}"


BRAND_REPLACEMENTS = [  # (regex, замена) — по составу продукта; порядок важен
    (re.compile(r"confectioner'?s?\s+swerve", re.I), "powdered erythritol"),
    (re.compile(r"granulated\s+swerve", re.I), "granulated erythritol"),
    (re.compile(r"swerve\s+brown(?:\s+sugar)?", re.I), "brown sugar substitute"),
    (re.compile(r"\bswerve\b", re.I), "erythritol"),
    (re.compile(r"sukrin\s+gold", re.I), "brown sugar substitute"),
    (re.compile(r"sukrin\s+melis", re.I), "powdered sweetener"),
    (re.compile(r"\bsukrin\b", re.I), "sweetener"),
    (re.compile(r"\b(?:besti|wholesome yum|pyure|lakanto|choczero|kerrygold|primal kitchen|happy farms|"
                r"countryside creamery|paleovalley|philadelphia)\b\s*", re.I), ""),
]
_URL_RE = re.compile(r"\s*\[?https?://[^\s\]\)]+\]?", re.IGNORECASE)


def neutralize_brands(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    for pat, repl in BRAND_REPLACEMENTS:
        if pat.search(text):
            notes.append(pat.pattern[:25])
            text = pat.sub(repl, text)
    return re.sub(r"[ \t]{2,}", " ", text), notes


def clean_source_line_noise(text: str) -> str:
    """Убирает партнёрские ссылки [https://amzn.to/..] и бренды из источника ДО отправки в ИИ."""
    text = _URL_RE.sub("", text or "")
    return neutralize_brands(text)[0]


def normalize_metric_leads(text: str) -> str:
    """'7-9 grams gelatin (about 2 tsp or a bit more)' -> '2 tsp gelatin (or a bit more)'."""
    out = []
    for line in (text or "").splitlines():
        mo = _METRIC_ONLY_RE.match(line)
        if mo and not _METRIC_LEAD_RE.match(line):
            lo = _metric_to_us(float(mo.group("n")), mo.group("u"))
            hi = _metric_to_us(float(mo.group("n2")), mo.group("u")) if mo.group("n2") else None
            if lo and (hi or not mo.group("n2")):
                head = lo if not hi else f"{lo.rsplit(' ', 1)[0]}-{hi}"
                out.append(f"{mo.group('ws')}{head} {mo.group('name')}")
                continue
        m = _METRIC_LEAD_RE.match(line)
        if m:
            tail = m.group("tail").strip()
            out.append(f"{_dec_to_fraction_text(m.group('us').strip())} {m.group('name').strip()}"
                       + (f" ({tail})" if tail else ""))
        else:
            out.append(line)
    return "\n".join(out)


def merge_broken_lines(text: str) -> str:
    """Склеивает строки, если в них не закрыта скобка. ТОЛЬКО для ингредиентов.
    Строка, начинающаяся с количества, никогда не приклеивается к предыдущей (битая '( (' в источнике)."""
    out, buf = [], ""
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if buf and re.match(r"^[\d½¼¾⅓⅔⅛]", s):
            out.append(buf)
            buf = ""
        buf = f"{buf} {s}" if buf else s
        if buf.count("(") <= buf.count(")"):
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return normalize_metric_leads(clean_source_line_noise("\n".join(out)))


_SN = r"\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?"
_SU = r"fl\.?\s*oz|tsps?|teaspoons?|tbsps?|tablespoons?|cups?|c\.|oz|ounces?|lbs?|pounds?"
_PACK_RE = re.compile(r"(?:cans?|jars?|packages?|pkgs?|bags?|boxes|box|bottles?|containers?|pouch(?:es)?|packets?)\b", re.IGNORECASE)

_LEAD_RE = re.compile(
    rf"^(?P<ws>\s*)(?P<n1>{_SN})(?:\s*(?:-|–|to)\s*(?P<n2>{_SN}))?"
    rf"(?![\d%/])(?!\s*-?\s*inch)(?P<sp>\s*)(?:(?P<u>{_SU})(?:\b|(?<=\.)))?",
    re.IGNORECASE,
)
_FRY_OIL_RE = re.compile(
    rf"^\s*(?P<q>{_SN})\s*cups?\b(?=[^\n]*\boil\b)", re.IGNORECASE)
_DIV_RE = re.compile(r"\((?P<pre>divided\s+into\s+|plus\s+)(?P<body>[^)]*)\)", re.IGNORECASE)
_APPROX_RE = re.compile(r"\((?P<pre>[^)]*?(?:~|≈|\babout\s+|\bapprox\.?\s+|\bapproximately\s+))(?P<body>[^)]*)\)", re.IGNORECASE)
_INSTR_RE = re.compile(
    rf"(?<![\w/.\-])(?P<n1>{_SN})(?:\s*(?:-|–|to)\s*(?P<n2>{_SN}))?\s+(?P<u>{_SU})(?:\b|(?<=\.))",
    re.IGNORECASE,
)


def _sval(s: str) -> Fraction:
    s = s.strip()
    if " " in s:
        whole, frac = s.split(None, 1)
        return Fraction(whole) + Fraction(frac)
    return Fraction(s)


def _sfmt(x: Fraction) -> str:
    whole = x.numerator // x.denominator
    rem = x - whole
    if rem == 0:
        return str(whole)
    r = f"{rem.numerator}/{rem.denominator}"
    return f"{whole} {r}" if whole else r


def _nearest(v: Fraction, options: list) -> Fraction:
    return min(options, key=lambda o: abs(o - v))


def _snap(v: Fraction, grid: list) -> Fraction:
    whole = v.numerator // v.denominator
    return whole + _nearest(v - whole, grid)


_CUP_GRID = [Fraction(0), Fraction(1, 4), Fraction(1, 3), Fraction(1, 2),
             Fraction(2, 3), Fraction(3, 4), Fraction(1)]
_QUARTERS = [Fraction(i, 4) for i in range(5)]
_HALVES = [Fraction(0), Fraction(1, 2), Fraction(1)]
_TSP_OPTS = [Fraction(1, 8), Fraction(1, 4), Fraction(1, 2), Fraction(3, 4)] + [
    Fraction(i, 4) for i in range(4, 25)
]
_CUP_TBSP = [Fraction(4), Fraction(16, 3), Fraction(8), Fraction(32, 3), Fraction(12), Fraction(16)]


def _kind(unit: str) -> str:
    u = re.sub(r"\s+", " ", unit.lower().replace(".", ""))
    if u.startswith(("teaspoon", "tsp")):
        return "tsp"
    if u.startswith(("tablespoon", "tbsp")):
        return "tbsp"
    if u.startswith("cup") or u == "c":
        return "cup"
    if u.startswith("fl"):
        return "floz"
    if u.startswith(("oz", "ounce")):
        return "oz"
    return "lb"


def _render(v: Fraction, kind: str, allow_change: bool = True) -> str:
    if allow_change:
        if kind == "tbsp" and v < 1:
            kind, v = "tsp", v * 3
        elif kind == "cup" and v < Fraction(1, 4):
            kind, v = "tbsp", v * 16
        elif kind == "lb" and v < 1:
            kind, v = "oz", v * 16
        elif kind == "oz" and v >= 16:
            kind, v = "lb", v / 16

    if kind == "tsp":
        if allow_change and v < Fraction(1, 16):
            return "1 pinch"
        if allow_change and v >= 3:
            tb = v / 3
            if abs(tb * 2 - round(tb * 2)) < Fraction(8, 100):
                kind, v = "tbsp", Fraction(round(tb * 2), 2)
        if kind == "tsp":
            v = _nearest(v, _TSP_OPTS) if v < 6 else _snap(v, _HALVES)

    if kind == "tbsp":
        if allow_change and v >= 4:
            for t in _CUP_TBSP:
                if abs(v - t) < Fraction(15, 100):
                    return _render(t / 16, "cup", False)
        v = _snap(v, _HALVES)
        if v == 0:
            v = Fraction(1, 2)

    if kind == "cup":
        v = _snap(v, _CUP_GRID)
        return f"{_sfmt(v)} {'cup' if v <= 1 else 'cups'}"

    if kind in ("oz", "floz"):
        v = _snap(v, _QUARTERS) if v < 2 else (_snap(v, _HALVES) if v < 8 else Fraction(round(v)))
        if v == 0:
            v = Fraction(1, 4)

    if kind == "lb":
        v = _snap(v, _QUARTERS)
        if v == 0:
            v = Fraction(1, 4)

    name = {"tsp": "tsp", "tbsp": "tbsp", "oz": "oz", "floz": "fl oz", "lb": "lb"}[kind]
    return f"{_sfmt(v)} {name}"


def _render_count(v: Fraction) -> str:
    if v >= 3:
        return str(int(v + Fraction(1, 2)))
    if v >= 1:
        return _sfmt(_snap(v, _HALVES))
    return _sfmt(_nearest(v, [Fraction(1, 2), Fraction(1)]))


def _scale_qty(n1: str, n2: str | None, unit: str | None, factor: float,
               warns: list, ctx: str) -> str | None:
    f = Fraction(factor).limit_denominator(1000)
    v1 = _sval(n1) * f
    v2 = _sval(n2) * f if n2 else None

    if unit:
        k = _kind(unit)
        if v2 is None:
            return _render(v1, k)
        a, b = _render(v1, k, False), _render(v2, k, False)
        if a == b:
            return a
        ua, ub = a.split(" ")[-1], b.split(" ")[-1]
        a_val, b_val = a.rsplit(" ", 1)[0], b.rsplit(" ", 1)[0]
        if ua == ub:
            return f"{a_val}-{b}"
        if ub == ua + "s":
            return f"{a_val}-{b_val} {ub}"
        if ua == ub + "s":
            return f"{a_val}-{b_val} {ua}"
        return f"{a}-{b}"

    out = _render_count(v1)
    if v2 is not None and _render_count(v2) != out:
        out += f"-{_render_count(v2)}"
    if v1 < 1:
        warns.append(f"счётный ингредиент уменьшен до {out}: {ctx.strip()[:50]}")
    return out


_EGG_RE = re.compile(
    r"^(?:(?:extra[- ]large|large|medium|small)\s+)?eggs?\b(?P<tail>[^,]*)(?P<note>.*)$", re.IGNORECASE)


def _egg_to_tbsp(v: Fraction, rest: str) -> str | None:
    """Дробное число яиц (<1) -> ложки: целое яйцо ~3 tbsp, белок ~2 tbsp, желток ~1 tbsp."""
    m = _EGG_RE.match(rest.strip())
    if not m or v >= 1:
        return None
    tail = m.group("tail").strip().lower()
    if tail.startswith("yolk"):   per, name = 1, "egg yolk"
    elif tail.startswith("white"): per, name = 2, "egg white"
    else:                          per, name = 3, "beaten egg"
    note = re.sub(r",?\s*\b(?:whisked|beaten|lightly beaten)\b", "", m.group("note"), flags=re.I).strip()
    tbsp = _nearest(v * per, [Fraction(i, 2) for i in range(1, 7)])
    return f"{_sfmt(tbsp)} tbsp {name}{note}"


_PLUS_RE = re.compile(rf"^\s+(?:plus|\+)\s+(?P<n>{_SN})\s*(?P<u>tsps?|teaspoons?|tbsps?|tablespoons?|cups?)\b", re.IGNORECASE)
_TSP_PER = {"tsp": Fraction(1), "tbsp": Fraction(3), "cup": Fraction(48)}


def _scale_compound(n1: str, unit: str, rest: str, factor: float):
    """'3/4 cup plus 2 tbsp' -> общая сумма * factor -> одна практичная мера. Возвращает (текст, остаток) или None."""
    pm = _PLUS_RE.match(rest)
    k1 = _kind(unit)
    if not pm or k1 not in _TSP_PER:
        return None
    total = (_sval(n1) * _TSP_PER[k1] + _sval(pm.group("n")) * _TSP_PER[_kind(pm.group("u"))]) \
        * Fraction(factor).limit_denominator(1000)
    if total >= 12:
        text = _render(total / 48, "cup")
    elif total >= 3:
        tb = _snap(total / 3, _HALVES)
        # tbsp, только если округление не уводит дальше ~8% (3 1/2 tsp лучше, чем «1 tbsp»)
        text = _render(total / 3, "tbsp") if abs(tb * 3 - total) / total <= Fraction(8, 100) else _render(total, "tsp", False)
    else:
        text = _render(total, "tsp")
    return text, rest[pm.end():]


def _scale_leading(line: str, factor: float, warns: list) -> str:
    m = _LEAD_RE.match(line)
    if not m:
        return line
    unit, rest = m.group("u"), line[m.end():]
    if unit and _PACK_RE.match(rest.lstrip()):
        warns.append(f"размер упаковки не масштабируется: {line.strip()[:50]}")
        return line
    if not unit and re.match(r"\s*(?:pinch|dash)(?:es)?\b", rest, re.IGNORECASE):
        return line  # щепотку и dash не делим
    if not unit and not m.group("n2") and re.match(r"\s*cloves?\b", rest, re.IGNORECASE):
        v = _sval(m.group("n1")) * Fraction(factor).limit_denominator(1000)
        return f"{m.group('ws')}{max(1, int(v + Fraction(1, 2)))}{m.group('sp') or ' '}{rest.lstrip()}"  # зубчик не делим
    if not unit and not m.group("n2"):
        head, par, paren = rest.partition("(")
        egg = _egg_to_tbsp(_sval(m.group("n1")) * Fraction(factor).limit_denominator(1000), head)
        if egg:
            warns.append(f"яйцо переведено в ложки: {line.strip()[:40]} -> {egg}")
            return f"{m.group('ws')}{egg}" + (f" ({paren}" if par else "")
    if unit and not m.group("n2"):
        comp = _scale_compound(m.group("n1"), unit, rest, factor)
        if comp:
            return f"{m.group('ws')}{comp[0]}{comp[1]}"
    new = _scale_qty(m.group("n1"), m.group("n2"), unit, factor, warns, line)
    sep = " " if (unit or m.group("sp")) else ""
    if unit:
        return f"{m.group('ws')}{new}{rest}"
    return f"{m.group('ws')}{new}{m.group('sp') or sep}{rest}"


_UNI_FRAC = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3", "⅛": "1/8"}


def _norm_fractions(text: str) -> str:
    """'1½ tsp' -> '1 1/2 tsp', '¼ cup' -> '1/4 cup' (иначе дробь в начале строки не масштабируется)."""
    text = text or ""
    text = re.sub(r"(\d)([½¼¾⅓⅔⅛])", lambda m: f"{m.group(1)} {_UNI_FRAC[m.group(2)]}", text)
    return re.sub(r"[½¼¾⅓⅔⅛]", lambda m: _UNI_FRAC[m.group(0)], text)


_PER_UNIT_RE = re.compile(r"\b(?:per|each|every|apiece|a\s+piece|per\s+serving|for\s+each)\b", re.IGNORECASE)


def scale_recipe(ingredients: str, instructions: str, factor: float) -> tuple[str, str, list[str]]:
    warns: list[str] = []
    if abs(factor - 1.0) < 1e-5:
        return ingredients, instructions, warns
    ingredients, instructions = _norm_fractions(ingredients), _norm_fractions(instructions)

    # Масло для фритюра/жарки «на глубину» не зависит от числа порций: размер сковороды фиксирован.
    # Только явные признаки фритюра/жарки «на глубину». Просто слово «fry» не считается
    # (иначе «fry the bacon» заморозит масло для маринада).
    frying = bool(re.search(r"inch(?:es)?\s+deep|\bdeep[- ]?fr(?:y|ied|ying)\b|\bshallow[- ]?fr(?:y|ied|ying)\b"
                            r"|\bfor (?:deep[- ]?)?frying\b|\bsubmerge",
                            instructions or "", re.IGNORECASE))
    frozen: set[str] = set()

    def instr_sub(m: re.Match) -> str:
        key = re.sub(r"\s+", " ", m.group(0).lower()).rstrip("s")
        if key in frozen:
            return m.group(0)
        # Нормы «на штуку/порцию» не масштабируются: «2 tbsp salsa on each taco», «13.5 oz each»
        ctx = m.string[max(0, m.start() - 45): m.end() + 45]
        if _PER_UNIT_RE.search(ctx):
            warns.append(f"норма на порцию/штуку не масштабируется: {m.group(0)}")
            return m.group(0)
        return _scale_qty(m.group("n1"), m.group("n2"), m.group("u"), factor, warns, m.group(0)) or m.group(0)

    lines = []
    for line in (ingredients or "").splitlines():
        fm = _FRY_OIL_RE.match(line) if frying else None
        if fm and _sval(fm.group("q")) >= Fraction(1, 2):  # меньше 1/2 cup — обычное масло, масштабируем
            frozen.add(re.sub(r"\s+", " ", f"{fm.group('q')} cup").lower())
            warns.append(f"масло для фритюра не масштабируется: {line.strip()[:45]}")
            lines.append(line)
            continue
        line = _scale_leading(line, factor, warns)
        def _approx(m: re.Match) -> str:
            body = m.group("body")
            lead = _LEAD_RE.match(body)
            new_body = (_INSTR_RE.sub(instr_sub, body) if lead and lead.group("u")
                        else _scale_leading(body, factor, warns))
            return f"({m.group('pre')}{new_body})"

        line = _APPROX_RE.sub(_approx, line)
        # '(divided into 1 tbsp and 2 tbsp)' — масштабируем все «число + единица» внутри скобки
        line = _DIV_RE.sub(lambda m: f"({m.group('pre')}{_INSTR_RE.sub(instr_sub, m.group('body'))})", line)
        lines.append(line)

    return "\n".join(lines), _INSTR_RE.sub(instr_sub, instructions or ""), warns


# ================= UNITS_FR =================
class LocaleMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locale: str
    lang: str
    market: str
    currency: str
    usd_rate: float = Field(gt=0)
    prompt_file: str


class LocaleNumberFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decimal_sep: str
    range_sep: str


class LocaleSpoons(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tsp: str
    tbsp: str


class LocaleWeightUnits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    g_step: int = Field(gt=0)
    g_step_min: int = Field(ge=0)
    kg_from_g: int = Field(gt=0)
    kg_step: float = Field(gt=0)
    g_suffix: str
    kg_suffix: str


class LocaleVolumeUnits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ml_step: int = Field(gt=0)
    ml_step_below: int = Field(gt=0)
    use_cl: bool
    cl_below_ml: int = Field(gt=0)
    l_from_ml: int = Field(gt=0)
    ml_suffix: str
    cl_suffix: str
    l_suffix: str


class LocaleLengthUnits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mm_below_cm: float = Field(gt=0)
    half_cm_below: float = Field(gt=0)
    mm_suffix: str
    cm_suffix: str


class LocaleUnits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weight: LocaleWeightUnits
    volume: LocaleVolumeUnits
    length: LocaleLengthUnits


class OvenEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    c: int
    th: str | None = None


class LocaleOven(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tolerance_f: int = Field(ge=0)
    convection_offset: int | None = None
    context_regex: str | None = None
    template: str
    fallback_template: str = "{c}°C"
    entries: dict[int, OvenEntry]


class LocaleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meta: LocaleMeta
    number_format: LocaleNumberFormat
    spoons: LocaleSpoons
    units: LocaleUnits
    cup_grams: list[tuple[str, int]]
    oven: LocaleOven
    containers: list[str]
    nutrition_headers: list[str]


def load_locale(locale_name: str, base_dir: Path | None = None) -> LocaleConfig:
    root = base_dir if base_dir is not None else Path(__file__).resolve().parent
    path = root / "locales" / f"{locale_name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Файл локали не найден: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Некорректный формат YAML в {path}: ожидается словарь верхнего уровня.")
    try:
        return LocaleConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Ошибка валидации локали {path}: {exc}") from exc


_LOCALE_BASE = BASE_DIR if "BASE_DIR" in globals() else Path(__file__).resolve().parent
_LOCALE_NAME = TARGET_LOCALE if "TARGET_LOCALE" in globals() else "fr_FR"
LOCALE = load_locale(_LOCALE_NAME, _LOCALE_BASE)

_UNI_FRAC = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3", "⅛": "1/8"}
_NUM = r"\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?"

TOKEN_RE = re.compile(
    rf"(?<![\w/.])(?P<n1>{_NUM})(?:\s*(?:-\s*to|-|–|to)\s*(?P<n2>{_NUM}))?"
    rf"(?:\s*x\s*(?P<n3>{_NUM}))?"
    r"\s*-?\s*(?P<unit>fl\.?\s*oz|fluid\s+ounces?|tsp|teaspoons?|tbsp|tablespoons?|"
    r"cups?|oz|ounces?|lbs?|pounds?|quarts?|qt|inch(?:es)?)\b",
    re.IGNORECASE,
)
TEMP_RE = re.compile(
    r"(?<![\w.])(?P<a>\d{2,3})(?:\s*(?:-|–|to)\s*(?P<b>\d{2,3}))?\s*(?:°|º|\s+degrees?)?\s*F\b"
    r"(?:\s*\(\s*\d{2,3}(?:\s*[-–]\s*\d{2,3})?\s*(?:°|º|degrees?)?\s*C\s*\))?"
)
PLACEHOLDER_RE = re.compile(r"\[\[[TQ]\d+\]\]")
_CONTAINERS_RE = re.compile(
    rf"\s*(?:oven[- ]safe\s+)?(?:{'|'.join(LOCALE.containers)})\b"
)
_NUTRITION_RE = re.compile(
    rf"({'|'.join(LOCALE.nutrition_headers)}).*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_OVEN_CTX_RE = re.compile(LOCALE.oven.context_regex) if LOCALE.oven.context_regex else None

OVEN_MAP = LOCALE.oven.entries
CUP_GRAMS = LOCALE.cup_grams


def _to_float(s: str) -> float:
    s = s.strip()
    if " " in s:
        whole, frac = s.split(None, 1)
        return float(whole) + _to_float(frac)
    if "/" in s:
        n, d = s.split("/")
        return float(n) / float(d)
    return float(s)


def _fr(s: str) -> str:
    return s.replace(".", LOCALE.number_format.decimal_sep)


def _fmt_g(g: float) -> str:
    w = LOCALE.units.weight
    if g >= w.kg_from_g:
        kg = round(g / (w.kg_step * 1000)) * w.kg_step
        return _fr(f"{kg:.2f}".rstrip("0").rstrip(".")) + f" {w.kg_suffix}"
    g = round(g / w.g_step) * w.g_step if g >= w.g_step_min else round(g)
    return f"{int(g)} {w.g_suffix}"


def _fmt_vol(ml: float) -> str:
    v = LOCALE.units.volume
    if ml < v.ml_step_below:
        return f"{int(round(ml / v.ml_step) * v.ml_step)} {v.ml_suffix}"
    if v.use_cl and ml < v.cl_below_ml:
        cl = round(ml / 10)
        if cl < 100:
            return f"{cl} {v.cl_suffix}"
    if not v.use_cl and ml < v.l_from_ml:
        return f"{int(round(ml / v.ml_step) * v.ml_step)} {v.ml_suffix}"
    return _fr(f"{round(ml / 1000, 2):g} {v.l_suffix}")


def _fmt_cm(cm: float) -> str:
    ln = LOCALE.units.length
    if cm < ln.mm_below_cm:
        return f"{int(round(cm * 10))} {ln.mm_suffix}"
    v = math.floor(cm * 2 + 0.4) / 2 if cm < ln.half_cm_below else round(cm)
    return _fr(f"{v:g}") + f" {ln.cm_suffix}"


def _rj(vals: list[str]) -> str:
    sep = LOCALE.number_format.range_sep
    if len(vals) == 2:
        a, b = vals[0].rsplit(" ", 1), vals[1].rsplit(" ", 1)
        if len(a) == 2 and len(b) == 2 and a[1] == b[1]:
            return f"{a[0]}{sep}{vals[1]}"
    return sep.join(vals)


def _convert_measure(m: re.Match, tail: str) -> str:
    unit = re.sub(r"\s+", " ", m.group("unit").lower().replace(".", ""))
    n1, n2, n3 = m.group("n1"), m.group("n2"), m.group("n3")
    nums = [n for n in (n1, n2) if n]
    sep = LOCALE.number_format.range_sep

    if unit.startswith(("tsp", "teaspoon")):
        return sep.join(_fr(n) for n in nums) + f" {LOCALE.spoons.tsp}"
    if unit.startswith(("tbsp", "tablespoon")):
        return sep.join(_fr(n) for n in nums) + f" {LOCALE.spoons.tbsp}"
    if unit.startswith(("fl oz", "fluid")):
        return _rj([_fmt_vol(_to_float(n) * 29.57) for n in nums])
    if unit.startswith("cup"):
        grams = next((g for k, g in CUP_GRAMS if k in tail), None)
        if grams:
            return _rj([_fmt_g(_to_float(n) * grams) for n in nums])
        return _rj([_fmt_vol(_to_float(n) * 240) for n in nums])
    if unit.startswith(("quart", "qt")):
        def _qt(n):
            ml = _to_float(n) * 946
            return (
                _fmt_vol(ml)
                if ml < LOCALE.units.volume.l_from_ml
                else _fr(f"{round(ml / 500) / 2:g}") + f" {LOCALE.units.volume.l_suffix}"
            )
        return _rj([_qt(n) for n in nums])
    if unit.startswith(("oz", "ounce")):
        if _CONTAINERS_RE.match(tail):
            return _rj([_fmt_vol(_to_float(n) * 29.57) for n in nums])
        return _rj([_fmt_g(_to_float(n) * 28.35) for n in nums])
    if unit.startswith(("lb", "pound")):
        return _rj([_fmt_g(round(_to_float(n) * 453.59 / 10) * 10) for n in nums])
    if unit.startswith("inch"):
        dims = [n1] + ([n3] if n3 else [])
        cm_suffix = LOCALE.units.length.cm_suffix
        if n2:
            return _rj([_fmt_cm(_to_float(n) * 2.54) for n in nums])
        if n3:
            cms = [_fmt_cm(_to_float(n) * 2.54).replace(f" {cm_suffix}", "") for n in dims]
            return " x ".join(cms) + f" {cm_suffix}"
        return _fmt_cm(_to_float(n1) * 2.54)
    return m.group(0)


def _f_to_c_parts(f: int) -> tuple[int, str | None, int | None, bool]:
    for key, entry in OVEN_MAP.items():
        if abs(f - key) <= LOCALE.oven.tolerance_f:
            c_conv = entry.c - LOCALE.oven.convection_offset if LOCALE.oven.convection_offset is not None else None
            return entry.c, entry.th, c_conv, True
    c_raw = (f - 32) * 5 / 9
    c = round(c_raw / 5) * 5 if c_raw >= 100 else round(c_raw)
    c_conv = c - LOCALE.oven.convection_offset if LOCALE.oven.convection_offset is not None else None
    return c, None, c_conv, False


def _format_oven(c_str: str, th: str | None, c_conv_str: str | None, in_table: bool, is_oven_ctx: bool = True) -> str:
    if is_oven_ctx and in_table and (th is not None or "{th}" not in LOCALE.oven.template):
        return LOCALE.oven.template.format(c=c_str, th=th or "", c_conv=c_conv_str or "")
    return LOCALE.oven.fallback_template.format(c=c_str, th=th or "", c_conv=c_conv_str or "")


def _f_to_c(f: int, is_oven_ctx: bool = True) -> str:
    c, th, c_conv, in_table = _f_to_c_parts(f)
    return _format_oven(str(c), th, str(c_conv) if c_conv is not None else None, in_table, is_oven_ctx)


def _convert_temp(m: re.Match, is_oven_ctx: bool = True) -> str:
    a, b = int(m.group("a")), m.group("b")
    if b:
        sep = LOCALE.number_format.range_sep
        ca, _, ca_conv, in_a = _f_to_c_parts(a)
        cb, th_b, cb_conv, in_b = _f_to_c_parts(int(b))
        c_range = f"{ca}{sep}{cb}"
        c_conv_range = f"{ca_conv}{sep}{cb_conv}" if (ca_conv is not None and cb_conv is not None) else None
        return _format_oven(c_range, th_b, c_conv_range, in_a or in_b, is_oven_ctx)
    return _f_to_c(a, is_oven_ctx)


def _stash(mapping: dict, kind: str, value: str) -> str:
    key = f"[[{kind}{len(mapping) + 1}]]"
    mapping[key] = value
    return key


def protect_units(text: str, mapping: dict) -> str:
    if not text:
        return text
    for k, v in _UNI_FRAC.items():
        text = text.replace(k, f" {v}")
    out = []
    for line in text.splitlines():
        is_oven_ctx = (_OVEN_CTX_RE is None) or bool(_OVEN_CTX_RE.search(line))
        line = TEMP_RE.sub(lambda m, ctx=is_oven_ctx: _stash(mapping, "T", _convert_temp(m, ctx)), line)

        def q(m, line=line):
            tail = re.split(r"[,;(]", line[m.end(): m.end() + 40])[0].lower()
            return _stash(mapping, "Q", _convert_measure(m, tail))

        out.append(TOKEN_RE.sub(q, line))
    return "\n".join(out)


def restore_units(text: str, mapping: dict) -> str:
    text = PLACEHOLDER_RE.sub(lambda m: mapping[m.group(0)], text)
    return re.sub(r"[ \t]+", " ", text).strip()


def strip_nutrition_block(text: str) -> str:
    return _NUTRITION_RE.sub("", text).strip()


_EN_INSTR = {"the", "and", "with", "until", "add", "into", "for", "stir", "bake", "over", "then", "about",
             "should", "from", "your"}
_EN_INGR = {"fresh", "chopped", "softened", "ground", "whole", "dried", "melted", "unsweetened", "sliced", "diced",
            "minced", "grated", "shredded", "cream", "butter", "cheese", "milk", "flour", "salt", "pepper", "juice",
            "eggs", "egg", "powder", "sweetener", "optional"}


def _en_hits(text: str, vocab: set) -> int:
    return sum(1 for w in re.findall(r"[a-zA-Z]+", text.lower()) if w in vocab)


_WORDNUM = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
            "seven": "7", "eight": "8", "nine": "9", "ten": "10", "twelve": "12"}
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?(?:/\d+)?")
_LEFTOVER_RE = re.compile(
    r"\b(?:cups?|tsp|tbsp|tablespoons?|teaspoons?|oz|ounces?|lbs?|pounds?|inch(?:es)?|"
    r"quarts?|pints?|gallons?|fahrenheit)\b|°\s*F\b|\[\[|\]\]",
    re.IGNORECASE,
)


def _numbers(text: str, words: bool = False) -> Counter:
    text = PLACEHOLDER_RE.sub(" ", text)
    nums = [n.replace(",", ".") for n in _NUM_RE.findall(text)]
    if words:
        nums += [_WORDNUM[w] for w in re.findall(r"[a-z]+", text.lower()) if w in _WORDNUM]
    return Counter(nums)


def final_check(text: str) -> list[str]:
    return sorted({m.group(0) for m in _LEFTOVER_RE.finditer(text)})


def validate_translation(item, prot: dict) -> list[str]:
    problems = []
    for field, src, out in (("ingredients", prot["ingredients"], item.ingredients_list),
                            ("instructions", prot["instructions"], item.cooking_instructions)):
        if Counter(PLACEHOLDER_RE.findall(src)) != Counter(PLACEHOLDER_RE.findall(out)):
            problems.append(f"{field}: плейсхолдеры потеряны/задвоены/изменены")
    if item.ingredients_list.strip() == prot["ingredients"].strip():
        problems.append("ingredients: не переведены")
    n_lines = max(1, len(item.ingredients_list.splitlines()))
    if _en_hits(item.ingredients_list, _EN_INGR) >= max(2, n_lines // 3):
        problems.append("ingredients: остались английские слова")
    if _en_hits(item.cooking_instructions, _EN_INSTR) >= 3:
        problems.append("instructions: остались английские слова")
    for field, src, out in (("ingredients", prot["ingredients"], item.ingredients_list),
                            ("instructions", prot["instructions"], item.cooking_instructions)):
        extra = _numbers(out) - _numbers(src, words=True)
        if extra:
            problems.append(f"{field}: числа, которых нет в исходнике: {sorted(extra)}")
    if len(item.ingredients_list.splitlines()) != len(prot["ingredients"].splitlines()):
        problems.append("ingredients: число строк не совпадает с исходником")
    return problems


# ================= СХЕМЫ ДАННЫХ =================
class RewrittenRecipe(BaseModel):
    rewrite_row_id: int = Field(ge=1)
    dish_name: str = Field(default="", description="Dish name")
    servings: int = Field(default=1, ge=1)
    ingredients_list: str = Field(min_length=1)
    cooking_instructions: str = Field(min_length=1)


class BatchRewriteResponse(BaseModel):
    results: list[RewrittenRecipe]


# ================= ТРЕКЕР РАСХОДОВ API =================
class SessionCostTracker:
    """Считает расход по usage_metadata и пишет КАЖДЫЙ вызов в api_usage.csv для сверки с биллингом Google.
    Платными считаются все токены, которых нет в prompt_token_count: output = total - prompt
    (в него входят кандидаты, мысли, tool-use), поэтому ничего не теряется, даже если какое-то поле не заполнено."""

    USAGE_LOG = BASE_DIR / "api_usage.csv"
    FIELDS = ["time", "model", "prompt", "cached", "candidates", "thoughts", "tool_use", "total",
              "billed_output", "cost_usd", "price_in", "price_out", "price_known"]

    def __init__(self):
        self.requests = 0
        self.failed_calls = 0
        self.rejected_items = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.thought_tokens = 0
        self.total_cost_usd = 0.0
        self._warned_unknown = False

    def add_usage(self, usage_metadata, model_name: str) -> float:
        if not usage_metadata:
            return 0.0
        g = lambda name: getattr(usage_metadata, name, 0) or 0  # noqa: E731
        prompt, cached = g("prompt_token_count"), g("cached_content_token_count")
        cand, thoughts = g("candidates_token_count"), g("thoughts_token_count")
        tool, total = g("tool_use_prompt_token_count"), g("total_token_count")
        known = model_name in MODEL_PRICING_USD_PER_MILLION
        if not known and not self._warned_unknown:
            LOGGER.warning(f"Модели '{model_name}' нет в MODEL_PRICING_USD_PER_MILLION — считаю по ценам по умолчанию "
                           f"(0.75/3.75). Расход может не совпадать с биллингом: добавьте цены модели в таблицу.")
            self._warned_unknown = True
        input_price, output_price = MODEL_PRICING_USD_PER_MILLION.get(model_name, (0.75, 3.75))
        billed_output = max(total - prompt, cand + thoughts + tool)
        cost = (prompt * input_price + billed_output * output_price) / 1_000_000
        if total and total != prompt + cand + thoughts + tool:
            LOGGER.warning(f"usage: total={total} != prompt+candidates+thoughts+tool={prompt + cand + thoughts + tool}; "
                           f"считаю output как total-prompt={total - prompt}")
        self.requests += 1
        self.input_tokens += prompt
        self.output_tokens += billed_output
        self.thought_tokens += thoughts
        self.total_cost_usd += cost
        try:
            new_file = not self.USAGE_LOG.exists()
            with self.USAGE_LOG.open("a", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh, delimiter=";")
                if new_file:
                    w.writerow(self.FIELDS)
                w.writerow([datetime.now().isoformat(timespec="seconds"), model_name, prompt, cached, cand, thoughts,
                            tool, total, billed_output, f"{cost:.6f}", input_price, output_price, int(known)])
        except OSError:
            pass
        return cost


SESSION_TRACKER = SessionCostTracker()


# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def clean_old_cache(cache_dir: Path, days: int) -> None:
    now = time.time()
    deleted_count = 0
    for f in cache_dir.glob("*.json"):
        if f.is_file() and now - f.stat().st_mtime > days * 86400:
            try:
                f.unlink()
                deleted_count += 1
            except OSError:
                pass
    if deleted_count > 0:
        LOGGER.info(f"Очищено {deleted_count} устаревших файлов кэша.")


def init_csv_log(filepath: Path) -> None:
    mismatch = False
    if filepath.exists():
        with filepath.open(mode="r", encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter=";")
            try:
                first_row = next(reader)
                if first_row != EXPECTED_CSV_HEADER:
                    mismatch = True
            except StopIteration:
                mismatch = True
        if mismatch:
            bak_path = filepath.parent / f"{filepath.name}.bak_{int(time.time())}"
            filepath.rename(bak_path)
            LOGGER.warning(f"Заголовки CSV не совпадают. Старый файл сохранён как {bak_path.name}")
        else:
            return
    with filepath.open(mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(EXPECTED_CSV_HEADER)


def append_csv_batch(filepath: Path, batch_data: list[tuple[dict, RewrittenRecipe | None, int | None, str]]) -> None:
    if not batch_data:
        return
    rows_to_write = []
    for previous, rewritten, rewrite_id_out, status_note in batch_data:
        rows_to_write.append([
            previous.get("rewrite_row_id") if previous.get("rewrite_row_id") is not None else "",
            previous["recipe_id"],
            previous.get("dish_name", ""),
            rewritten.dish_name if rewritten else "",
            previous.get("serves", ""),
            rewritten.servings if rewritten else "",
            previous.get("prep_time_minutes", ""), previous.get("cook_time_minutes", ""),
            previous.get("ingredients_list", ""),
            rewritten.ingredients_list if rewritten else "",
            previous.get("cooking_instructions", ""),
            rewritten.cooking_instructions if rewritten else "",
            previous["recipe_id"],
            rewrite_id_out if rewrite_id_out is not None else "",
            status_note,
        ])
    with filepath.open(mode="a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerows(rows_to_write)


def append_ids_file(filepath: Path, row_ids: Sequence[int]) -> int:
    if not row_ids:
        return 0
    filepath.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(str(int(rid)) for rid in row_ids) + "\n"
    with filepath.open(mode="a", encoding="utf-8", newline="\n") as f:
        f.write(payload)
    return len(row_ids)


def parse_rewrite_ids(raw: str) -> list[int]:
    if not raw:
        return []
    result, seen = [], set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            val = int(token)
            if val > 0 and val not in seen:
                seen.add(val)
                result.append(val)
        else:
            LOGGER.warning(f"Пропущен некорректный ID: {token!r}")
    return result


def generate_run_hash(prompt_text: str, model: str, target_servings: int) -> str:
    raw = (
        f"{prompt_text}|{model}|{target_servings}|{APP_MODE}|{TARGET_LOCALE}|{THINKING_LEVEL}|"
        f"{UNITS_VERSION}|{SCALE_VERSION}|{CHECKS_VERSION}"
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def row_fingerprint(row: dict[str, Any]) -> str:
    raw = (
        f"{row.get('dish_name', '')}|{row.get('ingredients_list', '')}|"
        f"{row.get('cooking_instructions', '')}|{row.get('serves', '')}"
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def get_db_connection() -> mariadb.Connection:
    return mariadb.connect(
        host=os.getenv("DB_HOST", "localhost"), user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""), database=os.getenv("DB_DATABASE", "seohelp_recipes"),
        port=int(os.getenv("DB_PORT", "3306")), autocommit=False,
    )


def fetch_rewrite_rows(connection: mariadb.Connection, rewrite_ids: Sequence[int]) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in rewrite_ids)
    sql = f"""
        SELECT id AS rewrite_row_id, recipe_id, variant_type, dish_name_variant AS dish_name,
               ingredients_list_variant AS ingredients_list, cooking_instructions_variant AS cooking_instructions,
               serves, prep_time_minutes, cook_time_minutes
        FROM recipe_rewrite WHERE id IN ({placeholders}) ORDER BY id ASC
    """
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, tuple(rewrite_ids))
        return cursor.fetchall()
    finally:
        cursor.close()


def normalize_servings(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not float(value).is_integer():
            return None
        ivalue = int(value)
        return ivalue if ivalue > 0 else None
    try:
        num = float(str(value).strip())
    except (ValueError, TypeError):
        return None
    if not num.is_integer():
        return None
    ivalue = int(num)
    return ivalue if ivalue > 0 else None


def validate_adapt_config(
    status: str,
    limit: int,
    force: bool = False,
    adapt_ids: Sequence[int] = (),
) -> None:
    if status not in VALID_ADAPT_STATUSES:
        raise ValueError(
            f"Недопустимое значение ADAPT_STATUS={status!r}. Допустимо: {', '.join(VALID_ADAPT_STATUSES)}"
        )
    if limit <= 0:
        raise ValueError(f"ADAPT_LIMIT должен быть > 0, получено: {limit}")
    if force and not adapt_ids:
        raise ValueError("ADAPT_FORCE=1 требует явный ADAPT_IDS")


def parse_dish_types(raw: str) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def build_adapt_select(
    market: str,
    locale: str,
    limit: int,
    recipe_ids: Sequence[int] = (),
    dish_types: Sequence[str] = (),
    force: bool = False,
    exclude_ids: Sequence[int] = (),
) -> tuple[str, tuple[Any, ...]]:
    where_parts: list[str] = ["r.serves IS NOT NULL AND r.serves > 0"]
    params: list[Any] = [market, locale]
    if not force:
        where_parts.append("w.id IS NULL")
    if recipe_ids:
        placeholders = ", ".join("?" for _ in recipe_ids)
        where_parts.append(f"r.id IN ({placeholders})")
        params.extend(recipe_ids)
    if exclude_ids:
        placeholders = ", ".join("?" for _ in exclude_ids)
        where_parts.append(f"r.id NOT IN ({placeholders})")
        params.extend(exclude_ids)
    if dish_types:
        placeholders = ", ".join("?" for _ in dish_types)
        where_parts.append(f"r.dish_type IN ({placeholders})")
        params.extend(dish_types)
    where_clause = f"WHERE {' AND '.join(where_parts)} "
    sql = (
        "SELECT r.id AS recipe_id, r.dish_name, r.ingredients_list, r.cooking_instructions, "
        "r.serves, r.prep_time_minutes, r.cook_time_minutes, r.dish_type "
        "FROM recipes3 r "
        "LEFT JOIN recipe_rewrite w ON w.recipe_id = r.id AND w.market = ? AND w.locale = ? "
        "AND w.variant_type = 'rewrite' "
        f"{where_clause}"
        "ORDER BY r.id LIMIT ?"
    )
    params.append(limit)
    return sql, tuple(params)


def fetch_adapt_rows(
    connection: mariadb.Connection,
    market: str,
    locale: str,
    limit: int,
    recipe_ids: Sequence[int] = (),
    dish_types: Sequence[str] = (),
    force: bool = False,
    exclude_ids: Sequence[int] = (),
) -> list[dict[str, Any]]:
    sql, params = build_adapt_select(
        market, locale, limit, recipe_ids, dish_types, force, exclude_ids=exclude_ids,
    )
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()


def build_adapt_insert_sql(force: bool = False) -> str:
    base = """
        INSERT INTO recipe_rewrite (
            recipe_id, market, locale, variant_type, status,
            dish_name_variant, ingredients_list_variant, cooking_instructions_variant,
            instructions_hash, serves, dish_cost_serves, needs_reprice,
            prep_time_minutes, cook_time_minutes, currency, ai_model
        ) VALUES (?, ?, ?, 'rewrite', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'USD', ?)
    """
    if not force:
        return base.strip()
    return (
        base.strip()
        + """
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id),
            status = VALUES(status),
            dish_name_variant = VALUES(dish_name_variant),
            ingredients_list_variant = VALUES(ingredients_list_variant),
            cooking_instructions_variant = VALUES(cooking_instructions_variant),
            instructions_hash = VALUES(instructions_hash),
            serves = VALUES(serves),
            dish_cost_serves = VALUES(dish_cost_serves),
            needs_reprice = 1,
            calories_per_person = NULL,
            proteins_per_person = NULL,
            fats_per_person = NULL,
            carbohydrates_per_person = NULL,
            netcarbs_per_person = NULL,
            dish_cost_local = NULL,
            nutrients_recalc_version = NULL,
            prep_time_minutes = VALUES(prep_time_minutes),
            cook_time_minutes = VALUES(cook_time_minutes),
            currency = VALUES(currency),
            ai_model = VALUES(ai_model),
            updated_at = NOW()
        """.rstrip()
    )


def save_adapted_recipe(
    connection: mariadb.Connection,
    source_row: dict[str, Any],
    data: RewrittenRecipe,
    ai_model: str,
    market: str,
    locale: str,
    status: str,
    force: bool = False,
) -> tuple[int | None, str]:
    instructions_hash = hashlib.sha256(data.cooking_instructions.encode("utf-8")).hexdigest()
    sql = build_adapt_insert_sql(force=force)
    params = (
        int(source_row["recipe_id"]),
        market,
        locale,
        status,
        source_row.get("dish_name") or "",
        data.ingredients_list,
        data.cooking_instructions,
        instructions_hash,
        data.servings,
        data.servings,
        source_row.get("prep_time_minutes"),
        source_row.get("cook_time_minutes"),
        ai_model,
    )
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        return getattr(cursor, "lastrowid", None), "OK"
    except mariadb.IntegrityError as exc:
        if not force:
            LOGGER.warning(f"ID {source_row['recipe_id']}: exists, skipped ({exc})")
            return None, "exists, skipped"
        raise
    finally:
        cursor.close()


def build_rewrite_update_sql() -> str:
    return """
        UPDATE recipe_rewrite SET ingredients_list_variant = ?, cooking_instructions_variant = ?,
        instructions_hash = ?, serves = ?, dish_cost_serves = ?, needs_reprice = 1,
        ai_model = ?, updated_at = NOW()
        WHERE id = ?
    """.strip()


def build_translate_insert_sql() -> str:
    return """
        INSERT INTO recipe_rewrite (
            recipe_id, market, locale, variant_type, dish_name_variant, ingredients_list_variant,
            cooking_instructions_variant, instructions_hash, serves, dish_cost_serves, needs_reprice,
            prep_time_minutes, cook_time_minutes, currency,
            calories_per_person, proteins_per_person, fats_per_person,
            carbohydrates_per_person, netcarbs_per_person,
            ai_model, created_at, updated_at
        )
        SELECT
            recipe_id, ?, ?, variant_type, ?, ?, ?, ?,
            serves, serves, 1, prep_time_minutes, cook_time_minutes, ?,
            calories_per_person, proteins_per_person, fats_per_person,
            carbohydrates_per_person, netcarbs_per_person,
            ?, NOW(), NOW()
        FROM recipe_rewrite
        WHERE id = ?
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id),
            dish_name_variant = VALUES(dish_name_variant),
            ingredients_list_variant = VALUES(ingredients_list_variant),
            cooking_instructions_variant = VALUES(cooking_instructions_variant),
            instructions_hash = VALUES(instructions_hash),
            dish_cost_serves = VALUES(dish_cost_serves),
            needs_reprice = 1,
            currency = VALUES(currency),
            ai_model = VALUES(ai_model),
            updated_at = NOW()
    """.strip()


def save_rewritten_recipe(connection: mariadb.Connection, source_row_id: int, data: RewrittenRecipe,
                          ai_model: str) -> int | None:
    instructions_hash = hashlib.sha256(data.cooking_instructions.encode("utf-8")).hexdigest()
    cursor = connection.cursor()
    try:
        if APP_MODE == "rewrite":
            cursor.execute(build_rewrite_update_sql(), (
                data.ingredients_list, data.cooking_instructions, instructions_hash, data.servings,
                data.servings, ai_model, source_row_id,
            ))
            return source_row_id

        elif APP_MODE == "translate":
            cursor.execute(build_translate_insert_sql(), (
                TARGET_MARKET, TARGET_LOCALE, data.dish_name, data.ingredients_list, data.cooking_instructions,
                instructions_hash, TARGET_CURRENCY,
                ai_model, source_row_id,
            ))
            return getattr(cursor, "lastrowid", None)
        return None
    finally:
        cursor.close()


def target_servings_for(src_srv: int) -> int:
    """Итоговое число порций: исходное, если масштабирование не нужно; иначе цель (при большом делителе удваиваем)."""
    if RESCALE_TARGET_SERVINGS <= 0 or src_srv <= RESCALE_ONLY_IF_MORE_THAN:
        return src_srv
    target = RESCALE_TARGET_SERVINGS
    while src_srv / target > RESCALE_MAX_DIVISOR:
        target *= 2
    return min(target, src_srv)


_COUNT_UNIT_RE = re.compile(
    r"^(?:fl\.?\s*oz|fluid\s+ounces?|tsps?|teaspoons?|tbsps?|tablespoons?|cups?|c\.|"
    r"oz|ounces?|lbs?|pounds?|quarts?|qt|pints?|pt|sticks?|g|grams?|kg|ml|l)\b",
    re.IGNORECASE,
)
_COUNT_NUM_RE = re.compile(r"^\s*(?P<n>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\b")
_COUNT_RANGE_AFTER_RE = re.compile(r"^\s*(?:-|–|to)\s*\d", re.IGNORECASE)


def _countable_egg_amounts(ingredients: str) -> list[float]:
    amounts: list[float] = []
    for raw_line in (ingredients or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for k, v in _UNI_FRAC.items():
            line = line.replace(k, f" {v}")
        m = _COUNT_NUM_RE.match(line)
        if not m:
            continue
        rest = line[m.end():]
        if _COUNT_RANGE_AFTER_RE.match(rest):
            continue
        if _COUNT_UNIT_RE.match(rest.lstrip()):
            continue
        if RESCALE_COUNT_RE.search(rest):
            amounts.append(_to_float(m.group("n")))
    return amounts


def _egg_penalty(src_srv: int, cand_srv: int, amounts: Sequence[float]) -> int:
    factor = cand_srv / src_srv
    return sum(1 for amt in amounts if abs(amt * factor - round(amt * factor)) > 1e-6)


def choose_target_servings(src_srv: int, ingredients: str) -> int:
    """Выбор целевого числа порций: избегает дробных счётных яиц, если есть допустимый кандидат лучше base."""
    if not RESCALE_SMART:
        return target_servings_for(src_srv)
    if RESCALE_TARGET_SERVINGS <= 0 or src_srv <= RESCALE_ONLY_IF_MORE_THAN:
        return src_srv
    base = target_servings_for(src_srv)
    amounts = _countable_egg_amounts(ingredients)
    if not amounts:
        return base
    base_pen = _egg_penalty(src_srv, base, amounts)
    if base_pen == 0:
        return base
    candidates = [
        c for c in RESCALE_CANDIDATES
        if RESCALE_TARGET_SERVINGS <= c < src_srv and (src_srv / c) <= RESCALE_MAX_DIVISOR
    ]
    if not candidates:
        return base
    best_pen, _, best_c = min((_egg_penalty(src_srv, c, amounts), abs(c - base), c) for c in candidates)
    if best_pen > 0 and best_pen >= base_pen:
        return base
    return best_c


_BRAND_RE = re.compile(r"\b(?:wholesome yum|besti|paleovalley)\b", re.IGNORECASE)
_NAME_QTY_RE = re.compile(
    r"^\s*(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?|[½¼¾⅓⅔])?\s*(?:-|to)?\s*(?:\d+(?:\.\d+)?)?\s*"
    r"(?:tsp|tbsp|cups?|oz|lbs?|fl\s*oz|large|medium|small|cloves?|slices?|pinch|cans?)?\b\s*(?:of\s+)?",
    re.IGNORECASE)
MULTI_VARIANT_MIN_DUPS = int(os.getenv("MULTI_VARIANT_MIN_DUPS", "2"))  # после исключения общих позиций (соль, ваниль, масло...)
SKIP_MULTI_VARIANT = os.getenv("SKIP_MULTI_VARIANT", "1") == "1"
# Источник, в котором у большинства строк ингредиентов нет количества (битый парсинг) — не переписываем.
SKIP_NO_QUANTITIES = os.getenv("SKIP_NO_QUANTITIES", "1") == "1"
NO_QTY_RATIO = float(os.getenv("NO_QTY_RATIO", "0.7"))
NO_QTY_MIN_LINES = int(os.getenv("NO_QTY_MIN_LINES", "4"))


_QTY_WORDS = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|twelve|half|a|an|few|pinch(?:es)?|dash(?:es)?|handful|splash)"
_HAS_QTY_RE = re.compile(
    rf"^(?:(?:about|approx\.?|approximately|around|~|≈)\s*)?\(?(?:\d|[½¼¾⅓⅔⅛]|{_QTY_WORDS}\b)", re.IGNORECASE)


def _plain_ingredient_line(line: str) -> str:
    """Снимает HTML-теги/сущности, маркеры списка (▢ ☐ • - * 1.) и NBSP, чтобы увидеть первый символ строки."""
    line = re.sub(r"<[^>]+>", " ", line or "")
    line = re.sub(r"&(?:nbsp|#160);", " ", line).replace("\u00a0", " ")
    line = re.sub(r"^[\s▢☐□■•·\-\*–—✓✔]+", "", line)
    return line.strip()


def no_quantity_ratio(ingredients: str) -> tuple[float, int]:
    """Доля строк ингредиентов без ведущего количества (заголовки вида 'Crust:' не считаются)."""
    lines = [_plain_ingredient_line(l) for l in (ingredients or "").splitlines()]
    lines = [l for l in lines if l and not l.endswith(":")]
    if not lines:
        return 0.0, 0
    nq = [l for l in lines if not _HAS_QTY_RE.match(l)]
    return len(nq) / len(lines), len(lines)


_COMMON_DUP_NAMES = re.compile(
    r"\b(?:salt|pepper|cinnamon|vanilla|extract|sweetener|erythritol|allulose|monk fruit|stevia|water|oil|butter|ghee|"
    r"baking powder|baking soda|xanthan|lemon juice|lime juice|garlic|onion|parsley|cilantro|chocolate chips?)\b", re.IGNORECASE)


def multi_variant_dups(ingredients: str) -> dict[str, int]:
    """Имена ингредиентов, встречающиеся в списке 2+ раз. Если таких >= MULTI_VARIANT_MIN_DUPS, источник —
    скорее всего несколько вариантов рецепта в одном списке (версия на миндальной/кокосовой муке, 5 вкусов)."""
    names = []
    for line in (ingredients or "").splitlines():
        l = re.split(r"[(,]", re.sub(r"\(.*?\)", "", line))[0]
        l = _NAME_QTY_RE.sub("", _BRAND_RE.sub("", l))
        l = re.sub(r"\b(?:large|medium|small|shredded|grated|fresh|dried|unsalted|melted|softened|ground|powdered)\b",
                   "", l, flags=re.IGNORECASE)
        l = re.sub(r"\b(mozzarella|cheddar|parmesan|feta|swiss|gruyere|provolone|colby|jack)\s+cheese\b", r"\1", l, flags=re.I)
        l = re.sub(r"\s+", " ", l).strip().lower()
        if len(l) > 2:
            names.append(l)
    cnt: dict[str, int] = {}
    for n in names:
        cnt[n] = cnt.get(n, 0) + 1
    # Соль, ваниль, подсластитель, масло и т. п. повторяются в любом рецепте из нескольких слоёв — это не варианты.
    return {k: v for k, v in cnt.items() if v > 1 and not _COMMON_DUP_NAMES.search(k)}


def build_sent_ingredients(src_row: dict[str, Any]) -> str:
    """Реконструирует ингредиенты в том виде, в котором они ушли в ИИ:
    merge_broken_lines -> strip_recipe_refs -> (rescale). Нужно для fidelity_check,
    чтобы API-результаты и кэш-хиты проверялись одинаково."""
    ing = merge_broken_lines(src_row.get("ingredients_list") or "")
    ing = strip_recipe_refs(ing)
    if APP_MODE not in ("rewrite", "adapt"):
        return ing
    src_srv = normalize_servings(src_row.get("serves")) or 1
    factor = choose_target_servings(src_srv, ing) / src_srv
    if abs(factor - 1.0) > 1e-5:
        ing, _, _ = scale_recipe(ing, src_row.get("cooking_instructions") or "", factor)
    return ing


AUTO_FIX_PREP = os.getenv("AUTO_FIX_PREP", "1") == "1"
_PREP_VERBS = {"drain": "drain", "squeez": "squeeze", "peel": "peel", "seed": "seed", "husk": "husk",
               "trim": "trim", "thaw": "thaw", "mince": "mince"}
_NOUN_SKIP = re.compile(r"^(?:(?:large|medium|small|whole|fresh|frozen|raw|ripe)\s+)+", re.IGNORECASE)
_NOUN_CONT = re.compile(r"^(?:cloves?|heads?|bunch(?:es)?|cans?|pieces?|slices?|packages?|pkgs?|packs?|bags?|boxes|box|blocks?|jars?|bottles?|containers?)\s+(?:of\s+)?", re.IGNORECASE)


def _ingredient_noun(line: str) -> str:
    """'1 medium cucumber, peeled' -> 'cucumber'; '2 cloves garlic, minced' -> 'garlic'."""
    t = re.sub(r"\(.*?\)", "", line).split(",")[0].strip()
    t = re.sub(r"^\s*(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)(?:\s*[-–]\s*\d+)?\s*"
               r"(?:tsps?|tbsps?|cups?|oz|lbs?)?\s*", "", t, flags=re.IGNORECASE)
    t = _NOUN_SKIP.sub("", t)
    t = _NOUN_CONT.sub("", t)
    t = _NOUN_SKIP.sub("", t)
    return t.strip().lower()


def auto_fix_prep(ingredients: str, instructions: str, labels: list[str]) -> tuple[str, list[str]]:
    """Если модель опустила шаг подготовки (peel/seed/drain/mince...), добавляет первый абзац
    'Before you begin, peel and seed the cucumber; drain the olives.' вместо повторного платного запроса."""
    import checks as _c
    verbs_by_noun: dict[str, list[str]] = {}
    for lab in labels:
        pat = _c._PREP_STEMS.get(lab) or _c._FORM_STEMS.get(lab)
        if not pat:
            continue
        for line in ingredients.splitlines():
            if re.search(pat, line.lower()):
                noun = _ingredient_noun(line)
                verb = _PREP_VERBS.get(lab, lab)
                if noun and verb not in verbs_by_noun.setdefault(noun, []):
                    verbs_by_noun[noun].append(verb)
    if not verbs_by_noun:
        return instructions, []
    parts = []
    for noun, verbs in verbs_by_noun.items():
        v = verbs[0] if len(verbs) == 1 else ", ".join(verbs[:-1]) + " and " + verbs[-1]
        parts.append(f"{v} the {noun}")
    sentence = "Before you begin, " + "; ".join(parts) + "."
    return sentence + "\n\n" + instructions, list(verbs_by_noun)


_DEC_RE = re.compile(r"\b(\d+\.\d+)\s*(oz|lbs?|cups?|tbsp|tsp)\b", re.IGNORECASE)
_METRIC_LEFT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(g|grams?|kg|ml|liters?|litres?)\b", re.IGNORECASE)


def fix_decimal_measures(text: str) -> tuple[str, list[str]]:
    """'7.1 oz' -> '7 oz', '2.2 lb' -> '2 1/4 lb', '13.5 oz' -> '13 1/2 oz' (одинаково в списке и методе)."""
    notes: list[str] = []

    def sub(m: re.Match) -> str:
        v, kind = Fraction(m.group(1)).limit_denominator(1000), _kind(m.group(2))
        if kind == "oz" and 2 <= v < 16:  # 13.5 oz остаётся 13 1/2 oz (размер банки), а не 14
            new = f"{_sfmt(_snap(v, _HALVES))} oz"
        else:
            new = _render(v, kind, False)
        notes.append(f"{m.group(0)} -> {new}")
        return new

    return _DEC_RE.sub(sub, text), notes


def fix_metric_leftovers(text: str) -> tuple[str, list[str]]:
    """Страховка: '250 g each', '200 ml' в результате -> US-мера (если метрика всё-таки просочилась)."""
    notes: list[str] = []

    def sub(m: re.Match) -> str:
        new = _metric_to_us(float(m.group(1)), m.group(2))
        if not new:
            return m.group(0)
        notes.append(f"{m.group(0)} -> {new}")
        return new

    return _METRIC_LEFT_RE.sub(sub, text), notes


def fix_poultry_word(ingredients: str, instructions: str) -> tuple[str, list[str]]:
    """«the poultry» -> «the chicken/turkey/duck», если в списке ингредиентов одна конкретная птица."""
    ing = ingredients.lower()
    if re.search(r"\bpoultry\b", ing) or not re.search(r"\bpoultry\b", instructions, re.I):
        return instructions, []
    kinds = [k for k in ("chicken", "turkey", "duck") if re.search(rf"\b{k}\b", ing)]
    if len(kinds) != 1:
        return instructions, []
    def _sub(m: re.Match) -> str:
        return kinds[0].capitalize() if m.group(0)[0].isupper() else kinds[0]
    return re.sub(r"\bpoultry\b", _sub, instructions, flags=re.I), [f"poultry -> {kinds[0]}"]


def validate_and_fix_rewrite(item: RewrittenRecipe, src_row: dict[str, Any]) -> list[str]:
    """Единая точка валидации и автофиксов для рерайта. Мутирует item in-place.
    Возвращает список предупреждений (для лога). Применяется и к API, и к кэшу
    (только в режиме APP_MODE == 'rewrite')."""
    warns: list[str] = []

    # 1. Порции
    src_srv = normalize_servings(src_row.get("serves")) or 1
    ing_for_srv = strip_recipe_refs(merge_broken_lines(src_row.get("ingredients_list") or ""))
    expected_srv = choose_target_servings(src_srv, ing_for_srv)
    if item.servings != expected_srv:
        warns.append(f"servings: AI={item.servings}, ожидалось {expected_srv} (принудительно)")
        item.servings = expected_srv

    # 2. Нутриенты
    item.ingredients_list = strip_nutrition_block(item.ingredients_list)
    item.cooking_instructions = strip_nutrition_block(item.cooking_instructions)

    # 3. Ссылки на чужой рецепт в ингредиентах (защитно; обычно ИИ уже не должен их оставить)
    stripped_ing = strip_recipe_refs(item.ingredients_list)
    if stripped_ing != item.ingredients_list:
        warns.append("в ингредиентах осталась ссылка на чужой рецепт (заменена на 'prepared ...')")
        item.ingredients_list = stripped_ing

    # 4. Fidelity (количества) — сравнение с тем, что ушло в ИИ (после merge+strip+rescale)
    sent_ing = build_sent_ingredients(src_row)
    fid = fidelity_check(sent_ing, item.ingredients_list)
    if fid:
        warns.append(f"fidelity: {fid}")

    # 5. Безопасные температуры
    item.cooking_instructions, temp_notes = fix_safety_temps(
        item.ingredients_list, item.cooking_instructions,
    )
    if temp_notes:
        warns.append(f"температура исправлена: {temp_notes}")

    for _field in ("ingredients_list", "cooking_instructions"):
        _txt, _nb = neutralize_brands(getattr(item, _field))
        if _nb:
            warns.append(f"бренды заменены ({_field}): {_nb}")
        _txt, _n1 = fix_metric_leftovers(_txt)
        _txt, _n2 = fix_decimal_measures(_txt)
        setattr(item, _field, _txt)
        if _n1 or _n2:
            warns.append(f"единицы приведены к US ({_field}): {(_n1 + _n2)[:4]}")
    item.cooking_instructions, pw = fix_poultry_word(item.ingredients_list, item.cooking_instructions)
    if pw:
        warns.append(f"исправлено слово: {pw}")

    # 6. Шаги подготовки из ингредиентов
    miss = prep_check(item.ingredients_list, item.cooking_instructions)
    if miss:
        warns.append(f"в методе нет шагов подготовки: {miss}")

    # 7. Ссылки на чужой рецепт (общий случай: «see my post», «according to recipe»)
    refs = recipe_ref_check(item.ingredients_list, item.cooking_instructions)
    if refs:
        warns.append(f"ссылка на чужой рецепт: {refs}")

    # 8. Форма нарезки из ингредиентов (мягкое предупреждение, только лог)
    fm = form_check(item.ingredients_list, item.cooking_instructions)
    if fm:
        warns.append(f"в методе нет формы нарезки из списка: {fm}")

    # 9. Стиль: poultry→chicken, «using X total», выдуманный microwave (только лог)
    st = style_check(
        item.ingredients_list,
        item.cooking_instructions,
        src_row.get("cooking_instructions") or "",
    )
    if st:
        warns.append(f"стиль: {st}")

    return warns


def build_batch_prompt(rows: Sequence[dict[str, Any]]) -> str:
    prompt_lines = []
    if APP_MODE == "translate":
        prompt_lines.append(f"Target Language for this batch: {TARGET_LANG.upper()}")

    prompt_lines.append("")

    for row in rows:
        src_servings = normalize_servings(row.get("serves")) or 1
        prompt_id = row["rewrite_row_id"] if row.get("rewrite_row_id") is not None else row["recipe_id"]
        prompt_lines.append(f"--- REWRITE ROW ID {prompt_id} ---")
        prompt_lines.append(f"Current Servings: {src_servings}")
        if row.get("scale_note"):
            prompt_lines.append(f"System note: {row['scale_note']}")
        prompt_lines.append(f"Current Name: {row.get('dish_name', '')}")
        prompt_lines.append(f"Current Ingredients:\n{row.get('ingredients_list', '')}")
        prompt_lines.append(f"Current Instructions:\n{row.get('cooking_instructions', '')}\n")

    return "\n".join(prompt_lines)


# ================= ОСНОВНОЙ ЦИКЛ =================
def validate_locale_env_sync() -> None:
    if APP_MODE != "translate":
        return
    mismatches = []
    if TARGET_LANG != LOCALE.meta.lang.lower():
        mismatches.append(f"TARGET_LANG={TARGET_LANG!r} != locale.meta.lang={LOCALE.meta.lang!r}")
    if TARGET_MARKET != LOCALE.meta.market:
        mismatches.append(f"TARGET_MARKET={TARGET_MARKET!r} != locale.meta.market={LOCALE.meta.market!r}")
    if TARGET_CURRENCY != LOCALE.meta.currency:
        mismatches.append(f"TARGET_CURRENCY={TARGET_CURRENCY!r} != locale.meta.currency={LOCALE.meta.currency!r}")
    if mismatches:
        raise ValueError(
            f"Рассинхрон настроек с локалью {TARGET_LOCALE!r}: " + "; ".join(mismatches)
        )


def format_adapt_exclude_line(
    manual_ids: Sequence[int],
    failed_ids: Sequence[int],
    invalid_servings_ids: Sequence[int],
    skipped_exists_ids: Sequence[int],
    missing_qty_ids: Sequence[int] = (),
    existing_exclude_ids: Sequence[int] = (),
) -> str | None:
    all_ids = sorted(
        set(manual_ids)
        | set(failed_ids)
        | set(invalid_servings_ids)
        | set(skipped_exists_ids)
        | set(missing_qty_ids)
        | set(existing_exclude_ids)
    )
    if not all_ids:
        return None
    return f"ADAPT_EXCLUDE_IDS={','.join(map(str, all_ids))}"


def main() -> int:
    validate_locale_env_sync()
    is_rewrite_pipeline = APP_MODE in ("rewrite", "adapt")
    if APP_MODE == "adapt":
        adapt_ids_list = parse_rewrite_ids(ADAPT_IDS_RAW)
        adapt_exclude_list = parse_rewrite_ids(ADAPT_EXCLUDE_IDS_RAW)
        validate_adapt_config(ADAPT_STATUS, ADAPT_LIMIT, force=ADAPT_FORCE, adapt_ids=adapt_ids_list)
        rewrite_ids: list[int] = []
    else:
        adapt_ids_list = []
        adapt_exclude_list = []
        ids_raw = REWRITE_IDS_RAW if APP_MODE == "rewrite" else TRANSLATE_IDS_RAW
        rewrite_ids = parse_rewrite_ids(ids_raw)
        if not rewrite_ids:
            LOGGER.error(f"Не заданы ID для режима {APP_MODE.upper()} в .env.")
            return 1

    try:
        system_prompt = SYSTEM_INSTRUCTION_FILE.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        LOGGER.error(f"Не удалось прочитать файл промпта: {exc}")
        return 1
    if not system_prompt:
        LOGGER.error(f"Файл промпта пуст: {SYSTEM_INSTRUCTION_FILE}")
        return 1

    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    except KeyError:
        LOGGER.error("Не найден GEMINI_API_KEY.")
        return 1

    run_hash = generate_run_hash(
        system_prompt, GEMINI_MODEL, f"{RESCALE_TARGET_SERVINGS}/{RESCALE_ONLY_IF_MORE_THAN}/{RESCALE_MAX_DIVISOR}",
    )
    LOGGER.info(
        f"Запуск | Режим: {APP_MODE.upper()} | ID: {len(rewrite_ids)} | DRY_RUN: {DRY_RUN} | "
        f"Thinking: {THINKING_LEVEL.upper()} | Hash: {run_hash} | Checks: {CHECKS_VERSION} | "
        f"Strict: {STRICT_REWRITE_CHECKS}"
    )

    if CACHE_TTL_DAYS > 0:
        clean_old_cache(CACHE_DIR, CACHE_TTL_DAYS)
    try:
        init_csv_log(CSV_LOG_FILE)
    except OSError as exc:
        LOGGER.error(f"Не удалось подготовить CSV-лог: {exc}")
        return 1

    generation_config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_schema=BatchRewriteResponse,
        thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL),
    )

    try:
        connection = get_db_connection()
    except mariadb.Error as exc:
        LOGGER.error(f"Ошибка подключения к БД: {exc}")
        return 1

    failed_ids, missing_in_db, invalid_servings_ids, manual_ids = [], [], [], []
    missing_qty_ids: list[int] = []
    skipped_exists_ids: list[int] = []
    ids_out_file = IDS_OUT_DIR / f"rewrite_ids_{APP_MODE}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    total_saved_ids = 0

    if APP_MODE == "adapt":
        adapt_rows = fetch_adapt_rows(
            connection,
            ADAPT_MARKET,
            ADAPT_LOCALE,
            ADAPT_LIMIT,
            recipe_ids=adapt_ids_list,
            dish_types=parse_dish_types(ADAPT_DISH_TYPES_RAW),
            force=ADAPT_FORCE,
            exclude_ids=adapt_exclude_list,
        )
        eligible_adapt_rows: list[dict[str, Any]] = []
        skipped_csv: list[tuple[dict, RewrittenRecipe | None, int | None, str]] = []
        for r in adapt_rows:
            r["rewrite_row_id"] = None
            if normalize_servings(r.get("serves")) is None:
                LOGGER.warning(f"ID {r['recipe_id']}: нет корректного serves — пропущен (no serves).")
                invalid_servings_ids.append(int(r["recipe_id"]))
                skipped_csv.append((r, None, None, "no serves"))
            else:
                eligible_adapt_rows.append(r)
        if skipped_csv:
            append_csv_batch(CSV_LOG_FILE, skipped_csv)
        batches_rows: list[list[dict[str, Any]]] = [
            eligible_adapt_rows[i:i + FETCH_SIZE] for i in range(0, len(eligible_adapt_rows), FETCH_SIZE)
        ]
        batches = [[int(r["recipe_id"]) for r in b] for b in batches_rows]
    else:
        batches_rows = []
        batches = [rewrite_ids[i:i + FETCH_SIZE] for i in range(0, len(rewrite_ids), FETCH_SIZE)]

    try:
        for batch_number, batch_ids in enumerate(batches, start=1):
            LOGGER.info(f"--- Батч {batch_number}/{len(batches)} | ID: {batch_ids} ---")
            api_called, api_failed = False, False
            eligible_ids: list[int] = []
            result_by_id: dict[int, RewrittenRecipe] = {}
            cache_hits: dict[int, RewrittenRecipe] = {}

            try:
                if APP_MODE == "adapt":
                    rows = batches_rows[batch_number - 1]
                    rows_by_id = {int(r["recipe_id"]): r for r in rows}
                else:
                    rows = fetch_rewrite_rows(connection, batch_ids)
                    rows_by_id = {int(r["rewrite_row_id"]): r for r in rows}

                missing = [rid for rid in batch_ids if rid not in rows_by_id]
                if missing:
                    missing_in_db.extend(missing)

                for rid in [x for x in batch_ids if x in rows_by_id]:
                    if normalize_servings(rows_by_id[rid].get("serves")) is None:
                        invalid_servings_ids.append(rid)
                    else:
                        eligible_ids.append(rid)

                if not eligible_ids:
                    continue

                api_requests: list[int] = []

                for rid in list(eligible_ids):
                    if is_rewrite_pipeline and SKIP_MULTI_VARIANT:
                        dups = multi_variant_dups(rows_by_id[rid].get("ingredients_list") or "")
                        if len(dups) >= MULTI_VARIANT_MIN_DUPS:
                            LOGGER.error(
                                f"ID {rid}: несколько вариантов рецепта в одном списке ({dups}) — "
                                f"пропущен, обработайте вручную (SKIP_MULTI_VARIANT=0 отключает пропуск)."
                            )
                            manual_ids.append(rid)
                            eligible_ids.remove(rid)

                if is_rewrite_pipeline and SKIP_NO_QUANTITIES:
                    for rid in list(eligible_ids):
                        ratio, n_lines = no_quantity_ratio(rows_by_id[rid].get("ingredients_list") or "")
                        if n_lines >= NO_QTY_MIN_LINES and ratio >= NO_QTY_RATIO:
                            LOGGER.error(
                                f"ID {rid}: в источнике у {ratio:.0%} ингредиентов нет количеств — пропущен "
                                f"(нужен повторный парсинг источника; SKIP_NO_QUANTITIES=0 отключает пропуск). "
                                f"Первые строки: "
                                f"{[l.strip()[:60] for l in (rows_by_id[rid].get('ingredients_list') or '').splitlines() if l.strip()][:3]}"
                            )
                            missing_qty_ids.append(rid)
                            eligible_ids.remove(rid)

                for rid in eligible_ids:
                    fingerprint = row_fingerprint(rows_by_id[rid])
                    cache_id = f"r3_{rid}" if APP_MODE == "adapt" else str(rid)
                    cache_file = CACHE_DIR / f"{cache_id}_{run_hash}_{fingerprint}.json"

                    if cache_file.exists():
                        try:
                            cached_item = RewrittenRecipe.model_validate_json(
                                cache_file.read_text(encoding="utf-8"),
                            )
                            if cached_item.rewrite_row_id != rid:
                                raise ValueError("Mismatch ID")
                            cache_hits[rid] = cached_item
                        except Exception as e:
                            LOGGER.warning(f"Кэш ID {rid} поврежден, запрашиваем заново: {e}")
                            cache_file.unlink(missing_ok=True)
                            api_requests.append(rid)
                    else:
                        api_requests.append(rid)

                # === Кэш-хиты: проверки рерайта в режимах rewrite и adapt ===
                if is_rewrite_pipeline:
                    for rid in list(cache_hits.keys()):
                        item = cache_hits[rid]
                        warns = validate_and_fix_rewrite(item, rows_by_id[rid])
                        for w in warns:
                            LOGGER.warning(f"ID {rid} (CACHE): {w}")
                        result_by_id[rid] = item
                else:
                    for rid, item in cache_hits.items():
                        result_by_id[rid] = item

                if api_requests:
                    api_called = True
                    LOGGER.info(f"[API] Запрашиваем у Gemini ID: {api_requests}")
                    ordered_rows_for_ai = [rows_by_id[rid] for rid in api_requests]

                    protected: dict[int, dict] = {}
                    prompt_rows = []

                    for r in ordered_rows_for_ai:
                        pr = dict(r)
                        src_srv = normalize_servings(r.get("serves")) or 1
                        row_key = int(r["recipe_id"] if APP_MODE == "adapt" else r["rewrite_row_id"])

                        if is_rewrite_pipeline:
                            merged = merge_broken_lines(r.get("ingredients_list") or "")
                            pr["ingredients_list"] = strip_recipe_refs(merged)
                            if "recipe" in pr["ingredients_list"].lower():
                                LOGGER.warning(
                                    f"ID {row_key}: в ингредиентах осталось слово 'recipe' "
                                    f"после strip_recipe_refs — проверьте вручную."
                                )

                        if APP_MODE == "translate":
                            mapping: dict[str, str] = {}
                            pr["ingredients_list"] = protect_units(r.get("ingredients_list") or "", mapping)
                            pr["cooking_instructions"] = protect_units(r.get("cooking_instructions") or "", mapping)
                            protected[row_key] = {
                                "ingredients": pr["ingredients_list"],
                                "instructions": pr["cooking_instructions"],
                                "mapping": mapping,
                                "servings": src_srv,
                            }
                        elif is_rewrite_pipeline:
                            new_srv = choose_target_servings(src_srv, pr["ingredients_list"])
                            if new_srv != src_srv:
                                factor = new_srv / src_srv
                                if abs(factor - 1.0) > 1e-5:
                                    pr["ingredients_list"], pr["cooking_instructions"], scale_warns = scale_recipe(
                                        pr["ingredients_list"], pr["cooking_instructions"], factor,
                                    )
                                    for w in scale_warns:
                                        LOGGER.warning(f"ID {row_key}: {w}")
                                pr["serves"] = new_srv
                                pr["scale_note"] = (
                                    f"The system scaled this recipe from {src_srv} to {new_srv} servings "
                                    f"(factor {factor:.2f}). Quantities are already final. Adjust any counts of pieces "
                                    f"mentioned in the method (patties, skewers, muffins, tortillas, bars) by the same factor."
                                )

                        prompt_rows.append(pr)

                    prompt_text = build_batch_prompt(prompt_rows)

                    try:
                        response = client.models.generate_content(
                            model=GEMINI_MODEL,
                            contents=prompt_text,
                            config=generation_config,
                        )

                        usage = getattr(response, "usage_metadata", None)
                        if usage:
                            batch_api_cost = SESSION_TRACKER.add_usage(usage, GEMINI_MODEL)
                            LOGGER.info(
                                f"Tokens: {usage.total_token_count} | Стоимость API: ${batch_api_cost:.6f}"
                            )

                        parsed = getattr(response, "parsed", None)
                        if parsed is not None:
                            batch_result = parsed if isinstance(parsed, BatchRewriteResponse) else \
                                BatchRewriteResponse.model_validate(parsed)
                        else:
                            text = getattr(response, "text", None)
                            if not text:
                                raise ValueError("Пустой ответ от Gemini.")
                            batch_result = BatchRewriteResponse.model_validate_json(text)

                        expected_ids = set(api_requests)
                        returned_ids = {it.rewrite_row_id for it in batch_result.results}
                        if expected_ids != returned_ids:
                            LOGGER.error(
                                f"Несовпадение ID: ожидалось {expected_ids}, получено {returned_ids}"
                            )
                            failed_ids.extend(expected_ids - returned_ids)

                        seen_ids: set[int] = set()
                        uniq = []
                        for it in batch_result.results:
                            if it.rewrite_row_id in expected_ids and it.rewrite_row_id not in seen_ids:
                                seen_ids.add(it.rewrite_row_id)
                                uniq.append(it)
                        batch_result.results = uniq

                        valid_results: list[RewrittenRecipe] = []
                        for item in batch_result.results:
                            if is_rewrite_pipeline:
                                warns = validate_and_fix_rewrite(
                                    item, rows_by_id[item.rewrite_row_id],
                                )
                                for w in warns:
                                    LOGGER.warning(f"ID {item.rewrite_row_id} (API): {w}")

                                if STRICT_REWRITE_CHECKS:
                                    hard = [m for m in prep_check(item.ingredients_list, item.cooking_instructions)
                                            if m in HARD_PREP]
                                    hard += [f"form:{m}" for m in form_check(item.ingredients_list, item.cooking_instructions)
                                             if m in HARD_FORM]
                                    hard_refs = recipe_ref_check(item.ingredients_list, item.cooking_instructions)
                                    if hard and not hard_refs and AUTO_FIX_PREP:
                                        labels = [h.replace("form:", "") for h in hard]
                                        item.cooking_instructions, fixed = auto_fix_prep(
                                            item.ingredients_list, item.cooking_instructions, labels)
                                        if fixed:
                                            LOGGER.warning(
                                                f"ID {item.rewrite_row_id}: шаг подготовки добавлен автоматически "
                                                f"({labels}: {fixed}) — повторный запрос не нужен.")
                                            hard = [m for m in prep_check(item.ingredients_list,
                                                                          item.cooking_instructions)
                                                    if m in HARD_PREP]
                                            hard += [f"form:{m}" for m in form_check(item.ingredients_list,
                                                                                    item.cooking_instructions)
                                                     if m in HARD_FORM]
                                    if hard or hard_refs:
                                        LOGGER.error(
                                            f"ID {item.rewrite_row_id}: отклонён строгой проверкой "
                                            f"(нет шагов: {hard}; ссылки: {hard_refs}) — повторите ID."
                                        )
                                        failed_ids.append(item.rewrite_row_id)
                                        SESSION_TRACKER.rejected_items += 1
                                        continue

                            if APP_MODE == "translate":
                                prot = protected[item.rewrite_row_id]
                                item.ingredients_list = strip_nutrition_block(item.ingredients_list)
                                item.cooking_instructions = strip_nutrition_block(item.cooking_instructions)

                                problems = validate_translation(item, prot)
                                if not item.dish_name.strip():
                                    problems.append("dish_name пустой")
                                if problems:
                                    LOGGER.error(f"ID {item.rewrite_row_id}: {'; '.join(problems)}")
                                    failed_ids.append(item.rewrite_row_id)
                                    continue

                                item.ingredients_list = restore_units(item.ingredients_list, prot["mapping"])
                                item.cooking_instructions = restore_units(
                                    item.cooking_instructions, prot["mapping"],
                                )
                                item.servings = prot["servings"]

                                leftovers = final_check(
                                    item.ingredients_list + "\n" + item.cooking_instructions,
                                )
                                if leftovers:
                                    LOGGER.error(
                                        f"ID {item.rewrite_row_id}: остались US-единицы/плейсхолдеры: {leftovers}"
                                    )
                                    failed_ids.append(item.rewrite_row_id)
                                    continue

                            valid_results.append(item)

                    except Exception:
                        api_failed = True
                        SESSION_TRACKER.failed_calls += 1
                        raise

                    for item in valid_results:
                        fingerprint = row_fingerprint(rows_by_id[item.rewrite_row_id])
                        cache_id = f"r3_{item.rewrite_row_id}" if APP_MODE == "adapt" else str(item.rewrite_row_id)
                        cache_file = CACHE_DIR / f"{cache_id}_{run_hash}_{fingerprint}.json"
                        cache_file.write_text(item.model_dump_json(), encoding="utf-8")
                        result_by_id[item.rewrite_row_id] = item

                csv_buffer: list[tuple[dict, RewrittenRecipe | None, int | None, str]] = []
                batch_saved_ids: list[int] = []
                for rid in eligible_ids:
                    rewritten = result_by_id.get(rid)
                    if not rewritten:
                        continue

                    src_servings = normalize_servings(rows_by_id[rid].get("serves")) or 1
                    source_tag = "CACHE" if rid in cache_hits else "API"

                    LOGGER.info(f"[{source_tag}] ID {rid} | Порции: {src_servings} -> {rewritten.servings}")

                    if not DRY_RUN:
                        if APP_MODE == "adapt":
                            saved_id, save_status = save_adapted_recipe(
                                connection, rows_by_id[rid], rewritten, GEMINI_MODEL,
                                ADAPT_MARKET, ADAPT_LOCALE, ADAPT_STATUS, force=ADAPT_FORCE,
                            )
                            if save_status == "OK" and saved_id:
                                batch_saved_ids.append(int(saved_id))
                            elif save_status == "exists, skipped":
                                skipped_exists_ids.append(int(rows_by_id[rid]["recipe_id"]))
                        else:
                            saved_id = save_rewritten_recipe(
                                connection, int(rows_by_id[rid]["rewrite_row_id"]), rewritten, GEMINI_MODEL,
                            )
                            save_status = "OK"
                            if saved_id:
                                batch_saved_ids.append(int(saved_id))
                    else:
                        saved_id = None if APP_MODE == "adapt" else int(rows_by_id[rid]["rewrite_row_id"])
                        save_status = "DRY_RUN"

                    csv_buffer.append((rows_by_id[rid], rewritten, saved_id, save_status))

                if not DRY_RUN:
                    connection.commit()
                    total_saved_ids += append_ids_file(ids_out_file, batch_saved_ids)

                try:
                    append_csv_batch(CSV_LOG_FILE, csv_buffer)
                except Exception as csv_exc:
                    LOGGER.error(f"БД обновлена, но CSV не записан: {csv_exc}")

            except Exception as exc:
                LOGGER.exception(f"Критическая ошибка в батче {batch_number}: {exc}")
                failed_ids.extend(
                    [rid for rid in eligible_ids if rid not in result_by_id and rid not in failed_ids]
                )
                if not DRY_RUN:
                    connection.rollback()

            if api_called and SLEEP_BETWEEN_REQUESTS > 0 and batch_number < len(batches):
                time.sleep(SLEEP_BETWEEN_REQUESTS * (2 if api_failed else 1))

    except KeyboardInterrupt:
        LOGGER.warning("Прервано пользователем.")
        if not DRY_RUN:
            try:
                connection.rollback()
            except Exception:
                pass
        return 130
    finally:
        try:
            connection.close()
        except Exception:
            pass

        LOGGER.info("=== ЗАВЕРШЕНО ===")
        if SESSION_TRACKER.requests > 0:
            LOGGER.info("=== РАСХОДЫ НА API (СЕАНС) ===")
            LOGGER.info(f"Всего платных запросов: {SESSION_TRACKER.requests}")
            LOGGER.info(f"ИТОГО потрачено (оценка): ${SESSION_TRACKER.total_cost_usd:.4f} USD")
            LOGGER.info(
                f"Токены: вход {SESSION_TRACKER.input_tokens}, выход {SESSION_TRACKER.output_tokens} "
                f"(из них мысли {SESSION_TRACKER.thought_tokens}); отклонено строгой проверкой "
                f"(оплачено, не сохранено): {SESSION_TRACKER.rejected_items}"
            )
            if SESSION_TRACKER.failed_calls:
                LOGGER.warning(
                    f"Запросов с исключением: {SESSION_TRACKER.failed_calls}. Они НЕ учтены в оценке, но могли быть "
                    f"оплачены (таймаут клиента не отменяет генерацию на сервере)."
                )
            LOGGER.info("Для сверки с биллингом используйте api_usage.csv (по строке на каждый запрос).")
        else:
            LOGGER.info("=== РАСХОДЫ НА API (СЕАНС) ===")
            LOGGER.info("Все данные взяты из кэша. Запросов к API не было. Потрачено: $0.0000 USD")
        if not DRY_RUN and total_saved_ids > 0:
            LOGGER.info(f"Файл ID для cost-nutrients: {ids_out_file} ({total_saved_ids} ID)")

    if missing_in_db:
        LOGGER.warning(f"Пропущено (нет в БД): {len(missing_in_db)} ID")
    if missing_qty_ids:
        LOGGER.error(f"Источник без количеств, нужен повторный парсинг: {sorted(set(missing_qty_ids))}")
    if manual_ids:
        LOGGER.error(f"Требуют ручной обработки (несколько вариантов в одном списке): {sorted(set(manual_ids))}")
    if invalid_servings_ids:
        LOGGER.warning(f"Пропущено (кривые порции): {len(invalid_servings_ids)} ID")
    if failed_ids:
        LOGGER.error(f"Упали или отклонены ИИ: {sorted(set(failed_ids))}")
        LOGGER.error(
            f"Для повтора (успешные берутся из кэша): "
            f"{'TRANSLATE_IDS' if APP_MODE == 'translate' else ('ADAPT_IDS' if APP_MODE == 'adapt' else 'REWRITE_IDS')}="
            f"{','.join(map(str, sorted(set(failed_ids))))}"
        )
    if APP_MODE == "adapt":
        exclude_line = format_adapt_exclude_line(
            manual_ids,
            failed_ids,
            invalid_servings_ids,
            skipped_exists_ids,
            missing_qty_ids=missing_qty_ids,
            existing_exclude_ids=adapt_exclude_list,
        )
        if exclude_line:
            LOGGER.info(f"Для исключения при следующем запуске:\n{exclude_line}")

    return 2 if failed_ids else 0


if __name__ == "__main__":
    raise SystemExit(main())