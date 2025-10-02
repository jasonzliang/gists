#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from pathlib import Path

def extract_cbr_to_pdf(cbr_file):
    """Extract CBR and convert to PDF with flattened image structure."""
    cbr_path = Path(cbr_file)
    temp_dir = cbr_path.parent / f"{cbr_path.stem}_tmp"
    flat_dir = cbr_path.parent / f"{cbr_path.stem}_flat"
    pdf_path = cbr_path.parent / f"{cbr_path.stem}.pdf"

    try:
        # Extract CBR
        temp_dir.mkdir(exist_ok=True)
        subprocess.run(["unrar", "x", str(cbr_path), str(temp_dir) + "/"],
                      check=True, capture_output=True)

        # Find all images recursively
        image_exts = {'.jpg', '.jpeg', '.png', '.gif'}
        images = [p for p in temp_dir.rglob('*')
                 if p.is_file() and p.suffix.lower() in image_exts]

        if not images:
            print(f"No images found in {cbr_file}")
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
        print(f"✗ Error processing {cbr_file}: {e}")
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

        if target.is_file() and target.suffix.lower() == '.cbr':
            cbr_files = [target]
        elif target.is_dir():
            cbr_files = list(target.glob("*.cbr"))
        else:
            print(f"Error: '{target}' is not a valid .cbr file or directory")
            sys.exit(1)
    else:
        cbr_files = list(Path.cwd().glob("*.cbr"))

    if not cbr_files:
        print("No .cbr files found")
        sys.exit(1)

    for cbr in cbr_files:
        extract_cbr_to_pdf(cbr)
