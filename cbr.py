#!/usr/bin/env python3
import shutil
import subprocess
import os
import sys
from pathlib import Path
import zipfile

def extract_archive_to_pdf(archive_file):
    """Extract CBR/CBZ and convert to PDF with flattened image structure."""
    archive_path = Path(archive_file)
    temp_dir = archive_path.parent / f"{archive_path.stem}_tmp"
    flat_dir = archive_path.parent / f"{archive_path.stem}_flat"
    pdf_path = archive_path.parent / f"{archive_path.stem}.pdf"

    try:
        temp_dir.mkdir(exist_ok=True)

        # Extract based on format
        if archive_path.suffix.lower() == '.cbr':
            subprocess.run(["unrar", "x", str(archive_path), str(temp_dir) + "/"],
                          check=True, capture_output=True)
        elif archive_path.suffix.lower() == '.cbz':
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)

        # Find all images recursively
        image_exts = {'.jpg', '.jpeg', '.png', '.gif'}
        images = [p for p in temp_dir.rglob('*')
                 if p.is_file() and p.suffix.lower() in image_exts]

        if not images:
            print(f"No images found in {archive_file}")
            return False

        # Sort images naturally
        images.sort(key=lambda p: str(p).lower())

        # Flatten: copy images to flat directory with numbered names
        flat_dir.mkdir(exist_ok=True)
        flat_images = []
        for i, img in enumerate(images, 1):
            flat_img = flat_dir / f"{i:04d}{img.suffix.lower()}"
            shutil.copy2(img, flat_img)
            flat_images.append(flat_img)

        # Convert to PDF
        subprocess.run(["convert"] + [str(p) for p in flat_images] + [str(pdf_path)],
                      check=True, capture_output=True)

        print(f"✓ Created {pdf_path.name}")
        return True

    except subprocess.CalledProcessError as e:
        print(f"✗ Error processing archive file {archive_file}: {e}")
        return False
    except zipfile.BadZipFile:
        print(f"✗ Error extracting CBZ file: {archive_file}")
        return False
    finally:
        # Cleanup
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        if flat_dir.exists():
            shutil.rmtree(flat_dir)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        if target.is_file() and target.suffix.lower() in {'.cbr', '.cbz'}:
            archive_files = [target]
        elif target.is_dir():
            archive_files = list(target.glob("*.cbr")) + list(target.glob("*.cbz"))
        else:
            print(f"Error: '{target}' is not a valid .cbr/.cbz file or directory")
            sys.exit(1)
    else:
        archive_files = list(Path.cwd().glob("*.cbr")) + list(Path.cwd().glob("*.cbz"))

    if not archive_files:
        print("No .cbr or .cbz files found")
        sys.exit(1)

    for archive in archive_files:
        extract_archive_to_pdf(archive)
