import argparse
import os
import shutil
import zipfile
import gzip
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
from tqdm import tqdm
from datetime import datetime

# --- CONFIGURATION ---
DEFAULT_JPG_QUALITY = 80
LOG_FILE = "compression_log.txt"

def setup_backup_dir(source_file, source_root, backup_root):
    relative_path = os.path.relpath(source_file, source_root)
    backup_file_path = os.path.join(backup_root, relative_path)
    backup_dir = os.path.dirname(backup_file_path)
    os.makedirs(backup_dir, exist_ok=True)
    return backup_file_path

def get_file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0

def write_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")

# --- COMPRESSION WORKERS ---

def compress_image(source_path, backup_path, use_avif, dry_run):
    from PIL import Image
    try:
        import pillow_avif
    except ImportError: pass

    try:
        orig_size = get_file_size(source_path)
        if not dry_run: shutil.copy2(source_path, backup_path)

        with Image.open(source_path) as img:
            if use_avif:
                new_path = os.path.splitext(source_path)[0] + ".avif"
                if not dry_run:
                    img.save(new_path, format="AVIF", quality=50, method=6)
                    os.remove(source_path)
                return True, orig_size, (orig_size * 0.4 if dry_run else get_file_size(new_path)), f"AVIF: {source_path}"

            orig_format = img.format if img.format else os.path.splitext(source_path)[1][1:].upper()
            if orig_format == "JPG": orig_format = "JPEG"

            save_params = {"format": orig_format, "optimize": True}
            if orig_format == "JPEG":
                orig_q = img.info.get("quality", DEFAULT_JPG_QUALITY)
                save_params["quality"] = min(DEFAULT_JPG_QUALITY, orig_q)
                save_params["progressive"] = True

            if not dry_run:
                data = list(img.getdata())
                clean_img = Image.new(img.mode, img.size)
                clean_img.putdata(data)
                clean_img.save(source_path, **save_params)

            return True, orig_size, (orig_size * 0.85 if dry_run else get_file_size(source_path)), f"Optimized: {source_path}"
    except Exception as e: return False, 0, 0, f"FAIL {source_path}: {str(e)}"

def compress_pdf(source_path, backup_path, dry_run):
    import pikepdf
    try:
        orig_size = get_file_size(source_path)
        if dry_run: return True, orig_size, orig_size * 0.9, f"PDF (est): {source_path}"

        temp_pdf = source_path + ".tmp"
        with pikepdf.Pdf.open(source_path) as pdf:
            pdf.save(temp_pdf, compress_streams=True, linearize=True)
        shutil.move(source_path, backup_path)
        shutil.move(temp_pdf, source_path)
        return True, orig_size, get_file_size(source_path), f"PDF: {source_path}"
    except Exception as e: return False, 0, 0, f"FAIL {source_path}: {str(e)}"

def repack_zip_format(source_path, backup_path, dry_run):
    try:
        orig_size = get_file_size(source_path)
        if dry_run: return True, orig_size, orig_size * 0.95, f"ZIP-Based (est): {source_path}"

        temp_zip = source_path + ".tmp"
        with zipfile.ZipFile(source_path, 'r') as z_in:
            with zipfile.ZipFile(temp_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z_out:
                for item in z_in.infolist():
                    z_out.writestr(item, z_in.read(item.filename))
        shutil.move(source_path, backup_path)
        shutil.move(temp_zip, source_path)
        return True, orig_size, get_file_size(source_path), f"Repacked: {source_path}"
    except Exception as e: return False, 0, 0, f"FAIL {source_path}: {str(e)}"

def minify_text_file(source_path, backup_path, file_ext, dry_run):
    try:
        orig_size = get_file_size(source_path)
        if dry_run: return True, orig_size, orig_size * 0.8, f"Minified (est): {source_path}"

        shutil.copy2(source_path, backup_path)
        with open(source_path, 'r', encoding='utf-8') as f:
            if file_ext == '.json':
                data = json.load(f)
                content = json.dumps(data, separators=(',', ':'))
            elif file_ext == '.xml':
                tree = ET.parse(f)
                content = ET.tostring(tree.getroot(), encoding='unicode', method='xml')

        with open(source_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, orig_size, get_file_size(source_path), f"Minified: {source_path}"
    except Exception as e: return False, 0, 0, f"FAIL {source_path}: {str(e)}"

def compress_svg(source_path, backup_path, dry_run):
    try:
        orig_size = get_file_size(source_path)
        if dry_run: return True, orig_size, orig_size * 0.5, f"SVGZ (est): {source_path}"

        new_ext_path = os.path.splitext(source_path)[0] + ".svgz"
        with open(source_path, 'rb') as f_in:
            with gzip.open(new_ext_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        shutil.move(source_path, backup_path)
        return True, orig_size, get_file_size(new_ext_path), f"SVGZ: {source_path}"
    except Exception as e: return False, 0, 0, f"FAIL {source_path}: {str(e)}"

# --- DISPATCHER ---

def process_file_task(task_args):
    file_path, source_root, backup_root, file_ext, use_avif, dry_run = task_args
    backup_path = setup_backup_dir(file_path, source_root, backup_root)

    if file_ext in {'.png', '.jpg', '.jpeg', '.webp', '.tiff'}:
        return compress_image(file_path, backup_path, use_avif, dry_run)
    elif file_ext == '.pdf':
        return compress_pdf(file_path, backup_path, dry_run)
    elif file_ext in {'.docx', '.xlsx', '.pptx', '.odt', '.epub', '.cbz'}:
        return repack_zip_format(file_path, backup_path, dry_run)
    elif file_ext == '.svg':
        return compress_svg(file_path, backup_path, dry_run)
    elif file_ext in {'.json', '.xml'}:
        return minify_text_file(file_path, backup_path, file_ext, dry_run)
    return False, 0, 0, f"Unknown: {file_path}"

# --- MAIN ---

def main():
    parser = argparse.ArgumentParser(description="Exhaustive multithreaded file compressor.")
    parser.add_argument("source_dir", help="Directory to search.")
    parser.add_argument("backup_dir", help="Directory for backups.")
    parser.add_argument("--avif", action="store_true", help="Convert images to AVIF.")
    parser.add_argument("--dry-run", action="store_true", help="Estimate savings.")
    parser.add_argument("--workers", type=int, default=multiprocessing.cpu_count(), help="Worker count.")
    parser.add_argument("--exclude", nargs='*', default=[], help="Exclude folder names/paths.")

    args = parser.parse_args()
    source_root = os.path.abspath(args.source_dir)
    backup_root = os.path.abspath(args.backup_dir)

    # Clear/Init log
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"--- Compression Session: {datetime.now()} ---\n")

    print(f"📁 Source Dir: {source_root} | Backup Dir: {backup_root}")
    print(f"🚀 Using {args.workers} workers | AVIF: {args.avif} | Dry Run: {args.dry_run}")

    default_skip_names = {'node_modules', '.git', '.venv', 'venv', '__pycache__', '.idea', '.vscode'}
    user_excludes = set(args.exclude)
    abs_user_excludes = {os.path.abspath(os.path.join(source_root, x)) for x in user_excludes}

    tasks = []
    target_exts = {'.png', '.jpg', '.jpeg', '.webp', '.tiff', '.pdf', '.docx', '.xlsx', '.pptx', '.odt', '.epub', '.cbz', '.svg', '.json', '.xml'}

    for root, dirs, files in os.walk(source_root):
        filtered_dirs = []
        for d in dirs:
            full_dir_path = os.path.abspath(os.path.join(root, d))
            if (full_dir_path == backup_root or d in default_skip_names or
                d in user_excludes or full_dir_path in abs_user_excludes):
                continue
            filtered_dirs.append(d)
        dirs[:] = filtered_dirs

        for file in files:
            file_ext = os.path.splitext(file)[1].lower()
            if file_ext in target_exts:
                tasks.append((os.path.join(root, file), source_root, backup_root, file_ext, args.avif, args.dry_run))

    if not tasks:
        print("🛑 No files found.")
        return

    ctx = multiprocessing.get_context('spawn')
    total_old_size, total_new_size, success_count = 0, 0, 0

    with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as executor:
        futures = [executor.submit(process_file_task, t) for t in tasks]

        with tqdm(total=len(tasks), desc="Processing", unit="file", colour="green") as pbar:
            for future in as_completed(futures):
                success, old_sz, new_sz, log_msg = future.result()
                write_log(log_msg)
                if success:
                    success_count += 1
                    total_old_size += old_sz
                    total_new_size += new_sz
                pbar.update(1)

    saved_mb = (total_old_size - total_new_size) / (1024 * 1024)
    percent = ((total_old_size - total_new_size) / total_old_size * 100) if total_old_size > 0 else 0

    print(f"\n✨ Finished! {success_count}/{len(tasks)} files processed.")
    print(f"📊 Space Saved: {saved_mb:.2f} MB ({percent:.1f}% reduction)")
    print(f"📝 Review detailed logs in: {os.path.abspath(LOG_FILE)}")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()