#!/bin/bash

# ==============================================================================
# AVIF MASTER WORKFLOW SCRIPT (V6 - Photos & Standalone Modes)
# ==============================================================================

# ------------------------------------------------------------------------------
# MODE DETECTION & PATH SETUP
# ------------------------------------------------------------------------------
if [ -n "$1" ]; then
    # Standalone Mode: A directory argument was provided
    if [ -d "$1" ]; then
        STANDALONE_MODE=true
        # Remove trailing slash if present to keep path names clean
        TARGET_DIR="${1%/}"
        ISSUES_DIR="${TARGET_DIR}_Issues"
    else
        echo "Error: Directory '$1' does not exist."
        exit 1
    fi
else
    # Default Mode: Apple Photos Workflow
    STANDALONE_MODE=false
    TARGET_DIR="$HOME/Desktop/Photos_AVIF_Workspace"
    ISSUES_DIR="$HOME/Desktop/Photos_AVIF_Issues"
fi

QUARANTINE_DIR="$ISSUES_DIR/Corrupt_Originals"
SALVAGE_DIR="$ISSUES_DIR/Salvaged_AVIFs"
ISSUES_LOG="$ISSUES_DIR/conversion_issues.log"

# Create necessary directories
mkdir -p "$TARGET_DIR"
mkdir -p "$QUARANTINE_DIR"
mkdir -p "$SALVAGE_DIR"

# Clear old log file if it exists
> "$ISSUES_LOG"

# ------------------------------------------------------------------------------
# PHASE 1: EXPORT (Apple Photos Mode Only)
# ------------------------------------------------------------------------------
if [ "$STANDALONE_MODE" = false ]; then
    echo "================================================="
    echo " PHASE 1: Exporting Albums from Apple Photos"
    echo "================================================="
    # Export originals, skipping edited versions, organizing by album
    osxphotos export "$TARGET_DIR" --skip-edited --directory "{album}"
else
    echo "================================================="
    echo " STANDALONE MODE ACTIVATED"
    echo " Target: $TARGET_DIR"
    echo "================================================="
fi

# ------------------------------------------------------------------------------
# PHASE 2: CONVERSION (Both Modes)
# ------------------------------------------------------------------------------
echo ""
echo "================================================="
echo " PHASE 2: Parallel AVIF Conversion with Quarantine"
echo "================================================="
CORES=$(sysctl -n hw.ncpu)

# 1. Count total files to process
TOTAL_FILES=$(find "$TARGET_DIR" -type f \( -iname \*.jpg -o -iname \*.jpeg -o -iname \*.png -o -iname \*.heic \) | wc -l | tr -d ' ')

if [ "$TOTAL_FILES" -eq 0 ]; then
    echo "No images found to convert in $TARGET_DIR."
else
    echo "Detected $CORES CPU cores."
    echo "Converting $TOTAL_FILES images..."

    # 2. Set up a temporary file to track completion count
    COUNTER_FILE=$(mktemp)

    # Export variables so the sub-shell spawned by xargs can access them
    export QUARANTINE_DIR
    export SALVAGE_DIR
    export ISSUES_LOG
    export COUNTER_FILE

    # 3. Start the background progress bar process
    (
        while true; do
            CURRENT=$(wc -l < "$COUNTER_FILE" | tr -d ' ')
            # Prevent division by zero if TOTAL_FILES is 0 (handled by if statement, but safe to guard)
            PERCENT=$(( CURRENT * 100 / TOTAL_FILES ))
            FILLED=$(( PERCENT / 2 ))
            EMPTY=$(( 50 - FILLED ))

            # Draw the progress bar
            printf "\r["
            [ $FILLED -gt 0 ] && printf "%${FILLED}s" | tr ' ' '#'
            [ $EMPTY -gt 0 ] && printf "%${EMPTY}s" | tr ' ' '-'
            printf "] %d%% (%d/%d)" "$PERCENT" "$CURRENT" "$TOTAL_FILES"

            # Stop the loop when finished
            if [ "$CURRENT" -ge "$TOTAL_FILES" ]; then
                printf "\n"
                break
            fi
            sleep 0.5
        done
    ) &
    PROGRESS_PID=$!

    # 4. Run the parallel conversion
    find "$TARGET_DIR" -type f \( -iname \*.jpg -o -iname \*.jpeg -o -iname \*.png -o -iname \*.heic \) -print0 | \
    xargs -0 -P "$CORES" -n 1 bash -c '
        img="$1"
        dir=$(dirname "$img")
        base=$(basename "$img")
        name="${base%.*}"
        avif_path="$dir/$name.avif"

        # Strict pass: Fail immediately if there is any corruption
        if magick -regard-warnings "${img}[0]" -quality 65 "$avif_path" 2>/dev/null; then
            # Conversion was successful
            rm "$img"
        else
            # Log the issue instead of echoing to the screen to protect the progress bar
            echo "WARNING: Corrupt data in $base. Forced salvage conversion." >> "$ISSUES_LOG"

            salvaged_avif="$SALVAGE_DIR/$name.avif"
            magick "${img}[0]" -quality 65 "$salvaged_avif" 2>/dev/null

            mv "$img" "$QUARANTINE_DIR/"
            rm -f "$avif_path"
        fi

        # Atomically increment the counter file
        echo "1" >> "$COUNTER_FILE"
    ' _

    # 5. Wait for the progress bar to naturally finish its final loop and clean up
    wait $PROGRESS_PID
    rm "$COUNTER_FILE"
fi

# ------------------------------------------------------------------------------
# PHASE 2.5 & 3: CLEANUP AND IMPORT (Apple Photos Mode Only)
# ------------------------------------------------------------------------------
if [ "$STANDALONE_MODE" = false ]; then
    echo ""
    echo "================================================="
    echo " PHASE 2.5: Sweeping for Orphaned Files"
    echo "================================================="
    echo "Cleaning up residual Live Photo videos and sidecar data..."
    find "$TARGET_DIR" -type f \( -iname \*.mov -o -iname \*.mp4 -o -iname \*.aae \) -delete

    echo ""
    echo "================================================="
    echo " PHASE 3: Re-Importing to Apple Photos"
    echo "================================================="
    echo "Importing clean files and rebuilding albums in the Photos app..."
    osxphotos import "$TARGET_DIR" --walk --album "{filepath.parent.name}"
fi

# ------------------------------------------------------------------------------
# COMPLETION
# ------------------------------------------------------------------------------
echo ""
echo "================================================="
echo " WORKFLOW COMPLETE!"
echo " All target images have been processed."
if [ -s "$ISSUES_LOG" ]; then
    echo " NOTE: Some corrupted files were found. Check $ISSUES_DIR"
fi

if [ "$STANDALONE_MODE" = false ]; then
    echo " You can safely delete the workspace folder: $TARGET_DIR"
fi
echo "================================================="