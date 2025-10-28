import json
import os
import requests
import shutil
from PIL import Image
import re
import concurrent.futures
import time
import argparse
import tempfile # For creating temporary directories securely

# --- Default Configuration (can be overridden by command-line args) ---
DEFAULT_OUTPUT_DIR = 'chapter_pdfs'  # Output directory for PDFs
DEFAULT_MAX_WORKERS = 20  # Number of workers to speed up download
DOWNLOAD_TIMEOUT = 30  # Timeout in seconds
RETRY_DELAY = 1  # Second delay between retries
MAX_RETRIES = 5  # Number of retries per image
# ---------------------

def sanitize_filename(name):
    """Removes or replaces invalid characters for filenames."""
    # Remove characters that are definitely invalid on most systems
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    # Replace spaces with underscores
    name = re.sub(r'\s+', '_', name).strip('_')
    # Limit length (optional, uncomment if needed)
    # max_len = 150
    # if len(name) > max_len:
    #     name = name[:max_len]
    return name

def format_chapter_key(key):
    """Formats the chapter key for sorting and filename/directory use."""
    # Attempt to format as zero-padded integer for sorting
    try:
        # Check if it's a float first, then format appropriately
        f_key = float(key)
        if f_key.is_integer():
             return f"{int(f_key):04d}"
        else:
            # Handle keys like '103.5' -> '0103_5'
            parts = key.split('.')
            return f"{int(parts[0]):04d}_{parts[1]}"
    except ValueError:
        # If it's not a number, sanitize it for path use
         return sanitize_filename(str(key))


def download_single_image_task(args):
    """Task function for downloading a single image."""
    url, filepath, index = args
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT)
            response.raise_for_status()
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return index, filepath
        except requests.exceptions.Timeout:
            error_message = f"Timeout downloading image {index+1} ({url}) on attempt {attempt+1}/{MAX_RETRIES+1}"
            # Log retry/failure
        except requests.exceptions.RequestException as e:
            error_message = f"Request error for image {index+1} ({url}) on attempt {attempt+1}/{MAX_RETRIES+1}: {e}"
            # Log retry/failure
        except Exception as e:
            error_message = f"Unexpected error downloading image {index+1} ({url}): {e}"
            # Log failure immediately
            print(f"    [Error] {error_message}")
            return index, None # Don't retry unexpected errors

        # If an exception occurred and we're retrying:
        if attempt < MAX_RETRIES:
            print(f"    [Retryable Error] {error_message}. Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
        else:
            print(f"    [Final Error] {error_message}. Giving up.")
            return index, None # Failed after retries

    return index, None # Should not be reached

def download_images_parallel(urls, temp_dir, max_workers):
    """Downloads images in parallel using a thread pool."""
    tasks = []
    for i, url in enumerate(urls):
        # Create a unique-ish temp filename within the chapter's temp dir
        img_filename = f"{i+1:04d}.img_tmp" # Use a consistent temp extension
        img_filepath = os.path.join(temp_dir, img_filename)
        tasks.append((url, img_filepath, i))

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
                    # Verbose success print (optional):
                    # print(f"    ({completed_count}/{total_tasks}) Success: Image {original_index+1}")
                else:
                    # Error message already printed in download_single_image_task
                    print(f"    ({completed_count}/{total_tasks}) Failed: Image {original_index+1}")
            except Exception as e:
                print(f"    ({completed_count}/{total_tasks}) [Error] Exception processing download task result for index {index}: {e}")

    # Sort paths based on the original index to maintain order for PDF creation
    sorted_paths = [downloaded_paths_dict[i] for i in sorted(downloaded_paths_dict) if i in downloaded_paths_dict]

    success_count = len(sorted_paths)
    if success_count != total_tasks:
        print(f"    [Warning] Downloaded {success_count} out of {total_tasks} images successfully.")
    else:
        print(f"    Finished downloading {success_count} images.")
    return sorted_paths


def create_pdf_from_images(image_paths, pdf_filepath):
    """Creates a PDF from a list of image file paths."""
    if not image_paths:
        print(f"    [Warning] No valid images provided to create PDF: {os.path.basename(pdf_filepath)}")
        return False # Indicate failure

    images_pil_to_append = []
    first_image_opened = None
    first_image_path = None # Store path of the first successfully opened image

    img_iter = iter(image_paths)
    while first_image_opened is None:
        try:
            current_path = next(img_iter)
            first_image_opened = Image.open(current_path)
            first_image_path = current_path
        except StopIteration:
            print(f"    [Error] No valid images could be opened for PDF: {os.path.basename(pdf_filepath)}")
            return False # Indicate failure
        except (IOError, OSError, Image.UnidentifiedImageError) as e:
            print(f"    [Warning] Skipping invalid first image {os.path.basename(current_path)}: {e}")
        except Exception as e:
             print(f"    [Error] Unexpected error opening first image {os.path.basename(current_path)}: {e}")


    if not first_image_opened: # Should be caught by StopIteration, but as a safeguard
        print(f"    [Error] Failed to open any image as the first page for PDF.")
        return False

    pdf_created = False
    try:
        # Convert first image to RGB if necessary (handles RGBA, P, etc.)
        if first_image_opened.mode not in ('RGB', 'L'): # Allow RGB or Grayscale
             # print(f"    Converting first image {os.path.basename(first_image_path)} from {first_image_opened.mode} to RGB.")
             first_image_converted = first_image_opened.convert('RGB')
             first_image_opened.close() # Close original
             first_image_opened = first_image_converted # Use converted

        # Process remaining images from the iterator
        for img_path in img_iter:
             img_object = None # Ensure img_object is reset
             try:
                 img_object = Image.open(img_path)
                 if img_object.mode not in ('RGB', 'L'):
                     # print(f"    Converting image {os.path.basename(img_path)} from {img_object.mode} to RGB.")
                     img_converted = img_object.convert('RGB')
                     img_object.close() # Close original
                     images_pil_to_append.append(img_converted) # Add converted
                 else:
                    images_pil_to_append.append(img_object) # Add as is
             except (IOError, OSError, Image.UnidentifiedImageError) as e:
                 print(f"    [Warning] Skipping invalid subsequent image {os.path.basename(img_path)}: {e}")
                 if img_object: img_object.close() # Close if opened but invalid
             except Exception as e:
                  print(f"    [Error] Unexpected error opening subsequent image {os.path.basename(img_path)}: {e}")
                  if img_object: img_object.close()

        # Save as PDF
        if images_pil_to_append:
             # Save first image + append others
             first_image_opened.save(pdf_filepath, "PDF", resolution=100.0, save_all=True, append_images=images_pil_to_append)
        else:
            # Only the first image was valid, save just that one
             first_image_opened.save(pdf_filepath, "PDF", resolution=100.0)

        print(f"    [Success] Created PDF: {os.path.basename(pdf_filepath)}")
        pdf_created = True

    except Exception as e:
        print(f"    [Error] Failed during PDF creation for {os.path.basename(pdf_filepath)}: {e}")
    finally:
        # Ensure all PIL Image objects are closed
        if first_image_opened:
            try: first_image_opened.close()
            except Exception: pass
        for img in images_pil_to_append:
             try: img.close()
             except Exception: pass
    return pdf_created


# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Convert chapter images from a JSON file into individual PDFs.')
    parser.add_argument('json_file', help='Path to the input JSON file.')
    parser.add_argument('-o', '--output-dir', default=DEFAULT_OUTPUT_DIR,
                        help=f'Directory to save the output PDFs (default: {DEFAULT_OUTPUT_DIR})')
    parser.add_argument('-w', '--workers', type=int, default=DEFAULT_MAX_WORKERS,
                        help=f'Number of parallel download workers (default: {DEFAULT_MAX_WORKERS})')
    parser.add_argument('--force', action='store_true',
                        help='Force regeneration of PDFs even if they already exist.')


    args = parser.parse_args()

    # Validate inputs
    if not os.path.isfile(args.json_file):
        print(f"Error: Input JSON file not found: {args.json_file}")
        exit(1)
    if args.workers < 1:
        print("Error: Number of workers must be at least 1.")
        exit(1)

    start_time = time.time()
    print(f"Starting PDF conversion process...")
    print(f"Input JSON: {args.json_file}")
    print(f"Output Directory: {args.output_dir}")
    print(f"Max Download Workers: {args.workers}")
    print(f"Force Regeneration: {args.force}")


    # 1. Load JSON data
    try:
        with open(args.json_file, 'r', encoding='utf-8') as f:
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
    # Try to find the chapters dictionary, flexible key name (common variations)
    chapters_data = None
    possible_keys = ['chapters', 'Chapters', 'content'] # Add other likely keys if needed
    for key in possible_keys:
        if key in data and isinstance(data[key], dict):
            chapters_data = data[key]
            print(f"Found chapters data under key: '{key}'")
            break

    if not chapters_data:
        print(f"Error: Could not find a 'chapters' dictionary in the JSON (tried keys: {possible_keys}). Check JSON structure.")
        exit(1)

    # Get title for PDF naming prefix (optional, falls back)
    manga_title = sanitize_filename(data.get('title', os.path.splitext(os.path.basename(args.json_file))[0]))


    # Sort chapters numerically/alphanumerically based on formatted keys
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

    for i, chapter_key in enumerate(sorted_chapter_keys, 1):
        chapter_info = chapters_data.get(chapter_key)
        if not isinstance(chapter_info, dict):
            print(f"Skipping key '{chapter_key}': Value is not a dictionary.")
            failed_count += 1
            continue

        chapter_title = chapter_info.get('title', f'Chapter_{chapter_key}')
        formatted_key = format_chapter_key(chapter_key) # For filename/temp dir

        # Construct potential PDF filename
        sanitized_title_part = sanitize_filename(chapter_title)
        pdf_filename = f"{manga_title}_Chapter_{formatted_key}_{sanitized_title_part}.pdf"
        # pdf_filename = f"Chapter_{formatted_key}_{sanitized_title_part}.pdf" # Alternative simpler naming
        pdf_filepath = os.path.join(args.output_dir, pdf_filename)

        print(f"({i}/{total_chapters}) Processing Chapter {chapter_key} ('{chapter_title}')...")

        # Check if PDF already exists and --force is not used
        if not args.force and os.path.exists(pdf_filepath):
            print(f"    [Skipped] PDF already exists: {pdf_filename}")
            skipped_count += 1
            print("-" * 20)
            continue # Move to the next chapter

        processed_count += 1 # Count as processed attempt
        temp_dir = None # Ensure temp_dir is reset

        try:
            # Create a unique temporary directory in the system's temp location
            # Prefix includes manga title and chapter key for easier identification
            temp_dir_prefix = f"json_pdf_{manga_title}_{formatted_key}_"
            temp_dir = tempfile.mkdtemp(prefix=temp_dir_prefix)
            # print(f"    Created temporary directory: {temp_dir}") # Optional: for debugging path

            image_urls = []
            # Extract image URLs (more flexible search)
            groups = chapter_info.get('groups') # Common key
            if not groups and 'pages' in chapter_info and isinstance(chapter_info['pages'], list):
                 image_urls = chapter_info['pages'] # Alternative structure
                 print("    Found image URLs under 'pages' key.")
            elif isinstance(groups, dict) and groups:
                # Get URLs from the first group found (original assumption)
                first_group_key = next(iter(groups), None)
                if first_group_key and isinstance(groups[first_group_key], list):
                    image_urls = groups[first_group_key]
                    # print(f"    Found image URLs under 'groups' -> '{first_group_key}'.")
                else:
                     print(f"    [Warning] Structure under 'groups' unexpected. No image list found.")
            elif isinstance(groups, list): # Directly a list of URLs under 'groups'?
                 image_urls = groups
                 print("    Found image URLs directly under 'groups' key (as a list).")


            if not image_urls:
                print(f"    [Warning] No image URLs found for Chapter {chapter_key}. Skipping PDF creation.")
                failed_count += 1
                print("-" * 20)
                continue # Skip to cleanup and next chapter

            # Download images for the chapter in parallel
            downloaded_image_paths = download_images_parallel(image_urls, temp_dir, args.workers)

            # Create PDF from successfully downloaded images
            if downloaded_image_paths:
                if create_pdf_from_images(downloaded_image_paths, pdf_filepath):
                    created_count += 1
                else:
                    failed_count +=1 # PDF creation failed
            else:
                 print(f"    [Skipped PDF] No images were successfully downloaded for Chapter {chapter_key}.")
                 failed_count += 1

        except Exception as e:
             print(f"    [Error] Unhandled exception during processing chapter {chapter_key}: {e}")
             failed_count += 1
        finally:
            # Clean up temporary directory if it was created
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                    # print(f"    Removed temporary directory: {temp_dir}") # Optional
                except OSError as e:
                    print(f"    [Error] Could not remove temporary directory {temp_dir}: {e}")
            print("-" * 20) # Separator for next chapter


    end_time = time.time()
    total_time = end_time - start_time
    print("\nScript finished.")
    print("-" * 30)
    print(f"Total chapters in JSON: {total_chapters}")
    print(f"Attempted processing: {processed_count}")
    print(f"Successfully created PDFs: {created_count}")
    print(f"Skipped existing PDFs: {skipped_count}")
    print(f"Failed/Skipped due to errors/no images: {failed_count}")
    print(f"Total execution time: {total_time:.2f} seconds")
    print("-" * 30)
