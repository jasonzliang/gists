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

# Function to find system volume
find_system_volume() {
    log "Searching for system volume..."

    # First, get the diskutil list output and parse it properly
    local diskutil_output=$(diskutil list)

    # Look for APFS volumes that could be system volumes
    # Priority order: exact "Macintosh HD", then system-like names
    local candidates=()

    # Parse diskutil list to find APFS volumes with their identifiers
    while IFS= read -r line; do
        if echo "$line" | grep -q "APFS Volume"; then
            local identifier=$(echo "$line" | awk '{print $NF}')
            local name=$(echo "$line" | sed 's/.*APFS Volume[[:space:]]*//' | sed 's/[[:space:]]*[[:space:]][[:space:]]*[0-9.].*$//')

            log "Found APFS volume: $name ($identifier)"

            # Prioritize exact matches for system volume names
            case "$name" in
                "Macintosh HD")
                    log "Found exact match for Macintosh HD: $identifier"
                    candidates=("$identifier" "${candidates[@]}")  # Put at front
                    ;;
                "macOS "*|"Mac OS "*|*" System")
                    log "Found system-like volume: $name ($identifier)"
                    candidates+=("$identifier")
                    ;;
            esac
        fi
    done <<< "$diskutil_output"

    # Test candidates in order
    for vol in "${candidates[@]}"; do
        log "Testing candidate volume: $vol"

        # Get detailed volume info
        local vol_info=$(diskutil info "$vol" 2>/dev/null)
        if [ $? -eq 0 ]; then
            log "Volume info for $vol:"
            echo "$vol_info" | grep -E "(Volume Name|Volume Role|Mount Point)" | while read line; do
                log "  $line"
            done

            # Check if it's a system volume by multiple criteria
            local is_system=false

            # Check for system role
            if echo "$vol_info" | grep -q "Volume Role.*System"; then
                log "✓ $vol has System role"
                is_system=true
            fi

            # Check for system volume name patterns
            if echo "$vol_info" | grep -q "Volume Name.*Macintosh HD$"; then
                log "✓ $vol is named 'Macintosh HD' (exact match)"
                is_system=true
            fi

            # Check if it's bootable (has System/Library)
            local mount_pt=$(echo "$vol_info" | grep "Mount Point" | sed 's/.*Mount Point:[[:space:]]*//')
            if [ -n "$mount_pt" ] && [ -d "$mount_pt/System/Library" ]; then
                log "✓ $vol contains System/Library at $mount_pt"
                is_system=true
            fi

            if [ "$is_system" = true ]; then
                log "Selected system volume: $vol"
                echo "$vol"
                return 0
            fi
        fi
    done

    # Fallback: try your specific disk layout patterns
    local fallback_candidates=("disk3s3" "disk1s1" "disk1s5" "disk0s2")
    log "No system volume found, trying fallback candidates..."

    for candidate in "${fallback_candidates[@]}"; do
        log "Testing fallback candidate: $candidate"
        if diskutil info "$candidate" >/dev/null 2>&1; then
            local info=$(diskutil info "$candidate" 2>/dev/null)
            if echo "$info" | grep -q "Apple_APFS"; then
                local vol_name=$(echo "$info" | grep "Volume Name" | sed 's/.*Volume Name:[[:space:]]*//')
                log "Found APFS volume: $vol_name ($candidate)"

                # Accept any APFS volume as last resort, but prefer Macintosh HD
                if echo "$vol_name" | grep -q "Macintosh HD"; then
                    log "✓ Fallback found Macintosh HD: $candidate"
                    echo "$candidate"
                    return 0
                fi
            fi
        fi
    done

    # Final fallback - accept any APFS volume that might be a system volume
    for candidate in "${fallback_candidates[@]}"; do
        if diskutil info "$candidate" >/dev/null 2>&1; then
            local info=$(diskutil info "$candidate" 2>/dev/null)
            if echo "$info" | grep -q "Apple_APFS"; then
                log "Using APFS volume as final fallback: $candidate"
                echo "$candidate"
                return 0
            fi
        fi
    done

    return 1
}

# Function to mount system volume
mount_system_volume() {
    local disk="$1"
    log "Attempting to mount $disk..."

    # First check if already mounted
    local mount_point=$(diskutil info "$disk" 2>/dev/null | grep "Mount Point" | sed 's/.*Mount Point: *//' | sed 's/[[:space:]]*$//')

    if [ -n "$mount_point" ] && [ "$mount_point" != "Not applicable (not mounted)" ]; then
        log "Volume already mounted at: $mount_point"
        echo "$mount_point"
        return 0
    fi

    # Try to mount
    log "Mounting $disk..."
    local mount_output
    if mount_output=$(diskutil mount "$disk" 2>&1); then
        log "Mount command succeeded"
        log "Mount output: $mount_output"

        # Extract mount point from output
        mount_point=$(echo "$mount_output" | grep -o "/Volumes/[^[:space:]]*" | head -1)

        if [ -z "$mount_point" ]; then
            # Try to get mount point from diskutil info
            mount_point=$(diskutil info "$disk" 2>/dev/null | grep "Mount Point" | sed 's/.*Mount Point: *//' | sed 's/[[:space:]]*$//')
        fi

        if [ -n "$mount_point" ] && [ -d "$mount_point" ]; then
            log "Successfully mounted at: $mount_point"
            echo "$mount_point"
            return 0
        fi
    else
        log "Mount command failed with output: $mount_output"
    fi

    # Check if it mounted somewhere else
    log "Checking for any new mounts in /Volumes..."
    for vol in /Volumes/*/; do
        if [ -d "$vol" ] && [ -d "${vol}System/Library" ]; then
            log "Found system files at: $vol"
            echo "$vol"
            return 0
        fi
    done

    return 1
}

# Check if running in Recovery Mode
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

# Step 2: Find and mount the system volume
log "Finding system volume..."
diskutil list

SYSTEM_DISK=$(find_system_volume)
if [ -z "$SYSTEM_DISK" ]; then
    log "Could not automatically detect system volume."
    echo ""
    echo "Available APFS volumes:"
    diskutil list | grep -E "(identifier|Apple_APFS)" | grep -B1 "Apple_APFS"
    echo ""
    echo "Which disk contains your macOS system? (e.g., disk1s1)"
    echo -n "Enter disk identifier: "
    read -r SYSTEM_DISK

    if [ -z "$SYSTEM_DISK" ]; then
        log "✗ No disk specified. Exiting."
        exit 1
    fi
fi

log "Using system disk: $SYSTEM_DISK"

# Mount the system volume
MOUNT_POINT=$(mount_system_volume "$SYSTEM_DISK")
if [ $? -ne 0 ] || [ -z "$MOUNT_POINT" ]; then
    log "✗ Failed to mount system volume"
    log "Manual intervention required:"
    log "1. Try running: diskutil mount $SYSTEM_DISK"
    log "2. Or check available volumes with: diskutil list"
    exit 1
fi

# Clean up mount point path
MOUNT_POINT=$(echo "$MOUNT_POINT" | sed 's|/$||')  # Remove trailing slash
log "✓ Using mount point: $MOUNT_POINT"

# Make it writable
log "Making system volume writable..."
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
    log "System directory contents:"
    ls -la "$SYSTEM_ROOT/" | head -5
else
    log "✗ System files not found at $SYSTEM_ROOT"
    log "Contents of $SYSTEM_ROOT:"
    ls -la "$SYSTEM_ROOT/" || log "Could not list directory contents"

    # Try fallback to root filesystem
    if [ -d "/System/Library" ]; then
        log "⚠ Using root filesystem as fallback"
        SYSTEM_ROOT="/"
    else
        log "✗ No usable system volume found"
        exit 1
    fi
fi

# Step 3: Backup original files
log "Creating backup directory..."
BACKUP_DIR="/tmp/mdm_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Backup Setup Assistant
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
        if cp "$plist" "$BACKUP_DIR/$plist_name" 2>/dev/null; then
            log "Backed up $plist_name"
        else
            log "⚠ Could not backup $plist_name"
        fi

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

        if chmod +x "$SETUP_ASSISTANT" 2>/dev/null; then
            log "✓ Setup Assistant modified to BLOCK execution when ForceMDMEnroll is detected"
        else
            log "✗ Could not set executable permissions on modified Setup Assistant"
        fi
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
if [ -d "\$BACKUP_DIR/Managed_Preferences" ]; then
    mkdir -p "/Library/Managed Preferences"
    cp -r "\$BACKUP_DIR/Managed_Preferences/"* "/Library/Managed Preferences/" 2>/dev/null
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