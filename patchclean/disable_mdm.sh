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

# Create backup directory
log "Creating backup directory..."
BACKUP_DIR="/tmp/mdm_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Backup original files
log "Creating backups..."
SETUP_ASSISTANT="$SYSTEM_ROOT/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant"
if [ -f "$SETUP_ASSISTANT" ]; then
    if cp "$SETUP_ASSISTANT" "$BACKUP_DIR/Setup_Assistant_original" 2>/dev/null; then
        log "✓ Setup Assistant backed up"
    else
        log "⚠ Could not backup Setup Assistant"
    fi
else
    log "⚠ Setup Assistant not found at: $SETUP_ASSISTANT"
fi

# Stop MDM-related services first
log "Stopping MDM-related services..."
MDM_SERVICES=(
    "com.apple.ManagedClient.enroll"
    "com.apple.ManagedClient"
    "com.apple.mdmclient.daemon.runatboot"
    "com.apple.mdmclient.daemon"
    "com.apple.mbsystemadministration"
    "com.apple.mbusertrampoline"
    "com.apple.betaenrollmentd"
)

for service in "${MDM_SERVICES[@]}"; do
    if launchctl list | grep -q "$service"; then
        log "Stopping service: $service"
        launchctl stop "$service" 2>/dev/null || log "⚠ Could not stop $service"
        launchctl unload "/System/Library/LaunchDaemons/${service}.plist" 2>/dev/null || log "⚠ Could not unload $service"
    fi
done

# Disable MDM launch daemons
log "Disabling MDM launch daemons..."
MDM_PLISTS=(
    "$SYSTEM_ROOT/System/Library/LaunchDaemons/com.apple.ManagedClient.enroll.plist"
    "$SYSTEM_ROOT/System/Library/LaunchDaemons/com.apple.ManagedClient.plist"
    "$SYSTEM_ROOT/System/Library/LaunchDaemons/com.apple.mdmclient.daemon.runatboot.plist"
    "$SYSTEM_ROOT/System/Library/LaunchDaemons/com.apple.mdmclient.daemon.plist"
    "$SYSTEM_ROOT/System/Library/LaunchDaemons/com.apple.mbsystemadministration.plist"
    "$SYSTEM_ROOT/System/Library/LaunchDaemons/com.apple.mbusertrampoline.plist"
    "$SYSTEM_ROOT/System/Library/LaunchDaemons/com.apple.betaenrollmentd.plist"
)

for plist in "${MDM_PLISTS[@]}"; do
    if [ -f "$plist" ]; then
        plist_name=$(basename "$plist")

        # Backup original
        if cp "$plist" "$BACKUP_DIR/$plist_name" 2>/dev/null; then
            log "Backed up $plist_name"
        else
            log "⚠ Could not backup $plist_name"
        fi

        # Rename to disable
        if mv "$plist" "${plist}.disabled" 2>/dev/null; then
            log "✓ Disabled $plist_name"
        else
            log "✗ Failed to disable $plist_name (may require remounting as writable)"

            # Try remounting as writable
            log "Attempting to remount root as writable..."
            if mount -uw / 2>/dev/null; then
                log "✓ Root remounted as writable"
                if mv "$plist" "${plist}.disabled" 2>/dev/null; then
                    log "✓ Disabled $plist_name after remount"
                else
                    log "✗ Still failed to disable $plist_name"
                fi
            else
                log "✗ Failed to remount root as writable"
            fi
        fi
    else
        log "⚠ File not found: $plist"
    fi
done

# Modify Setup Assistant to block ForceMDMEnroll
log "Modifying Setup Assistant binary..."
if [ -f "$SETUP_ASSISTANT" ]; then
    # Create a wrapper script that blocks ForceMDMEnroll
    if mv "$SETUP_ASSISTANT" "${SETUP_ASSISTANT}.original" 2>/dev/null; then
        cat > "$SETUP_ASSISTANT" << 'EOF'
#!/bin/bash
# Modified Setup Assistant that blocks ForceMDMEnroll
ORIGINAL_BINARY="${0}.original"
ARGS=()
BLOCK_EXECUTION=false

# Check for MDM-related arguments
for arg in "$@"; do
    case "$arg" in
        -ForceMDMEnroll|*ForceMDMEnroll*)
            BLOCK_EXECUTION=true
            echo "Setup Assistant blocked: ForceMDMEnroll detected"
            echo "MDM enrollment has been disabled by system modification"
            ;;
        -MiniBuddyYes)
            BLOCK_EXECUTION=true
            echo "Setup Assistant blocked: MiniBuddyYes detected"
            echo "Automated setup has been disabled by system modification"
            ;;
        *)
            ARGS+=("$arg")
            ;;
    esac
done

# If blocking flags detected, exit without running Setup Assistant
if [ "$BLOCK_EXECUTION" = true ]; then
    echo "Setup Assistant execution blocked to prevent forced MDM enrollment"
    echo "If you need to run setup manually, use: ${ORIGINAL_BINARY}"
    exit 0
fi

# Execute original binary only if no blocking flags present
exec "$ORIGINAL_BINARY" "${ARGS[@]}"
EOF

        if chmod +x "$SETUP_ASSISTANT" 2>/dev/null; then
            log "✓ Setup Assistant modified to block ForceMDMEnroll execution"
        else
            log "✗ Could not set executable permissions on modified Setup Assistant"
        fi
    else
        log "✗ Could not modify Setup Assistant (may require remounting as writable)"

        # Try remounting as writable
        log "Attempting to remount root as writable..."
        if mount -uw / 2>/dev/null; then
            log "✓ Root remounted as writable"
            if mv "$SETUP_ASSISTANT" "${SETUP_ASSISTANT}.original" 2>/dev/null; then
                # Recreate the wrapper script
                cat > "$SETUP_ASSISTANT" << 'EOF'
#!/bin/bash
# Modified Setup Assistant that blocks ForceMDMEnroll
ORIGINAL_BINARY="${0}.original"
ARGS=()
BLOCK_EXECUTION=false

for arg in "$@"; do
    case "$arg" in
        -ForceMDMEnroll|*ForceMDMEnroll*|-MiniBuddyYes)
            BLOCK_EXECUTION=true
            echo "Setup Assistant blocked: MDM enrollment flag detected"
            ;;
        *)
            ARGS+=("$arg")
            ;;
    esac
done

if [ "$BLOCK_EXECUTION" = true ]; then
    echo "Setup Assistant execution blocked to prevent forced MDM enrollment"
    exit 0
fi

exec "$ORIGINAL_BINARY" "${ARGS[@]}"
EOF
                chmod +x "$SETUP_ASSISTANT" && log "✓ Setup Assistant modified after remount"
            else
                log "✗ Still failed to modify Setup Assistant"
            fi
        else
            log "✗ Failed to remount root as writable"
        fi
    fi
else
    log "⚠ Setup Assistant binary not found"
fi

# Remove current MDM enrollment
log "Removing current MDM enrollment..."
if command -v profiles >/dev/null 2>&1; then
    # Remove all configuration profiles
    profiles -P 2>/dev/null | while read -r profile_id; do
        if [ -n "$profile_id" ]; then
            if profiles -R -p "$profile_id" 2>/dev/null; then
                log "✓ Removed profile: $profile_id"
            else
                log "⚠ Could not remove profile: $profile_id"
            fi
        fi
    done
else
    log "⚠ profiles command not available"
fi

# Remove MDM configuration profiles manually
log "Removing MDM configuration profiles..."
PROFILES_DIR="/var/db/ConfigurationProfiles"
if [ -d "$PROFILES_DIR" ]; then
    # Backup profiles
    if cp -r "$PROFILES_DIR" "$BACKUP_DIR/ConfigurationProfiles" 2>/dev/null; then
        log "✓ Configuration profiles backed up"
    else
        log "⚠ Could not backup configuration profiles"
    fi

    # Remove MDM-related profiles
    removed_count=0
    for profile in "$PROFILES_DIR"/*.configprofile "$PROFILES_DIR"/*.plist; do
        if [ -f "$profile" ]; then
            if rm -f "$profile" 2>/dev/null; then
                ((removed_count++))
            fi
        fi
    done
    log "✓ Removed $removed_count configuration profiles"

    # Also remove Settings directory if it exists
    if [ -d "$PROFILES_DIR/Settings" ]; then
        rm -rf "$PROFILES_DIR/Settings" 2>/dev/null && log "✓ Removed profile settings"
    fi
else
    log "⚠ Configuration profiles directory not found"
fi

# Clear MDM-related preferences
log "Clearing MDM preferences..."
MDM_PREFS=(
    "/Library/Preferences/com.apple.mdmclient.plist"
    "/Library/Preferences/com.apple.ManagedClient.plist"
    "/Library/Managed Preferences"
    "/private/var/db/ConfigurationProfiles"
)

for pref in "${MDM_PREFS[@]}"; do
    if [ -e "$pref" ]; then
        pref_name=$(basename "$pref")
        safe_name=$(echo "$pref_name" | tr ' ' '_')

        # Backup
        if cp -r "$pref" "$BACKUP_DIR/$safe_name" 2>/dev/null; then
            log "✓ Backed up $pref_name"
        else
            log "⚠ Could not backup $pref_name"
        fi

        # Remove
        if rm -rf "$pref" 2>/dev/null; then
            log "✓ Removed $pref_name"
        else
            log "⚠ Could not remove $pref_name"
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
            else
                log "⚠ Could not remove MDM preferences for user: $user_name"
            fi
        fi
    fi
done

# Create restoration script
log "Creating restoration script..."
cat > "$BACKUP_DIR/restore_mdm.sh" << EOF
#!/bin/bash
# Script to restore MDM functionality if needed
# Run with sudo in normal macOS

if [ "\$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run with sudo"
    exit 1
fi

echo "WARNING: This will restore MDM enrollment functionality"
echo "Press Enter to continue or Ctrl+C to abort..."
read

BACKUP_DIR="\$(dirname "\$0")"

# Restore launch daemons
for file in "\$BACKUP_DIR"/*.plist; do
    if [ -f "\$file" ]; then
        filename=\$(basename "\$file")
        target_path="/System/Library/LaunchDaemons/\$filename"

        # Remove .disabled version if it exists
        [ -f "\${target_path}.disabled" ] && rm -f "\${target_path}.disabled"

        # Restore original
        if cp "\$file" "\$target_path" 2>/dev/null; then
            echo "Restored \$filename"
            # Load the service
            launchctl load "\$target_path" 2>/dev/null || echo "Could not load \$filename"
        fi
    fi
done

# Restore Setup Assistant
if [ -f "\$BACKUP_DIR/Setup_Assistant_original" ]; then
    setup_path="/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant"

    # Remove wrapper files
    rm -f "\${setup_path}.original"

    # Restore original
    if cp "\$BACKUP_DIR/Setup_Assistant_original" "\$setup_path" 2>/dev/null; then
        chmod +x "\$setup_path"
        echo "Restored Setup Assistant"
    fi
fi

# Restore profiles
if [ -d "\$BACKUP_DIR/ConfigurationProfiles" ]; then
    mkdir -p "/var/db/ConfigurationProfiles"
    cp -r "\$BACKUP_DIR/ConfigurationProfiles/"* "/var/db/ConfigurationProfiles/" 2>/dev/null
    echo "Restored configuration profiles"
fi

# Restore preferences
for pref_backup in "\$BACKUP_DIR"/com.apple.*.plist; do
    if [ -f "\$pref_backup" ]; then
        pref_name=\$(basename "\$pref_backup")
        if cp "\$pref_backup" "/Library/Preferences/\$pref_name" 2>/dev/null; then
            echo "Restored \$pref_name"
        fi
    fi
done

# Restore managed preferences directory
if [ -d "\$BACKUP_DIR/Managed_Preferences" ]; then
    mkdir -p "/Library/Managed Preferences"
    cp -r "\$BACKUP_DIR/Managed_Preferences/"* "/Library/Managed Preferences/" 2>/dev/null
    echo "Restored Managed Preferences"
fi

echo "MDM functionality restored. Restart required."
EOF

chmod +x "$BACKUP_DIR/restore_mdm.sh"

# Clear system caches
log "Clearing system caches..."
if command -v kextcache >/dev/null 2>&1; then
    kextcache -clear-staging 2>/dev/null && log "✓ Cleared kext staging cache"
fi

# Reset launch services database
if command -v lsregister >/dev/null 2>&1; then
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -kill -r -domain local -domain system -domain user 2>/dev/null && log "✓ Reset launch services database"
fi

# Final summary
log "=== MDM Removal Complete ==="
log "✓ MDM services stopped and disabled"
log "✓ MDM launch daemons disabled"
log "✓ Setup Assistant modified to block forced enrollment"
log "✓ Configuration profiles removed"
log "✓ MDM preferences cleared"
log "✓ User-level MDM preferences cleared"
log "✓ System caches cleared"
log "✓ Backup created at: $BACKUP_DIR"
log ""
log "IMPORTANT NEXT STEPS:"
log "1. Restart your Mac to complete the process"
log "2. The Setup Assistant should no longer force MDM enrollment"
log "3. If you need to restore MDM later, run: sudo $BACKUP_DIR/restore_mdm.sh"
log "4. Consider re-enabling SIP for better security: csrutil enable (in Recovery Mode)"
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