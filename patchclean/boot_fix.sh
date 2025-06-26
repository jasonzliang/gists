#!/bin/bash

# macOS M1 Boot Recovery and Diagnostic Script
# For macOS Sequoia on Apple Silicon Macs
# Run this script from macOS Recovery Mode Terminal

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging
LOG_FILE="/tmp/boot_fix_$(date +%Y%m%d_%H%M%S).log"

log() {
    echo -e "$1" | tee -a "$LOG_FILE"
}

error() {
    log "${RED}ERROR: $1${NC}"
}

success() {
    log "${GREEN}SUCCESS: $1${NC}"
}

warning() {
    log "${YELLOW}WARNING: $1${NC}"
}

info() {
    log "${BLUE}INFO: $1${NC}"
}

# Check if running on Apple Silicon
check_architecture() {
    if [[ $(uname -m) != "arm64" ]]; then
        error "This script is designed for Apple Silicon (M1/M2) Macs only"
        exit 1
    fi
    success "Running on Apple Silicon Mac"
}

# Detect available disks and main system volume
detect_volumes() {
    info "Detecting available volumes..."

    # Get the physical disk (e.g., disk0, disk1)
    PHYSICAL_DISK=$(diskutil list | grep -E "^/dev/disk[0-9]+.*internal" | head -1 | awk '{print $1}' | sed 's|/dev/||')

    if [[ -z "$PHYSICAL_DISK" ]]; then
        # Fallback: get first internal disk
        PHYSICAL_DISK=$(diskutil list | grep -E "/dev/disk[0-9]+" | head -1 | awk '{print $1}' | sed 's|/dev/||')
    fi

    # Get APFS container (e.g., disk0s2, disk1s2)
    APFS_CONTAINER=$(diskutil list | grep "Apple_APFS Container" | head -1 | awk '{print $NF}')

    # Try to find system volume
    SYSTEM_VOLUME=$(diskutil list | grep -E "(Macintosh HD|System)" | head -1 | awk '{print $NF}')

    # Also get data volume if it exists
    DATA_VOLUME=$(diskutil list | grep -E "(Macintosh HD - Data|Data)" | head -1 | awk '{print $NF}')

    info "Physical disk: $PHYSICAL_DISK"
    info "APFS container: $APFS_CONTAINER"
    info "System volume: $SYSTEM_VOLUME"
    info "Data volume: $DATA_VOLUME"

    if [[ -z "$PHYSICAL_DISK" ]]; then
        error "Could not detect physical disk"
        return 1
    fi

    return 0
}

# Check disk health
check_disk_health() {
    info "Checking disk health..."

    # Verify the physical disk first
    if [[ -n "$PHYSICAL_DISK" ]]; then
        info "Verifying physical disk: $PHYSICAL_DISK"
        if diskutil verifyDisk "/dev/$PHYSICAL_DISK" 2>/dev/null; then
            success "Physical disk verification passed"
        else
            warning "Physical disk verification failed, attempting repair..."
            if diskutil repairDisk "/dev/$PHYSICAL_DISK"; then
                success "Physical disk repair completed"
            else
                error "Physical disk repair failed"
            fi
        fi
    fi

    # Check APFS container
    if [[ -n "$APFS_CONTAINER" ]]; then
        info "Verifying APFS container: $APFS_CONTAINER"
        if diskutil verifyVolume "/dev/$APFS_CONTAINER" 2>/dev/null; then
            success "APFS container verification passed"
        else
            warning "APFS container verification failed, attempting repair..."
            if diskutil repairVolume "/dev/$APFS_CONTAINER"; then
                success "APFS container repair completed"
            else
                error "APFS container repair failed"
            fi
        fi
    fi

    # Check system volume
    if [[ -n "$SYSTEM_VOLUME" ]]; then
        info "Verifying system volume: $SYSTEM_VOLUME"
        if diskutil verifyVolume "/dev/$SYSTEM_VOLUME" 2>/dev/null; then
            success "System volume verification passed"
        else
            warning "System volume verification failed, attempting repair..."
            if diskutil repairVolume "/dev/$SYSTEM_VOLUME"; then
                success "System volume repair completed"
            else
                error "System volume repair failed"
            fi
        fi
    fi

    # Check data volume if it exists
    if [[ -n "$DATA_VOLUME" ]]; then
        info "Verifying data volume: $DATA_VOLUME"
        if diskutil verifyVolume "/dev/$DATA_VOLUME" 2>/dev/null; then
            success "Data volume verification passed"
        else
            warning "Data volume verification failed, attempting repair..."
            if diskutil repairVolume "/dev/$DATA_VOLUME"; then
                success "Data volume repair completed"
            else
                error "Data volume repair failed"
            fi
        fi
    fi
}

# Reset NVRAM/PRAM
reset_nvram() {
    info "Resetting NVRAM..."
    if nvram -c 2>/dev/null; then
        success "NVRAM reset completed"
    else
        warning "NVRAM reset failed or not supported in recovery mode"
    fi
}

# Check and repair boot policy
check_boot_policy() {
    info "Checking boot policy..."

    # Check if bputil is available (Apple Silicon only)
    if command -v bputil >/dev/null 2>&1; then
        # Check current boot policy - but handle it carefully as bputil is dangerous
        info "Checking boot policy with bputil (advanced tool)..."
        warning "bputil can make system unbootable - proceeding with caution"

        # Just display policy without modifying
        if bputil -d 2>/dev/null; then
            success "Boot policy displayed successfully"
        else
            warning "Could not read boot policy - may need user authentication"
        fi
    else
        warning "bputil not available - this is expected in Recovery Mode"
    fi

    # Check startup security through alternative means
    info "Alternative boot security checks..."

    # Check for system integrity protection status
    if csrutil status 2>/dev/null | grep -q "enabled"; then
        info "System Integrity Protection is enabled"
    elif csrutil status 2>/dev/null | grep -q "disabled"; then
        warning "System Integrity Protection is disabled"
    else
        info "Cannot determine SIP status from Recovery Mode"
    fi
}

# Rebuild system caches
rebuild_caches() {
    info "Rebuilding system caches..."

    if [[ -n "$SYSTEM_VOLUME" ]]; then
        # Mount the system volume if not already mounted
        MOUNT_POINT="/Volumes/$(basename "$SYSTEM_VOLUME")"
        if [[ ! -d "$MOUNT_POINT" ]]; then
            if diskutil mount "/dev/$SYSTEM_VOLUME"; then
                success "System volume mounted at $MOUNT_POINT"
            else
                error "Failed to mount system volume"
                return 1
            fi
        fi

        # Use kmutil instead of kextcache for Sequoia (macOS 15+)
        if [[ -d "$MOUNT_POINT/System" ]]; then
            info "Rebuilding kernel cache using kmutil..."
            # kmutil is the new tool for macOS Big Sur and later
            if command -v kmutil >/dev/null 2>&1; then
                if kmutil create -V "$MOUNT_POINT" --kernel "$MOUNT_POINT/System/Library/Kernels/kernel" 2>/dev/null; then
                    success "Kernel cache rebuilt with kmutil"
                else
                    warning "kmutil rebuild failed, trying legacy kextcache..."
                    if kextcache -i "$MOUNT_POINT" 2>/dev/null; then
                        success "Kernel cache rebuilt with kextcache"
                    else
                        warning "Both kmutil and kextcache failed"
                    fi
                fi
            else
                # Fallback to kextcache
                info "kmutil not available, using kextcache..."
                if kextcache -i "$MOUNT_POINT" 2>/dev/null; then
                    success "Kernel cache rebuilt with kextcache"
                else
                    warning "Kernel cache rebuild failed"
                fi
            fi

            # Clear system extension cache for Sequoia
            info "Clearing system extension cache..."
            if [[ -d "$MOUNT_POINT/System/Library/SystemExtensions" ]]; then
                # Touch the SystemExtensions directory to force cache rebuild
                touch "$MOUNT_POINT/System/Library/SystemExtensions" 2>/dev/null || true
            fi
        fi
    fi
}

# Check for common file system issues
check_filesystem_issues() {
    info "Checking for filesystem issues..."

    # Check for full disk
    if [[ -n "$SYSTEM_VOLUME" ]]; then
        MOUNT_POINT="/Volumes/$(basename "$SYSTEM_VOLUME")"
        if [[ -d "$MOUNT_POINT" ]] || diskutil mount "/dev/$SYSTEM_VOLUME" 2>/dev/null; then
            DISK_USAGE=$(df -h "$MOUNT_POINT" 2>/dev/null | tail -1 | awk '{print $5}' | sed 's/%//')
            if [[ -n "$DISK_USAGE" && "$DISK_USAGE" -gt 95 ]]; then
                error "Disk is ${DISK_USAGE}% full - this may cause boot issues"
                info "Consider freeing up space in Recovery Mode"
            elif [[ -n "$DISK_USAGE" ]]; then
                success "Disk usage is acceptable (${DISK_USAGE}%)"
            else
                warning "Could not determine disk usage"
            fi
        fi
    fi

    # Check for corrupted preference files - safer approach for Sequoia
    if [[ -d "/Volumes/Macintosh HD/Library/Preferences" ]]; then
        info "Checking system preferences..."
        EMPTY_PLISTS=$(find "/Volumes/Macintosh HD/Library/Preferences" -name "*.plist" -size 0 2>/dev/null | wc -l)
        if [[ "$EMPTY_PLISTS" -gt 0 ]]; then
            warning "Found $EMPTY_PLISTS empty preference files"
            # List them but don't auto-delete in Sequoia due to tighter security
            find "/Volumes/Macintosh HD/Library/Preferences" -name "*.plist" -size 0 2>/dev/null | head -5 | while read -r empty_plist; do
                warning "Empty plist: $empty_plist"
            done
            info "Consider manually removing empty preference files if safe to do so"
        else
            success "No corrupted preference files found"
        fi
    fi

    # Check for system extension issues specific to Sequoia
    if [[ -d "/Volumes/Macintosh HD/System/Library/SystemExtensions" ]]; then
        info "Checking system extensions (Sequoia-specific)..."
        EXT_COUNT=$(find "/Volumes/Macintosh HD/System/Library/SystemExtensions" -name "*.systemextension" 2>/dev/null | wc -l)
        info "Found $EXT_COUNT system extensions"

        # Check for common system extension cache issues
        if [[ -d "/Volumes/Macintosh HD/Library/SystemExtensions" ]]; then
            USER_EXT_COUNT=$(find "/Volumes/Macintosh HD/Library/SystemExtensions" -name "*.systemextension" 2>/dev/null | wc -l)
            info "Found $USER_EXT_COUNT user system extensions"
        fi
    fi
}

# Restore from Time Machine (interactive)
suggest_time_machine_restore() {
    info "Time Machine restore option available"
    echo "If other fixes fail, consider restoring from Time Machine backup:"
    echo "1. In Recovery Mode, select 'Restore from Time Machine Backup'"
    echo "2. Follow the prompts to select your backup"
    echo "3. This will restore your system to a previous working state"
}

# Check startup security utility settings
check_security_settings() {
    info "Checking startup security settings..."

    echo "=== Sequoia-Specific Security Considerations ==="
    echo "macOS Sequoia introduces stricter security policies that may affect boot:"
    echo ""
    echo "1. System Extension Security:"
    echo "   - Some third-party system extensions may need re-approval"
    echo "   - Check System Settings > Privacy & Security > System Extensions"
    echo ""
    echo "2. Startup Security Utility (Manual Steps):"
    echo "   - From Recovery Mode: Utilities > Startup Security Utility"
    echo "   - For boot issues, try 'Reduced Security' temporarily"
    echo "   - Enable 'Allow booting from external media' if needed"
    echo ""
    echo "3. System Integrity Protection (SIP):"
    if csrutil status 2>/dev/null | grep -q "enabled"; then
        echo "   - SIP is ENABLED (recommended for security)"
    elif csrutil status 2>/dev/null | grep -q "disabled"; then
        echo "   - SIP is DISABLED (may help with boot issues but reduces security)"
    else
        echo "   - SIP status unknown (check from Recovery Mode)"
    fi
    echo ""
    echo "4. FileVault Considerations:"
    echo "   - If FileVault is enabled and causing issues, you may need to disable it temporarily"
    echo "   - This requires your recovery key or password"
    echo ""
    echo "5. Platform SSO (Enterprise environments):"
    echo "   - Known issue in Sequoia where Platform SSO can cause Recovery boot loops"
    echo "   - May need to disable FileVault temporarily if affected"
}

# Main execution function
main() {
    log "${BLUE}=== macOS M1 Boot Recovery Script ===${NC}"
    log "Started at: $(date)"
    log "Log file: $LOG_FILE"

    # Run diagnostics and fixes
    check_architecture

    if detect_volumes; then
        check_disk_health
        check_filesystem_issues
        rebuild_caches
    else
        error "Could not detect system volumes"
    fi

    reset_nvram
    check_boot_policy
    check_security_settings

    log "\n${BLUE}=== Additional Recovery Options ===${NC}"
    suggest_time_machine_restore

    log "\n${BLUE}=== Sequoia-Specific Troubleshooting ===${NC}"
    echo "If script fixes don't resolve the issue, try these Sequoia-specific steps:"
    echo ""
    echo "1. System Extension Issues:"
    echo "   - Boot in Safe Mode: hold Shift during startup"
    echo "   - Go to System Settings > Privacy & Security"
    echo "   - Re-approve any blocked system extensions"
    echo ""
    echo "2. Platform SSO Boot Loop (Enterprise Macs):"
    echo "   - This is a known Sequoia bug with Platform SSO + FileVault"
    echo "   - Temporary fix: disable FileVault from Recovery Mode"
    echo "   - Command: 'fdesetup disable' (requires admin password)"
    echo ""
    echo "3. Reset SMC (Apple Silicon):"
    echo "   - Shut down Mac completely"
    echo "   - Press and hold power button for 10 seconds"
    echo "   - Release and wait 5 seconds, then power on normally"
    echo ""
    echo "4. Force Internet Recovery:"
    echo "   - If local recovery fails, use Internet Recovery"
    echo "   - Power on while holding Option + Command + R"
    echo "   - Requires stable internet connection"
    echo ""
    echo "5. Reinstall macOS (Clean Install):"
    echo "   - Last resort: complete macOS reinstall"
    echo "   - Backup data first if possible"
    echo "   - Use 'Erase Mac' feature in Recovery Mode"

    log "\n${GREEN}Script completed. Check log file: $LOG_FILE${NC}"

    # Offer to view log
    read -p "View detailed log? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        less "$LOG_FILE"
    fi
}

# Run main function
main "$@"
