"""Тесты запуска волны tools/run_wave.ps1 и tools/wave_helper.py (без БД, API и .env).
Запуск: python tests/test_run_wave.py
"""
import json
import logging
import re
import subprocess
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

    # 7. summary.md при досрочной остановке (spending cap и 3 подряд неудавшихся батча)
    run_log_cap = tmp_path / "run_cap.log"
    run_log_cap.write_text(
        "2026-11-25 14:00:00 | INFO | [API] ID 4127 | Порции: 12 -> 6\n"
        "2026-11-25 14:00:01 | INFO | Прогресс: 12/100 рецептов | батч 4/34 | потрачено $0.03 | пропущено 0\n"
        "2026-11-25 14:00:02 | ERROR | Исчерпан месячный лимит Google. Поднимите лимит и повторите ту же команду, кэш сохранён.\n"
        "2026-11-25 14:00:02 | ERROR | Остановка волны: причина=Исчерпан месячный лимит Google (spending cap) | обработано=12 | осталось=88\n"
        "2026-11-25 14:00:02 | ERROR | Необработанные ID для продолжения: ADAPT_IDS=5001,5002\n",
        encoding="utf-8",
    )
    sum_cap = tmp_path / "summary_cap.md"
    wh.write_wave_summary(sum_cap, run_log_cap, env_live, exit_code=2, ts="20261125_140000")
    cap_text = sum_cap.read_text(encoding="utf-8")
    check("summary.md reflects spending cap stop with processed, remaining, and reason",
          "## Остановка" in cap_text
          and "spending cap" in cap_text
          and "Обработано: 12" in cap_text
          and "Осталось: 88" in cap_text
          and "ADAPT_IDS=5001,5002" in cap_text
          and "Прогресс: 12/100 рецептов" in cap_text)

    run_log_3fail = tmp_path / "run_3fail.log"
    run_log_3fail.write_text(
        "2026-11-25 14:05:00 | ERROR | Остановка волны: причина=3 подряд неудавшихся батча API | обработано=6 | осталось=24\n",
        encoding="utf-8",
    )
    sum_3fail = tmp_path / "summary_3fail.md"
    wh.write_wave_summary(sum_3fail, run_log_3fail, env_dry, exit_code=2, ts="20261125_140500")
    fail3_text = sum_3fail.read_text(encoding="utf-8")
    check("summary.md reflects 3 consecutive failed batches stop",
          "## Остановка" in fail3_text
          and "3 подряд неудавшихся батча API" in fail3_text
          and "Обработано: 6" in fail3_text
          and "Осталось: 24" in fail3_text)

    # 8. run_wave.ps1 не использует Tee-Object, фильтрует консоль, пишет env.json без BOM и обрабатывает ошибку summarize
    ps1_text = (HERE / "tools" / "run_wave.ps1").read_text(encoding="utf-8-sig")
    check("run_wave.ps1 streams output via ForEach-Object without Tee-Object",
          "Tee-Object" not in ps1_text
          and "ForEach-Object" in ps1_text
          and "Out-File -FilePath $runLog -Append -Encoding utf8" in ps1_text)
    check("run_wave.ps1 filters console to progress/ERROR/stop and passes --env-file",
          "Прогресс:" in ps1_text
          and "Остановка волны:" in ps1_text
          and "--env-file" in ps1_text
          and "UTF8Encoding($false)" in ps1_text
          and "Сводка не создана:" in ps1_text)

    # 9. CLI summarize с --env-file (UTF-8 без BOM): создаёт summary.md и печатает короткий итог
    env_file_ok = tmp_path / "env.json"
    env_file_ok.write_bytes(json.dumps(env_live, ensure_ascii=False).encode("utf-8"))
    sum_cli_ok = tmp_path / "summary_cli.md"
    proc_ok = subprocess.run(
        [
            sys.executable, str(HERE / "tools" / "wave_helper.py"), "summarize",
            "--summary", str(sum_cli_ok),
            "--log", str(run_log),
            "--env-file", str(env_file_ok),
            "--exit-code", "0",
            "--ts", "20261125_150000",
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    check("CLI summarize with --env-file succeeds and prints short console summary",
          proc_ok.returncode == 0
          and sum_cli_ok.is_file()
          and "обработано 2" in proc_ok.stdout
          and "$0.0042 USD" in proc_ok.stdout
          and str(sum_cli_ok) in proc_ok.stdout,
          f"rc={proc_ok.returncode} out={proc_ok.stdout!r} err={proc_ok.stderr!r}")

    # 10. CLI summarize с битым --env-file возвращает ошибку и не создаёт summary.md
    env_file_bad = tmp_path / "env_bad.json"
    env_file_bad.write_text("{broken json", encoding="utf-8")
    sum_cli_bad = tmp_path / "summary_bad.md"
    proc_bad = subprocess.run(
        [
            sys.executable, str(HERE / "tools" / "wave_helper.py"), "summarize",
            "--summary", str(sum_cli_bad),
            "--log", str(run_log),
            "--env-file", str(env_file_bad),
            "--exit-code", "0",
            "--ts", "20261125_150001",
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    check("CLI summarize with broken --env-file fails with error and does not create summary.md",
          proc_bad.returncode != 0
          and not sum_cli_bad.exists()
          and "Некорректный JSON" in proc_bad.stderr,
          f"rc={proc_bad.returncode} err={proc_bad.stderr!r}")

    # 11. ConsoleLogFilter в rewr-transl.py скрывает по-рецептные строки и повторяющиеся WARNING
    src = (HERE / "rewr-transl.py").read_text(encoding="utf-8")
    ns_flt = {"logging": logging, "re": re}
    exec(src[src.index("class ConsoleLogFilter"): src.index("_LOG_FORMAT =")], ns_flt)
    flt = ns_flt["ConsoleLogFilter"]()
    rec_api = logging.LogRecord("t", logging.INFO, "", 0, "[API] ID 4127 | Порции: 12 -> 6", (), None)
    rec_cache = logging.LogRecord("t", logging.INFO, "", 0, "[CACHE] ID 2248 | Порции: 4 -> 4", (), None)
    rec_prog = logging.LogRecord("t", logging.INFO, "", 0, "Прогресс: 10/100 рецептов | батч 4/34 | потрачено $0.02 | пропущено 0", (), None)
    rec_w1 = logging.LogRecord("t", logging.WARNING, "", 0, "ID 101: выпечка в форме, масштабирование отключено", (), None)
    rec_w2 = logging.LogRecord("t", logging.WARNING, "", 0, "ID 202: выпечка в форме, масштабирование отключено", (), None)
    rec_err = logging.LogRecord("t", logging.ERROR, "", 0, "ID 303: критическая ошибка", (), None)
    check("ConsoleLogFilter hides per-recipe lines and duplicate warnings, keeps progress and ERROR",
          flt.filter(rec_api) is False
          and flt.filter(rec_cache) is False
          and flt.filter(rec_prog) is True
          and flt.filter(rec_w1) is True
          and flt.filter(rec_w2) is False
          and flt.filter(rec_err) is True)

print(f"\nИТОГО run_wave: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
