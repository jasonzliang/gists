#!/bin/bash

# macOS Application Support & System Cleanup Script
# Research-based commands for safe cleanup of outdated and unnecessary files

echo "🧹 Starting macOS Application Support cleanup..."
echo "⚠️  WARNING: Always backup your Mac before running these commands!"

# Check free space before cleanup
echo "📊 Disk space before cleanup:"
df -h /

echo ""
echo "1️⃣ Cleaning User Application Caches (Safe)"
# Remove user-level app caches - safest to clean
sudo rm -rf ~/Library/Caches/com.apple.Safari/Webpage\ Previews/*
sudo rm -rf ~/Library/Caches/com.google.Chrome/Default/Cache/*
sudo rm -rf ~/Library/Caches/Firefox/Profiles/*/cache2/*
sudo rm -rf ~/Library/Caches/com.spotify.client/Storage/*

echo ""
echo "2️⃣ Cleaning Browser Data (Safe)"
# Clear browser caches and temporary data
rm -rf ~/Library/Safari/LocalStorage/*
rm -rf ~/Library/Safari/Databases/*
rm -rf ~/Library/Application\ Support/Google/Chrome/Default/Service\ Worker/CacheStorage/*
rm -rf ~/Library/Application\ Support/Firefox/Profiles/*/storage/default/*

echo ""
echo "3️⃣ Cleaning Application Support Leftovers"
# Find and list orphaned app folders (review before deleting)
echo "🔍 Scanning for potential orphaned application folders..."
find ~/Library/Application\ Support -type d -maxdepth 1 -name "*" -exec basename {} \; | sort

# Common safe-to-remove Application Support folders (after apps uninstalled)
echo "🗑️  Removing common temporary/cache folders..."
rm -rf ~/Library/Application\ Support/*/Cache*
rm -rf ~/Library/Application\ Support/*/cache*
rm -rf ~/Library/Application\ Support/*/Temp*
rm -rf ~/Library/Application\ Support/*/temp*

echo ""
echo "4️⃣ Cleaning System Logs (Safe)"
# Remove old log files and crash reports
sudo rm -rf /var/log/*.gz
sudo rm -rf /var/log/*.bz2
sudo rm -rf /Library/Logs/DiagnosticReports/*
sudo rm -rf ~/Library/Logs/*
rm -rf ~/Library/Logs/CoreSimulator/*

echo ""
echo "5️⃣ Cleaning Download & Temporary Files"
# Clean Downloads folder of old files (older than 30 days)
find ~/Downloads -type f -mtime +30 -exec rm {} \;

# Clean system temporary files
sudo rm -rf /private/var/tmp/*
sudo rm -rf /tmp/*
sudo rm -rf /private/tmp/*

echo ""
echo "6️⃣ Cleaning iOS Device Backups & Mobile Data"
echo "📱 iOS backup locations (review before deleting):"
ls -la ~/Library/Application\ Support/MobileSync/Backup/ 2>/dev/null || echo "No iOS backups found"

# Clean iOS simulator data if present
rm -rf ~/Library/Developer/CoreSimulator/Devices/*/data/Containers/Data/Application/*/tmp/*
rm -rf ~/Library/Developer/CoreSimulator/Caches/*
# Uncomment to delete old iOS backups (BE CAREFUL!)
# find ~/Library/Application\ Support/MobileSync/Backup -type d -mtime +90 -exec rm -rf {} \;

echo ""
echo "7️⃣ Cleaning Development Tools Cache"
# Xcode derived data and archives
rm -rf ~/Library/Developer/Xcode/DerivedData/*
rm -rf ~/Library/Developer/Xcode/Archives/*
rm -rf ~/Library/Developer/CoreSimulator/Caches/*

echo ""
echo "8️⃣ Cleaning Adobe & Creative Apps Cache"
# Adobe cache files (common space hogs)
rm -rf ~/Library/Application\ Support/Adobe/Common/Media\ Cache\ Files/*
rm -rf ~/Library/Caches/Adobe/*
rm -rf ~/Library/Caches/com.adobe.*/*

echo ""
echo "9️⃣ Cleaning Trash & Hidden Files"
# Empty all trash cans
sudo rm -rf ~/.Trash/*
sudo rm -rf /Volumes/*/.Trashes/*

echo ""
echo "🔟 Cleaning Containers & Group Containers (Orphaned Apps)"
# Clean containers for uninstalled apps (be very careful here)
echo "📦 Checking for orphaned app containers..."
find ~/Library/Containers -maxdepth 1 -type d -exec basename {} \; | sort > /tmp/containers_list.txt
echo "⚠️  Manual review recommended for: ~/Library/Containers"
echo "   Run: ls -la ~/Library/Containers | sort -k5 -nr"

# Clean Group Containers cache files safely
rm -rf ~/Library/Group\ Containers/*/Cache*
rm -rf ~/Library/Group\ Containers/*/cache*

echo ""
echo "1️⃣1️⃣ Cleaning Mail Attachments & WebKit Data"
# Mail attachments can be huge space wasters
echo "📧 Cleaning Mail data..."
rm -rf ~/Library/Mail/V*/MailData/Envelope\ Index*
rm -rf ~/Library/Mail/V*/MailData/Envelope\ Index-shm
rm -rf ~/Library/Mail/V*/MailData/Envelope\ Index-wal

# WebKit data cleanup
echo "🌐 Cleaning WebKit caches..."
rm -rf ~/Library/Caches/com.apple.WebKit.Networking/*
rm -rf ~/Library/WebKit/*/WebKitCache/*

echo ""
echo "1️⃣2️⃣ Cleaning Preferences & Saved Application State"
# Old preference files (be very careful - only clean truly orphaned ones)
echo "⚙️  Cleaning old preference files..."
find ~/Library/Preferences -name "*.plist" -mtime +365 -size +0c | head -10
find ~/Library/Preferences/ByHost -name "*.plist" -mtime +365 -size +0c | head -10

# Saved application states for deleted apps
echo "💾 Cleaning saved application states..."
rm -rf ~/Library/Saved\ Application\ State/*/

echo ""
echo "1️⃣3️⃣ Cleaning QuickLook & Thumbnail Caches"
# QuickLook cache can contain sensitive thumbnail data
echo "👁️  Clearing QuickLook cache..."
qlmanage -r cache
rm -rf $(getconf DARWIN_USER_CACHE_DIR)/com.apple.QuickLook.thumbnailcache

echo ""
echo "1️⃣4️⃣ Cleaning Launch Agents & Daemons (Review First!)"
echo "🚀 LaunchAgents that may need review:"
find ~/Library/LaunchAgents -name "*.plist" -exec basename {} \; 2>/dev/null | sort
find /Library/LaunchAgents -name "*.plist" -exec basename {} \; 2>/dev/null | sort
echo "⚠️  DO NOT delete Apple (com.apple.*) or essential system agents!"

echo ""
echo "1️⃣5️⃣ Advanced Cleanup (Use with Caution)"
echo "⚠️  The following commands are more aggressive - uncomment if needed:"

# Uncomment these lines if you want more aggressive cleanup:
echo "Cleaning all user caches..."
rm -rf ~/Library/Caches/*

echo "Cleaning system caches (RISKY)..."
sudo rm -rf /Library/Caches/*

echo "Cleaning WebKit databases..."
rm -rf ~/Library/WebKit/*/Databases/*

# Remove .DS_Store files from user directories (much faster)
# find ~ -name ".DS_Store" -delete 2>/dev/null
# find /Volumes -name ".DS_Store" -delete 2>/dev/null

# echo "Cleaning language files (keeps English only)..."
# sudo find /Applications -name "*.lproj" -not -name "en.lproj" -exec rm -rf {} +

# echo "Remove old iOS backups (30+ days old)..."
# find ~/Library/Application\ Support/MobileSync/Backup -type d -mtime +30 -exec rm -rf {} \;

# echo "Cleaning all Containers (VERY RISKY - only for clean slate)..."
# rm -rf ~/Library/Containers/*/Data/Library/Caches/*

echo ""
echo "🏁 Cleanup Complete!"

# Additional system maintenance
echo "🔧 Running additional system maintenance..."

# Flush DNS cache
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder

# Update Spotlight index
sudo mdutil -E / 2>/dev/null

# Clear font caches
sudo atsutil databases -remove 2>/dev/null

# Show space freed up
echo "📊 Disk space after cleanup:"
df -h /

echo ""
echo "✅ Safe operations completed!"
echo "⚠️  Consider rebooting your Mac to complete the cleanup process."
echo ""
echo "📋 What was cleaned:"
echo "   • Browser caches and temporary data"
echo "   • Application cache folders"
echo "   • System logs and crash reports"
echo "   • Temporary and download files"
echo "   • Development tool caches"
echo "   • Adobe cache files"
echo "   • Trash and hidden system files"
echo "   • QuickLook thumbnail cache"
echo "   • WebKit and Mail data"
echo "   • DNS and font caches"
echo ""
echo "🔍 Manual review recommended for:"
echo "   • ~/Library/Application Support/* (check for unknown large folders)"
echo "   • ~/Library/Containers/* (orphaned app containers)"
echo "   • ~/Library/Group Containers/* (shared app data)"
echo "   • ~/Library/Preferences/* (old .plist files)"
echo "   • ~/Library/LaunchAgents/* (startup agents)"
echo "   • iOS device backups before deletion"
echo "   • Any custom app data you want to preserve"