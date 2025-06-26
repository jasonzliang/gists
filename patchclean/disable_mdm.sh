#!/bin/bash

# Script to remove forced MDM enrollment in normal macOS mode
# Prerequisites: SIP must be disabled, run with sudo
#
# IMPORTANT: This script makes system-level changes that could affect
# your device's security and management. Use at your own risk.

set -e  # Exit on any error

echo "=== MDM Removal Script for Normal macOS Mode ==="
echo "Prerequisites: SIP disabled, running with sudo"
echo "WARNING: This will remove MDM enrollment and modify system files"
echo "Press Enter to continue or Ctrl+C to abort..."
read

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run with sudo"
    echo "Usage: sudo $0"
    exit 1
fi

# Check if SIP is disabled
SIP_STATUS=$(csrutil status 2>/dev/null)
if echo "$SIP_STATUS" | grep -q "enabled"; then
    echo "ERROR: System Integrity Protection is enabled"
    echo "SIP must be disabled to modify system files"
    echo "Current status: $SIP_STATUS"
    echo ""
    echo "To disable SIP:"
    echo "1. Restart and hold Cmd+R to enter Recovery Mode"
    echo "2. Open Terminal and run: csrutil disable"
    echo "3. Restart normally and run this script again"
    exit 1
fi

log "SIP Status: $SIP_STATUS"
log "Starting MDM removal process..."

# Use root filesystem since we're in normal mode
SYSTEM_ROOT="/"

# Verify we can see system files
if [ ! -d "$SYSTEM_ROOT/System/Library" ]; then
    log "✗ System files not found at $SYSTEM_ROOT"
    exit 1
fi

log "✓ System files found at $SYSTEM_ROOT"

# Make system volume writable
log "Making system volume writable..."
if mount -uw / 2>/dev/null; then
    log "✓ System volume remounted as writable"
else
    log "⚠ Could not remount as writable - continuing anyway"
fi

# Kill MDM processes aggressively
log "Killing MDM-related processes..."
MDM_PROCESSES=(
    "ManagedClient"
    "mdmclient"
    "mbsystemadministration"
    "mbusertrampoline"
    "betaenrollmentd"
    "Setup Assistant"
)

for process in "${MDM_PROCESSES[@]}"; do
    pids=$(pgrep -f "$process" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        log "Killing $process processes: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || log "⚠ Could not kill some $process processes"
    else
        log "No $process processes found"
    fi
done

# Remove MDM launch daemons
log "Removing MDM launch daemons..."
MDM_PLISTS=(
    "/System/Library/LaunchDaemons/com.apple.ManagedClient.enroll.plist"
    "/System/Library/LaunchDaemons/com.apple.ManagedClient.plist"
    "/System/Library/LaunchDaemons/com.apple.mdmclient.daemon.runatboot.plist"
    "/System/Library/LaunchDaemons/com.apple.mdmclient.daemon.plist"
    "/System/Library/LaunchDaemons/com.apple.mbsystemadministration.plist"
    "/System/Library/LaunchDaemons/com.apple.mbusertrampoline.plist"
    "/System/Library/LaunchDaemons/com.apple.betaenrollmentd.plist"
)

for plist in "${MDM_PLISTS[@]}"; do
    if [ -f "$plist" ]; then
        plist_name=$(basename "$plist")
        log "Removing $plist_name"

        if rm -f "$plist" 2>/dev/null; then
            log "✓ Removed $plist_name"
        else
            log "✗ Failed to remove $plist_name"
        fi
    else
        log "⚠ File not found: $(basename "$plist")"
    fi
done

# Remove Setup Assistant binary
log "Removing Setup Assistant binary..."
SETUP_ASSISTANT="/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant"
if [ -f "$SETUP_ASSISTANT" ]; then
    if rm -f "$SETUP_ASSISTANT" 2>/dev/null; then
        log "✓ Removed Setup Assistant binary"
    else
        log "✗ Failed to remove Setup Assistant binary"
    fi
else
    log "⚠ Setup Assistant binary not found"
fi

# Remove entire Setup Assistant app bundle
SETUP_ASSISTANT_APP="/System/Library/CoreServices/Setup Assistant.app"
if [ -d "$SETUP_ASSISTANT_APP" ]; then
    log "Removing Setup Assistant app bundle..."
    if rm -rf "$SETUP_ASSISTANT_APP" 2>/dev/null; then
        log "✓ Removed Setup Assistant app bundle"
    else
        log "✗ Failed to remove Setup Assistant app bundle"
    fi
fi

# Remove MDM configuration profiles
log "Removing MDM configuration profiles..."
if [ -d "/var/db/ConfigurationProfiles" ]; then
    rm -rf "/var/db/ConfigurationProfiles"/* 2>/dev/null && log "✓ Removed configuration profiles"
fi

# Remove MDM preferences
log "Clearing MDM preferences..."
MDM_PREFS=(
    "/Library/Preferences/com.apple.mdmclient.plist"
    "/Library/Preferences/com.apple.ManagedClient.plist"
    "/Library/Managed Preferences"
    "/private/var/db/ConfigurationProfiles"
)

for pref in "${MDM_PREFS[@]}"; do
    if [ -e "$pref" ]; then
        if rm -rf "$pref" 2>/dev/null; then
            log "✓ Removed $(basename "$pref")"
        else
            log "⚠ Could not remove $(basename "$pref")"
        fi
    fi
done

# Clear user-level MDM preferences for all users
log "Clearing user-level MDM preferences..."
for user_home in /Users/*; do
    if [ -d "$user_home" ] && [ "$(basename "$user_home")" != "Shared" ]; then
        user_name=$(basename "$user_home")
        user_prefs="$user_home/Library/Preferences/com.apple.mdmclient.plist"

        if [ -f "$user_prefs" ]; then
            if rm -f "$user_prefs" 2>/dev/null; then
                log "✓ Removed MDM preferences for user: $user_name"
            fi
        fi
    fi
done

# Remove MDM binaries
log "Removing MDM binaries..."
MDM_BINARIES=(
    "/usr/libexec/mdmclient"
    "/usr/bin/profiles"
    "/System/Library/PrivateFrameworks/ManagedConfiguration.framework"
    "/System/Library/PrivateFrameworks/ConfigurationProfiles.framework"
)

for binary in "${MDM_BINARIES[@]}"; do
    if [ -e "$binary" ]; then
        if rm -rf "$binary" 2>/dev/null; then
            log "✓ Removed $(basename "$binary")"
        else
            log "⚠ Could not remove $(basename "$binary")"
        fi
    fi
done

# Clear system caches
log "Clearing system caches..."
if command -v kextcache >/dev/null 2>&1; then
    kextcache -clear-staging 2>/dev/null && log "✓ Cleared kext staging cache"
fi

# Reset launch services database
if [ -f "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister" ]; then
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -kill -r -domain local -domain system -domain user 2>/dev/null && log "✓ Reset launch services database"
fi

# Kill any remaining MDM processes one more time
log "Final cleanup - killing any remaining MDM processes..."
for process in "${MDM_PROCESSES[@]}"; do
    pids=$(pgrep -f "$process" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill -9 2>/dev/null || true
    fi
done

# Final summary
log "=== MDM Removal Complete ==="
log "✓ MDM processes killed"
log "✓ MDM launch daemons removed"
log "✓ Setup Assistant removed"
log "✓ Configuration profiles removed"
log "✓ MDM preferences cleared"
log "✓ User-level MDM preferences cleared"
log "✓ MDM binaries removed"
log "✓ System caches cleared"
log ""
log "IMPORTANT NEXT STEPS:"
log "1. Restart your Mac to complete the process"
log "2. MDM enrollment should now be completely disabled"
log "3. Consider re-enabling SIP for better security: csrutil enable (in Recovery Mode)"
log ""
log "WARNING: Your system security is reduced with SIP disabled."
log "Re-enable SIP after confirming MDM removal works if possible."

echo ""
echo "Restart now to complete MDM removal? (y/N)"
read -r restart_choice

if [[ "$restart_choice" =~ ^[Yy]$ ]]; then
    log "Restarting system..."
    reboot
else
    log "Restart manually when ready to complete the process."
fi