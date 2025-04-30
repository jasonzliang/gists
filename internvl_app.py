import datetime
import json
import os
import sys
import random
import re
import hashlib
from io import BytesIO

import cv2
import numpy as np
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

import math
import torch
import types
import torchvision.transforms as T
from torchvision.transforms.functional import InterpolationMode
from transformers import AutoModel, AutoTokenizer, AutoConfig

# Fix for torch.classes error
dummy_module = types.ModuleType("dummy_classes")
dummy_module.__path__ = []
sys.modules["torch.classes"] = dummy_module

# Constants for image processing
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
LOGDIR = "internvl_logs"  # Directory for storing logs

# Create log directory if it doesn't exist
os.makedirs(LOGDIR, exist_ok=True)
os.makedirs(os.path.join(LOGDIR, 'serve_images'), exist_ok=True)

# Check for available devices
def get_device():
    """Determine the best available device (CUDA, MPS, or CPU)"""
    if torch.cuda.is_available():
        return "cuda"
    elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        return "mps"
    else:
        return "cpu"

# Image processing functions
def build_transform(input_size):
    MEAN, STD = IMAGENET_MEAN, IMAGENET_STD
    transform = T.Compose([
        T.Lambda(lambda img: img.convert('RGB') if img.mode != 'RGB' else img),
        T.Resize((input_size, input_size), interpolation=InterpolationMode.BICUBIC),
        T.ToTensor(),
        T.Normalize(mean=MEAN, std=STD)
    ])
    return transform

def find_closest_aspect_ratio(aspect_ratio, target_ratios, width, height, image_size):
    best_ratio_diff = float('inf')
    best_ratio = (1, 1)
    area = width * height
    for ratio in target_ratios:
        target_aspect_ratio = ratio[0] / ratio[1]
        ratio_diff = abs(aspect_ratio - target_aspect_ratio)
        if ratio_diff < best_ratio_diff:
            best_ratio_diff = ratio_diff
            best_ratio = ratio
        elif ratio_diff == best_ratio_diff:
            if area > 0.5 * image_size * image_size * ratio[0] * ratio[1]:
                best_ratio = ratio
    return best_ratio

def dynamic_preprocess(image, min_num=1, max_num=12, image_size=448, use_thumbnail=False):
    orig_width, orig_height = image.size
    aspect_ratio = orig_width / orig_height

    # calculate the existing image aspect ratio
    target_ratios = set(
        (i, j) for n in range(min_num, max_num + 1) for i in range(1, n + 1) for j in range(1, n + 1) if
        i * j <= max_num and i * j >= min_num)
    target_ratios = sorted(target_ratios, key=lambda x: x[0] * x[1])

    # find the closest aspect ratio to the target
    target_aspect_ratio = find_closest_aspect_ratio(
        aspect_ratio, target_ratios, orig_width, orig_height, image_size)

    # calculate the target width and height
    target_width = image_size * target_aspect_ratio[0]
    target_height = image_size * target_aspect_ratio[1]
    blocks = target_aspect_ratio[0] * target_aspect_ratio[1]

    # resize the image
    resized_img = image.resize((target_width, target_height))
    processed_images = []
    for i in range(blocks):
        box = (
            (i % (target_width // image_size)) * image_size,
            (i // (target_width // image_size)) * image_size,
            ((i % (target_width // image_size)) + 1) * image_size,
            ((i // (target_width // image_size)) + 1) * image_size
        )
        # split the image
        split_img = resized_img.crop(box)
        processed_images.append(split_img)
    assert len(processed_images) == blocks
    if use_thumbnail and len(processed_images) != 1:
        thumbnail_img = image.resize((image_size, image_size))
        processed_images.append(thumbnail_img)
    return processed_images

def load_image(image_file, input_size=448, max_num=12):
    if isinstance(image_file, str):
        image = Image.open(image_file).convert('RGB')
    else:
        image = image_file
    transform = build_transform(input_size=input_size)
    images = dynamic_preprocess(image, image_size=input_size, use_thumbnail=True, max_num=max_num)
    pixel_values = [transform(image) for image in images]
    pixel_values = torch.stack(pixel_values)
    return pixel_values

@st.cache_resource
def load_model(model_path):
    """Load the InternVL model"""
    try:
        device = get_device()

        if device == "cuda":
            world_size = torch.cuda.device_count()
            if world_size > 0:
                st.info(f"Loading model on NVIDIA GPU (cuda)")
                model = AutoModel.from_pretrained(
                    model_path,
                    torch_dtype=torch.float16,
                    load_in_8bit=False,
                    low_cpu_mem_usage=True,
                    trust_remote_code=True,
                    device_map=device).eval()
            else:
                st.warning("No GPUs detected. Loading model on CPU (this will be very slow)")
                model = AutoModel.from_pretrained(
                    model_path,
                    torch_dtype=torch.float32,
                    low_cpu_mem_usage=True,
                    trust_remote_code=True).eval()

        elif device == "mps":
            st.info("Loading model on Apple Silicon GPU (MPS)")
            # For MPS, we use float16 if available but fallback to float32 if needed
            try:
                model = AutoModel.from_pretrained(
                    model_path,
                    torch_dtype=torch.float16,
                    low_cpu_mem_usage=True,
                    trust_remote_code=True).eval().to(device)
            except Exception as e:
                st.warning(f"Could not load model in float16 on MPS: {str(e)}. Trying float32.")
                model = AutoModel.from_pretrained(
                    model_path,
                    torch_dtype=torch.float32,
                    low_cpu_mem_usage=True,
                    trust_remote_code=True).eval().to(device)

        else:
            st.warning("No GPUs detected. Loading model on CPU (this will be very slow)")
            model = AutoModel.from_pretrained(
                model_path,
                torch_dtype=torch.float32,
                low_cpu_mem_usage=True,
                trust_remote_code=True).eval()

        # Set up tokenizer
        tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True, use_fast=False)

        # Perform a test run to catch any initialization issues
        try:
            dummy_input = tokenizer("Hello, world!", return_tensors="pt")
            if device == "cuda":
                dummy_input = {k: v.cuda() for k, v in dummy_input.items()}
            elif device == "mps":
                dummy_input = {k: v.to(device) for k, v in dummy_input.items()}

            with torch.no_grad():
                # Try to run a quick test to catch initialization issues
                _ = model.generate(**dummy_input, max_new_tokens=5)

            st.success("Model initialized and tested successfully.")
        except Exception as e:
            st.warning(f"Model initialized but test generation failed: {str(e)}. Some features may not work properly.")

        return model, tokenizer
    except Exception as e:
        st.error(f"Failed to load model: {str(e)}")
        return None, None

# Simple library component to display images
class Library:
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

def generate_response(messages, model, tokenizer, max_length, temperature, top_p, repetition_penalty, max_input_tiles):
    """Generate a response using the InternVL model with robust error handling"""
    placeholder = st.empty()
    device = get_device()

    # Process images from the latest user message if any
    pixel_values = None
    if messages[-1]['role'] == 'user' and 'image' in messages[-1] and len(messages[-1]['image']) > 0:
        with st.status("Processing images..."):
            # Get all images from the user's message
            images = messages[-1]['image']

            # Process only one image at a time if there are issues
            image = images[0]  # Just use the first image

            try:
                img_pixel_values = load_image(image, max_num=max_input_tiles)

                # Convert to appropriate precision based on device
                if device == "cuda":
                    img_pixel_values = img_pixel_values.to(torch.float16).cuda()
                elif device == "mps":
                    # Try float16 first on MPS (consistent with model loading)
                    try:
                        img_pixel_values = img_pixel_values.to(torch.float16).to(device)
                    except Exception as e:
                        print(f"Warning: Could not use float16 on MPS: {str(e)}. Falling back to float32.")
                        img_pixel_values = img_pixel_values.to(torch.float32).to(device)
                else:
                    img_pixel_values = img_pixel_values.to(torch.float32)

                # Check for NaN/Inf values
                if torch.isnan(img_pixel_values).any() or torch.isinf(img_pixel_values).any():
                    # Try to fix the tensor
                    img_pixel_values = torch.nan_to_num(img_pixel_values, nan=0.0, posinf=1.0, neginf=-1.0)

                pixel_values = img_pixel_values

            except Exception as e:
                st.error(f"Error processing image: {str(e)}")
                return f"Error processing image: {str(e)}. Please try a different image or check the image format."

    # Prepare conversation history
    history = None
    if len(messages) > 2:
        history = []
        for i in range(0, len(messages) - 1, 2):
            if i + 1 < len(messages):
                history.append([messages[i]['content'], messages[i+1]['content']])

    # Configure generation parameters - use safer values
    generation_config = {
        'max_new_tokens': max_length,
        'do_sample': temperature > 0.05,  # Only sample if temperature is meaningful
        'temperature': temperature,  # Ensure temperature isn't too close to zero
        'top_p': top_p,  # Keep top_p in a safer range
        'repetition_penalty': repetition_penalty  # Moderate repetition penalty
    }

    # Generate response with multiple fallback strategies
    try:
        with st.status("Generating response..."):
            # Get the user's question
            question = messages[-1]['content']

            # For image conversation, add <image> tags if not already present
            if pixel_values is not None and '<image>' not in question:
                question = '<image>\n' + question

            # Attempt generation with the specified parameters
            try:
                if history is None:
                    response = model.chat(tokenizer, pixel_values, question, generation_config)
                else:
                    response, _ = model.chat(tokenizer, pixel_values, question, generation_config,
                                       history=history, return_history=True)
            except RuntimeError as e:
                if "inf" in str(e) or "nan" in str(e) or "< 0" in str(e):
                    st.warning("Encountered probability error. Trying conservative settings...")

                    # Fallback to greedy decoding
                    fallback_config = {
                        'max_new_tokens': max_length,
                        'do_sample': False,  # Use greedy decoding
                        'num_beams': 1       # Simple beam search
                    }

                    if history is None:
                        response = model.chat(tokenizer, pixel_values, question, fallback_config)
                    else:
                        response, _ = model.chat(tokenizer, pixel_values, question, fallback_config,
                                        history=history, return_history=True)
                else:
                    raise e  # Re-raise other errors

            # Format and display response
            if isinstance(response, str) and len(response) > 0:
                # Handle potential formatting issues
                if ('\\[' in response and '\\]' in response) or ('\\(' in response and '\\)' in response):
                    response = response.replace('\\[', '$').replace('\\]', '$').replace('\\(', '$').replace('\\)', '$')

                placeholder.markdown(response)
            else:
                response = "Error: Model returned an empty response. Please try again with different parameters."
                placeholder.markdown(response)
    except Exception as e:
        error_msg = f"Error generating response: {str(e)}"
        st.error(error_msg)

        # Offer helpful suggestions based on the error
        if "CUDA out of memory" in str(e):
            response = f"{error_msg}\n\nSuggestion: Try using a smaller model or reducing the max_input_tiles parameter in advanced settings."
        elif "inf" in str(e) or "nan" in str(e) or "< 0" in str(e):
            response = f"{error_msg}\n\nSuggestion: Try reducing temperature to 0.1 and disabling sampling in advanced settings."
        else:
            response = f"{error_msg}\n\nSuggestion: Please try again with different parameters or a different image."

    return response

def find_bounding_boxes(response, messages):
    """Process bounding box annotations in the response"""
    pattern = re.compile(r'<ref>\s*(.*?)\s*</ref>\s*<box>\s*(\[\[.*?\]\])\s*</box>')
    matches = pattern.findall(response)
    results = []
    for match in matches:
        results.append((match[0], eval(match[1])))

    returned_image = None
    for message in messages:
        if message['role'] == 'user' and 'image' in message and len(message['image']) > 0:
            last_image = message['image'][-1]
            width, height = last_image.size
            returned_image = last_image.copy()
            draw = ImageDraw.Draw(returned_image)

    if not returned_image or not results:
        return None

    for result in results:
        line_width = max(1, int(min(width, height) / 200))
        random_color = (random.randint(0, 128), random.randint(0, 128), random.randint(0, 128))
        category_name, coordinates = result
        coordinates = [(float(x[0]) / 1000, float(x[1]) / 1000, float(x[2]) / 1000, float(x[3]) / 1000) for x in coordinates]
        coordinates = [(int(x[0] * width), int(x[1] * height), int(x[2] * width), int(x[3] * height)) for x in coordinates]

        for box in coordinates:
            # Draw rectangle and label
            draw.rectangle(box, outline=random_color, width=line_width)
            # Try to load a default font or use a simple font
            try:
                font = ImageFont.truetype('Arial.ttf', int(20 * line_width / 2))
            except:
                font = ImageFont.load_default()

            text_width, text_height = font.getsize(category_name) if hasattr(font, 'getsize') else (len(category_name) * 8, 12)
            text_position = (box[0], max(0, box[1] - text_height))

            # Background for text
            draw.rectangle(
                [text_position, (text_position[0] + text_width, text_position[1] + text_height)],
                fill=random_color
            )
            # Draw text
            draw.text(text_position, category_name, fill='white', font=font)

    return returned_image

def get_conv_log_filename():
    """Generate a filename for saving conversation logs"""
    t = datetime.datetime.now()
    name = os.path.join(LOGDIR, f'{t.year}-{t.month:02d}-{t.day:02d}-conv.json')
    return name

def save_chat_history(messages, model_name):
    """Save the conversation history to a log file"""
    if not messages:
        return

    new_messages = []
    for message in messages:
        new_message = {'role': message['role'], 'content': message['content']}
        if 'filenames' in message:
            new_message['filenames'] = message['filenames']
        new_messages.append(new_message)

    fout = open(get_conv_log_filename(), 'a')
    data = {
        'type': 'chat',
        'model': model_name,
        'messages': new_messages,
    }
    fout.write(json.dumps(data, ensure_ascii=False) + '\n')
    fout.close()

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
    """Load uploaded files and display them"""
    if uploaded_files is not None:
        images, filenames = [], []
        for file in uploaded_files:
            file_bytes = np.asarray(bytearray(file.read()), dtype=np.uint8)
            img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(img)

            # Resize the image if it exceeds 1 million pixels
            img = resize_image_to_max_pixels(img, max_pixels=1000000)

            images.append(img)

        # Generate filenames for saving
        image_hashes = [hashlib.md5(image.tobytes()).hexdigest() for image in images]
        for image, hash_val in zip(images, image_hashes):
            t = datetime.datetime.now()
            directory = os.path.join(LOGDIR, 'serve_images', f'{t.year}-{t.month:02d}-{t.day:02d}')
            os.makedirs(directory, exist_ok=True)
            filename = os.path.join(directory, f'{hash_val}.jpg')
            filenames.append(filename)
            if not os.path.isfile(filename):
                image.save(filename)

        return images, filenames
    return [], []

def show_one_or_multiple_images(message, total_image_num, lan='English', is_input=True):
    """Display images from a message"""
    if 'image' in message:
        if is_input:
            total_image_num = total_image_num + len(message['image'])
            if lan == 'English':
                if len(message['image']) == 1 and total_image_num == 1:
                    label = f"(In this conversation, {len(message['image'])} image was uploaded, {total_image_num} image in total)"
                elif len(message['image']) == 1 and total_image_num > 1:
                    label = f"(In this conversation, {len(message['image'])} image was uploaded, {total_image_num} images in total)"
                else:
                    label = f"(In this conversation, {len(message['image'])} images were uploaded, {total_image_num} images in total)"
            else:
                label = f"(在本次对话中，上传了{len(message['image'])}张图片，总共上传了{total_image_num}张图片)"

        # Display images
        upload_image_preview = st.empty()
        with upload_image_preview.container():
            Library(message['image'])

        if is_input and len(message['image']) > 0:
            st.markdown(label)

        return total_image_num
    return total_image_num

def main():
    # App title
    st.set_page_config(page_title='InternVL3 Demo', layout="wide")

    # Session state initialization
    if 'uploader_key' not in st.session_state:
        st.session_state.uploader_key = 0

    if 'needs_rerun' not in st.session_state:
        st.session_state.needs_rerun = False

    if 'messages' not in st.session_state.keys():
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

        lan = st.selectbox('Language / 语言', ['English', '中文'],
            help='This is only for switching the UI language.')

        if lan == 'English':
            system_message_default = 'I am InternVL3, a multimodal large language model developed by OpenGVLab.'
            system_message_editable = 'Please answer the user questions in detail.'
        else:
            system_message_default = '我是书生·万象，英文名是InternVL，是由上海人工智能实验室、清华大学及多家合作单位联合开发的多模态大语言模型。'
            system_message_editable = '请尽可能详细地回答用户的问题。'

        model_options = [
            "OpenGVLab/InternVL3-1B",
            "OpenGVLab/InternVL3-2B",
            "OpenGVLab/InternVL3-8B",
            "OpenGVLab/InternVL3-9B",
            "OpenGVLab/InternVL3-14B",
            "OpenGVLab/InternVL3-38B",
            "OpenGVLab/InternVL3-78B"
        ]
        model_path = st.selectbox("Model Selection", model_options, index=2,
                                 help="Select the InternVL3 model variant to use.")

        # Add this code after the model selection
        if 'current_model_path' not in st.session_state or st.session_state.current_model_path != model_path:
            # Explicitly unload the previous model if it exists
            if 'model' in st.session_state:
                try:
                    # Move model to CPU first if it's on GPU
                    if get_device() == "cuda":
                        st.session_state.model.to('cpu')

                    # Delete the model and tokenizer
                    del st.session_state.model
                    del st.session_state.tokenizer

                    # Force garbage collection
                    import gc; gc.collect()

                    # Clear CUDA cache if using GPU
                    if get_device() == "cuda":
                        torch.cuda.empty_cache()

                    st.info("Previous model unloaded successfully")
                except Exception as e:
                    st.warning(f"Error unloading previous model: {str(e)}")

            # Reset model-related session state
            st.session_state.pop('model', None)
            st.session_state.pop('tokenizer', None)
            st.session_state.current_model_path = model_path
            st.session_state.needs_rerun = True

        with st.expander('🤖 System Prompt'):
            system_message_editable = st.text_area('System Prompt', value=system_message_editable,
                                      help='System prompt is a message used to instruct the assistant.', height=100)

        with st.expander('🔥 Advanced Options'):
            temperature = st.slider('Temperature', min_value=0.0, max_value=1.0, value=0.3, step=0.01)
            top_p = st.slider('Top-p', min_value=0.0, max_value=1.0, value=0.9, step=0.01)
            repetition_penalty = st.slider('Repetition Penalty', min_value=1.0, max_value=1.5, value=1.1, step=0.01)
            max_length = st.slider('Max New Tokens', min_value=0, max_value=1024, value=512, step=8)
            max_input_tiles = st.slider('Max Input Tiles (controls image resolution)',
                min_value=1, max_value=24, value=12, step=1)

        # File uploader
        upload_image_preview = st.empty()
        uploaded_files = st.file_uploader('Upload files', accept_multiple_files=True,
                                         type=['png', 'jpg', 'jpeg', 'webp'],
                                         help='You can upload up to 4 images.',
                                         key=f'uploader_{st.session_state.uploader_key}')

        uploaded_pil_images, save_filenames = load_upload_file_and_show(uploaded_files)

        if len(uploaded_pil_images) > 0:
            with upload_image_preview.container():
                Library(uploaded_pil_images)

        # Clear history button
        clear_history = st.button('Clear Chat History')
        if clear_history:
            st.session_state.messages = []
            st.session_state.uploader_key += 1
            st.session_state.needs_rerun = True

    # Main content area
    st.title("InternVL3 Chat Demo")
    st.caption("A multimodal large language model for vision-language understanding")

    # Load the model on first run or if requested
    if 'model' not in st.session_state or 'tokenizer' not in st.session_state:
        # Check if we're loading a different model than before
        if 'current_model_path' in st.session_state and st.session_state.current_model_path != model_path:
            st.info(f"Switched from {st.session_state.current_model_path} to {model_path}")

        with st.spinner("Loading InternVL3 model... This may take a few minutes."):
            model, tokenizer = load_model(model_path)
            if model is not None and tokenizer is not None:
                st.session_state.model = model
                st.session_state.tokenizer = tokenizer
                st.session_state.current_model_path = model_path  # Save the current model path
                st.success("Model loaded successfully!")
            else:
                st.error("Failed to load model. Please check the model path and try again.")
                st.stop()

    # Initialize system prompt
    if len(st.session_state.messages) == 0:
        st.session_state.messages.append(
            {'role': 'system', 'content': system_message_default + '\n\n' + system_message_editable})

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
        if lan == 'English':
            prompt = st.chat_input('Too many images have been uploaded. Please clear the history.',
                                  disabled=input_disable_flag)
        else:
            prompt = st.chat_input('输入的图片太多了，请清空历史记录。', disabled=input_disable_flag)
    else:
        if lan == 'English':
            prompt = st.chat_input('Send messages to InternVL3')
        else:
            prompt = st.chat_input('发送信息给 InternVL3')

    # Handle new message
    if prompt:
        # Add user message to chat
        image_list = uploaded_pil_images
        st.session_state.messages.append(
            {'role': 'user', 'content': prompt, 'image': image_list, 'filenames': save_filenames})

        # Display user message
        with st.chat_message('user'):
            st.write(prompt)
            show_one_or_multiple_images(st.session_state.messages[-1], total_image_num, lan=lan, is_input=True)

        # Reset file uploader
        if image_list:
            st.session_state.uploader_key += 1
            st.session_state.needs_rerun = True

    # Generate response if last message is from user
    if len(st.session_state.messages) > 0 and st.session_state.messages[-1]['role'] == 'user':
        with st.chat_message('assistant'):
            with st.spinner('InternVL3 is thinking...'):
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
