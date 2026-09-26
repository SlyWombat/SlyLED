# SlyLED orchestrator on Linux (headless)

Runs `desktop/shared/parent_server.py` as the systemd service `slyled` (#948 Phase 2),
or as a container (#962). Supported: Ubuntu 22.04+ / Debian Bookworm+, x86_64 or
aarch64, Python >= 3.10.

## Install from the release tarball (no git)

Each orchestrator release carries `SlyLED-<ver>-linux.tar.gz` and its `.sha256`.

```bash
# one-liner: download the latest release, verify its sha256, install/upgrade
curl -fsSL https://raw.githubusercontent.com/SlyWombat/SlyLED/main/desktop/linux/install.sh \
  | sudo bash -s -- --release latest          # or --release v2.1.7

# or by hand
sha256sum -c SlyLED-2.1.7-linux.tar.gz.sha256
tar xzf SlyLED-2.1.7-linux.tar.gz
sudo bash SlyLED-2.1.7/desktop/linux/install.sh
```

```bash
bash install.sh --version                                    # installed + this tarball's version
sudo bash SlyLED-<ver>/desktop/linux/install.sh --uninstall  # remove service; keep data
sudo bash SlyLED-<ver>/desktop/linux/install.sh --uninstall --purge   # also data + user
```

- **Upgrade in place:** run a newer tarball's `install.sh` (or `--release` again). It says
  `upgrading vX → vY`, stops the service, replaces the code, reuses the venv when the
  system Python is unchanged, upgrades dependencies and restarts. `/var/lib/slyled` is
  never touched. **Roll back** the same way with the older tarball.
- **Online install:** Python packages come from PyPI at install time (one tarball serves
  x86_64 and aarch64). For a pinned/offline install use the Docker image below.
- A source checkout still works: `sudo bash desktop/linux/install.sh` from a clone.
- The tarball is built from git at the release tag by `desktop/linux/build_tarball.py`
  (never from a working tree); `/opt/slyled/VERSION` records what is installed.

| What | Where |
|---|---|
| Code + venv (root-owned, read-only to the service) | `/opt/slyled`, `/opt/slyled/.venv`, `/opt/slyled/VERSION` |
| Projects, settings, logs | `/var/lib/slyled/SlyLED/data` |
| Firmware download cache, add-on runtimes | `/var/lib/slyled/SlyLED/firmware`, `.../runtimes` |
| Unit | `/etc/systemd/system/slyled.service` (runs as `slyled`, group `dialout`) |
| USB permissions | `/etc/udev/rules.d/99-slyled-usb.rules` (every board in `firmware_manager.KNOWN_BOARDS`) |

The install tree has no `.git`, so `app_dirs` never writes into it; the unit points
`XDG_DATA_HOME` at `/var/lib/slyled` (created by `StateDirectory=`).

- **Logs:** `journalctl -u slyled -f`
- **Port:** 8080. To change it, `sudo systemctl edit slyled` and override `ExecStart`.
- **Firewall:** with ufw/firewalld active, the installer opens TCP 8080 and UDP 4210 (performers),
  4211 (Android auto-brightness), 5568 (sACN), 6454 (Art-Net).
- **Network:** discovery/Art-Net/camera scan use every physical NIC (`net_ifaces`); `docker*`,
  `br-*`, `veth*`, `virbr*`, tunnels and VPNs are skipped.
- **One instance per port:** a second headless start on a port that already answers exits 1
  with "already answering on port …".
- **Not included:** the tray icon, and system-audio loopback (local audio uses a capture device).

## Docker (Linux hosts only)

`ghcr.io/slywombat/slyled:<ver>` — amd64 + arm64, non-root (uid 1000), everything
pinned. `:latest` is the newest non-prerelease.

```bash
docker run -d --name slyled --network host --restart unless-stopped \
  -e TZ=America/Toronto -v slyled-data:/var/lib/slyled \
  ghcr.io/slywombat/slyled:2.1.7
```

or with the `docker-compose.yml` attached to each release (repo root copy uses
`${SLYLED_VERSION:-latest}`): `docker compose up -d`.

- **`--network host` is required.** Discovery broadcasts a PING on UDP 4210 to every
  subnet, the HinksPix sweep probes the LAN, Art-Net polls by broadcast and sACN can
  multicast; none of that leaves a bridge network (Docker's NAT doesn't forward broadcasts,
  and inside a bridge the container only sees its 172.x address). In bridge mode with
  `-p 8080:8080` only the web UI, manually-added IPs and unicast-routed DMX work — not
  supported.
- **Linux hosts only.** Docker Desktop (Windows/macOS) runs containers in a VM, so "host"
  is the VM, not your LAN. On Windows use `SlyLED-Setup.exe`.
- **Data:** the named volume at `/var/lib/slyled` holds projects, firmware cache and
  runtimes (same layout as the service). A bind mount must be writable by uid 1000; a
  named volume avoids that (on a Raspberry Pi uid 1000 is usually the first user).
- **Time zone:** set `TZ` (the show scheduler uses its own zone from Settings; `TZ` sets
  the logs' clock).
- **Firewall:** host-mode sockets are subject to the host firewall — open TCP 8080 and
  UDP 4210 4211 5568 6454 (ufw: `sudo ufw allow 8080/tcp` etc.).
- **Port:** `-e SLYLED_PORT=8090` (the healthcheck follows it).
- **Health:** `docker ps` shows `healthy` once `/status` answers.
- **Stop:** `docker stop` sends SIGTERM: DMX output is blacked out and stopped, the
  scheduler's HinksPix hand-off runs, exit 0 (grace 20 s, as the unit).
- **One orchestrator per lighting network.** A second one anywhere on the LAN (a laptop,
  a bench container) is detected and both show a red conflict banner (#966); stop one.
  Peers on a routed VLAN: `SLYLED_PEER_TARGETS=host[,host…]`.
- **Don't run it alongside the `slyled` service** on the same port — the container exits
  1 ("already answering on port 8080").
- **USB flashing (optional):** `--device /dev/ttyUSB0 --group-add <host dialout gid>`
  (`getent group dialout`); install `99-slyled-usb.rules` on the host to keep
  ModemManager off the port. Giga DFU (`arduino-cli`) isn't in the image.
- The depth-runtime add-on isn't bundled. For AI auto-tune point SlyLED at an Ollama on
  another machine — see *AI runtime (remote Ollama)* below.

## AI runtime (remote Ollama)

Camera auto-tune works without AI. The optional AI evaluator needs an Ollama with a vision
model; on a headless box that is usually **another machine** on the LAN (#965). Set it in
**Settings → Advanced → AI Runtime → Runs on: a remote Ollama** (URL, *Test connection*,
*Save*) — or pin it in the deployment, which then wins over Settings:

```bash
sudo systemctl edit slyled
#   [Service]
#   Environment=SLYLED_OLLAMA_URL=http://192.168.10.67:11434
#   Environment=SLYLED_OLLAMA_MODEL=qwen2.5vl:3b      # optional default model
sudo systemctl restart slyled
```

Docker: add `SLYLED_OLLAMA_URL: http://192.168.10.67:11434` under `environment:` in
`docker-compose.yml` (or `-e SLYLED_OLLAMA_URL=…`).

SlyLED never installs, starts, stops or warms up Ollama on a remote, and pulls a model onto
it only when Settings allows remote pulls **and** you confirm that pull. The remote must
listen on the LAN (`OLLAMA_HOST=0.0.0.0`) — Ollama has no authentication and camera frames
cross the network, so keep it LAN-only. If it stops answering, auto-tune falls back to the
CV analyzer; nothing is installed locally.

## Tests

`tests/test_linux_packaging.py` (static contract), `tests/test_linux_install_docker.py
[--image ubuntu:26.04] [--tarball dist/SlyLED-<ver>-linux.tar.gz]` (runs install.sh in a
container, drives the service as `slyled`, and with `--tarball` an in-place `--release`
upgrade), `tests/test_docker_image.py --image <ref>` (the image, host network).
`.github/workflows/linux-package.yml` builds + tests both on PRs and publishes them on
each orchestrator release. Hardware bench: `tests/qa/qa_948_linux_bench.py` (QA only).
