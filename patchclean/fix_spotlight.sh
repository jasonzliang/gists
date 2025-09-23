#!/bin/bash

# Spotlight Indexing Enable Script
# Comprehensive script to fix disabled Spotlight indexing and searching
# Author: AI Assistant
# Usage: chmod +x spotlight_fix.sh && sudo ./spotlight_fix.sh
# Note: Script must be run with root privileges (sudo) Script
# Comprehensive script to fix disabled Spotlight indexing and searching
# Author: AI Assistant
# Usage: chmod +x spotlight_fix.sh && sudo ./spotlight_fix.sh

set -euo pipefail  # Exit on any error, undefined vars, pipe failures

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Check if running as root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        error "This script must be run with sudo privileges"
        exit 1
    fi
}

# Get macOS version
get_macos_version() {
    sw_vers -productVersion | cut -d '.' -f 1-2
}

# Check current Spotlight status
check_spotlight_status() {
    log "Checking current Spotlight status..."
    echo "Current indexing status:"
    mdutil -sa 2>/dev/null || warning "mdutil command failed"
}

# Check for blocking files
check_blocking_files() {
    log "Checking for files that block Spotlight indexing..."

    local blocking_files=(
        "/.metadata_never_index"
        "/System/Volumes/Data/.metadata_never_index"
        "/var/db/.metadata_never_index"
    )

    for file in "${blocking_files[@]}"; do
        if [ -f "$file" ]; then
            warning "Found blocking file: $file"
            if [[ "${INTERACTIVE:-true}" == "true" ]]; then
                set +e  # Temporarily disable exit on error for interactive input
                read -p "Remove $file? (y/n): " -n 1 -r
                echo
                if [[ $REPLY =~ ^[Yy]$ ]]; then
                    rm -f "$file" && success "Removed $file"
                fi
                set -e  # Re-enable exit on error
            else
                # Auto-remove in non-interactive mode
                rm -f "$file" && success "Auto-removed $file"
            fi
        fi
    done
}

# Basic Spotlight enable
basic_enable() {
    log "Running basic Spotlight enable commands..."

    # Enable indexing on all volumes
    mdutil -ai on 2>/dev/null && success "Enabled indexing on all volumes" || warning "Failed to enable on all volumes"

    # Force rebuild on all volumes
    mdutil -aE 2>/dev/null && success "Forced rebuild on all volumes" || warning "Failed to force rebuild"
}

# Classic 4-command sequence (most reliable method)
classic_four_command() {
    log "Running classic 4-command sequence (most reliable method)..."

    # Disable indexing
    mdutil -i off / 2>/dev/null && success "Disabled indexing on root" || warning "Failed to disable indexing"

    # Remove spotlight index files
    rm -rf /.Spotlight* 2>/dev/null && success "Removed Spotlight index files" || warning "Failed to remove index files"

    # Enable indexing
    mdutil -i on / 2>/dev/null && success "Enabled indexing on root" || warning "Failed to enable indexing"

    # Force rebuild
    mdutil -E / 2>/dev/null && success "Forced rebuild on root" || warning "Failed to force rebuild"
}

# Spotlight daemon management
restart_spotlight_daemon() {
    log "Restarting Spotlight daemon..."

    # Unload spotlight daemon
    launchctl unload -w /System/Library/LaunchDaemons/com.apple.metadata.mds.plist 2>/dev/null && success "Unloaded Spotlight daemon" || warning "Failed to unload daemon (may not be loaded)"

    sleep 2

    # Reload spotlight daemon
    launchctl load -w /System/Library/LaunchDaemons/com.apple.metadata.mds.plist 2>/dev/null && success "Loaded Spotlight daemon" || warning "Failed to load daemon"

    # Kill and restart processes
    pkill -9 mds 2>/dev/null && success "Killed mds processes" || warning "No mds processes to kill"
    pkill -9 mdworker 2>/dev/null && success "Killed mdworker processes" || warning "No mdworker processes to kill"
    pkill -9 mds_stores 2>/dev/null && success "Killed mds_stores processes" || warning "No mds_stores processes to kill"
}

# Complete reset for modern macOS
complete_reset_modern() {
    log "Running complete reset for modern macOS..."

    local macos_version=$(get_macos_version)
    local spotlight_path="/.Spotlight-V100"

    # Use different path for Catalina (10.15) and newer
    # Simple version comparison without bc dependency
    local major=$(echo "$macos_version" | cut -d. -f1)
    local minor=$(echo "$macos_version" | cut -d. -f2)
    if [[ $major -gt 10 ]] || [[ $major -eq 10 && $minor -ge 15 ]]; then
        spotlight_path="/System/Volumes/Data/.Spotlight-V100"
    fi

    # Disable all indexing
    mdutil -ai off 2>/dev/null && success "Disabled all indexing" || warning "Failed to disable indexing"

    # Unload daemon
    launchctl unload -w /System/Library/LaunchDaemons/com.apple.metadata.mds.plist 2>/dev/null || warning "Daemon unload failed"

    # Remove index files
    rm -rf "$spotlight_path" 2>/dev/null && success "Removed $spotlight_path" || warning "Failed to remove $spotlight_path"
    rm -rf ~/.Spotlight-V100 2>/dev/null && success "Removed user Spotlight files" || warning "No user Spotlight files found"

    # Reload daemon
    launchctl load -w /System/Library/LaunchDaemons/com.apple.metadata.mds.plist 2>/dev/null && success "Reloaded daemon" || warning "Daemon reload failed"

    # Enable all indexing
    mdutil -ai on 2>/dev/null && success "Enabled all indexing" || warning "Failed to enable indexing"

    # Force rebuild all
    mdutil -aE 2>/dev/null && success "Forced rebuild on all volumes" || warning "Failed to force rebuild"
}

# Enable indexing for specific volumes
enable_specific_volumes() {
    log "Enabling indexing for specific volumes..."

    # Common volume paths
    local volumes=(
        "/"
        "/System/Volumes/Data"
        "/System/Volumes/Preboot"
    )

    for volume in "${volumes[@]}"; do
        if [ -d "$volume" ]; then
            mdutil -i on "$volume" 2>/dev/null && success "Enabled indexing for $volume" || warning "Failed to enable indexing for $volume"
            mdutil -E "$volume" 2>/dev/null && success "Forced rebuild for $volume" || warning "Failed to force rebuild for $volume"
        fi
    done

    # Check for external volumes
    log "Checking external volumes..."
    # Use nullglob to handle case where /Volumes/* matches nothing
    shopt -s nullglob
    for volume in /Volumes/*; do
        if [ -d "$volume" ] && [ "$(basename "$volume")" != "." ]; then
            log "Found external volume: $volume"
            set +e  # Temporarily disable exit on error for interactive input
            read -p "Enable indexing for $volume? (y/n): " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                mdutil -i on "$volume" 2>/dev/null && success "Enabled indexing for $volume" || warning "Failed to enable indexing for $volume"
                mdutil -E "$volume" 2>/dev/null && success "Forced rebuild for $volume" || warning "Failed to force rebuild for $volume"
            fi
            set -e  # Re-enable exit on error
        fi
    done
    shopt -u nullglob
}

# Enable locate database
enable_locate_database() {
    log "Enabling locate database..."
    launchctl load -w /System/Library/LaunchDaemons/com.apple.locate.plist 2>/dev/null && success "Enabled locate database" || warning "Failed to enable locate database"
}

# Nuclear option (requires SIP disabled)
nuclear_option() {
    warning "NUCLEAR OPTION: This requires System Integrity Protection to be disabled"
    warning "You must boot to Recovery Mode and run 'csrutil disable' first"
    warning "After running this, you must boot to Recovery Mode and run 'csrutil enable'"

    set +e  # Temporarily disable exit on error for interactive input
    read -p "Have you disabled SIP and want to continue? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log "Skipping nuclear option"
        set -e  # Re-enable exit on error
        return
    fi
    set -e  # Re-enable exit on error

    log "Running nuclear option..."

    # Unload daemon
    launchctl unload -w /System/Library/LaunchDaemons/com.apple.metadata.mds.plist 2>/dev/null || warning "Daemon unload failed"

    # Erase all indexes
    mdutil -Ea 2>/dev/null && success "Erased all indexes" || warning "Failed to erase indexes"

    # Delete indexes with -X flag
    local volumes=("/" "/System/Volumes/Data" "/System/Volumes/Preboot")
    for volume in "${volumes[@]}"; do
        if [ -d "$volume" ]; then
            mdutil -X "$volume" 2>/dev/null && success "Deleted index for $volume" || warning "Failed to delete index for $volume"
        fi
    done

    # Delete external volume indexes
    shopt -s nullglob
    for volume in /Volumes/*; do
        if [ -d "$volume" ] && [ "$(basename "$volume")" != "." ]; then
            mdutil -X "$volume" 2>/dev/null && success "Deleted index for $volume" || warning "Failed to delete index for $volume"
        fi
    done
    shopt -u nullglob

    # Reload daemon
    launchctl load -w /System/Library/LaunchDaemons/com.apple.metadata.mds.plist 2>/dev/null && success "Reloaded daemon" || warning "Daemon reload failed"

    # Enable indexing
    mdutil -i on / 2>/dev/null && success "Enabled indexing for root" || warning "Failed to enable root indexing"
    mdutil -i on /System/Volumes/Data 2>/dev/null && success "Enabled indexing for Data volume" || warning "Failed to enable Data volume indexing"
    mdutil -i on /System/Volumes/Preboot 2>/dev/null && success "Enabled indexing for Preboot volume" || warning "Failed to enable Preboot volume indexing"

    warning "Remember to re-enable SIP by booting to Recovery Mode and running 'csrutil enable'"
}

# Verification
verify_indexing() {
    log "Verifying Spotlight indexing..."

    echo "Final indexing status:"
    mdutil -sa 2>/dev/null || warning "mdutil status check failed"

    log "Checking for active Spotlight processes..."
    if pgrep -f "mds\|mdworker" > /dev/null; then
        success "Spotlight processes are running"
        echo "Active processes:"
        ps aux | grep -E "(mds|mdworker)" | grep -v grep
    else
        warning "No Spotlight processes found"
    fi

    log "Testing search functionality..."
    local test_result=$(mdfind -name "System" 2>/dev/null | head -1)
    if [ -n "$test_result" ]; then
        success "Spotlight search is working"
    else
        warning "Spotlight search may not be working yet (indexing may still be in progress)"
    fi
}

# Main menu
show_menu() {
    echo
    echo "=========================================="
    echo "       Spotlight Fix Script Menu"
    echo "=========================================="
    echo "1. Check current status"
    echo "2. Basic enable (safe)"
    echo "3. Classic 4-command sequence (most reliable)"
    echo "4. Restart Spotlight daemon"
    echo "5. Complete modern reset"
    echo "6. Enable specific volumes"
    echo "7. Nuclear option (requires SIP disabled)"
    echo "8. Run all methods (recommended)"
    echo "9. Verify indexing status"
    echo "0. Exit"
    echo "=========================================="
}

# Run all methods (with proper error handling for interactive mode)
run_all_methods() {
    log "Running all Spotlight fix methods..."

    # Set non-interactive mode for run_all_methods
    local original_interactive=${INTERACTIVE:-false}
    INTERACTIVE=false

    check_blocking_files
    basic_enable
    sleep 2
    classic_four_command
    sleep 2
    restart_spotlight_daemon
    sleep 2
    complete_reset_modern
    sleep 2
    # Skip interactive volume selection in run_all mode
    log "Enabling indexing for standard volumes..."
    local volumes=("/" "/System/Volumes/Data" "/System/Volumes/Preboot")
    for volume in "${volumes[@]}"; do
        if [ -d "$volume" ]; then
            mdutil -i on "$volume" 2>/dev/null && success "Enabled indexing for $volume" || warning "Failed to enable indexing for $volume"
            mdutil -E "$volume" 2>/dev/null && success "Forced rebuild for $volume" || warning "Failed to force rebuild for $volume"
        fi
    done
    enable_locate_database

    # Restore original interactive setting
    INTERACTIVE=$original_interactive

    success "All methods completed. Verifying..."
    verify_indexing
}

# Main execution
main() {
    echo "Spotlight Indexing Fix Script"
    echo "macOS Version: $(get_macos_version)"
    check_root

    if [ $# -eq 0 ]; then
        # Interactive mode
        while true; do
            show_menu
            set +e  # Temporarily disable exit on error for interactive input
            read -p "Select an option (0-9): " choice
            set -e  # Re-enable exit on error
            case $choice in
                1) check_spotlight_status ;;
                2) check_blocking_files; basic_enable ;;
                3) classic_four_command ;;
                4) restart_spotlight_daemon ;;
                5) complete_reset_modern ;;
                6) enable_specific_volumes ;;
                7) nuclear_option ;;
                8) run_all_methods ;;
                9) verify_indexing ;;
                0) log "Exiting..."; exit 0 ;;
                *) error "Invalid option. Please try again." ;;
            esac
            echo
            set +e  # Temporarily disable exit on error for interactive input
            read -p "Press Enter to continue..."
            set -e  # Re-enable exit on error
        done
    else
        # Non-interactive mode - run all methods
        run_all_methods
    fi
}

# Run main function
main "$@"
