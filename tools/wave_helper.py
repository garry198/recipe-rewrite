#!/usr/bin/env python3
"""Вспомогательный модуль для tools/run_wave.ps1:
- разбор файлов ID (UTF-8 с BOM и без, комментарии #, пустые строки, запятые);
- объединение исключений (wave_exclude.txt + -Exclude);
- формирование env-переменных и сигнатуры запуска (dry-run по умолчанию);
- генерация logs/waves/<ts>/summary.md по run.log.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence


def parse_ids_text(text: str) -> list[int]:
    """Извлекает уникальные положительные int ID из текста (комментарии #, пустые строки, запятые/пробелы)."""
    result: list[int] = []
    seen: set[int] = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        for tok in re.split(r"[\s,;]+", line):
            tok = tok.strip()
            if tok.isdigit():
                val = int(tok)
                if val > 0 and val not in seen:
                    seen.add(val)
                    result.append(val)
    return result


def load_ids_file(path: Path) -> list[int]:
    """Читает файл ID в UTF-8 (с BOM и без)."""
    if not path.is_file():
        return []
    return parse_ids_text(path.read_text(encoding="utf-8-sig"))


def merge_excludes(exclude_file: Path | None, extra_exclude: str = "") -> list[int]:
    base = load_ids_file(exclude_file) if exclude_file is not None else []
    extra = parse_ids_text(extra_exclude)
    return sorted(set(base) | set(extra))


def build_wave_env(
    ids: Sequence[int] = (),
    limit: int = 0,
    force: bool = False,
    live: bool = False,
    exclude_ids: Sequence[int] = (),
    scale: int = 0,
) -> dict[str, str]:
    env: dict[str, str] = {
        "APP_MODE": "adapt",
        "DRY_RUN": "0" if live else "1",
        "ADAPT_FORCE": "1" if force else "0",
        "ADAPT_IDS": ",".join(str(x) for x in ids),
        "ADAPT_EXCLUDE_IDS": ",".join(str(x) for x in exclude_ids),
    }
    if limit > 0:
        env["ADAPT_LIMIT"] = str(limit)
    elif ids:
        env["ADAPT_LIMIT"] = str(len(ids))
    if scale > 0:
        env["RESCALE_TARGET_SERVINGS"] = str(scale)
    return env


def wave_signature(env: dict[str, str]) -> str:
    raw = (
        f"ids={env.get('ADAPT_IDS', '')}|limit={env.get('ADAPT_LIMIT', '')}|"
        f"force={env.get('ADAPT_FORCE', '0')}|scale={env.get('RESCALE_TARGET_SERVINGS', '')}|"
        f"exclude={env.get('ADAPT_EXCLUDE_IDS', '')}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def parse_run_log(log_text: str) -> dict[str, Any]:
    processed: list[int] = []
    servings_changed: list[str] = []
    warnings: list[str] = []
    skipped: list[str] = []
    cost_line = ""
    account_line = ""
    ids_file_line = ""
    stop_reason = ""
    stop_processed: int | None = None
    stop_remaining: int | None = None
    stop_continue_line = ""
    last_progress_line = ""

    for raw in (log_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m_proc = re.search(r"\[(CACHE|API)\]\s+ID\s+(\d+)\s+\|\s+Порции:\s+(\d+)\s*->\s*(\d+)", line)
        if m_proc:
            rid = int(m_proc.group(2))
            s_before, s_after = int(m_proc.group(3)), int(m_proc.group(4))
            processed.append(rid)
            if s_before != s_after:
                servings_changed.append(f"ID {rid}: {s_before} -> {s_after}")
        if "| WARNING |" in line:
            warnings.append(line.split("| WARNING |", 1)[1].strip())
        if any(k in line for k in (
            "пропущен", "Пропущено", "Упали или отклонены ИИ", "Сводка необработанных ADAPT_IDS",
            "exists, skipped", "Источник без количеств", "ссылкой на другой рецепт",
        )):
            msg = line.split("|", 2)[-1].strip() if "|" in line else line
            skipped.append(msg)
        if "ИТОГО потрачено (оценка):" in line or "Потрачено: $0.0000 USD" in line:
            cost_line = line.split("|", 2)[-1].strip() if "|" in line else line
        if "Аккаунт:" in line and "key_fp:" in line:
            account_line = line.split("|", 2)[-1].strip() if "|" in line else line
        if "Файл ID для cost-nutrients:" in line:
            ids_file_line = line.split("Файл ID для cost-nutrients:", 1)[1].strip()
        if "Прогресс:" in line and "рецептов" in line:
            last_progress_line = line.split("|", 2)[-1].strip() if "|" in line else line
        m_stop = re.search(
            r"Остановка волны:\s*причина=([^|]+)\|\s*обработано=(\d+)\s*\|\s*осталось=(\d+)",
            line,
        )
        if m_stop:
            stop_reason = m_stop.group(1).strip()
            stop_processed = int(m_stop.group(2))
            stop_remaining = int(m_stop.group(3))
        if "Исчерпан месячный лимит Google" in line and not stop_reason:
            stop_reason = "Исчерпан месячный лимит Google (spending cap)"
        if "Необработанные ID для продолжения:" in line:
            stop_continue_line = line.split("Необработанные ID для продолжения:", 1)[1].strip()

    return {
        "processed_ids": sorted(set(processed)),
        "servings_changed": servings_changed,
        "warnings": warnings,
        "skipped": skipped,
        "cost_line": cost_line,
        "account_line": account_line,
        "ids_file_line": ids_file_line,
        "stop_reason": stop_reason,
        "stop_processed": stop_processed,
        "stop_remaining": stop_remaining,
        "stop_continue_line": stop_continue_line,
        "last_progress_line": last_progress_line,
    }


def write_wave_summary(
    summary_path: Path,
    run_log_path: Path,
    env: dict[str, str],
    exit_code: int,
    ts: str,
) -> Path:
    log_text = run_log_path.read_text(encoding="utf-8-sig") if run_log_path.is_file() else ""
    parsed = parse_run_log(log_text)
    mode_label = "LIVE (DRY_RUN=0)" if env.get("DRY_RUN") == "0" else "DRY_RUN (DRY_RUN=1)"

    lines = [
        f"# Wave summary — {ts}",
        "",
        "## Параметры запуска",
        f"- Режим: **{mode_label}**",
        f"- `ADAPT_IDS`: `{env.get('ADAPT_IDS') or '(по LIMIT)'}`",
        f"- `ADAPT_LIMIT`: `{env.get('ADAPT_LIMIT') or '(из .env)'}`",
        f"- `ADAPT_FORCE`: `{env.get('ADAPT_FORCE', '0')}`",
        f"- `RESCALE_TARGET_SERVINGS`: `{env.get('RESCALE_TARGET_SERVINGS') or '(из .env)'}`",
        f"- `ADAPT_EXCLUDE_IDS`: `{env.get('ADAPT_EXCLUDE_IDS') or '-'}`",
        f"- Код выхода: `{exit_code}`",
        f"- Лог консоли: `{run_log_path.name}`",
        "",
        "## Итоги и расход",
        f"- Аккаунт: {parsed['account_line'] or 'не указан'}",
        f"- Расход: {parsed['cost_line'] or 'нет данных'}",
        f"- Обработано рецептов: {len(parsed['processed_ids'])}",
        f"- Пропущено / замечания по отбору: {len(parsed['skipped'])}",
        f"- Файл ID для cost-nutrients: {parsed['ids_file_line'] or 'нет (DRY_RUN или 0 записей)'}",
    ]
    if parsed["last_progress_line"]:
        lines.append(f"- Последний прогресс: {parsed['last_progress_line']}")

    if parsed["stop_reason"]:
        done_cnt = parsed["stop_processed"] if parsed["stop_processed"] is not None else len(parsed["processed_ids"])
        rem_cnt = parsed["stop_remaining"] if parsed["stop_remaining"] is not None else 0
        lines.extend([
            "",
            "## Остановка",
            f"- Причина: **{parsed['stop_reason']}**",
            f"- Обработано: {done_cnt}",
            f"- Осталось: {rem_cnt}",
        ])
        if parsed["stop_continue_line"]:
            lines.append(f"- Продолжить: `{parsed['stop_continue_line']}`")

    lines.extend([
        "",
        "## Порции изменились",
    ])
    if parsed["servings_changed"]:
        lines.extend(f"- {s}" for s in parsed["servings_changed"])
    else:
        lines.append("- нет")

    lines.append("")
    lines.append("## Пропущенные / причины")
    if parsed["skipped"]:
        lines.extend(f"- {s}" for s in parsed["skipped"])
    else:
        lines.append("- нет")

    lines.append("")
    lines.append("## Предупреждения (WARNING)")
    if parsed["warnings"]:
        lines.extend(f"- {w}" for w in parsed["warnings"])
    else:
        lines.append("- нет")

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prep = sub.add_parser("prepare")
    p_prep.add_argument("--ids", default="")
    p_prep.add_argument("--ids-file", default="")
    p_prep.add_argument("--limit", type=int, default=0)
    p_prep.add_argument("--force", action="store_true")
    p_prep.add_argument("--live", action="store_true")
    p_prep.add_argument("--exclude-file", default="wave_exclude.txt")
    p_prep.add_argument("--exclude", default="")
    p_prep.add_argument("--scale", type=int, default=0)

    p_sum = sub.add_parser("summarize")
    p_sum.add_argument("--summary", type=Path, required=True)
    p_sum.add_argument("--log", type=Path, required=True)
    p_sum.add_argument("--env-json", required=True)
    p_sum.add_argument("--exit-code", type=int, required=True)
    p_sum.add_argument("--ts", required=True)

    args = parser.parse_args()
    if args.cmd == "prepare":
        ids = parse_ids_text(args.ids)
        if args.ids_file:
            for x in load_ids_file(Path(args.ids_file)):
                if x not in ids:
                    ids.append(x)
        excl = merge_excludes(Path(args.exclude_file) if args.exclude_file else None, args.exclude)
        env = build_wave_env(ids=ids, limit=args.limit, force=args.force, live=args.live, exclude_ids=excl, scale=args.scale)
        sig = wave_signature(env)
        print(json.dumps({"ids": ids, "exclude": excl, "env": env, "signature": sig}, ensure_ascii=False))
        return 0

    if args.cmd == "summarize":
        env = json.loads(args.env_json)
        write_wave_summary(args.summary, args.log, env, args.exit_code, args.ts)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
