from conftest import requires_display

from zulip_tray import appearance


def test_prefers_dark():
    assert appearance.prefers_dark("prefer-dark")
    assert not appearance.prefers_dark("default")
    assert not appearance.prefers_dark("prefer-light")


def test_theme_for_switches_theme_variants():
    known = {"Yaru-sage", "Yaru-sage-dark", "Adwaita", "Adwaita-dark", "Solo"}.__contains__
    # Ubuntu dark: a separate -dark theme, so "light" needs the base theme
    assert appearance.theme_for("light", "Yaru-sage-dark", True, known) == ("Yaru-sage", False)
    assert appearance.theme_for("dark", "Yaru-sage", False, known) == ("Yaru-sage-dark", True)
    assert appearance.theme_for("system", "Yaru-sage-dark", True, known) == ("Yaru-sage-dark", True)
    # Debian: Adwaita stays, the flag does the work
    assert appearance.theme_for("system", "Adwaita", True, known) == ("Adwaita", True)
    assert appearance.theme_for("light", "Adwaita", True, known) == ("Adwaita", False)
    # no sibling theme: keep it, only the flag changes
    assert appearance.theme_for("dark", "Solo", False, known) == ("Solo", True)
    assert appearance.theme_for("light", "Lonely-dark", True, known) == ("Lonely-dark", False)
    # no GNOME color-scheme: guess from the theme name
    assert appearance.theme_for("system", "Yaru-sage-dark", None, known) == ("Yaru-sage-dark", True)


@requires_display
def test_color_scheme_forced_and_system():
    from gi.repository import Gtk

    prefer_dark = lambda: Gtk.Settings.get_default().get_property("gtk-application-prefer-dark-theme")  # noqa: E731
    theme = lambda: Gtk.Settings.get_default().get_property("gtk-theme-name")  # noqa: E731
    scheme = appearance.ColorScheme("dark")
    for mode in ("dark", "light", "system"):
        scheme.set_mode(mode)
        expected = appearance.theme_for(mode, scheme.system_theme, scheme.system_dark)
        assert (theme(), prefer_dark()) == expected
