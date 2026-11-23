
import os
import uuid
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from faster_whisper import WhisperModel

app = FastAPI(title="ClipForge AI")

BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
STATIC = BASE / "static"

for folder in [UPLOADS, OUTPUTS]:
    folder.mkdir(exist_ok=True)

app.mount("/outputs", StaticFiles(directory=str(OUTPUTS)), name="outputs")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

jobs = {}

ALLOWED_DOMAINS = [
    "youtube.com", "youtu.be", "instagram.com",
    "tiktok.com", "facebook.com", "fb.watch",
    "twitter.com", "x.com", "vimeo.com"
]

def valid_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return any(
            host == domain or host.endswith("." + domain)
            for domain in ALLOWED_DOMAINS
        )
    except Exception:
        return False


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "No file selected")

    job_id = str(uuid.uuid4())
    extension = Path(file.filename).suffix or ".mp4"
    video_path = UPLOADS / f"{job_id}{extension}"

    size = 0
    with open(video_path, "wb") as output:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 500 * 1024 * 1024:
                video_path.unlink(missing_ok=True)
                raise HTTPException(413, "Video must be under 500 MB")
            output.write(chunk)

    jobs[job_id] = str(video_path)
    return {"job_id": job_id}


class URLRequest(BaseModel):
    url: str


@app.post("/api/upload-url")
def upload_url(data: URLRequest):
    url = data.url.strip()

    if not valid_url(url):
        raise HTTPException(400, "This video platform URL is not supported")

    job_id = str(uuid.uuid4())
    video_path = UPLOADS / f"{job_id}.mp4"

    options = {
        "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
        "outtmpl": str(video_path),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "max_filesize": 500 * 1024 * 1024,
        "socket_timeout": 30,
        "retries": 2,
        "quiet": True
    }

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([url])

        if not video_path.exists():
            candidates = list(UPLOADS.glob(f"{job_id}.*"))
            if not candidates:
                raise Exception("Downloaded video not found")
            video_path = candidates[0]

        jobs[job_id] = str(video_path)
        return {"job_id": job_id}

    except Exception as error:
        video_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not download video: {str(error)[:250]}")


@app.post("/api/generate/{job_id}")
def generate_clips(
    job_id: str,
    max_clips: int = 3,
    clip_length: int = 30,
    vertical: bool = True
):
    if job_id not in jobs:
        raise HTTPException(404, "Video job not found")

    video_path = Path(jobs[job_id])
    if not video_path.exists():
        raise HTTPException(404, "Video file not found")

    try:
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, info = model.transcribe(str(video_path), beam_size=1)

        transcript = list(segments)
        if not transcript:
            raise Exception("No speech detected in video")

        duration = max(float(s.end) for s in transcript)
        candidates = []

        for segment in transcript:
            text = segment.text.strip()
            if len(text.split()) >= 5:
                score = len(text.split())
                keywords = [
                    "wow", "amazing", "crazy", "secret",
                    "important", "love", "money", "why",
                    "how", "best", "never", "mistake"
                ]
                score += sum(word in text.lower() for word in keywords) * 5
                candidates.append((score, float(segment.start), text))

        candidates.sort(reverse=True)

        clips = []
        used_times = []

        for score, start, text in candidates:
            if len(clips) >= max(1, min(max_clips, 10)):
                break

            if any(abs(start - old) < clip_length for old in used_times):
                continue

            end = min(start + clip_length, duration)
            if end - start < 5:
                continue

            clip_id = str(uuid.uuid4())[:8]
            output_path = OUTPUTS / f"{clip_id}.mp4"

            command = ["ffmpeg", "-y", "-ss", str(start),
                       "-i", str(video_path), "-t", str(end - start)]

            if vertical:
                command += [
                    "-vf",
                    "scale=1080:1920:force_original_aspect_ratio=increase,"
                    "crop=1080:1920"
                ]

            command += [
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-movflags", "+faststart",
                str(output_path)
            ]

            subprocess.run(
                command, check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )

            clips.append({
                "title": text[:70] or "AI Highlight",
                "start": round(start, 1),
                "end": round(end, 1),
                "url": f"/outputs/{clip_id}.mp4"
            })
            used_times.append(start)

        if not clips:
            raise Exception("Could not find suitable clips")

        return {"clips": clips}

    except Exception as error:
        raise HTTPException(500, detail=str(error)[:300])
        
