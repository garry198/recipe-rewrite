"""Тесты нормализации расплывчатых единиц (r12.1) и отключения масштабирования выпечки в форме (r12.2).
Реальные случаи из БД (без БД, API и .env):
  r12.1: 9511, 9515, 9536, 9540, 9594, 9605
  r12.2: 5036, 5038, 9484
Запуск: python tests/test_vague_units.py [путь_к_скрипту]
"""
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent.parent
SCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "rewr-transl.py"
sys.path.insert(0, str(HERE))
import checks as C  # noqa: E402

src = SCRIPT.read_text(encoding="utf-8")

warnings_log: list[str] = []


class ListHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        warnings_log.append(record.getMessage())


_test_logger = logging.getLogger("test_vague_units")
_test_logger.setLevel(logging.WARNING)
_test_logger.addHandler(ListHandler())

ns: dict[str, Any] = {
    "os": os,
    "re": re,
    "Path": Path,
    "BASE_DIR": HERE,
    "Any": Any,
    "Sequence": Sequence,
    "LOGGER": _test_logger,
    "RESCALE_TARGET_SERVINGS": 4,
    "RESCALE_ONLY_IF_MORE_THAN": 8,
    "RESCALE_MAX_DIVISOR": 2.5,
    "RESCALE_SMART": True,
    "RESCALE_SKIP_PAREN_QTY": True,
    "RESCALE_SKIP_BAKEWARE": True,
    "RESCALE_CANDIDATES": [4, 6, 8, 12, 16, 24, 32],
    "RESCALE_COUNT_RE": re.compile(
        r"\b(?:large|medium|small|extra[- ]large|whole)?\s*(?:eggs?|egg\s+yolks?|egg\s+whites?)\b",
        re.IGNORECASE,
    ),
    "_UNI_FRAC": {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3", "⅛": "1/8"},
}
exec(src[src.index("def _to_float"): src.index("def _fr")], ns)
exec(src[src.index("def target_servings_for"): src.index("def build_sent_ingredients")], ns)

norm_vague = ns["normalize_vague_units"]
choose = ns["choose_target_servings"]

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("OK   " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# === r12.1: реальные случаи 9511, 9515, 9536, 9540, 9594, 9605 ===
out_9511, w_9511 = norm_vague("handful of shredded rocket (arugula) or herbs of choice", row_id=9511)
check("9511: handful of shredded rocket -> 1 cup", out_9511.startswith("1 cup shredded rocket") and not w_9511, out_9511)

out_9515, w_9515 = norm_vague("½ pack feta, crumbled (100 g/ 3.5 oz)", row_id=9515)
check("9515: 1/2 pack feta (3.5 oz) -> 3 1/2 oz feta, crumbled", out_9515 == "3 1/2 oz feta, crumbled" and not w_9515, out_9515)

out_9536, w_9536 = norm_vague("bunch of fresh wild garlic, leaves only (20 g/ 0.71 oz)", row_id=9536)
check("9536: bunch wild garlic (0.71 oz) -> 3/4 oz", out_9536 == "3/4 oz fresh wild garlic, leaves only" and not w_9536, out_9536)

out_9540, w_9540 = norm_vague("1 pack mascarpone cheese (250 g/ 8.8 oz)", row_id=9540)
check("9540: 1 pack mascarpone (8.8 oz) -> 9 oz", out_9540 == "9 oz mascarpone cheese" and not w_9540, out_9540)

out_9594, w_9594 = norm_vague("6 scoops MCT oil powder (60 g/ 2.1 oz)", row_id=9594)
check("9594: 6 scoops MCT oil powder (2.1 oz) -> 2 oz", out_9594 == "2 oz MCT oil powder" and not w_9594, out_9594)

out_9605, w_9605 = norm_vague("large handful of watercress (50 g/ 1.8 oz)", row_id=9605)
check("9605: large handful watercress (1.8 oz) -> 1 3/4 oz", out_9605 == "1 3/4 oz watercress" and not w_9605, out_9605)

# === r12.1: табличные правила без скобки, pinch, и неизвестная единица -> WARNING ===
out_feta, _ = norm_vague("1/2 pack feta, crumbled")
check("1/2 pack feta (no parens) -> 4 oz feta, crumbled", out_feta == "4 oz feta, crumbled", out_feta)

out_thyme, _ = norm_vague("1 bunch fresh thyme")
check("1 bunch fresh thyme -> 4 sprigs fresh thyme", out_thyme == "4 sprigs fresh thyme", out_thyme)

out_pinch2, _ = norm_vague("2 pinches sea salt")
check("2 pinches sea salt -> 1/4 tsp sea salt", out_pinch2 == "1/4 tsp sea salt", out_pinch2)

out_pinch1, _ = norm_vague("1 pinch sea salt")
check("1 pinch sea salt unchanged", out_pinch1 == "1 pinch sea salt", out_pinch1)

warnings_log.clear()
out_unk, w_unk = norm_vague("1 bottle mystery sauce", row_id=9999)
check("unknown vague unit kept with WARNING",
      out_unk == "1 bottle mystery sauce" and len(w_unk) == 1 and any("9999" in w for w in warnings_log),
      f"out={out_unk!r}, warns={w_unk}")

check("checks.vague_units_check catches leftover vague units and ignores clean US units",
      len(C.vague_units_check("1 handful arugula\n1 cup almond flour\n2 pinches salt")) == 2)

# === r12.2: выпечка в форме не масштабируется (5036, 5038, 9484) ===
warnings_log.clear()
got_5036 = choose(
    10, "1/2 cup psyllium husks\n1/4 cup chia seeds", row_id=5036,
    instructions="When thickened, spread out into a greased or lined 8x4-inch loaf pan.",
)
check("5036: loaf pan disables scaling (10 -> 10) + WARNING",
      got_5036 == 10 and any("ID 5036: выпечка в форме" in w for w in warnings_log), f"got={got_5036}")

warnings_log.clear()
got_5038 = choose(
    12, "1 1/2 cups almond flour\n6 large eggs", row_id=5038,
    instructions="Preheat oven to 325°F; grease a 9x5-inch loaf pan or line with parchment paper.",
)
check("5038: 9x5 loaf pan disables scaling (12 -> 12) + WARNING",
      got_5038 == 12 and any("ID 5038: выпечка в форме" in w for w in warnings_log), f"got={got_5038}")

warnings_log.clear()
got_9484 = choose(
    16, "1 1/2 cups unsalted butter\n6 large eggs", row_id=9484,
    instructions="Line the bottom of a 9 inch (23 cm) springform pan with parchment paper.",
)
check("9484: springform pan disables scaling (16 -> 16) + WARNING",
      got_9484 == 16 and any("ID 9484: выпечка в форме" in w for w in warnings_log), f"got={got_9484}")

# При RESCALE_SKIP_BAKEWARE=0 масштабирование работает
ns["RESCALE_SKIP_BAKEWARE"] = False
check("RESCALE_SKIP_BAKEWARE=0 scales 9484 (16 -> 8)",
      choose(16, "1 1/2 cups unsalted butter\n6 large eggs", instructions="Use a springform pan.") == 8)
ns["RESCALE_SKIP_BAKEWARE"] = True

# Рецепт без формы масштабируется как раньше
check("no bakeware scales 16 -> 8",
      choose(16, "2 cups almond flour\n1/2 cup butter", instructions="Stir in a large bowl and chill.") == 8)

print(f"\nИТОГО vague_units: {'все тесты пройдены' if not fails else f'упало {len(fails)}: {fails}'}")
sys.exit(1 if fails else 0)
