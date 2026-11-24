#!/usr/bin/env python3
"""Откат перезаписи recipe_rewrite по резервной копии из ADAPT_FORCE=1.
По умолчанию (без --apply) работает в dry-run режиме и ничего не меняет в БД.
Использование:
  python tools/rollback_force.py backups/recipe_rewrite_force_YYYYMMDD_HHMMSS.jsonl [--apply] [--delete-inserted]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent


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


def build_rollback_update_sql(columns: list[str]) -> str:
    set_clause = ", ".join(f"{col} = ?" for col in columns)
    return f"UPDATE recipe_rewrite SET {set_clause} WHERE id = ?"


def load_backup_files(jsonl_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    meta_path = jsonl_path.with_suffix(".meta.json")
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return rows, meta


def run_rollback(
    jsonl_path: Path,
    connection: Any = None,
    apply: bool = False,
    delete_inserted: bool = False,
) -> dict[str, Any]:
    rows, meta = load_backup_files(jsonl_path)
    restore_ids = [int(r["id"]) for r in rows]
    inserted_ids = [int(x) for x in meta.get("inserted_ids", [])] if delete_inserted else []

    if not apply:
        print(f"[DRY-RUN] Резервная копия: {jsonl_path}")
        print(f"[DRY-RUN] Будет восстановлено строк recipe_rewrite: {len(restore_ids)} -> {restore_ids}")
        if delete_inserted:
            print(f"[DRY-RUN] Будет удалено вставленных строк (inserted_ids): {len(inserted_ids)} -> {inserted_ids}")
        return {"restored": len(restore_ids), "deleted": len(inserted_ids), "applied": False}

    if connection is None:
        connection = get_db_connection()
        owns_conn = True
    else:
        owns_conn = False

    cursor = connection.cursor()
    try:
        for row in rows:
            cols = [k for k in row.keys() if k != "id"]
            sql = build_rollback_update_sql(cols)
            params = tuple(row[c] for c in cols) + (int(row["id"]),)
            cursor.execute(sql, params)

        if delete_inserted and inserted_ids:
            placeholders = ", ".join("?" for _ in inserted_ids)
            del_sql = f"DELETE FROM recipe_rewrite WHERE id IN ({placeholders})"
            cursor.execute(del_sql, tuple(inserted_ids))

        connection.commit()
        print(f"[APPLIED] Восстановлено строк: {len(restore_ids)}; удалено inserted_ids: {len(inserted_ids)}")
        return {"restored": len(restore_ids), "deleted": len(inserted_ids), "applied": True}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        if owns_conn:
            connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Откат recipe_rewrite по резервной копии.")
    parser.add_argument("backup_jsonl", type=Path, help="Путь к файлу .jsonl резервной копии")
    parser.add_argument("--apply", action="store_true", help="Применить изменения в БД (по умолчанию dry-run)")
    parser.add_argument("--delete-inserted", action="store_true", help="Удалить строки из meta.inserted_ids")
    args = parser.parse_args()

    if not args.backup_jsonl.is_file():
        print(f"Ошибка: файл не найден: {args.backup_jsonl}", file=sys.stderr)
        return 1

    run_rollback(args.backup_jsonl, apply=args.apply, delete_inserted=args.delete_inserted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
