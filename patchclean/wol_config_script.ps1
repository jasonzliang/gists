# Complete Wake on LAN Configuration Script for Windows 11
# Combines WoL enablement with intelligent USB Selective Suspend management
# Run as Administrator

if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "Administrator privileges required. Please run as Administrator." -ForegroundColor Red
    exit 1
}

Write-Host "=== Complete Wake on LAN Configuration ===" -ForegroundColor Cyan

# Get active Ethernet adapters
$adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" -and $_.PhysicalMediaType -eq "802.3" }

if (-not $adapters) {
    Write-Host "No active Ethernet adapters found!" -ForegroundColor Red
    exit 1
}

# Analyze and configure each adapter
$usbAdapterFound = $false
$configuredAdapters = @()

foreach ($adapter in $adapters) {
    Write-Host "`nConfiguring: $($adapter.Name)" -ForegroundColor Yellow

    $adapterInfo = @{
        Name = $adapter.Name
        Description = $adapter.InterfaceDescription
        MacAddress = $adapter.MacAddress
        IsUSBBased = $false
        WoLEnabled = $false
    }

    # Detect USB-based adapters
    $usbPatterns = @("USB", "DisplayLink", "Surface Ethernet", "ASIX", "Realtek USB")
    foreach ($pattern in $usbPatterns) {
        if ($adapter.InterfaceDescription -match $pattern) {
            $adapterInfo.IsUSBBased = $true
            $usbAdapterFound = $true
            break
        }
    }

    # Additional USB detection via device enumeration
    try {
        $pnpDevice = Get-PnPDevice | Where-Object {
            $_.FriendlyName -like "*$($adapter.InterfaceDescription)*" -and $_.Status -eq "OK"
        } | Select-Object -First 1

        if ($pnpDevice -and $pnpDevice.InstanceId -match "USB\\") {
            $adapterInfo.IsUSBBased = $true
            $usbAdapterFound = $true
        }
    } catch {
        # Fallback: assume PCIe if detection fails
    }

    # Configure Wake on LAN
    try {
        # Enable WoL through PowerShell
        Enable-NetAdapterPowerManagement -Name $adapter.Name -WakeOnMagicPacket -ErrorAction SilentlyContinue

        # Verify WoL status
        $powerMgmt = Get-NetAdapterPowerManagement -Name $adapter.Name -ErrorAction SilentlyContinue
        if ($powerMgmt -and $powerMgmt.WakeOnMagicPacket -eq "Enabled") {
            $adapterInfo.WoLEnabled = $true
            Write-Host "  Wake on LAN: Enabled" -ForegroundColor Green
        } else {
            Write-Host "  Wake on LAN: Configuration attempted" -ForegroundColor Yellow
        }

        # Configure device power management via registry
        $wmiAdapter = Get-WmiObject -Class Win32_NetworkAdapter | Where-Object { $_.GUID -eq $adapter.InterfaceGuid }
        if ($wmiAdapter) {
            $wmiAdapter.SetPowerState(1) | Out-Null
        }

    } catch {
        Write-Host "  Warning: WoL configuration may be incomplete" -ForegroundColor Yellow
    }

    # Display adapter info
    $connectionType = if ($adapterInfo.IsUSBBased) { "USB" } else { "PCIe/Integrated" }
    Write-Host "  Type: $connectionType" -ForegroundColor $(if($adapterInfo.IsUSBBased) {"Red"} else {"Green"})
    Write-Host "  MAC: $($adapterInfo.MacAddress)" -ForegroundColor Cyan

    $configuredAdapters += $adapterInfo
}

# Configure power management settings
Write-Host "`nConfiguring power management..." -ForegroundColor Yellow

try {
    $activeScheme = (powercfg /getactivescheme).Split()[3]

    # Core power settings for WoL
    $powerSettings = @{
        "Allow wake timers" = @("bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d", 1)
        "Allow hybrid sleep" = @("94ac6d29-73ce-41a6-809f-6363ba21b47e", 0)
        "Allow standby states" = @("abfc2519-3608-4c2a-94ea-171b0ed546ab", 1)
    }

    foreach ($setting in $powerSettings.Keys) {
        $guid, $value = $powerSettings[$setting]
        try {
            powercfg /setacvalueindex $activeScheme SUB_SLEEP $guid $value 2>$null
            powercfg /setdcvalueindex $activeScheme SUB_SLEEP $guid $value 2>$null
            Write-Host "  $setting configured" -ForegroundColor Green
        } catch {
            Write-Host "  ${setting}: partial configuration" -ForegroundColor Yellow
        }
    }

    powercfg /setactive $activeScheme

} catch {
    Write-Host "  Power settings: using fallback configuration" -ForegroundColor Yellow
}

# Intelligent USB Selective Suspend configuration
Write-Host "`nConfiguring USB Selective Suspend..." -ForegroundColor Yellow

try {
    $activeScheme = (powercfg /getactivescheme).Split()[3]
    $usbSuspendGuid = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"
    $usbRootGuid = "2a737441-1930-4402-8d77-b2bebba308a3"

    if ($usbAdapterFound) {
        # Disable USB Selective Suspend for USB adapters
        powercfg /setacvalueindex $activeScheme $usbRootGuid $usbSuspendGuid 0
        powercfg /setdcvalueindex $activeScheme $usbRootGuid $usbSuspendGuid 0
        Write-Host "  USB Selective Suspend: DISABLED (USB adapter detected)" -ForegroundColor Red
    } else {
        # Enable USB Selective Suspend for power savings
        powercfg /setacvalueindex $activeScheme $usbRootGuid $usbSuspendGuid 1
        powercfg /setdcvalueindex $activeScheme $usbRootGuid $usbSuspendGuid 1
        Write-Host "  USB Selective Suspend: ENABLED (power saving)" -ForegroundColor Green
    }

    powercfg /setactive $activeScheme

} catch {
    Write-Host "  USB Selective Suspend: configuration failed" -ForegroundColor Yellow
}

# Configure Windows Fast Startup (optional but recommended)
Write-Host "`nChecking Fast Startup..." -ForegroundColor Yellow

try {
    $fastStartupEnabled = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power" -Name "HiberbootEnabled" -ErrorAction SilentlyContinue).HiberbootEnabled

    if ($fastStartupEnabled -eq 1) {
        $choice = Read-Host "Fast Startup is enabled and may interfere with WoL. Disable it? (y/n)"
        if ($choice -eq "y" -or $choice -eq "Y") {
            Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power" -Name "HiberbootEnabled" -Value 0
            Write-Host "  Fast Startup: DISABLED (restart required)" -ForegroundColor Green
        } else {
            Write-Host "  Fast Startup: LEFT ENABLED" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  Fast Startup: Already disabled" -ForegroundColor Green
    }
} catch {
    Write-Host "  Fast Startup: Could not check/modify" -ForegroundColor Yellow
}

# Final configuration summary
Write-Host "`n=== CONFIGURATION SUMMARY ===" -ForegroundColor Cyan

$successCount = 0
foreach ($adapter in $configuredAdapters) {
    Write-Host "`n$($adapter.Name):" -ForegroundColor White
    Write-Host "  MAC Address: $($adapter.MacAddress)" -ForegroundColor Cyan
    Write-Host "  Connection: $(if($adapter.IsUSBBased) {'USB-based'} else {'PCIe/Integrated'})" -ForegroundColor $(if($adapter.IsUSBBased) {"Red"} else {"Green"})
    Write-Host "  Wake on LAN: $(if($adapter.WoLEnabled) {'Verified Enabled'} else {'Configured'})" -ForegroundColor $(if($adapter.WoLEnabled) {"Green"} else {"Yellow"})

    if ($adapter.WoLEnabled) { $successCount++ }
}

Write-Host "`nUSB Selective Suspend: $(if($usbAdapterFound) {'DISABLED'} else {'ENABLED'})" -ForegroundColor $(if($usbAdapterFound) {"Red"} else {"Green"})

# Essential next steps
Write-Host "`n=== IMPORTANT NEXT STEPS ===" -ForegroundColor Yellow
Write-Host "1. Check BIOS/UEFI for 'Wake on LAN' or 'PME' settings" -ForegroundColor White
Write-Host "2. Ensure network cable stays connected during sleep" -ForegroundColor White
Write-Host "3. Test with sleep mode before trying full shutdown" -ForegroundColor White

if ($successCount -gt 0) {
    Write-Host "`nWake on LAN configuration completed successfully!" -ForegroundColor Green
    Write-Host "Use the MAC addresses above to send WoL packets" -ForegroundColor Cyan
} else {
    Write-Host "`nConfiguration completed with warnings. Manual verification recommended." -ForegroundColor Yellow
}

pause
