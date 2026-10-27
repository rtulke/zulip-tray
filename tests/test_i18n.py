import json
import re

import pytest

from zulip_tray import i18n

PLACEHOLDER = re.compile(r"\{(\w+)\}")
# Shipped complete; a newly contributed language may be partial and fall
# back (see TRANSLATING.md), these may not.
COMPLETE_LANGUAGES = ("de", "en", "es", "fr", "gsw", "gsw-u-sd-chbe", "it")
LOCALE_FILES = sorted(i18n.LOCALE_DIR.glob("*.json"))


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _texts(path):
    return {key: text for key, text in _load(path).items() if key != i18n.META_KEY}


ENGLISH = _texts(i18n.LOCALE_DIR / "en.json")


def test_shipped_languages_are_found():
    assert {i18n._normalize(code) for code in COMPLETE_LANGUAGES} <= set(i18n.available_languages())


@pytest.mark.parametrize("path", LOCALE_FILES, ids=lambda p: p.name)
def test_locale_file_is_valid(path):
    data = _load(path)
    meta = data.get(i18n.META_KEY)
    assert isinstance(meta, dict) and meta.get("name"), "needs _meta.name"
    fallback = meta.get("fallback")
    if fallback is not None:
        assert fallback in i18n.available_languages(), f"unknown fallback {fallback}"
    texts = _texts(path)
    unknown = set(texts) - set(ENGLISH)
    assert not unknown, f"keys not in en.json (typo or removed): {sorted(unknown)}"
    for key, text in texts.items():
        assert isinstance(text, str) and text, key
        assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(ENGLISH[key])), f"placeholders in {key}"
        assert "rocket" not in text.lower()


@pytest.mark.parametrize("code", COMPLETE_LANGUAGES)
def test_shipped_languages_are_complete(code):
    missing = set(ENGLISH) - set(_texts(i18n.LOCALE_DIR / f"{code}.json"))
    assert not missing, f"{code} misses {sorted(missing)}"


def test_tr_fallbacks(monkeypatch):
    monkeypatch.setattr(i18n, "LANGUAGE", "de")
    assert i18n.tr("tray.status_invisible") == "Unsichtbar"
    assert i18n.tr("settings.idle_minutes_value", minutes=10) == "10 Min."
    assert i18n.tr("no.such.key") == "no.such.key"
    monkeypatch.setattr(i18n, "LANGUAGE", "gsw")
    assert i18n.tr("tray.contacts_today") == "Hüt"
    i18n.set_language("gsw-u-sd-chbe")  # Bernese: its own file, as in the settings
    assert i18n.tr("tray.mark_all_read") == "Aui aus gläse markiere"


def test_fallback_chain_fills_gaps(monkeypatch):
    # A partial dialect file falls back to its base language, then English.
    monkeypatch.setattr(i18n, "_catalog", lambda code: {
        "gsw": {"a": "gsw-a"}, "de": {"a": "de-a", "b": "de-b"}, "en": {"a": "en-a", "b": "en-b", "c": "en-c"},
    }.get(code, {}))
    monkeypatch.setattr(i18n, "LANGUAGE", "gsw")
    assert (i18n.tr("a"), i18n.tr("b"), i18n.tr("c")) == ("gsw-a", "de-b", "en-c")


@pytest.mark.parametrize("environ, expected", [
    ({"LANG": "de_CH.UTF-8"}, "de"),  # Swiss Standard German, not the dialect
    ({"LANG": "gsw_CH.UTF-8"}, "gsw"),
    ({"LANG": "fr_CH.UTF-8"}, "fr"),
    ({"LANG": "it_CH.UTF-8", "LC_MESSAGES": "es_ES.UTF-8"}, "es"),
    ({"LANG": "de_CH.UTF-8", "LC_ALL": "en_US.UTF-8"}, "en"),
    ({"LANG": "de_CH.UTF-8", "LANGUAGE": "gsw:de"}, "gsw"),  # GNOME's priority list wins
    ({"LANG": "de_CH.UTF-8", "LANGUAGE": "xx:it"}, "it"),
    ({"LANG": "C.UTF-8", "LANGUAGE": "de"}, "en"),  # gettext ignores LANGUAGE under C
    ({"LANG": "ja_JP.UTF-8"}, "en"),
    ({}, "en"),
])
def test_system_language(environ, expected):
    assert i18n.system_language(environ) == expected


def test_set_language(monkeypatch):
    monkeypatch.setenv("LANG", "fr_CH.UTF-8")
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(i18n, "LANGUAGE", "en")
    i18n.set_language("gsw")
    assert i18n.LANGUAGE == "gsw"
    i18n.set_language("system")
    assert i18n.LANGUAGE == "fr"
    i18n.set_language("xx")  # file removed since it was chosen
    assert i18n.LANGUAGE == "fr"
