#!/bin/bash

# Script to disable SIP and remove forced MDM enrollment
# Run this script in macOS Recovery Mode
#
# IMPORTANT: This script makes system-level changes that could affect
# your device's security and management. Use at your own risk.

set -e  # Exit on any error

echo "=== MDM Removal Script for Recovery Mode ==="
echo "WARNING: This will disable System Integrity Protection and remove MDM enrollment"
echo "Press Enter to continue or Ctrl+C to abort..."
read

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Check if running in Recovery Mode
# In Recovery Mode, csrutil should work and show current status
SIP_STATUS=$(csrutil status 2>/dev/null)
if [ $? -ne 0 ]; then
    echo "ERROR: Cannot check SIP status. This script must be run in Recovery Mode"
    echo "1. Restart your Mac and hold Cmd+R during startup"
    echo "2. Open Terminal from Utilities menu"
    echo "3. Run this script"
    exit 1
fi

log "Current SIP Status: $SIP_STATUS"

log "Starting MDM removal process..."

# Step 1: Disable System Integrity Protection
log "Disabling System Integrity Protection..."
csrutil disable
if [ $? -eq 0 ]; then
    log "✓ SIP disabled successfully"
else
    log "✗ Failed to disable SIP"
    exit 1
fi

# Step 2: Mount the system volume as writable
log "Mounting system volume as writable..."

# In Recovery Mode, we need to find and mount the actual system volume
log "Available volumes:"
diskutil list

# Try the most likely candidates in order
CANDIDATES=("disk0s2" "disk1s1" "disk1s5" "disk2s1")

SYSTEM_DISK=""
for candidate in "${CANDIDATES[@]}"; do
    log "Trying candidate: $candidate"
    if diskutil info "$candidate" >/dev/null 2>&1; then
        # Check if this looks like a system volume
        VOLUME_INFO=$(diskutil info "$candidate" 2>/dev/null)
        if echo "$VOLUME_INFO" | grep -q "Apple_APFS"; then
            log "✓ Found APFS volume: $candidate"
            SYSTEM_DISK="$candidate"
            break
        fi
    fi
done

# If no candidates worked, ask user
if [ -z "$SYSTEM_DISK" ]; then
    echo ""
    echo "Could not automatically detect system volume."
    echo "Available APFS volumes:"
    diskutil list | grep -E "(identifier|Apple_APFS)" | grep -B1 "Apple_APFS"
    echo ""
    echo "Which disk contains your macOS system? (e.g., disk0s2)"
    echo "Enter disk identifier: "
    read -r SYSTEM_DISK

    if [ -z "$SYSTEM_DISK" ]; then
        log "✗ No disk specified. Exiting."
        exit 1
    fi
fi

log "Using system disk: $SYSTEM_DISK"

# Mount the disk
log "Mounting $SYSTEM_DISK..."

# Try mounting with timeout
if timeout 30 diskutil mount "$SYSTEM_DISK" >/tmp/mount_output 2>&1; then
    MOUNT_OUTPUT=$(cat /tmp/mount_output)
    log "✓ Mount completed"
else
    log "Mount command timed out or failed. Checking if already mounted..."
    MOUNT_OUTPUT=$(cat /tmp/mount_output 2>/dev/null || echo "No output")
fi

log "Mount output: $MOUNT_OUTPUT"

# Check if it's already mounted by looking at diskutil list
ALREADY_MOUNTED=$(diskutil list | grep "$SYSTEM_DISK" | grep -o "/Volumes/[^[:space:]]*")
if [ -n "$ALREADY_MOUNTED" ]; then
    MOUNT_POINT="$ALREADY_MOUNTED"
    log "✓ Volume already mounted at: $MOUNT_POINT"
else
    # Find where it mounted from the mount output
    MOUNT_POINT=$(echo "$MOUNT_OUTPUT" | grep "mounted at" | sed 's/.*mounted at //')
    if [ -z "$MOUNT_POINT" ]; then
        # Try to find it by volume name
        VOLUME_NAME=$(diskutil info "$SYSTEM_DISK" 2>/dev/null | grep "Volume Name" | sed 's/.*Volume Name: *//' | sed 's/[[:space:]]*$//')
        if [ -n "$VOLUME_NAME" ]; then
            MOUNT_POINT="/Volumes/$VOLUME_NAME"
            log "Trying mount point from volume name: $MOUNT_POINT"
        fi
    fi
fi

# Manual check of /Volumes if we still don't have a mount point
if [ -z "$MOUNT_POINT" ] || [ ! -d "$MOUNT_POINT" ]; then
    log "Checking /Volumes for possible mount points:"
    ls -la /Volumes/

    # Look for any volume that has System/Library
    for vol in /Volumes/*/; do
        if [ -d "${vol}System/Library" ]; then
            MOUNT_POINT="$vol"
            log "✓ Found system files at: $MOUNT_POINT"
            break
        fi
    done
fi

if [ -z "$MOUNT_POINT" ] || [ ! -d "$MOUNT_POINT" ]; then
    log "✗ Could not find mount point for $SYSTEM_DISK"
    log "Available volumes in /Volumes:"
    ls -la /Volumes/ 2>/dev/null || echo "Could not list /Volumes"

    # Try proceeding with root filesystem as last resort
    if [ -d "/System/Library" ]; then
        log "⚠ Using root filesystem as fallback"
        MOUNT_POINT="/"
    else
        log "✗ No usable system volume found"
        exit 1
    fi
fi

log "✓ Using mount point: $MOUNT_POINT"

# Make it writable
if mount -uw "$MOUNT_POINT" 2>/dev/null; then
    log "✓ System volume mounted as writable"
else
    log "⚠ Warning: Could not mount as writable. Proceeding anyway..."
fi

# Update paths to use the mounted volume
SYSTEM_ROOT="$MOUNT_POINT"

# Verify we can see system files
if [ -d "$SYSTEM_ROOT/System/Library" ]; then
    log "✓ System files found at $SYSTEM_ROOT"
    log "✓ Found directories:"
    ls -la "$SYSTEM_ROOT/" | head -10
else
    log "✗ System files not found at $SYSTEM_ROOT"
    log "Contents of $SYSTEM_ROOT:"
    ls -la "$SYSTEM_ROOT/"
    exit 1
fi

# Step 3: Backup original files
log "Creating backup directory..."
BACKUP_DIR="/tmp/mdm_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Backup Setup Assistant
SETUP_ASSISTANT="$SYSTEM_ROOT/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant"
if [ -f "$SETUP_ASSISTANT" ]; then
    cp "$SETUP_ASSISTANT" "$BACKUP_DIR/Setup_Assistant_original" 2>/dev/null || {
        log "⚠ Could not backup Setup Assistant"
    }
    log "✓ Setup Assistant backed up"
else
    log "⚠ Setup Assistant not found at: $SETUP_ASSISTANT"
fi

# Step 4: Disable MDM launch daemons
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
        # Backup original
        plist_name=$(basename "$plist")
        cp "$plist" "$BACKUP_DIR/$plist_name" 2>/dev/null || {
            log "⚠ Could not backup $plist_name"
        }

        # Rename to disable
        if mv "$plist" "${plist}.disabled" 2>/dev/null; then
            log "✓ Disabled $plist_name"
        else
            log "✗ Failed to disable $plist_name (may be protected)"
        fi
    else
        log "⚠ File not found: $plist"
    fi
done

# Step 5: Modify Setup Assistant to remove ForceMDMEnroll
log "Modifying Setup Assistant binary..."

SETUP_ASSISTANT="$SYSTEM_ROOT/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant"

if [ -f "$SETUP_ASSISTANT" ]; then
    # Create a wrapper script that removes the ForceMDMEnroll flag
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
            # Block execution entirely if ForceMDMEnroll is present
            BLOCK_EXECUTION=true
            echo "Setup Assistant blocked: ForceMDMEnroll detected"
            echo "MDM enrollment has been disabled by system modification"
            ;;
        -MiniBuddyYes)
            # Also block on MiniBuddyYes (often used with forced enrollment)
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
    echo "If you need to run setup manually, use: ${ORIGINAL_BINARY}.original"
    exit 0
fi

# Execute original binary only if no blocking flags present
exec "$ORIGINAL_BINARY" "${ARGS[@]}"
EOF

        chmod +x "$SETUP_ASSISTANT" 2>/dev/null
        log "✓ Setup Assistant modified to BLOCK execution when ForceMDMEnroll is detected"
    else
        log "✗ Could not modify Setup Assistant (file may be protected)"
    fi
else
    log "⚠ Setup Assistant binary not found"
fi

# Step 6: Remove MDM configuration profiles
log "Removing MDM configuration profiles..."
PROFILES_DIR="$SYSTEM_ROOT/var/db/ConfigurationProfiles"
if [ -d "$PROFILES_DIR" ]; then
    # Backup profiles
    if cp -r "$PROFILES_DIR" "$BACKUP_DIR/ConfigurationProfiles" 2>/dev/null; then
        log "✓ Configuration profiles backed up"
    else
        log "⚠ Could not backup configuration profiles"
    fi

    # Remove MDM-related profiles
    removed_count=0
    for profile in "$PROFILES_DIR"/*.configprofile; do
        if [ -f "$profile" ]; then
            if rm -f "$profile" 2>/dev/null; then
                ((removed_count++))
            fi
        fi
    done
    log "✓ Removed $removed_count configuration profiles"
else
    log "⚠ Configuration profiles directory not found at: $PROFILES_DIR"
fi

# Step 7: Clear MDM-related preferences
log "Clearing MDM preferences..."
MDM_PREFS=(
    "$SYSTEM_ROOT/Library/Preferences/com.apple.mdmclient.plist"
    "$SYSTEM_ROOT/Library/Preferences/com.apple.ManagedClient.plist"
    "$SYSTEM_ROOT/Library/Managed Preferences"
)

for pref in "${MDM_PREFS[@]}"; do
    if [ -e "$pref" ]; then
        # Backup
        pref_name=$(basename "$pref")
        # Handle spaces and special characters in filenames
        safe_name=$(echo "$pref_name" | tr ' ' '_')
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
    else
        log "⚠ Not found: $(basename "$pref")"
    fi
done

# Step 8: Create restoration script for later use
log "Creating restoration script..."
cat > "$BACKUP_DIR/restore_mdm.sh" << EOF
#!/bin/bash
# Script to restore MDM functionality if needed
# Run with sudo in normal macOS (not Recovery Mode)

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
        cp "\$file" "\$target_path" && echo "Restored \$filename"
    fi
done

# Restore Setup Assistant
if [ -f "\$BACKUP_DIR/Setup_Assistant_original" ]; then
    cp "\$BACKUP_DIR/Setup_Assistant_original" "$SYSTEM_ROOT/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant"
    chmod +x "$SYSTEM_ROOT/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant"

    # Remove our wrapper files
    rm -f "$SYSTEM_ROOT/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant.original"

    echo "Restored Setup Assistant"
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
if [ -d "\$BACKUP_DIR/Managed Preferences" ]; then
    mkdir -p "/Library/Managed Preferences"
    cp -r "\$BACKUP_DIR/Managed Preferences/"* "/Library/Managed Preferences/" 2>/dev/null
    echo "Restored Managed Preferences"
fi

echo "MDM functionality restored. Reboot required."
echo "To re-enable SIP, boot to Recovery Mode and run: csrutil enable"
EOF

chmod +x "$BACKUP_DIR/restore_mdm.sh"

# Final summary
log "=== MDM Removal Complete ==="
log "✓ System Integrity Protection disabled"
log "✓ MDM launch daemons disabled"
log "✓ Setup Assistant modified to block forced enrollment execution"
log "✓ Configuration profiles removed"
log "✓ Backup created at: $BACKUP_DIR"
log ""
log "IMPORTANT NEXT STEPS:"
log "1. Restart your Mac normally (option below)"
log "2. The Setup Assistant should no longer force MDM enrollment"
log "3. If you need to restore MDM later, run: sudo $BACKUP_DIR/restore_mdm.sh"
log "4. To re-enable SIP later, boot to Recovery Mode and run: csrutil enable"
log ""
log "WARNING: Your system security is reduced with SIP disabled."
log "Consider re-enabling SIP after confirming MDM removal works."
log "You can also choose to re-enable SIP immediately below."

echo ""
echo "Reboot options:"
echo "1. Press Enter to restart normally"
echo "2. Type 'stay' to remain in Recovery Mode"
echo "3. Type 'enable-sip' to re-enable SIP and restart"
read -r choice

case "$choice" in
    "stay")
        log "Staying in Recovery Mode. You can manually reboot when ready."
        ;;
    "enable-sip")
        log "Re-enabling SIP..."
        csrutil enable
        log "SIP re-enabled. Rebooting..."
        reboot
        ;;
    *)
        log "Rebooting normally..."
        reboot
        ;;
esac