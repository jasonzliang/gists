#!/usr/bin/env bash

# Unified macOS Cleanup Script - Comprehensive system maintenance and cache cleanup
# Combines safety checks, extensive cleanup operations, and detailed reporting

set -euo pipefail

# Configuration
readonly SCRIPT_NAME="macOS Unified Cleanup"
readonly MIN_BACKUP_AGE=3600  # 1 hour in seconds
readonly OLD_BACKUP_DAYS=180
readonly OLD_FILE_DAYS=30
readonly MAX_FIND_ITEMS=1000  # Skip find operations on directories with more items

# Colors and formatting
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color
readonly BOLD='\033[1m'

# Utility functions
log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }
section() { echo -e "\n${BOLD}${BLUE}$1${NC}"; }

bytes_to_human() {
    local b=${1:-0}
    local d=''
    local s=0
    # Bash arrays are always 0-indexed
    local S=(Bytes KiB MiB GiB TiB PiB EiB ZiB YiB)

    while ((b >= 1024)); do
        # Calculate decimal part roughly using integer math
        d="$(printf ".%02d" $((b % 1024 * 100 / 1024)))"
        b=$((b / 1024))
        ((s++))
    done

    echo "$b$d ${S[s]}"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "Please run as root (use sudo)"
        exit 1
    fi
}

check_time_machine() {
    if tmutil status 2>/dev/null | grep -q "Running = 1"; then
        error "Time Machine is currently running. Let it finish first!"
        exit 1
    fi

    local last_backup_string=$(tmutil latestbackup 2>/dev/null | grep -Eo "[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}" || true)
    if [[ -n "$last_backup_string" ]]; then
        local last_backup_date=$(date -j -f "%Y-%m-%d-%H%M%S" "$last_backup_string" "+%s" 2>/dev/null || echo 0)
        local time_diff=$(($(date +%s) - last_backup_date))

        if ((time_diff > MIN_BACKUP_AGE)); then
            warn "Time Machine backup is older than 1 hour ($(date -j -f %s $last_backup_date 2>/dev/null || echo 'unknown'))"
            read -p "Continue anyway? (y/N): " -n 1 -r
            echo
            [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
        else
            log "Recent Time Machine backup found: $(date -j -f %s $last_backup_date 2>/dev/null || echo 'unknown')"
        fi
    else
        warn "No Time Machine backup found"
    fi
}

safe_remove() {
    local path="$1"
    local desc="${2:-$path}"

    # Handle tilde expansion for root/sudo context
    if [[ "$path" == ~* ]]; then
        local user_home=$(eval echo ~$SUDO_USER)
        path="${path/#\~/$user_home}"
    fi

    if [[ -e "$path" ]] || [[ -d "$path" ]]; then
        log "Cleaning: $desc"
        rm -rf "$path" 2>/dev/null && return 0 || warn "Could not remove: $path"
    fi
    return 1
}

safe_command() {
    local cmd="$1"
    local desc="${2:-Running command}"
    log "$desc"
    eval "$cmd" &>/dev/null || warn "Command failed: $cmd"
}

# Main cleanup functions
cleanup_basic_caches() {
    section "🗑️  Basic Cache Cleanup (Preserving User Settings)"

    # Trash and temporary files (safe to remove)
    [[ -d /Volumes ]] && rm -rf /Volumes/*/.Trashes 2>/dev/null || true
    [[ -d ~/.Trash ]] && rm -rf ~/.Trash/* 2>/dev/null || true
    [[ -d /tmp ]] && rm -rf /tmp/* 2>/dev/null || true
    [[ -d /private/var/tmp ]] && rm -rf /private/var/tmp/* 2>/dev/null || true
    log "Cleaned trash and temporary files"

    # System caches only (avoid user preference caches)
    [[ -e /Library/Caches/com.apple.iconservices.store ]] && rm -rf /Library/Caches/com.apple.iconservices.store 2>/dev/null || true
    [[ -d /Library/Caches ]] && rm -rf /Library/Caches/com.apple.preferencepanes.prefpanekit 2>/dev/null || true
    [[ -d /Library/Caches ]] && rm -rf /Library/Caches/com.apple.LaunchServices* 2>/dev/null || true
    log "Cleaned system caches"

    # Selective user caches (expand tilde properly)
    local user_home=$(eval echo ~$SUDO_USER)
    if [[ -d "$user_home/Library/Caches" ]]; then
        rm -rf "$user_home/Library/Caches/com.apple.WebKit"* 2>/dev/null || true
        rm -rf "$user_home/Library/Caches/com.apple.Safari"* 2>/dev/null || true
        rm -rf "$user_home/Library/Caches/com.google.Chrome"* 2>/dev/null || true

        # Clean cache directories (be more specific)
        for cache_dir in "$user_home/Library/Caches"/*; do
            if [[ -d "$cache_dir" ]] && [[ "$(basename "$cache_dir")" == *"Cache"* || "$(basename "$cache_dir")" == *"cache"* || "$(basename "$cache_dir")" == *"Temp"* || "$(basename "$cache_dir")" == *"temp"* ]]; then
                rm -rf "$cache_dir" 2>/dev/null || true
            fi
        done
        log "Cleaned user caches"
    fi

    # Container caches only (preserve settings)
    if [[ -d "$user_home/Library/Containers" ]]; then
        for container_cache in "$user_home/Library/Containers"/*/Data/Library/Caches/*; do
            [[ -d "$container_cache" ]] && rm -rf "$container_cache" 2>/dev/null || true
        done
        log "Cleaned container caches"
    fi

    log "✅ Basic caches cleaned while preserving user preferences"
}

cleanup_logs() {
    section "📋 Log File Cleanup"

    # System logs (check existence first)
    [[ -d /private/var/log ]] && rm -f /private/var/log/*.gz 2>/dev/null || true
    [[ -d /private/var/log ]] && rm -f /private/var/log/*.bz2 2>/dev/null || true
    [[ -d /private/var/log/asl ]] && rm -f /private/var/log/asl/*.asl 2>/dev/null || true
    [[ -d /Library/Logs ]] && rm -rf /Library/Logs/* 2>/dev/null || true
    [[ -d /Library/Logs/DiagnosticReports ]] && rm -rf /Library/Logs/DiagnosticReports/* 2>/dev/null || true
    log "Cleaned system logs"

    local user_home=$(eval echo ~$SUDO_USER)

    # User logs
    [[ -d "$user_home/Library/Logs" ]] && rm -rf "$user_home/Library/Logs"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Logs/CoreSimulator" ]] && rm -rf "$user_home/Library/Logs/CoreSimulator"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Logs/CrashReporter/MobileDevice" ]] && rm -rf "$user_home/Library/Logs/CrashReporter/MobileDevice"/* 2>/dev/null || true
    log "Cleaned user logs"

    # Mail logs
    if [[ -d "$user_home/Library/Containers/com.apple.mail/Data/Library/Logs/Mail" ]]; then
        rm -rf "$user_home/Library/Containers/com.apple.mail/Data/Library/Logs/Mail"/* 2>/dev/null || true
        log "Cleaned Mail logs"
    fi

    log "✅ Log files cleaned"
}

cleanup_browser_caches() {
    section "🌐 Browser Cache Cleanup (Preserving History/Cookies)"

    local user_home=$(eval echo ~$SUDO_USER)

    # Safari - only cache files, NOT history/cookies/bookmarks
    [[ -d "$user_home/Library/Caches/com.apple.Safari" ]] && rm -rf "$user_home/Library/Caches/com.apple.Safari/Webpage Previews"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Caches/com.apple.Safari" ]] && rm -rf "$user_home/Library/Caches/com.apple.Safari/fsCachedData"/* 2>/dev/null || true

    # Chrome - only cache, NOT history/cookies/bookmarks/passwords
    [[ -d "$user_home/Library/Caches/com.google.Chrome" ]] && rm -rf "$user_home/Library/Caches/com.google.Chrome/Default/Cache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Caches/com.google.Chrome" ]] && rm -rf "$user_home/Library/Caches/com.google.Chrome/Default/Code Cache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Caches/com.google.Chrome" ]] && rm -rf "$user_home/Library/Caches/com.google.Chrome/Default/GPUCache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Caches/com.google.Chrome" ]] && rm -rf "$user_home/Library/Caches/com.google.Chrome/ShaderCache"/* 2>/dev/null || true

    # Firefox - only cache, NOT history/cookies/bookmarks
    if [[ -d "$user_home/Library/Caches/Firefox" ]]; then
        for profile in "$user_home/Library/Caches/Firefox/Profiles"/*; do
            [[ -d "$profile" ]] && rm -rf "$profile/cache2"/* 2>/dev/null || true
            [[ -d "$profile" ]] && rm -rf "$profile/startupCache"/* 2>/dev/null || true
            [[ -d "$profile" ]] && rm -rf "$profile/thumbnails"/* 2>/dev/null || true
        done
    fi

    # WebKit - only cache files
    [[ -d "$user_home/Library/Caches/com.apple.WebKit.Networking" ]] && rm -rf "$user_home/Library/Caches/com.apple.WebKit.Networking"/* 2>/dev/null || true
    if [[ -d "$user_home/Library/WebKit" ]]; then
        for webkit_dir in "$user_home/Library/WebKit"/*; do
            [[ -d "$webkit_dir/WebKitCache" ]] && rm -rf "$webkit_dir/WebKitCache"/* 2>/dev/null || true
        done
    fi

    log "✅ Browser caches cleaned while preserving history, cookies, and bookmarks"
}

cleanup_development_tools() {
    section "⚒️  Development Tools Cleanup"

    local user_home=$(eval echo ~$SUDO_USER)

    # Xcode
    [[ -d "$user_home/Library/Developer/Xcode/DerivedData" ]] && rm -rf "$user_home/Library/Developer/Xcode/DerivedData"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Developer/Xcode/Archives" ]] && rm -rf "$user_home/Library/Developer/Xcode/Archives"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Developer/Xcode/iOS Device Logs" ]] && rm -rf "$user_home/Library/Developer/Xcode/iOS Device Logs"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Developer/CoreSimulator/Caches" ]] && rm -rf "$user_home/Library/Developer/CoreSimulator/Caches"/* 2>/dev/null || true
    log "Cleaned Xcode caches"

    # Simulator cleanup
    if command -v xcrun >/dev/null 2>&1; then
        sudo -u "$SUDO_USER" xcrun simctl delete unavailable 2>/dev/null || true
        log "Purged unavailable simulators"
    fi

    # Package managers and tools
    if command -v brew >/dev/null 2>&1; then
        sudo -u "$SUDO_USER" brew cleanup --prune=all 2>/dev/null || true
        sudo -u "$SUDO_USER" brew autoremove 2>/dev/null || true
        log "Cleaned Homebrew"
    fi
    [[ -d /Library/Caches/Homebrew ]] && rm -rf /Library/Caches/Homebrew/* 2>/dev/null || true

    if command -v gem >/dev/null 2>&1; then
        sudo -u "$SUDO_USER" gem cleanup 2>/dev/null || true
        log "Cleaned Ruby gems"
    fi

    if command -v npm >/dev/null 2>&1; then
        sudo -u "$SUDO_USER" npm cache clean --force 2>/dev/null || true
        log "Cleaned NPM cache"
    fi
    [[ -d "$user_home/.npm/_cacache" ]] && rm -rf "$user_home/.npm/_cacache"/* 2>/dev/null || true

    if command -v yarn >/dev/null 2>&1; then
        sudo -u "$SUDO_USER" yarn cache clean 2>/dev/null || true
        log "Cleaned Yarn cache"
    fi

    if command -v pod >/dev/null 2>&1; then
        sudo -u "$SUDO_USER" pod cache clean --all 2>/dev/null || true
        log "Cleaned CocoaPods cache"
    fi

    if command -v conda >/dev/null 2>&1; then
        sudo -u "$SUDO_USER" conda clean --all --yes 2>/dev/null || true
        log "Cleaned Conda cache"
    fi

    if command -v go >/dev/null 2>&1; then
        sudo -u "$SUDO_USER" go clean -cache 2>/dev/null || true
        log "Cleaned Go cache"
    fi

    # Cache directories
    [[ -d "$user_home/.bundle/cache" ]] && rm -rf "$user_home/.bundle/cache"/* 2>/dev/null || true
    [[ -d "$user_home/.gradle/caches" ]] && rm -rf "$user_home/.gradle/caches"/* 2>/dev/null || true
    [[ -d "$user_home/.m2/repository" ]] && rm -rf "$user_home/.m2/repository"/* 2>/dev/null || true
    [[ -d "$user_home/.cache/pip" ]] && rm -rf "$user_home/.cache/pip"/* 2>/dev/null || true
    log "Cleaned package manager caches"

    # Python bytecode - only in common development directories
    local dev_dirs=("$user_home/Documents" "$user_home/Desktop" "$user_home/Developer" "$user_home/Projects" "$user_home/Code")
    for dev_dir in "${dev_dirs[@]}"; do
        if [[ -d "$dev_dir" ]]; then
            log "Cleaning Python bytecode in $dev_dir"
            find "$dev_dir" -maxdepth 3 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
            find "$dev_dir" -maxdepth 3 -name "*.pyc" -delete 2>/dev/null || true
            find "$dev_dir" -maxdepth 3 -name "*.pyo" -delete 2>/dev/null || true
            find "$dev_dir" -maxdepth 3 -type d -name ".ipynb_checkpoints" -exec rm -rf {} + 2>/dev/null || true
        fi
    done
}

cleanup_applications() {
    section "📱 Application Cache Cleanup (Preserving Settings)"

    local user_home=$(eval echo ~$SUDO_USER)

    # Adobe - only cache files, NOT preferences or recent files
    [[ -d "$user_home/Library/Application Support/Adobe/Common/Media Cache Files" ]] && rm -rf "$user_home/Library/Application Support/Adobe/Common/Media Cache Files"/* 2>/dev/null || true

    if [[ -d "$user_home/Library/Caches/Adobe" ]]; then
        for adobe_cache in "$user_home/Library/Caches/Adobe"/*/Cache; do
            [[ -d "$adobe_cache" ]] && rm -rf "$adobe_cache"/* 2>/dev/null || true
        done
    fi
    log "Cleaned Adobe caches"

    # Communication apps - only cache, NOT chat history or settings
    [[ -d "$user_home/Library/Application Support/Slack/Cache" ]] && rm -rf "$user_home/Library/Application Support/Slack/Cache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/Slack/Code Cache" ]] && rm -rf "$user_home/Library/Application Support/Slack/Code Cache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/Slack/GPUCache" ]] && rm -rf "$user_home/Library/Application Support/Slack/GPUCache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Caches/com.tinyspeck.slackmacgap" ]] && rm -rf "$user_home/Library/Caches/com.tinyspeck.slackmacgap"/* 2>/dev/null || true

    [[ -d "$user_home/Library/Application Support/discord/Cache" ]] && rm -rf "$user_home/Library/Application Support/discord/Cache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/discord/Code Cache" ]] && rm -rf "$user_home/Library/Application Support/discord/Code Cache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/discord/GPUCache" ]] && rm -rf "$user_home/Library/Application Support/discord/GPUCache"/* 2>/dev/null || true

    [[ -d "$user_home/Library/Application Support/Microsoft/Teams/Cache" ]] && rm -rf "$user_home/Library/Application Support/Microsoft/Teams/Cache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/Microsoft/Teams/Code Cache" ]] && rm -rf "$user_home/Library/Application Support/Microsoft/Teams/Code Cache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/Microsoft/Teams/GPUCache" ]] && rm -rf "$user_home/Library/Application Support/Microsoft/Teams/GPUCache"/* 2>/dev/null || true

    [[ -d "$user_home/Library/Application Support/zoom.us/AutoDownload" ]] && rm -rf "$user_home/Library/Application Support/zoom.us/AutoDownload"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Caches/us.zoom.xos" ]] && rm -rf "$user_home/Library/Caches/us.zoom.xos"/* 2>/dev/null || true
    log "Cleaned communication app caches"

    # Development IDEs - only cache, NOT settings, recent projects, or plugins
    [[ -d "$user_home/Library/Application Support/Code/Cache" ]] && rm -rf "$user_home/Library/Application Support/Code/Cache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/Code/CachedData" ]] && rm -rf "$user_home/Library/Application Support/Code/CachedData"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/Code/CachedExtensions" ]] && rm -rf "$user_home/Library/Application Support/Code/CachedExtensions"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/Code/CachedExtensionVSIXs" ]] && rm -rf "$user_home/Library/Application Support/Code/CachedExtensionVSIXs"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/Code/Code Cache" ]] && rm -rf "$user_home/Library/Application Support/Code/Code Cache"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/Code/GPUCache" ]] && rm -rf "$user_home/Library/Application Support/Code/GPUCache"/* 2>/dev/null || true

    # JetBrains IDEs - only cache directories
    if [[ -d "$user_home/Library/Caches/JetBrains" ]]; then
        for ide_cache in "$user_home/Library/Caches/JetBrains"/*/caches; do
            [[ -d "$ide_cache" ]] && rm -rf "$ide_cache"/* 2>/dev/null || true
        done
    fi
    log "Cleaned development IDE caches"

    # Media and entertainment - only cache, NOT playlists or preferences
    [[ -d "$user_home/Library/Caches/com.spotify.client/Data" ]] && rm -rf "$user_home/Library/Caches/com.spotify.client/Data"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Application Support/Spotify/PersistentCache" ]] && rm -rf "$user_home/Library/Application Support/Spotify/PersistentCache"/* 2>/dev/null || true

    [[ -d "$user_home/Library/Application Support/Steam/steamapps/downloading" ]] && rm -rf "$user_home/Library/Application Support/Steam/steamapps/downloading"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Caches/com.valvesoftware.steam" ]] && rm -rf "$user_home/Library/Caches/com.valvesoftware.steam"/* 2>/dev/null || true

    # Other applications - cache only
    [[ -d "$user_home/Library/Caches/Java/tmp" ]] && rm -rf "$user_home/Library/Caches/Java/tmp"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Caches/com.apple.SpeechRecognitionCore" ]] && rm -rf "$user_home/Library/Caches/com.apple.SpeechRecognitionCore"/* 2>/dev/null || true

    log "✅ Application caches cleaned while preserving user settings and data"
}

cleanup_ios_data() {
    section "📱 iOS Device Data Cleanup"

    local user_home=$(eval echo ~$SUDO_USER)
    local backup_dir="$user_home/Library/Application Support/MobileSync/Backup"

    if [[ -d "$backup_dir" ]]; then
        log "iOS backup directory found"
        local backup_count=$(ls "$backup_dir" 2>/dev/null | wc -l)

        if [[ $backup_count -lt 20 ]]; then
            log "Scanning for old iOS backups (found $backup_count backups)"
            find "$backup_dir" -maxdepth 1 -type d -mtime +$OLD_BACKUP_DAYS -exec rm -rf {} + 2>/dev/null || true
            log "Removed iOS backups older than $OLD_BACKUP_DAYS days"
        else
            warn "Too many iOS backups ($backup_count) - manual cleanup recommended"
            warn "Run: ls -la '$backup_dir' | sort -k6,7"
        fi
    else
        log "No iOS backups directory found"
    fi

    log "✅ iOS data cleanup completed"
}

cleanup_mail_data() {
    section "📧 Mail Cache Cleanup (Preserving Emails)"

    local user_home=$(eval echo ~$SUDO_USER)

    # Only remove downloads and temporary cache, NOT actual emails
    [[ -d "$user_home/Library/Containers/com.apple.mail/Data/Library/Mail Downloads" ]] && rm -rf "$user_home/Library/Containers/com.apple.mail/Data/Library/Mail Downloads"/* 2>/dev/null || true
    [[ -d "$user_home/Library/Caches/com.apple.mail" ]] && rm -rf "$user_home/Library/Caches/com.apple.mail"/* 2>/dev/null || true

    # Vacuum Mail database for performance (doesn't delete emails)
    local mail_db_found=false
    for mail_db in "$user_home/Library/Mail/V"*/MailData/Envelope\ Index; do
        if [[ -f "$mail_db" ]]; then
            log "Optimizing Mail database (preserving all emails)"
            sqlite3 "$mail_db" vacuum 2>/dev/null || true
            mail_db_found=true
            break
        fi
    done

    if [[ "$mail_db_found" == false ]]; then
        log "No Mail database found to optimize"
    fi

    log "✅ Mail caches cleaned while preserving all emails and settings"
}

cleanup_system_caches() {
    section "🖥️  System Cache Cleanup"

    local user_home=$(eval echo ~$SUDO_USER)

    # QuickLook and thumbnails
    if command -v qlmanage >/dev/null 2>&1; then
        sudo -u "$SUDO_USER" qlmanage -r cache 2>/dev/null || true
        log "Reset QuickLook cache"
    fi

    # Thumbnail cache
    local quicklook_cache=$(sudo -u "$SUDO_USER" getconf DARWIN_USER_CACHE_DIR 2>/dev/null)/com.apple.QuickLook.thumbnailcache
    [[ -e "$quicklook_cache" ]] && rm -rf "$quicklook_cache" 2>/dev/null || true
    [[ -d "$user_home/Library/Thumbnails" ]] && rm -rf "$user_home/Library/Thumbnails"/* 2>/dev/null || true
    log "Cleaned thumbnail caches"

    # Font cache
    if command -v atsutil >/dev/null 2>&1; then
        atsutil databases -remove 2>/dev/null || true
        log "Cleared font cache"
    fi

    # DNS cache
    dscacheutil -flushcache 2>/dev/null || true
    killall -HUP mDNSResponder 2>/dev/null || true
    log "Flushed DNS cache"

    # CUPS printing
    [[ -d /var/spool/cups/cache ]] && rm -rf /var/spool/cups/cache/* 2>/dev/null || true
    log "Cleaned printing cache"

    # Kernel extensions
    if command -v kextcache >/dev/null 2>&1; then
        kextcache --clear-staging 2>/dev/null || true
        log "Cleared kernel extension cache"
    fi

    # Software update catalog
    if command -v softwareupdate >/dev/null 2>&1; then
        softwareupdate --clear-catalog 2>/dev/null || true
        log "Cleared software update catalog"
    fi

    log "✅ System caches cleaned"
}

cleanup_containers() {
    section "📦 Container Cleanup"

    local user_home=$(eval echo ~$SUDO_USER)

    # Clean cache files from all containers (direct path access)
    if [[ -d "$user_home/Library/Group Containers" ]]; then
        for group_container in "$user_home/Library/Group Containers"/*; do
            [[ -d "$group_container/Cache" ]] && rm -rf "$group_container/Cache"/* 2>/dev/null || true
            [[ -d "$group_container/cache" ]] && rm -rf "$group_container/cache"/* 2>/dev/null || true
        done
        log "Cleaned group container caches"
    fi

    # List containers efficiently
    if [[ -d "$user_home/Library/Containers" ]]; then
        local container_count=$(ls "$user_home/Library/Containers" 2>/dev/null | wc -l)
        log "Found $container_count app containers"
        warn "For manual review of large containers, run:"
        warn "  ls -la '$user_home/Library/Containers' | sort -k5 -nr | head -20"
    fi

    if [[ -d "$user_home/Library/Group Containers" ]]; then
        local group_count=$(ls "$user_home/Library/Group Containers" 2>/dev/null | wc -l)
        log "Found $group_count group containers"
    fi

    log "✅ Container cleanup completed"
}

cleanup_docker() {
    section "🐳 Docker Cleanup"

    if command -v docker &> /dev/null; then
        log "Cleaning Docker containers, images, and volumes"
        sudo -u "$SUDO_USER" docker container prune -f 2>/dev/null || true
        sudo -u "$SUDO_USER" docker image prune -af 2>/dev/null || true
        sudo -u "$SUDO_USER" docker volume prune -f 2>/dev/null || true
        sudo -u "$SUDO_USER" docker system prune -af 2>/dev/null || true
        log "Docker cleanup completed"
    else
        log "Docker not found - skipping Docker cleanup"
    fi

    log "✅ Docker cleanup completed"
}

cleanup_old_files() {
    section "🗂️  Old File Cleanup (Preserving Important Data)"

    local user_home=$(eval echo ~$SUDO_USER)

    # Only check Downloads if it exists and isn't too large
    if [[ -d "$user_home/Downloads" ]]; then
        local download_count=$(ls "$user_home/Downloads" 2>/dev/null | wc -l)
        if [[ $download_count -lt 1000 ]]; then
            local old_downloads=$(find "$user_home/Downloads" -maxdepth 2 -type f -mtime +$OLD_FILE_DAYS 2>/dev/null | wc -l)
            if [[ $old_downloads -gt 0 ]]; then
                warn "Found $old_downloads downloads older than $OLD_FILE_DAYS days"
                read -p "Remove old downloads? (y/N): " -n 1 -r
                echo
                if [[ $REPLY =~ ^[Yy]$ ]]; then
                    find "$user_home/Downloads" -maxdepth 2 -type f -mtime +$OLD_FILE_DAYS -delete 2>/dev/null || true
                    log "Removed old downloads"
                else
                    log "Skipped old downloads cleanup"
                fi
            else
                log "No old downloads found to clean"
            fi
        else
            warn "Downloads directory too large ($download_count files) - skipping automatic cleanup"
            warn "Manually review: ls -la '$user_home/Downloads' | sort -k6,7"
        fi
    fi

    # .DS_Store files - only in common user directories
    # local user_dirs=("$user_home/Desktop" "$user_home/Documents" "$user_home/Downloads" "$user_home/Pictures" "$user_home/Movies" "$user_home/Music")
    # for user_dir in "${user_dirs[@]}"; do
    #     if [[ -d "$user_dir" ]]; then
    #         log "Removing .DS_Store files from $user_dir"
    #         find "$user_dir" -name ".DS_Store" -delete 2>/dev/null || true
    #     fi
    # done

    # External volumes .DS_Store (only if mounted and accessible)
    # if [[ -d /Volumes ]]; then
    #     for volume in /Volumes/*/; do
    #         if [[ -w "$volume" ]]; then
    #             find "$volume" -maxdepth 3 -name ".DS_Store" -delete 2>/dev/null || true
    #         fi
    #     done
    #     log "Removed .DS_Store files from external volumes"
    # fi

    # log "✅ Cleaned .DS_Store files and optionally old downloads"
}

final_maintenance() {
    section "🔧 Final System Maintenance"

    # Memory purge
    if command -v purge >/dev/null 2>&1; then
        purge 2>/dev/null || true
        log "Memory purged"
    fi

    # Update Spotlight index
    if command -v mdutil >/dev/null 2>&1; then
        mdutil -E / 2>/dev/null || true
        log "Spotlight reindex initiated"
    fi

    log "✅ All cleanup operations completed successfully!"
}

# Main execution
main() {
    section "🧹 $SCRIPT_NAME"
    warn "⚠️  Always backup your Mac before running cleanup operations!"

    # Pre-flight checks
    check_root
    check_time_machine

    # Record initial disk space
    local old_available=$(df / | tail -1 | awk '{print $4}')
    section "📊 Initial disk space: $(df -h / | tail -1 | awk '{print $4}') available"

    # Execute cleanup operations
    cleanup_basic_caches
    cleanup_logs
    cleanup_browser_caches
    cleanup_development_tools
    cleanup_applications
    cleanup_ios_data
    cleanup_mail_data
    cleanup_system_caches
    cleanup_containers
    cleanup_docker
    cleanup_old_files
    final_maintenance

    # Calculate and display space recovered
    local new_available=$(df / | tail -1 | awk '{print $4}')
    local recovered=$(( (new_available - old_available) * 512 ))

    section "✅ Cleanup Complete!"
    log "💾 Final disk space: $(df -h / | tail -1 | awk '{print $4}') available"
    if ((recovered > 0)); then
        log "🎉 Space recovered: $(bytes_to_human $recovered)"
    fi

    echo
    warn "🔄 Consider rebooting your Mac to complete the cleanup process"
    warn "📋 Manual review recommended for:"
    warn "   • ~/Library/Application Support/* (large unknown folders)"
    warn "   • ~/Library/Containers/* (orphaned app containers)"
    warn "   • ~/Library/LaunchAgents/* (startup agents)"
    warn "   • iOS device backups before deletion"
}

# Script entry point
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi