#!/bin/bash

# Script to block Setup Assistant forced MDM enrollment
# Prerequisites: SIP must be disabled, run with sudo

set -e  # Exit on any error

echo "=== Setup Assistant MDM Blocker ==="
echo "This will prevent Setup Assistant from forcing MDM enrollment"
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
    exit 1
fi

log "SIP Status: $SIP_STATUS"

# Setup Assistant path
SETUP_ASSISTANT="/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant"

# Check if Setup Assistant exists
if [ ! -f "$SETUP_ASSISTANT" ]; then
    log "✗ Setup Assistant not found at: $SETUP_ASSISTANT"
    exit 1
fi

log "✓ Found Setup Assistant at: $SETUP_ASSISTANT"

# Make system volume writable
log "Making system volume writable..."
if mount -uw / 2>/dev/null; then
    log "✓ System volume remounted as writable"
else
    log "✗ Failed to remount system volume as writable"
    exit 1
fi

# Backup original Setup Assistant
log "Backing up original Setup Assistant..."
if cp "$SETUP_ASSISTANT" "${SETUP_ASSISTANT}.original" 2>/dev/null; then
    log "✓ Original Setup Assistant backed up"
else
    log "✗ Failed to backup Setup Assistant"
    exit 1
fi

# Create wrapper script that blocks MDM enrollment
log "Creating MDM blocking wrapper..."
cat > "$SETUP_ASSISTANT" << 'EOF'
#!/bin/bash

# Setup Assistant wrapper that blocks forced MDM enrollment
ORIGINAL_BINARY="${0}.original"

# Check if both problematic flags are present
has_minibuddy=false
has_force_mdm=false

for arg in "$@"; do
    case "$arg" in
        -MiniBuddyYes)
            has_minibuddy=true
            ;;
        -ForceMDMEnroll)
            has_force_mdm=true
            ;;
    esac
done

# If both flags are present, block execution
if [ "$has_minibuddy" = true ] && [ "$has_force_mdm" = true ]; then
    echo "Setup Assistant blocked: Forced MDM enrollment detected"
    echo "Arguments were: $*"
    echo "Blocking execution to prevent forced enrollment"
    exit 0
fi

# Otherwise, run the original Setup Assistant with all arguments
if [ -f "$ORIGINAL_BINARY" ]; then
    exec "$ORIGINAL_BINARY" "$@"
else
    echo "Error: Original Setup Assistant binary not found"
    exit 1
fi
EOF

# Make the wrapper executable
if chmod +x "$SETUP_ASSISTANT" 2>/dev/null; then
    log "✓ Setup Assistant wrapper created and made executable"
else
    log "✗ Failed to make Setup Assistant wrapper executable"
    exit 1
fi

# Verify the wrapper was created correctly
if [ -f "$SETUP_ASSISTANT" ] && [ -f "${SETUP_ASSISTANT}.original" ]; then
    log "✓ Verification successful:"
    log "  - Wrapper: $SETUP_ASSISTANT"
    log "  - Original: ${SETUP_ASSISTANT}.original"
else
    log "✗ Verification failed"
    exit 1
fi

log "=== Setup Complete ==="
log "✓ Setup Assistant will now block forced MDM enrollment"
log "✓ When called with -MiniBuddyYes -ForceMDMEnroll, it will exit without running"
log "✓ Normal Setup Assistant functionality is preserved for other use cases"
log ""
log "To restore original Setup Assistant:"
log "sudo mv '${SETUP_ASSISTANT}.original' '$SETUP_ASSISTANT'"
log ""
log "The modification is now active. No restart required."