#!/usr/bin/env python3
"""Нормализация названий блюд (dish_name_variant) при записи в БД (r18).

Модуль не зависит от БД и API.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_BASE_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _BASE_DIR / "locales" / "dish_name.yaml"

_UNICODE_SPACES_RE = re.compile(r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")
_LOW_CARB_RE = re.compile(r"\blow[\s-]+carb\b", re.IGNORECASE)

# Токены: слово (включая буквы, апострофы и дефисы внутри) или не-слово (пробелы, пунктуация)
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:['’][A-Za-zÀ-ÖØ-öø-ÿ]+)*(?:-[A-Za-zÀ-ÖØ-öø-ÿ]+(?:['’][A-Za-zÀ-ÖØ-öø-ÿ]+)*)*|[^A-Za-zÀ-ÖØ-öø-ÿ]+")
_AFTER_CAP_PUNCT = set(':({["\u2013\u2014')

_CONFIG_CACHE: dict[Path, tuple[frozenset[str], frozenset[str]]] = {}

_FALLBACK_ABBREVIATIONS = frozenset({"BBQ", "BLT", "MCT", "PB", "DIY", "USA", "UK", "TV", "NYC"})
_FALLBACK_SMALL_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "nor", "for", "so", "yet",
    "of", "in", "on", "at", "to", "by", "as", "with", "from", "de",
})


def load_dish_name_config(config_path: Path | None = None) -> tuple[frozenset[str], frozenset[str]]:
    """Загружает множества аббревиатур и малых слов из locales/dish_name.yaml."""
    path = config_path if config_path is not None else _DEFAULT_CONFIG_PATH
    cached = _CONFIG_CACHE.get(path)
    if cached is not None:
        return cached
    if not path.is_file():
        res = (_FALLBACK_ABBREVIATIONS, _FALLBACK_SMALL_WORDS)
        _CONFIG_CACHE[path] = res
        return res
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    abbrs = frozenset(str(x).strip().upper() for x in raw.get("abbreviations", []) if str(x).strip())
    smalls = frozenset(str(x).strip().lower() for x in raw.get("small_words", []) if str(x).strip())
    res = (abbrs or _FALLBACK_ABBREVIATIONS, smalls or _FALLBACK_SMALL_WORDS)
    _CONFIG_CACHE[path] = res
    return res


def _letter_count(s: str) -> int:
    return sum(1 for ch in s if ch.isalpha())


def _is_all_upper(s: str) -> bool:
    letters = [ch for ch in s if ch.isalpha()]
    return bool(letters) and all(ch.isupper() for ch in letters)


def _is_all_lower(s: str) -> bool:
    letters = [ch for ch in s if ch.isalpha()]
    return bool(letters) and all(ch.islower() for ch in letters)


def _cap_first(s: str) -> str:
    """Первая буква заглавная, остальные строчные (без капитализации после апострофа: CHICKEN'S -> Chicken's)."""
    if not s:
        return s
    return s[0].upper() + s[1:].lower()


def _normalize_hyphen_part(part: str, abbreviations: frozenset[str]) -> str:
    upper = part.upper()
    if upper in abbreviations and _is_all_upper(part):
        return upper
    if _is_all_upper(part) or _is_all_lower(part):
        return _cap_first(part)
    # Смешанный регистр (например, McDonald's) не трогаем
    return part


def _normalize_word(
    word: str,
    *,
    force_cap: bool,
    abbreviations: frozenset[str],
    small_words: frozenset[str],
) -> str:
    if "-" in word:
        parts = word.split("-")
        return "-".join(_normalize_hyphen_part(p, abbreviations) for p in parts)

    upper = word.upper()
    lower = word.lower()

    if upper in abbreviations and _is_all_upper(word):
        return upper

    if lower in small_words:
        if force_cap:
            return _cap_first(word) if (_is_all_upper(word) or _is_all_lower(word)) else word
        return lower

    if _is_all_upper(word) and _letter_count(word) >= 2:
        return _cap_first(word)

    # Слова уже в смешанном регистре (и однобуквенные не-малые слова) не трогаются
    return word


def normalize_dish_name(name: str, config_path: Path | None = None) -> str:
    """Приводит название блюда к единому виду (Low-Carb, Title Case для ALL CAPS, малые слова, аббревиатуры).

    Идемпотентна: normalize_dish_name(normalize_dish_name(x)) == normalize_dish_name(x).
    """
    if not name:
        return ""
    text = _UNICODE_SPACES_RE.sub(" ", name).strip()
    if not text:
        return ""

    abbreviations, small_words = load_dish_name_config(config_path)

    tokens = _TOKEN_RE.findall(text)
    out: list[str] = []
    seen_word = False
    after_cap_punct = False

    for tok in tokens:
        if any(ch.isalpha() for ch in tok):
            force_cap = (not seen_word) or after_cap_punct
            out.append(
                _normalize_word(
                    tok,
                    force_cap=force_cap,
                    abbreviations=abbreviations,
                    small_words=small_words,
                )
            )
            seen_word = True
            after_cap_punct = False
        else:
            out.append(tok)
            stripped = tok.strip()
            if stripped:
                # Если последний непустой знак перед следующим словом — из _AFTER_CAP_PUNCT
                after_cap_punct = stripped[-1] in _AFTER_CAP_PUNCT

    result = "".join(out)
    # Правило 1: любые варианты «low carb» / «low-carb» (после нормализации NBSP) -> «Low-Carb»
    result = _LOW_CARB_RE.sub("Low-Carb", result)
    return result
