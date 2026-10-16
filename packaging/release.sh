#!/usr/bin/env bash
set -euo pipefail

# Bumps the app version, commits, pushes to main, then tags and pushes a
# release. The tag push triggers .github/workflows/release.yml, which
# builds the .deb, verifies it on Debian 12/13 + Ubuntu 24.04/26.04 and
# publishes a GitHub Release with it attached.
#
# Usage: packaging/release.sh <version> "<commit message>"

if [ $# -lt 2 ]; then
    echo "Usage: $0 <version> \"<commit message>\"" >&2
    exit 1
fi

VERSION="$1"
shift
MESSAGE="$*"

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Version must look like X.Y.Z, got: $VERSION" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
    echo "Refusing to release from branch '$BRANCH' (must be on main)." >&2
    exit 1
fi

IDENTITY_EMAIL="$(git config --local user.email || true)"
if [ "$IDENTITY_EMAIL" != "rt@debian.sh" ]; then
    echo "Local git identity for this repo is '${IDENTITY_EMAIL:-<unset>}', expected 'rt@debian.sh'." >&2
    echo "Fix with:" >&2
    echo "  git config --local user.name \"Robert Tulke\"" >&2
    echo "  git config --local user.email rt@debian.sh" >&2
    exit 1
fi

echo "==> Bumping version to $VERSION"
sed -i "s/^__version__ = \".*\"/__version__ = \"$VERSION\"/" zulip_tray/__init__.py
sed -i "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml

# `add -u` (tracked/modified only), not `add -A`: new untracked files must
# be staged explicitly first so nothing unintended gets swept in.
git add -u
git add zulip_tray/__init__.py pyproject.toml

if git diff --cached --quiet; then
    echo "Nothing to commit (version already at $VERSION and no other staged changes)." >&2
    exit 1
fi

echo
echo "==> About to commit:"
git diff --cached --stat
echo
echo "Commit message: $MESSAGE"
echo
read -r -p "Commit, push to main, and release v$VERSION? [y/N] " CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    echo "Aborted." >&2
    exit 1
fi

echo "==> Committing"
git commit -m "$MESSAGE"

echo "==> Pushing main"
git push origin main

echo "==> Tagging v$VERSION"
git tag -a "v$VERSION" -m "v$VERSION: $MESSAGE"

# Separate push: GitHub skips Actions events when many refs arrive at
# once, so the tag goes out on its own to reliably trigger the workflow.
echo "==> Pushing tag v$VERSION (triggers the release build)"
git push origin "v$VERSION"

echo
echo "Done. Watch the build here:"
echo "  https://github.com/rtulke/zulip-tray/actions"
echo "Release (with the .deb download) will appear here once it finishes:"
echo "  https://github.com/rtulke/zulip-tray/releases/tag/v$VERSION"
