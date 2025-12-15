# Install required packages
# pip install yt_dlp mutagen

import yt_dlp
import mutagen
import os
from mutagen.id3 import ID3, COMM, TALB, TPE1, TRCK, TIT2, TYER, TCON, TBPM
import subprocess
import json

# YouTube URL of the album
url = "https://www.youtube.com/watch?v=eHgktVIHLYM"

# Album information
album_info = {
    "album": "Apsara",
    "artist": "Various Artists",
    "genre": "Goa Trance",
    "year": "2005",
    "tracks": [
        {"time": "00:00:00", "title": "Digitalys", "artist": "Aes Dana", "producer": "Vincent Villuis", "bpm": "120"},
        {"time": "00:06:38", "title": "On The Edge Of Time", "artist": "Yesod", "producer": "Fredrik Ekholm", "bpm": "142"},
        {"time": "00:13:12", "title": "Babylone Beach", "artist": "Avigmati", "producer": "Matthieu Chamoux", "collaborator": "DJ Chaï", "bpm": "145"},
        {"time": "00:20:54", "title": "Metamorphosis", "artist": "Lost Buddha", "producer": "Filipe Santos", "bpm": "139"},
        {"time": "00:28:35", "title": "Tiny Universe", "artist": "Filteria", "producer": "Jannis Tzikas", "bpm": "143"},
        {"time": "00:37:18", "title": "Scraqp", "artist": "Ka Sol", "producer": "Christer Lundström", "bpm": "142"},
        {"time": "00:45:14", "title": "Titanium", "artist": "Ypsilon 5", "producer": "David Lilja", "bpm": "148"},
        {"time": "00:54:23", "title": "I'm Ready", "artist": "Goasia", "producer": "Balint Tihamer", "bpm": "144"},
        {"time": "01:01:49", "title": "Communication!", "artist": "Radical Distortion", "producer": "John Spanos, Nick Polytaridis", "bpm": "136"}
    ]
}

# Calculate end times for each track
for i in range(len(album_info["tracks"])):
    start_time = album_info["tracks"][i]["time"]
    if i < len(album_info["tracks"]) - 1:
        end_time = album_info["tracks"][i+1]["time"]
    else:
        # For the last track, we'll need to get this from the video duration later
        end_time = None

    # Convert time format for ffmpeg
    album_info["tracks"][i]["start_time"] = start_time
    album_info["tracks"][i]["end_time"] = end_time

# Function to check if cache files exist
def check_cache(video_id):
    expected_mp3 = f"V.A. - Apsara (Full Mix) [{video_id}].mp3"
    expected_info = f"V.A. - Apsara (Full Mix) [{video_id}].info.json"

    mp3_exists = os.path.exists(expected_mp3)
    info_exists = os.path.exists(expected_info)

    return mp3_exists and info_exists, expected_mp3, expected_info

# Function to convert timestamp to seconds
def timestamp_to_seconds(timestamp):
    parts = timestamp.split(':')
    if len(parts) == 3:  # HH:MM:SS
        hours, minutes, seconds = map(int, parts)
        return hours * 3600 + minutes * 60 + seconds
    elif len(parts) == 2:  # MM:SS
        minutes, seconds = map(int, parts)
        return minutes * 60 + seconds

# Function to add ID3 tags directly
def add_id3_tags(file_path, track_info, track_num):
    # Remove any existing tags
    try:
        id3 = ID3(file_path)
        id3.delete()
    except:
        pass

    # Create new ID3 tag
    id3 = ID3()

    # Add basic tags
    id3.add(TIT2(encoding=3, text=track_info['title']))
    id3.add(TPE1(encoding=3, text=track_info['artist']))
    id3.add(TALB(encoding=3, text=album_info['album']))
    id3.add(TYER(encoding=3, text=album_info['year']))
    id3.add(TCON(encoding=3, text=album_info['genre']))
    id3.add(TRCK(encoding=3, text=str(track_num)))

    # Add BPM if available
    if 'bpm' in track_info:
        id3.add(TBPM(encoding=3, text=track_info['bpm']))

    # Add producer info as comment
    if 'producer' in track_info:
        id3.add(COMM(
            encoding=3,
            lang='eng',
            desc='Producer',
            text=track_info['producer']
        ))

    # Add collaborator info if available
    if 'collaborator' in track_info:
        id3.add(COMM(
            encoding=3,
            lang='eng',
            desc='Collaborator',
            text=track_info['collaborator']
        ))

    # Save tags
    id3.save(file_path)
    print(f"Added tags to {os.path.basename(file_path)}")

def main():
    # Extract video ID from URL
    video_id = url.split("v=")[-1].split("&")[0]

    # Check if we have cached files
    cache_exists, mp3_filename, info_filename = check_cache(video_id)

    if cache_exists:
        print(f"Using cached files: {mp3_filename}")
    else:
        print("Starting download of Apsara compilation...")

        # Download options
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
            'writeinfojson': True,
            'outtmpl': f'V.A. - Apsara (Full Mix) [%(id)s].%(ext)s',
        }

        # Download the album
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

    # Load info from json file if it exists
    if os.path.exists(info_filename):
        with open(info_filename, 'r') as f:
            info_data = json.load(f)

        # Get the duration for the last track's end time
        duration = info_data.get('duration')
        if duration and album_info["tracks"][-1]["end_time"] is None:
            hours, remainder = divmod(duration, 3600)
            minutes, seconds = divmod(remainder, 60)
            album_info["tracks"][-1]["end_time"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    print("Starting to split tracks...")

    # Create output directory
    output_dir = "Apsara (2005)"
    os.makedirs(output_dir, exist_ok=True)

    # Split and tag each track
    for i, track in enumerate(album_info["tracks"]):
        track_num = i + 1
        output_file = os.path.join(output_dir, f"{track_num:02d}. {track['artist']} - {track['title']}.mp3")

        # Skip if file already exists and force_retag is False
        if os.path.exists(output_file):
            print(f"Track {track_num} already exists: {output_file}")
            # Retag existing files to ensure consistent tagging
            add_id3_tags(output_file, track, track_num)
            continue

        start_seconds = timestamp_to_seconds(track["start_time"])
        if track["end_time"]:
            end_seconds = timestamp_to_seconds(track["end_time"])
            duration = end_seconds - start_seconds
        else:
            # Use None for end_time to copy until the end of the file
            duration = None

        print(f"Extracting track {track_num}: {track['artist']} - {track['title']}")

        # Split using ffmpeg
        if duration:
            ffmpeg_cmd = [
                'ffmpeg', '-i', mp3_filename,
                '-ss', str(start_seconds), '-t', str(duration),
                '-c:a', 'libmp3lame', '-q:a', '0', '-y', output_file
            ]
        else:
            ffmpeg_cmd = [
                'ffmpeg', '-i', mp3_filename,
                '-ss', str(start_seconds),
                '-c:a', 'libmp3lame', '-q:a', '0', '-y', output_file
            ]

        subprocess.run(ffmpeg_cmd)

        # Add tags
        add_id3_tags(output_file, track, track_num)
    
    print(f"Album processing complete! All tracks saved to {output_dir} directory")

if __name__ == "__main__":
    main()
