#!/usr/bin/env python3
"""
Universal file compressor — merges compress_file.py and compress_books.sh.

Supports: images, PDFs, EPUBs, CBZ, CBR, Office docs, SVG, JSON, XML.
Optimizes images inside archives (EPUB/CBZ/CBR). Converts CBR (RAR) → CBZ.

Usage:
    python compress_file.py <source_dir> <backup_or_output_dir> [options]

    # In-place mode (default): compresses files in source_dir, backs up originals
    python compress_file.py ./books ./backups

    # Copy mode: originals untouched, compressed copies go to output dir
    python compress_file.py ./books ./compressed --copy-mode

    # Heavy PDF compression via Ghostscript (much better than pikepdf)
    python compress_file.py ./books ./backups --gs

    # Dry run to estimate savings
    python compress_file.py ./books ./backups --dry-run
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import gzip
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
from datetime import datetime

try:
    from tqdm import tqdm
except ImportError:
    # Minimal fallback if tqdm not installed
    class tqdm:
        def __init__(self, total=0, **kwargs):
            self.total = total
            self.n = 0
        def update(self, n=1):
            self.n += n
            print(f"\r  {self.n}/{self.total}", end="", flush=True)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            print()

# --- CONFIGURATION ---
DEFAULT_JPG_QUALITY = 80
PNG_COMPRESS_LEVEL = 9
# Minimum savings threshold (percentage) to keep compressed version
PDF_SAVINGS_THRESHOLD = 0.05    # 5% for PDFs
ARCHIVE_SAVINGS_THRESHOLD = 0.03  # 3% for EPUB/CBZ/CBR
LOG_FILE = "compression_log.txt"

# File type groups
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.tiff', '.tif', '.bmp'}
PDF_EXTS = {'.pdf'}
ARCHIVE_BOOK_EXTS = {'.epub', '.cbz'}
CBR_EXTS = {'.cbr'}
OFFICE_EXTS = {'.docx', '.xlsx', '.pptx', '.odt'}
SVG_EXTS = {'.svg'}
TEXT_MINIFY_EXTS = {'.json', '.xml'}
ALL_TARGET_EXTS = (IMAGE_EXTS | PDF_EXTS | ARCHIVE_BOOK_EXTS | CBR_EXTS |
                   OFFICE_EXTS | SVG_EXTS | TEXT_MINIFY_EXTS)

DEFAULT_SKIP_DIRS = {
    'node_modules', '.git', '.venv', 'venv', '__pycache__',
    '.idea', '.vscode', '.DS_Store',
}


def get_file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def human_size(nbytes):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f}{unit}"
        nbytes /= 1024
    return f"{nbytes:.1f}PB"


def write_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def setup_output_path(source_file, source_root, output_root):
    """Get the corresponding path in the output/backup directory."""
    relative_path = os.path.relpath(source_file, source_root)
    out_path = os.path.join(output_root, relative_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    return out_path


# --- IMAGE HELPERS ---

def _open_pillow():
    from PIL import Image
    try:
        import pillow_avif  # noqa: F401 — registers AVIF codec
    except ImportError:
        pass
    return Image


def optimize_image_bytes(data, filename):
    """Optimize a single image's bytes using Pillow. Returns optimized bytes or None."""
    Image = _open_pillow()
    ext = os.path.splitext(filename)[1].lower()
    try:
        img = Image.open(io.BytesIO(data))
    except Exception:
        return None

    buf = io.BytesIO()
    if ext in ('.jpg', '.jpeg'):
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.save(buf, format='JPEG', quality=DEFAULT_JPG_QUALITY,
                 optimize=True, progressive=True)
    elif ext == '.png':
        img.save(buf, format='PNG', optimize=True,
                 compress_level=PNG_COMPRESS_LEVEL)
    elif ext in ('.bmp', '.tiff', '.tif'):
        # Convert BMP/TIFF to PNG
        img.save(buf, format='PNG', optimize=True,
                 compress_level=PNG_COMPRESS_LEVEL)
    elif ext == '.webp':
        img.save(buf, format='WEBP', quality=DEFAULT_JPG_QUALITY,
                 method=6)
    else:
        return None

    result = buf.getvalue()
    return result if len(result) < len(data) else None


# --- COMPRESSION WORKERS ---

def compress_image(source_path, output_path, use_avif, dry_run, copy_mode):
    """Compress a standalone image file."""
    Image = _open_pillow()
    try:
        orig_size = get_file_size(source_path)
        if dry_run:
            est = orig_size * 0.4 if use_avif else orig_size * 0.85
            return True, orig_size, est, f"Image (est): {source_path}"

        with Image.open(source_path) as img:
            if use_avif:
                new_path = os.path.splitext(
                    output_path if copy_mode else source_path)[0] + ".avif"
                if not copy_mode:
                    shutil.copy2(source_path, output_path)  # backup
                img.save(new_path, format="AVIF", quality=50, method=6)
                if not copy_mode and new_path != source_path:
                    os.remove(source_path)
                return (True, orig_size, get_file_size(new_path),
                        f"AVIF: {source_path}")

            orig_format = (img.format if img.format
                           else os.path.splitext(source_path)[1][1:].upper())
            if orig_format == "JPG":
                orig_format = "JPEG"

            save_params = {"format": orig_format, "optimize": True}
            if orig_format == "JPEG":
                orig_q = img.info.get("quality", DEFAULT_JPG_QUALITY)
                save_params["quality"] = min(DEFAULT_JPG_QUALITY, orig_q)
                save_params["progressive"] = True
            elif orig_format == "PNG":
                save_params["compress_level"] = PNG_COMPRESS_LEVEL

            # Strip metadata by rebuilding pixel data
            data = list(img.getdata())
            clean_img = Image.new(img.mode, img.size)
            clean_img.putdata(data)

            if copy_mode:
                clean_img.save(output_path, **save_params)
            else:
                shutil.copy2(source_path, output_path)  # backup
                clean_img.save(source_path, **save_params)

        dest = output_path if copy_mode else source_path
        new_size = get_file_size(dest)
        # If no savings in copy mode, use original
        if copy_mode and new_size >= orig_size:
            shutil.copy2(source_path, output_path)
            new_size = orig_size
        return True, orig_size, new_size, f"Image: {source_path}"
    except Exception as e:
        if copy_mode:
            try:
                shutil.copy2(source_path, output_path)
            except Exception:
                pass
        return False, 0, 0, f"FAIL image {source_path}: {e}"


def compress_pdf_pikepdf(source_path, output_path, dry_run, copy_mode):
    """Compress PDF using pikepdf (lightweight, fast)."""
    try:
        import pikepdf
    except ImportError:
        if copy_mode:
            try:
                shutil.copy2(source_path, output_path)
            except Exception:
                pass
        return False, 0, 0, f"FAIL PDF {source_path}: pikepdf not installed"
    try:
        orig_size = get_file_size(source_path)
        if dry_run:
            return True, orig_size, orig_size * 0.9, f"PDF/pikepdf (est): {source_path}"

        temp_pdf = source_path + ".tmp"
        with pikepdf.Pdf.open(source_path) as pdf:
            pdf.save(temp_pdf, compress_streams=True, linearize=True)

        new_size = get_file_size(temp_pdf)
        if new_size < orig_size * (1 - PDF_SAVINGS_THRESHOLD):
            if copy_mode:
                shutil.move(temp_pdf, output_path)
            else:
                shutil.copy2(source_path, output_path)  # backup
                shutil.move(temp_pdf, source_path)
            dest = output_path if copy_mode else source_path
            return True, orig_size, get_file_size(dest), f"PDF/pikepdf: {source_path}"
        else:
            os.remove(temp_pdf)
            if copy_mode:
                shutil.copy2(source_path, output_path)
            return True, orig_size, orig_size, f"PDF/pikepdf (no savings): {source_path}"
    except Exception as e:
        if copy_mode:
            try:
                shutil.copy2(source_path, output_path)
            except Exception:
                pass
        return False, 0, 0, f"FAIL PDF {source_path}: {e}"


def compress_pdf_ghostscript(source_path, output_path, dry_run, copy_mode):
    """Compress PDF using Ghostscript /prepress preset (best quality+compression)."""
    try:
        orig_size = get_file_size(source_path)
        if dry_run:
            return True, orig_size, orig_size * 0.7, f"PDF/gs (est): {source_path}"

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp_path = tmp.name

        cmd = [
            'gs', '-sDEVICE=pdfwrite',
            '-dCompatibilityLevel=1.5',
            '-dPDFSETTINGS=/prepress',
            '-dNOPAUSE', '-dBATCH', '-dQUIET',
            '-dAutoRotatePages=/None',
            '-dColorImageDownsampleType=/Bicubic',
            '-dGrayImageDownsampleType=/Bicubic',
            '-dDownsampleColorImages=false',
            '-dDownsampleGrayImages=false',
            '-dDownsampleMonoImages=false',
            f'-sOutputFile={tmp_path}',
            source_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)

        new_size = get_file_size(tmp_path)
        if result.returncode != 0 or new_size == 0:
            os.unlink(tmp_path)
            # Fall back to pikepdf
            return compress_pdf_pikepdf(source_path, output_path, dry_run, copy_mode)

        if new_size < orig_size * (1 - PDF_SAVINGS_THRESHOLD):
            if copy_mode:
                shutil.move(tmp_path, output_path)
            else:
                shutil.copy2(source_path, output_path)  # backup
                shutil.move(tmp_path, source_path)
            dest = output_path if copy_mode else source_path
            return True, orig_size, get_file_size(dest), f"PDF/gs: {source_path}"
        else:
            os.unlink(tmp_path)
            if copy_mode:
                shutil.copy2(source_path, output_path)
            return True, orig_size, orig_size, f"PDF/gs (no savings): {source_path}"
    except FileNotFoundError:
        # Ghostscript not installed, fall back to pikepdf
        return compress_pdf_pikepdf(source_path, output_path, dry_run, copy_mode)
    except Exception as e:
        if copy_mode:
            try:
                shutil.copy2(source_path, output_path)
            except Exception:
                pass
        return False, 0, 0, f"FAIL PDF/gs {source_path}: {e}"


def _optimize_images_in_zip(z_in, z_out, file_ext):
    """Read images from z_in, optimize them, write to z_out."""
    for item in z_in.infolist():
        if item.is_dir():
            continue
        data = z_in.read(item.filename)
        inner_ext = os.path.splitext(item.filename)[1].lower()

        if inner_ext in IMAGE_EXTS:
            optimized = optimize_image_bytes(data, item.filename)
            if optimized is not None:
                data = optimized
            # Convert BMP/TIFF filenames to .png
            if inner_ext in ('.bmp', '.tiff', '.tif'):
                item.filename = os.path.splitext(item.filename)[0] + '.png'

        # Skip macOS junk files
        basename = os.path.basename(item.filename)
        if basename in ('.DS_Store',) or basename.startswith('._'):
            continue

        z_out.writestr(item, data)


def compress_epub(source_path, output_path, dry_run, copy_mode):
    """Compress EPUB: optimize internal images, repack with max deflate.
    Preserves mimetype as first entry stored uncompressed (EPUB spec)."""
    try:
        orig_size = get_file_size(source_path)
        if dry_run:
            return True, orig_size, orig_size * 0.9, f"EPUB (est): {source_path}"

        temp_path = source_path + ".tmp"
        with zipfile.ZipFile(source_path, 'r') as z_in:
            with zipfile.ZipFile(temp_path, 'w') as z_out:
                # EPUB spec: mimetype must be first entry, stored uncompressed
                has_mimetype = 'mimetype' in z_in.namelist()
                if has_mimetype:
                    z_out.writestr('mimetype', z_in.read('mimetype'),
                                   compress_type=zipfile.ZIP_STORED)

                for item in z_in.infolist():
                    if item.filename == 'mimetype' or item.is_dir():
                        continue
                    basename = os.path.basename(item.filename)
                    if basename in ('.DS_Store',) or basename.startswith('._'):
                        continue

                    data = z_in.read(item.filename)
                    inner_ext = os.path.splitext(item.filename)[1].lower()

                    if inner_ext in IMAGE_EXTS:
                        optimized = optimize_image_bytes(data, item.filename)
                        if optimized is not None:
                            data = optimized
                        if inner_ext in ('.bmp', '.tiff', '.tif'):
                            item.filename = (os.path.splitext(item.filename)[0]
                                             + '.png')

                    z_out.writestr(item, data,
                                   compress_type=zipfile.ZIP_DEFLATED,
                                   compresslevel=9)

        new_size = get_file_size(temp_path)
        if new_size < orig_size * (1 - ARCHIVE_SAVINGS_THRESHOLD):
            if copy_mode:
                shutil.move(temp_path, output_path)
            else:
                shutil.copy2(source_path, output_path)  # backup
                shutil.move(temp_path, source_path)
            dest = output_path if copy_mode else source_path
            return (True, orig_size, get_file_size(dest),
                    f"EPUB: {source_path}")
        else:
            os.remove(temp_path)
            if copy_mode:
                shutil.copy2(source_path, output_path)
            return (True, orig_size, orig_size,
                    f"EPUB (no savings): {source_path}")
    except Exception as e:
        if copy_mode:
            try:
                shutil.copy2(source_path, output_path)
            except Exception:
                pass
        return False, 0, 0, f"FAIL EPUB {source_path}: {e}"


def compress_cbz(source_path, output_path, dry_run, copy_mode):
    """Compress CBZ: optimize internal images, repack with max deflate."""
    try:
        orig_size = get_file_size(source_path)
        if dry_run:
            return True, orig_size, orig_size * 0.9, f"CBZ (est): {source_path}"

        temp_path = source_path + ".tmp"
        with zipfile.ZipFile(source_path, 'r') as z_in:
            with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED,
                                 compresslevel=9) as z_out:
                _optimize_images_in_zip(z_in, z_out, '.cbz')

        new_size = get_file_size(temp_path)
        if new_size < orig_size * (1 - ARCHIVE_SAVINGS_THRESHOLD):
            if copy_mode:
                shutil.move(temp_path, output_path)
            else:
                shutil.copy2(source_path, output_path)  # backup
                shutil.move(temp_path, source_path)
            dest = output_path if copy_mode else source_path
            return True, orig_size, get_file_size(dest), f"CBZ: {source_path}"
        else:
            os.remove(temp_path)
            if copy_mode:
                shutil.copy2(source_path, output_path)
            return (True, orig_size, orig_size,
                    f"CBZ (no savings): {source_path}")
    except Exception as e:
        if copy_mode:
            try:
                shutil.copy2(source_path, output_path)
            except Exception:
                pass
        return False, 0, 0, f"FAIL CBZ {source_path}: {e}"


def compress_cbr(source_path, output_path, dry_run, copy_mode):
    """Compress CBR: extract RAR, optimize images, repack as CBZ."""
    try:
        import rarfile
    except ImportError:
        # Fall back to 7z command
        return _compress_cbr_7z(source_path, output_path, dry_run, copy_mode)

    try:
        orig_size = get_file_size(source_path)
        # Output as .cbz
        output_path = os.path.splitext(output_path)[0] + '.cbz'
        if not copy_mode:
            # For in-place, the new file replaces with .cbz extension
            pass

        if dry_run:
            return True, orig_size, orig_size * 0.85, f"CBR→CBZ (est): {source_path}"

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        temp_cbz = source_path + ".cbz.tmp"

        with rarfile.RarFile(source_path, 'r') as rf:
            with zipfile.ZipFile(temp_cbz, 'w', zipfile.ZIP_DEFLATED,
                                 compresslevel=9) as z_out:
                for entry in rf.infolist():
                    if entry.isdir():
                        continue
                    basename = os.path.basename(entry.filename)
                    if basename in ('.DS_Store',) or basename.startswith('._'):
                        continue

                    data = rf.read(entry.filename)
                    inner_ext = os.path.splitext(entry.filename)[1].lower()

                    if inner_ext in IMAGE_EXTS:
                        optimized = optimize_image_bytes(data, entry.filename)
                        if optimized is not None:
                            data = optimized
                        if inner_ext in ('.bmp', '.tiff', '.tif'):
                            entry.filename = (
                                os.path.splitext(entry.filename)[0] + '.png')

                    z_out.writestr(entry.filename, data)

        new_size = get_file_size(temp_cbz)
        if new_size > 0 and new_size < orig_size * (1 - ARCHIVE_SAVINGS_THRESHOLD):
            if copy_mode:
                shutil.move(temp_cbz, output_path)
            else:
                # backup original .cbr
                backup_path = os.path.splitext(output_path)[0] + '.cbr'
                shutil.copy2(source_path, backup_path)
                # Place new .cbz next to original
                new_cbz = os.path.splitext(source_path)[0] + '.cbz'
                shutil.move(temp_cbz, new_cbz)
                os.remove(source_path)
            return (True, orig_size, new_size,
                    f"CBR→CBZ: {source_path}")
        else:
            os.remove(temp_cbz)
            if copy_mode:
                shutil.copy2(source_path, output_path)
            return (True, orig_size, orig_size,
                    f"CBR (no savings): {source_path}")
    except Exception as e:
        if copy_mode:
            try:
                cbr_out = os.path.splitext(output_path)[0] + '.cbr'
                shutil.copy2(source_path, cbr_out)
            except Exception:
                pass
        return False, 0, 0, f"FAIL CBR {source_path}: {e}"


def _compress_cbr_7z(source_path, output_path, dry_run, copy_mode):
    """Fallback CBR handler using 7z command-line tool."""
    try:
        orig_size = get_file_size(source_path)
        output_path = os.path.splitext(output_path)[0] + '.cbz'

        if dry_run:
            return True, orig_size, orig_size * 0.85, f"CBR→CBZ/7z (est): {source_path}"

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        tmpdir = tempfile.mkdtemp(prefix='cbr_')
        try:
            subprocess.run(['7z', 'x', f'-o{tmpdir}', source_path],
                           capture_output=True, check=True, timeout=120)

            temp_cbz = source_path + ".cbz.tmp"
            with zipfile.ZipFile(temp_cbz, 'w', zipfile.ZIP_DEFLATED,
                                 compresslevel=9) as z_out:
                for root, dirs, files in os.walk(tmpdir):
                    for fname in files:
                        basename = os.path.basename(fname)
                        if basename in ('.DS_Store',) or basename.startswith('._'):
                            continue
                        full_path = os.path.join(root, fname)
                        arcname = os.path.relpath(full_path, tmpdir)
                        with open(full_path, 'rb') as f:
                            data = f.read()

                        inner_ext = os.path.splitext(fname)[1].lower()
                        if inner_ext in IMAGE_EXTS:
                            optimized = optimize_image_bytes(data, fname)
                            if optimized is not None:
                                data = optimized
                            if inner_ext in ('.bmp', '.tiff', '.tif'):
                                arcname = os.path.splitext(arcname)[0] + '.png'

                        z_out.writestr(arcname, data)

            new_size = get_file_size(temp_cbz)
            if new_size > 0:
                if copy_mode:
                    shutil.move(temp_cbz, output_path)
                else:
                    backup_path = os.path.splitext(output_path)[0] + '.cbr'
                    shutil.copy2(source_path, backup_path)
                    new_cbz = os.path.splitext(source_path)[0] + '.cbz'
                    shutil.move(temp_cbz, new_cbz)
                    os.remove(source_path)
                return True, orig_size, new_size, f"CBR→CBZ/7z: {source_path}"
            else:
                os.remove(temp_cbz)
                if copy_mode:
                    shutil.copy2(source_path, output_path)
                return True, orig_size, orig_size, f"CBR/7z (no savings): {source_path}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    except FileNotFoundError:
        if copy_mode:
            try:
                shutil.copy2(source_path, os.path.splitext(output_path)[0] + '.cbr')
            except Exception:
                pass
        return False, 0, 0, f"FAIL CBR {source_path}: neither rarfile nor 7z available"
    except Exception as e:
        if copy_mode:
            try:
                shutil.copy2(source_path, os.path.splitext(output_path)[0] + '.cbr')
            except Exception:
                pass
        return False, 0, 0, f"FAIL CBR/7z {source_path}: {e}"


def repack_zip_format(source_path, output_path, dry_run, copy_mode):
    """Repack ZIP-based files (Office docs) with max deflate."""
    try:
        orig_size = get_file_size(source_path)
        if dry_run:
            return True, orig_size, orig_size * 0.95, f"ZIP repack (est): {source_path}"

        temp_path = source_path + ".tmp"
        with zipfile.ZipFile(source_path, 'r') as z_in:
            with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED,
                                 compresslevel=9) as z_out:
                for item in z_in.infolist():
                    z_out.writestr(item, z_in.read(item.filename))

        new_size = get_file_size(temp_path)
        if new_size < orig_size:
            if copy_mode:
                shutil.move(temp_path, output_path)
            else:
                shutil.copy2(source_path, output_path)
                shutil.move(temp_path, source_path)
            dest = output_path if copy_mode else source_path
            return True, orig_size, get_file_size(dest), f"ZIP repack: {source_path}"
        else:
            os.remove(temp_path)
            if copy_mode:
                shutil.copy2(source_path, output_path)
            return True, orig_size, orig_size, f"ZIP repack (no savings): {source_path}"
    except Exception as e:
        if copy_mode:
            try:
                shutil.copy2(source_path, output_path)
            except Exception:
                pass
        return False, 0, 0, f"FAIL ZIP {source_path}: {e}"


def minify_text_file(source_path, output_path, file_ext, dry_run, copy_mode):
    """Minify JSON or XML files."""
    try:
        orig_size = get_file_size(source_path)
        if dry_run:
            return True, orig_size, orig_size * 0.8, f"Minify (est): {source_path}"

        with open(source_path, 'r', encoding='utf-8') as f:
            if file_ext == '.json':
                data = json.load(f)
                content = json.dumps(data, separators=(',', ':'))
            elif file_ext == '.xml':
                tree = ET.parse(f)
                content = ET.tostring(tree.getroot(), encoding='unicode',
                                      method='xml')
            else:
                return False, 0, 0, f"Unknown text format: {source_path}"

        if copy_mode:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)
        else:
            shutil.copy2(source_path, output_path)
            with open(source_path, 'w', encoding='utf-8') as f:
                f.write(content)

        dest = output_path if copy_mode else source_path
        return True, orig_size, get_file_size(dest), f"Minified: {source_path}"
    except Exception as e:
        if copy_mode:
            try:
                shutil.copy2(source_path, output_path)
            except Exception:
                pass
        return False, 0, 0, f"FAIL minify {source_path}: {e}"


def compress_svg(source_path, output_path, dry_run, copy_mode):
    """Compress SVG to SVGZ (gzipped SVG)."""
    try:
        orig_size = get_file_size(source_path)
        if dry_run:
            return True, orig_size, orig_size * 0.5, f"SVGZ (est): {source_path}"

        svgz_source = os.path.splitext(source_path)[0] + ".svgz"
        svgz_output = os.path.splitext(output_path)[0] + ".svgz"

        with open(source_path, 'rb') as f_in:
            with gzip.open(svgz_source if not copy_mode else svgz_output,
                           'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

        if not copy_mode:
            shutil.move(source_path, output_path)  # backup original
        dest = svgz_output if copy_mode else svgz_source
        return True, orig_size, get_file_size(dest), f"SVGZ: {source_path}"
    except Exception as e:
        if copy_mode:
            try:
                shutil.copy2(source_path, output_path)
            except Exception:
                pass
        return False, 0, 0, f"FAIL SVG {source_path}: {e}"


# --- DISPATCHER ---

def process_file_task(task_args):
    (file_path, source_root, output_root, file_ext,
     use_avif, use_gs, dry_run, copy_mode) = task_args
    output_path = setup_output_path(file_path, source_root, output_root)

    if file_ext in IMAGE_EXTS:
        return compress_image(file_path, output_path, use_avif, dry_run,
                              copy_mode)
    elif file_ext in PDF_EXTS:
        if use_gs:
            return compress_pdf_ghostscript(file_path, output_path, dry_run,
                                            copy_mode)
        else:
            return compress_pdf_pikepdf(file_path, output_path, dry_run,
                                        copy_mode)
    elif file_ext == '.epub':
        return compress_epub(file_path, output_path, dry_run, copy_mode)
    elif file_ext == '.cbz':
        return compress_cbz(file_path, output_path, dry_run, copy_mode)
    elif file_ext in CBR_EXTS:
        return compress_cbr(file_path, output_path, dry_run, copy_mode)
    elif file_ext in OFFICE_EXTS:
        return repack_zip_format(file_path, output_path, dry_run, copy_mode)
    elif file_ext in SVG_EXTS:
        return compress_svg(file_path, output_path, dry_run, copy_mode)
    elif file_ext in TEXT_MINIFY_EXTS:
        return minify_text_file(file_path, output_path, file_ext, dry_run,
                                copy_mode)
    return False, 0, 0, f"Unknown type: {file_path}"


# --- MAIN ---

def main():
    parser = argparse.ArgumentParser(
        description="Universal file compressor for images, PDFs, ebooks, "
                    "comics, Office docs, SVG, JSON, and XML.")
    parser.add_argument("source_dir", help="Directory to compress.")
    parser.add_argument("output_dir",
                        help="Backup dir (in-place mode) or output dir "
                             "(copy mode).")
    parser.add_argument("--copy-mode", action="store_true",
                        help="Write compressed files to output dir, leave "
                             "originals untouched.")
    parser.add_argument("--gs", action="store_true",
                        help="Use Ghostscript for PDFs (better compression, "
                             "requires gs installed).")
    parser.add_argument("--avif", action="store_true",
                        help="Convert images to AVIF format.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Estimate savings without modifying files.")
    parser.add_argument("--workers", type=int,
                        default=multiprocessing.cpu_count(),
                        help="Number of parallel workers.")
    parser.add_argument("--exclude", nargs='*', default=[],
                        help="Additional folder names/paths to exclude.")
    parser.add_argument("--copy-other", action="store_true",
                        help="In copy mode, also copy non-target files to "
                             "output dir.")

    args = parser.parse_args()
    source_root = os.path.abspath(args.source_dir)
    output_root = os.path.abspath(args.output_dir)

    if source_root == output_root:
        print("Error: source and output directories must be different.")
        sys.exit(1)

    # Init log
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"--- Compression Session: {datetime.now()} ---\n")

    mode_str = "Copy" if args.copy_mode else "In-place (backup)"
    pdf_str = "Ghostscript" if args.gs else "pikepdf"
    print(f"Source:  {source_root}")
    print(f"Output:  {output_root}")
    print(f"Mode:    {mode_str} | PDF engine: {pdf_str} | "
          f"AVIF: {args.avif} | Workers: {args.workers}")
    if args.dry_run:
        print("DRY RUN — no files will be modified")
    print()

    user_excludes = set(args.exclude)
    abs_excludes = {os.path.abspath(os.path.join(source_root, x))
                    for x in user_excludes}

    tasks = []
    other_files = []

    for root, dirs, files in os.walk(source_root):
        # Filter directories
        filtered = []
        for d in dirs:
            full = os.path.abspath(os.path.join(root, d))
            if (full == output_root or d in DEFAULT_SKIP_DIRS or
                    d in user_excludes or full in abs_excludes):
                continue
            filtered.append(d)
        dirs[:] = filtered

        for fname in files:
            file_ext = os.path.splitext(fname)[1].lower()
            full_path = os.path.join(root, fname)
            if file_ext in ALL_TARGET_EXTS:
                tasks.append((full_path, source_root, output_root, file_ext,
                              args.avif, args.gs, args.dry_run,
                              args.copy_mode))
            elif args.copy_mode and args.copy_other:
                other_files.append(full_path)

    # Count by type
    type_counts = {}
    for t in tasks:
        ext = t[3]
        type_counts[ext] = type_counts.get(ext, 0) + 1
    print(f"Found {len(tasks)} files to process:")
    for ext, count in sorted(type_counts.items()):
        print(f"  {ext}: {count}")
    print()

    if not tasks:
        print("No target files found.")
        return

    ctx = multiprocessing.get_context('spawn')
    total_old, total_new, success_count, fail_count = 0, 0, 0, 0

    with ProcessPoolExecutor(max_workers=args.workers,
                             mp_context=ctx) as executor:
        futures = [executor.submit(process_file_task, t) for t in tasks]

        with tqdm(total=len(tasks), desc="Compressing", unit="file") as pbar:
            for future in as_completed(futures):
                success, old_sz, new_sz, log_msg = future.result()
                write_log(log_msg)
                if success:
                    success_count += 1
                    total_old += old_sz
                    total_new += new_sz
                else:
                    fail_count += 1
                pbar.update(1)

    # Copy other files in copy mode
    if other_files and not args.dry_run:
        print(f"\nCopying {len(other_files)} other files...")
        for fp in other_files:
            dest = setup_output_path(fp, source_root, output_root)
            if not os.path.exists(dest):
                shutil.copy2(fp, dest)

    # Summary
    saved = total_old - total_new
    pct = (saved / total_old * 100) if total_old > 0 else 0
    print(f"\nDone! {success_count}/{len(tasks)} succeeded"
          + (f", {fail_count} failed" if fail_count else ""))
    print(f"Space saved: {human_size(saved)} "
          f"({human_size(total_old)} -> {human_size(total_new)}, "
          f"{pct:.1f}% reduction)")
    print(f"Log: {os.path.abspath(LOG_FILE)}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
