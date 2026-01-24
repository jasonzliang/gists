#!/usr/bin/env bash
#
# macOS Unified Cleanup Script v3
# Comprehensive system maintenance combining best practices from multiple cleanup approaches
#
# Features:
#   - Safe by default (prunes by age, prompts before destructive actions)
#   - Supports --dry-run, --aggressive, and -y/--yes (noninteractive)
#   - Preserves user settings, history, cookies, and important data
#   - Reports space usage for manual review
#
# Usage:
#   chmod +x mac_cleanup_v3.sh
#   sudo ./mac_cleanup_v3.sh --dry-run       # Preview what would be cleaned
#   sudo ./mac_cleanup_v3.sh                 # Standard cleanup with prompts
#   sudo ./mac_cleanup_v3.sh --aggressive    # More thorough cleanup
#   sudo ./mac_cleanup_v3.sh -y              # Auto-accept all prompts
#   sudo ./mac_cleanup_v3.sh --aggressive -y # Full cleanup, no prompts
#

set -euo pipefail

# -----------------------------
# Configuration
# -----------------------------
readonly SCRIPT_NAME="macOS Unified Cleanup v3"
readonly SCRIPT_VERSION="3.0.0"
readonly MIN_BACKUP_AGE=3600      # 1 hour in seconds
readonly OLD_BACKUP_DAYS=180
readonly OLD_FILE_DAYS=30
readonly KEEP_LOG_DAYS=30
readonly KEEP_TMP_DAYS=7

# Colors and formatting
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly NC='\033[0m'
readonly BOLD='\033[1m'

# Modes
DRY_RUN=0
AGGRESSIVE=0
ASSUME_YES=0

# Globals set in main()
SUDO_USER_RESOLVED=""
USER_HOME=""

# -----------------------------
# Utility functions
# -----------------------------
log()     { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1" >&2; }
section() { echo -e "\n${BOLD}${BLUE}=== $1 ===${NC}"; }
debug()   { [[ "${DEBUG:-0}" == "1" ]] && echo -e "${CYAN}[DEBUG]${NC} $1" || true; }

bytes_to_human() {
    local b=${1:-0}
    local d=''
    local s=0
    local S=(Bytes KiB MiB GiB TiB PiB)

    while ((b >= 1024 && s < 5)); do
        d="$(printf ".%02d" $((b % 1024 * 100 / 1024)))"
        b=$((b / 1024))
        ((s++))
    done

    echo "$b$d ${S[s]}"
}

is_tty() {
    [[ -t 0 && -t 1 ]]
}

confirm() {
    local question="$1"
    local default="${2:-N}"

    if (( ASSUME_YES )); then
        log "Auto-yes (-y): $question"
        return 0
    fi

    if ! is_tty; then
        if [[ "$default" =~ ^[Yy]$ ]]; then
            warn "Non-interactive: defaulting YES for: $question"
            return 0
        fi
        warn "Non-interactive: defaulting NO for: $question (use -y to auto-accept)"
        return 1
    fi

    local suffix
    if [[ "$default" =~ ^[Yy]$ ]]; then
        suffix="(Y/n)"
    else
        suffix="(y/N)"
    fi

    local reply=""
    if ! read -r -p "$question $suffix: " reply; then
        reply="$default"
    fi
    reply="${reply:-$default}"
    [[ "$reply" =~ ^[Yy]$ ]]
}

print_usage() {
    cat <<EOF
$SCRIPT_NAME v$SCRIPT_VERSION

Usage:
  sudo ./mac_cleanup_v3.sh [OPTIONS]

Options:
  --dry-run     Preview what would be removed without deleting anything
  --aggressive  Enable more thorough cleanup operations (with additional prompts)
  -y, --yes     Auto-accept all prompts (noninteractive mode)
  -h, --help    Show this help message
  --version     Show version information

Examples:
  sudo ./mac_cleanup_v3.sh --dry-run       # Safe preview
  sudo ./mac_cleanup_v3.sh                 # Interactive cleanup
  sudo ./mac_cleanup_v3.sh --aggressive -y # Full automated cleanup
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)
                DRY_RUN=1
                ;;
            --aggressive)
                AGGRESSIVE=1
                ;;
            -y|--yes)
                ASSUME_YES=1
                ;;
            -h|--help)
                print_usage
                exit 0
                ;;
            --version)
                echo "$SCRIPT_NAME v$SCRIPT_VERSION"
                exit 0
                ;;
            *)
                warn "Unknown option: $1"
                ;;
        esac
        shift
    done
}

check_root() {
    if [[ ${EUID:-0} -ne 0 ]]; then
        error "Please run as root (use sudo)"
        exit 1
    fi
}

resolve_user() {
    local su="${SUDO_USER:-}"
    if [[ -z "$su" || "$su" == "root" ]]; then
        su="$(stat -f%Su /dev/console 2>/dev/null || true)"
    fi
    if [[ -z "$su" || "$su" == "root" ]]; then
        error "Could not determine target user. Please run via: sudo ./mac_cleanup_v3.sh"
        exit 1
    fi

    SUDO_USER_RESOLVED="$su"
    USER_HOME="$(eval echo "~$SUDO_USER_RESOLVED")"
}

check_time_machine() {
    command -v tmutil >/dev/null 2>&1 || { warn "tmutil not found; skipping Time Machine checks."; return 0; }

    if tmutil status 2>/dev/null | grep -q "Running = 1"; then
        error "Time Machine is currently running. Please wait for it to finish."
        exit 1
    fi

    local last_backup_string
    last_backup_string="$(tmutil latestbackup 2>/dev/null | grep -Eo "[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}" || true)"

    if [[ -n "$last_backup_string" ]]; then
        local last_backup_date
        last_backup_date="$(date -j -f "%Y-%m-%d-%H%M%S" "$last_backup_string" "+%s" 2>/dev/null || echo 0)"
        local time_diff
        time_diff="$(($(date +%s) - last_backup_date))"

        if ((time_diff > MIN_BACKUP_AGE)); then
            warn "Time Machine backup is older than 1 hour."
            if ! confirm "Continue without recent backup?" "N"; then
                exit 1
            fi
        else
            log "Recent Time Machine backup found."
        fi
    else
        warn "No Time Machine backup detected."
    fi
}

# -----------------------------
# DRY-RUN aware removal helpers
# -----------------------------
rm_rf() {
    local path="$1"
    local desc="${2:-$path}"

    [[ -n "$path" ]] || return 0

    if [[ -e "$path" || -d "$path" ]]; then
        if (( DRY_RUN )); then
            echo -e "${YELLOW}[DRY]${NC} Would remove: $desc"
            return 0
        fi
        rm -rf "$path" 2>/dev/null || true
    fi
}

rm_children() {
    local dir="$1"
    local desc="${2:-$dir}"

    [[ -d "$dir" ]] || return 0

    if (( DRY_RUN )); then
        echo -e "${YELLOW}[DRY]${NC} Would remove contents: $desc/*"
        return 0
    fi

    local old_nullglob old_dotglob
    old_nullglob="$(shopt -p nullglob || true)"
    old_dotglob="$(shopt -p dotglob || true)"
    shopt -s nullglob dotglob

    local items=( "$dir"/* )

    eval "$old_nullglob" 2>/dev/null || true
    eval "$old_dotglob" 2>/dev/null || true

    if (( ${#items[@]} > 0 )); then
        rm -rf "${items[@]}" 2>/dev/null || true
    fi
}

safe_command() {
    local cmd="$1"
    local desc="${2:-Running command}"
    log "$desc"
    if (( DRY_RUN )); then
        echo -e "${YELLOW}[DRY]${NC} Would run: $cmd"
        return 0
    fi
    eval "$cmd" &>/dev/null || warn "Command may have partially failed: $desc"
}

run_as_user() {
    local cmd="$1"
    local desc="${2:-Running as user}"
    log "$desc"
    if (( DRY_RUN )); then
        echo -e "${YELLOW}[DRY]${NC} Would run as $SUDO_USER_RESOLVED: $cmd"
        return 0
    fi
    sudo -u "$SUDO_USER_RESOLVED" bash -c "$cmd" &>/dev/null || warn "Command may have partially failed: $desc"
}

prune_old_files() {
    local dir="$1"
    local days="$2"
    shift 2

    [[ -d "$dir" ]] || return 0

    local one
    one="$(find "$dir" -mindepth 1 "$@" -mtime +"$days" -print -quit 2>/dev/null || true)"
    [[ -z "$one" ]] && return 0

    if (( DRY_RUN )); then
        echo -e "${YELLOW}[DRY]${NC} Would prune files older than ${days}d in: $dir"
        return 0
    fi

    find "$dir" -mindepth 1 "$@" -mtime +"$days" -print0 2>/dev/null | xargs -0 rm -rf 2>/dev/null || true
}

report_top_dirs() {
    local dir="$1"
    local n="${2:-20}"
    [[ -d "$dir" ]] || return 0
    echo -e "\n${CYAN}Top items in: $dir${NC}"
    du -xhd 1 "$dir" 2>/dev/null | sort -hr | head -n "$n" || true
}

# -----------------------------
# High-impact optional cleanups
# -----------------------------
cleanup_time_machine_snapshots() {
    section "Time Machine Local Snapshots"
    command -v tmutil >/dev/null 2>&1 || { log "tmutil not found - skipping"; return 0; }

    local snaps
    snaps="$(tmutil listlocalsnapshots / 2>/dev/null || true)"
    if [[ -z "$snaps" ]]; then
        log "No local snapshots detected."
        return 0
    fi

    warn "Local snapshots can consume significant 'purgeable' space."
    if confirm "Thin local snapshots to reclaim ~10GB?" "N"; then
        safe_command "tmutil thinlocalsnapshots / 10000000000 4" "Thinning local snapshots"
    else
        log "Skipped snapshot thinning"
    fi
}

cleanup_macos_installers() {
    section "macOS Installers"
    local installers=()
    local old_nullglob
    old_nullglob="$(shopt -p nullglob || true)"
    shopt -s nullglob
    installers=( /Applications/Install\ macOS*.app )
    eval "$old_nullglob" 2>/dev/null || true

    if (( ${#installers[@]} == 0 )); then
        log "No macOS installer apps found"
        return 0
    fi

    warn "Found installer app(s):"
    for app in "${installers[@]}"; do
        echo "  - $app"
    done

    if confirm "Remove these installer apps?" "N"; then
        for app in "${installers[@]}"; do
            rm_rf "$app" "Installer: $app"
        done
        log "Removed installer apps"
    else
        log "Skipped installer removal"
    fi
}

# -----------------------------
# Core cleanup functions
# -----------------------------
cleanup_trash_and_temp() {
    section "Trash and Temporary Files"

    # User trash
    rm_children "$USER_HOME/.Trash" "User Trash"

    # Volume trashes
    if [[ -d /Volumes ]]; then
        local vol
        for vol in /Volumes/*; do
            [[ -d "$vol/.Trashes" ]] && rm_children "$vol/.Trashes" "Volume Trash: $vol"
        done
    fi

    # Temp directories - prune by age for safety
    prune_old_files "/tmp" "$KEEP_TMP_DAYS" -type f
    prune_old_files "/private/var/tmp" "$KEEP_TMP_DAYS" -type f
    prune_old_files "/tmp" "$KEEP_TMP_DAYS" -type d -empty
    prune_old_files "/private/var/tmp" "$KEEP_TMP_DAYS" -type d -empty

    log "Trash emptied; temp files older than ${KEEP_TMP_DAYS} days removed"
}

cleanup_system_caches() {
    section "System Caches"

    # Apple system caches
    rm_rf "/Library/Caches/com.apple.iconservices.store" "IconServices cache"
    rm_rf "/Library/Caches/com.apple.preferencepanes.prefpanekit" "PreferencePanes cache"

    if [[ -d /Library/Caches ]]; then
        local item
        for item in /Library/Caches/com.apple.LaunchServices*; do
            [[ -e "$item" ]] && rm_rf "$item" "LaunchServices cache"
        done
    fi

    log "System caches cleaned"
}

cleanup_user_caches() {
    section "User Caches (Preserving Preferences)"

    [[ -d "$USER_HOME/Library/Caches" ]] || return 0

    # Known safe-to-remove caches
    local cache
    for cache in \
        "$USER_HOME/Library/Caches/com.apple.WebKit"* \
        "$USER_HOME/Library/Caches/com.apple.Safari"* \
        "$USER_HOME/Library/Caches/com.google.Chrome"* \
        "$USER_HOME/Library/Caches/com.apple.SpeechRecognitionCore" \
        "$USER_HOME/Library/Caches/Java/tmp"; do
        [[ -e "$cache" ]] && rm_rf "$cache" "Cache: $(basename "$cache")"
    done

    # Aggressive: remove cache/temp-like directories
    if (( AGGRESSIVE )); then
        warn "AGGRESSIVE: Removing cache/temp directories under ~/Library/Caches"
        local cache_dir
        for cache_dir in "$USER_HOME/Library/Caches"/*; do
            [[ -d "$cache_dir" ]] || continue
            local base
            base="$(basename "$cache_dir")"
            if [[ "$base" == *"Cache"* || "$base" == *"cache"* || "$base" == *"Temp"* || "$base" == *"temp"* ]]; then
                rm_rf "$cache_dir" "Cache folder: $base"
            fi
        done
    fi

    log "User caches cleaned"
}

cleanup_container_caches() {
    section "App Container Caches"

    # Standard containers
    if [[ -d "$USER_HOME/Library/Containers" ]]; then
        local container_cache
        for container_cache in "$USER_HOME/Library/Containers"/*/Data/Library/Caches/*; do
            [[ -d "$container_cache" ]] && rm_rf "$container_cache" "Container cache: $(basename "$container_cache")"
        done
        log "Container caches cleaned"
    fi

    # Group containers
    if [[ -d "$USER_HOME/Library/Group Containers" ]]; then
        local group_container
        for group_container in "$USER_HOME/Library/Group Containers"/*; do
            rm_children "$group_container/Cache" "Group cache: $(basename "$group_container")"
            rm_children "$group_container/cache" "Group cache: $(basename "$group_container")"
        done
        log "Group container caches cleaned"
    fi
}

cleanup_logs() {
    section "Log Files (Keeping Recent)"

    # System logs - prune by age
    prune_old_files "/private/var/log" "$KEEP_LOG_DAYS" -type f \( -name "*.gz" -o -name "*.bz2" -o -name "*.old" \)
    prune_old_files "/private/var/log/asl" "$KEEP_LOG_DAYS" -type f -name "*.asl"
    prune_old_files "/Library/Logs" "$KEEP_LOG_DAYS" -type f -name "*.log"
    prune_old_files "/Library/Logs/DiagnosticReports" "$KEEP_LOG_DAYS" -type f

    # User logs
    prune_old_files "$USER_HOME/Library/Logs" "$KEEP_LOG_DAYS" -type f
    prune_old_files "$USER_HOME/Library/Logs/DiagnosticReports" "$KEEP_LOG_DAYS" -type f
    prune_old_files "$USER_HOME/Library/Logs/CrashReporter" "$KEEP_LOG_DAYS" -type f
    prune_old_files "$USER_HOME/Library/Logs/CoreSimulator" "$KEEP_LOG_DAYS" -type f
    prune_old_files "$USER_HOME/Library/Logs/CrashReporter/MobileDevice" "$KEEP_LOG_DAYS" -type f

    # Mail logs
    prune_old_files "$USER_HOME/Library/Containers/com.apple.mail/Data/Library/Logs/Mail" "$KEEP_LOG_DAYS" -type f

    log "Logs older than ${KEEP_LOG_DAYS} days pruned"
}

cleanup_browser_caches() {
    section "Browser Caches (Preserving History/Cookies/Bookmarks)"

    # Safari
    rm_children "$USER_HOME/Library/Caches/com.apple.Safari/Webpage Previews" "Safari Webpage Previews"
    rm_children "$USER_HOME/Library/Caches/com.apple.Safari/fsCachedData" "Safari fsCachedData"

    # Chrome
    rm_children "$USER_HOME/Library/Caches/com.google.Chrome/Default/Cache" "Chrome Cache"
    rm_children "$USER_HOME/Library/Caches/com.google.Chrome/Default/Code Cache" "Chrome Code Cache"
    rm_children "$USER_HOME/Library/Caches/com.google.Chrome/Default/GPUCache" "Chrome GPUCache"
    rm_children "$USER_HOME/Library/Caches/com.google.Chrome/ShaderCache" "Chrome ShaderCache"

    # Firefox
    if [[ -d "$USER_HOME/Library/Caches/Firefox/Profiles" ]]; then
        local profile
        for profile in "$USER_HOME/Library/Caches/Firefox/Profiles"/*; do
            [[ -d "$profile" ]] || continue
            rm_children "$profile/cache2" "Firefox cache2"
            rm_children "$profile/startupCache" "Firefox startupCache"
            rm_children "$profile/thumbnails" "Firefox thumbnails"
        done
    fi

    # WebKit
    rm_children "$USER_HOME/Library/Caches/com.apple.WebKit.Networking" "WebKit Networking"
    if [[ -d "$USER_HOME/Library/WebKit" ]]; then
        local webkit_dir
        for webkit_dir in "$USER_HOME/Library/WebKit"/*; do
            rm_children "$webkit_dir/WebKitCache" "WebKitCache"
        done
    fi

    log "Browser caches cleaned"
}

cleanup_development_tools() {
    section "Development Tools"

    # Xcode
    rm_children "$USER_HOME/Library/Developer/Xcode/DerivedData" "Xcode DerivedData"
    rm_children "$USER_HOME/Library/Developer/Xcode/Archives" "Xcode Archives"
    rm_children "$USER_HOME/Library/Developer/Xcode/iOS Device Logs" "Xcode iOS Device Logs"
    rm_children "$USER_HOME/Library/Developer/CoreSimulator/Caches" "CoreSimulator Caches"

    # Simulator cleanup
    if command -v xcrun >/dev/null 2>&1; then
        run_as_user "xcrun simctl delete unavailable" "Removing unavailable simulators"
    fi

    # iOS DeviceSupport (aggressive)
    if (( AGGRESSIVE )); then
        local ds_dir="$USER_HOME/Library/Developer/Xcode/iOS DeviceSupport"
        if [[ -d "$ds_dir" ]]; then
            warn "AGGRESSIVE: iOS DeviceSupport can be very large"
            if confirm "Prune iOS DeviceSupport older than 180 days?" "N"; then
                prune_old_files "$ds_dir" 180 -type d
                log "Pruned old iOS DeviceSupport"
            fi
        fi
    fi

    log "Xcode caches cleaned"

    # Homebrew
    if command -v brew >/dev/null 2>&1; then
        run_as_user "brew cleanup --prune=all" "Homebrew cleanup"
        run_as_user "brew autoremove" "Homebrew autoremove"
        rm_children "/Library/Caches/Homebrew" "Homebrew system cache"
    fi

    # NPM
    if command -v npm >/dev/null 2>&1; then
        run_as_user "npm cache clean --force" "NPM cache clean"
        rm_children "$USER_HOME/.npm/_cacache" "NPM cacache"
    fi

    # Yarn
    if command -v yarn >/dev/null 2>&1; then
        run_as_user "yarn cache clean" "Yarn cache clean"
    fi

    # Ruby gems
    if command -v gem >/dev/null 2>&1; then
        run_as_user "gem cleanup" "Ruby gem cleanup"
    fi

    # CocoaPods
    if command -v pod >/dev/null 2>&1; then
        run_as_user "pod cache clean --all" "CocoaPods cache clean"
    fi

    # Conda
    if command -v conda >/dev/null 2>&1; then
        run_as_user "conda clean --all --yes" "Conda cache clean"
    fi

    # Go
    if command -v go >/dev/null 2>&1; then
        run_as_user "go clean -cache" "Go cache clean"
    fi

    # Package manager caches (aggressive)
    if (( AGGRESSIVE )); then
        rm_children "$USER_HOME/.bundle/cache" "Bundler cache"
        rm_children "$USER_HOME/.gradle/caches" "Gradle caches"
        rm_children "$USER_HOME/.cache/pip" "pip cache"

        warn "AGGRESSIVE: Maven repository cleanup will require re-downloading dependencies"
        if confirm "Clear Maven ~/.m2/repository?" "N"; then
            rm_children "$USER_HOME/.m2/repository" "Maven repository"
        fi
    fi

    # Python bytecode in common dev directories
    local dev_dirs=("$USER_HOME/Documents" "$USER_HOME/Desktop" "$USER_HOME/Developer" "$USER_HOME/Projects" "$USER_HOME/Code")
    local dev_dir
    for dev_dir in "${dev_dirs[@]}"; do
        [[ -d "$dev_dir" ]] || continue
        log "Cleaning Python bytecode in $(basename "$dev_dir")"
        if (( DRY_RUN )); then
            echo -e "${YELLOW}[DRY]${NC} Would remove __pycache__, *.pyc, .ipynb_checkpoints in: $dev_dir"
            continue
        fi
        find "$dev_dir" -maxdepth 3 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        find "$dev_dir" -maxdepth 3 -name "*.pyc" -delete 2>/dev/null || true
        find "$dev_dir" -maxdepth 3 -name "*.pyo" -delete 2>/dev/null || true
        find "$dev_dir" -maxdepth 3 -type d -name ".ipynb_checkpoints" -exec rm -rf {} + 2>/dev/null || true
    done

    log "Development tools cleaned"
}

cleanup_applications() {
    section "Application Caches (Preserving Settings)"

    # Adobe
    rm_children "$USER_HOME/Library/Application Support/Adobe/Common/Media Cache Files" "Adobe Media Cache"
    if [[ -d "$USER_HOME/Library/Caches/Adobe" ]]; then
        local adobe_cache
        for adobe_cache in "$USER_HOME/Library/Caches/Adobe"/*/Cache; do
            [[ -d "$adobe_cache" ]] && rm_children "$adobe_cache" "Adobe cache"
        done
    fi

    # Slack
    rm_children "$USER_HOME/Library/Application Support/Slack/Cache" "Slack Cache"
    rm_children "$USER_HOME/Library/Application Support/Slack/Code Cache" "Slack Code Cache"
    rm_children "$USER_HOME/Library/Application Support/Slack/GPUCache" "Slack GPUCache"
    rm_children "$USER_HOME/Library/Caches/com.tinyspeck.slackmacgap" "Slack macgap"

    # Discord
    rm_children "$USER_HOME/Library/Application Support/discord/Cache" "Discord Cache"
    rm_children "$USER_HOME/Library/Application Support/discord/Code Cache" "Discord Code Cache"
    rm_children "$USER_HOME/Library/Application Support/discord/GPUCache" "Discord GPUCache"

    # Microsoft Teams
    rm_children "$USER_HOME/Library/Application Support/Microsoft/Teams/Cache" "Teams Cache"
    rm_children "$USER_HOME/Library/Application Support/Microsoft/Teams/Code Cache" "Teams Code Cache"
    rm_children "$USER_HOME/Library/Application Support/Microsoft/Teams/GPUCache" "Teams GPUCache"

    # Zoom
    rm_children "$USER_HOME/Library/Application Support/zoom.us/AutoDownload" "Zoom AutoDownload"
    rm_children "$USER_HOME/Library/Caches/us.zoom.xos" "Zoom cache"

    # VS Code
    rm_children "$USER_HOME/Library/Application Support/Code/Cache" "VS Code Cache"
    rm_children "$USER_HOME/Library/Application Support/Code/CachedData" "VS Code CachedData"
    rm_children "$USER_HOME/Library/Application Support/Code/CachedExtensions" "VS Code CachedExtensions"
    rm_children "$USER_HOME/Library/Application Support/Code/CachedExtensionVSIXs" "VS Code CachedExtensionVSIXs"
    rm_children "$USER_HOME/Library/Application Support/Code/Code Cache" "VS Code Code Cache"
    rm_children "$USER_HOME/Library/Application Support/Code/GPUCache" "VS Code GPUCache"

    # JetBrains IDEs
    if [[ -d "$USER_HOME/Library/Caches/JetBrains" ]]; then
        local ide_cache
        for ide_cache in "$USER_HOME/Library/Caches/JetBrains"/*/caches; do
            [[ -d "$ide_cache" ]] && rm_children "$ide_cache" "JetBrains caches"
        done
    fi

    # Spotify
    rm_children "$USER_HOME/Library/Caches/com.spotify.client/Data" "Spotify cache"
    rm_children "$USER_HOME/Library/Application Support/Spotify/PersistentCache" "Spotify PersistentCache"

    # Steam
    rm_children "$USER_HOME/Library/Application Support/Steam/steamapps/downloading" "Steam downloads"
    rm_children "$USER_HOME/Library/Caches/com.valvesoftware.steam" "Steam cache"

    log "Application caches cleaned"
}

cleanup_ios_data() {
    section "iOS Device Data"

    local backup_dir="$USER_HOME/Library/Application Support/MobileSync/Backup"

    if [[ -d "$backup_dir" ]]; then
        local backup_count
        backup_count="$(ls "$backup_dir" 2>/dev/null | wc -l | tr -d ' ')"

        if [[ "${backup_count:-0}" =~ ^[0-9]+$ ]] && (( backup_count > 0 && backup_count < 20 )); then
            log "Found $backup_count iOS backup(s)"
            if (( DRY_RUN )); then
                echo -e "${YELLOW}[DRY]${NC} Would remove backups older than ${OLD_BACKUP_DAYS} days"
            else
                find "$backup_dir" -maxdepth 1 -type d -mtime +"$OLD_BACKUP_DAYS" -exec rm -rf {} + 2>/dev/null || true
            fi
            log "Removed iOS backups older than $OLD_BACKUP_DAYS days"
        elif (( backup_count >= 20 )); then
            warn "Many iOS backups found ($backup_count) - manual review recommended"
            warn "Run: ls -la '$backup_dir' | sort -k6,7"
        else
            log "No iOS backups found"
        fi
    fi

    # IPSW files (aggressive)
    if (( AGGRESSIVE )); then
        local itunes_dir="$USER_HOME/Library/iTunes"
        if [[ -d "$itunes_dir" ]]; then
            warn "AGGRESSIVE: Searching for old IPSW firmware files"
            if confirm "Remove *.ipsw older than 60 days?" "N"; then
                prune_old_files "$itunes_dir" 60 -type f -name "*.ipsw"
                log "Pruned old IPSW files"
            fi
        fi
    fi

    log "iOS data cleanup completed"
}

cleanup_mail() {
    section "Mail (Preserving Emails)"

    rm_children "$USER_HOME/Library/Containers/com.apple.mail/Data/Library/Mail Downloads" "Mail Downloads"
    rm_children "$USER_HOME/Library/Caches/com.apple.mail" "Mail cache"

    # Optimize Mail database
    if command -v sqlite3 >/dev/null 2>&1; then
        local mail_db
        for mail_db in "$USER_HOME/Library/Mail/V"*/MailData/Envelope\ Index; do
            if [[ -f "$mail_db" ]]; then
                log "Optimizing Mail database"
                if (( DRY_RUN )); then
                    echo -e "${YELLOW}[DRY]${NC} Would VACUUM: $mail_db"
                else
                    sqlite3 "$mail_db" vacuum 2>/dev/null || true
                fi
                break
            fi
        done
    fi

    log "Mail cleanup completed"
}

cleanup_system_maintenance() {
    section "System Maintenance"

    # QuickLook
    if command -v qlmanage >/dev/null 2>&1; then
        run_as_user "qlmanage -r cache" "Reset QuickLook cache"
    fi

    # QuickLook thumbnails
    if command -v getconf >/dev/null 2>&1; then
        local ql_cache_dir
        ql_cache_dir="$(sudo -u "$SUDO_USER_RESOLVED" getconf DARWIN_USER_CACHE_DIR 2>/dev/null || true)"
        if [[ -n "$ql_cache_dir" ]]; then
            rm_rf "$ql_cache_dir/com.apple.QuickLook.thumbnailcache" "QuickLook thumbnails"
        fi
    fi
    rm_children "$USER_HOME/Library/Thumbnails" "User thumbnails"

    # Font cache
    if command -v atsutil >/dev/null 2>&1; then
        run_as_user "atsutil databases -removeUser" "Clear user font cache"
    fi

    # DNS cache
    safe_command "dscacheutil -flushcache" "Flush DNS cache"
    safe_command "killall -HUP mDNSResponder" "Restart mDNSResponder"

    # CUPS printing
    rm_children "/var/spool/cups/cache" "CUPS cache"

    # Kernel extension cache
    if command -v kextcache >/dev/null 2>&1; then
        safe_command "kextcache --clear-staging" "Clear kext staging"
    fi

    # Software update catalog (aggressive)
    if (( AGGRESSIVE )) && command -v softwareupdate >/dev/null 2>&1; then
        warn "AGGRESSIVE: Clearing software update catalog"
        if confirm "Clear software update catalog?" "N"; then
            safe_command "softwareupdate --clear-catalog" "Clear software update catalog"
        fi
    fi

    log "System maintenance completed"
}

cleanup_docker() {
    section "Docker"

    command -v docker >/dev/null 2>&1 || { log "Docker not installed - skipping"; return 0; }

    # Check if Docker is running
    if ! docker info &>/dev/null; then
        log "Docker daemon not running - skipping"
        return 0
    fi

    warn "Docker cleanup removes stopped containers and dangling images"
    if confirm "Run safe Docker cleanup?" "N"; then
        run_as_user "docker system prune -f" "Docker system prune"
    fi

    if (( AGGRESSIVE )); then
        warn "AGGRESSIVE: Full Docker cleanup (including unused images/volumes)"
        if confirm "Run aggressive Docker cleanup?" "N"; then
            run_as_user "docker container prune -f" "Docker container prune"
            run_as_user "docker image prune -af" "Docker image prune"
            run_as_user "docker volume prune -f" "Docker volume prune"
            run_as_user "docker system prune -af" "Docker full prune"
        fi
    fi

    log "Docker cleanup completed"
}

cleanup_old_downloads() {
    section "Old Downloads"

    [[ -d "$USER_HOME/Downloads" ]] || return 0

    local download_count
    download_count="$(ls "$USER_HOME/Downloads" 2>/dev/null | wc -l | tr -d ' ')"

    if [[ ! "${download_count:-0}" =~ ^[0-9]+$ ]] || (( download_count >= 1000 )); then
        warn "Downloads folder too large ($download_count items) - skipping automatic scan"
        return 0
    fi

    local old_count
    old_count="$(find "$USER_HOME/Downloads" -maxdepth 2 -type f -mtime +"$OLD_FILE_DAYS" 2>/dev/null | wc -l | tr -d ' ')"

    if [[ "${old_count:-0}" =~ ^[0-9]+$ ]] && (( old_count > 0 )); then
        warn "Found $old_count files older than $OLD_FILE_DAYS days in Downloads"
        if confirm "Remove old downloads?" "N"; then
            if (( DRY_RUN )); then
                echo -e "${YELLOW}[DRY]${NC} Would delete $old_count old files from Downloads"
            else
                find "$USER_HOME/Downloads" -maxdepth 2 -type f -mtime +"$OLD_FILE_DAYS" -delete 2>/dev/null || true
            fi
            log "Removed old downloads"
        fi
    else
        log "No old downloads to clean"
    fi
}

# -----------------------------
# Final operations
# -----------------------------
final_maintenance() {
    section "Final Maintenance"

    # Memory purge
    if command -v purge >/dev/null 2>&1; then
        if confirm "Purge inactive memory?" "N"; then
            safe_command "purge" "Memory purge"
        fi
    fi

    # Spotlight reindex (aggressive or on request)
    if (( AGGRESSIVE )) && command -v mdutil >/dev/null 2>&1; then
        warn "Spotlight reindex will increase disk activity temporarily"
        if confirm "Rebuild Spotlight index?" "N"; then
            safe_command "mdutil -E /" "Spotlight reindex"
        fi
    fi

    log "Final maintenance completed"
}

report_space_usage() {
    section "Space Usage Report (Manual Review)"
    report_top_dirs "$USER_HOME/Library/Application Support" 15
    report_top_dirs "$USER_HOME/Library/Caches" 15
    report_top_dirs "$USER_HOME/Library/Containers" 15
    report_top_dirs "$USER_HOME/Library/Developer" 15
}

# -----------------------------
# Main execution
# -----------------------------
main() {
    parse_args "$@"

    echo -e "\n${BOLD}${GREEN}$SCRIPT_NAME${NC}"
    echo -e "${CYAN}Comprehensive macOS system cleanup${NC}\n"

    warn "Always ensure you have a backup before running cleanup operations!"

    if (( DRY_RUN )); then
        echo -e "${YELLOW}DRY-RUN MODE: No files will be deleted${NC}"
    fi
    if (( AGGRESSIVE )); then
        echo -e "${YELLOW}AGGRESSIVE MODE: Additional cleanup options enabled${NC}"
    fi
    if (( ASSUME_YES )); then
        echo -e "${YELLOW}AUTO-YES MODE: All prompts will be auto-accepted${NC}"
    fi
    echo

    check_root
    resolve_user
    check_time_machine

    log "Target user: $SUDO_USER_RESOLVED"
    log "Home directory: $USER_HOME"

    # Record initial disk space
    local old_available
    old_available="$(df / | tail -1 | awk '{print $4}')"
    section "Initial Disk Space: $(df -h / | tail -1 | awk '{print $4}') available"

    # Execute cleanup operations
    cleanup_time_machine_snapshots
    cleanup_macos_installers
    cleanup_trash_and_temp
    cleanup_system_caches
    cleanup_user_caches
    cleanup_container_caches
    cleanup_logs
    cleanup_browser_caches
    cleanup_development_tools
    cleanup_applications
    cleanup_ios_data
    cleanup_mail
    cleanup_system_maintenance
    cleanup_docker
    cleanup_old_downloads
    final_maintenance
    report_space_usage

    # Calculate space recovered
    local new_available
    new_available="$(df / | tail -1 | awk '{print $4}')"
    local recovered=$(( (new_available - old_available) * 512 ))

    section "Cleanup Complete!"
    log "Final disk space: $(df -h / | tail -1 | awk '{print $4}') available"

    if ((recovered > 0)); then
        echo -e "${GREEN}Space recovered: $(bytes_to_human "$recovered")${NC}"
    else
        log "No immediate disk space change (some space may be 'purgeable')"
    fi

    echo
    warn "Consider rebooting to complete cleanup and release all cached memory"
    warn "Manual review recommended for:"
    warn "  - ~/Library/Application Support/* (large app data)"
    warn "  - ~/Library/Containers/* (orphaned app containers)"
    warn "  - iOS device backups"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
