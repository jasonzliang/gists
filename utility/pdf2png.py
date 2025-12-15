import argparse
import sys
from pdf2image import convert_from_path
from PIL import Image

def concat_pdf_pages(pdf_path, output_path, dpi=200, side_margin=50, top_margin=50, bottom_margin=50):
    """
    Converts PDF to strip with side margins, plus a top margin on the first page
    and a bottom margin on the last page.
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

        # Final Width = Max Page Width + Left Margin + Right Margin
        final_width = max_page_width + (side_margin * 2)

        # Final Height = All pages + Top Margin + Bottom Margin
        final_height = total_page_height + top_margin + bottom_margin

        # 2. Create blank white image
        final_image = Image.new('RGB', (final_width, final_height), (255, 255, 255))

        # 3. Paste pages
        # Start at 'top_margin' so the first page is pushed down
        current_y = top_margin

        for page in pages:
            # Calculate X to center the page horizontally
            x_offset = side_margin + ((max_page_width - page.width) // 2)

            final_image.paste(page, (x_offset, current_y))

            # Move Y down by the page height so the next page touches this one immediately
            current_y += page.height

        # Note: We don't need to explicitly "draw" the bottom margin.
        # Since 'final_height' includes 'bottom_margin', the space after the last paste
        # remains white automatically.

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
    parser.add_argument("--side-margin", type=int, default=50, help="Pixels on Left/Right")
    parser.add_argument("--top-margin", type=int, default=50, help="Pixels above first page")
    parser.add_argument("--bottom-margin", type=int, default=50, help="Pixels below last page")

    args = parser.parse_args()

    concat_pdf_pages(
        args.input_pdf,
        args.output_image,
        args.dpi,
        side_margin=args.side_margin,
        top_margin=args.top_margin,
        bottom_margin=args.bottom_margin
    )