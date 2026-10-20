import pytest
from conftest import requires_display

from zulip_tray import appearance


def test_prefers_dark():
    assert appearance.prefers_dark("prefer-dark")
    assert not appearance.prefers_dark("default")
    assert not appearance.prefers_dark("prefer-light")


@requires_display
def test_follow_system_color_scheme_matches_gnome_setting():
    from gi.repository import Gtk

    settings = appearance.follow_system_color_scheme()
    if settings is None:
        pytest.skip("no GNOME color-scheme setting")
    expected = settings.get_string("color-scheme") == "prefer-dark"
    assert Gtk.Settings.get_default().get_property("gtk-application-prefer-dark-theme") is expected
