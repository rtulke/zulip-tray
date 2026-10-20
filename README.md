# Zulip Tray

A small GNOME system-tray client for [Zulip](https://zulip.com/): your own presence as a colour-coded tray icon, desktop notifications for new messages, and your status right in the tray menu — without running the Electron desktop app. The chat itself opens in the web client.

- Tray icon mirrors your presence as the server sees it across all your clients: green (active), orange (idle), grey (offline or invisible). Changes made in the web or mobile client show up here too.
- Desktop notifications according to your Zulip notification settings (direct messages, mentions, channels with desktop notifications on; muted channels stay quiet), with click-to-open-that-conversation. Messages you read elsewhere drop out of the "missed messages" list automatically.
- Configurable notification sound from the system's GNOME/freedesktop sound theme (or the bundled chime).
- Status menu: automatic, online, away, invisible — plus a status text.
- Direct messages menu: a search window over everyone in the organization, your favorite people (picked in Settings, stored locally) and the people you most recently exchanged direct messages with, grouped by today, yesterday, last week and older, each with their presence, unread count and the time or date of the last message; one click opens the conversation. Group DMs are left out.
- Optional automatic "idle" after a configurable period of system inactivity.
- Sign-in through the server's OpenID Connect provider in your browser; the API key is stored in the GNOME keyring, no password is ever stored. Servers with password login, or pasting an API key, work too.
- Server URL can be set fleet-wide by an admin and overridden per user from the Settings dialog.

Built with GTK 3 via PyGObject on top of distro packages only, so the `.deb` is a few hundred kilobytes and one package serves every supported release.

## Supported systems

| Distribution | Version |
|---|---|
| Debian | 12 (bookworm), 13 (trixie) |
| Ubuntu | 24.04 LTS, 26.04 LTS |

Requires GNOME Shell with the [AppIndicator and KStatusNotifierItem Support](https://extensions.gnome.org/extension/615/appindicator-support/) extension (`gnome-shell-extension-appindicator`, recommended by the package):

```bash
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com
```

## Installation

```bash
curl -LO https://github.com/rtulke/zulip-tray/releases/latest/download/zulip-tray_<version>_all.deb
sudo apt install ./zulip-tray_<version>_all.deb
sudo nano /etc/zulip-tray/config.conf     # set url (and email_domain) once for all users
```

Then log out/in (autostart) or run `zulip-tray`. The first run opens the sign-in dialog: "Sign in in your browser" sends you to the identity provider; after the login the browser hands the key back to Zulip Tray (via the `zulip://` URL scheme the package registers) and the tray icon turns green.

## Configuration

**Server URL** — resolved in this order:

1. A per-user override from the Settings dialog, stored in `~/.config/zulip-tray/settings.json`.
2. The fleet-wide default in `/etc/zulip-tray/config.conf` (`[server] url = ...`).

`email_domain` in the same file prefills the sign-in dialog with `<os-user>@<domain>`; Zulip identifies accounts by e-mail address.

**Everything else** (notifications, sound, idle behaviour, autostart, dedicated browser window) lives in the Settings dialog in the tray menu.

## Development

```bash
git clone https://github.com/rtulke/zulip-tray.git
cd zulip-tray
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 gir1.2-secret-1 python3-requests python3-pytest
python3 -m venv --system-site-packages .venv      # --system-site-packages: GTK comes from the distro
.venv/bin/pip install -e .

sudo mkdir -p /etc/zulip-tray
sudo cp packaging/config.conf.example /etc/zulip-tray/config.conf
sudo nano /etc/zulip-tray/config.conf              # set url to a real server

.venv/bin/zulip-tray
python3 -m pytest -q                                # unit tests (GTK parts need a display)
zulip-tray-send alice,#general "Hello" --topic Test # send a message with the stored credentials
```

### Building the `.deb`

```bash
bash packaging/build-local.sh              # build, then install-verify on ubuntu2404
bash packaging/build-local.sh debian13     # or: debian12 debian13 ubuntu2404 ubuntu2604
```

Builds in a container from your current working tree (uncommitted changes included), drops the package into `dist/`, and installs it in a fresh container of the chosen distro with real apt dependency resolution. CI (`.github/workflows/release.yml`) runs the tests, builds once and verifies on all four distros on every push, and publishes a GitHub Release on a `vX.Y.Z` tag — `packaging/release.sh <version> "<message>"` does the bump, commit, tag and push.

## License

[MIT](LICENSE)
