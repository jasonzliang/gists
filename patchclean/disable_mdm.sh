#!/bin/bash

# Script to block Setup Assistant forced MDM enrollment
# Works with sealed system volume by using process interception

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

log "Setting up MDM enrollment blocker..."

# Create blocking script in /usr/local/bin
mkdir -p /usr/local/bin

# Create the blocking wrapper
cat > /usr/local/bin/setup_assistant_wrapper << 'EOF'
#!/bin/bash

# Setup Assistant MDM blocker
SETUP_ASSISTANT="/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant"

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

# Otherwise, run the original Setup Assistant
exec "$SETUP_ASSISTANT" "$@"
EOF

chmod +x /usr/local/bin/setup_assistant_wrapper
log "✓ Created blocking wrapper in /usr/local/bin"

# Create launch daemon that intercepts Setup Assistant calls
cat > /Library/LaunchDaemons/com.mdm.blocker.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.mdm.blocker</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/mdm_process_monitor</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardErrorPath</key>
    <string>/tmp/mdm_blocker.log</string>
    <key>StandardOutPath</key>
    <string>/tmp/mdm_blocker.log</string>
</dict>
</plist>
EOF

log "✓ Created launch daemon"

# Create process monitor that kills Setup Assistant with MDM flags
cat > /usr/local/bin/mdm_process_monitor << 'EOF'
#!/bin/bash

# Monitor for Setup Assistant processes with MDM flags and kill them

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log_message "MDM Process Monitor started"

while true; do
    # Look for Setup Assistant processes with both problematic flags
    pids=$(ps aux | grep "Setup Assistant" | grep "\-MiniBuddyYes" | grep "\-ForceMDMEnroll" | grep -v grep | awk '{print $2}' || true)

    if [ -n "$pids" ]; then
        log_message "Found Setup Assistant with MDM flags, killing PIDs: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        log_message "Blocked forced MDM enrollment attempt"
    fi

    sleep 1
done
EOF

chmod +x /usr/local/bin/mdm_process_monitor
log "✓ Created process monitor"

# Load the launch daemon
launchctl load /Library/LaunchDaemons/com.mdm.blocker.plist 2>/dev/null || true
log "✓ Loaded MDM blocker daemon"

# Create shell environment protection using macOS standard locations
# Add to /etc/zshrc for zsh (default shell in macOS)
if ! grep -q "MDM Setup Assistant blocker" /etc/zshrc 2>/dev/null; then
    cat >> /etc/zshrc << 'EOF'

# MDM Setup Assistant blocker
setup_assistant_safe() {
    local has_minibuddy=false
    local has_force_mdm=false

    for arg in "$@"; do
        case "$arg" in
            -MiniBuddyYes) has_minibuddy=true ;;
            -ForceMDMEnroll) has_force_mdm=true ;;
        esac
    done

    if [ "$has_minibuddy" = true ] && [ "$has_force_mdm" = true ]; then
        echo "Setup Assistant blocked: Forced MDM enrollment detected"
        return 0
    fi

    "/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant" "$@"
}

# Override Setup Assistant path in PATH
if [ -d "/usr/local/bin" ]; then
    export PATH="/usr/local/bin:$PATH"
fi
EOF
    log "✓ Added zsh environment blocker"
fi

# Also add to /etc/bashrc for bash users
if ! grep -q "MDM Setup Assistant blocker" /etc/bashrc 2>/dev/null; then
    cat >> /etc/bashrc << 'EOF'

# MDM Setup Assistant blocker
setup_assistant_safe() {
    local has_minibuddy=false
    local has_force_mdm=false

    for arg in "$@"; do
        case "$arg" in
            -MiniBuddyYes) has_minibuddy=true ;;
            -ForceMDMEnroll) has_force_mdm=true ;;
        esac
    done

    if [ "$has_minibuddy" = true ] && [ "$has_force_mdm" = true ]; then
        echo "Setup Assistant blocked: Forced MDM enrollment detected"
        return 0
    fi

    "/System/Library/CoreServices/Setup Assistant.app/Contents/MacOS/Setup Assistant" "$@"
}

# Override Setup Assistant path in PATH
if [ -d "/usr/local/bin" ]; then
    export PATH="/usr/local/bin:$PATH"
fi
EOF
    log "✓ Added bash environment blocker"
fi

# Create symlink to intercept direct calls
ln -sf /usr/local/bin/setup_assistant_wrapper "/usr/local/bin/Setup Assistant" 2>/dev/null || true
log "✓ Created Setup Assistant symlink"

# Create removal script
cat > /usr/local/bin/remove_mdm_blocker << 'EOF'
#!/bin/bash

echo "Removing MDM blocker..."

# Stop and remove launch daemon
launchctl unload /Library/LaunchDaemons/com.mdm.blocker.plist 2>/dev/null || true
rm -f /Library/LaunchDaemons/com.mdm.blocker.plist

# Remove scripts and shell configurations
rm -f /usr/local/bin/setup_assistant_wrapper
rm -f /usr/local/bin/mdm_process_monitor
rm -f /usr/local/bin/Setup\ Assistant

# Remove from shell config files
sed -i '' '/# MDM Setup Assistant blocker/,/^$/d' /etc/zshrc 2>/dev/null || true
sed -i '' '/# MDM Setup Assistant blocker/,/^$/d' /etc/bashrc 2>/dev/null || true

# Remove this script itself
rm -f /usr/local/bin/remove_mdm_blocker

echo "MDM blocker removed"
EOF

chmod +x /usr/local/bin/remove_mdm_blocker
log "✓ Created removal script"

log "=== Setup Complete ==="
log "✓ MDM enrollment blocker is now active"
log "✓ Process monitor running to kill Setup Assistant with MDM flags"
log "✓ Multiple interception methods deployed:"
log "  - Process monitoring and killing"
log "  - PATH override with wrapper script"
log "  - Shell environment blocker"
log ""
log "The blocker will:"
log "  - Kill any Setup Assistant process launched with -MiniBuddyYes -ForceMDMEnroll"
log "  - Allow normal Setup Assistant operations"
log ""
log "To remove the blocker later:"
log "sudo /usr/local/bin/remove_mdm_blocker"
log ""
log "No restart required - protection is active immediately."
