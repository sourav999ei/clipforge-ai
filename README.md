# ClipForge AI — self-hosted MVP

A starter AI clipping website: upload a long video, transcribe speech with Whisper, rank candidate moments with a simple hook/length heuristic, and render separate MP4 clips with FFmpeg.

## Important MVP limitations

- This is a functional **starter**, not a finished OpusClip competitor.
- Highlight ranking is heuristic and transcript-based; it does not guarantee viral clips.
- Current export center-crops to 9:16. Smart face tracking, animated captions, accounts, payments, cloud storage and a production job queue are not included yet.
- Whisper runs locally on the server. First run downloads a model and can be slow, especially on a free CPU host.
- Keep uploads private and set sensible limits before opening this to public users.

## Run locally

Requirements: Python 3.10+, FFmpeg and FFprobe installed and available on PATH.

### Windows
1. Install FFmpeg and add its `bin` folder to PATH.
2. Open PowerShell in this folder:
   ```powershell
   py -m venv .venv
   .venv\\Scripts\\Activate.ps1
   pip install -r requirements.txt
   $env:WHISPER_MODEL="tiny"
   uvicorn app:app --reload
   ```
3. Open http://127.0.0.1:8000

### macOS
```bash
brew install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
WHISPER_MODEL=tiny uvicorn app:app --reload
```

### Linux
```bash
sudo apt-get update && sudo apt-get install -y ffmpeg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
WHISPER_MODEL=tiny uvicorn app:app --reload
```

## Deploy with Docker

Build and run:
```bash
docker build -t clipforge-ai .
docker run --rm -p 8000:8000 -v clipforge_uploads:/app/uploads -v clipforge_outputs:/app/outputs clipforge-ai
```
Then open port 8000 on your host.

Set `WHISPER_MODEL=tiny` for lower memory usage. `base` or `small` may improve transcription quality but need more RAM and processing time.

## API

- `GET /api/health` — basic health check
- `POST /api/upload` — multipart form field `file`
- `POST /api/generate/{job_id}?max_clips=5&clip_length=35&vertical=true`
- `GET /api/download/{filename}` — download generated MP4

## Public launch checklist

Before public use, add authentication, per-user storage isolation, rate limits, background workers, upload virus scanning, automatic cleanup, HTTPS, privacy policy, abuse reporting and resource quotas. Do not deploy this single-process in-memory job registry as a multi-user production service.

## Copyright & consent

Only upload videos you own or have permission to edit and distribute. Respect privacy, publicity rights, platform terms and music/content licenses.
