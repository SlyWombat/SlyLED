# SlyLED orchestrator on Linux (headless)

Runs `desktop/shared/parent_server.py` as the systemd service `slyled` (#948 Phase 2).
Supported: Ubuntu 22.04+ / Debian Bookworm+, x86_64 or aarch64, Python >= 3.10.

```bash
sudo bash desktop/linux/install.sh                      # install / upgrade, enable, start
sudo bash desktop/linux/install.sh --uninstall          # remove service; keep data
sudo bash desktop/linux/install.sh --uninstall --purge  # also delete data + user
```

| What | Where |
|---|---|
| Code + venv (root-owned, read-only to the service) | `/opt/slyled`, `/opt/slyled/.venv` |
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
- **Not included:** the tray icon, and system-audio loopback (local audio uses a capture device).

Tests: `tests/test_linux_packaging.py` (static contract) and
`tests/test_linux_install_docker.py [--image ubuntu:26.04]` (runs install.sh in a container,
drives the service as `slyled`). Hardware bench: `tests/qa/qa_948_linux_bench.py` (QA only).
