import argparse
import sys
import os
from pdf2image import convert_from_path
from PIL import Image

def concat_pdf_pages(pdf_path, output_path, dpi=200, side_margin=50, top_margin=50, bottom_margin=50, quality=95):
    """
    Converts PDF to strip with margins and quality control.
    quality: JPEG quality (1-100). Ignored for PNG (which uses lossless optimization).
    """
    print(f"Processing: {pdf_path}...")

    try:
        pages = convert_from_path(pdf_path, dpi=dpi, thread_count=4)

        if not pages:
            print("Error: The PDF appears to be empty.")
            return

        # 1. Calculate dimensions
        max_page_width = max(page.width for page in pages)
        total_page_height = sum(page.height for page in pages)

        final_width = max_page_width + (side_margin * 2)
        final_height = total_page_height + top_margin + bottom_margin

        # 2. Create blank white image
        final_image = Image.new('RGB', (final_width, final_height), (255, 255, 255))

        # 3. Paste pages
        current_y = top_margin

        for page in pages:
            x_offset = side_margin + ((max_page_width - page.width) // 2)
            final_image.paste(page, (x_offset, current_y))
            current_y += page.height

        # 4. Save with Quality Settings
        # Check file extension to determine save method
        ext = os.path.splitext(output_path)[1].lower()

        if ext in ['.jpg', '.jpeg']:
            # JPEG: Use 'quality' parameter (1-100)
            # subsampling=0 turns off chroma subsampling for sharper colors/text
            final_image.save(output_path, quality=quality, subsampling=0)
            print(f"Success! Saved JPG to {output_path} (Quality: {quality})")

        elif ext == '.png':
            # PNG: Use 'optimize' flag for smaller file size (lossless)
            # PNG doesn't use the 'quality' param in the same way as JPEG
            final_image.save(output_path, optimize=True)
            print(f"Success! Saved PNG to {output_path} (Optimized)")

        else:
            # Fallback for other formats (BMP, TIFF, etc)
            final_image.save(output_path)
            print(f"Success! Saved to {output_path}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input_pdf", help="Path to source PDF")
    parser.add_argument("output_image", help="Path to output image")
    parser.add_argument("--dpi", type=int, default=200)

    # Margin controls
    parser.add_argument("--side-margin", type=int, default=50)
    parser.add_argument("--top-margin", type=int, default=50)
    parser.add_argument("--bottom-margin", type=int, default=50)

    # Quality control
    parser.add_argument("--quality", type=int, default=95, help="JPEG Quality (1-100). Default is 95.")

    args = parser.parse_args()

    concat_pdf_pages(
        args.input_pdf,
        args.output_image,
        args.dpi,
        side_margin=args.side_margin,
        top_margin=args.top_margin,
        bottom_margin=args.bottom_margin,
        quality=args.quality
    )