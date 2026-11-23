# recipe-rewrite

Рерайт английских keto/low-carb рецептов (Gemini Flash) + перевод (сейчас FR, планируется DE)
с локальными мерами. Источник и приёмник — MariaDB (`recipes3` → `recipe_rewrite`).

- `rewr-transl.py` — основной скрипт (режимы `APP_MODE=rewrite|translate`)
- `checks.py` — проверки результата (версия `CHECKS_VERSION` входит в хэш кэша)
- `prompts/` — системные инструкции (версия в имени файла)
- `tests/test_regressions.py` — регрессии на реальных логах, без БД и API
- `AGENTS.md` — правила для ИИ-воркера (читать первым)
- `docs/` — WORKFLOW, CONTRACTS (стыки с другими пайплайнами), STATE (текущее состояние)
- `tasks/` — задания от Claude (`rNN.md`), `tasks/done/` — выполненные
- `tools/` — `make_review_pack.ps1`, `setup_repo.ps1` (стартовый коммит)

Репозиторий: https://github.com/garry198/recipe-rewrite (приватный)

## Запуск
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install google-genai python-dotenv mysql-connector-python pydantic   # уточнить по импортам скрипта
copy .env.example .env     # заполнить ключи
python tests\test_regressions.py                                        # должно быть: все тесты пройдены
python rewr-transl.py
```
Секреты (`.env`), логи, кэш и CSV в git не попадают.
