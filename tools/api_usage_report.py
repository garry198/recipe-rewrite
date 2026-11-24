#!/usr/bin/env python3
"""Сводный отчёт по api_usage.csv и api_usage_legacy_*.csv.
Использование:
  python tools/api_usage_report.py [--since YYYY-MM-DD] [--by account|day|run_hash] [--path api_usage.csv]
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_usage_rows(usage_path: Path, since: str | None = None) -> list[dict[str, Any]]:
    parent = usage_path.parent
    files: list[Path] = sorted(parent.glob("api_usage_legacy_*.csv"))
    if usage_path.is_file():
        files.append(usage_path)

    rows: list[dict[str, Any]] = []
    for fp in files:
        with fp.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh, delimiter=";")
            for r in reader:
                t = (r.get("time") or "").strip()
                if since and t[:10] < since:
                    continue
                acct = (r.get("account_label") or "").strip() or "legacy"
                r["_account"] = acct
                r["_day"] = t[:10] or "unknown"
                r["_run_hash"] = (r.get("run_hash") or "").strip() or "-"
                rows.append(r)
    return rows


def aggregate_usage(rows: list[dict[str, Any]], by: str = "account") -> dict[str, dict[str, Any]]:
    key_map = {"account": "_account", "day": "_day", "run_hash": "_run_hash"}
    if by not in key_map:
        raise ValueError(f"Неизвестная группировка: {by!r}")
    field = key_map[by]
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"requests": 0, "prompt": 0, "billed_output": 0, "total": 0, "cost_usd": 0.0}
    )
    for r in rows:
        k = r[field]
        agg[k]["requests"] += 1
        agg[k]["prompt"] += int(float(r.get("prompt") or 0))
        agg[k]["billed_output"] += int(float(r.get("billed_output") or 0))
        agg[k]["total"] += int(float(r.get("total") or 0))
        agg[k]["cost_usd"] += float(r.get("cost_usd") or 0.0)
    return dict(sorted(agg.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Отчёт по расходам API из api_usage.csv и legacy-файлов.")
    parser.add_argument("--since", default=None, help="Фильтр по дате (YYYY-MM-DD, включительно)")
    parser.add_argument("--by", default="account", choices=["account", "day", "run_hash"], help="Поле группировки")
    parser.add_argument("--path", type=Path, default=Path("api_usage.csv"), help="Путь к api_usage.csv")
    args = parser.parse_args()

    rows = load_usage_rows(args.path, since=args.since)
    summary = aggregate_usage(rows, by=args.by)
    if not summary:
        print("Нет данных.")
        return 0

    print(f"{args.by:<16} | {'req':>5} | {'prompt':>10} | {'output':>10} | {'total':>10} | {'cost_usd':>10}")
    print("-" * 74)
    tot_req = tot_in = tot_out = tot_all = 0
    tot_cost = 0.0
    for key, vals in summary.items():
        tot_req += vals["requests"]
        tot_in += vals["prompt"]
        tot_out += vals["billed_output"]
        tot_all += vals["total"]
        tot_cost += vals["cost_usd"]
        print(
            f"{key:<16} | {vals['requests']:>5} | {vals['prompt']:>10} | "
            f"{vals['billed_output']:>10} | {vals['total']:>10} | ${vals['cost_usd']:>9.6f}"
        )
    print("-" * 74)
    print(f"{'ИТОГО':<16} | {tot_req:>5} | {tot_in:>10} | {tot_out:>10} | {tot_all:>10} | ${tot_cost:>9.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
