import os, re, uuid, shutil, subprocess
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

app = FastAPI(title="ClipForge AI", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

ALLOWED = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
jobs = {}

def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout)[-2500:])
    return p.stdout

def duration_of(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(out.strip())

def transcribe(path):
    try:
        from faster_whisper import WhisperModel
        model_name = os.getenv("WHISPER_MODEL", "small")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, info = model.transcribe(str(path), vad_filter=True, word_timestamps=False)
        return [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()} for s in segments]
    except ImportError:
        raise RuntimeError("AI transcription is not installed. Install faster-whisper (see README), or use demo mode.")
    except Exception as e:
        raise RuntimeError(f"Transcription failed: {e}")

HOOKS = [
    "but", "why", "how", "secret", "mistake", "never", "always", "best", "worst",
    "crazy", "insane", "imagine", "actually", "truth", "important", "problem",
    "because", "finally", "money", "win", "lose", "shocking", " कहानी", "लेकिन",
    "क्यों", "कैसे", "सच", "गलती", "जिंदगी", "पैसा", "भाई", "देखो"
]

def pick_highlights(segments, total, max_clips=5, clip_len=35):
    if not segments:
        return []
    candidates = []
    for i, seg in enumerate(segments):
        start = max(0, seg["start"] - 2.0)
        end = min(total, max(seg["end"] + 8.0, start + 15.0))
        # Extend around nearby speech, while respecting requested max duration.
        end = min(end, start + clip_len)
        text = seg["text"].lower()
        score = min(len(text) / 90, 2.0) + sum(1.2 for h in HOOKS if h in text)
        score += 0.4 if "?" in text or "!" in text else 0
        if end - start >= 8:
            candidates.append({"start": start, "end": end, "score": round(score, 2),
                               "title": (seg["text"][:75] or "AI highlight").strip()})
    candidates.sort(key=lambda x: x["score"], reverse=True)
    chosen = []
    for c in candidates:
        if all(c["end"] <= x["start"] or c["start"] >= x["end"] for x in chosen):
            chosen.append(c)
        if len(chosen) >= max_clips:
            break
    return sorted(chosen, key=lambda x: x["start"])

def make_clip(source, item, output, vertical=True):
    start, end = item["start"], item["end"]
    if vertical:
        vf = "scale=ih*9/16:ih:force_original_aspect_ratio=increase,crop=ih*9/16:ih,scale=1080:1920"
    else:
        vf = "scale=1920:-2"
    run(["ffmpeg", "-y", "-ss", str(start), "-i", str(source), "-t", str(end-start),
         "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output)])

@app.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")

@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, "Please upload MP4, MOV, MKV, WebM or M4V.")
    job_id = uuid.uuid4().hex[:12]
    dest = UPLOADS / f"{job_id}{ext}"
    size = 0
    with dest.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 2 * 1024 * 1024 * 1024:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "Maximum upload size is 2 GB.")
            f.write(chunk)
    jobs[job_id] = {"status": "uploaded", "filename": file.filename, "path": str(dest), "clips": [], "error": None}
    return {"job_id": job_id, "filename": file.filename, "size": size}

@app.post("/api/generate/{job_id}")
def generate(job_id: str, max_clips: int = 5, clip_length: int = 35, vertical: bool = True):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Upload not found. Upload the video again.")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise HTTPException(500, "FFmpeg and FFprobe are required. See README.")
    try:
        job["status"] = "transcribing"
        total = duration_of(job["path"])
        segments = transcribe(job["path"])
        highlights = pick_highlights(segments, total, max(1, min(max_clips, 10)), max(10, min(clip_length, 90)))
        if not highlights:
            raise RuntimeError("No suitable speech highlights found in this video.")
        job["status"] = "rendering"
        clips = []
        for idx, item in enumerate(highlights, 1):
            name = f"{job_id}_clip_{idx}.mp4"
            output = OUTPUTS / name
            make_clip(job["path"], item, output, vertical)
            clips.append({"name": name, "title": item["title"], "start": round(item["start"],1),
                          "end": round(item["end"],1), "score": item["score"],
                          "url": f"/api/download/{name}"})
        job["clips"] = clips
        job["status"] = "done"
        return {"job_id": job_id, "status": "done", "clips": clips}
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        raise HTTPException(500, str(e))

@app.get("/api/download/{name}")
def download(name: str):
    # Restrict paths to generated mp4 files.
    if Path(name).name != name or not name.endswith(".mp4"):
        raise HTTPException(400, "Invalid filename.")
    path = OUTPUTS / name
    if not path.exists():
        raise HTTPException(404, "Clip not found.")
    return FileResponse(path, media_type="video/mp4", filename=name)

@app.get("/api/health")
def health():
    return {"ok": True, "ffmpeg": shutil.which("ffmpeg") is not None}
