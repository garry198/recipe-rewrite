"""Golden-тест побайтового совпадения protect_units + restore_units для локали fr_FR.
Запуск: python tests/test_units_golden.py [путь_к_скрипту]
Не требует БД, Gemini и .env.
"""
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "rewr-transl.py"
CORPUS_FILE = HERE / "tests" / "golden" / "units_corpus.txt"
EXPECTED_FILE = HERE / "tests" / "golden" / "units_fr_expected.txt"

src = SCRIPT.read_text(encoding="utf-8")
ns = {"re": re, "math": math, "Path": Path, "BASE_DIR": HERE, "TARGET_LOCALE": "fr_FR"}
if "# ================= СХЕМЫ ДАННЫХ" in src and "# ================= UNITS_FR" in src:
    exec(src[src.index("# ================= UNITS_FR"): src.index("# ================= СХЕМЫ ДАННЫХ")], ns)
else:
    raise RuntimeError("Не найдены маркеры UNITS_FR / СХЕМЫ ДАННЫХ")

corpus_lines = CORPUS_FILE.read_bytes().decode("utf-8").splitlines()
expected_bytes = EXPECTED_FILE.read_bytes().replace(b"\r\n", b"\n")

actual_lines = []
for line in corpus_lines:
    mapping = {}
    prot = ns["protect_units"](line, mapping)
    actual_lines.append(ns["restore_units"](prot, mapping))

actual_bytes = ("\n".join(actual_lines) + "\n").encode("utf-8")

if actual_bytes != expected_bytes:
    exp_lines = expected_bytes.decode("utf-8").splitlines()
    diffs = [
        (i + 1, corpus_lines[i], exp_lines[i] if i < len(exp_lines) else "<EOF>", actual_lines[i])
        for i in range(max(len(exp_lines), len(actual_lines)))
        if (exp_lines[i] if i < len(exp_lines) else None) != (actual_lines[i] if i < len(actual_lines) else None)
    ]
    print(f"FAIL golden: {len(diffs)} расхождений из {len(corpus_lines)} строк")
    for ln, inp, exp, act in diffs[:10]:
        print(f"  L{ln}: in={inp!r}\n    exp={exp!r}\n    got={act!r}")
    sys.exit(1)

print(f"OK   golden fr_FR: {len(corpus_lines)} строк совпали побайтово")
