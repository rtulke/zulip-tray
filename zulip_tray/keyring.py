from __future__ import annotations

import logging

import gi

gi.require_version("Secret", "1")
from gi.repository import GLib, Secret  # noqa: E402

logger = logging.getLogger(__name__)

APP_ID = "io.github.rtulke.ZulipTray"

# API keys live in the GNOME keyring (libsecret / Secret Service), keyed
# by server + e-mail so a server or account change never reads a stale
# key. Display-free on purpose: the CLI uses this over SSH too.
_SCHEMA = Secret.Schema.new(
    APP_ID, Secret.SchemaFlags.NONE,
    {"server": Secret.SchemaAttributeType.STRING, "email": Secret.SchemaAttributeType.STRING},
)


def _attrs(server_url: str, email: str) -> dict:
    return {"server": server_url.rstrip("/"), "email": email.strip().lower()}


def get_stored_api_key(server_url: str, email: str) -> str | None:
    if not email:
        return None
    try:
        return Secret.password_lookup_sync(_SCHEMA, _attrs(server_url, email), None)
    except GLib.Error as exc:
        # No Secret Service on the bus (e.g. the CLI over plain SSH): treat
        # as "nothing stored" rather than crashing GUI and CLI alike.
        logger.warning("Kein Keyring verfuegbar: %s", exc.message)
        return None


def store_api_key(server_url: str, email: str, api_key: str) -> None:
    Secret.password_store_sync(
        _SCHEMA, _attrs(server_url, email), Secret.COLLECTION_DEFAULT,
        f"Zulip Tray ({email} @ {server_url})", api_key, None,
    )


def delete_api_key(server_url: str, email: str) -> None:
    if not email:
        return
    try:
        Secret.password_clear_sync(_SCHEMA, _attrs(server_url, email), None)
    except GLib.Error as exc:
        logger.warning("API-Key konnte nicht aus dem Keyring entfernt werden: %s", exc.message)
