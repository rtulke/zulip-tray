#!/usr/bin/env bash
# Builds the .deb locally (in a container, so nfpm doesn't need installing
# here) and then installs it in a fresh container of the chosen distro the
# way a real user would -- `apt-get install ./pkg.deb` -- so a missing or
# misnamed dependency shows up before a release. The package is arch:all
# pure Python, so one build serves every distro; only the verify step is
# per-distro.
#
# Builds from your CURRENT working tree, uncommitted changes included.
#
# Usage: packaging/build-local.sh [distro]
# distro: debian12 | debian13 | ubuntu2404 (default) | ubuntu2604
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DISTRO="${1:-ubuntu2404}"
NFPM_VERSION="2.47.0"

case "$DISTRO" in
    debian12) IMAGE="debian:12" ;;
    debian13) IMAGE="debian:13" ;;
    ubuntu2404) IMAGE="ubuntu:24.04" ;;
    ubuntu2604) IMAGE="ubuntu:26.04" ;;
    *)
        echo "Unknown distro '$DISTRO'. Use one of: debian12 debian13 ubuntu2404 ubuntu2604" >&2
        exit 1
        ;;
esac

ENGINE="${CONTAINER_ENGINE:-}"
if [ -z "$ENGINE" ]; then
    if command -v docker >/dev/null 2>&1; then
        ENGINE=docker
    elif command -v podman >/dev/null 2>&1; then
        ENGINE=podman
    else
        echo "Neither docker nor podman found; install one or set CONTAINER_ENGINE." >&2
        exit 1
    fi
fi

# apt's privilege-dropping sandbox fails under rootless podman's user
# namespace, and recommended packages drag in postinst scripts that choke
# on an NFS-backed overlay -- both harmless to switch off in a throwaway
# build container (see rocketchat-tray's build-local.sh for the history).
APT_OPTS="-o APT::Sandbox::User=root -o APT::Install-Recommends=false"

# Proxy: forwarded only if set on the host. apt ignores plain *_proxy env
# vars here and needs Acquire::*::Proxy spelled out; curl honours the env.
PROXY_ENV_ARGS=()
for var in http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY; do
    if [ -n "${!var:-}" ]; then
        PROXY_ENV_ARGS+=(-e "$var=${!var}")
    fi
done
if [ -n "${http_proxy:-}" ]; then
    APT_OPTS="$APT_OPTS -o Acquire::http::Proxy=$http_proxy"
fi
if [ -n "${https_proxy:-}" ]; then
    APT_OPTS="$APT_OPTS -o Acquire::https::Proxy=$https_proxy"
fi

# Rootless podman on an NFS-backed $HOME needs a local storage root.
ENGINE_ARGS=()
if "$ENGINE" --version 2>/dev/null | grep -qi podman; then
    PODMAN_LOCAL_STORAGE="${TMPDIR:-/tmp}/zt-podman-storage"
    mkdir -p "$PODMAN_LOCAL_STORAGE"
    ENGINE_ARGS=(--root "$PODMAN_LOCAL_STORAGE")
fi

VERSION=$(python3 -c "import re; print(re.search(r'__version__ = \"([^\"]+)\"', open('$REPO_ROOT/zulip_tray/__init__.py').read()).group(1))")
OUT_DIR="$REPO_ROOT/dist"
mkdir -p "$OUT_DIR"
DEB_NAME="zulip-tray_${VERSION}_all.deb"

echo "==> Building $DEB_NAME via $ENGINE (ubuntu:24.04 build container)"
echo "==> Working tree copied in as-is (uncommitted changes included)"

# The repo is bind-mounted read-only; the container works on an rsync'd
# copy so nothing it does touches the working directory. Only the finished
# .deb crosses back out via the read-write /out mount.
"$ENGINE" "${ENGINE_ARGS[@]}" run --rm \
    -v "$REPO_ROOT:/src:ro" \
    -v "$OUT_DIR:/out" \
    -e "APT_OPTS=$APT_OPTS" \
    "${PROXY_ENV_ARGS[@]}" \
    ubuntu:24.04 \
    bash -c '
        set -euo pipefail
        apt-get $APT_OPTS update -qq
        DEBIAN_FRONTEND=noninteractive apt-get $APT_OPTS install -y -qq curl ca-certificates rsync
        mkdir -p /build
        rsync -a --exclude=".git" --exclude="stage" --exclude="dist" --exclude=".venv" --exclude="__pycache__" --exclude=".pytest_cache" /src/ /build/
        cd /build
        mkdir -p stage && rsync -a --exclude="__pycache__" zulip_tray/ stage/zulip_tray/
        curl -sSL "https://github.com/goreleaser/nfpm/releases/download/v'"$NFPM_VERSION"'/nfpm_'"$NFPM_VERSION"'_Linux_x86_64.tar.gz" -o /tmp/nfpm.tar.gz
        tar -xzf /tmp/nfpm.tar.gz -C /usr/local/bin --no-same-owner nfpm
        chmod +x /usr/local/bin/nfpm
        PKG_VERSION='"$VERSION"' nfpm package -f packaging/nfpm.yaml -p deb -t "/out/'"$DEB_NAME"'"
    '

echo "==> Built: $OUT_DIR/$DEB_NAME"
echo "==> Verifying install (fresh $IMAGE container, real apt dependency resolution)"

"$ENGINE" "${ENGINE_ARGS[@]}" run --rm \
    -v "$OUT_DIR:/out:ro" \
    -v "$REPO_ROOT/packaging/verify-installed.sh:/verify.sh:ro" \
    -e "APT_OPTS=$APT_OPTS" \
    "${PROXY_ENV_ARGS[@]}" \
    "$IMAGE" \
    bash -c '
        set -euo pipefail
        apt-get $APT_OPTS update -qq
        DEBIAN_FRONTEND=noninteractive apt-get $APT_OPTS install -y -qq "/out/'"$DEB_NAME"'"
        bash /verify.sh
    '

echo
echo "==> $DISTRO install verify OK: $OUT_DIR/$DEB_NAME"
echo "    Install it yourself to test the actual app (needs a real GNOME session):"
echo "      sudo apt install $OUT_DIR/$DEB_NAME"
