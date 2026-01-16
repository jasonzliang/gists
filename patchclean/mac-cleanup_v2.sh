#!/usr/bin/env bash
#
# macOS Unified Cleanup Script (Tahoe-friendly)
# - Safer by default (prunes by age; prompts before destructive actions)
# - Supports --dry-run, --aggressive, and -y/--yes (noninteractive)
#
# Usage:
#   chmod +x macos_cleanup.sh
#   sudo ./macos_cleanup.sh --dry-run
#   sudo ./macos_cleanup.sh
#   sudo ./macos_cleanup.sh --aggressive
#   sudo ./macos_cleanup.sh -y
#   sudo ./macos_cleanup.sh --aggressive -y
#

set -euo pipefail

# -----------------------------
# Configuration
# -----------------------------
readonly SCRIPT_NAME="macOS Unified Cleanup"
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
readonly NC='\033[0m' # No Color
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
section() { echo -e "\n${BOLD}${BLUE}$1${NC}"; }

bytes_to_human() {
  local b=${1:-0}
  local d=''
  local s=0
  local S=(Bytes KiB MiB GiB TiB PiB EiB ZiB YiB)

  while ((b >= 1024)); do
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
  # Usage: confirm "Question?" [default=N|Y]
  # - If ASSUME_YES=1: always return yes (0), no prompt.
  # - If non-interactive and not ASSUME_YES: return based on default (safe default is N).
  local question="$1"
  local default="${2:-N}"

  if (( ASSUME_YES )); then
    log "Auto-yes (-y): $question"
    return 0
  fi

  if ! is_tty; then
    if [[ "$default" =~ ^[Yy]$ ]]; then
      warn "Non-interactive shell; defaulting YES for: $question (use -y to force YES explicitly)"
      return 0
    fi
    warn "Non-interactive shell; defaulting NO for: $question (use -y to auto-accept)"
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
    # EOF or read error -> default
    reply="$default"
  fi
  reply="${reply:-$default}"
  [[ "$reply" =~ ^[Yy]$ ]]
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY_RUN=1 ;;
      --aggressive) AGGRESSIVE=1 ;;
      -y|--yes) ASSUME_YES=1 ;;
      -h|--help)
        cat <<'EOF'
Usage:
  sudo ./macos_cleanup.sh [--dry-run] [--aggressive] [-y|--yes]

Options:
  --dry-run     Print what would be removed, without deleting.
  --aggressive  Enable more aggressive cleanups (still guarded; with -y will auto-accept).
  -y, --yes     Noninteractive: auto-accept ALL prompts (assume "yes").
EOF
        exit 0
        ;;
      *) ;;
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
  # Prefer sudo invoker. If script run as root directly, fall back to console user.
  local su="${SUDO_USER:-}"
  if [[ -z "$su" || "$su" == "root" ]]; then
    su="$(stat -f%Su /dev/console 2>/dev/null || true)"
  fi
  if [[ -z "$su" || "$su" == "root" ]]; then
    error "Could not determine target user. Please run via: sudo ./macos_cleanup.sh"
    exit 1
  fi

  SUDO_USER_RESOLVED="$su"
  USER_HOME="$(eval echo "~$SUDO_USER_RESOLVED")"
}

check_time_machine() {
  if command -v tmutil >/dev/null 2>&1; then
    if tmutil status 2>/dev/null | grep -q "Running = 1"; then
      error "Time Machine is currently running. Let it finish first!"
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
        if ! confirm "Continue anyway?" "N"; then
          exit 1
        fi
      else
        log "Recent Time Machine backup found."
      fi
    else
      warn "No Time Machine backup found (or tmutil can't access it)."
    fi
  else
    warn "tmutil not found; skipping Time Machine checks."
  fi
}

# DRY-RUN aware removal helpers
rm_rf() {
  local path="$1"
  local desc="${2:-$path}"

  [[ -n "$path" ]] || return 0

  if [[ -e "$path" || -d "$path" ]]; then
    if (( DRY_RUN )); then
      echo -e "${YELLOW}[DRY]${NC} Would remove: $desc -> $path"
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
    echo -e "${YELLOW}[DRY]${NC} Would remove contents: $desc -> $dir/*"
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
  eval "$cmd" &>/dev/null || warn "Command failed: $cmd"
}

prune_old_files() {
  # Usage: prune_old_files /dir days find_predicates...
  local dir="$1"
  local days="$2"
  shift 2

  [[ -d "$dir" ]] || return 0

  local one
  one="$(find "$dir" -mindepth 1 "$@" -mtime +"$days" -print -quit 2>/dev/null || true)"
  [[ -z "$one" ]] && return 0

  if (( DRY_RUN )); then
    echo -e "${YELLOW}[DRY]${NC} Would prune in: $dir (older than ${days}d) predicates: $*"
    return 0
  fi

  find "$dir" -mindepth 1 "$@" -mtime +"$days" -print0 2>/dev/null | xargs -0 rm -rf 2>/dev/null || true
}

report_top_dirs() {
  local dir="$1"
  local n="${2:-25}"
  [[ -d "$dir" ]] || return 0
  section "📌 Largest items in: $dir"
  du -xhd 1 "$dir" 2>/dev/null | sort -hr | head -n "$n" || true
}

# -----------------------------
# High-impact cleanups
# -----------------------------
cleanup_time_machine_local_snapshots() {
  section "🕒 Time Machine Local Snapshots (optional)"
  command -v tmutil >/dev/null 2>&1 || { log "tmutil not found - skipping"; return 0; }

  local snaps
  snaps="$(tmutil listlocalsnapshots / 2>/dev/null || true)"
  if [[ -z "$snaps" ]]; then
    log "No local snapshots detected (or insufficient permissions)."
    return 0
  fi

  warn "Local snapshots detected. These can consume large 'purgeable' space."
  if confirm "Attempt to reclaim ~10GB by thinning snapshots?" "N"; then
    safe_command "tmutil thinlocalsnapshots / 10000000000 4" "Thinning local snapshots (~10GB target)"
  else
    log "Skipped snapshot thinning"
  fi
}

cleanup_macos_installers() {
  section "📦 Old macOS Installers (optional)"
  local installers=()
  local old_nullglob
  old_nullglob="$(shopt -p nullglob || true)"
  shopt -s nullglob
  installers=( /Applications/Install\ macOS*.app )
  eval "$old_nullglob" 2>/dev/null || true

  if (( ${#installers[@]} == 0 )); then
    log "No 'Install macOS …' apps found in /Applications"
    return 0
  fi

  warn "Found installer app(s):"
  for app in "${installers[@]}"; do
    echo "  - $app"
  done

  if confirm "Remove these installer app(s)?" "N"; then
    for app in "${installers[@]}"; do
      rm_rf "$app" "Remove installer: $app"
    done
    log "Removed selected installers"
  else
    log "Skipped installer removal"
  fi
}

# -----------------------------
# Cleanup functions (safer defaults)
# -----------------------------
cleanup_trash_and_tmp_safer() {
  section "🗑️  Trash + Temp (Safer)"

  if [[ -d "$USER_HOME/.Trash" ]]; then
    rm_children "$USER_HOME/.Trash" "User Trash"
  fi

  if [[ -d /Volumes ]]; then
    local vol
    for vol in /Volumes/*; do
      [[ -d "$vol/.Trashes" ]] && rm_children "$vol/.Trashes" "Volume Trash: $vol/.Trashes" || true
    done
  fi

  prune_old_files "/tmp" "$KEEP_TMP_DAYS" -type f
  prune_old_files "/private/var/tmp" "$KEEP_TMP_DAYS" -type f
  prune_old_files "/tmp" "$KEEP_TMP_DAYS" -type d -empty
  prune_old_files "/private/var/tmp" "$KEEP_TMP_DAYS" -type d -empty

  log "Trash cleaned; temp pruned (>${KEEP_TMP_DAYS} days)."
}

cleanup_basic_caches() {
  section "🗑️  Basic Cache Cleanup (Preserving User Settings)"

  rm_rf "/Library/Caches/com.apple.iconservices.store" "IconServices cache"
  rm_rf "/Library/Caches/com.apple.preferencepanes.prefpanekit" "PreferencePanes cache"
  if [[ -d /Library/Caches ]]; then
    local lsitem
    for lsitem in /Library/Caches/com.apple.LaunchServices*; do
      [[ -e "$lsitem" ]] && rm_rf "$lsitem" "LaunchServices cache: $lsitem"
    done
  fi
  log "Cleaned select system caches"

  if [[ -d "$USER_HOME/Library/Caches" ]]; then
    local c
    for c in \
      "$USER_HOME/Library/Caches/com.apple.WebKit"* \
      "$USER_HOME/Library/Caches/com.apple.Safari"* \
      "$USER_HOME/Library/Caches/com.google.Chrome"*; do
      [[ -e "$c" ]] && rm_rf "$c" "User cache: $c"
    done

    if (( AGGRESSIVE )); then
      warn "AGGRESSIVE: Removing many cache/temp-like subfolders under ~/Library/Caches."
      local cache_dir
      for cache_dir in "$USER_HOME/Library/Caches"/*; do
        [[ -d "$cache_dir" ]] || continue
        local base
        base="$(basename "$cache_dir")"
        if [[ "$base" == *"Cache"* || "$base" == *"cache"* || "$base" == *"Temp"* || "$base" == *"temp"* ]]; then
          rm_rf "$cache_dir" "User cache folder: $cache_dir"
        fi
      done
    fi

    log "Cleaned user caches"
  fi

  if [[ -d "$USER_HOME/Library/Containers" ]]; then
    local container_cache
    for container_cache in "$USER_HOME/Library/Containers"/*/Data/Library/Caches/*; do
      [[ -d "$container_cache" ]] && rm_rf "$container_cache" "Container cache: $container_cache"
    done
    log "Cleaned container caches"
  fi

  log "✅ Basic caches cleaned"
}

cleanup_logs_safer() {
  section "📋 Log Cleanup (Prune by age; keep recent)"

  prune_old_files "/private/var/log" "$KEEP_LOG_DAYS" -type f \( -name "*.gz" -o -name "*.bz2" -o -name "*.old" \)
  prune_old_files "/Library/Logs/DiagnosticReports" "$KEEP_LOG_DAYS" -type f
  prune_old_files "/Library/Logs" "$KEEP_LOG_DAYS" -type f -name "*.log"

  prune_old_files "$USER_HOME/Library/Logs" "$KEEP_LOG_DAYS" -type f
  prune_old_files "$USER_HOME/Library/Logs/DiagnosticReports" "$KEEP_LOG_DAYS" -type f
  prune_old_files "$USER_HOME/Library/Logs/CrashReporter" "$KEEP_LOG_DAYS" -type f
  prune_old_files "$USER_HOME/Library/Containers/com.apple.mail/Data/Library/Logs/Mail" "$KEEP_LOG_DAYS" -type f

  log "✅ Pruned logs older than ${KEEP_LOG_DAYS} days"
}

cleanup_browser_caches() {
  section "🌐 Browser Cache Cleanup (Preserving History/Cookies)"

  rm_children "$USER_HOME/Library/Caches/com.apple.Safari/Webpage Previews" "Safari Webpage Previews"
  rm_children "$USER_HOME/Library/Caches/com.apple.Safari/fsCachedData" "Safari fsCachedData"

  rm_children "$USER_HOME/Library/Caches/com.google.Chrome/Default/Cache" "Chrome Cache"
  rm_children "$USER_HOME/Library/Caches/com.google.Chrome/Default/Code Cache" "Chrome Code Cache"
  rm_children "$USER_HOME/Library/Caches/com.google.Chrome/Default/GPUCache" "Chrome GPUCache"
  rm_children "$USER_HOME/Library/Caches/com.google.Chrome/ShaderCache" "Chrome ShaderCache"

  if [[ -d "$USER_HOME/Library/Caches/Firefox/Profiles" ]]; then
    local profile
    for profile in "$USER_HOME/Library/Caches/Firefox/Profiles"/*; do
      [[ -d "$profile" ]] || continue
      rm_children "$profile/cache2" "Firefox cache2: $(basename "$profile")"
      rm_children "$profile/startupCache" "Firefox startupCache: $(basename "$profile")"
      rm_children "$profile/thumbnails" "Firefox thumbnails: $(basename "$profile")"
    done
  fi

  rm_children "$USER_HOME/Library/Caches/com.apple.WebKit.Networking" "WebKit Networking Cache"
  if [[ -d "$USER_HOME/Library/WebKit" ]]; then
    local webkit_dir
    for webkit_dir in "$USER_HOME/Library/WebKit"/*; do
      rm_children "$webkit_dir/WebKitCache" "WebKitCache: $(basename "$webkit_dir")"
    done
  fi

  log "✅ Browser caches cleaned"
}

cleanup_development_tools() {
  section "⚒️  Development Tools Cleanup"

  rm_children "$USER_HOME/Library/Developer/Xcode/DerivedData" "Xcode DerivedData"
  rm_children "$USER_HOME/Library/Developer/Xcode/Archives" "Xcode Archives"
  rm_children "$USER_HOME/Library/Developer/Xcode/iOS Device Logs" "Xcode iOS Device Logs"
  rm_children "$USER_HOME/Library/Developer/CoreSimulator/Caches" "CoreSimulator Caches"
  log "Cleaned Xcode caches"

  if command -v xcrun >/dev/null 2>&1; then
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" xcrun simctl delete unavailable" "Purging unavailable simulators"
    log "Purged unavailable simulators"
  fi

  if (( AGGRESSIVE )); then
    local ds_dir="$USER_HOME/Library/Developer/Xcode/iOS DeviceSupport"
    if [[ -d "$ds_dir" ]]; then
      warn "AGGRESSIVE: iOS DeviceSupport can be large."
      if confirm "Prune iOS DeviceSupport folders older than 180 days?" "N"; then
        prune_old_files "$ds_dir" 180 -type d
        log "Pruned old iOS DeviceSupport folders"
      else
        log "Skipped DeviceSupport pruning"
      fi
    fi
  fi

  if command -v brew >/dev/null 2>&1; then
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" brew cleanup --prune=all" "Cleaning Homebrew caches"
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" brew autoremove" "Removing unused Homebrew dependencies"
    rm_children "/Library/Caches/Homebrew" "Homebrew system cache"
    log "Cleaned Homebrew"
  fi

  if command -v npm >/dev/null 2>&1; then
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" npm cache clean --force" "Cleaning NPM cache"
    rm_children "$USER_HOME/.npm/_cacache" "NPM cacache"
    log "Cleaned NPM cache"
  fi

  if command -v yarn >/dev/null 2>&1; then
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" yarn cache clean" "Cleaning Yarn cache"
    log "Cleaned Yarn cache"
  fi

  if command -v gem >/dev/null 2>&1; then
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" gem cleanup" "Cleaning Ruby gems"
    log "Cleaned Ruby gems"
  fi

  if command -v pod >/dev/null 2>&1; then
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" pod cache clean --all" "Cleaning CocoaPods cache"
    log "Cleaned CocoaPods cache"
  fi

  if command -v conda >/dev/null 2>&1; then
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" conda clean --all --yes" "Cleaning Conda cache"
    log "Cleaned Conda cache"
  fi

  if command -v go >/dev/null 2>&1; then
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" go clean -cache" "Cleaning Go build cache"
    log "Cleaned Go cache"
  fi

  if (( AGGRESSIVE )); then
    rm_children "$USER_HOME/.bundle/cache" "Bundler cache"
    rm_children "$USER_HOME/.gradle/caches" "Gradle caches"
    rm_children "$USER_HOME/.cache/pip" "pip cache"
    warn "AGGRESSIVE: Clearing ~/.m2/repository will force Maven to redownload dependencies."
    if confirm "Clear Maven ~/.m2/repository?" "N"; then
      rm_children "$USER_HOME/.m2/repository" "Maven repository"
    fi
    log "Aggressive package caches cleaned"
  fi

  local dev_dirs=("$USER_HOME/Documents" "$USER_HOME/Desktop" "$USER_HOME/Developer" "$USER_HOME/Projects" "$USER_HOME/Code")
  local dev_dir
  for dev_dir in "${dev_dirs[@]}"; do
    [[ -d "$dev_dir" ]] || continue
    log "Cleaning Python bytecode in $dev_dir"
    if (( DRY_RUN )); then
      echo -e "${YELLOW}[DRY]${NC} Would remove __pycache__, *.pyc, *.pyo, .ipynb_checkpoints (depth<=3) under: $dev_dir"
      continue
    fi
    find "$dev_dir" -maxdepth 3 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$dev_dir" -maxdepth 3 -name "*.pyc" -delete 2>/dev/null || true
    find "$dev_dir" -maxdepth 3 -name "*.pyo" -delete 2>/dev/null || true
    find "$dev_dir" -maxdepth 3 -type d -name ".ipynb_checkpoints" -exec rm -rf {} + 2>/dev/null || true
  done
}

cleanup_applications() {
  section "📱 Application Cache Cleanup (Preserving Settings)"

  rm_children "$USER_HOME/Library/Application Support/Adobe/Common/Media Cache Files" "Adobe Media Cache Files"
  if [[ -d "$USER_HOME/Library/Caches/Adobe" ]]; then
    local adobe_cache
    for adobe_cache in "$USER_HOME/Library/Caches/Adobe"/*/Cache; do
      [[ -d "$adobe_cache" ]] && rm_children "$adobe_cache" "Adobe cache: $adobe_cache"
    done
  fi
  log "Cleaned Adobe caches"

  rm_children "$USER_HOME/Library/Application Support/Slack/Cache" "Slack Cache"
  rm_children "$USER_HOME/Library/Application Support/Slack/Code Cache" "Slack Code Cache"
  rm_children "$USER_HOME/Library/Application Support/Slack/GPUCache" "Slack GPUCache"
  rm_children "$USER_HOME/Library/Caches/com.tinyspeck.slackmacgap" "Slack macgap cache"

  rm_children "$USER_HOME/Library/Application Support/discord/Cache" "Discord Cache"
  rm_children "$USER_HOME/Library/Application Support/discord/Code Cache" "Discord Code Cache"
  rm_children "$USER_HOME/Library/Application Support/discord/GPUCache" "Discord GPUCache"

  rm_children "$USER_HOME/Library/Application Support/Microsoft/Teams/Cache" "Teams Cache"
  rm_children "$USER_HOME/Library/Application Support/Microsoft/Teams/Code Cache" "Teams Code Cache"
  rm_children "$USER_HOME/Library/Application Support/Microsoft/Teams/GPUCache" "Teams GPUCache"

  rm_children "$USER_HOME/Library/Application Support/zoom.us/AutoDownload" "Zoom AutoDownload"
  rm_children "$USER_HOME/Library/Caches/us.zoom.xos" "Zoom cache"
  log "Cleaned communication app caches"

  rm_children "$USER_HOME/Library/Application Support/Code/Cache" "VS Code Cache"
  rm_children "$USER_HOME/Library/Application Support/Code/CachedData" "VS Code CachedData"
  rm_children "$USER_HOME/Library/Application Support/Code/CachedExtensions" "VS Code CachedExtensions"
  rm_children "$USER_HOME/Library/Application Support/Code/CachedExtensionVSIXs" "VS Code CachedExtensionVSIXs"
  rm_children "$USER_HOME/Library/Application Support/Code/Code Cache" "VS Code Code Cache"
  rm_children "$USER_HOME/Library/Application Support/Code/GPUCache" "VS Code GPUCache"

  if [[ -d "$USER_HOME/Library/Caches/JetBrains" ]]; then
    local ide_cache
    for ide_cache in "$USER_HOME/Library/Caches/JetBrains"/*/caches; do
      [[ -d "$ide_cache" ]] && rm_children "$ide_cache" "JetBrains caches: $ide_cache"
    done
  fi
  log "Cleaned development IDE caches"

  rm_children "$USER_HOME/Library/Caches/com.spotify.client/Data" "Spotify cache data"
  rm_children "$USER_HOME/Library/Application Support/Spotify/PersistentCache" "Spotify PersistentCache"

  rm_children "$USER_HOME/Library/Application Support/Steam/steamapps/downloading" "Steam downloading"
  rm_children "$USER_HOME/Library/Caches/com.valvesoftware.steam" "Steam cache"

  rm_children "$USER_HOME/Library/Caches/Java/tmp" "Java tmp cache"
  rm_children "$USER_HOME/Library/Caches/com.apple.SpeechRecognitionCore" "SpeechRecognitionCore cache"

  log "✅ Application caches cleaned"
}

cleanup_ios_data() {
  section "📱 iOS Device Data Cleanup"

  local backup_dir="$USER_HOME/Library/Application Support/MobileSync/Backup"

  if [[ -d "$backup_dir" ]]; then
    log "iOS backup directory found"
    local backup_count
    backup_count="$(ls "$backup_dir" 2>/dev/null | wc -l | tr -d ' ')"

    if [[ "${backup_count:-0}" =~ ^[0-9]+$ ]] && (( backup_count < 20 )); then
      log "Scanning for old iOS backups (found $backup_count)"
      if (( DRY_RUN )); then
        echo -e "${YELLOW}[DRY]${NC} Would remove backups older than ${OLD_BACKUP_DAYS} days under: $backup_dir"
      else
        find "$backup_dir" -maxdepth 1 -type d -mtime +"$OLD_BACKUP_DAYS" -exec rm -rf {} + 2>/dev/null || true
      fi
      log "Removed iOS backups older than $OLD_BACKUP_DAYS days (if any matched)"
    else
      warn "Too many iOS backups ($backup_count) - manual cleanup recommended"
      warn "Run: ls -la '$backup_dir' | sort -k6,7"
    fi
  else
    log "No iOS backups directory found"
  fi

  if (( AGGRESSIVE )); then
    local itunes_updates="$USER_HOME/Library/iTunes"
    if [[ -d "$itunes_updates" ]]; then
      warn "AGGRESSIVE: Searching for large *.ipsw files in $itunes_updates"
      if confirm "Remove *.ipsw older than 60 days?" "N"; then
        prune_old_files "$itunes_updates" 60 -type f -name "*.ipsw"
        log "Pruned old IPSW files"
      fi
    fi
  fi

  log "✅ iOS data cleanup completed"
}

cleanup_mail_data() {
  section "📧 Mail Cache Cleanup (Preserving Emails)"

  rm_children "$USER_HOME/Library/Containers/com.apple.mail/Data/Library/Mail Downloads" "Mail Downloads"
  rm_children "$USER_HOME/Library/Caches/com.apple.mail" "Mail caches"

  if command -v sqlite3 >/dev/null 2>&1; then
    local mail_db_found=false
    local mail_db
    for mail_db in "$USER_HOME/Library/Mail/V"*/MailData/Envelope\ Index; do
      if [[ -f "$mail_db" ]]; then
        log "Optimizing Mail database (VACUUM)"
        if (( DRY_RUN )); then
          echo -e "${YELLOW}[DRY]${NC} Would run: sqlite3 \"$mail_db\" vacuum"
        else
          sqlite3 "$mail_db" vacuum 2>/dev/null || true
        fi
        mail_db_found=true
        break
      fi
    done
    [[ "$mail_db_found" == false ]] && log "No Mail database found to optimize"
  else
    warn "sqlite3 not found; skipping Mail database VACUUM"
  fi

  log "✅ Mail caches cleaned"
}

cleanup_system_caches() {
  section "🖥️  System Cache Cleanup"

  if command -v qlmanage >/dev/null 2>&1; then
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" qlmanage -r cache" "Resetting QuickLook cache"
  fi

  if command -v getconf >/dev/null 2>&1; then
    local ql_cache_dir
    ql_cache_dir="$(sudo -u "$SUDO_USER_RESOLVED" getconf DARWIN_USER_CACHE_DIR 2>/dev/null || true)"
    if [[ -n "$ql_cache_dir" ]]; then
      rm_rf "$ql_cache_dir/com.apple.QuickLook.thumbnailcache" "QuickLook thumbnail cache"
    fi
  fi
  rm_children "$USER_HOME/Library/Thumbnails" "User thumbnails"

  if command -v atsutil >/dev/null 2>&1; then
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" atsutil databases -removeUser" "Clearing user font cache"
  fi

  safe_command "dscacheutil -flushcache" "Flushing DNS cache (dscacheutil)"
  safe_command "killall -HUP mDNSResponder" "Flushing DNS cache (mDNSResponder)"

  rm_children "/var/spool/cups/cache" "CUPS cache"

  if command -v kextcache >/dev/null 2>&1; then
    safe_command "kextcache --clear-staging" "Clearing kext staging cache"
  fi

  if (( AGGRESSIVE )) && command -v softwareupdate >/dev/null 2>&1; then
    warn "AGGRESSIVE: Clearing the software update catalog may force update metadata/files to be redownloaded."
    if confirm "Run: softwareupdate --clear-catalog ?" "N"; then
      safe_command "softwareupdate --clear-catalog" "Clearing software update catalog"
    else
      log "Skipped softwareupdate --clear-catalog"
    fi
  fi

  log "✅ System caches cleaned"
}

cleanup_containers() {
  section "📦 Container Cleanup"

  if [[ -d "$USER_HOME/Library/Group Containers" ]]; then
    local group_container
    for group_container in "$USER_HOME/Library/Group Containers"/*; do
      rm_children "$group_container/Cache" "Group container Cache: $(basename "$group_container")"
      rm_children "$group_container/cache" "Group container cache: $(basename "$group_container")"
    done
    log "Cleaned group container caches"
  fi

  if [[ -d "$USER_HOME/Library/Containers" ]]; then
    local container_count
    container_count="$(ls "$USER_HOME/Library/Containers" 2>/dev/null | wc -l | tr -d ' ')"
    log "Found $container_count app containers"
    warn "For manual review of large containers, run:"
    warn "  du -hd 1 \"$USER_HOME/Library/Containers\" | sort -hr | head -25"
  fi

  if [[ -d "$USER_HOME/Library/Group Containers" ]]; then
    local group_count
    group_count="$(ls "$USER_HOME/Library/Group Containers" 2>/dev/null | wc -l | tr -d ' ')"
    log "Found $group_count group containers"
  fi

  log "✅ Container cleanup completed"
}

cleanup_docker() {
  section "🐳 Docker Cleanup"

  if ! command -v docker >/dev/null 2>&1; then
    log "Docker not found - skipping Docker cleanup"
    return 0
  fi

  warn "Docker cleanup can remove stopped containers + dangling images."
  if confirm "Run safe Docker cleanup (docker system prune -f)?" "N"; then
    safe_command "sudo -u \"$SUDO_USER_RESOLVED\" docker system prune -f" "Docker: system prune (safe)"
  else
    log "Skipped safe Docker prune"
  fi

  if (( AGGRESSIVE )); then
    warn "AGGRESSIVE: This can delete Docker images and volumes (data loss if volumes are used)."
    if confirm "Run aggressive Docker cleanup (images + volumes + everything unused)?" "N"; then
      safe_command "sudo -u \"$SUDO_USER_RESOLVED\" docker container prune -f" "Docker: container prune"
      safe_command "sudo -u \"$SUDO_USER_RESOLVED\" docker image prune -af" "Docker: image prune -a"
      safe_command "sudo -u \"$SUDO_USER_RESOLVED\" docker volume prune -f" "Docker: volume prune"
      safe_command "sudo -u \"$SUDO_USER_RESOLVED\" docker system prune -af" "Docker: system prune -a"
      log "Aggressive Docker cleanup completed"
    else
      log "Skipped aggressive Docker cleanup"
    fi
  fi

  log "✅ Docker cleanup completed"
}

cleanup_old_files() {
  section "🗂️  Old File Cleanup (Preserving Important Data)"

  if [[ -d "$USER_HOME/Downloads" ]]; then
    local download_count
    download_count="$(ls "$USER_HOME/Downloads" 2>/dev/null | wc -l | tr -d ' ')"
    if [[ "${download_count:-0}" =~ ^[0-9]+$ ]] && (( download_count < 1000 )); then
      local old_count
      old_count="$(find "$USER_HOME/Downloads" -maxdepth 2 -type f -mtime +"$OLD_FILE_DAYS" 2>/dev/null | wc -l | tr -d ' ')"
      if [[ "${old_count:-0}" =~ ^[0-9]+$ ]] && (( old_count > 0 )); then
        warn "Found $old_count downloads older than $OLD_FILE_DAYS days"
        if confirm "Remove old downloads?" "N"; then
          if (( DRY_RUN )); then
            echo -e "${YELLOW}[DRY]${NC} Would delete old files in Downloads (>${OLD_FILE_DAYS} days)"
          else
            find "$USER_HOME/Downloads" -maxdepth 2 -type f -mtime +"$OLD_FILE_DAYS" -delete 2>/dev/null || true
          fi
          log "Removed old downloads"
        else
          log "Skipped old downloads cleanup"
        fi
      else
        log "No old downloads found to clean"
      fi
    else
      warn "Downloads directory too large ($download_count items) - skipping automatic cleanup"
    fi
  fi
}

report_space_hogs() {
  section "🔎 Space hog report (manual review)"
  report_top_dirs "$USER_HOME/Library/Application Support" 25
  report_top_dirs "$USER_HOME/Library/Containers" 25
  report_top_dirs "$USER_HOME/Library/Group Containers" 25
  report_top_dirs "$USER_HOME/Library/Caches" 25
  report_top_dirs "$USER_HOME/Library/Developer" 25
}

final_maintenance() {
  section "🔧 Final System Maintenance (optional)"

  if command -v mdutil >/dev/null 2>&1; then
    warn "Spotlight reindex can increase disk activity temporarily."
    if confirm "Rebuild Spotlight index (mdutil -E /)?" "N"; then
      safe_command "mdutil -E /" "Initiating Spotlight reindex"
    else
      log "Skipped Spotlight reindex"
    fi
  fi

  log "✅ Final maintenance complete"
}

# -----------------------------
# Main execution
# -----------------------------
main() {
  parse_args "$@"

  section "🧹 $SCRIPT_NAME"
  warn "⚠️  Always backup your Mac before running cleanup operations!"
  if (( DRY_RUN )); then
    warn "DRY-RUN enabled: no files will be deleted."
  fi
  if (( AGGRESSIVE )); then
    warn "AGGRESSIVE enabled: more cleanups are enabled."
  fi
  if (( ASSUME_YES )); then
    warn "YES (-y) enabled: all prompts auto-accepted; running non-interactively."
  fi

  check_root
  resolve_user
  check_time_machine

  section "👤 Target user: $SUDO_USER_RESOLVED"
  section "🏠 User home: $USER_HOME"

  local old_available
  old_available="$(df / | tail -1 | awk '{print $4}')"
  section "📊 Initial disk space: $(df -h / | tail -1 | awk '{print $4}') available"

  cleanup_time_machine_local_snapshots
  cleanup_macos_installers

  cleanup_trash_and_tmp_safer
  cleanup_basic_caches
  cleanup_logs_safer
  cleanup_browser_caches
  cleanup_development_tools
  cleanup_applications
  cleanup_ios_data
  cleanup_mail_data
  cleanup_system_caches
  cleanup_containers
  cleanup_docker
  cleanup_old_files

  report_space_hogs
  final_maintenance

  local new_available
  new_available="$(df / | tail -1 | awk '{print $4}')"
  local recovered=$(( (new_available - old_available) * 512 ))

  section "✅ Cleanup Complete!"
  log "💾 Final disk space: $(df -h / | tail -1 | awk '{print $4}') available"
  if ((recovered > 0)); then
    log "🎉 Space recovered: $(bytes_to_human "$recovered")"
  else
    log "ℹ️  No measurable disk space change (some space may be 'purgeable' or reclaimed later)."
  fi

  echo
  warn "🔄 A reboot can help finalize some cache cleanup."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
