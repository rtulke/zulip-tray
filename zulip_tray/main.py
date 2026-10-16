from __future__ import annotations

import logging
import os
import signal
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

import urllib3  # noqa: E402

from . import auth, autostart, keyring, zulip_api  # noqa: E402
from .about_dialog import show_about  # noqa: E402
from .config import ConfigError, UserSettings, effective_admin_config  # noqa: E402
from .deeplink import RoomOpener, open_url_default  # noqa: E402
from .i18n import tr  # noqa: E402
from .idle_watch import IdleWatcher  # noqa: E402
from .mainloop import run_on_main, spawn  # noqa: E402
from .notifier import NotificationManager  # noqa: E402
from .presence import PresenceCoordinator  # noqa: E402
from .settings_dialog import SettingsDialog  # noqa: E402
from .tray import TrayController  # noqa: E402
from .worker import ZulipWorker  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

APP_ID = keyring.APP_ID


class ZulipTrayApplication(Gtk.Application):
    """Composition root. A Gtk.Application gives us, for free, what
    rocketchat-tray hand-rolled with a local socket: single instance (a
    second launch just activates this one), remote actions over D-Bus
    (`gapplication action <id> restart` from packaging/postinstall.sh
    after an upgrade), and the "open" signal through which the browser
    hands back the zulip://login URI of the OIDC sign-in."""

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.restart_requested = False
        self._activated_once = False
        self._login_dialog: auth.LoginDialog | None = None
        self._worker_started = False

    # --- Gio.Application hooks ---------------------------------------------------------

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        for name, handler in (("restart", self._handle_restart_action), ("quit", lambda *_: self.quit_app())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda *_: self.quit_app() or False)
        self._bootstrap()

    def do_activate(self) -> None:
        if not self._activated_once:
            # First launch: tray only, no window. hold() keeps the
            # application alive without any toplevel.
            self._activated_once = True
            self.hold()
            return
        # Clicking the launcher again while running (GNOME's app grid, a
        # second manual start): behave like a dock icon and show the chat.
        if self._login_dialog is not None:
            self._login_dialog.present()
        else:
            self.open_chat(None)

    def do_open(self, files, _n_files: int, _hint: str) -> None:
        if not self._activated_once:
            # Launched *by* a zulip:// URI (nothing was running): become
            # the primary instance normally, then handle the URI.
            self.do_activate()
        for file in files:
            uri = file.get_uri()
            if self._login_dialog is not None and self._login_dialog.handle_login_uri(uri):
                continue
            logger.info("Ignoriere unerwartete URI: %s", uri[:40])

    # --- setup ---------------------------------------------------------------------------

    def _bootstrap(self) -> None:
        self.settings = UserSettings.load()
        try:
            self.admin_config = effective_admin_config(self.settings)
        except ConfigError as exc:
            logger.error("%s", exc)
            dialog = Gtk.MessageDialog(
                message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text="Zulip Tray",
            )
            dialog.format_secondary_text(tr("main.config_error", error=exc))
            dialog.run()
            dialog.destroy()
            self.quit()
            return
        if not self.admin_config.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        self.room_opener = RoomOpener()
        self.notifier = NotificationManager(self.settings)
        self.idle_watcher = IdleWatcher()
        self.idle_watcher.set_threshold_minutes(self.settings.idle_threshold_minutes)
        self.idle_watcher.set_enabled(self.settings.idle_detection_enabled)
        self.worker = ZulipWorker(self.admin_config.server_url, self.admin_config.verify_ssl, self._credentials)
        self.presence = PresenceCoordinator(self.settings, self.worker, self.idle_watcher)
        self.tray = TrayController(
            self.settings,
            on_open_chat=lambda: self.open_chat(None),
            on_open_conversation=self.open_chat,
            on_open_settings=self.open_settings,
            on_reenter_login=self.reenter_login,
            on_set_status=self.set_status,
            on_set_status_message=self.set_status_message,
            on_show_about=show_about,
            on_quit=self.quit_app,
        )
        self.notifier.set_click_handler(self.tray.handle_conversation_click)

        # Everything below arrives from the worker thread via the main loop
        # (Signal.emit_from_thread) -- handlers may touch widgets.
        worker = self.worker
        worker.connecting.connect(self.tray.set_connecting)
        worker.connected.connect(self.tray.set_connected)
        worker.connected.connect(self.presence.handle_connected)
        worker.disconnected.connect(self.tray.set_disconnected)
        worker.disconnected.connect(self.presence.handle_disconnected)
        worker.auth_failed.connect(self.tray.set_auth_error)
        worker.auth_failed.connect(self.presence.handle_disconnected)
        worker.reconnect_scheduled.connect(self.tray.set_reconnect_scheduled)
        worker.notification.connect(self.tray.notify_unread)
        worker.notification.connect(self.notifier.notify)
        worker.own_presence.connect(self.presence.note_server_presence)
        worker.user_setting_changed.connect(self.presence.note_user_setting)
        worker.messages_read.connect(self.tray.messages_read)
        self.presence.status_applied.connect(self.tray.set_presence_status)
        self.presence.forced_status_changed.connect(self.tray.set_forced_status)
        self.idle_watcher.became_idle.connect(self.presence.apply)
        self.idle_watcher.became_active.connect(self.presence.apply)

        if self._credentials():
            self._start_worker()
        else:
            self.show_login(first_run=True)

    def _credentials(self) -> tuple[str, str] | None:
        email = self.settings.email
        api_key = keyring.get_stored_api_key(self.admin_config.server_url, email) if email else None
        return (email, api_key) if email and api_key else None

    def _start_worker(self) -> None:
        if self._worker_started:
            self.worker.retry_now()
            return
        self._worker_started = True
        self.worker.start()

    # --- login ------------------------------------------------------------------------------

    def show_login(self, first_run: bool = False) -> None:
        if self._login_dialog is not None:
            self._login_dialog.present()
            return
        server_url, verify_ssl = self.admin_config.server_url, self.admin_config.verify_ssl

        def fetch_settings() -> None:
            try:
                server_settings = zulip_api.get_server_settings(server_url, verify_ssl)
            except zulip_api.ApiError as exc:
                logger.warning("server_settings nicht abrufbar (%s), zeige Login ohne Server-Infos", exc)
                server_settings = None
            run_on_main(self._present_login, server_settings, first_run)

        spawn(fetch_settings, name="server-settings")

    def _present_login(self, server_settings: dict | None, first_run: bool) -> None:
        if self._login_dialog is not None:
            return
        default_email = self.settings.email or self.admin_config.default_email()
        dialog = auth.LoginDialog(
            self.admin_config.server_url, self.admin_config.verify_ssl, default_email, server_settings,
            on_open_url=open_url_default, on_success=self._handle_login_success,
        )

        def on_response(_dialog, response_id: int) -> None:
            self._login_dialog = None
            dialog.destroy()
            if response_id != Gtk.ResponseType.OK and first_run:
                # Nothing to run without credentials on a first start; a
                # cancelled *re*-login just leaves the auth-error state in
                # the tray, with "Erneut anmelden" still on offer.
                self.quit_app()

        dialog.connect("response", on_response)
        self._login_dialog = dialog
        dialog.present()

    def _handle_login_success(self, email: str, _api_key: str) -> None:
        self.settings.email = email
        self.settings.save()
        self._start_worker()

    def reenter_login(self) -> None:
        keyring.delete_api_key(self.admin_config.server_url, self.settings.email)
        self.show_login()

    # --- actions from the tray / settings -------------------------------------------------------

    def open_chat(self, fragment: str | None) -> None:
        self.room_opener.open(self.admin_config.server_url, fragment, app_mode=self.settings.app_mode_enabled)

    def open_settings(self) -> None:
        dialog = SettingsDialog(
            self.settings, self.admin_config.server_url, self.admin_config.verify_ssl, autostart.is_enabled(),
            self.settings.email, self.notifier.play_test_sound, self.update_server, self.reenter_login,
            autostart.set_enabled,
        )

        def on_response(_dialog, response_id: int) -> None:
            if response_id == Gtk.ResponseType.OK:
                self.idle_watcher.set_threshold_minutes(self.settings.idle_threshold_minutes)
                self.idle_watcher.set_enabled(self.settings.idle_detection_enabled)
                self.presence.apply()
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def update_server(self, server_url: str, verify_ssl: bool) -> None:
        if server_url == self.admin_config.server_url and verify_ssl == self.admin_config.verify_ssl:
            return
        self.admin_config.server_url = server_url
        self.admin_config.verify_ssl = verify_ssl
        self.settings.server_url_override = server_url
        self.settings.verify_ssl_override = verify_ssl
        self.settings.save()
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        # A different server means a different account: drop the stored
        # e-mail (the key is bound to server+email anyway) and reconnect
        # straight into the login dialog.
        self.settings.email = ""
        self.settings.save()
        self.worker.update_server(server_url, verify_ssl)
        self.show_login()

    def set_status(self, status: str) -> None:
        self.settings.forced_status = status
        self.settings.save()
        self.presence.apply()

    def set_status_message(self, message: str) -> None:
        self.settings.status_message = message
        self.settings.save()
        self.presence.set_status_text(message)

    def quit_app(self) -> None:
        self.worker.stop()
        self.quit()

    def _handle_restart_action(self, *_args) -> None:
        # packaging/postinstall.sh triggers this after a .deb upgrade so
        # the running (now outdated) instance relaunches itself. The actual
        # exec happens in main() after the main loop has ended and this
        # process's D-Bus name is gone -- a child spawned *now* would still
        # find us registered, defer to us as the primary instance, and exit.
        logger.info("Neustart angefordert (Paket-Upgrade), starte neu...")
        self.restart_requested = True
        self.quit_app()


def main() -> int:
    # GLib.set_prgname is what X11 WM_CLASS / the Wayland app_id are derived
    # from for windows -- matched against the .desktop file for dock icons.
    GLib.set_prgname("zulip-tray")
    GLib.set_application_name("Zulip Tray")
    app = ZulipTrayApplication()
    exit_code = app.run(sys.argv)
    if app.restart_requested:
        os.execv(sys.executable, [sys.executable, "-m", "zulip_tray.main"])
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
