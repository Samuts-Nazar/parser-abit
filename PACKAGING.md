# Пакування в exe (PyInstaller)

## Команди

```bash
pip install pyinstaller

# GUI (основний застосунок для користувача)
pyinstaller --onedir --name parser-abit-gui --windowed --hidden-import google.genai --hidden-import anthropic --noconfirm gui.py

# CLI (опційно, той самий движок)
pyinstaller --onedir --name parser-abit-cli --hidden-import google.genai --hidden-import anthropic --noconfirm main.py
```

Результат — у `dist/parser-abit-gui/` і `dist/parser-abit-cli/`. Готові `.spec`-файли (`parser-abit-gui.spec`, `parser-abit-cli.spec`) уже в репо — можна білдити просто `pyinstaller parser-abit-gui.spec`.

`--onedir`, а не `--onefile` — свідомо: швидший запуск, менше false-positive від антивірусів. `--onefile` можна спробувати пізніше, якщо знадобиться один файл для роздачі.

## Що перевірено на живих даних (фрозен exe, не просто білд без помилок)

- **certifi/SSL:** `pyinstaller-hooks-contrib` сам підхоплює хук для `certifi` — жодних ручних `--add-data` не знадобилось. Перевірено живим HTTPS-запитом на abit-poisk.org.ua з фрозен `parser-abit-cli.exe`.
- **Кеш/конфіг у `%APPDATA%\parser-abit\`:** працює однаково в білді й поза ним, бо шлях береться з `os.environ["APPDATA"]` (`abit_parser/paths.py`), а не відносно розташування exe.
- **Кирилиця в консолі:** знайдений і виправлений реальний баг — фрозен `parser-abit-cli.exe` падав з `UnicodeEncodeError` при виводі кирилиці, бо консоль Windows типово віддає `cp1252`. Виправлено в [main.py](main.py) через `sys.stdout.reconfigure(encoding="utf-8")` на старті — це виправлення діє і для звичайного `python main.py` на "голому" cmd.exe, не тільки для exe.
- **google-genai / anthropic:** обидва — опційні залежності, імпортуються лише всередині функцій (`summarize.py`), тому PyInstaller не бачить їх статично — додані явно через `--hidden-import`.

## Відомі граблі (з бріфу v3, не всі актуальні зараз)

- **AV false-positive** — PyInstaller-exe (особливо `--onefile`) часто ловлять антивіруси. З `--onedir` рідше, але можливо. За потреби — цифровий підпис exe.
- Якщо колись переходити на `--onefile` — certifi/customtkinter-хуки та шлях до `%APPDATA%` мають лишитись робочими, але варто перетестувати живим запуском, а не вважати само собою зрозумілим.
