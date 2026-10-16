from __future__ import annotations

import argparse
import sys

import urllib3

from . import keyring, zulip_api
from .config import ConfigError, UserSettings, effective_admin_config
from .i18n import tr


def resolve_target(target: str, email_domain: str) -> tuple[str, object]:
    """"#name" -> channel message; anything else is a direct message to an
    e-mail address, with a bare user name completed to <user>@<domain>."""
    target = target.strip()
    if target.startswith("#"):
        return "channel", target[1:]
    if target.startswith("@"):
        target = target[1:]
    if "@" not in target and email_domain:
        target = f"{target}@{email_domain}"
    return "direct", [target]


def main(argv: list[str] | None = None) -> int:
    """Testing helper, not used by the tray app itself: sends a message to
    users/channels from the command line, with the same server config and
    keyring credentials as the tray app -- handy for triggering a real
    notification while developing."""
    parser = argparse.ArgumentParser(prog="zulip-tray-send", usage=tr("cli.usage"), add_help=True)
    parser.add_argument("targets")
    parser.add_argument("message")
    parser.add_argument("--topic", default="")
    args = parser.parse_args(argv)

    targets = [t for t in (part.strip() for part in args.targets.split(",")) if t]
    if not targets:
        print(tr("cli.usage"), file=sys.stderr)
        return 2

    settings = UserSettings.load()
    try:
        admin_config = effective_admin_config(settings)
    except ConfigError as exc:
        print(tr("cli.config_error", error=exc), file=sys.stderr)
        return 1
    if not admin_config.verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    api_key = keyring.get_stored_api_key(admin_config.server_url, settings.email)
    if not settings.email or not api_key:
        print(tr("cli.no_credentials", server=admin_config.server_url), file=sys.stderr)
        return 1
    api = zulip_api.ZulipApi(admin_config.server_url, settings.email, api_key, admin_config.verify_ssl)

    exit_code = 0
    for target in targets:
        recipient_type, to = resolve_target(target, admin_config.email_domain)
        try:
            api.send_message(recipient_type, to, args.message, args.topic)
        except zulip_api.AuthError as exc:
            print(tr("cli.login_failed", error=exc), file=sys.stderr)
            return 1
        except zulip_api.ApiError as exc:
            print(tr("cli.send_failed", channel=target, error=exc), file=sys.stderr)
            exit_code = 1
        else:
            print(tr("cli.sent_ok", channel=target))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
