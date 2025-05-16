import streamlit as st
import openai
import base64
from PIL import Image, ImageDraw, ImageFont
import cv2
import numpy as np
import os
import json
import re
import io
from typing import List, Tuple, Dict, Optional
import tempfile

class AdvancedMangaTranslator:
    def __init__(self, openrouter_api_key: str, model: str = "opengvlab/internvl3-14b:free"):
        """
        Initialize the Advanced Manga Translator with OpenRouter API key

        Args:
            openrouter_api_key: Your OpenRouter API key
            model: Vision model to use for OCR/translation
        """
        self.client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_api_key,
        )

        self.model = model

    def encode_image(self, image_path: str) -> str:
        """Encode image to base64 for API"""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def extract_manga_text_and_translate(self,
                                       image_path: str,
                                       source_language: str = "Japanese",
                                       target_language: str = "English",
                                       include_coordinates: bool = True) -> Dict:
        """
        Extract text from manga and translate to target language

        Args:
            image_path: Path to manga image
            source_language: Source language of the text (default: Japanese)
            target_language: Target language for translation (default: English)
            include_coordinates: Whether to include text coordinates in output

        Returns:
            Dictionary with detected text, translations, and coordinates
        """
        base64_image = self.encode_image(image_path)

        # Simplified and clearer prompt
        prompt = f"""
        You are a manga translator and OCR expert. Your task is simple:

        1. **Find all {source_language} text** in this manga page
        2. **Get the exact location** of each text (very important for overlay placement)
        3. **Translate to {target_language}** naturally

        For each piece of text you find, I need:
        - The original {source_language} text
        - Natural {target_language} translation
        - Exact coordinates where the text appears (normalized 0.0 to 1.0)
        - Type of text (speech bubble, thought bubble, narration, etc.)

        **Coordinate format**: Give x1,y1 (top-left) and x2,y2 (bottom-right) of the text area only (not the bubble border). Be very precise with coordinates.

        Return your response as JSON in this exact format:

        {{
            "translations": [
                {{
                    "original_text": "exact {source_language} text",
                    "translation": "natural {target_language} translation",
                    "type": "speech/thought/narration/sign/sound_effect",
                    "coordinates": {{
                        "x1": 0.123,
                        "y1": 0.456,
                        "x2": 0.789,
                        "y2": 0.654
                    }}
                }}
            ]
        }}

        Focus on accuracy of both translation and coordinates. The coordinates must be precise for proper text overlay.
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )

            result_text = response.choices[0].message.content

            # Extract and parse JSON response
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                result = json.loads(json_str)
            else:
                # Fallback parsing
                result = {
                    "translations": [
                        {
                            "original_text": "Failed to parse JSON",
                            "translation": result_text[:500],
                            "type": "unknown",
                            "coordinates": {"x1": 0.1, "y1": 0.1, "x2": 0.9, "y2": 0.9}
                        }
                    ]
                }

            return result

        except Exception as e:
            return {"error": str(e), "model_used": self.model}

def draw_text_overlay(image: Image.Image, translation_result: Dict) -> Image.Image:
    """
    Draw simple translated text overlays that align with original text positions

    Args:
        image: PIL Image object
        translation_result: Result from the translator

    Returns:
        PIL Image with text overlays
    """
    # Create a copy to work on, ensure RGBA mode for compositing
    if image.mode != 'RGBA':
        img_copy = image.convert('RGBA')
    else:
        img_copy = image.copy()

    # Get image dimensions
    img_width, img_height = img_copy.size

    # Load font - try to get a clean, readable font
    try:
        # Try common font paths for different operating systems
        font_paths = [
            "/System/Library/Fonts/Arial.ttf",  # macOS
            "/System/Library/Fonts/Helvetica.ttc",  # macOS
            "C:/Windows/Fonts/arial.ttf",  # Windows
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",  # Linux
            "/usr/share/fonts/TTF/arial.ttf"  # Some Linux distributions
        ]

        # Calculate appropriate font size based on image size
        base_font_size = max(14, min(img_width, img_height) // 40)
        font = None

        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, base_font_size)
                    break
                except:
                    continue

        if not font:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # Create overlay for text backgrounds
    bg_overlay = Image.new('RGBA', img_copy.size, (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg_overlay)

    # Create another overlay for text
    text_overlay = Image.new('RGBA', img_copy.size, (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_overlay)

    if "translations" in translation_result:
        print(f"Processing {len(translation_result['translations'])} translations")

        for idx, translation in enumerate(translation_result["translations"]):
            print(f"\nTranslation {idx + 1}:")
            print(f"  Original: {translation.get('original_text', 'N/A')}")
            print(f"  Translation: {translation.get('translation', 'N/A')}")
            print(f"  Coordinates: {translation.get('coordinates', 'N/A')}")

            if "coordinates" not in translation:
                print(f"  Skipping - no coordinates")
                continue

            coords = translation["coordinates"]

            # Validate coordinates
            required_keys = ['x1', 'y1', 'x2', 'y2']
            if not all(key in coords for key in required_keys):
                print(f"  Skipping - missing coordinate keys")
                continue

            # Convert normalized coordinates to pixel coordinates
            x1 = max(0, min(img_width, int(coords["x1"] * img_width)))
            y1 = max(0, min(img_height, int(coords["y1"] * img_height)))
            x2 = max(0, min(img_width, int(coords["x2"] * img_width)))
            y2 = max(0, min(img_height, int(coords["y2"] * img_height)))

            # Ensure valid rectangle
            if x2 <= x1 or y2 <= y1:
                print(f"  Skipping - invalid rectangle: ({x1},{y1}) to ({x2},{y2})")
                continue

            # Get translation text
            text = translation.get("translation", "").strip()
            if not text:
                print(f"  Skipping - no translation text")
                continue

            print(f"  Drawing at: ({x1},{y1}) to ({x2},{y2})")

            # Calculate available space
            available_width = x2 - x1
            available_height = y2 - y1

            # Ensure minimum size
            if available_width < 20 or available_height < 10:
                print(f"  Skipping - area too small: {available_width}x{available_height}")
                continue

            # Word wrap the text to fit in the available space
            words = text.split()
            lines = []
            current_line = ""

            for word in words:
                test_line = current_line + word + " " if current_line else word + " "
                bbox = text_draw.textbbox((0, 0), test_line, font=font)
                test_width = bbox[2] - bbox[0]

                if test_width <= available_width - 8 or not current_line:  # Leave some padding
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line.strip())
                    current_line = word + " "

            if current_line:
                lines.append(current_line.strip())

            if not lines:
                print(f"  Skipping - no lines after wrapping")
                continue

            # Calculate total text height
            line_height = text_draw.textbbox((0, 0), "Ayg")[3] - text_draw.textbbox((0, 0), "Ayg")[1]
            total_text_height = len(lines) * line_height

            # Position text in the center of the available area
            start_y = y1 + max(0, (available_height - total_text_height) // 2)

            # Draw semi-transparent white background for the entire text block
            padding = 4
            bg_x1 = x1 + padding
            bg_y1 = start_y - padding
            bg_x2 = x2 - padding
            bg_y2 = start_y + total_text_height + padding

            # Ensure background doesn't go outside bounds
            bg_x1 = max(0, bg_x1)
            bg_y1 = max(0, bg_y1)
            bg_x2 = min(img_width, bg_x2)
            bg_y2 = min(img_height, bg_y2)

            bg_draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2],
                             fill=(255, 255, 255, 200))

            # Draw each line of text
            for i, line in enumerate(lines):
                # Calculate line position
                line_bbox = text_draw.textbbox((0, 0), line, font=font)
                line_width = line_bbox[2] - line_bbox[0]
                line_x = x1 + (available_width - line_width) // 2
                current_y = start_y + i * line_height

                # Ensure text doesn't go outside image bounds
                line_x = max(0, min(img_width - line_width, line_x))
                current_y = max(0, min(img_height - line_height, current_y))

                # Draw the text in solid black
                text_draw.text((line_x, current_y), line,
                             font=font, fill=(0, 0, 0, 255))

                print(f"    Line {i+1}: '{line}' at ({line_x}, {current_y})")

    # Composite all layers
    img_copy = Image.alpha_composite(img_copy, bg_overlay)
    img_copy = Image.alpha_composite(img_copy, text_overlay)

    # Convert back to RGB if needed
    if image.mode != 'RGBA':
        img_copy = img_copy.convert('RGB')

    return img_copy

def main():
    st.set_page_config(
        page_title="Manga Translator",
        page_icon="📖",
        layout="wide"
    )

    st.title("🌸 Advanced Manga Translator 📖")
    st.write("Upload a manga page to automatically detect and translate Japanese text!")

    # Sidebar for settings
    with st.sidebar:
        st.header("⚙️ Settings")

        # Get API key from environment or show current status
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if api_key:
            st.success("✅ API key loaded from environment")
        else:
            st.warning("⚠️ No API key found in environment")
            st.info("Set OPENROUTER_API_KEY environment variable")
            # Fallback option to input API key manually
            api_key = st.text_input(
                "OpenRouter API Key (optional)",
                type="password",
                help="Enter your OpenRouter API key if not set in environment"
            )

        # Model selection
        st.header("🤖 AI Model")
        model_options = {
            "InternVL3 14B (Free)": "opengvlab/internvl3-14b:free",
            "Qwen2.5 VL 72B (Free)": "qwen/qwen2.5-vl-72b-instruct:free",
            "Gemma 3 12B (Free)": "google/gemma-3-12b-it:free",
            "Custom Model": "custom"
        }

        selected_model_name = st.selectbox(
            "Select Vision Model",
            options=list(model_options.keys()),
            index=0,
            help="Choose the AI model for text detection and translation"
        )

        if selected_model_name == "Custom Model":
            model_name = st.text_input(
                "Enter custom model name",
                placeholder="e.g., meta-llama/llama-3.2-90b-vision-instruct",
                help="Enter any OpenRouter model identifier"
            )
        else:
            model_name = model_options[selected_model_name]

        # Display model info
        if "free" in model_name.lower():
            st.success("✅ Free model selected")
        else:
            st.warning("💰 Paid model - check OpenRouter pricing")

        # Source language selection
        source_language = st.selectbox(
            "Source Language (in manga)",
            ["Japanese", "Chinese", "Korean", "Thai", "Vietnamese"],
            index=0,
            help="Select the language of text in the manga"
        )

        # Target language selection
        target_language = st.selectbox(
            "Target Language",
            ["English", "Spanish", "French", "German", "Portuguese", "Italian"],
            index=0
        )

        # Display options
        st.header("🎨 Display Options")
        show_original = st.checkbox("Show original text", value=True)

    # Main content area
    st.header("📤 Upload Manga Page")
    uploaded_file = st.file_uploader(
        "Choose a manga image",
        type=["jpg", "jpeg", "png", "webp"],
        help="Upload a manga page image to translate"
    )

    if uploaded_file is not None:
        # Display uploaded image
        image = Image.open(uploaded_file)
        st.image(image, caption="Original Manga Page", use_container_width=True)

        # Translation button
        if st.button("🚀 Translate", disabled=not api_key):
            if not api_key:
                st.error("Please set OPENROUTER_API_KEY environment variable or enter API key in sidebar!")
            else:
                with st.spinner("Analyzing and translating manga..."):
                    # Save uploaded file temporarily
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
                        image.save(tmp_file.name)

                        # Initialize translator with selected model
                        translator = AdvancedMangaTranslator(api_key, model=model_name)

                        # Translate
                        result = translator.extract_manga_text_and_translate(
                            tmp_file.name,
                            source_language=source_language,
                            target_language=target_language,
                            include_coordinates=True
                        )

                        # Clean up temp file
                        os.unlink(tmp_file.name)

                        # Store result in session state
                        st.session_state.translation_result = result
                        st.session_state.original_image = image

        # Show translation results below original image
        if hasattr(st.session_state, 'translation_result'):
            result = st.session_state.translation_result

            st.header("📋 Translation Results")

            if "error" in result:
                st.error(f"Translation failed: {result['error']}")
            else:
                # Draw overlays on image
                annotated_image = draw_text_overlay(st.session_state.original_image, result)
                st.image(annotated_image, caption="Translated Manga with Overlays", use_container_width=True)

                # Display translation details
                st.subheader("📝 Detailed Translations")

                if "translations" in result:
                    for i, translation in enumerate(result["translations"]):
                        with st.expander(f"Text {i+1} - {translation.get('type', 'unknown').title()}"):
                            col_a, col_b = st.columns(2)

                            with col_a:
                                if show_original:
                                    st.write("**Original:**")
                                    st.write(translation.get("original_text", "N/A"))

                            with col_b:
                                st.write("**Translation:**")
                                st.write(translation.get("translation", "N/A"))

                # Download button for annotated image
                img_buffer = io.BytesIO()
                annotated_image.save(img_buffer, format='PNG')
                img_buffer.seek(0)

                st.download_button(
                    label="💾 Download Translated Image",
                    data=img_buffer.getvalue(),
                    file_name="translated_manga.png",
                    mime="image/png"
                )

                # Download translation data as JSON
                json_data = json.dumps(result, indent=2, ensure_ascii=False)
                st.download_button(
                    label="💾 Download Translation Data (JSON)",
                    data=json_data,
                    file_name="translation_data.json",
                    mime="application/json"
                )

    # Instructions
    with st.expander("ℹ️ How to Use"):
        st.markdown("""
        ### Instructions:
        1. **Get an API Key**: Sign up at [OpenRouter](https://openrouter.ai/) and get your free API key
        2. **Enter API Key**: Paste your API key in the sidebar
        3. **Upload Image**: Choose a manga page image (JPG, PNG, WebP)
        4. **Select Language**: Choose your target translation language
        5. **Translate**: Click the translate button to process the image
        6. **Review Results**: View the annotated image and detailed translations
        7. **Download**: Save the translated image or translation data

        ### Features:
        - 🔍 Advanced OCR using state-of-the-art vision models
        - 🌐 Multiple target languages supported
        - 📍 Precise text localization with clean overlays
        - 💾 Export translated images and data

        ### Tips:
        - Upload high-quality, clear manga images for best results
        - The model works best with standard manga layouts
        - Review translations for context and accuracy
        """)

if __name__ == "__main__":
    main()
