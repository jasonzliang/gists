#!/usr/bin/env python

"""
Japanese Image OCR and Translation Tool - CPU Version

This script processes a directory of images containing Japanese text:
1. Uses PaddleOCR to extract Japanese text from images (CPU only)
2. Translates the text to English using Google Translate API
3. Outputs two text files:
   - japanese_output.txt: Original Japanese text from each image
   - english_output.txt: Translated English text from each image

Requirements:
- PaddlePaddle (CPU version)
- PaddleOCR
- Google Cloud Translate API
- PIL (Python Imaging Library)

Usage:
    python translate_images.py --image_dir /path/to/image/directory --google_credentials /path/to/google_credentials.json
"""

import os
import argparse
from pathlib import Path
from typing import Dict, List, Tuple
import logging
import time
import gc

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import PaddleOCR
try:
    from paddleocr import PaddleOCR
except ImportError:
    logger.error("PaddleOCR not found. Installing...")
    os.system("pip install paddleocr")
    from paddleocr import PaddleOCR

# Import Google Translate
try:
    from google.cloud import translate_v2 as translate
except ImportError:
    logger.error("Google Cloud Translate not found. Installing...")
    os.system("pip install google-cloud-translate==2.0.1")
    from google.cloud import translate_v2 as translate

# Import PIL for image validation
try:
    from PIL import Image
except ImportError:
    logger.error("PIL not found. Installing...")
    os.system("pip install pillow")
    from PIL import Image


def is_valid_image(file_path: str) -> bool:
    """Check if the file is a valid image."""
    try:
        img = Image.open(file_path)
        img.verify()  # Verify it's an image
        return True
    except Exception:
        return False


def get_image_files(directory: str) -> List[str]:
    """Get all valid image files from a directory."""
    image_extensions = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp', '.gif']
    image_files = []

    try:
        for file in os.listdir(directory):
            file_path = os.path.join(directory, file)
            if os.path.isfile(file_path) and os.path.splitext(file)[1].lower() in image_extensions:
                try:
                    if is_valid_image(file_path):
                        image_files.append(file_path)
                    else:
                        logger.warning(f"File has image extension but is not a valid image: {file}")
                except Exception as e:
                    logger.error(f"Error validating image {file}: {e}")
    except Exception as e:
        logger.error(f"Error accessing directory {directory}: {e}")

    # Sort files alphabetically for consistent processing
    return sorted(image_files)


def extract_text_from_image(ocr, image_path: str) -> str:
    """Extract text from an image using PaddleOCR."""
    try:
        # Set lower threshold to detect more text
        result = ocr.ocr(image_path, cls=True)

        # Check if result is empty
        if not result or len(result) == 0:
            logger.warning(f"No text detected in {image_path}")
            return ""

        # Flatten the result and extract text
        text_lines = []
        for idx in range(len(result)):
            res = result[idx]
            if not res:  # Skip empty results
                continue

            for line in res:
                if isinstance(line, list) and len(line) >= 2:
                    text = line[1][0]  # Get the text part
                    confidence = line[1][1]  # Get confidence score

                    # Only add text with confidence above threshold
                    if text and text.strip() and confidence > 0.5:
                        text_lines.append(text)

        # Clean up memory explicitly
        del result

        return "\n".join(text_lines)
    except Exception as e:
        logger.error(f"Error extracting text from {image_path}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return ""


def translate_text(translate_client, text: str, target_language: str = 'en', source_language: str = 'ja') -> str:
    """Translate text using Google Translate API."""
    if not text.strip():
        return ""

    try:
        # Explicitly set source language to Japanese
        result = translate_client.translate(
            text,
            target_language=target_language,
            source_language=source_language
        )
        return result['translatedText']
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return f"[Translation error: {e}]"

def preprocess_image(image_path, max_size=1600):
    """
    Preprocess image to reduce its size if it's too large.
    Returns the path to the preprocessed image.
    """
    try:
        from PIL import Image
        import numpy as np
        import os

        # Create a temporary directory if it doesn't exist
        temp_dir = os.path.join(os.path.dirname(image_path), 'temp')
        os.makedirs(temp_dir, exist_ok=True)

        # Check if we need to preprocess
        img = Image.open(image_path)
        w, h = img.size

        # If image is small enough, return original path
        if max(w, h) <= max_size:
            return image_path

        # Calculate new dimensions
        if w > h:
            new_w = max_size
            new_h = int(h * (max_size / w))
        else:
            new_h = max_size
            new_w = int(w * (max_size / h))

        # Resize the image
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Save to temporary file
        temp_path = os.path.join(temp_dir, os.path.basename(image_path))
        img.save(temp_path)

        logger.info(f"Resized image from {w}x{h} to {new_w}x{new_h}")
        return temp_path

    except Exception as e:
        logger.warning(f"Error preprocessing image {image_path}: {e}. Using original image.")
        return image_path

def process_images(args) -> Dict[str, Tuple[str, str]]:
    """Process all images in the directory."""
    # Set environment variables to ensure CPU-only operation and proper configuration
    os.environ['CUDA_VISIBLE_DEVICES'] = '-1'  # Force CPU mode
    os.environ['FLAGS_use_cuda'] = '0'  # Disable CUDA
    os.environ['FLAGS_selected_gpus'] = '-1'  # No GPU selection
    os.environ['FLAGS_fraction_of_gpu_memory_to_use'] = '0'  # No GPU memory
    os.environ['FLAGS_eager_delete_tensor_gb'] = '0.0'  # Help with memory management
    os.environ['FLAGS_allocator_strategy'] = 'naive_best_fit'  # Use simpler allocator

    # Try to enable the Paddle CPU backend properly
    os.environ['FLAGS_use_mkldnn'] = '0'  # Disable MKL-DNN since it's causing issues
    os.environ['FLAGS_paddle_num_threads'] = str(args.cpu_threads)  # Set number of threads

    # Initialize OCR with Japanese language model, explicitly using CPU mode
    logger.info("Initializing PaddleOCR with Japanese language model in CPU-only mode...")
    ocr = PaddleOCR(
        use_angle_cls=True,
        lang='japan',
        use_gpu=False,  # Explicitly set to use CPU
        # Remove mkldnn-related parameters that are causing errors
        cpu_threads=args.cpu_threads,  # Control CPU threads
        rec_batch_num=1,  # Process images one by one
        max_text_length=50,  # Limit max text length to reduce memory usage
        det_db_box_thresh=args.confidence_threshold,  # Detection threshold
        use_space_char=True,  # Important for Japanese text
    )

    # Initialize Google Translate
    logger.info("Initializing Google Translate API...")
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = args.google_credentials
    translate_client = translate.Client()

    # Get all image files
    logger.info(f"Scanning directory: {args.image_dir}")
    image_files = get_image_files(args.image_dir)
    if not image_files:
        logger.warning(f"No valid image files found in {args.image_dir}")
        return {}

    logger.info(f"Found {len(image_files)} valid image files")

    results = {}
    total = len(image_files)
    batch_size = min(args.batch_size, 5)  # Limit batch size to avoid memory issues

    # Process images in smaller batches to manage memory
    for i in range(0, len(image_files), batch_size):
        batch_files = image_files[i:i+batch_size]
        logger.info(f"Processing batch {i//batch_size + 1}/{(len(image_files)-1)//batch_size + 1} ({len(batch_files)} images)")

        batch_results = {}
        for j, image_path in enumerate(batch_files, 1):
            image_name = os.path.basename(image_path)
            logger.info(f"Processing image {i + j}/{total}: {image_name}")

            try:
                # Preprocess image to reduce size if necessary
                preprocessed_path = preprocess_image(image_path, args.max_image_size)

                # Extract Japanese text
                japanese_text = extract_text_from_image(ocr, preprocessed_path)

                # Clean up preprocessed image if it's different from original
                if preprocessed_path != image_path and os.path.exists(preprocessed_path):
                    try:
                        os.remove(preprocessed_path)
                    except Exception:
                        pass

                # Translate to English if we got text
                english_text = ""
                if japanese_text:
                    logger.info(f"Translating text from {image_name}")
                    english_text = translate_text(translate_client, japanese_text, target_language='en', source_language='ja')
                else:
                    logger.warning(f"No text extracted from {image_name}")

                # Store results
                batch_results[image_name] = (japanese_text, english_text)

                # Add to overall results
                results[image_name] = (japanese_text, english_text)

            except Exception as e:
                logger.error(f"Error processing {image_name}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                # Continue with next image
                continue

            # Force garbage collection after each image to free memory
            gc.collect()

        # Force garbage collection after each batch
        gc.collect()

        # Write intermediate results to avoid losing progress if process crashes
        if batch_results:
            write_output_files(batch_results, args.output_dir, args.image_dir)

    return results


def write_output_files(results: Dict[str, Tuple[str, str]], output_dir: str, image_dir: str):
    """Write the results to output files."""
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Use the input directory name for the output files
    dir_name = os.path.basename(os.path.normpath(image_dir))
    japanese_output_path = os.path.join(output_dir, f"{dir_name}_japanese.txt")
    english_output_path = os.path.join(output_dir, f"{dir_name}_english.txt")

    # Add timestamp to help differentiate between runs
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Write Japanese output (append mode)
    with open(japanese_output_path, 'a', encoding='utf-8') as jp_file:
        jp_file.write(f"\n\n===== RUN: {timestamp} =====\n\n")
        if not results:
            jp_file.write("No results to write.\n")
        else:
            for image_name, (japanese_text, _) in results.items():
                jp_file.write(f"===== {image_name} =====\n")
                jp_file.write(japanese_text if japanese_text else "[No text detected]" + "\n\n")

    # Write English output (append mode)
    with open(english_output_path, 'a', encoding='utf-8') as en_file:
        en_file.write(f"\n\n===== RUN: {timestamp} =====\n\n")
        if not results:
            en_file.write("No results to write.\n")
        else:
            for image_name, (_, english_text) in results.items():
                en_file.write(f"===== {image_name} =====\n")
                en_file.write(english_text if english_text else "[No translation available]" + "\n\n")

    logger.info(f"Output appended to {japanese_output_path} and {english_output_path}")


def main():
    """Main function to run the script."""
    parser = argparse.ArgumentParser(description="Process images with Japanese text using PaddleOCR and translate to English")
    parser.add_argument("--image_dir", required=True, help="Directory containing images to process")
    parser.add_argument("--google_credentials", required=True, help="Path to Google Cloud credentials JSON file")
    parser.add_argument("--output_dir", default="output", help="Directory for output files (default: 'output')")
    parser.add_argument("--batch_size", type=int, default=3, help="Number of images to process before writing results (default: 3)")
    parser.add_argument("--max_image_size", type=int, default=1200, help="Maximum dimension for image preprocessing (default: 1200)")
    parser.add_argument("--cpu_threads", type=int, default=2, help="Number of CPU threads to use (default: 2)")
    parser.add_argument("--confidence_threshold", type=float, default=0.5, help="Confidence threshold for text detection (default: 0.5)")
    args = parser.parse_args()

    # Check if the image directory exists
    if not os.path.isdir(args.image_dir):
        logger.error(f"Image directory not found: {args.image_dir}")
        return

    # Check if the credentials file exists
    if not os.path.isfile(args.google_credentials):
        logger.error(f"Google credentials file not found: {args.google_credentials}")
        return

    # Verify we can access the credentials file
    try:
        with open(args.google_credentials, 'r') as f:
            # Just checking if we can read it
            pass
    except Exception as e:
        logger.error(f"Error reading Google credentials file: {e}")
        return

    # Configure logging to file as well
    log_dir = os.path.join(args.output_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"ocr_translation_{time.strftime('%Y%m%d_%H%M%S')}.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)

    try:
        # Ensure CPU-only operation before any PaddlePaddle imports
        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
        os.environ['FLAGS_use_cuda'] = '0'
        os.environ['FLAGS_selected_gpus'] = '-1'
        os.environ['FLAGS_use_mkldnn'] = '0'  # Disable MKLDNN since it's causing compatibility issues
        os.environ['FLAGS_allocator_strategy'] = 'naive_best_fit'

        # Process the images
        logger.info("Starting image processing in CPU-only mode...")
        logger.info(f"Using batch size: {args.batch_size}, CPU threads: {args.cpu_threads}, Max image size: {args.max_image_size}")

        results = process_images(args)

        # Write the output files
        if results:
            logger.info(f"Successfully processed {len(results)} images")
        else:
            logger.warning("No results were produced. Check the logs for details.")

        logger.info("Processing complete!")

    except Exception as e:
        logger.error(f"An error occurred during processing: {e}")
        import traceback
        logger.error(traceback.format_exc())

    finally:
        # Clean up temporary files
        temp_dir = os.path.join(args.image_dir, 'temp')
        if os.path.exists(temp_dir):
            try:
                import shutil
                shutil.rmtree(temp_dir)
                logger.info(f"Removed temporary directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to remove temporary directory: {e}")

        # Force garbage collection
        gc.collect()


if __name__ == "__main__":
    main()