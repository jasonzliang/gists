import streamlit as st
import openai
import base64
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import os
import json
import re
import io
from typing import Dict, List, Tuple
import tempfile
import textwrap

class AdvancedMangaTranslator:
    def __init__(self, openrouter_api_key: str, model: str = "opengvlab/internvl3-14b:free"):
        """Initialize the Advanced Manga Translator with OpenRouter API key"""
        self.client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_api_key,
        )
        self.model = model

    def encode_image(self, image_path: str) -> str:
        """Encode image to base64 for API"""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def extract_manga_text_and_translate(self, image_path: str, source_language: str = "Japanese",
                                       target_language: str = "English", include_coordinates: bool = True) -> Dict:
        """Extract text from manga and translate to target language"""
        base64_image = self.encode_image(image_path)

        prompt = f"""
        You are a manga translator and OCR expert. Your task is:

        1. **Find all {source_language} text** in this manga page
        2. **Get the exact location** of each text
        3. **Translate to {target_language}** naturally

        For each piece of text, provide:
        - The original {source_language} text
        - Natural {target_language} translation
        - Exact coordinates (normalized 0.0 to 1.0)
        - Type of text (speech bubble, thought bubble, narration, etc.)

        **Coordinate format**: Give x1,y1 (top-left) and x2,y2 (bottom-right) of the text area.

        Return JSON in this exact format:
        {{
            "translations": [
                {{
                    "original_text": "original {source_language} text",
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
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }],
                max_tokens=4000,
                temperature=0.1
            )

            result_text = response.choices[0].message.content
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)

            if json_match:
                return json.loads(json_match.group())
            else:
                # Fallback parsing
                return {
                    "translations": [{
                        "original_text": "Failed to parse JSON",
                        "translation": result_text[:500],
                        "type": "unknown",
                        "coordinates": {"x1": 0.1, "y1": 0.1, "x2": 0.9, "y2": 0.9}
                    }]
                }

        except Exception as e:
            return {"error": str(e), "model_used": self.model}

def get_optimal_font_size(text: str, box_width: int, box_height: int, max_font_size: int = 50) -> int:
    """Calculate optimal font size that fits in the given box"""
    # Start with estimated font size based on box dimensions
    font_size = min(box_width // max(1, len(text) // 3), box_height // 2, max_font_size)
    return max(8, font_size)  # Minimum font size of 8

def wrap_text_to_fit(text: str, max_width: int, font_size: int) -> List[str]:
    """Wrap text to fit within specified width"""
    # Estimate characters per line based on font size and width
    chars_per_line = max(1, max_width // (font_size * 0.6))
    return textwrap.wrap(text, width=int(chars_per_line))

def draw_text_overlay(image: Image.Image, translation_result: Dict) -> Image.Image:
    """Draw translated text overlay on the manga image"""
    if "translations" not in translation_result or not translation_result["translations"]:
        return image.copy()

    # Create a copy of the image to draw on
    annotated_image = image.copy()
    draw = ImageDraw.Draw(annotated_image)
    width, height = image.size

    # Try to load a font, fallback to default if not available
    try:
        # You can customize the font path here
        font_path = None  # Will use default font
        default_font = ImageFont.load_default()
    except:
        default_font = ImageFont.load_default()

    for translation in translation_result["translations"]:
        coords = translation.get("coordinates", {})
        translated_text = translation.get("translation", "")
        text_type = translation.get("type", "speech")

        if not coords or not translated_text:
            continue

        # Convert normalized coordinates to pixel coordinates
        x1 = int(coords.get("x1", 0) * width)
        y1 = int(coords.get("y1", 0) * height)
        x2 = int(coords.get("x2", 1) * width)
        y2 = int(coords.get("y2", 1) * height)

        # Calculate box dimensions
        box_width = abs(x2 - x1)
        box_height = abs(y2 - y1)

        if box_width <= 0 or box_height <= 0:
            continue

        # Get optimal font size
        font_size = get_optimal_font_size(translated_text, box_width, box_height)

        try:
            if font_path and os.path.exists(font_path):
                font = ImageFont.truetype(font_path, font_size)
            else:
                # Use default font with approximate scaling
                font = default_font
                # For default font, we can't set size, so we'll adjust text wrapping
        except:
            font = default_font

        # Wrap text to fit in the box
        wrapped_lines = wrap_text_to_fit(translated_text, box_width, font_size)

        # Calculate total text height
        line_height = font_size + 2
        total_text_height = len(wrapped_lines) * line_height

        # Choose colors based on text type
        if text_type in ["speech", "dialogue"]:
            bg_color = (255, 255, 255, 200)  # Semi-transparent white
            text_color = (0, 0, 0)  # Black text
            outline_color = (0, 0, 0)
        elif text_type in ["thought", "thinking"]:
            bg_color = (240, 240, 240, 180)  # Light gray
            text_color = (50, 50, 50)  # Dark gray text
            outline_color = (100, 100, 100)
        elif text_type in ["narration", "narrator"]:
            bg_color = (255, 255, 200, 160)  # Light yellow
            text_color = (50, 50, 50)
            outline_color = (100, 100, 50)
        elif text_type in ["sound_effect", "sfx"]:
            bg_color = (255, 200, 200, 140)  # Light red
            text_color = (80, 0, 0)  # Dark red
            outline_color = (120, 50, 50)
        else:
            bg_color = (255, 255, 255, 180)  # Default white
            text_color = (0, 0, 0)
            outline_color = (0, 0, 0)

        # Create a temporary image for the text with alpha channel
        text_img = Image.new('RGBA', (box_width, box_height), (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_img)

        # Draw background rectangle
        text_draw.rectangle([0, 0, box_width, box_height], fill=bg_color)

        # Calculate starting position to center text vertically
        start_y = max(0, (box_height - total_text_height) // 2)

        # Draw each line of text
        current_y = start_y
        for line in wrapped_lines:
            # Get text dimensions for centering horizontally
            bbox = text_draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            text_x = max(0, (box_width - text_width) // 2)

            # Draw text with outline for better readability
            outline_width = 1
            for dx in [-outline_width, 0, outline_width]:
                for dy in [-outline_width, 0, outline_width]:
                    if dx != 0 or dy != 0:
                        text_draw.text((text_x + dx, current_y + dy), line,
                                     font=font, fill=outline_color)

            # Draw main text
            text_draw.text((text_x, current_y), line, font=font, fill=text_color)
            current_y += line_height

        # Paste the text image onto the main image
        annotated_image.paste(text_img, (x1, y1), text_img)

    return annotated_image

def main():
    st.set_page_config(page_title="Manga Translator", page_icon="📖", layout="wide")
    st.title("🌸 Advanced Manga Translator 📖")
    st.write("Upload a manga page to automatically detect and translate Japanese text!")

    # Sidebar for settings
    with st.sidebar:
        st.header("⚙️ Settings")

        # API key configuration
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if api_key:
            st.success("✅ API key loaded from environment")
        else:
            st.warning("⚠️ No API key found in environment")
            api_key = st.text_input("OpenRouter API Key", type="password",
                                   help="Enter your OpenRouter API key")

        # Model selection
        st.header("🤖 AI Model")
        model_options = {
            "InternVL3 14B (Free)": "opengvlab/internvl3-14b:free",
            "Qwen2.5 VL 72B (Free)": "qwen/qwen2.5-vl-72b-instruct:free",
            "Gemma 3 12B (Free)": "google/gemma-3-12b-it:free",
            "Custom Model": "custom"
        }

        selected_model = st.selectbox("Select Vision Model", list(model_options.keys()))

        if selected_model == "Custom Model":
            model_name = st.text_input("Enter custom model name",
                                     placeholder="e.g., meta-llama/llama-3.2-90b-vision-instruct")
        else:
            model_name = model_options[selected_model]

        # Language settings
        source_language = st.selectbox("Source Language",
                                     ["Japanese", "Chinese", "Korean", "Thai", "Vietnamese"])
        target_language = st.selectbox("Target Language",
                                     ["English", "Spanish", "French", "German", "Portuguese", "Italian"])

        # Display options
        st.header("🎨 Display Options")
        show_original = st.checkbox("Show original text", value=True)

    # Main content area
    st.header("📤 Upload Manga Page")
    uploaded_file = st.file_uploader("Choose a manga image",
                                   type=["jpg", "jpeg", "png", "webp"])

    if uploaded_file is not None:
        image = Image.open(uploaded_file)
        st.image(image, caption="Original Manga Page", use_container_width=True)

        if st.button("🚀 Translate", disabled=not api_key):
            if not api_key:
                st.error("Please enter your OpenRouter API key!")
                return

            with st.spinner("Analyzing and translating manga..."):
                # Save uploaded file temporarily
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
                    image.save(tmp_file.name)

                    # Initialize translator
                    translator = AdvancedMangaTranslator(api_key, model=model_name)

                    # Translate
                    result = translator.extract_manga_text_and_translate(
                        tmp_file.name, source_language, target_language)

                    # Clean up
                    os.unlink(tmp_file.name)

                    # Store results
                    st.session_state.translation_result = result
                    st.session_state.original_image = image

        # Display results
        if hasattr(st.session_state, 'translation_result'):
            result = st.session_state.translation_result
            st.header("📋 Translation Results")

            if "error" in result:
                st.error(f"Translation failed: {result['error']}")
            else:
                # Draw overlays
                annotated_image = draw_text_overlay(st.session_state.original_image, result)
                st.image(annotated_image, caption="Translated Manga with Overlays",
                        use_container_width=True)

                # Display translation details
                st.subheader("📝 Detailed Translations")
                if "translations" in result:
                    for i, translation in enumerate(result["translations"]):
                        with st.expander(f"Text {i+1} - {translation.get('type', 'unknown').title()}"):
                            col1, col2 = st.columns(2)

                            with col1:
                                if show_original:
                                    st.write("**Original:**")
                                    st.write(translation.get("original_text", "N/A"))

                            with col2:
                                st.write("**Translation:**")
                                st.write(translation.get("translation", "N/A"))

                # Download buttons
                col1, col2 = st.columns(2)

                with col1:
                    img_buffer = io.BytesIO()
                    annotated_image.save(img_buffer, format='PNG')
                    st.download_button("💾 Download Translated Image",
                                     data=img_buffer.getvalue(),
                                     file_name="translated_manga.png",
                                     mime="image/png")

                with col2:
                    json_data = json.dumps(result, indent=2, ensure_ascii=False)
                    st.download_button("💾 Download Translation Data",
                                     data=json_data,
                                     file_name="translation_data.json",
                                     mime="application/json")

    # Instructions
    with st.expander("ℹ️ How to Use"):
        st.markdown("""
        ### Instructions:
        1. **Get API Key**: Sign up at [OpenRouter](https://openrouter.ai/) for free
        2. **Enter API Key**: Paste your key in the sidebar
        3. **Upload Image**: Choose a manga page (JPG, PNG, WebP)
        4. **Select Languages**: Choose source and target languages
        5. **Translate**: Click translate to process the image
        6. **Review**: View annotated image and detailed translations
        7. **Download**: Save translated image or JSON data

        ### Features:
        - 🔍 Advanced OCR with state-of-the-art vision models
        - 🌐 Multiple language support
        - 📍 Precise text localization with colored overlays
        - 💾 Export capabilities
        - 🎨 Type-specific color coding (speech, thought, narration, SFX)

        ### Tips:
        - Use high-quality, clear images for best results
        - Works best with standard manga layouts
        - Different text types get different colored backgrounds
        - Review translations for context accuracy
        """)

if __name__ == "__main__":
    main()