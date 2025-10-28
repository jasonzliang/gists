import json
import os
import requests
import shutil
from PIL import Image, ImageFile
import re
import concurrent.futures
import time
import argparse # Import argparse for command-line arguments
import tempfile # For creating temporary directories securely

# Allow loading truncated images (useful for potentially corrupted downloads)
ImageFile.LOAD_TRUNCATED_IMAGES = True

# --- Default Configuration (can be overridden by command-line args) ---
DEFAULT_OUTPUT_DIR = 'chapter_pdfs'
DEFAULT_MAX_WORKERS = 20
DEFAULT_PORTRAIT_WIDTH = 900 # Default target width for portrait images
DOWNLOAD_TIMEOUT = 30 # Seconds
RETRY_DELAY = 1 # Second delay between retries
MAX_RETRIES = 5 # Number of retries per image
LANDSCAPE_WIDTH_MULTIPLIER = 1.4 # Landscape width relative to portrait width
# ---------------------------------------------------------------------

def sanitize_filename(name):
    """Removes or replaces invalid characters for filenames."""
    # Remove characters that are definitely invalid on most systems, plus parentheses
    name = re.sub(r'[\\/*?:"<>|()]', '', name)
    # Replace sequences of whitespace with a single underscore
    name = re.sub(r'\s+', '_', name).strip('_')
    # Limit length (optional, uncomment if needed)
    # max_len = 180
    # if len(name) > max_len:
    #     name = name[:max_len]
    return name

def format_chapter_key(key):
    """Formats the chapter key for sorting and filename/directory use."""
    # Attempt to format as zero-padded integer for sorting
    try:
        f_key = float(key)
        if f_key.is_integer():
             # Pad integers with leading zeros
             return f"{int(f_key)}"
        else:
            # Handle keys like '103.5' -> '000103_5'
            parts = key.split('.', 1) # Split only once
            return f"{int(parts[0])}_{parts[1]}"
    except ValueError:
        # If it's not a number, sanitize it for path use and pad
         # Use zfill for padding strings which might represent chapters like 'extra'
         return sanitize_filename(str(key)).zfill(6)


def download_single_image_task(args):
    """Task function for downloading a single image with retries."""
    url, filepath, index = args
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT)
            response.raise_for_status() # Check for HTTP errors (4xx, 5xx)
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return index, filepath # Success: return original index and path
        except requests.exceptions.Timeout:
            error_message = f"Timeout downloading image {index+1} ({url}) on attempt {attempt+1}/{MAX_RETRIES+1}"
            log_level = "Retryable Error" if attempt < MAX_RETRIES else "Final Error"
        except requests.exceptions.RequestException as e:
            error_message = f"Request error for image {index+1} ({url}) on attempt {attempt+1}/{MAX_RETRIES+1}: {e}"
            log_level = "Retryable Error" if attempt < MAX_RETRIES else "Final Error"
        except Exception as e:
            error_message = f"Unexpected error downloading image {index+1} ({url}): {e}"
            log_level = "Error" # Don't retry unexpected errors

        # Log and potentially retry
        print(f"    [{log_level}] {error_message}")
        if log_level == "Retryable Error":
            print(f"      Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
        elif log_level == "Final Error":
            return index, None # Failure after retries
        else: # Unexpected Error
             return index, None

    return index, None # Fallback return if loop finishes unexpectedly


def download_images_parallel(urls, temp_dir, max_workers):
    """Downloads images in parallel using a thread pool."""
    tasks = []
    for i, url in enumerate(urls):
        img_filename = f"{i+1:04d}.img_tmp" # Use a consistent temp extension
        img_filepath = os.path.join(temp_dir, img_filename)
        tasks.append((url, img_filepath, i)) # Pass original index

    downloaded_paths_dict = {}
    total_tasks = len(tasks)
    completed_count = 0

    print(f"    Starting parallel download of {total_tasks} images (max workers: {max_workers})...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {executor.submit(download_single_image_task, task): task[2] for task in tasks}

        for future in concurrent.futures.as_completed(future_to_index):
            completed_count += 1
            index = future_to_index[future]
            try:
                original_index, result_path = future.result()
                if result_path:
                    downloaded_paths_dict[original_index] = result_path
                    # print(f"    ({completed_count}/{total_tasks}) Success: Image {original_index+1}") # Verbose
                else:
                    print(f"    ({completed_count}/{total_tasks}) Failed: Image {original_index+1}") # Error already printed
            except Exception as e:
                print(f"    ({completed_count}/{total_tasks}) [Error] Processing download result for index {index}: {e}")

    # Sort the successfully downloaded paths based on their original index
    sorted_paths = [downloaded_paths_dict[i] for i in sorted(downloaded_paths_dict) if i in downloaded_paths_dict]

    success_count = len(sorted_paths)
    if success_count != total_tasks:
        print(f"    [Warning] Downloaded {success_count} out of {total_tasks} images successfully.")
    else:
        print(f"    Finished downloading all {success_count} images.")
    return sorted_paths


def resize_image(img, target_portrait_width):
    """Resizes a PIL image object based on orientation."""
    try:
        original_width, original_height = img.size

        if original_width == 0 or original_height == 0:
            print("      [Warning] Image has zero dimension, skipping resize.")
            return img # Return original image if dimensions are invalid

        is_portrait = original_height > original_width
        # Calculate target width based on orientation and multiplier
        target_width = target_portrait_width if is_portrait else int(target_portrait_width * LANDSCAPE_WIDTH_MULTIPLIER)

        # Only resize if the original width is larger than the target width
        # if original_width <= target_width:
        #     # print(f"      Image width {original_width}px is already <= target {target_width}px. Skipping resize.")
        #     return img # No need to resize

        aspect_ratio = original_height / original_width
        target_height = int(target_width * aspect_ratio)

        # Use LANCZOS for high-quality downscaling
        # print(f"      Resizing image from {original_width}x{original_height} to {target_width}x{target_height}")
        # Ensure target dimensions are at least 1x1
        target_width = max(1, target_width)
        target_height = max(1, target_height)
        resized_img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
        return resized_img

    except Exception as e:
        print(f"      [Error] Failed to resize image: {e}")
        return img # Return original image on error


def create_pdf_from_images(image_paths, pdf_filepath, target_portrait_width):
    """Opens, resizes, converts, and creates a PDF from image paths."""
    if not image_paths:
        print(f"    [Warning] No image paths provided for PDF: {os.path.basename(pdf_filepath)}")
        return False # Indicate failure

    processed_images_pil = []
    success = False

    print(f"    Processing and resizing {len(image_paths)} images for PDF...")
    for i, img_path in enumerate(image_paths):
        img_object = None
        try:
            img_object = Image.open(img_path)

            # --- Resize Step ---
            resized_img = resize_image(img_object, target_portrait_width)
            # Important: Close the original if resize returned a *new* object
            if resized_img is not img_object:
                img_object.close()
            img_object = resized_img # Work with the potentially resized image
            # --------------------

            # --- Convert to RGB (if needed) ---
            if img_object.mode not in ('RGB', 'L'):
                # print(f"      Converting image {i+1} from {img_object.mode} to RGB.")
                converted_img = img_object.convert('RGB')
                img_object.close() # Close the resized object
                img_object = converted_img # Use the converted object
            # ----------------------------------

            processed_images_pil.append(img_object) # Add the final processed image object

        except (IOError, OSError, Image.UnidentifiedImageError, ValueError) as e: # Added ValueError for potential Pillow issues
            print(f"    [Warning] Skipping invalid/corrupt image file {os.path.basename(img_path)}: {e}")
            if img_object:
                try: img_object.close()
                except Exception: pass
        except Exception as e:
            print(f"    [Error] Unexpected error processing image {os.path.basename(img_path)}: {e}")
            if img_object:
                try: img_object.close()
                except Exception: pass

    if not processed_images_pil:
        print(f"    [Error] No valid images could be processed to create PDF: {os.path.basename(pdf_filepath)}")
        return False

    # --- Save PDF ---
    first_image = processed_images_pil[0]
    first_image = first_image.convert('RGBA')
    images_to_append = processed_images_pil[1:]
    images_to_append = [img.convert('RGB') for img in images_to_append]

    try:
        # Ensure the first image object is still valid before saving
        if not first_image:
             raise ValueError("First image object became invalid before saving.")

        first_image.save(
            pdf_filepath,
            "PDF",
            resolution=100.0,
            save_all=True,
            append_images=images_to_append,
            # quality=80
        )
        print(f"    [Success] Created PDF: {os.path.basename(pdf_filepath)}")
        success = True
    except Exception as e:
        print(f"    [Error] Failed to save PDF {os.path.basename(pdf_filepath)}: {e}")
    finally:
        # Ensure all PIL objects used for PDF generation are closed
        for img in processed_images_pil:
            try: img.close()
            except Exception: pass
    # ----------------

    return success


# --- Main Execution ---
if __name__ == "__main__":
    # --- Argument Parsing ---
    parser = argparse.ArgumentParser(description='Convert chapter images from a JSON file into individual PDFs, with resizing.')
    parser.add_argument('input_json', help='Path to the input JSON file.')
    parser.add_argument('-o', '--output-dir', default=DEFAULT_OUTPUT_DIR,
                        help=f'Directory to save the output PDFs (default: {DEFAULT_OUTPUT_DIR})')
    parser.add_argument('-w', '--workers', type=int, default=DEFAULT_MAX_WORKERS,
                        help=f'Maximum number of parallel image download workers (default: {DEFAULT_MAX_WORKERS})')
    parser.add_argument('-p', '--portrait-width', type=int, default=DEFAULT_PORTRAIT_WIDTH,
                        help=f'Target width in pixels for portrait images (landscape will be {LANDSCAPE_WIDTH_MULTIPLIER}x this) (default: {DEFAULT_PORTRAIT_WIDTH})')
    parser.add_argument('--force', action='store_true',
                        help='Force regeneration of PDFs even if they already exist.')

    args = parser.parse_args()

    # Validate inputs
    if not os.path.isfile(args.input_json):
        print(f"Error: Input JSON file not found: {args.input_json}")
        exit(1)
    if args.workers < 1:
        print("Error: Number of workers must be at least 1.")
        exit(1)
    if args.portrait_width < 50: # Arbitrary minimum sensible width
        print("Error: Portrait width must be at least 50 pixels.")
        exit(1)


    start_time = time.time()
    print(f"Starting PDF conversion process...")
    print(f"Input JSON: {args.input_json}")
    print(f"Output Directory: {args.output_dir}")
    print(f"Max Download Workers: {args.workers}")
    landscape_target_width = int(args.portrait_width * LANDSCAPE_WIDTH_MULTIPLIER)
    print(f"Target Portrait Width: {args.portrait_width}px (Landscape: ~{landscape_target_width}px)")
    print(f"Force Regeneration: {args.force}")

    # 1. Load JSON data
    try:
        with open(args.input_json, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"Successfully loaded JSON data.")
    except json.JSONDecodeError as e:
        print(f"Error: Could not decode JSON. Check format. Details: {e}")
        exit(1)
    except Exception as e:
        print(f"An unexpected error occurred loading JSON: {e}")
        exit(1)

    # 2. Create output directory
    try:
        os.makedirs(args.output_dir, exist_ok=True)
        print(f"Output directory '{args.output_dir}' ensured.")
    except OSError as e:
        print(f"Error creating output directory '{args.output_dir}': {e}")
        exit(1)

    # 3. Process each chapter
    chapters_data = None
    possible_keys = ['chapters', 'Chapters', 'content']
    for key in possible_keys:
        if key in data and isinstance(data[key], dict):
            chapters_data = data[key]
            print(f"Found chapters data under key: '{key}'")
            break

    if not chapters_data:
        print(f"Error: Could not find a 'chapters' dictionary (tried keys: {possible_keys}).")
        exit(1)

    manga_title = sanitize_filename(data.get('title', os.path.splitext(os.path.basename(args.input_json))[0]))

    try:
        sorted_chapter_keys = sorted(chapters_data.keys(), key=format_chapter_key)
    except Exception as e:
        print(f"Warning: Could not reliably sort chapter keys ({e}). Processing in default order.")
        sorted_chapter_keys = list(chapters_data.keys())

    processed_count = 0
    skipped_count = 0
    created_count = 0
    failed_count = 0
    total_chapters = len(sorted_chapter_keys)

    print(f"\nFound {total_chapters} chapters. Starting processing...")
    print("-" * 30)

    # Get the system's temp directory path once
    system_temp_dir = tempfile.gettempdir()
    print(f"Using system temporary directory: {system_temp_dir}")

    for i, chapter_key in enumerate(sorted_chapter_keys, 1):
        chapter_info = chapters_data.get(chapter_key)
        if not isinstance(chapter_info, dict):
            print(f"({i}/{total_chapters}) Skipping key '{chapter_key}': Value is not a dictionary.")
            failed_count += 1
            continue

        chapter_title_original = chapter_info.get('title', f'Chapter_{chapter_key}')
        formatted_key = format_chapter_key(chapter_key) # For filename/temp dir

        # --- Clean up the title part for filename ---
        # 1. Define pattern to find "Chapter X:" or "Chapter X " at the start
        #    Handles integers and decimals in X (like 8.5) by escaping the key
        #    Allows for potential whitespace variations.
        prefix_pattern = re.compile(rf"^\s*Chapter\s+{re.escape(str(chapter_key))}\s*[: ]?\s*", re.IGNORECASE)

        # 2. Check if the *original* title matches the pattern
        match = prefix_pattern.match(chapter_title_original)
        if match:
            # If matched, take the part *after* the match and sanitize it
            remaining_title = chapter_title_original[match.end():]
            title_part_for_filename = sanitize_filename(remaining_title).strip('_')
        else:
            # If no prefix match, sanitize the whole original title
            title_part_for_filename = sanitize_filename(chapter_title_original).strip('_')

        # If the title part becomes empty after potential stripping
        if not title_part_for_filename:
            title_part_for_filename = "Title" # Use a generic placeholder
        # --- End title cleanup ---

        pdf_filename = f"{manga_title}_Chapter_{formatted_key}_{title_part_for_filename}.pdf"
        pdf_filepath = os.path.join(args.output_dir, pdf_filename)

        print(f"({i}/{total_chapters}) Processing Chapter {chapter_key} ({chapter_title_original})...")

        if not args.force and os.path.exists(pdf_filepath):
            print(f"    [Skipped] PDF already exists: {pdf_filename}")
            skipped_count += 1
            print("-" * 20)
            continue

        processed_count += 1
        temp_dir_path = None # Define outside try block for cleanup

        try:
            # Create a unique temporary directory within the system's temp location
            temp_dir_prefix = f"json_pdf_{manga_title}_{formatted_key}_"
            temp_dir_path = tempfile.mkdtemp(prefix=temp_dir_prefix, dir=system_temp_dir)
            # print(f"    Created temporary directory: {temp_dir_path}") # Debugging

            image_urls = []
            groups = chapter_info.get('groups')
            if not groups and 'pages' in chapter_info and isinstance(chapter_info['pages'], list):
                 image_urls = chapter_info['pages']
                 # print("    Found image URLs under 'pages'.")
            elif isinstance(groups, dict) and groups:
                first_group_key = next(iter(groups), None)
                if first_group_key and isinstance(groups[first_group_key], list):
                    image_urls = groups[first_group_key]
                    # print(f"    Found image URLs under 'groups' -> '{first_group_key}'.")
                else:
                     print(f"    [Warning] Structure under 'groups' unexpected.")
            elif isinstance(groups, list):
                 image_urls = groups
                 # print("    Found image URLs directly under 'groups' list.")

            if not image_urls:
                print(f"    [Warning] No image URLs found. Skipping PDF creation.")
                failed_count += 1
                print("-" * 20)
                # Cleanup handled in finally block
                continue

            # Download images in parallel
            downloaded_image_paths = download_images_parallel(image_urls, temp_dir_path, args.workers)

            # Create PDF if downloads were successful
            if downloaded_image_paths:
                if create_pdf_from_images(downloaded_image_paths, pdf_filepath, args.portrait_width):
                    created_count += 1
                else:
                    failed_count += 1 # PDF creation failed
            else:
                 print(f"    [Failed] No images downloaded successfully. PDF not created.")
                 failed_count += 1

        except Exception as e:
             print(f"    [Error] Unhandled exception during processing chapter {chapter_key}: {e}")
             failed_count += 1
        finally:
            # Clean up temporary directory if it was created
            if temp_dir_path and os.path.exists(temp_dir_path):
                try:
                    shutil.rmtree(temp_dir_path)
                    # print(f"    Removed temporary directory: {temp_dir_path}") # Optional
                except OSError as e:
                    print(f"    [Error] Could not remove temporary directory {temp_dir_path}: {e}")
            print("-" * 20) # Separator

    # --- Final Summary ---
    end_time = time.time()
    total_time = end_time - start_time
    total_chapters = len(sorted_chapter_keys)
    success_count = created_count + skipped_count

    print("\n--- Script Finished ---")
    print("-" * 30)
    print(f"Total chapters in JSON: {total_chapters}")
    print(f"Successfully processed (created or skipped): {success_count}/{total_chapters}")
    print(f"  - New PDFs created: {created_count}")
    print(f"  - Skipped existing PDFs: {skipped_count}")
    print(f"Failed chapters (download/image/PDF errors): {failed_count}")
    print(f"Total execution time: {total_time:.2f} seconds")
    print(f"Output PDFs are in: '{os.path.abspath(args.output_dir)}'")
    print("-" * 30)