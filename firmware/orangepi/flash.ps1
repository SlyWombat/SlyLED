
# Orange Pi 4A SD Card Flasher
# Run from an elevated (admin) PowerShell:
#   Set-ExecutionPolicy Bypass -Scope Process -Force; & '.\flash.ps1'
#
# Place the .img file in the same directory or update $imgPath below.
# Uses diskpart clean to release Windows volume locks before writing.

Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public class RawDisk {
    const uint GENERIC_READ        = 0x80000000;
    const uint GENERIC_WRITE       = 0x40000000;
    const uint FILE_SHARE_READ     = 0x00000001;
    const uint FILE_SHARE_WRITE    = 0x00000002;
    const uint OPEN_EXISTING       = 3;
    const uint FILE_FLAG_SEQ       = 0x08000000;

    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    static extern SafeFileHandle CreateFile(string path, uint access, uint share,
        IntPtr sa, uint disposition, uint flags, IntPtr tmpl);

    public static FileStream Open(string path) {
        var h = CreateFile(path, GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE, IntPtr.Zero,
            OPEN_EXISTING, FILE_FLAG_SEQ, IntPtr.Zero);
        if (h.IsInvalid) throw new System.ComponentModel.Win32Exception();
        return new FileStream(h, FileAccess.ReadWrite, 4 * 1024 * 1024);
    }
}
'@ -ErrorAction SilentlyContinue

# --- CONFIGURE THESE ---
$imgPath  = 'C:\temp\Orangepi4a_1.0.4_ubuntu_jammy_server_linux5.15.147.img'
$diskNum  = 1   # verify with: Get-Disk | Format-Table Number, FriendlyName, Size
$diskPath = "\\.\PhysicalDrive$diskNum"
# -----------------------

# Verify disk before proceeding
$disk = Get-Disk -Number $diskNum
Write-Host ("Target : Disk $diskNum — " + $disk.FriendlyName + ' (' + [math]::Round($disk.Size/1GB,1) + ' GB)')
Write-Host ('Image  : ' + [math]::Round((Get-Item $imgPath).Length/1GB,2) + ' GB')
$confirm = Read-Host 'Type YES to flash'
if ($confirm -ne 'YES') { Write-Host 'Aborted.'; exit }

Write-Host 'Clearing partition table (diskpart clean)...'
"select disk $diskNum`r`nclean`r`nexit" | diskpart | Out-Null
Start-Sleep -Seconds 2

Write-Host 'Opening disk...'
$reader = [System.IO.File]::OpenRead($imgPath)
$writer = [RawDisk]::Open($diskPath)

$bufferSize = 4MB
$buffer     = New-Object byte[] $bufferSize
$written    = [long]0
$total      = $reader.Length
$lastPct    = -10

Write-Host 'Writing...'
while ($true) {
    $n = $reader.Read($buffer, 0, $bufferSize)
    if ($n -eq 0) { break }
    $writer.Write($buffer, 0, $n)
    $written += $n
    $pct = [int]($written * 100 / $total)
    if (($pct - $lastPct) -ge 10) {
        Write-Host ($pct.ToString() + '% - ' + [math]::Round($written/1GB,2) + ' GB')
        $lastPct = $pct
    }
}
$writer.Flush()
$reader.Close()
$writer.Close()
Write-Host 'Flash complete! Safe to eject.'
