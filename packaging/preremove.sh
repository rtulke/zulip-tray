#!/bin/sh
set -e

if command -v py3clean >/dev/null 2>&1; then
    py3clean -p zulip-tray /usr/lib/python3/dist-packages/zulip_tray || true
fi

exit 0
