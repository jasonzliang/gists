import datetime
import json
import gc
import os
import sys
import random
import re
import time
import hashlib
import glob
from io import BytesIO
from pathlib import Path
from functools import lru_cache

import cv2
import numpy as np
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

import torch
import torchvision.transforms as T
from torchvision.transforms.functional import InterpolationMode
from transformers import AutoModel, AutoTokenizer, AutoConfig

# Fix for torch.classes error
import types
sys.modules["torch.classes"] = types.ModuleType("dummy_classes")
sys.modules["torch.classes"].__path__ = []

# Register image format plugins
import pillow_avif
import pillow_heif
pillow_heif.register_heif_opener()

# Constants
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
LOGDIR = "internvl_dir"

# Create log directories
os.makedirs(LOGDIR, exist_ok=True)
os.makedirs(os.path.join(LOGDIR, 'serve_images'), exist_ok=True)

# Utility Functions
def debug_print_state(message, show_contents=False):
    """Print debug info about the current session state"""
    print(f"DEBUG: {message}")
    if 'messages' in st.session_state:
        msg_count = len(st.session_state.messages)
        print(f"Message count: {msg_count}")
        if msg_count > 0 and show_contents:
            print(f"First message role: {st.session_state.messages[0]['role']}")
            print(f"First message content: {st.session_state.messages[0]['content'][:100]}...")

def log_state_change(event_name):
    """Log important state changes for debugging"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg_count = len(st.session_state.get('messages', []))

    with open(os.path.join(LOGDIR, "debug_log.txt"), 'a') as f:
        f.write(f"[{timestamp}] {event_name}: Messages={msg_count}, Model={'model' in st.session_state}\n")

def reset_chat_context():
    """Reset the chat context when needed, but preserve system message"""
    if 'messages' not in st.session_state:
        st.session_state.messages = []
        return

    # Keep only the system message if it exists
    if st.session_state.messages and st.session_state.messages[0]['role'] == 'system':
        system_msg = st.session_state.messages[0]
        st.session_state.messages = [system_msg]
    else:
        st.session_state.messages = []

        # Re-add system message if not using empty prompt
        if not st.session_state.get('use_empty_system_prompt', False):
            system_content = (st.session_state.get('system_message_default', 'I am InternVL3, a multimodal large language model.') +
                            '\n\n' +
                            st.session_state.get('system_message_editable', 'Please answer the user questions in detail.'))
            st.session_state.messages.append({'role': 'system', 'content': system_content})

def get_device():
    """Determine the best available device (CUDA, MPS, or CPU)"""
    if torch.cuda.is_available():
        return "cuda"
    elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        return "mps"
    else:
        return "cpu"

# Image Processing
def build_transform(input_size):
    """Build image transformation pipeline"""
    return T.Compose([
        T.Lambda(lambda img: img.convert('RGB') if img.mode != 'RGB' else img),
        T.Resize((input_size, input_size), interpolation=InterpolationMode.BICUBIC),
        T.ToTensor(),
        T.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD)
    ])

def find_closest_aspect_ratio(aspect_ratio, target_ratios, width, height, image_size):
    """Find closest aspect ratio for image tiling"""
    best_ratio_diff = float('inf')
    best_ratio = (1, 1)
    area = width * height

    for ratio in target_ratios:
        target_aspect_ratio = ratio[0] / ratio[1]
        ratio_diff = abs(aspect_ratio - target_aspect_ratio)

        if ratio_diff < best_ratio_diff:
            best_ratio_diff = ratio_diff
            best_ratio = ratio
        elif ratio_diff == best_ratio_diff and area > 0.5 * image_size * image_size * ratio[0] * ratio[1]:
            best_ratio = ratio

    return best_ratio

def dynamic_preprocess(image, min_num=1, max_num=12, image_size=448, use_thumbnail=False):
    """Preprocess image with dynamic tiling based on aspect ratio"""
    orig_width, orig_height = image.size
    aspect_ratio = orig_width / orig_height

    # Calculate possible aspect ratios
    target_ratios = set(
        (i, j) for n in range(min_num, max_num + 1)
        for i in range(1, n + 1)
        for j in range(1, n + 1)
        if i * j <= max_num and i * j >= min_num
    )
    target_ratios = sorted(target_ratios, key=lambda x: x[0] * x[1])

    # Find closest aspect ratio
    target_aspect_ratio = find_closest_aspect_ratio(
        aspect_ratio, target_ratios, orig_width, orig_height, image_size)

    # Calculate target dimensions
    target_width = image_size * target_aspect_ratio[0]
    target_height = image_size * target_aspect_ratio[1]
    blocks = target_aspect_ratio[0] * target_aspect_ratio[1]

    # Resize and split the image
    resized_img = image.resize((target_width, target_height))
    processed_images = []

    for i in range(blocks):
        box = (
            (i % (target_width // image_size)) * image_size,
            (i // (target_width // image_size)) * image_size,
            ((i % (target_width // image_size)) + 1) * image_size,
            ((i // (target_width // image_size)) + 1) * image_size
        )
        processed_images.append(resized_img.crop(box))

    # Add thumbnail if needed
    if use_thumbnail and len(processed_images) > 1:
        thumbnail_img = image.resize((image_size, image_size))
        processed_images.append(thumbnail_img)

    return processed_images

def load_image(image_file, input_size=448, max_num=12):
    """Load and transform image for model input"""
    if isinstance(image_file, str):
        image = Image.open(image_file).convert('RGB')
    else:
        image = image_file

    transform = build_transform(input_size=input_size)
    images = dynamic_preprocess(image, image_size=input_size, use_thumbnail=True, max_num=max_num)
    pixel_values = torch.stack([transform(image) for image in images])

    return pixel_values

def process_image(image, max_num=12):
    """Process image for model input with device-specific handling"""
    device = get_device()
    img_pixel_values = load_image(image, input_size=448, max_num=max_num)

    # Handle device placement and precision
    if device == "mps":
        try:
            img_pixel_values = img_pixel_values.to(torch.float16).to(device)
        except Exception as e:
            print(f"Warning: Could not use float16 on MPS: {str(e)}. Falling back to float32.")
            img_pixel_values = img_pixel_values.to(torch.float32).to(device)
    elif device == "cuda":
        img_pixel_values = img_pixel_values.to(torch.float16).to(device)
    else:
        img_pixel_values = img_pixel_values.to(torch.float32)

    # Handle NaN/Inf values
    if torch.isnan(img_pixel_values).any() or torch.isinf(img_pixel_values).any():
        img_pixel_values = torch.nan_to_num(img_pixel_values, nan=0.0, posinf=1.0, neginf=-1.0)

    return img_pixel_values

# Model Loading
@st.cache_resource(show_spinner=False)
def load_model(model_path):
    """Load the InternVL model with proper device handling

    This function is cached with @st.cache_resource to ensure the model is
    only loaded once per model_path, preventing duplicate loading.
    """
    try:
        device = get_device()
        config = AutoConfig.from_pretrained(model_path, trust_remote_code=True)

        # Common arguments for model loading
        common_args = {
            "low_cpu_mem_usage": True,
            "trust_remote_code": True
        }

        if device == "mps":
            st.info("Loading model on Apple Silicon GPU (MPS)")

            # Configure for MPS
            model_args = {
                **common_args,
                "torch_dtype": torch.float16,
                "use_flash_attn": False,
                "device_map": None
            }

            try:
                model = AutoModel.from_pretrained(model_path, **model_args)
                model = model.to("mps").eval()
                st.success("Model loaded in float16 on MPS")
            except Exception as e:
                st.warning(f"Could not load model in float16: {str(e)}. Trying float32.")
                model_args["torch_dtype"] = torch.float32
                model = AutoModel.from_pretrained(model_path, **model_args)
                model = model.to("mps").eval()
                st.success("Model loaded in float32 on MPS")

        elif device == "cuda":
            st.info(f"Loading model on NVIDIA GPU (CUDA)")

            model_args = {
                **common_args,
                "torch_dtype": torch.float16,
                "use_flash_attn": True,
                "device_map": "auto"
            }

            model = AutoModel.from_pretrained(model_path, **model_args).eval()

        else:
            st.warning("No GPUs detected. Loading model on CPU (this will be very slow)")

            model_args = {
                **common_args,
                "torch_dtype": torch.float32,
                "use_flash_attn": False
            }

            model = AutoModel.from_pretrained(model_path, **model_args).eval()

        # Load tokenizer
        tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True, use_fast=False)

        print(f"Model {model_path} loaded successfully via cache_resource decorator")
        return model, tokenizer
    except Exception as e:
        st.error(f"Failed to load model: {str(e)}")
        return None, None

# Response Generation
def generate_response(messages, model, tokenizer, max_length, temperature, top_p, repetition_penalty, max_input_tiles):
    """Generate a response using the InternVL model"""
    placeholder = st.empty()

    # Ensure model is in eval mode
    if not model.training:
        model.eval()

    # Process images from the latest user message if any
    pixel_values = None
    num_patches_list = None

    if messages[-1]['role'] == 'user' and 'image' in messages[-1] and messages[-1]['image']:
        with st.status("Processing images..."):
            try:
                # Get all images from the user's message
                images = messages[-1]['image']
                all_pixel_values = []
                num_patches_list = []

                # Process each image
                for img in images:
                    img_pixel_values = process_image(img, max_num=max_input_tiles)
                    num_patches_list.append(img_pixel_values.size(0))
                    all_pixel_values.append(img_pixel_values)

                # Concatenate all image pixel values
                if all_pixel_values:
                    pixel_values = torch.cat(all_pixel_values, dim=0)

                    # Add image indicators for multiple images
                    if len(images) > 1:
                        image_prefix = ''.join([f'Image-{i+1}: <image>\n' for i in range(len(images))])
                        messages[-1]['content'] = image_prefix + messages[-1]['content']
            except Exception as e:
                st.error(f"Error processing images: {str(e)}")
                return f"Error processing images: {str(e)}"

    # Prepare conversation history
    history = None
    if not st.session_state.get('reset_history', False) and len(messages) > 2:
        history = []
        # Format history according to the reference implementation
        for i in range(1, len(messages) - 1, 2):
            if i + 1 < len(messages):
                history.append([messages[i]['content'], messages[i+1]['content']])

    # Reset the flag after using it
    if st.session_state.get('reset_history', False):
        st.session_state.reset_history = False

    # Configure generation parameters
    generation_config = {
        'max_new_tokens': max_length,
        'do_sample': temperature > 0.01,
        'temperature': float(temperature),
        'top_p': float(top_p),
        'repetition_penalty': float(repetition_penalty),
    }

    # Generate response
    try:
        with st.status("Generating response..."):
            # Get the user's question
            question = messages[-1]['content']

            # For image conversation, add <image> tags if not already present
            if pixel_values is not None and '<image>' not in question:
                if len(messages[-1]['image']) == 1:
                    question = '<image>\n' + question

            # Generate response
            try:
                if history is None:
                    # First message in the conversation
                    if num_patches_list and len(num_patches_list) > 1:
                        # For multiple images
                        response = model.chat(tokenizer, pixel_values, question, generation_config,
                                           num_patches_list=num_patches_list)
                    else:
                        # For single image or no image
                        response = model.chat(tokenizer, pixel_values, question, generation_config)
                else:
                    # Continuing conversation
                    if num_patches_list and len(num_patches_list) > 1:
                        # For multiple images
                        response, _ = model.chat(tokenizer, pixel_values, question, generation_config,
                                              num_patches_list=num_patches_list,
                                              history=None, return_history=True)
                    else:
                        # For single image or no image
                        response, _ = model.chat(tokenizer, pixel_values, question, generation_config,
                                              history=None, return_history=True)
            except RuntimeError as e:
                # Handle probability tensor error
                if "inf" in str(e) or "nan" in str(e) or "< 0" in str(e):
                    st.warning("Encountered probability error. Trying conservative settings...")

                    # Fallback to greedy decoding
                    fallback_config = {
                        'max_new_tokens': max_length,
                        'do_sample': False,
                        'repetition_penalty': 1.0
                    }

                    if history is None:
                        response = model.chat(tokenizer, pixel_values, question, fallback_config)
                    else:
                        response, _ = model.chat(tokenizer, pixel_values, question, fallback_config,
                                             history=None, return_history=True)
                else:
                    raise e

            # Update display with the final response
            placeholder.markdown(response)
    except Exception as e:
        error_msg = f"Error generating response: {str(e)}"
        st.error(error_msg)
        response = error_msg

    return response

# UI Components
class Library:
    """Simple component to display multiple images in a grid"""
    def __init__(self, images):
        if not images:
            return

        num_images = len(images)
        cols = min(num_images, 4)  # Maximum 4 columns
        rows = (num_images + cols - 1) // cols

        for i in range(rows):
            row_cols = st.columns(cols)
            for j in range(cols):
                idx = i * cols + j
                if idx < num_images:
                    with row_cols[j]:
                        st.image(images[idx], use_container_width=True)

def find_bounding_boxes(response, messages):
    """Extract and visualize bounding boxes from model response"""
    pattern = re.compile(r'<ref>\s*(.*?)\s*</ref>\s*<box>\s*(\[\[.*?\]\])\s*</box>')
    matches = pattern.findall(response)

    if not matches:
        return None

    # Find the last image used
    last_image = None
    for message in messages:
        if message['role'] == 'user' and 'image' in message and message['image']:
            last_image = message['image'][-1]

    if not last_image:
        return None

    # Create a copy to draw on
    returned_image = last_image.copy()
    draw = ImageDraw.Draw(returned_image)
    width, height = returned_image.size
    line_width = max(1, int(min(width, height) / 200))

    # Draw each bounding box
    for category_name, coordinates in matches:
        # Generate a random color
        color = (random.randint(0, 128), random.randint(0, 128), random.randint(0, 128))

        # Process coordinates
        coordinates = eval(coordinates)

        # Convert from normalized to pixel coordinates
        pixel_coords = [
            (int(x[0] * width / 1000), int(x[1] * height / 1000),
             int(x[2] * width / 1000), int(x[3] * height / 1000))
            for x in coordinates
        ]

        # Draw each box
        for box in pixel_coords:
            # Draw rectangle
            draw.rectangle(box, outline=color, width=line_width)

            # Try to load font
            try:
                font = ImageFont.truetype('Arial.ttf', int(20 * line_width / 2))
            except:
                font = ImageFont.load_default()

            # Get text dimensions
            text_width = len(category_name) * 8
            text_height = 12
            if hasattr(font, 'getsize'):
                text_width, text_height = font.getsize(category_name)

            # Draw text background
            text_position = (box[0], max(0, box[1] - text_height))
            draw.rectangle(
                [text_position, (text_position[0] + text_width, text_position[1] + text_height)],
                fill=color
            )

            # Draw text
            draw.text(text_position, category_name, fill='white', font=font)

    return returned_image

# File and Image Handling
def get_conv_log_filename():
    """Generate a filename for saving conversation logs"""
    t = datetime.datetime.now()
    return os.path.join(LOGDIR, f'{t.year}-{t.month:02d}-{t.day:02d}-conv.json')

def save_chat_history(messages, model_name):
    """Save the conversation history to a log file"""
    if not messages:
        return

    # Create a simplified copy of messages for logging
    log_messages = []
    for message in messages:
        log_message = {'role': message['role'], 'content': message['content']}
        if 'filenames' in message:
            log_message['filenames'] = message['filenames']
        log_messages.append(log_message)

    # Write to log file
    with open(get_conv_log_filename(), 'a') as f:
        json.dump({
            'type': 'chat',
            'model': model_name,
            'messages': log_messages,
        }, f, ensure_ascii=False)
        f.write('\n')

def clear_logs_and_images():
    """Delete log files and saved images"""
    try:
        # Delete log files
        for file in glob.glob(os.path.join(LOGDIR, '*.json')):
            os.remove(file)

        # Delete image directories
        image_dir = os.path.join(LOGDIR, 'serve_images')
        if os.path.exists(image_dir):
            for dir_path in glob.glob(os.path.join(image_dir, '*')):
                # Delete files in the directory
                for image_file in glob.glob(os.path.join(dir_path, '*')):
                    os.remove(image_file)
                # Remove the directory
                os.rmdir(dir_path)

        return True
    except Exception as e:
        st.error(f"Error clearing logs and images: {str(e)}")
        return False

def resize_image_to_max_pixels(img, max_pixels=1000000):
    """Resize an image to have approximately max_pixels while maintaining aspect ratio"""
    width, height = img.size
    current_pixels = width * height

    if current_pixels <= max_pixels:
        return img  # No resizing needed

    scale_factor = (max_pixels / current_pixels) ** 0.5
    new_width = int(width * scale_factor)
    new_height = int(height * scale_factor)

    return img.resize((new_width, new_height), Image.LANCZOS)

def load_upload_file_and_show(uploaded_files):
    """Load uploaded files, resize if needed, and save them"""
    if not uploaded_files:
        return [], []

    images, filenames = [], []
    for file in uploaded_files:
        # Read file bytes
        file_bytes = np.asarray(bytearray(file.read()), dtype=np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(img)

        # Resize if necessary
        img = resize_image_to_max_pixels(img, max_pixels=1000000)
        images.append(img)

    # Generate filenames using hash
    for image in images:
        image_hash = hashlib.md5(image.tobytes()).hexdigest()
        t = datetime.datetime.now()
        directory = os.path.join(LOGDIR, 'serve_images', f'{t.year}-{t.month:02d}-{t.day:02d}')
        os.makedirs(directory, exist_ok=True)

        filename = os.path.join(directory, f'{image_hash}.jpg')
        filenames.append(filename)

        # Save the image if it doesn't exist
        if not os.path.isfile(filename):
            image.save(filename)

    return images, filenames

def show_one_or_multiple_images(message, total_image_num, lan='English', is_input=True):
    """Display images from a message and return updated total"""
    if 'image' not in message or not message['image']:
        return total_image_num

    if is_input:
        # Update total image count
        new_total = total_image_num + len(message['image'])

        # Create appropriate label based on language
        if lan == 'English':
            if len(message['image']) == 1 and new_total == 1:
                label = f"(In this conversation, {len(message['image'])} image was uploaded, {new_total} image in total)"
            elif len(message['image']) == 1 and new_total > 1:
                label = f"(In this conversation, {len(message['image'])} image was uploaded, {new_total} images in total)"
            else:
                label = f"(In this conversation, {len(message['image'])} images were uploaded, {new_total} images in total)"
        else:
            label = f"(在本次对话中，上传了{len(message['image'])}张图片，总共上传了{new_total}张图片)"

        # Display images
        with st.container():
            Library(message['image'])

        if len(message['image']) > 0:
            st.markdown(label)

        return new_total
    else:
        # Just display images for non-input messages
        with st.container():
            Library(message['image'])
        return total_image_num

def reset_model_state(model):
    # Reset img_context_token_id
    model.img_context_token_id = None

    # If you're in the middle of a conversation and want to start fresh
    # Reset the conversation template
    from internvl_helper import get_conv_template
    model.conv_template = get_conv_template(model.template)

    # Force release of CUDA cache if using GPU
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # Ensure model is in eval mode for inference
    model.eval()

    # Reset any potential hooks
    for module in model.modules():
        module._forward_hooks.clear()
        module._forward_pre_hooks.clear()
        module._backward_hooks.clear()

    # Clear KV cache
    if hasattr(model.language_model, 'past_key_values'):
        model.language_model.past_key_values = None

    # For transformer models, we can reset any module that might contain state
    for module in model.modules():
        if hasattr(module, 'past_key_values'):
            module.past_key_values = None
        if hasattr(module, 'past_key_value'):
            module.past_key_value = None

    return model

def main():
    # App configuration
    st.set_page_config(page_title='InternVL3 Demo', layout="wide")

    # Initialize session state
    if 'uploader_key' not in st.session_state:
        st.session_state.uploader_key = 0
    if 'needs_rerun' not in st.session_state:
        st.session_state.needs_rerun = False
    if 'messages' not in st.session_state:
        st.session_state.messages = []

    # Sidebar for settings
    with st.sidebar:
        st.title("InternVL3 Chat Demo")

        # Display device information
        device = get_device()
        if device == "cuda":
            st.success(f"Using CUDA GPU: {torch.cuda.get_device_name(0)}")
        elif device == "mps":
            st.success("Using Apple Silicon GPU (MPS)")
        else:
            st.warning("Using CPU (this will be slow)")

        # Language selection
        lan = st.selectbox('Language / 语言', ['English', '中文'],
                         help='This is only for switching the UI language.')

        # Set default messages based on language
        if lan == 'English':
            system_message_default = 'I am InternVL3, a multimodal large language model developed by OpenGVLab.'
            system_message_editable = 'Please answer the user questions in detail.'
        else:
            system_message_default = '我是书生·万象，英文名是InternVL，是由上海人工智能实验室、清华大学及多家合作单位联合开发的多模态大语言模型。'
            system_message_editable = '请尽可能详细地回答用户的问题。'

        # Model selection
        model_options = [
            "OpenGVLab/InternVL3-1B",
            "OpenGVLab/InternVL3-2B",
            "OpenGVLab/InternVL3-8B",
            "OpenGVLab/InternVL3-9B",
            "OpenGVLab/InternVL3-14B",
            "OpenGVLab/InternVL3-38B",
            "OpenGVLab/InternVL3-78B"
        ]
        model_path = st.selectbox("Model Selection", model_options, index=4,
                                help="Select the InternVL3 model variant to use.")

        st.sidebar.markdown("---")
        st.sidebar.subheader("Model Status")
        model_status = st.sidebar.empty()

        # Handle model switching with improved checks to prevent double loading
        need_model_switch = False

        # Check if we need to switch models
        if 'current_model_path' not in st.session_state:
            need_model_switch = True
            print("No current model path - will trigger model load")
        elif st.session_state.current_model_path != model_path:
            need_model_switch = True
            print(f"Model path changed from {st.session_state.current_model_path} to {model_path} - will trigger model load")

        if need_model_switch:
            # Unload previous model if it exists
            if 'model' in st.session_state:
                try:
                    # Move model to CPU first if it's on GPU
                    current_device = get_device()
                    if current_device != "cpu":
                        st.session_state.model.to('cpu')

                    # Clear references
                    st.session_state.model = None
                    st.session_state.tokenizer = None
                    gc.collect()

                    # Clear device cache
                    if current_device == "cuda":
                        torch.cuda.empty_cache()

                    # Wait a bit to ensure resources are released
                    time.sleep(1.0)

                    model_status.success("Previous model unloaded successfully")
                except Exception as e:
                    model_status.warning(f"Error unloading previous model: {str(e)}")

            # Update model path and trigger rerun
            # Important: Remove the model from session state so it will be reloaded
            st.session_state.pop('model', None)
            st.session_state.pop('tokenizer', None)
            st.session_state.current_model_path = model_path
            st.session_state.needs_rerun = True
            print(f"Model switch prepared: will load {model_path} on next rerun")

        # System prompt settings
        use_empty_system_prompt = st.checkbox('Use empty system prompt',
            help='Check this to use an empty string as system prompt instead of the default.')
        st.session_state.use_empty_system_prompt = use_empty_system_prompt

        with st.expander('🤖 System Prompt'):
            if not use_empty_system_prompt:
                # Show editable system prompt
                system_message_editable = st.text_area('System Prompt', value=system_message_editable,
                    help='System prompt is a message used to instruct the assistant.', height=100)

                # Store in session state
                st.session_state.system_message_default = system_message_default
                st.session_state.system_message_editable = system_message_editable

                # Initialize or update system message
                if not st.session_state.messages:
                    # Initialize with system message
                    st.session_state.messages.append(
                        {'role': 'system', 'content': system_message_default + '\n\n' + system_message_editable})
                elif st.session_state.messages[0]['role'] != 'system':
                    # No system message, but should have one - add it at the beginning
                    st.session_state.messages.insert(0,
                        {'role': 'system', 'content': system_message_default + '\n\n' + system_message_editable})
            else:
                # Display a message indicating empty system prompt is being used
                st.info('Using empty system prompt. Uncheck the option above to use a custom prompt.')
                # Remove system message if present
                if st.session_state.messages and st.session_state.messages[0]['role'] == 'system':
                    st.session_state.messages.pop(0)

        # Advanced generation options
        with st.expander('🔥 Advanced Options'):
            temperature = st.slider('Temperature', min_value=0.0, max_value=1.0, value=0.3, step=0.01)
            top_p = st.slider('Top-p', min_value=0.0, max_value=1.0, value=0.9, step=0.01)
            repetition_penalty = st.slider('Repetition Penalty', min_value=1.0, max_value=1.5, value=1.1, step=0.01)
            max_length = st.slider('Max New Tokens', min_value=0, max_value=1024, value=512, step=8)
            max_input_tiles = st.slider('Max Input Tiles (controls image resolution)',
                min_value=1, max_value=24, value=12, step=1)

        # Clear history button
        if st.button('Clear chat history, logs, images'):
            with st.spinner("Clearing history..."):
                # Log state before clearing
                log_state_change("Before clearing history")

                # Save current system prompt before clearing
                current_system_prompt = ""
                if st.session_state.messages and st.session_state.messages[0]['role'] == 'system':
                    current_system_prompt = st.session_state.messages[0]['content']
                elif not st.session_state.get('use_empty_system_prompt', False):
                    # Use default if no system prompt exists yet
                    current_system_prompt = (st.session_state.get('system_message_default', system_message_default) +
                                           '\n\n' +
                                           st.session_state.get('system_message_editable', system_message_editable))

                # Call reset_chat_context() to properly clear messages
                reset_chat_context()

                # Reset model state
                reset_model_state(st.session_state.model)

                # Set reset_history flag for the response generation
                st.session_state.reset_history = True

                # Reset file uploader
                st.session_state.uploader_key += 1

                # Clear logs and images
                success = clear_logs_and_images()

                if success:
                    st.success("Chat history, logs, and images cleared successfully!")
                else:
                    st.error("Failed to clear some logs or images. See console for details.")

                # Force a rerun to update the UI
                st.session_state.needs_rerun = True
            time.sleep(1)

        # File uploader
        upload_image_preview = st.empty()
        uploaded_files = st.file_uploader('Upload files', accept_multiple_files=True,
                                         type=['png', 'jpg', 'jpeg', 'webp', 'avif', 'heic', 'webp'],
                                         help='You can upload up to 4 images.',
                                         key=f'uploader_{st.session_state.uploader_key}')

        uploaded_pil_images, save_filenames = load_upload_file_and_show(uploaded_files)

        if uploaded_pil_images:
            with upload_image_preview.container():
                Library(uploaded_pil_images)

        # Debug section
        with st.sidebar.expander("Debug Information", expanded=False):
            st.write("Session State Keys:", list(st.session_state.keys()))
            if st.button('Show Messages'):
                if 'messages' in st.session_state:
                    st.write(f"Current messages (length: {len(st.session_state.messages)}):")
                    if st.session_state.messages:
                        for msg in st.session_state.messages:
                            st.write("%s: %s" % (msg["role"], msg["content"][:50]))

    # Main content area
    st.title("InternVL3 Chat Demo")
    st.caption("A multimodal large language model for vision-language understanding")

    # Load the model - with additional checks to prevent duplicate loading
    should_load_model = False

    # Check if we need to load the model
    if 'model' not in st.session_state or 'tokenizer' not in st.session_state:
        should_load_model = True
    elif 'current_model_path' in st.session_state and st.session_state.current_model_path != model_path:
        # We're switching models, but this should already be handled in the sidebar code
        # This is a safety check in case the sidebar code didn't run properly
        should_load_model = True

    # Only load if needed
    if should_load_model:
        with st.spinner(f"Loading {model_path} model... This may take a few minutes."):
            try:
                model, tokenizer = load_model(model_path)
                if model is not None and tokenizer is not None:
                    st.session_state.model = model
                    st.session_state.tokenizer = tokenizer
                    st.session_state.current_model_path = model_path
                    model_status.success(f"Model {model_path} loaded successfully!")
                else:
                    model_status.error("Failed to load model. Please check the model path and try again.")
                    st.stop()
            except Exception as e:
                model_status.error(f"Error loading model: {str(e)}")
                st.stop()

    # Display chat messages
    total_image_num = 0
    for message in st.session_state.messages:
        with st.chat_message(message['role']):
            st.markdown(message['content'])
            total_image_num = show_one_or_multiple_images(message, total_image_num, lan=lan,
                                                        is_input=message['role'] == 'user')

    # Max image limit check
    max_image_limit = 4  # Maximum number of images allowed
    input_disable_flag = total_image_num + len(uploaded_pil_images) > max_image_limit

    # Chat input
    if input_disable_flag:
        prompt_text = 'Too many images have been uploaded. Please clear the history.' if lan == 'English' else '输入的图片太多了，请清空历史记录。'
        prompt = st.chat_input(prompt_text, disabled=True)
    else:
        prompt_text = 'Send messages to InternVL3' if lan == 'English' else '发送信息给 InternVL3'
        prompt = st.chat_input(prompt_text)

    # Handle new message
    if prompt:
        # Add user message to chat
        st.session_state.messages.append({
            'role': 'user',
            'content': prompt,
            'image': uploaded_pil_images,
            'filenames': save_filenames
        })

        # Display user message
        with st.chat_message('user'):
            st.write(prompt)
            total_image_num = show_one_or_multiple_images(st.session_state.messages[-1], total_image_num, lan=lan, is_input=True)

        # Reset file uploader
        if uploaded_pil_images:
            st.session_state.uploader_key += 1
            st.session_state.needs_rerun = True

    # Generate response if last message is from user
    if st.session_state.messages and st.session_state.messages[-1]['role'] == 'user':
        with st.chat_message('assistant'):
            with st.spinner('InternVL3 is thinking...'):

                # Additional check to ensure we have a valid model and state
                if 'model' not in st.session_state or 'tokenizer' not in st.session_state:
                    st.error("Model not loaded properly. Please refresh the page.")
                    st.stop()

                # Check if the model is in a valid state
                try:
                    # Simple check if the model is responsive
                    model_device = next(st.session_state.model.parameters()).device
                    print(f"Model is on device: {model_device}")
                except Exception as e:
                    st.error(f"Model appears to be in an invalid state: {str(e)}. Please refresh the page.")
                    # Force model reload on next run
                    st.session_state.pop('model', None)
                    st.session_state.pop('tokenizer', None)
                    st.session_state.needs_rerun = True
                    st.stop()

                # Generate response
                response = generate_response(
                    st.session_state.messages,
                    st.session_state.model,
                    st.session_state.tokenizer,
                    max_length, temperature, top_p, repetition_penalty, max_input_tiles,
                )

                # Create message for the assistant's response
                message = {'role': 'assistant', 'content': response}

                # Handle any bounding box annotations in the response
                if '<ref>' in response and '</box>' in response:
                    returned_image = find_bounding_boxes(response, st.session_state.messages)
                    if returned_image:
                        message['image'] = [returned_image]

                # Add message to history
                st.session_state.messages.append(message)

                # Show any images in the response
                show_one_or_multiple_images(message, total_image_num, lan=lan, is_input=False)

    # Save chat history
    save_chat_history(st.session_state.messages, model_path)

    # Handle rerun if needed
    if st.session_state.needs_rerun:
        st.session_state.needs_rerun = False
        st.rerun()

if __name__ == "__main__":
    main()