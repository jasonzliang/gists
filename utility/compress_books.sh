#!/usr/bin/env bash
#
# compress_books.sh — Reduce file sizes of ebooks/comics (lossless or near-lossless)
#
# Strategies:
#   PDFs:       Ghostscript /prepress preset (300dpi, high-quality JPEG, color-preserving)
#   EPUBs:      Unzip → lossless JPEG optimize (mozjpeg jpegtran) → rezip with deflate
#   CBZ:        Unzip → lossless JPEG optimize → repack with deflate
#   CBR:        Extract with 7z → lossless JPEG optimize → repack as CBZ
#   AVIF/other: Copied as-is
#
# All image optimization is fully lossless (Huffman + progressive rewrite only).
# PDF recompression is near-lossless (/prepress = highest quality Ghostscript preset).
#
# Originals are NEVER modified or deleted. Compressed copies go to the output directory.
#
# Usage: ./compress_books.sh <input_dir> [output_dir]
#   If output_dir is omitted, you will be prompted for it.

set -euo pipefail

INPUT_DIR="${1:-}"
OUTPUT_DIR="${2:-}"

if [ -z "$INPUT_DIR" ]; then
    echo "Usage: $0 <input_dir> [output_dir]"
    exit 1
fi

# Resolve to absolute paths
INPUT_DIR=$(cd "$INPUT_DIR" && pwd)

if [ -z "$OUTPUT_DIR" ]; then
    read -rp "Output directory: " OUTPUT_DIR
fi

# Create and resolve output dir
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd)

if [ "$INPUT_DIR" = "$OUTPUT_DIR" ]; then
    echo "Error: input and output directories must be different."
    exit 1
fi

JPEGTRAN="/opt/homebrew/opt/mozjpeg/bin/jpegtran"
GS="gs"
SEVENZ="7z"
NCPU=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)
JOBS_HEAVY=$NCPU
JOBS_LIGHT=$NCPU

# Temp file for thread-safe summary accumulation (each line: before_bytes after_bytes label)
STATS_FILE=$(mktemp /tmp/compress_stats_XXXXXX)
trap 'rm -f "$STATS_FILE"' EXIT

# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[COPY]${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC} $*"; }

# Check dependencies
for cmd in "$GS" "$JPEGTRAN" "$SEVENZ" magick zip unzip; do
    if ! command -v "$cmd" &>/dev/null && [ ! -x "$cmd" ]; then
        err "Missing required tool: $cmd"
        exit 1
    fi
done

file_size() {
    stat -f%z "$1" 2>/dev/null || stat --printf="%s" "$1" 2>/dev/null
}

human_size() {
    local bytes=$1
    if (( bytes >= 1073741824 )); then
        echo "$(echo "scale=1; $bytes / 1073741824" | bc)G"
    elif (( bytes >= 1048576 )); then
        echo "$(echo "scale=1; $bytes / 1048576" | bc)M"
    elif (( bytes >= 1024 )); then
        echo "$(echo "scale=1; $bytes / 1024" | bc)K"
    else
        echo "${bytes}B"
    fi
}

# Get the relative path of a file within INPUT_DIR
relpath() {
    echo "${1#"$INPUT_DIR"/}"
}

# Get the output path for a given input file
outpath() {
    echo "$OUTPUT_DIR/$(relpath "$1")"
}

# Ensure parent directory exists in output
ensure_outdir() {
    mkdir -p "$(dirname "$1")"
}

record_stats() {
    echo "$1 $2" >> "$STATS_FILE"
}

# ── Optimize a single JPEG (called by xargs) ──
optimize_one_jpeg() {
    local img="$1"
    local tmp="${img}.opt"
    if "$JPEGTRAN" -copy all -optimize -progressive -outfile "$tmp" "$img" 2>/dev/null; then
        local old_size new_size
        old_size=$(stat -f%z "$img" 2>/dev/null || stat --printf="%s" "$img" 2>/dev/null)
        new_size=$(stat -f%z "$tmp" 2>/dev/null || stat --printf="%s" "$tmp" 2>/dev/null)
        if (( new_size < old_size )); then
            mv "$tmp" "$img"
        else
            rm -f "$tmp"
        fi
    else
        rm -f "$tmp"
    fi
}
export -f optimize_one_jpeg
export JPEGTRAN

# ── Optimize a single PNG (called by xargs) ──
optimize_one_png() {
    local img="$1"
    local tmp="${img}.opt.png"
    if magick "$img" -strip -define png:compression-level=9 -define png:compression-strategy=1 -define png:compression-filter=5 "$tmp" 2>/dev/null; then
        local old_size new_size
        old_size=$(stat -f%z "$img" 2>/dev/null || stat --printf="%s" "$img" 2>/dev/null)
        new_size=$(stat -f%z "$tmp" 2>/dev/null || stat --printf="%s" "$tmp" 2>/dev/null)
        if (( new_size < old_size )); then
            mv "$tmp" "$img"
        else
            rm -f "$tmp"
        fi
    else
        rm -f "$tmp"
    fi
}
export -f optimize_one_png

# ── Convert a BMP/TIFF to optimized PNG (called by xargs) ──
convert_to_png() {
    local img="$1"
    local out="${img%.*}.png"
    if [ "$img" = "$out" ]; then return; fi
    if magick "$img" -strip -define png:compression-level=9 -define png:compression-strategy=1 -define png:compression-filter=5 "$out" 2>/dev/null; then
        rm -f "$img"
    fi
}
export -f convert_to_png

# ── Optimize all images in a directory (parallel) ──
optimize_images_in_dir() {
    local dir="$1"

    # Convert BMP/TIFF to PNG first
    find "$dir" -iregex '.*\.\(bmp\|tiff\?\)$' -print0 2>/dev/null \
        | xargs -0 -P "$JOBS_LIGHT" -I {} bash -c 'convert_to_png "$@"' _ {}

    find "$dir" -iregex '.*\.\(jpg\|jpeg\)$' -print0 2>/dev/null \
        | xargs -0 -P "$JOBS_LIGHT" -I {} bash -c 'optimize_one_jpeg "$@"' _ {}

    find "$dir" -iregex '.*\.png$' -print0 2>/dev/null \
        | xargs -0 -P "$JOBS_LIGHT" -I {} bash -c 'optimize_one_png "$@"' _ {}
}

# ── Process a PDF with Ghostscript ──
process_pdf() {
    local file="$1"
    local bname
    bname=$(basename "$file")
    local dest
    dest=$(outpath "$file")
    if [ -s "$dest" ]; then echo "[SKIP] PDF: $bname — already exists"; return; fi
    ensure_outdir "$dest"

    local size_before
    size_before=$(file_size "$file")

    local tmpout
    tmpout=$(mktemp /tmp/gs_XXXXXX)

    if "$GS" -sDEVICE=pdfwrite \
        -dCompatibilityLevel=1.5 \
        -dPDFSETTINGS=/prepress \
        -dNOPAUSE -dBATCH -dQUIET \
        -dAutoRotatePages=/None \
        -dColorImageDownsampleType=/Bicubic \
        -dGrayImageDownsampleType=/Bicubic \
        -dDownsampleColorImages=false \
        -dDownsampleGrayImages=false \
        -dDownsampleMonoImages=false \
        -sOutputFile="$tmpout" "$file" 2>/dev/null; then

        local size_after
        size_after=$(file_size "$tmpout")

        local threshold=$(( size_before * 95 / 100 ))
        if (( size_after == 0 )); then
            rm -f "$tmpout"
            cp "$file" "$dest"
            err "PDF: $bname — Ghostscript produced empty file, copied original"
        elif (( size_after < threshold )); then
            mv "$tmpout" "$dest"
            record_stats "$size_before" "$size_after"
            log "PDF: $bname  $(human_size "$size_before") → $(human_size "$size_after")  (saved $(human_size $((size_before - size_after))))"
        else
            rm -f "$tmpout"
            cp "$file" "$dest"
            warn "PDF: $bname — no significant savings, copied original ($(human_size "$size_before"))"
        fi
    else
        rm -f "$tmpout"
        cp "$file" "$dest"
        err "PDF: $bname — Ghostscript failed, copied original"
    fi
}

# ── Process an EPUB ──
process_epub() {
    local file="$1"
    local bname
    bname=$(basename "$file")
    local dest
    dest=$(outpath "$file")
    if [ -s "$dest" ]; then echo "[SKIP] EPUB: $bname — already exists"; return; fi
    ensure_outdir "$dest"

    local size_before
    size_before=$(file_size "$file")

    local tmpdir
    tmpdir=$(mktemp -d /tmp/epub_XXXXXX)
    local tmpout
    tmpout=$(mktemp /tmp/epub_out_XXXXXX)
    rm -f "$tmpout"
    tmpout="${tmpout}.zip"

    if ! unzip -q -o "$file" -d "$tmpdir" 2>/dev/null; then
        rm -rf "$tmpdir" "$tmpout"
        cp "$file" "$dest"
        err "EPUB: $bname — unzip failed, copied original"
        return
    fi

    optimize_images_in_dir "$tmpdir"

    (
        cd "$tmpdir"
        if [ -f mimetype ]; then
            zip -0 -X "$tmpout" mimetype
            zip -r -9 -X "$tmpout" . -x "mimetype" -x ".DS_Store" -x "*/.DS_Store" -x ".__*" -x "*/.___*"
        else
            zip -r -9 -X "$tmpout" . -x ".DS_Store" -x "*/.DS_Store" -x ".__*" -x "*/.___*"
        fi
    ) &>/dev/null

    if [ ! -s "$tmpout" ]; then
        rm -f "$tmpout"
        cp "$file" "$dest"
        err "EPUB: $bname — repack failed, copied original"
        rm -rf "$tmpdir"
        return
    fi

    local size_after
    size_after=$(file_size "$tmpout")

    local threshold=$(( size_before * 97 / 100 ))
    if (( size_after < threshold )); then
        mv "$tmpout" "$dest"
        record_stats "$size_before" "$size_after"
        log "EPUB: $bname  $(human_size "$size_before") → $(human_size "$size_after")  (saved $(human_size $((size_before - size_after))))"
    else
        rm -f "$tmpout"
        cp "$file" "$dest"
        warn "EPUB: $bname — no significant savings, copied original ($(human_size "$size_before"))"
    fi

    rm -rf "$tmpdir"
}

# ── Process a CBZ ──
process_cbz() {
    local file="$1"
    local bname
    bname=$(basename "$file")
    local dest
    dest=$(outpath "$file")
    if [ -s "$dest" ]; then echo "[SKIP] CBZ: $bname — already exists"; return; fi
    ensure_outdir "$dest"

    local size_before
    size_before=$(file_size "$file")

    local tmpdir
    tmpdir=$(mktemp -d /tmp/cbz_XXXXXX)
    local tmpout
    tmpout=$(mktemp /tmp/cbz_out_XXXXXX)
    rm -f "$tmpout"
    tmpout="${tmpout}.zip"

    if ! unzip -q -o "$file" -d "$tmpdir" 2>/dev/null; then
        rm -rf "$tmpdir" "$tmpout"
        cp "$file" "$dest"
        err "CBZ: $bname — unzip failed, copied original"
        return
    fi

    optimize_images_in_dir "$tmpdir"

    (cd "$tmpdir" && zip -r -9 "$tmpout" . -x ".DS_Store" -x "*/.DS_Store" -x ".__*" -x "*/.___*") &>/dev/null

    if [ ! -s "$tmpout" ]; then
        rm -f "$tmpout"
        cp "$file" "$dest"
        err "CBZ: $bname — repack failed, copied original"
        rm -rf "$tmpdir"
        return
    fi

    local size_after
    size_after=$(file_size "$tmpout")

    local threshold=$(( size_before * 97 / 100 ))
    if (( size_after < threshold )); then
        mv "$tmpout" "$dest"
        record_stats "$size_before" "$size_after"
        log "CBZ: $bname  $(human_size "$size_before") → $(human_size "$size_after")  (saved $(human_size $((size_before - size_after))))"
    else
        rm -f "$tmpout"
        cp "$file" "$dest"
        warn "CBZ: $bname — no significant savings, copied original ($(human_size "$size_before"))"
    fi

    rm -rf "$tmpdir"
}

# ── Process a CBR → CBZ ──
process_cbr() {
    local file="$1"
    local bname
    bname=$(basename "$file")
    # Output as .cbz
    local dest
    dest=$(outpath "$file")
    dest="${dest%.cbr}.cbz"
    local dest_cbr
    dest_cbr=$(outpath "$file")
    if [ -s "$dest" ] || [ -s "$dest_cbr" ]; then echo "[SKIP] CBR: $bname — already exists"; return; fi
    ensure_outdir "$dest"

    local size_before
    size_before=$(file_size "$file")

    local tmpdir
    tmpdir=$(mktemp -d /tmp/cbr_XXXXXX)

    if ! "$SEVENZ" x -o"$tmpdir" "$file" &>/dev/null; then
        rm -rf "$tmpdir"
        # Fall back to copying original as-is
        cp "$file" "$dest_cbr"
        err "CBR: $bname — extraction failed, copied original"
        return
    fi

    optimize_images_in_dir "$tmpdir"

    local tmpout
    tmpout=$(mktemp /tmp/cbr_out_XXXXXX)
    rm -f "$tmpout"
    tmpout="${tmpout}.zip"
    (cd "$tmpdir" && zip -r -9 "$tmpout" . -x ".DS_Store" -x "*/.DS_Store" -x ".__*" -x "*/.___*") &>/dev/null

    if [ ! -s "$tmpout" ]; then
        rm -f "$tmpout"
        cp "$file" "$dest_cbr"
        err "CBR: $bname — repack failed, copied original"
        rm -rf "$tmpdir"
        return
    fi

    local size_after
    size_after=$(file_size "$tmpout")

    local threshold=$(( size_before * 97 / 100 ))
    if (( size_after < threshold )); then
        mv "$tmpout" "$dest"
        record_stats "$size_before" "$size_after"
        log "CBR→CBZ: $bname  $(human_size "$size_before") → $(human_size "$size_after")  (saved $(human_size $((size_before - size_after))))"
    else
        rm -f "$tmpout"
        # Copy original .cbr unchanged
        cp "$file" "$dest_cbr"
        warn "CBR: $bname — no significant savings, copied original ($(human_size "$size_before"))"
    fi

    rm -rf "$tmpdir"
}

# ── Copy unprocessed files (non-PDF/EPUB/CBZ/CBR) to output ──
copy_other_files() {
    while IFS= read -r -d '' file; do
        local ext="${file##*.}"
        ext=$(echo "$ext" | tr '[:upper:]' '[:lower:]')
        case "$ext" in
            pdf|epub|cbz|cbr) continue ;;
        esac
        local dest
        dest=$(outpath "$file")
        if [ -e "$dest" ]; then continue; fi
        ensure_outdir "$dest"
        cp "$file" "$dest"
    done < <(find "$INPUT_DIR" -type f -print0 2>/dev/null)
}

# ── Parallel job runner with concurrency limit ──
run_parallel() {
    local max_jobs="$1"
    local func="$2"
    shift 2

    local running=0
    for file in "$@"; do
        "$func" "$file" &
        running=$((running + 1))
        if (( running >= max_jobs )); then
            wait -n 2>/dev/null || true
            running=$((running - 1))
        fi
    done
    wait
}

# ══════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════

echo "=========================================="
echo "  Book Collection Compressor"
echo "  Input:  $INPUT_DIR"
echo "  Output: $OUTPUT_DIR"
echo "  CPU cores: $NCPU  (heavy jobs: $JOBS_HEAVY, image jobs: $JOBS_LIGHT)"
echo "=========================================="
echo ""
echo "Originals will NOT be modified. Compressed copies go to the output directory."
echo ""

# Collect files into arrays (skip output dir if it's a subdirectory of input)
pdfs=(); epubs=(); cbzs=(); cbrs=()
while IFS= read -r -d '' f; do pdfs+=("$f"); done < <(find "$INPUT_DIR" -iname '*.pdf' -not -path "$OUTPUT_DIR/*" -print0 2>/dev/null)
while IFS= read -r -d '' f; do epubs+=("$f"); done < <(find "$INPUT_DIR" -iname '*.epub' -not -path "$OUTPUT_DIR/*" -print0 2>/dev/null)
while IFS= read -r -d '' f; do cbzs+=("$f"); done < <(find "$INPUT_DIR" -iname '*.cbz' -not -path "$OUTPUT_DIR/*" -print0 2>/dev/null)
while IFS= read -r -d '' f; do cbrs+=("$f"); done < <(find "$INPUT_DIR" -iname '*.cbr' -not -path "$OUTPUT_DIR/*" -print0 2>/dev/null)

echo "Found: ${#pdfs[@]} PDFs, ${#epubs[@]} EPUBs, ${#cbzs[@]} CBZs, ${#cbrs[@]} CBRs"
echo ""

# Export everything subshells need
export INPUT_DIR OUTPUT_DIR JPEGTRAN GS SEVENZ NCPU JOBS_LIGHT STATS_FILE
export GREEN YELLOW RED NC
export -f log warn err file_size human_size relpath outpath ensure_outdir record_stats
export -f optimize_images_in_dir optimize_one_jpeg optimize_one_png
export -f process_pdf process_epub process_cbz process_cbr

# Process each type in parallel
if (( ${#pdfs[@]} > 0 )); then
    echo "── Processing ${#pdfs[@]} PDFs (${JOBS_HEAVY} parallel) ──"
    run_parallel "$JOBS_HEAVY" process_pdf "${pdfs[@]}"
    echo ""
fi

if (( ${#epubs[@]} > 0 )); then
    echo "── Processing ${#epubs[@]} EPUBs (${JOBS_HEAVY} parallel) ──"
    run_parallel "$JOBS_HEAVY" process_epub "${epubs[@]}"
    echo ""
fi

if (( ${#cbzs[@]} > 0 )); then
    echo "── Processing ${#cbzs[@]} CBZs (${JOBS_HEAVY} parallel) ──"
    run_parallel "$JOBS_HEAVY" process_cbz "${cbzs[@]}"
    echo ""
fi

if (( ${#cbrs[@]} > 0 )); then
    echo "── Processing ${#cbrs[@]} CBRs (${JOBS_HEAVY} parallel) ──"
    run_parallel "$JOBS_HEAVY" process_cbr "${cbrs[@]}"
    echo ""
fi

# Copy all other file types unchanged
echo "── Copying other files ──"
copy_other_files
log "Other files copied to output."
echo ""

# Summary
echo "=========================================="
echo "  SUMMARY"
echo "=========================================="
if [ -s "$STATS_FILE" ]; then
    total_before=0
    total_after=0
    file_count=0
    while read -r before after; do
        total_before=$((total_before + before))
        total_after=$((total_after + after))
        file_count=$((file_count + 1))
    done < "$STATS_FILE"
    saved=$((total_before - total_after))
    pct=$(echo "scale=1; $saved * 100 / $total_before" | bc)
    echo "  Files compressed: $file_count"
    echo "  Size reduced: $(human_size $total_before) → $(human_size $total_after)"
    echo "  Total saved: $(human_size $saved) (${pct}%)"
else
    echo "  No files were compressed (all copied as-is)."
fi
echo "  Output: $OUTPUT_DIR"
echo "  Originals: untouched"
echo "=========================================="
