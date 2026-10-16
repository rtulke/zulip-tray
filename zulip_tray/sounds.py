from __future__ import annotations

from pathlib import Path

from .i18n import tr
from .resources import SOUND_PATH

# Where GNOME/freedesktop notification-relevant sound themes typically live.
# Checked in this order so Ubuntu's own Yaru theme wins over the generic
# freedesktop fallback when both provide the same name.
_SYSTEM_THEME_DIRS = [
    Path("/usr/share/sounds/gnome/default/alerts"),
    Path("/usr/share/sounds/Yaru/stereo"),
    Path("/usr/share/sounds/freedesktop/stereo"),
]
_EXTENSIONS = (".oga", ".ogg", ".wav")

# (key, i18n key). "click"/"string"/"swing"/"hum" are GNOME Settings' own
# alert-sound picker choices; the rest are the freedesktop sound theme's
# notification-relevant sounds.
_CATALOG = [
    ("click", "sounds.click"),
    ("string", "sounds.string"),
    ("swing", "sounds.swing"),
    ("hum", "sounds.hum"),
    ("message-new-instant", "sounds.message_short"),
    ("message", "sounds.message"),
    ("bell", "sounds.bell"),
    ("complete", "sounds.complete"),
]

DEFAULT_CHOICE = "message-new-instant"


def _find(stem: str) -> Path | None:
    for directory in _SYSTEM_THEME_DIRS:
        for ext in _EXTENSIONS:
            candidate = directory / f"{stem}{ext}"
            if candidate.exists():
                return candidate
    return None


def available_choices() -> list[tuple[str, str, Path]]:
    """(key, label, path) for every sound that actually exists on this
    system. The bundled chime is always included as a fallback."""
    choices = [("bundled", tr("sounds.bundled"), SOUND_PATH)]
    for key, i18n_key in _CATALOG:
        path = _find(key)
        if path:
            choices.append((key, tr(i18n_key), path))
    return choices


def resolve(key: str) -> Path:
    for choice_key, _label, path in available_choices():
        if choice_key == key:
            return path
    return SOUND_PATH
