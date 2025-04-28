import streamlit as st
import sys
import os
from PIL import Image, ImageDraw
import torch
import platform
import psutil
import GPUtil
import types
import traceback
import numpy as np

import pillow_heif
import pillow_avif

# Register the HEIF file format plugin
pillow_heif.register_heif_opener()

# Fix for torch.classes error
dummy_module = types.ModuleType("dummy_classes")
dummy_module.__path__ = []
sys.modules["torch.classes"] = dummy_module

# Configure the app
st.set_page_config(
    page_title="Moondream2 Image Analysis",
    page_icon="🌙",
    layout="wide"
)

# Create custom module for vision fixes
os.makedirs("./moondream_custom", exist_ok=True)

# Write the fixed vision.py file
with open("./moondream_custom/vision.py", "w") as f:
    f.write("""
# Modified vision.py with device handling fix
import torch
import torch.nn as nn

def create_projection_layer(in_features, out_features, init_scale):
    return nn.Linear(in_features, out_features)

def vision_projection(global_features, reconstructed, vision_config, config):
    # Ensure both tensors are on the same device
    if global_features.device != reconstructed.device:
        reconstructed = reconstructed.to(global_features.device)

    # Concatenate global features with reconstructed token features
    final_features = torch.cat([global_features, reconstructed], dim=-1)

    if config.projection_dim is not None and hasattr(config, "projection"):
        if hasattr(config, "projection_expanded"):
            if hasattr(config, "context_length"):
                projection_expanded_dim = config.projection_expanded
                projection = create_projection_layer(
                    final_features.shape[-1], projection_expanded_dim, 1.0
                )
                final_features = projection(final_features)
                final_features = nn.GELU()(final_features)

                if hasattr(config, "projection_expanded2"):
                    projection_expanded_dim2 = config.projection_expanded2
                    projection = create_projection_layer(
                        projection_expanded_dim, projection_expanded_dim2, 1.0
                    )
                    final_features = projection(final_features)
                    final_features = nn.GELU()(final_features)

            projection = create_projection_layer(
                final_features.shape[-1], config.projection_dim, 1.0
            )
            final_features = projection(final_features)
        else:
            projection = create_projection_layer(
                final_features.shape[-1], config.projection_dim, 1.0
            )
            final_features = projection(final_features)
            final_features = nn.GELU()(final_features)

    return final_features
""")

# Create __init__.py
with open("./moondream_custom/__init__.py", "w") as f:
    f.write("# Custom moondream module")

# Add directory to Python path
sys.path.insert(0, os.path.abspath('./'))

# Function to calculate display dimensions without modifying the original image
def calculate_display_dimensions(image, max_width=900, max_height=600):
    width, height = image.size

    # Calculate the scaling factors for both dimensions
    width_scale = max_width / width if width > max_width else 1
    height_scale = max_height / height if height > max_height else 1

    # Use the smaller scaling factor to ensure both dimensions fit within constraints
    scale = min(width_scale, height_scale)

    # Calculate new dimensions
    display_width = int(width * scale)
    display_height = int(height * scale)

    return display_width, display_height

# Get system info function
def get_system_info():
    info = {
        "OS": f"{platform.system()} {platform.version()}",
        "Python": platform.python_version(),
        "CPU": f"{platform.processor()} ({psutil.cpu_count(logical=False)} cores, {psutil.cpu_count(logical=True)} threads)",
        "RAM": f"{round(psutil.virtual_memory().total / (1024**3), 2)}GB total, {round(psutil.virtual_memory().available / (1024**3), 2)}GB available",
        "PyTorch": f"{torch.__version__} (CUDA: {torch.cuda.is_available()}, MPS: {torch.backends.mps.is_available() if hasattr(torch.backends, 'mps') else False})"
    }

    # Add GPU info if available
    try:
        gpus = GPUtil.getGPUs()
        if gpus:
            info["GPU"] = ", ".join([f"{gpu.name} ({gpu.memoryTotal}MB)" for gpu in gpus])
        else:
            info["GPU"] = "No NVIDIA GPU detected"
    except:
        info["GPU"] = "GPU info not available"

    return info

# Create a cached function for loading the model
@st.cache_resource
def load_model():
    try:
        from transformers import AutoModelForCausalLM

        # Modify sys.modules to use custom vision.py
        from moondream_custom import vision
        sys.modules["vision"] = vision

        # Determine best device
        if torch.cuda.is_available():
            device = "cuda"
            device_map = "auto"
        elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            device = "mps"
            device_map = "auto"
        else:
            device = "cpu"
            device_map = None

        # Load the model
        model = AutoModelForCausalLM.from_pretrained(
            "vikhyatk/moondream2",
            trust_remote_code=True,
            use_safetensors=True,
            low_cpu_mem_usage=True,
            device_map=device_map
        )

        # Patch the vision_projection function
        for name, module in list(sys.modules.items()):
            if name.endswith('moondream') and hasattr(module, '_vis_proj'):
                def patched_vis_proj(self, g, r):
                    if g.device != r.device:
                        r = r.to(g.device)
                    from moondream_custom.vision import vision_projection
                    return vision_projection(g, r, self.vision, self.config.vision)

                model._vis_proj = types.MethodType(patched_vis_proj, model)
                break

        return model, device
    except Exception as e:
        st.error(f"Error loading model: {str(e)}")
        raise e

# Safe execution wrapper for model functions
def safe_model_execution(model_func, *args, **kwargs):
    model, device = st.session_state.model_and_device

    try:
        return model_func(*args, **kwargs)
    except RuntimeError as e:
        error_msg = str(e)
        if "all input tensors must be on the same device" in error_msg or "Expected all tensors to be on the same device" in error_msg:
            st.warning("Detected device mismatch. Trying CPU fallback...")
            original_device = next(model.parameters()).device
            model.to("cpu")
            result = model_func(*args, **kwargs)
            model.to(original_device)
            return result
        else:
            st.error(f"Error in model execution: {error_msg}")
            raise e

# Function to draw annotations on image - with improved bounding box handling
def draw_annotations(image, annotation_type, objects, label):
    if not objects:
        return image

    # Create a copy of the image
    display_image = image.copy()
    draw = ImageDraw.Draw(display_image)
    img_width, img_height = image.size
    font_size = int(max(img_width, img_height) * 0.04)

    # Import font if available
    try:
        from PIL import ImageFont
        font = ImageFont.load_default(size=font_size)
    except:
        font = None

    # Process each object
    x = None; y = None; radius = 20
    for i, obj in enumerate(objects):
        # For detection objects
        if annotation_type == "detection":
            # Some models return "bbox" as a nested dict, others directly in the object
            if "bbox" in obj:
                bbox = obj["bbox"]
            else:
                bbox = obj  # The object itself might be the bbox

            # print(bbox)
            # Check if we have all required bbox properties
            if all(k in bbox for k in ["x_min", "y_min", "x_max", "y_max"]):
                # Get coordinates
                x = float(bbox["x_min"]) * img_width
                y = float(bbox["y_min"]) * img_height
                x2 = float(bbox["x_max"]) * img_width
                y2 = float(bbox["y_max"]) * img_height

                # Draw rectangle - use thick line
                draw.rectangle([x, y, x2, y2], outline="red", width=3)

        # For pointing objects
        elif "x" in obj and "y" in obj:
            # Draw point
            x = float(obj["x"]) * img_width
            y = float(obj["y"]) * img_height

            # Draw circle with a larger radius for better visibility
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill="red")

            # Draw a white border around the circle
            border_width = 2
            draw.ellipse((x-radius-border_width, y-radius-border_width,
                         x+radius+border_width, y+radius+border_width),
                         outline="white", width=border_width)

        # Add label with improved visibility
        label_text = f"{i+1}: {label}"
        if font:
            text_width, text_height = draw.textbbox((0, 0), label_text, font=font)[2:]
            draw.text((x+radius, y+5), f"{i+1}", fill="white", stroke_width=2,
                stroke_fill="black", font=font)
        else:
            draw.text((x+radius, y+5), f"{i+1}", fill="black", stroke_width=2,
                stroke_fill="black")

    if display_image.mode == 'RGBA' and image.mode != 'RGBA':
        # Convert back to original mode if needed
        display_image = display_image.convert(image.mode)

    return display_image

# Main app
st.title("🌙 Moondream2 Image Analysis")

# Initialize session state variables
session_vars = {
    'model_loaded': False, 'encoded_image': None, 'model_and_device': None,
    'image': None, 'image_id': None, 'annotated_image': None,
    'annotation_results': None, 'short_caption': None, 'normal_caption': None,
    'long_caption': None
}

for key, value in session_vars.items():
    if key not in st.session_state:
        st.session_state[key] = value

# Sidebar with system info
with st.sidebar:
    st.markdown("### About This App\nThis app uses Moondream2 to analyze your images.")

    st.markdown("### System Information")
    system_info = get_system_info()
    for key, value in system_info.items():
        st.write(f"**{key}:** {value}")

# Image display container
image_container = st.container()

# Upload section
uploaded_file = st.file_uploader("Choose an image...", type=["jpg", "jpeg", "png", "avif", "heic"])

if uploaded_file is not None:
    # Check if this is a new image
    current_file_bytes = uploaded_file.getvalue()
    if 'last_uploaded_file_bytes' not in st.session_state or current_file_bytes != st.session_state.last_uploaded_file_bytes:
        st.session_state.last_uploaded_file_bytes = current_file_bytes
        st.session_state.short_caption = None
        st.session_state.normal_caption = None
        st.session_state.long_caption = None

    # Load image
    image = Image.open(uploaded_file)
    st.session_state.image = image

    # Display the image with smaller dimension limitation
    with image_container:
        display_width, display_height = calculate_display_dimensions(image)
        st.image(image, caption="Uploaded Image", width=display_width)

    # Model loading button
    if st.button("Load Model and Process Image") or st.session_state.model_loaded:
        try:
            # Load model if not already loaded
            if not st.session_state.model_loaded:
                with st.spinner("Loading Moondream2 model (this may take a few minutes)..."):
                    st.session_state.model_and_device = load_model()
                    st.session_state.model_loaded = True

            # Process the current image
            with st.spinner("Processing image..."):
                model, device = st.session_state.model_and_device
                st.session_state.encoded_image = None  # Clear previous
                st.session_state.encoded_image = safe_model_execution(
                    lambda img: model.encode_image(img),
                    st.session_state.image
                )

            # Create tabs for different features
            tabs = st.tabs(["Captions", "Visual Query", "Object Analysis"])

            # Tab 1: Captions
            with tabs[0]:
                st.subheader("Generate Image Captions")
                model, _ = st.session_state.model_and_device

                # Caption buttons in a row
                col1, col2, col3 = st.columns(3)

                # Define a helper function for caption generation
                def generate_caption(length):
                    with st.spinner(f"Generating {length} caption..."):
                        caption_result = safe_model_execution(
                            lambda img: model.caption(img, length=length),
                            st.session_state.encoded_image
                        )
                        return caption_result["caption"]

                # Add buttons for each caption type
                if col1.button("Generate Short Caption"):
                    st.session_state.short_caption = generate_caption("short")
                if col2.button("Generate Normal Caption"):
                    st.session_state.normal_caption = generate_caption("normal")
                if col3.button("Generate Long Caption"):
                    st.session_state.long_caption = generate_caption("long")

                # Display all captions
                st.markdown("### Caption Results")
                caption_results = st.container()

                with caption_results:
                    for caption_type, caption in [
                        ("Short Caption", st.session_state.short_caption),
                        ("Normal Caption", st.session_state.normal_caption),
                        ("Long Caption", st.session_state.long_caption)
                    ]:
                        if caption:
                            st.info(f"**{caption_type}:** {caption}")

            # Tab 2: Visual Query
            with tabs[1]:
                st.subheader("Ask Questions About the Image")
                model, _ = st.session_state.model_and_device

                # Question options
                questions = [
                    "",
                    "What's in this image?",
                    "How many people are in this image?",
                    "What colors are prominent?",
                    "What is the setting/location?",
                    "Describe the mood or atmosphere."
                ]

                selected_q = st.selectbox("Select a question or type your own:", questions)
                custom_q = st.text_input("Your question:", value=selected_q)

                if st.button("Ask Question") and custom_q:
                    with st.spinner(f"Answering: {custom_q}"):
                        try:
                            answer = safe_model_execution(
                                lambda img, q: model.query(img, q),
                                st.session_state.encoded_image,
                                custom_q
                            )["answer"]
                            st.success(f"**Answer:** {answer}")
                        except Exception as e:
                            st.error(f"Error: {str(e)}")

            # Tab 3: Object Analysis
            with tabs[2]:
                st.subheader("Object Analysis")
                model, _ = st.session_state.model_and_device

                # Object selection
                objects = ["person", "face", "cat", "dog", "car", "building", "tree", "food"]
                col1, col2 = st.columns(2)

                with col1:
                    selected_obj = st.selectbox("Select object:", objects)
                    custom_obj = st.text_input("Or specify another object:", "")
                    obj_type = custom_obj if custom_obj else selected_obj

                with col2:
                    analysis_type = st.radio("Analysis type:", ["Detection (boxes)", "Pointing (points)"])

                    if st.button("Analyze"):
                        if obj_type:
                            with st.spinner(f"Finding {obj_type}..."):
                                try:
                                    # Determine function to call based on analysis type
                                    analysis_config = {
                                        "Detection (boxes)": {
                                            "function": lambda img, obj: model.detect(img, obj),
                                            "key": "detection"
                                        },
                                        "Pointing (points)": {
                                            "function": lambda img, obj: model.point(img, obj),
                                            "key": "pointing"
                                        }
                                    }

                                    config = analysis_config[analysis_type]
                                    result = safe_model_execution(
                                        config["function"],
                                        st.session_state.encoded_image,
                                        obj_type
                                    )

                                    # Get objects from result
                                    objects = result.get("objects", result.get("points", []))

                                    # Display results
                                    if objects:
                                        st.success(f"Found {len(objects)} instances of '{obj_type}'")

                                        # Store results
                                        st.session_state.annotation_results = {
                                            'type': config["key"],
                                            'objects': objects,
                                            'label': obj_type
                                        }

                                        # Create annotated image
                                        st.session_state.annotated_image = draw_annotations(
                                            st.session_state.image,
                                            config["key"],
                                            objects,
                                            obj_type
                                        )

                                        # Display object details
                                        with st.expander("View object details", expanded=False):
                                            for i, obj in enumerate(objects):
                                                st.text(f"Item {i+1}: {obj}")
                                    else:
                                        st.info(f"No instances of '{obj_type}' found")
                                        st.session_state.annotation_results = None
                                        st.session_state.annotated_image = None

                                except Exception as e:
                                    st.error(f"Error: {str(e)}")
                                    st.session_state.annotation_results = None
                                    st.session_state.annotated_image = None

                    if st.button("Reset Analysis"):
                        st.session_state.annotation_results = None
                        st.session_state.annotated_image = None
                        st.rerun()

                # Display annotated image if available
                if st.session_state.annotated_image is not None:
                    st.write("")  # Add spacing
                    with st.expander("View Annotated Image", expanded=True):
                        display_width, display_height = calculate_display_dimensions(
                            st.session_state.annotated_image, max_height=1000)
                        st.image(
                            st.session_state.annotated_image,
                            caption=f"Analyzing: {st.session_state.annotation_results['label']}",
                            width=display_width
                        )

        except Exception as e:
            st.error(f"Error: {str(e)}")
            st.code(traceback.format_exc())
            st.markdown("""
            ### Troubleshooting Tips:
            1. Try reloading the page
            2. Check your internet connection
            3. Make sure you have sufficient RAM
            4. Try a system without MPS/GPU if problems persist
            """)