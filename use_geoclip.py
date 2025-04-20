#!/usr/bin/env python
import os
import sys
import time

import numpy as np
from geoclip import GeoCLIP
from geopy.geocoders import Nominatim

def resolve_lat_lon(lat, lon, prob, geolocator=None):
    # Initialize a geocoder (Nominatim uses OpenStreetMap data)
    if geolocator is None:
        geolocator = Nominatim(user_agent="geoclip_app")

    print(f"Coordinates: ({lat:.6f}, {lon:.6f})")
    print(f"Probability: {prob*100:.4f}%")

    # Convert the coordinates to an address
    try:
        location = geolocator.reverse(f"{lat}, {lon}", language="en")
        if location:
            address = location.address
            print(f"Address: {address}")
        else:
            print("Address: Could not determine address")
    except Exception as e:
        print(f"Error getting address: {e}")

def geoclip(image_path, top_k=5):
    assert os.path.exists(image_path); assert top_k > 0

    # Initialize the GeoCLIP model
    model = GeoCLIP()
    top_pred_gps, top_pred_prob = model.predict(image_path, top_k=top_k)
    assert top_k == len(top_pred_gps) == len(top_pred_prob)

    print("=====================")
    print("Top %d GPS predictions" % top_k)
    print("=====================")
    for i in range(top_k):
        # print("Rank %d prediction" % (i+1))
        lat, lon = top_pred_gps[i]; prob = top_pred_prob[i]
        resolve_lat_lon(lat, lon, prob); print("")

    print(""); print("Average from all %d predictions" % top_k)
    lats = [x.numpy() for x, y in top_pred_gps]
    lons = [y.numpy() for x, y in top_pred_gps]
    probs = [x.numpy() for x in top_pred_prob]
    resolve_lat_lon(np.mean(lats), np.mean(lons), np.mean(probs))

if __name__ == "__main__":
    if len(sys.argv) == 2:
        geoclip(sys.argv[1])
    elif len(sys.argv) == 3:
        geoclip(sys.argv[1], int(sys.argv[2]))
    else:
        print("Usage: ./use_geoclip.py [image path] [opt: top-k pred]")
