# Orange Pi 4A — Camera Node Setup

## Hardware
- **Board:** Orange Pi 4A (Allwinner T527, 2 TOPS NPU)
- **RAM:** 2 GB
- **Storage:** 32 GB SD card
- **Role:** Camera node for SlyLED issue #195 — object detection / people tracking

## Working Image
**Official Orange Pi Ubuntu Server 22.04 (Jammy)**
- Filename: `Orangepi4a_1.0.4_ubuntu_jammy_server_linux5.15.147.img`
- Kernel: 5.15.147-sun55iw3
- Download: Google Drive `1I7anca2Y7RXn8-bkTEE0O-EhtLblMVyn`
  - `gdown 1I7anca2Y7RXn8-bkTEE0O-EhtLblMVyn` (may be rate-limited; use browser instead)
- **HDMI works, SSH works, WiFi works**

## Images Tried (for reference)
| Image | Result |
|-------|--------|
| Armbian unofficial bookworm (self-built, WSL) | u-boot ran, Linux started but never got DHCP lease |
| Armbian community trixie 26.2.0-trunk.668 | u-boot did not run, no boot at all |
| **Official OrangePi Ubuntu Jammy 1.0.4** | ✅ Boots, HDMI, SSH, WiFi all working |

## Network
- Ethernet: `192.168.10.216` (DHCP, may change)
- WiFi: `192.168.10.235` (DHCP, may change — connected to `ktown`)
- Hostname: `orangepi4a`
- SSH: `root@192.168.10.216` or `root@192.168.10.235`
- SSH key: `~/.ssh/id_ed25519` (GPD-Dave)

## Default Credentials
- Username: `root`
- Password: `orangepi` (change after setup)

## Flashing
1. Download image to `C:\temp\`
2. Run `flash.ps1` from an **elevated** PowerShell:
   ```powershell
   Set-ExecutionPolicy Bypass -Scope Process -Force; & '.\flash.ps1'
   ```
3. Insert SD card into Orange Pi 4A and power on
4. Connect via SSH over ethernet (check OPNsense DHCP leases at 192.168.10.1)

## WiFi Setup (first boot, over ethernet)
```bash
nmcli dev wifi connect ktown password <password>
```

## Windows Flashing Notes
- Must run PowerShell as Administrator
- `diskpart clean` is required before writing — Windows blocks raw writes to removable media with mounted volumes even as admin
- SD card disk number changes between insertions — always verify with `Get-Disk` first
- OPNsense API used to watch for DHCP leases: `https://192.168.10.1/api/dhcpv4/leases/searchLease`

## System Info (as of 2026-04-05)
```
Linux orangepi4a 5.15.147-sun55iw3 #1.0.4 SMP PREEMPT
RAM:  1.9 GB total, ~1.7 GB free
Disk: 29 GB total, 27 GB free (auto-expanded)
```

## Next Steps
- [ ] Change root password
- [ ] Set static IP or DHCP reservation in OPNsense
- [ ] Install camera stack (OpenCV, Python, YOLO/MobileNet)
- [ ] Integrate with SlyLED orchestrator (POST stage objects to `/api/children`)
- [ ] Set up NPU inference pipeline for people tracking
