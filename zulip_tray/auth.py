from __future__ import annotations

import logging
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import zulip_api
from .i18n import tr
from .mainloop import run_on_main, spawn

logger = logging.getLogger(__name__)

# Zulip API keys are 32 ASCII characters; the mobile-flow one-time pad is
# the same length in bytes, i.e. 64 hex characters (is_valid_otp server-side).
API_KEY_LENGTH = 32
OTP_HEX_LENGTH = API_KEY_LENGTH * 2
LOGIN_URI_SCHEME = "zulip"

from .keyring import APP_ID, delete_api_key, get_stored_api_key, store_api_key  # noqa: F401 (re-exported)

# --- OIDC "mobile flow": browser login that hands back an API key ----------------
#
# Zulip's native-app login: the app opens <server>/accounts/login/social/
# <backend>?mobile_flow_otp=<64 hex chars> in the browser; after the
# identity-provider login the server answers with a 302 to
# zulip://login?otp_encrypted_api_key=<hex>&email=...&user_id=...&realm=...
# where the key is XORed with the one-time pad -- so a rogue app that also
# registered the zulip:// scheme can't read it without the pad. This app
# registers x-scheme-handler/zulip in its .desktop file and receives that
# URI through Gio.Application's "open" signal (see main.py).


def generate_otp() -> str:
    return secrets.token_hex(API_KEY_LENGTH)


def decrypt_api_key(otp_encrypted_api_key: str, otp: str) -> str:
    """Mirror of zerver/lib/mobile_auth_otp.py's otp_decrypt_api_key."""
    if len(otp) != OTP_HEX_LENGTH or len(otp_encrypted_api_key) != OTP_HEX_LENGTH:
        raise ValueError("OTP oder verschluesselter API-Key hat die falsche Laenge")
    xored = "".join(f"{int(a, 16) ^ int(b, 16):x}" for a, b in zip(otp_encrypted_api_key, otp))
    return bytes.fromhex(xored).decode("ascii")


def build_browser_login_url(server_url: str, login_path: str, otp: str) -> str:
    """login_path is the external_authentication_methods[].login_url from
    GET /server_settings, e.g. "/accounts/login/social/oidc/<idp>"."""
    separator = "&" if "?" in login_path else "?"
    return f"{server_url.rstrip('/')}{login_path}{separator}{urlencode({'mobile_flow_otp': otp})}"


def parse_login_uri(uri: str) -> dict | None:
    """zulip://login?... -> {otp_encrypted_api_key, email, user_id, realm}
    or None for anything that isn't that URI."""
    parts = urlsplit(uri)
    if parts.scheme != LOGIN_URI_SCHEME or parts.netloc != "login":
        return None
    query = parse_qs(parts.query)
    return {key: values[0] for key, values in query.items() if values}


def browser_login_method(server_settings: dict) -> dict | None:
    """First external (OIDC/SAML/...) login method the server offers, or
    None. Example: {"name": "oidc:<idp>", "display_name": "Example IdP",
    "login_url": "/accounts/login/social/oidc/<idp>", ...}."""
    methods = server_settings.get("external_authentication_methods") or []
    for method in methods:
        if method.get("login_url"):
            return method
    return None


def password_login_available(server_settings: dict) -> bool:
    return bool(server_settings.get("email_auth_enabled") or server_settings.get("authentication_methods", {}).get("ldap"))


# --- login dialog ------------------------------------------------------------------


class LoginDialog(Gtk.Dialog):
    """First-run / re-auth dialog. Three ways in, shown according to what
    GET /server_settings says the server supports: sign in through the
    browser (OIDC mobile flow, the normal path on OIDC/SAML-only servers), e-mail +
    password (servers with a password backend), or pasting an API key.
    Every path ends in validate-via-GET-/users/me on a thread, then
    on_success(email, api_key) and a Gtk.ResponseType.OK response.

    Not run() with a nested loop: the OIDC path needs the Gio "open"
    signal to reach the application while this dialog is up, so the
    caller connects to "response" and keeps the main loop as is."""

    def __init__(
        self, server_url: str, verify_ssl: bool, default_email: str, server_settings: dict | None,
        on_open_url, on_success, parent=None,
    ):
        super().__init__(title=tr("auth.dialog_title"), transient_for=parent, modal=False)
        self._server_url = server_url.rstrip("/")
        self._verify_ssl = verify_ssl
        self._on_open_url = on_open_url
        self._on_success = on_success
        self._pending_otp: str | None = None
        self._busy = False
        settings = server_settings or {}
        self._browser_method = browser_login_method(settings)
        self._password_available = password_login_available(settings)

        self.set_default_size(440, -1)
        self.set_resizable(False)
        self.set_icon_name("zulip-tray")
        content = self.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(16)
        content.set_margin_bottom(6)
        content.set_margin_start(18)
        content.set_margin_end(18)

        server_label = Gtk.Label(label=tr("auth.server_label", url=self._server_url), xalign=0)
        server_label.set_line_wrap(True)
        content.pack_start(server_label, False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        self._email_entry = Gtk.Entry(text=default_email, hexpand=True)
        self._email_entry.set_input_purpose(Gtk.InputPurpose.EMAIL)
        grid.attach(Gtk.Label(label=tr("auth.email_label"), xalign=1), 0, 0, 1, 1)
        grid.attach(self._email_entry, 1, 0, 1, 1)
        row = 1
        self._password_entry: Gtk.Entry | None = None
        if self._password_available:
            self._password_entry = Gtk.Entry(hexpand=True, visibility=False)
            self._password_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            self._password_entry.connect("activate", lambda *_: self._attempt_password_login())
            grid.attach(Gtk.Label(label=tr("auth.password_label"), xalign=1), 0, row, 1, 1)
            grid.attach(self._password_entry, 1, row, 1, 1)
            row += 1
        content.pack_start(grid, False, False, 0)

        self._status_label = Gtk.Label(xalign=0)
        self._status_label.set_line_wrap(True)
        self._status_label.get_style_context().add_class("dim-label")
        self._spinner = Gtk.Spinner()
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_row.pack_start(self._spinner, False, False, 0)
        status_row.pack_start(self._status_label, True, True, 0)
        content.pack_start(status_row, False, False, 0)

        if self._browser_method:
            provider = self._browser_method.get("display_name") or self._browser_method.get("name", "")
            hint = Gtk.Label(label=tr("auth.browser_login_hint", provider=provider), xalign=0)
            hint.set_line_wrap(True)
            content.pack_start(hint, False, False, 0)
            self._browser_button = Gtk.Button(label=tr("auth.browser_login_button", provider=provider))
            self._browser_button.get_style_context().add_class("suggested-action")
            self._browser_button.connect("clicked", lambda *_: self._start_browser_login())
            content.pack_start(self._browser_button, False, False, 0)
        elif not self._password_available:
            hint = Gtk.Label(label=tr("auth.no_browser_login"), xalign=0)
            hint.set_line_wrap(True)
            content.pack_start(hint, False, False, 0)

        # Manual API key entry -- always available as the fallback.
        expander = Gtk.Expander(label=tr("auth.manual_expander"))
        manual_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        manual_box.set_margin_top(8)
        key_hint = Gtk.Label(label=tr("auth.api_key_hint"), xalign=0)
        key_hint.set_line_wrap(True)
        key_hint.get_style_context().add_class("dim-label")
        manual_box.pack_start(key_hint, False, False, 0)
        key_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._api_key_entry = Gtk.Entry(hexpand=True, visibility=False)
        self._api_key_entry.connect("activate", lambda *_: self._attempt_api_key_login())
        key_row.pack_start(Gtk.Label(label=tr("auth.api_key_label")), False, False, 0)
        key_row.pack_start(self._api_key_entry, True, True, 0)
        manual_box.pack_start(key_row, False, False, 0)
        self._api_key_button = Gtk.Button(label=tr("auth.login_button"))
        self._api_key_button.set_halign(Gtk.Align.END)
        self._api_key_button.connect("clicked", lambda *_: self._attempt_api_key_login())
        manual_box.pack_start(self._api_key_button, False, False, 0)
        expander.add(manual_box)
        expander.set_expanded(not self._browser_method and not self._password_available)
        content.pack_start(expander, False, False, 0)

        self.add_button(tr("common.cancel"), Gtk.ResponseType.CANCEL)
        if self._password_available:
            login_button = self.add_button(tr("auth.login_button"), Gtk.ResponseType.APPLY)
            login_button.get_style_context().add_class("suggested-action")
            self.set_default_response(Gtk.ResponseType.APPLY)
        self.connect("response", self._handle_response)
        content.show_all()
        self._spinner.hide()
        (self._password_entry or self._email_entry).grab_focus()

    # --- flows -----------------------------------------------------------------

    def _handle_response(self, _dialog, response_id: int) -> None:
        if response_id == Gtk.ResponseType.APPLY:
            self.stop_emission_by_name("response")
            self._attempt_password_login()

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self._status_label.set_text(text)
        if busy:
            self._spinner.show()
            self._spinner.start()
        else:
            self._spinner.stop()
            self._spinner.hide()
        for widget in (self._email_entry, self._password_entry, self._api_key_entry, self._api_key_button):
            if widget is not None:
                widget.set_sensitive(not busy)

    def _start_browser_login(self) -> None:
        if self._busy:
            return
        self._pending_otp = generate_otp()
        url = build_browser_login_url(self._server_url, self._browser_method["login_url"], self._pending_otp)
        self._set_busy(True, tr("auth.waiting_for_browser"))
        self._on_open_url(url)

    def handle_login_uri(self, uri: str) -> bool:
        """Called by the application when a zulip://login URI arrives.
        Returns True if this dialog consumed it."""
        params = parse_login_uri(uri)
        if params is None or not self._pending_otp:
            return False
        otp, self._pending_otp = self._pending_otp, None
        realm = params.get("realm", "").rstrip("/")
        if realm and realm.lower() != self._server_url.lower():
            self._fail(tr("auth.realm_mismatch", realm=realm, server=self._server_url))
            return True
        try:
            api_key = decrypt_api_key(params.get("otp_encrypted_api_key", ""), otp)
        except ValueError as exc:
            self._fail(tr("auth.failed_generic", error=exc))
            return True
        email = params.get("email") or self._email_entry.get_text().strip()
        self._email_entry.set_text(email)
        self._validate_and_finish(email, api_key)
        return True

    def _attempt_password_login(self) -> None:
        if self._busy or self._password_entry is None:
            return
        email = self._email_entry.get_text().strip()
        password = self._password_entry.get_text()
        if not email or not password:
            return
        self._set_busy(True, "")

        def worker() -> None:
            try:
                canonical_email, api_key = zulip_api.fetch_api_key(self._server_url, email, password, self._verify_ssl)
            except zulip_api.ApiError as exc:
                run_on_main(self._fail, self._describe(exc))
                return
            run_on_main(self._validate_and_finish, canonical_email, api_key)

        spawn(worker, name="password-login")

    def _attempt_api_key_login(self) -> None:
        if self._busy:
            return
        email = self._email_entry.get_text().strip()
        api_key = self._api_key_entry.get_text().strip()
        if not email or not api_key:
            return
        self._validate_and_finish(email, api_key)

    def _validate_and_finish(self, email: str, api_key: str) -> None:
        self._set_busy(True, "")

        def worker() -> None:
            api = zulip_api.ZulipApi(self._server_url, email, api_key, self._verify_ssl)
            try:
                me = api.get_me()
            except zulip_api.ApiError as exc:
                run_on_main(self._fail, self._describe(exc))
                return
            run_on_main(self._finish, me.get("email") or email, api_key)

        spawn(worker, name="login-validate")

    def _finish(self, email: str, api_key: str) -> None:
        self._set_busy(False)
        store_api_key(self._server_url, email, api_key)
        self._on_success(email, api_key)
        self.response(Gtk.ResponseType.OK)

    def _describe(self, exc: zulip_api.ApiError) -> str:
        if isinstance(exc, zulip_api.AuthError):
            return tr("auth.invalid_credentials")
        if exc.code == "CONNECTION":
            return tr("auth.server_unreachable", url=self._server_url, error=exc)
        return tr("auth.failed_generic", error=exc)

    def _fail(self, message: str) -> None:
        self._set_busy(False)
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK, text=tr("auth.failed_title"),
        )
        dialog.format_secondary_text(message)
        dialog.connect("response", lambda d, _r: d.destroy())
        dialog.show()
        if self._password_entry is not None:
            self._password_entry.select_region(0, -1)
            self._password_entry.grab_focus()
