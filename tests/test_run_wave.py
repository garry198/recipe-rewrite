"""Тесты запуска волны tools/run_wave.ps1 и tools/wave_helper.py (без БД, API и .env).
Запуск: python tests/test_run_wave.py
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tools"))
import wave_helper as wh  # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("OK   " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)

    # 1. Разбор файла ID: UTF-8 без BOM, комментарии, пустые строки, запятые, дедупликация
    f_nobom = tmp_path / "ids_nobom.txt"
    f_nobom.write_text("# comment\n\n2248, 2250 # inline\n2252\n2248\n", encoding="utf-8")
    check("load_ids_file UTF-8 without BOM", wh.load_ids_file(f_nobom) == [2248, 2250, 2252])

    # 2. Разбор файла ID: UTF-8 с BOM
    f_bom = tmp_path / "ids_bom.txt"
    f_bom.write_text("100\n# skip 999\n200, 300\n", encoding="utf-8-sig")
    check("load_ids_file UTF-8 with BOM", wh.load_ids_file(f_bom) == [100, 200, 300])

    # 3. Исключения: wave_exclude.txt в корне проекта + добавка -Exclude
    repo_excl = HERE / "wave_exclude.txt"
    base_excl = wh.load_ids_file(repo_excl)
    check("wave_exclude.txt contains required manual IDs",
          set([1570, 2261, 4423, 4436, 4865, 4877, 4897]).issubset(set(base_excl)),
          repr(base_excl))
    merged = wh.merge_excludes(repo_excl, "9999, 1570")
    check("merge_excludes merges and deduplicates",
          9999 in merged and merged.count(1570) == 1 and len(merged) == len(base_excl) + 1)

    # 4. Dry-run по умолчанию, -Live, -Force, -Scale
    env_dry = wh.build_wave_env(ids=[2248, 2250], exclude_ids=[1570])
    check("dry-run by default sets DRY_RUN=1, APP_MODE=adapt, ADAPT_LIMIT=len(ids)",
          env_dry["DRY_RUN"] == "1"
          and env_dry["APP_MODE"] == "adapt"
          and env_dry["ADAPT_FORCE"] == "0"
          and env_dry["ADAPT_LIMIT"] == "2"
          and env_dry["ADAPT_IDS"] == "2248,2250"
          and "RESCALE_TARGET_SERVINGS" not in env_dry)

    env_live = wh.build_wave_env(ids=[2248], force=True, live=True, scale=6)
    check("live + force + scale sets DRY_RUN=0, ADAPT_FORCE=1, RESCALE_TARGET_SERVINGS=6",
          env_live["DRY_RUN"] == "0"
          and env_live["ADAPT_FORCE"] == "1"
          and env_live["RESCALE_TARGET_SERVINGS"] == "6")

    # 5. Сигнатура одинакова для dry-run и live с теми же параметрами
    sig_dry = wh.wave_signature(wh.build_wave_env(ids=[2248], force=True, live=False, scale=6))
    sig_live = wh.wave_signature(env_live)
    check("wave_signature matches between dry-run and live", sig_dry == sig_live)

    # 6. Генерация summary.md по run.log
    run_log = tmp_path / "run.log"
    run_log.write_text(
        "2026-11-25 13:00:00 | INFO | [API] ID 4127 | Порции: 12 -> 6\n"
        "2026-11-25 13:00:01 | INFO | [CACHE] ID 2248 | Порции: 4 -> 4\n"
        "2026-11-25 13:00:02 | WARNING | ID 5036: выпечка в форме (loaf pan), масштабирование отключено\n"
        "2026-11-25 13:00:03 | ERROR | ID 4423: в источнике ссылка на другой рецепт — пропущен\n"
        "2026-11-25 13:00:04 | INFO | Аккаунт: main | key_fp: 1234abcd\n"
        "2026-11-25 13:00:05 | INFO | ИТОГО потрачено (оценка): $0.0042 USD\n"
        "2026-11-25 13:00:06 | INFO | Файл ID для cost-nutrients: logs\\rewrite_ids_adapt_20261125_130000.txt (2 ID)\n",
        encoding="utf-8",
    )
    summary_md = tmp_path / "summary.md"
    wh.write_wave_summary(summary_md, run_log, env_live, exit_code=0, ts="20261125_130000")
    s_text = summary_md.read_text(encoding="utf-8")
    check("summary.md contains mode, cost, servings changed, warnings, skipped, cost ids path",
          "LIVE (DRY_RUN=0)" in s_text
          and "$0.0042 USD" in s_text
          and "ID 4127: 12 -> 6" in s_text
          and "выпечка в форме" in s_text
          and "4423" in s_text
          and "rewrite_ids_adapt_20261125_130000.txt" in s_text)

print(f"\nИТОГО run_wave: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
