import re

from zulip_tray import i18n

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_all_catalogs_have_identical_keys():
    keys = {lang: set(cat) for lang, cat in i18n._CATALOGS.items()}
    assert set(keys) == set(i18n.SUPPORTED_LANGUAGES)
    for lang, ks in keys.items():
        assert ks == keys["en"], f"{lang} differs: {ks ^ keys['en']}"


def test_placeholders_consistent_across_languages():
    for key, en_text in i18n._EN.items():
        expected = set(PLACEHOLDER.findall(en_text))
        for lang, cat in i18n._CATALOGS.items():
            assert set(PLACEHOLDER.findall(cat[key])) == expected, f"{lang}:{key}"


def test_no_rocketchat_leftovers():
    for cat in i18n._CATALOGS.values():
        for text in cat.values():
            assert "rocket" not in text.lower()


def test_tr_fallbacks(monkeypatch):
    monkeypatch.setattr(i18n, "LANGUAGE", "de")
    assert i18n.tr("tray.status_invisible") == "Unsichtbar"
    assert i18n.tr("settings.idle_minutes_value", minutes=10) == "10 Min."
    assert i18n.tr("no.such.key") == "no.such.key"
