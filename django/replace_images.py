#!/usr/bin/env python
import os
import shutil
import random
from pathlib import Path
import urllib.request
import time
import json

# Configuration
project_dir = "."
real_images_dir = "real_images"
static_dir = "./rome_ai_app/static"
size_threshold = 10240  # 10 KB (changed from 1KB)
img_whitelist = ['.jpg', '.jpeg', '.png', '.gif']
clean_up = True

# Get Pixabay API key from environment variable
PIXABAY_API_KEY = os.environ.get('PIXABAY_API_KEY')
if not PIXABAY_API_KEY:
    print("Warning: PIXABAY_API_KEY environment variable not set.")
    print("Set it with: export PIXABAY_API_KEY='your_key_here'")
    print("You can get a free API key by signing up at: https://pixabay.com/api/docs/")

# Create directory for real images
os.makedirs(real_images_dir, exist_ok=True)

# Function to generate search query from filename
def generate_search_query(filename):
    # Remove file extension
    base_name = os.path.splitext(filename)[0]

    # Replace underscores, hyphens with spaces
    query = base_name.replace('_', ' ').replace('-', ' ')

    # Add "image" to the query for better results
    query = query + " image"

    return query

# Function to download a sample image for a filename
def download_sample_image(filename, image_type=None):
    # Generate search query from filename
    query = generate_search_query(filename)

    # Determine file extension
    ext = os.path.splitext(filename)[1].lower()
    target_path = os.path.join(real_images_dir, filename)

    if not os.path.exists(target_path):
        print(f"Downloading {filename} with query: {query}...")
        try:
            # For SVG files, create a simple SVG
            if ext == ".svg":
                with open(target_path, 'w') as f:
                    # Different SVG for logo-white
                    if "white" in filename.lower():
                        f.write('<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" width="200" height="80" viewBox="0 0 200 80"><rect width="200" height="80" fill="#333"/><text x="100" y="50" font-family="Arial" font-size="24" text-anchor="middle" fill="#fff">Rome AI</text></svg>')
                    else:
                        f.write('<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" width="200" height="80" viewBox="0 0 200 80"><rect width="200" height="80" fill="#4a6bff"/><text x="100" y="50" font-family="Arial" font-size="24" text-anchor="middle" fill="#fff">Rome AI</text></svg>')
            else:
                # Try Pixabay if API key is available
                if PIXABAY_API_KEY:
                    # Properly format the Pixabay API URL with query parameters
                    encoded_query = urllib.parse.quote(query)
                    pixabay_url = f"https://pixabay.com/api/?key={PIXABAY_API_KEY}&q={encoded_query}&image_type=photo&per_page=10"

                    # Add headers to mimic a browser request
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    }

                    req = urllib.request.Request(pixabay_url, headers=headers)

                    with urllib.request.urlopen(req) as response:
                        data = json.loads(response.read().decode())
                        if data.get('hits') and len(data['hits']) > 0:
                            # Get a random image from the results (instead of always the first)
                            random_image = random.choice(data['hits'])
                            image_url = random_image['webformatURL']

                            # Download the actual image
                            image_req = urllib.request.Request(image_url, headers=headers)
                            with urllib.request.urlopen(image_req) as img_response:
                                with open(target_path, 'wb') as f:
                                    f.write(img_response.read())
                            return target_path

                # Fallback to a simple colored rectangle if Pixabay fails or no API key
                try:
                    from PIL import Image, ImageDraw, ImageFont

                    # Create a basic image with query text
                    colors = ['4a6bff', '6b4aff', 'ff4a6b', '4aff6b', 'ff6b4a']
                    bg_color = tuple(int(random.choice(colors)[i:i+2], 16) for i in (0, 2, 4))

                    # Create a simple colored image
                    img = Image.new('RGB', (800, 600), color=bg_color)
                    d = ImageDraw.Draw(img)

                    # Add text
                    display_text = query

                    # Try to get a font
                    try:
                        font = ImageFont.truetype("arial.ttf", 36)
                    except IOError:
                        try:
                            # Try a font that should be available on most systems
                            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
                        except IOError:
                            # Fall back to default if no font is available
                            font = ImageFont.load_default()

                    # Calculate text position (center)
                    text_width = d.textlength(display_text, font=font) if hasattr(d, 'textlength') else 400
                    text_position = ((800 - text_width) // 2, 300)

                    # Draw text
                    d.text(text_position, display_text, fill=(255, 255, 255), font=font)

                    # Save the image
                    img.save(target_path)
                    print(f"Created placeholder image for {filename}")
                    return target_path

                except Exception as pil_error:
                    print(f"Failed to create image with PIL: {str(pil_error)}")

                    # Ultra fallback: create a tiny colored image
                    with open(target_path, 'wb') as f:
                        # Create a 10x10 colored square (minimal valid image)
                        color = random.randint(0, 255)
                        img_data = bytes([
                            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
                            0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR chunk
                            0x00, 0x00, 0x00, 0x0A, 0x00, 0x00, 0x00, 0x0A,  # width=10, height=10
                            0x08, 0x02, 0x00, 0x00, 0x00, 0x02, 0x50, 0x58,  # bit depth, color type, etc.
                            0x4C, 0xF8, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44,  # IDAT chunk
                            0x41, 0x54, 0x78, 0x01, 0x63, 0x60, 0x60, color, color,  # compressed data
                            color, color, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0x03,  # more data
                            0x00, 0x05, 0x00, 0x01, 0x5F, 0xF0, 0xCF, 0x6D,  # end of data
                            0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44,  # IEND chunk
                            0xAE, 0x42, 0x60, 0x82                            # CRC
                        ])
                        f.write(img_data)
                    return target_path

        except Exception as e:
            print(f"Error downloading {filename}: {str(e)}")
            return None

    return target_path

# Function to determine image type based on context
def determine_image_type(path):
    # Get parent directory and grandparent directory names
    parent_dir = os.path.basename(os.path.dirname(path))
    grandparent_dir = os.path.basename(os.path.dirname(os.path.dirname(path)))

    # Combine with filename for context
    filename = os.path.basename(path)
    context = f"{grandparent_dir}_{parent_dir}_{filename}".lower()

    # Check for common types
    if "logo" in context:
        return "logo"
    elif "hero" in context or "banner" in context:
        return "hero"
    elif "partner" in context or "sponsor" in context:
        return "partner"
    elif "research" in context or "project" in context:
        return "research"
    elif "about" in context or "team" in context:
        return "about"
    elif "twitter" in context or "social" in context:
        return "twitter"
    elif "og" in context or "open-graph" in context:
        return "og-image"
    elif "favicon" in context or "icon" in context:
        return "favicon"
    else:
        return "general"

# Find all image files under the size threshold (likely placeholders)
print("Finding placeholder images (less than 10 KB)...")
placeholder_images = []

for root, dirs, files in os.walk(static_dir):
    for file in files:
        file_path = os.path.join(root, file)
        ext = os.path.splitext(file)[1].lower()
        # Check if it's an image file and under size threshold
        if ext in img_whitelist and os.path.getsize(file_path) < size_threshold:
            placeholder_images.append(file_path)
            print(f"Found placeholder: {file_path}")

print(f"Found {len(placeholder_images)} placeholder images.")

# For each placeholder image, download or create a replacement
for img_path in placeholder_images:
    filename = os.path.basename(img_path)

    # Determine image type based on context
    image_type = determine_image_type(img_path)

    # Get a replacement image
    replacement = download_sample_image(filename, image_type)

    if replacement and os.path.exists(replacement):
        # Replace the placeholder
        print(f"Replacing {img_path} with {replacement}")
        shutil.copy2(replacement, img_path)

if clean_up: os.system("rm -rf %s" % real_images_dir)
print("Image replacement complete!")
