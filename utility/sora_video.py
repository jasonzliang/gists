import argparse
import time
import os
import sys
import traceback

from openai import OpenAI

def calculate_cost(model, size, seconds):
    """Calculates cost based on 2026 Sora 2 pricing."""
    duration = int(seconds)
    if model == "sora-2":
        return duration * 0.10
    if model == "sora-2-pro":
        if "1792" in size or "1024" in size:
            return duration * 0.50
        return duration * 0.30
    return 0.0

def refine_prompt(client, original_prompt, reason="proactive"):
    """Uses GPT-5.2 to rewrite prompts into safe, cinematic language."""
    print(f"🔄 [{reason.upper()}] Refining prompt with GPT-5.2...")
    try:
        completion = client.chat.completions.create(
            model="gpt-5.2",
            messages=[
                {"role": "system", "content": "You are a cinematic prompt engineer. Rewrite video prompts to be safe for Sora 2 so it will not be rejected by automated moderation checks. Remove brand names, celebrities, and gore. Use high-end cinematic descriptions while keeping original artistic intent."},
                {"role": "user", "content": f"Refine this prompt: '{original_prompt}'."}
            ]
        )
        refined = completion.choices[0].message.content
        print(f"✨ New Prompt: \"{refined}\"")
        return refined
    except Exception as e:
        print(f"⚠️ Prompt refinement failed: {e}")
        return original_prompt

def download_video(client, video_id, filename):
    """Helper function to handle video downloading safely."""
    print(f"⬇️  Downloading video {video_id} to {filename}...")
    try:
        response = client.videos.download_content(video_id)

        with open(filename, "wb") as f:
            if hasattr(response, 'iter_bytes'):
                for chunk in response.iter_bytes(chunk_size=8192):
                    f.write(chunk)
            elif hasattr(response, 'read'):
                f.write(response.read())
            else:
                f.write(response)

        print(f"🎉 Success! Video saved as {filename}")
    except Exception as e:
        print(f"🚨 Download Error: {e}")
        sys.exit(1)

def get_error_details(job_error):
    """Safely extracts code and message whether job.error is a dict or object."""
    if not job_error:
        return "unknown", "No details provided."

    # Check if it's an object (Pydantic model)
    if hasattr(job_error, 'code'):
        return job_error.code, getattr(job_error, 'message', str(job_error))

    # Check if it's a dictionary
    if isinstance(job_error, dict):
        return job_error.get('code', 'unknown'), job_error.get('message', str(job_error))

    return "unknown", str(job_error)

def main():
    parser = argparse.ArgumentParser(description="Proactive Sora 2 Video Generator (Fixed Error Handling)")

    parser.add_argument("--prompt", type=str, help="Text description of the video to generate")
    parser.add_argument("--size", type=str, default="720x1280", choices=["720x1280", "1280x720", "1024x1792", "1792x1024"])
    parser.add_argument("--seconds", type=str, default="8", choices=["4", "8", "12"])
    parser.add_argument("--model", type=str, default="sora-2", choices=["sora-2", "sora-2-pro"])
    parser.add_argument("--output", type=str, default="sora_video.mp4")

    parser.add_argument("--auto-refine", action="store_true", dest="auto_refine")
    parser.add_argument("--manual-refine", action="store_true", dest="manual_refine")
    parser.add_argument("--video-id", type=str, dest="video_id", help="Skip generation and download an existing video ID")

    args = parser.parse_args()
    client = OpenAI()

    # --- PATH 1: Direct Download ---
    if args.video_id:
        print(f"📂 Mode: Download Existing Video")
        download_video(client, args.video_id, args.output)
        return

    # --- PATH 2: Generate New Video ---
    if not args.prompt:
        print("🚨 Error: --prompt is required unless --video-id is specified.")
        sys.exit(1)

    current_prompt = args.prompt

    if args.manual_refine:
        current_prompt = refine_prompt(client, current_prompt, reason="proactive manual check")

    max_retries = 1 if args.auto_refine else 0
    attempt = 0

    while attempt <= max_retries:
        est_cost = calculate_cost(args.model, args.size, args.seconds)
        print("-" * 50)
        print(f"🚀 Attempt {attempt + 1} | {args.model} | {args.size} | {args.seconds}s | Est: ${est_cost:.2f}")
        print(f"📝 Prompt: \"{current_prompt}\"")
        print("-" * 50)

        try:
            video_job = client.videos.create(
                model=args.model,
                prompt=current_prompt,
                size=args.size,
                seconds=args.seconds
            )
            video_id = video_job.id
            print(f"✅ Job Created! ID: {video_id}")

            while True:
                job = client.videos.retrieve(video_id)
                status = job.status
                progress = getattr(job, 'progress', 0)

                if status == "completed":
                    print(f"\n✨ Generation complete!")
                    download_video(client, video_id, args.output)
                    print(f"💰 Final Estimated Bill: ${est_cost:.2f}")
                    return

                elif status == "failed":
                    # --- FIXED ERROR EXTRACTION ---
                    error_info = getattr(job, 'error', None)
                    error_code, error_msg = get_error_details(error_info)

                    print(f"\n🚨 FAILED: {error_code}")

                    if args.auto_refine and attempt < max_retries:
                        current_prompt = refine_prompt(client, current_prompt, reason=error_msg)
                        attempt += 1
                        break
                    else:
                        print(f"❌ Critical Failure: {error_msg}")
                        sys.exit(1)
                else:
                    print(f"⏳ Progress: {progress}% | Status: {status}", end="\r")
                    time.sleep(5)

        except Exception as e:
            if "policy" in str(e).lower() and args.auto_refine and attempt < max_retries:
                current_prompt = refine_prompt(client, current_prompt, reason="initial prompt rejection")
                attempt += 1
                continue
            print(f"🚨 Fatal Error: {e}")
            traceback.print_exc()
            sys.exit(1)

if __name__ == "__main__":
    main()