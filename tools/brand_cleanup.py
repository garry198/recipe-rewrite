#!/usr/bin/env python3
"""Замена названий брендов на нейтральные в активных рерайтах recipe_rewrite (r17).
По умолчанию работает в режиме dry-run (ничего не пишет в БД).
Использование:
  python tools/brand_cleanup.py [--apply] [--ids "1,2,3"] [--ids-file path] [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DICT_PATH = BASE_DIR / "locales" / "brand_neutral.yaml"
DEFAULT_LOGS_DIR = BASE_DIR / "logs"

TEXT_FIELDS = ("ingredients_list_variant", "cooking_instructions_variant")
_NUMBER_RE = re.compile(r"\d+(?:[./]\d+)?|[½¼¾⅓⅔⅛]")


@dataclass(frozen=True)
class BrandRule:
    rule_id: str
    brand: str
    regex: re.Pattern[str]
    replacement: str


@dataclass(frozen=True)
class BrandDictionary:
    brands: list[tuple[str, re.Pattern[str]]]
    strip_links_re: re.Pattern[str] | None
    brand_number_res: list[re.Pattern[str]]
    rules: list[BrandRule]
    normalize: list[tuple[re.Pattern[str], str]]


@dataclass
class LineChange:
    row_id: int
    field_name: str
    before: str
    after: str
    rule: str
    is_warning: bool = False
    brands: list[str] = field(default_factory=list)


def load_brand_dictionary(path: Path = DEFAULT_DICT_PATH) -> BrandDictionary:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    brands = [
        (str(item["name"]), re.compile(str(item["detect"]), re.IGNORECASE))
        for item in raw.get("brands", [])
    ]
    strip_pat = raw.get("strip_links_pattern")
    strip_links_re = re.compile(str(strip_pat), re.IGNORECASE) if strip_pat else None
    brand_number_res = [
        re.compile(str(p), re.IGNORECASE)
        for p in raw.get("brand_number_tokens", [])
    ]
    rules = [
        BrandRule(
            rule_id=str(item["id"]),
            brand=str(item["brand"]),
            regex=re.compile(str(item["pattern"]), re.IGNORECASE),
            replacement=str(item["replacement"]),
        )
        for item in raw.get("rules", [])
    ]
    normalize = [
        (re.compile(str(item["pattern"]), re.IGNORECASE), str(item["replacement"]))
        for item in raw.get("normalize", [])
    ]
    return BrandDictionary(
        brands=brands,
        strip_links_re=strip_links_re,
        brand_number_res=brand_number_res,
        rules=rules,
        normalize=normalize,
    )


def get_db_connection() -> Any:
    import mariadb
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
    return mariadb.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_DATABASE", "seohelp_recipes"),
        port=int(os.getenv("DB_PORT", "3306")),
        autocommit=False,
    )


def parse_ids_arg(ids_str: str | None = None, ids_file: Path | None = None) -> list[int]:
    tokens: list[str] = []
    if ids_str:
        tokens.extend(re.split(r"[\s,;]+", ids_str.strip()))
    if ids_file is not None:
        for line in ids_file.read_text(encoding="utf-8").splitlines():
            clean = line.split("#", 1)[0].strip()
            if clean:
                tokens.extend(re.split(r"[\s,;]+", clean))
    seen: set[int] = set()
    result: list[int] = []
    for tok in tokens:
        if not tok:
            continue
        val = int(tok)
        if val not in seen:
            seen.add(val)
            result.append(val)
    return result


def _extract_numbers(text: str, dictionary: BrandDictionary, strip_known: bool = False) -> list[str]:
    sample = text
    if strip_known:
        if dictionary.strip_links_re is not None:
            sample = dictionary.strip_links_re.sub("", sample)
        for pat in dictionary.brand_number_res:
            sample = pat.sub("", sample)
    return _NUMBER_RE.findall(sample)


def has_brand(text: str, dictionary: BrandDictionary) -> bool:
    if not text:
        return False
    return any(pat.search(text) for _, pat in dictionary.brands)


def clean_line(
    line: str,
    dictionary: BrandDictionary,
) -> tuple[str, list[str], list[str], str | None]:
    """Очищает одну строку от брендов.

    Возвращает: (новая_строка, сработавшие_правила, затронутые_бренды, warning_или_None).
    Если строка пустая или потеряла числа — возвращает исходную строку и warning.
    """
    if not has_brand(line, dictionary):
        return line, [], [], None

    work = line
    applied_rules: list[str] = []
    matched_brands: list[str] = []

    if dictionary.strip_links_re is not None:
        new_work, n_links = dictionary.strip_links_re.subn("", work)
        if n_links:
            work = new_work
            applied_rules.append("strip_amzn_link")

    for rule in dictionary.rules:
        new_work, count = rule.regex.subn(rule.replacement, work)
        if count:
            work = new_work
            applied_rules.append(rule.rule_id)
            matched_brands.append(rule.brand)

    if not applied_rules:
        return line, [], [], None

    for norm_re, norm_repl in dictionary.normalize:
        work = norm_re.sub(norm_repl, work)
    work = work.strip()

    if work == line:
        return line, [], [], None

    if not work:
        return line, applied_rules, matched_brands, "WARNING: empty_after_cleanup"

    nums_before = _extract_numbers(line, dictionary, strip_known=True)
    nums_after = _extract_numbers(work, dictionary, strip_known=False)
    if nums_before != nums_after:
        return line, applied_rules, matched_brands, (
            f"WARNING: numbers_changed ({nums_before} -> {nums_after})"
        )

    return work, applied_rules, matched_brands, None


def clean_text(
    text: str | None,
    row_id: int,
    field_name: str,
    dictionary: BrandDictionary,
) -> tuple[str | None, list[LineChange]]:
    if text is None or not text:
        return text, []

    lines = text.splitlines()
    had_trailing_nl = text.endswith("\n")
    line_sep = "\r\n" if "\r\n" in text else "\n"
    new_lines: list[str] = []
    changes: list[LineChange] = []

    for line in lines:
        cleaned, rules, brands, warning = clean_line(line, dictionary)
        if warning is not None:
            new_lines.append(line)
            changes.append(
                LineChange(
                    row_id=row_id,
                    field_name=field_name,
                    before=line,
                    after=line,
                    rule=warning,
                    is_warning=True,
                    brands=brands,
                )
            )
        elif cleaned != line:
            new_lines.append(cleaned)
            changes.append(
                LineChange(
                    row_id=row_id,
                    field_name=field_name,
                    before=line,
                    after=cleaned,
                    rule=",".join(rules),
                    is_warning=False,
                    brands=brands,
                )
            )
        else:
            new_lines.append(line)

    result = line_sep.join(new_lines)
    if had_trailing_nl:
        result += line_sep
    return result, changes


def build_select_query(
    dictionary: BrandDictionary,
    ids: Sequence[int] | None = None,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    cols = "id, ingredients_list_variant, cooking_instructions_variant, needs_reprice"
    params: list[Any] = []
    if ids:
        placeholders = ", ".join("?" for _ in ids)
        sql = f"SELECT {cols} FROM recipe_rewrite WHERE id IN ({placeholders}) ORDER BY id"
        params.extend(int(i) for i in ids)
    else:
        like_clauses: list[str] = []
        for brand_name, _ in dictionary.brands:
            pattern = f"%{brand_name.lower()}%"
            like_clauses.append("LOWER(COALESCE(ingredients_list_variant, '')) LIKE ?")
            params.append(pattern)
            like_clauses.append("LOWER(COALESCE(cooking_instructions_variant, '')) LIKE ?")
            params.append(pattern)
        where_brands = " OR ".join(like_clauses) if like_clauses else "1=0"
        sql = (
            f"SELECT {cols} FROM recipe_rewrite "
            f"WHERE status = 'active' AND ({where_brands}) ORDER BY id"
        )
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(int(limit))
    return sql, tuple(params)


def fetch_candidate_rows(
    connection: Any,
    dictionary: BrandDictionary,
    ids: Sequence[int] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    sql, params = build_select_query(dictionary, ids=ids, limit=limit)
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        cursor.close()


def write_report_csv(path: Path, changes: Sequence[LineChange]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["id", "поле", "было", "стало", "правило"])
        for ch in changes:
            writer.writerow([ch.row_id, ch.field_name, ch.before, ch.after, ch.rule])


def write_backup_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["id", "ingredients_list_variant", "cooking_instructions_variant", "needs_reprice"])
        for r in rows:
            writer.writerow([
                r["id"],
                r.get("ingredients_list_variant") if r.get("ingredients_list_variant") is not None else "",
                r.get("cooking_instructions_variant") if r.get("cooking_instructions_variant") is not None else "",
                r.get("needs_reprice"),
            ])


def write_ids_file(path: Path, ids: Sequence[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(str(i) for i in ids) + ("\n" if ids else "")
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def run_brand_cleanup(
    connection: Any = None,
    dictionary: BrandDictionary | None = None,
    ids: Sequence[int] | None = None,
    limit: int | None = None,
    apply: bool = False,
    logs_dir: Path = DEFAULT_LOGS_DIR,
    timestamp: str | None = None,
) -> dict[str, Any]:
    if dictionary is None:
        dictionary = load_brand_dictionary()
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")

    owns_conn = connection is None
    if owns_conn:
        connection = get_db_connection()

    try:
        rows = fetch_candidate_rows(connection, dictionary, ids=ids, limit=limit)

        all_changes: list[LineChange] = []
        updates: list[tuple[dict[str, Any], str | None, str | None]] = []
        brand_counts: Counter[str] = Counter()
        lines_changed = 0
        lines_skipped = 0

        for row in rows:
            rid = int(row["id"])
            ing_before = row.get("ingredients_list_variant")
            cook_before = row.get("cooking_instructions_variant")

            ing_after, ing_changes = clean_text(ing_before, rid, "ingredients_list_variant", dictionary)
            cook_after, cook_changes = clean_text(cook_before, rid, "cooking_instructions_variant", dictionary)

            row_changes = ing_changes + cook_changes
            all_changes.extend(row_changes)

            for ch in row_changes:
                if ch.is_warning:
                    lines_skipped += 1
                else:
                    lines_changed += 1
                    for b in ch.brands:
                        brand_counts[b] += 1

            if ing_after != ing_before or cook_after != cook_before:
                updates.append((row, ing_after, cook_after))

        report_path = logs_dir / f"brand_cleanup_{ts}.csv"
        write_report_csv(report_path, all_changes)

        affected_ids = [int(r["id"]) for r, _, _ in updates]
        backup_path: Path | None = None
        ids_out_path: Path | None = None

        if apply and updates:
            backup_path = logs_dir / f"backup_brand_{ts}.csv"
            ids_out_path = logs_dir / f"rewrite_ids_brand_{ts}.txt"
            cursor = connection.cursor()
            try:
                # Бэкап и файл id пишутся ДО commit; при ошибке — rollback.
                write_backup_csv(backup_path, [r for r, _, _ in updates])
                update_sql = (
                    "UPDATE recipe_rewrite "
                    "SET ingredients_list_variant = ?, "
                    "cooking_instructions_variant = ?, "
                    "needs_reprice = 1 "
                    "WHERE id = ?"
                )
                for row, ing_after, cook_after in updates:
                    cursor.execute(update_sql, (ing_after, cook_after, int(row["id"])))
                write_ids_file(ids_out_path, affected_ids)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

        mode_label = "APPLY" if apply else "DRY-RUN"
        print(f"[{mode_label}] Затронуто текстовых строк: {lines_changed}")
        print(f"[{mode_label}] Затронуто id: {len(affected_ids)}")
        print(f"[{mode_label}] Пропущено (WARNING): {lines_skipped}")
        if brand_counts:
            summary_brands = ", ".join(f"{k}: {v}" for k, v in sorted(brand_counts.items()))
            print(f"[{mode_label}] Замен по брендам: {summary_brands}")
        else:
            print(f"[{mode_label}] Замен по брендам: 0")
        print(f"[{mode_label}] Отчёт: {report_path}")
        if backup_path is not None:
            print(f"[{mode_label}] Бэкап: {backup_path}")
        if ids_out_path is not None:
            print(f"[{mode_label}] Список id: {ids_out_path}")

        return {
            "applied": bool(apply and updates),
            "lines_changed": lines_changed,
            "lines_skipped": lines_skipped,
            "affected_ids": affected_ids,
            "brand_counts": dict(brand_counts),
            "report_path": report_path,
            "backup_path": backup_path,
            "ids_out_path": ids_out_path,
        }
    finally:
        if owns_conn:
            connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Замена названий брендов на нейтральные в recipe_rewrite (r17)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить изменения в БД (по умолчанию dry-run).",
    )
    parser.add_argument(
        "--ids",
        type=str,
        default=None,
        help='Список recipe_rewrite.id через запятую (например "1,2,3").',
    )
    parser.add_argument(
        "--ids-file",
        type=Path,
        default=None,
        help="Путь к файлу со списком recipe_rewrite.id.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить количество выбираемых строк.",
    )
    args = parser.parse_args(argv)

    if args.ids_file is not None and not args.ids_file.is_file():
        print(f"Ошибка: файл не найден: {args.ids_file}", file=sys.stderr)
        return 1

    try:
        ids = parse_ids_arg(args.ids, args.ids_file) if (args.ids or args.ids_file) else None
        run_brand_cleanup(ids=ids, limit=args.limit, apply=args.apply)
        return 0
    except Exception as exc:
        print(f"Ошибка выполнения brand_cleanup: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
