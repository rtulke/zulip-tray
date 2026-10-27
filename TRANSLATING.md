# Translating Zulip Tray

Every language is a single JSON file in [`zulip_tray/locales`](zulip_tray/locales). No code changes are needed: a new file shows up in Settings › Language and is picked automatically when the desktop runs in that language.

## Add a language

1. Copy `zulip_tray/locales/en.json` to `<code>.json`, named after the locale code: `nl.json`, `pt_BR.json`, `rm.json`. A regional dialect without a code of its own uses a BCP 47 subdivision tag, like Bernese German: `gsw-u-sd-chbe.json` (Swiss German, canton of Bern).
2. Set the `_meta` block:
   ```json
   "_meta": {
     "name": "Nederlands",
     "fallback": "en"
   },
   ```
   - `name` is the language's name in the language itself, as the settings list shows it.
   - `fallback` is where missing texts come from. Use `en`, or the base language for a regional variant (`gsw` uses `de`, Bernese `gsw-u-sd-chbe` uses `gsw`, `pt_BR` could use `pt`).
3. Translate the values. Keep the keys exactly as they are.
4. Open a pull request.

A partial translation is fine: missing keys are taken from the fallback language. You can delete keys you have not translated yet.

## Rules the tests check

`python3 -m pytest tests/test_i18n.py` runs on every pull request and checks that:

- the file is valid JSON with a `_meta.name`;
- every key also exists in `en.json` (no typos, no leftovers);
- placeholders in braces stay the same: `{sender}`, `{count}`, `{error}` must appear in the translation exactly like in English, but may move within the sentence;
- no text is empty.

## Tips

- `\n` is a line break; keep it where the English text has one.
- `…` means "opens a dialog" in menus; keep it.
- Try it from a checkout: quit the running tray, set Settings › Language to "System language", then start `LANGUAGE=nl python3 -m zulip_tray.main`. Or copy the file into the installed `locales` directory and pick it in the settings.
- Corrections to existing languages are welcome too, especially for the Swiss German dialect, where spellings vary by region.
