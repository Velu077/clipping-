# ClipPulse

Paste a YouTube link (ended stream or video) -> get short vertical clips with captions -> edit -> export MP4.
Works in any phone or laptop browser (open the site, then "Add to Home Screen").

## Run locally
1. Install Python 3.10+ and ffmpeg (`brew install ffmpeg` / `sudo apt install ffmpeg fonts-dejavu-core`).
2. `pip install -r requirements.txt`
3. `uvicorn app:app --reload`
4. Open http://localhost:8000

## Deploy (so it works from your phone)
Push this folder to GitHub, then create a Web Service from the repo on Render, Railway or Fly.io (they detect the Dockerfile).
Use at least 1 GB RAM. Optional env vars: WHISPER_MODEL (tiny/base/small, default base), MAX_MINUTES (default 90).

## Notes
- Live streams are not supported yet: the app asks you to try again after the stream ends.
- YouTube often blocks downloads from cloud servers. If you see a "Sign in to confirm" error, run it locally or add cookies to yt-dlp.
- Keep yt-dlp updated (`pip install -U yt-dlp`); YouTube changes often.
- Only clip your own content or content you have permission to use.
- Data is stored in ./data and lost when a free host restarts.
