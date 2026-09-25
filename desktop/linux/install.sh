#!/usr/bin/env bash
# SlyLED orchestrator — headless Linux install as a systemd service (#948 Phase 2).
#
#   sudo bash SlyLED-<ver>/desktop/linux/install.sh   install or upgrade from an
#                                  extracted release tarball (or a source tree)
#   sudo bash install.sh --release <tag|latest>        download the release tarball,
#                                  verify its sha256, install/upgrade from it
#   sudo bash install.sh --uninstall [--purge]         remove service, udev rule,
#                                  /opt/slyled (--purge: + data + the slyled user)
#   bash install.sh --version                          installed / this tree's version
#
# One-liner: curl -fsSL https://raw.githubusercontent.com/SlyWombat/SlyLED/main/desktop/linux/install.sh | sudo bash -s -- --release latest
#
# Layout:
#   /opt/slyled                  code (root-owned, read-only to the service) + .venv
#   /var/lib/slyled/SlyLED/      data/, firmware/ cache, runtimes/ (owned by user slyled)
#   /etc/systemd/system/slyled.service, /etc/udev/rules.d/99-slyled-usb.rules
#
# Tested on Ubuntu 22.04+/Debian Bookworm+ (x86_64, aarch64). Needs python3 >= 3.10.
set -euo pipefail

PREFIX=/opt/slyled
SERVICE=slyled
SVC_USER=slyled
STATE_DIR=/var/lib/slyled
CACHE_DIR=/var/cache/slyled
UNIT_DST=/etc/systemd/system/${SERVICE}.service
UDEV_DST=/etc/udev/rules.d/99-slyled-usb.rules
PORT=8080

# Piped (`curl … | sudo bash -s -- --release latest`) there is no script file:
# BASH_SOURCE is unset (fatal under `set -u`) and there is no tree to install
# from — only --release works, and it re-execs the downloaded tarball's own
# install.sh (#962 follow-up).
SELF="${BASH_SOURCE[0]:-}"
if [ -n "$SELF" ] && [ -f "$SELF" ]; then
    HERE=$(cd "$(dirname "$SELF")" && pwd)
    SRC=$(cd "$HERE/../.." && pwd)
else
    HERE=""
    SRC=""
fi

if [ -t 1 ]; then B=$'\033[1m' R=$'\033[0m'; else B='' R=''; fi
say() { printf '%s[slyled]%s %s\n' "$B" "$R" "$*"; }
die() { printf '[slyled] ERROR: %s\n' "$*" >&2; exit 1; }
have_systemd() { [ -d /run/systemd/system ]; }

usage() {
    if [ -n "$SELF" ] && [ -f "$SELF" ]; then sed -n '2,20p' "$SELF" | sed 's/^# \{0,1\}//'
    else echo "usage: install.sh [--release <tag|latest>] [--uninstall [--purge]] [--version]"; fi
    exit "${1:-0}"
}

# The version a tree carries: the release tarball's VERSION file, else the
# orchestrator's own VERSION line (a source checkout). Never bumped here.
tree_version() {
    if [ -f "$1/VERSION" ]; then tr -d ' \n' < "$1/VERSION"
    elif [ -f "$1/desktop/shared/parent_server.py" ]; then
        sed -n 's/^VERSION *= *"\([0-9.]*\)".*/\1/p' "$1/desktop/shared/parent_server.py" | head -1
    fi
}

UNINSTALL=0; PURGE=0; RELEASE=""; SHOW_VERSION=0
while [ $# -gt 0 ]; do
    case "$1" in
        --uninstall) UNINSTALL=1 ;;
        --purge) PURGE=1 ;;
        --release) shift; [ $# -gt 0 ] || die "--release needs a tag (v2.1.7) or 'latest'"; RELEASE="$1" ;;
        --release=*) RELEASE="${1#--release=}" ;;
        --version) SHOW_VERSION=1 ;;
        -h|--help) usage 0 ;;
        *) echo "unknown option: $1" >&2; usage 2 ;;
    esac
    shift
done

if [ "$SHOW_VERSION" -eq 1 ]; then
    if [ -f "$PREFIX/VERSION" ]; then echo "installed: $(cat "$PREFIX/VERSION")"
    else echo "installed: none"; fi
    if [ -n "$SRC" ]; then v=$(tree_version "$SRC"); [ -n "$v" ] && echo "this tree: $v"; fi
    exit 0
fi

[ "$(id -u)" -eq 0 ] || die "run as root: sudo bash ${SELF:-install.sh} …"

# ── --release: fetch + verify the tarball, then run ITS install.sh ──────
# #962 — no git checkout needed. SLYLED_RELEASE_BASE overrides the download
# base (tests, mirrors).
if [ -n "$RELEASE" ]; then
    command -v curl >/dev/null || die "--release needs curl"
    command -v sha256sum >/dev/null || die "--release needs sha256sum"
    repo=https://github.com/SlyWombat/SlyLED
    tag="$RELEASE"
    if [ "$tag" = latest ]; then
        tag=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$repo/releases/latest" | sed 's#.*/tag/##')
        [ -n "$tag" ] || die "could not resolve the latest release tag"
    fi
    case "$tag" in v*) ;; *) tag="v$tag" ;; esac
    ver="${tag#v}"
    base="${SLYLED_RELEASE_BASE:-$repo/releases/download/$tag}"
    tgz="SlyLED-$ver-linux.tar.gz"
    dl=$(mktemp -d)
    trap 'rm -rf "$dl"' EXIT
    say "downloading $tgz ($tag)"
    curl -fsSL -o "$dl/$tgz" "$base/$tgz" || die "download failed: $base/$tgz"
    curl -fsSL -o "$dl/$tgz.sha256" "$base/$tgz.sha256" || die "download failed: $base/$tgz.sha256"
    (cd "$dl" && sha256sum -c --status "$tgz.sha256") || die "sha256 mismatch for $tgz — not installing"
    say "sha256 verified"
    tar -C "$dl" -xzf "$dl/$tgz"
    [ -f "$dl/SlyLED-$ver/desktop/linux/install.sh" ] || die "$tgz has no desktop/linux/install.sh"
    args=()
    [ "$PURGE" -eq 1 ] && args+=(--purge)
    bash "$dl/SlyLED-$ver/desktop/linux/install.sh" "${args[@]}"
    exit $?
fi

uninstall() {
    if have_systemd; then
        systemctl disable --now "$SERVICE" 2>/dev/null || true
    fi
    rm -f "$UNIT_DST" "$UDEV_DST"
    if have_systemd; then systemctl daemon-reload; fi
    if command -v udevadm >/dev/null; then udevadm control --reload || true; fi
    rm -rf "$PREFIX"
    say "removed $SERVICE service, udev rule and $PREFIX"
    if [ "$PURGE" -eq 1 ]; then
        rm -rf "$STATE_DIR" "$CACHE_DIR"
        if id "$SVC_USER" >/dev/null 2>&1; then userdel "$SVC_USER" || true; fi
        say "purged $STATE_DIR, $CACHE_DIR and user $SVC_USER"
    else
        say "kept project data in $STATE_DIR (add --purge to delete it)"
    fi
}

if [ "$UNINSTALL" -eq 1 ]; then
    uninstall
    exit 0
fi

# ── prerequisites ────────────────────────────────────────────────────────
[ -n "$SRC" ] || die "piped install: add --release latest (or --release v<ver>), e.g. curl -fsSL …/install.sh | sudo bash -s -- --release latest"
[ -f "$SRC/desktop/shared/parent_server.py" ] \
    || die "run from a SlyLED source tree or an extracted release tarball ($SRC), or use --release"
command -v python3 >/dev/null || die "python3 not found"
python3 - <<'PY' || die "python3 >= 3.10 required (found $(python3 --version 2>&1))"
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

if command -v apt-get >/dev/null; then
    # python3-venv: Debian/Ubuntu split venv+ensurepip out of python3.
    # libportaudio2: sounddevice's native library (local audio brightness).
    need=()
    python3 -c 'import ensurepip, venv' 2>/dev/null || need+=(python3-venv)
    dpkg -s libportaudio2 >/dev/null 2>&1 || need+=(libportaudio2)
    if [ "${#need[@]}" -gt 0 ]; then
        say "apt-get install ${need[*]}"
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${need[@]}" >/dev/null
    fi
else
    python3 -c 'import ensurepip, venv' 2>/dev/null \
        || die "python3 venv/ensurepip missing — install your distro's python3-venv package"
    say "not a Debian/Ubuntu host: install PortAudio (libportaudio) yourself for local audio"
fi

# ── service user ─────────────────────────────────────────────────────────
if ! id "$SVC_USER" >/dev/null 2>&1; then
    nologin=$(command -v nologin || echo /usr/sbin/nologin)
    useradd --system --user-group --home-dir "$STATE_DIR" --no-create-home \
            --shell "$nologin" "$SVC_USER"
    say "created system user $SVC_USER"
fi

NEW_VER=$(tree_version "$SRC")
OLD_VER=""; [ -f "$PREFIX/VERSION" ] && OLD_VER=$(tr -d ' \n' < "$PREFIX/VERSION")
if [ -z "$OLD_VER" ] && [ -f "$PREFIX/desktop/shared/parent_server.py" ]; then
    OLD_VER=$(tree_version "$PREFIX")
fi
if [ -z "$OLD_VER" ]; then say "installing v${NEW_VER:-?}"
elif [ "$OLD_VER" = "$NEW_VER" ]; then say "same version (v$NEW_VER), reinstalling"
else say "upgrading v$OLD_VER → v${NEW_VER:-?}"; fi

if have_systemd && systemctl is-active --quiet "$SERVICE"; then
    say "stopping running $SERVICE for upgrade"
    systemctl stop "$SERVICE"
fi

# ── code ─────────────────────────────────────────────────────────────────
# Only what the server reads at runtime: the Python + SPA, the manual (/help,
# /api/glossary), the firmware registry and the camera-node sources the
# Firmware tab deploys. No .git, so app_dirs never writes into this tree.
mkdir -p "$PREFIX"
staging=$(mktemp -d "$PREFIX/.staging.XXXXXX")
trap 'rm -rf "$staging"' EXIT
paths=(desktop/shared desktop/linux firmware/registry.json firmware/orangepi)
for p in docs/help docs/schema docs/build docs/USER_MANUAL.md docs/USER_MANUAL_fr.md \
         docs/USER_MANUAL.pdf docs/USER_MANUAL_fr.pdf \
         docs/USER_MANUAL.docx docs/USER_MANUAL_fr.docx; do
    [ -e "$SRC/$p" ] && paths+=("$p")
done
# firmware/orangepi/models is gitignored camera-model bulk (~200 MB) that a
# dirty checkout would otherwise sweep into /opt (#962).
tar -C "$SRC" --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='desktop/shared/data' --exclude='firmware/orangepi/models' \
    -cf - "${paths[@]}" | tar -C "$staging" -xf -
rm -rf "$PREFIX/desktop" "$PREFIX/docs" "$PREFIX/firmware"
mv "$staging/desktop" "$staging/firmware" "$PREFIX/"
[ -d "$staging/docs" ] && mv "$staging/docs" "$PREFIX/"
printf '%s\n' "${NEW_VER:-unknown}" > "$PREFIX/VERSION"
say "code v${NEW_VER:-?} installed to $PREFIX from $SRC"

# ── virtualenv ───────────────────────────────────────────────────────────
# Rebuilt only when missing or the interpreter changed; pip then upgrades in
# place. No --only-binary: esptool is published as an sdist.
venv_py="$PREFIX/.venv/bin/python"
sys_ver=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if [ -x "$venv_py" ] && [ "$("$venv_py" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" = "$sys_ver" ]; then
    say "reusing $PREFIX/.venv (python $sys_ver)"
else
    rm -rf "$PREFIX/.venv"
    python3 -m venv "$PREFIX/.venv"
    say "created $PREFIX/.venv (python $sys_ver)"
fi
"$venv_py" -m pip install -q --disable-pip-version-check --upgrade pip
"$venv_py" -m pip install -q --disable-pip-version-check \
    -r "$PREFIX/desktop/linux/requirements.txt"
"$venv_py" -m compileall -q "$PREFIX/desktop/shared" >/dev/null || true
chown -R root:root "$PREFIX"
chmod -R go-w "$PREFIX"

# ── system integration ───────────────────────────────────────────────────
install -D -m 0644 "$PREFIX/desktop/linux/slyled.service" "$UNIT_DST"
install -D -m 0644 "$PREFIX/desktop/linux/99-slyled-usb.rules" "$UDEV_DST"
if command -v udevadm >/dev/null; then
    udevadm control --reload || true
    udevadm trigger --subsystem-match=tty --subsystem-match=usb || true
fi

if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q '^Status: active'; then
    for rule in "$PORT/tcp" 4210/udp 4211/udp 5568/udp 6454/udp; do
        ufw allow "$rule" comment 'SlyLED' >/dev/null
    done
    say "ufw: allowed $PORT/tcp 4210/udp 4211/udp 5568/udp 6454/udp"
elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
    for rule in "$PORT/tcp" 4210/udp 4211/udp 5568/udp 6454/udp; do
        firewall-cmd --quiet --permanent --add-port="$rule"
    done
    firewall-cmd --quiet --reload
    say "firewalld: opened $PORT/tcp 4210/udp 4211/udp 5568/udp 6454/udp"
else
    say "no active ufw/firewalld; if a firewall is added later, open $PORT/tcp and UDP 4210 4211 5568 6454"
fi

if ! have_systemd; then
    say "systemd is not running here — installed but not started."
    say "run: sudo -u $SVC_USER env XDG_DATA_HOME=$STATE_DIR $venv_py $PREFIX/desktop/shared/parent_server.py --no-browser --port $PORT"
    exit 0
fi

systemctl daemon-reload
systemctl enable --now "$SERVICE"

say "waiting for http://127.0.0.1:$PORT/status ..."
if "$venv_py" - "$PORT" <<'PY'
import sys, time, urllib.request
url = f"http://127.0.0.1:{sys.argv[1]}/status"
for _ in range(60):
    try:
        urllib.request.urlopen(url, timeout=2).read()
        sys.exit(0)
    except Exception:
        time.sleep(1)
sys.exit(1)
PY
then
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    say "running — open http://${ip:-<this-host>}:$PORT from a browser on the LAN"
    say "logs: journalctl -u $SERVICE -f    data: $STATE_DIR/SlyLED/data"
else
    journalctl -u "$SERVICE" -n 30 --no-pager || true
    die "$SERVICE did not answer on port $PORT within 60 s (journal above)"
fi
