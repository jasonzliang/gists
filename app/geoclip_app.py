import io
import os
import sys
import types
import tempfile
import time
import traceback
import hmac
import base64

import torch
import numpy as np
import folium
from geoclip import GeoCLIP
from geopy.geocoders import Nominatim
from PIL import Image
import pillow_heif
import pillow_avif
import streamlit as st
from streamlit_folium import folium_static

# Try to fix an error
dummy_module = types.ModuleType("dummy_classes")
dummy_module.__path__ = []
sys.modules["torch.classes"] = dummy_module

# Register the HEIF file format plugin
pillow_heif.register_heif_opener()

st.set_page_config(
    page_title="GeoCLIP Location Predictor",
    page_icon="🌍",
    layout="wide"
)

# Add these authentication functions after the imports and before the page config
def check_password():
    """Returns `True` if the user had the correct password."""

    # Return true if password is not set
    try:
        assert "password" in st.secrets
    except st.errors.StreamlitSecretNotFoundError as e:
        return True

    # Return True if the password is validated
    if st.session_state.get("password_correct", False):
        return True

    # Show input for password
    st.title("🔒 Moondream2 Image Analysis")
    st.write("Please enter the password to access this app")

    # Create password field and button
    password = st.text_input("Password", type="password", key="password_input")
    login_button = st.button("Login")

    if login_button:
        if hmac.compare_digest(password, st.secrets["password"]):
            st.session_state["password_correct"] = True
            st.rerun()
            return True
        else:
            st.error("😕 Incorrect password. Please try again.")

    return False

# Cache the model to avoid reloading it for each prediction
@st.cache_resource
def load_geoclip_model():
    return GeoCLIP()

def resolve_lat_lon(lat, lon, prob, geolocator=None):
    # Initialize a geocoder (Nominatim uses OpenStreetMap data)
    if geolocator is None:
        geolocator = Nominatim(user_agent="geoclip_streamlit_app")

    result = {
        "coordinates": (lat, lon),
        "probability": prob * 100,
        "address": "Could not determine address"
    }

    # Convert the coordinates to an address
    try:
        location = geolocator.reverse(f"{lat}, {lon}", language="en")
        if location:
            result["address"] = location.address
    except Exception as e:
        result["error"] = str(e)

    return result

def geoclip_predict(image_path, top_k=5):
    assert os.path.exists(image_path)
    assert top_k > 0

    # Initialize the GeoCLIP model
    try:
        model = load_geoclip_model()

        top_pred_gps, top_pred_prob = model.predict(image_path, top_k=top_k)
        assert top_k == len(top_pred_gps) == len(top_pred_prob)

        results = []

        # Process individual predictions
        for i in range(top_k):
            lat, lon = top_pred_gps[i]
            prob = top_pred_prob[i]

            # Convert tensors to numpy if needed
            if hasattr(lat, 'numpy'):
                lat = lat.numpy()
            if hasattr(lon, 'numpy'):
                lon = lon.numpy()
            if hasattr(prob, 'numpy'):
                prob = prob.numpy()

            result = resolve_lat_lon(lat, lon, prob)
            result["rank"] = i + 1
            results.append(result)

        # Convert tensors to numpy if needed
        lats = []
        lons = []
        probs = []

        for x, y in top_pred_gps:
            lats.append(x.numpy() if hasattr(x, 'numpy') else x)
            lons.append(y.numpy() if hasattr(y, 'numpy') else y)

        for p in top_pred_prob:
            probs.append(p.numpy() if hasattr(p, 'numpy') else p)

        avg_result = resolve_lat_lon(np.mean(lats), np.mean(lons), np.mean(probs))
        avg_result["rank"] = "Average"

        results.append(avg_result)
        return results

    except ImportError:
        st.error("GeoCLIP is not installed. Please install it with: pip install geoclip")
        st.stop()
    except Exception as e:
        st.error(f"Error during prediction: {str(e)}")
        st.info("Make sure GeoCLIP is properly installed and compatible with your Python version.")
        raise

def create_map(results):
    # Create a map centered on the average prediction
    avg_result = results[-1]  # The last result is the average
    map_center = avg_result["coordinates"]

    m = folium.Map(location=map_center, zoom_start=4)

    # Add markers for each prediction
    for result in results:
        lat, lon = result["coordinates"]
        rank = result["rank"]
        prob = result["probability"]

        if rank == "Average":
            # Highlight the average prediction
            folium.Marker(
                location=[lat, lon],
                popup=f"Average Prediction<br>Probability: {prob:.4f}%<br>Address: {result['address']}",
                icon=folium.Icon(color="red", icon="info-sign"),
            ).add_to(m)
        else:
            # Regular prediction markers
            folium.Marker(
                location=[lat, lon],
                popup=f"Rank {rank} Prediction<br>Probability: {prob:.4f}%<br>Address: {result['address']}",
                icon=folium.Icon(color="blue"),
            ).add_to(m)

    return m

def convert_image(uploaded_file):
    """Convert various image formats to a format compatible with GeoCLIP."""
    # Get file extension (lowercased)
    file_ext = os.path.splitext(uploaded_file.name)[1].lower()

    # Read file bytes
    file_bytes = uploaded_file.getvalue()

    # Handle HEIC/HEIF formats
    if file_ext in ['.heic', '.heif']:
        try:
            heif_file = pillow_heif.read_heif(file_bytes)
            image = Image.frombytes(
                heif_file.mode,
                heif_file.size,
                heif_file.data,
                "raw",
                heif_file.mode,
                heif_file.stride,
            )
        except Exception as e:
            st.error(f"Error processing HEIC/HEIF image: {e}")
            st.info("Make sure pillow_heif is properly installed: pip install pillow-heif")
            raise
    else:
        # Handle other formats (WebP, AVIF, regular formats)
        # For AVIF: pip install pillow-avif-plugin
        try:
            image = Image.open(io.BytesIO(file_bytes))
        except Exception as e:
            st.error(f"Error opening image: {e}")
            st.info("If you're trying to open an AVIF image, make sure your Pillow installation has AVIF support installed: pip install pillow-avif-plugin")
            raise

    # Convert to RGB if needed (in case of RGBA or other formats)
    if image.mode != 'RGB':
        image = image.convert('RGB')

    # Save to temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
        image.save(tmp_file.name, format="JPEG", quality=95)
        return tmp_file.name

def main():
    # Check for authentication first
    if not check_password():
        return

    # Set headers for file upload requests
    st.markdown("""
    <style>
    /* CSS to help with CORS issues */
    </style>
    """, unsafe_allow_html=True)

    st.title("📍 GeoCLIP Location Predictor")
    st.write("Upload an image to predict its geographical location using GeoCLIP")

    # Check if required packages are installed
    try:
        import importlib.util
        if importlib.util.find_spec("geoclip") is None:
            st.error("GeoCLIP package is not installed. Please install it with: pip install geoclip")
            st.info("Run this command in your terminal or command prompt: `pip install geoclip`")
            st.stop()
    except Exception as e:
        st.error(f"Error checking for GeoCLIP package: {e}")

    col1, col2 = st.columns([1, 1])

    with col1:
        uploaded_file = st.file_uploader("Choose an image...",
                                         type=["jpg", "jpeg", "png", "heic", "heif", "webp", "avif"])
        top_k = st.slider("Number of predictions (top-k)", min_value=1, max_value=10, value=5)

        if uploaded_file is not None:
            # Display the uploaded image
            st.image(uploaded_file, caption="Uploaded Image", use_container_width=True)

            # Convert and save the image to a temporary file
            try:
                temp_path = convert_image(uploaded_file)
                st.success(f"Successfully processed {uploaded_file.name}")
            except Exception as e:
                st.error(f"Error processing image: {e}")
                st.stop()

            # Make predictions when user clicks the button
            if st.button("Predict Location"):
                with st.spinner("Analyzing image location..."):
                    try:
                        results = geoclip_predict(temp_path, top_k)

                        # Clean up the temporary file
                        os.unlink(temp_path)

                        # Create and display the map
                        with col2:
                            st.subheader("Predicted Locations")
                            map_obj = create_map(results)
                            folium_static(map_obj, width=600)

                        # Display detailed results
                        st.subheader("Detailed Predictions")
                        for result in results[:-1]:  # All except the average
                            lat, lon = result["coordinates"]
                            st.markdown(f"""
                            **Rank {result['rank']} Prediction**
                            - Coordinates: ({lat:.6f}, {lon:.6f})
                            - Probability: {result['probability']:.4f}%
                            - Address: {result['address']}
                            """)

                        # Display average prediction
                        avg_result = results[-1]
                        lat, lon = avg_result["coordinates"]
                        st.markdown(f"""
                        **Average Prediction (from all {top_k} predictions)**
                        - Coordinates: ({lat:.6f}, {lon:.6f})
                        - Probability: {avg_result['probability']:.4f}%
                        - Address: {avg_result['address']}
                        """)

                    except Exception as e:
                        st.error(f"Error processing image: {e}")

    # Footer
    st.markdown("---")
    st.caption("GeoCLIP Location Predictor - Powered by GeoCLIP and Streamlit")

    # Add information about supported image formats
    with st.expander("Supported Image Formats"):
        st.markdown("""
        This app supports the following image formats:
        - JPEG (.jpg, .jpeg)
        - PNG (.png)
        - HEIC/HEIF (.heic, .heif) - Apple's High Efficiency Image Format
        - WebP (.webp) - Google's image format for the web
        - AVIF (.avif) - AV1 Image File Format

        All images are automatically converted to JPEG format for processing with GeoCLIP.
        """)

if __name__ == "__main__":
    # streamlit run geoclip_app.py --server.port 10000 --server.headless true --browser.gatherUsageStats false
    try:
        main()
    except Exception as e:
        st.error(f"An unexpected error occurred: {e}")
        st.info("Please check that all required packages are installed:\n```\npip install streamlit geoclip geopy folium streamlit-folium pillow pillow-heif pillow-avif-plugin\n```")
