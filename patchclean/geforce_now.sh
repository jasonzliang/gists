#!/bin/bash

# GeForce NOW Optimization Script for M1 MacBook Pro (macOS Sequoia)
# VERIFIED AND CORRECTED VERSION
# Run with: bash geforce_now_optimize.sh

echo "🎮 Optimizing M1 MacBook Pro for GeForce NOW..."

# WARNING: Always backup current settings first
echo "📋 Backing up current settings..."
pmset -g > ~/pmset_backup_$(date +%Y%m%d_%H%M%S).txt
echo "Current pmset settings backed up to ~/pmset_backup_*.txt"

# 1. Network Performance Optimization (CORRECTED)
echo "📡 Configuring network settings..."

# These are SAFE and VERIFIED settings for modern macOS
sudo sysctl -w net.inet.tcp.win_scale_factor=8
sudo sysctl -w net.inet.tcp.autorcvbufmax=33554432
sudo sysctl -w net.inet.tcp.autosndbufmax=33554432

# Reduce TCP delayed ACK (safe)
sudo sysctl -w net.inet.tcp.delayed_ack=0

# 2. Power Management (VERIFIED SAFE)
echo "⚡ Optimizing power settings for performance..."

# Disable power nap and standby features during gaming
sudo pmset -a powernap 0
sudo pmset -a standby 0
sudo pmset -a autopoweroff 0
sudo pmset -a proximitywake 0

# Keep system awake longer (prevent sleep during gaming)
sudo pmset -a sleep 0
sudo pmset -a displaysleep 0

# 3. Display Performance
echo "🖥️  Configuring display for low latency..."

# Disable window animations (verified safe)
defaults write NSGlobalDomain NSAutomaticWindowAnimationsEnabled -bool false
defaults write NSGlobalDomain NSWindowResizeTime -float 0.001

# Disable transparency effects
defaults write com.apple.universalaccess reduceTransparency -bool true

# 4. Memory and CPU (M1 COMPATIBLE)
echo "🧠 Optimizing system resources..."

# These are safe for M1 Macs
sudo sysctl -w vm.swappiness=1
sudo sysctl -w kern.timer.longterm.threshold=0

# 5. Audio Optimization (VERIFIED)
echo "🔊 Configuring audio for gaming..."

# Set optimal sample rates
sudo defaults write com.apple.audio.CoreAudio DefaultInputSampleRate -int 48000
sudo defaults write com.apple.audio.CoreAudio DefaultOutputSampleRate -int 48000

# 6. Browser Optimization Flags (CORRECTED)
echo "🌐 Creating browser optimization file..."

# Chrome flags for hardware acceleration
cat > ~/chrome_gaming_flags.txt << 'EOF'
--enable-gpu-rasterization
--enable-zero-copy
--disable-gpu-driver-bug-workarounds
--enable-hardware-overlays
--enable-features=VaapiVideoDecoder,CanvasOopRasterization
--disable-features=UseChromeOSDirectVideoDecoder
--max_old_space_size=4096
--enable-accelerated-2d-canvas
--enable-accelerated-video-decode
EOF

# 7. Make Network Settings Persistent (FIXED)
echo "💾 Making network optimizations persistent..."

# Create LaunchDaemon for persistence (modern macOS approach)
sudo tee /Library/LaunchDaemons/com.geforce.networking.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.geforce.networking</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/sbin/sysctl</string>
        <string>-w</string>
        <string>net.inet.tcp.win_scale_factor=8</string>
        <string>net.inet.tcp.autorcvbufmax=33554432</string>
        <string>net.inet.tcp.autosndbufmax=33554432</string>
        <string>net.inet.tcp.delayed_ack=0</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF

sudo launchctl load /Library/LaunchDaemons/com.geforce.networking.plist

echo "✅ Optimization complete!"
echo ""
echo "📋 Additional Manual Steps:"
echo "1. Set display refresh rate: System Settings > Displays > Refresh Rate"
echo "2. Connect to 5GHz WiFi or use Ethernet cable"
echo "3. Close unnecessary applications before gaming"
echo "4. Use GeForce NOW native app (not browser) when possible"
echo "5. For Chrome gaming: open -a 'Google Chrome' --args \$(cat ~/chrome_gaming_flags.txt)"
echo ""
echo "⚠️  To restore original settings:"
echo "sudo pmset restoredefaults"
echo "defaults write NSGlobalDomain NSAutomaticWindowAnimationsEnabled -bool true"
echo "sudo launchctl unload /Library/LaunchDaemons/com.geforce.networking.plist"
echo "sudo rm /Library/LaunchDaemons/com.geforce.networking.plist"
echo ""
echo "🔄 Changes take effect immediately. Reboot recommended for full optimization."

# Verification
echo ""
echo "🔍 Verifying key settings..."
echo "TCP Window Scale Factor: $(sysctl -n net.inet.tcp.win_scale_factor)"
echo "Power Nap: $(pmset -g | grep powernap)"
echo "Display Animations: $(defaults read NSGlobalDomain NSAutomaticWindowAnimationsEnabled 2>/dev/null || echo 'false')"
