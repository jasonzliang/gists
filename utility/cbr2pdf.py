#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import os
import sys
from pathlib import Path
import zipfile

def optimize_image(img_path, max_dim=2048, jpeg_quality=80):
    """Reduce image file size with minimal perceptual quality loss.

    - Downscales images whose longest side exceeds max_dim.
    - Re-encodes as JPEG at the given quality (skips already-small files).
    - Returns the (possibly new) path to the optimized image.
    """
    from PIL import Image

    img = Image.open(img_path)

    # Downscale if oversized
    w, h = img.size
    if max(w, h) > max_dim:
        ratio = max_dim / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    # Always save as JPEG for smaller size (convert incompatible modes)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    optimized = img_path.with_suffix(".jpg")
    img.save(optimized, "JPEG", quality=jpeg_quality, optimize=True)

    # If we created a new file (was .png/.gif), remove the original
    if optimized != img_path and img_path.exists():
        img_path.unlink()

    return optimized

def optimize_pdf(pdf_file, max_dim=2048, jpeg_quality=80):
    """Extract images from a PDF, optimize them, and repack into a new PDF."""
    import fitz  # PyMuPDF
    from PIL import Image
    import io
    import tempfile

    pdf_path = Path(pdf_file)
    orig_size = pdf_path.stat().st_size

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        doc = fitz.open(pdf_path)
        optimized_images = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            image_list = page.get_images(full=True)
            if not image_list:
                continue
            # Use the largest image on the page (the main content)
            best = max(image_list, key=lambda x: x[2] * x[3])
            xref = best[0]
            base_image = doc.extract_image(xref)
            img = Image.open(io.BytesIO(base_image["image"]))

            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            w, h = img.size
            if max(w, h) > max_dim:
                ratio = max_dim / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

            out_path = tmp_dir / f"{page_num:04d}.jpg"
            img.save(out_path, "JPEG", quality=jpeg_quality, optimize=True)
            optimized_images.append(out_path)

        doc.close()

        if not optimized_images:
            print(f"No images found in {pdf_path.name}")
            return False

        # Rebuild PDF from optimized images
        new_doc = fitz.open()
        for img_path in optimized_images:
            img = Image.open(img_path)
            w, h = img.size
            page = new_doc.new_page(width=w, height=h)
            page.insert_image(fitz.Rect(0, 0, w, h), filename=str(img_path))

        new_doc.save(str(pdf_path) + ".tmp", deflate=True, garbage=4)
        new_doc.close()

        tmp_pdf = Path(str(pdf_path) + ".tmp")
        opt_size = tmp_pdf.stat().st_size
        if opt_size < orig_size:
            tmp_pdf.replace(pdf_path)
            saved = (1 - opt_size / orig_size) * 100
            print(f"✓ Optimized {pdf_path.name}: {orig_size/1024/1024:.1f}MB → {opt_size/1024/1024:.1f}MB ({saved:.0f}% smaller)")
        else:
            tmp_pdf.unlink()
            print(f"✓ {pdf_path.name} already compact, no change")

    return True

def extract_archive_to_pdf(archive_file, optimize=False, max_dim=2048, jpeg_quality=80):
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
            if optimize:
                flat_img = optimize_image(flat_img, max_dim=max_dim, jpeg_quality=jpeg_quality)
            flat_images.append(flat_img)

        # Convert to PDF
        subprocess.run(["convert"] + [str(p) for p in flat_images] + [str(pdf_path)],
                      check=True, capture_output=True)

        # Ghostscript PDF optimization: re-encodes images and strips metadata
        if optimize and shutil.which("gs"):
            optimized_pdf = pdf_path.with_name(pdf_path.stem + "_opt.pdf")
            gs_result = subprocess.run([
                "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
                "-dNOPAUSE", "-dBATCH", "-dQUIET",
                "-dColorImageDownsampleType=/Bicubic",
                "-dColorImageResolution=300",
                "-dGrayImageDownsampleType=/Bicubic",
                "-dGrayImageResolution=300",
                "-dAutoFilterColorImages=false",
                "-dAutoFilterGrayImages=false",
                "-dColorImageFilter=/FlateEncode",
                "-dGrayImageFilter=/FlateEncode",
                f"-sOutputFile={optimized_pdf}",
                str(pdf_path),
            ], capture_output=True)
            if gs_result.returncode == 0 and optimized_pdf.exists():
                orig_size = pdf_path.stat().st_size
                opt_size = optimized_pdf.stat().st_size
                if opt_size < orig_size:
                    optimized_pdf.replace(pdf_path)
                    saved = (1 - opt_size / orig_size) * 100
                    print(f"  Optimized: {orig_size/1024/1024:.1f}MB → {opt_size/1024/1024:.1f}MB ({saved:.0f}% smaller)")
                else:
                    optimized_pdf.unlink()

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
    parser = argparse.ArgumentParser(description="Convert CBR/CBZ archives to PDF, or optimize existing PDFs")
    parser.add_argument("target", nargs="?", default=None,
                        help="CBR/CBZ/PDF file or directory (default: current directory)")
    parser.add_argument("-o", "--optimize", action="store_true",
                        help="Reduce PDF file size with minimal perceptual quality loss")
    parser.add_argument("-q", "--quality", type=int, default=80,
                        help="JPEG quality for image optimization (1-100, default: 80)")
    parser.add_argument("-d", "--downscale", type=int, default=2048,
                        help="Max pixel dimension before downscaling (default: 2048)")
    args = parser.parse_args()

    archive_exts = {'.cbr', '.cbz'}
    all_exts = archive_exts | {'.pdf'}

    if args.target:
        target = Path(args.target)
        if target.is_file() and target.suffix.lower() in all_exts:
            files = [target]
        elif target.is_dir():
            files = [p for p in target.iterdir() if p.suffix.lower() in all_exts]
        else:
            print(f"Error: '{target}' is not a valid .cbr/.cbz/.pdf file or directory")
            sys.exit(1)
    else:
        files = [p for p in Path.cwd().iterdir() if p.suffix.lower() in all_exts]

    if not files:
        print("No .cbr, .cbz, or .pdf files found")
        sys.exit(1)

    for f in sorted(files):
        if f.suffix.lower() == '.pdf':
            if args.optimize:
                optimize_pdf(f, max_dim=args.downscale, jpeg_quality=args.quality)
            else:
                print(f"Skipping {f.name} (use -o to optimize existing PDFs)")
        else:
            extract_archive_to_pdf(f, optimize=args.optimize,
                                   max_dim=args.downscale, jpeg_quality=args.quality)
