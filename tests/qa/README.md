# tests/qa — hardware QA harnesses (run by hand, never by CI)

Everything here may talk to **real devices on the house LAN**: the HinksPix PRO at
192.168.10.6 (config pushes that rewrite and reboot it), the Linux bench host kdocker3
(installs a systemd service; it is also the production Ollama host), and live sACN output.

- Do **not** glob this directory from CI or any test runner.
- Hermetic, CI-safe tests belong in `tests/test_*.py` and are wired into
  `.github/workflows/python-tests.yml` explicitly.
- Each harness defaults to read-only or offline mode; writes need an explicit flag
  (`--hw-apply`, `--install`) or are gated on the plan containing the expected rows.

| File | What it drives |
|---|---|
| `qa_943_hinkspix.py` | Wire protocol vs a strict socket fake; `--hw-read` / `--hw-apply` on the unit |
| `qa_945_947_bench.py` | Import → apply → change → apply → restore on the unit (writes, reboots it) |
| `qa_948_linux_bench.py` | Install + verify the headless Linux service on kdocker3 |
| `qa_sacn_solid.py` | Solid colour over unicast E1.31 (run from Windows Python on davebook-5: WSL blocks UDP) |
| `hinkspix_ms160_capture_2026_09_23/` | Factory config captured from the unit, the restore point |
