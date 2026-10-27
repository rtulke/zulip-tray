from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# One JSON file per language, named after its locale code (de.json,
# gsw.json, pt_BR.json). Adding a language means adding a file: it shows up
# in the settings and is picked from the system locale without code changes.
# See TRANSLATING.md.
LOCALE_DIR = Path(__file__).parent / "locales"
DEFAULT_LANGUAGE = "en"
SYSTEM = "system"  # settings value: follow the session's locale
META_KEY = "_meta"


@dataclass(frozen=True)
class Language:
    code: str
    name: str  # in the language itself, as the settings list shows it
    fallback: str | None  # where missing texts come from (gsw -> de -> en)


def _normalize(code: str) -> str:
    return code.lower().replace("-", "_")


@lru_cache(maxsize=None)
def available_languages() -> dict[str, Language]:
    """Every language with a file in LOCALE_DIR, keyed by normalized code."""
    languages: dict[str, Language] = {}
    for path in sorted(LOCALE_DIR.glob("*.json")):
        try:
            meta = json.loads(path.read_text(encoding="utf-8")).get(META_KEY) or {}
        except (OSError, ValueError) as exc:
            logger.warning("Sprachdatei %s nicht lesbar: %s", path.name, exc)
            continue
        code = _normalize(path.stem)
        fallback = meta.get("fallback")
        if fallback is None and code != DEFAULT_LANGUAGE:
            fallback = DEFAULT_LANGUAGE
        languages[code] = Language(code, meta.get("name") or path.stem, _normalize(fallback) if fallback else None)
    return languages


@lru_cache(maxsize=None)
def _catalog(code: str) -> dict[str, str]:
    path = next((p for p in LOCALE_DIR.glob("*.json") if _normalize(p.stem) == code), None)
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: text for key, text in data.items() if key != META_KEY and isinstance(text, str)}


def match_locale(value: str) -> str | None:
    """Our language for one locale name such as "de_CH.UTF-8" or
    "gsw_CH@euro": the exact variant first, then the base language."""
    code = _normalize(value.split(".", 1)[0].split("@", 1)[0])
    if not code or code in ("c", "posix"):
        return None
    languages = available_languages()
    for candidate in (code, code.split("_", 1)[0]):
        if candidate in languages:
            return candidate
    return None


def system_language(environ=None) -> str:
    """Language from the session, with gettext's precedence: LANGUAGE (a
    colon-separated priority list, set by GNOME's Language setting) before
    LC_ALL > LC_MESSAGES > LANG. gettext ignores LANGUAGE under the C
    locale, and so do we."""
    env = os.environ if environ is None else environ
    locale = next((env[var] for var in ("LC_ALL", "LC_MESSAGES", "LANG") if env.get(var)), "")
    is_c_locale = locale.split(".", 1)[0].upper() in ("", "C", "POSIX")
    priority = [] if is_c_locale else (env.get("LANGUAGE") or "").split(":")
    for value in [*priority, locale]:
        language = match_locale(value) if value else None
        if language:
            return language
    return DEFAULT_LANGUAGE


LANGUAGE = system_language()


def set_language(choice: str | None) -> None:
    """Applies the settings choice: a language code, or "system"."""
    global LANGUAGE
    code = _normalize(choice) if choice and choice != SYSTEM else ""
    LANGUAGE = code if code in available_languages() else system_language()


def _chain(code: str):
    seen = set()
    languages = available_languages()
    while code and code not in seen:
        seen.add(code)
        yield code
        language = languages.get(code)
        code = language.fallback if language else DEFAULT_LANGUAGE
    if DEFAULT_LANGUAGE not in seen:
        yield DEFAULT_LANGUAGE


def tr(key: str, **kwargs) -> str:
    """Look up `key` in the current language, then along its fallback
    chain (e.g. gsw -> de -> en), then the key itself, so a missing
    translation never crashes the app -- just shows something less
    polished."""
    text = next((_catalog(code)[key] for code in _chain(LANGUAGE) if key in _catalog(code)), key)
    return text.format(**kwargs) if kwargs else text
