"""Bounded upload tools and actionable download diagnostics for WaveRise."""
import functools
import asyncio
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
from fastapi import File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

MAX_SECONDS = 180
SIZES = {"vertical": (720, 1280), "square": (720, 720), "landscape": (1280, 720), "portrait": (720, 900)}
LANGUAGES = {"auto", "en", "te", "hi", "ta"}
LOCAL_INPUT = ["-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm,avi,mpeg,mpegts"]
_processing = asyncio.Semaphore(2)


async def bounded_work(function, *arguments):
    from starlette.concurrency import run_in_threadpool
    async with _processing:
        return await run_in_threadpool(function, *arguments)


def limit_request(request, speech=False):
    from wr_admin import client_ip, rate_limit
    rate_limit("creator:" + client_ip(request), 6, 600)
    if speech:
        rate_limit("speech:" + client_ip(request), 3, 600)
        rate_limit("speech:daily", int(os.getenv("SUBTITLE_DAILY_LIMIT", "100")), 86400)


def upload_limit():
    # Vercel has a 4.5 MB function request limit, including multipart overhead.
    return 4 * 1024 * 1024 if os.getenv("VERCEL") else 50 * 1024 * 1024


def diagnose(status, message):
    """Classify the existing public error without making claims about a private post."""
    text = str(message).lower()
    if status == 451:
        code, title, action, retry = "restricted", "This link is unavailable", "Try a different post.", False
    elif status == 429 or any(x in text for x in ("limiting", "too many", "rate limit")):
        code, title, action, retry = "rate_limited", "The platform is limiting requests", "Wait a few minutes before retrying.", True
    elif any(x in text for x in ("timeout", "timed out", "took too long")) or status == 504:
        code, title, action, retry = "timeout", "The request took too long", "Try again, or choose a shorter video.", True
    elif "coming soon" in text:
        code, title, action, retry = "unsupported_platform", "This platform is not ready yet", "Use a public Instagram, Facebook or X post.", False
    elif "media link expired" in text:
        code, title, action, retry = "media_expired", "The media link is no longer working", "Retry to refresh the link. If it keeps failing, wait a few minutes before trying again.", True
    elif status == 413:
        code, title, action, retry = "file_too_large", "The file exceeds the server limit", "Choose a smaller file or a shorter clip.", False
    elif "no video" in text or "no downloadable" in text:
        code, title, action, retry = "no_media", "No supported media was found", "Try another public video or photo post.", False
    elif any(x in text for x in ("private", "login", "log in", "deleted", "unavailable", "no longer available", "didn't share")):
        code, title, action, retry = "access_limited", "The platform did not share this post", "Open it in a private browser tab to check it is public. If it is, retry later.", True
    elif status in (400, 422):
        code, title, action, retry = "invalid_link", "Check the post link", "Copy the full post link using Share → Copy link. Profile and Story links are not supported.", False
    elif status == 404:
        code, title, action, retry = "not_found", "The file could not be found", "Copy the post link again. It may have been removed.", False
    else:
        code, title, action, retry = "source_error", "The download could not finish", "Check your connection, then retry. If it continues, send the link through Contact.", True
    return {"code": code, "title": title, "action": action, "retryable": retry,
            "retry_after": 120 if code == "rate_limited" else 0}


def validate_cues(raw):
    try:
        cues = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(cues, list) or not 1 <= len(cues) <= 300:
            raise ValueError()
        result = []
        last_end = 0
        for cue in cues:
            start, end = float(cue["start"]), float(cue["end"])
            text = str(cue["text"]).strip()
            if not all(map(math.isfinite, (start, end))) or not (last_end <= start < end <= MAX_SECONDS):
                raise ValueError()
            if not text or len(text) > 500:
                raise ValueError()
            result.append({"start": start, "end": end, "text": text})
            last_end = end
        return result
    except (ValueError, TypeError, KeyError, OverflowError):
        raise HTTPException(422, "Use 1–300 captions with text and non-overlapping start/end times between 0 and 180 seconds.")


def stamp(seconds, separator=","):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}{separator}{ms % 1000:03}"


def srt(cues):
    # Caption text is always literal. Strip HTML/ASS controls rather than treating user text as formatting.
    return "\n\n".join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n" +
                         re.sub(r"<[^>]*>|\{[^}]*\}", "", c["text"]).replace("\\", "")
                         for i, c in enumerate(cues, 1)) + "\n"


async def save_upload(upload, directory):
    path = Path(directory) / "input.media"
    size = 0
    try:
        with path.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > upload_limit():
                    raise HTTPException(413, f"Choose a file smaller than {upload_limit() // 1048576} MB.")
                output.write(chunk)
    finally:
        await upload.close()
    if not size:
        raise HTTPException(422, "Choose a video file first.")
    return str(path)


def run_ffmpeg(binary, arguments, timeout=150):
    try:
        result = subprocess.run([binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + arguments,
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Processing took too long. Try a shorter video or a smaller file.")
    if result.returncode:
        raise HTTPException(422, "This file could not be processed. Try an MP4 video with H.264 video and AAC audio.")


def probe(binary, path):
    try:
        r = subprocess.run([binary, "-hide_banner", "-nostdin"] + LOCAL_INPUT + ["-i", path], capture_output=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise HTTPException(422, "Could not read this video. Try another MP4 file.")
    text = r.stderr.decode(errors="replace")
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match or "Video:" not in text:
        raise HTTPException(422, "Choose a video with a readable duration, such as an MP4 file.")
    hours, minutes, seconds = map(float, match.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    if not 0 < duration <= MAX_SECONDS:
        raise HTTPException(422, "Choose a video up to 3 minutes long.")
    # Refuse extreme decoded frame sizes before allocating filters.
    dimensions = re.search(r"Video: [^\n]*?\b(\d{2,6})x(\d{2,6})\b", text)
    if not dimensions or int(dimensions[1]) * int(dimensions[2]) > 4096 * 2160:
        raise HTTPException(422, "Choose a video with a resolution up to 4096 × 2160 pixels.")
    return duration


def resize_filter(shape, fit, x=0.5, y=0.5):
    w, h = SIZES[shape]
    if fit == "crop":
        return (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}:(iw-ow)*{x}:(ih-oh)*{y},setsar=1"), False
    if fit == "pad":
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1", False
    return (f"[0:v]split=2[bg][fg];[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},boxblur=20:2[blur];[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[front];"
            "[blur][front]overlay=(W-w)/2:(H-h)/2,setsar=1[out]"), True


def install(router, ffmpeg_path):
    @functools.lru_cache(maxsize=1)
    def subtitle_filter_ready():
        try:
            r = subprocess.run([ffmpeg_path(), "-filters"], capture_output=True, timeout=10)
            return bool(re.search(r"\bsubtitles\s", r.stdout.decode(errors="replace")))
        except (OSError, subprocess.TimeoutExpired):
            return False

    @router.get("/creator/capabilities")
    def capabilities():
        return {"transcription": bool(os.getenv("OPENAI_API_KEY", "").strip()), "subtitle_video": subtitle_filter_ready(),
                "max_upload_mb": upload_limit() // 1048576, "max_seconds": MAX_SECONDS,
                "languages": ["en", "te", "hi", "ta"]}

    @router.post("/creator/resize")
    async def resize(request: Request, file: UploadFile = File(...), shape: str = Form("vertical"), fit: str = Form("blur"),
                     x: float = Form(0.5), y: float = Form(0.5)):
        limit_request(request)
        if shape not in SIZES or fit not in {"crop", "pad", "blur"} or not (0 <= x <= 1 and 0 <= y <= 1):
            raise HTTPException(422, "Choose a supported shape, fit and crop position.")
        temp = tempfile.TemporaryDirectory(prefix="waverise-")
        try:
            src = await save_upload(file, temp.name)
            binary = ffmpeg_path()
            # CPU-heavy work runs outside the event loop, including probing.
            await bounded_work(probe, binary, src)
            filt, complex_ = resize_filter(shape, fit, x, y)
            out = str(Path(temp.name) / "resized.mp4")
            args = LOCAL_INPUT + ["-i", src, "-filter_complex" if complex_ else "-vf", filt,
                    "-map", "[out]" if complex_ else "0:v:0", "-map", "0:a:0?", "-t", str(MAX_SECONDS),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-map_metadata", "-1", "-threads", "2", out]
            await bounded_work(run_ffmpeg, binary, args)
            return FileResponse(out, media_type="video/mp4", filename=f"waverise-{shape}.mp4",
                                background=BackgroundTask(temp.cleanup))
        except BaseException:
            temp.cleanup()
            raise

    @router.post("/creator/transcribe")
    async def transcribe(request: Request, file: UploadFile = File(...), language: str = Form("auto")):
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise HTTPException(503, "Automatic subtitles are not configured yet. You can still add captions manually and export SRT.")
        if language not in LANGUAGES:
            raise HTTPException(422, "Choose Auto, English, Telugu, Hindi or Tamil.")
        limit_request(request, speech=True)
        with tempfile.TemporaryDirectory(prefix="waverise-") as directory:
            src = await save_upload(file, directory)
            binary = ffmpeg_path()
            duration = await bounded_work(probe, binary, src)
            audio = str(Path(directory) / "audio.mp3")
            await bounded_work(run_ffmpeg, binary, LOCAL_INPUT + ["-i", src, "-t", str(MAX_SECONDS), "-vn", "-ac", "1",
                                                          "-ar", "16000", "-b:a", "48k", "-map_metadata", "-1", audio])
            data = {"model": "whisper-1", "response_format": "verbose_json", "timestamp_granularities[]": "segment"}
            if language != "auto":
                data["language"] = language
            try:
                async with httpx.AsyncClient(timeout=100) as client:
                    with open(audio, "rb") as stream:
                        response = await client.post("https://api.openai.com/v1/audio/transcriptions",
                                                     headers={"Authorization": f"Bearer {key}"}, data=data,
                                                     files={"file": ("audio.mp3", stream, "audio/mpeg")})
            except httpx.HTTPError:
                raise HTTPException(502, "The speech service could not be reached. Retry in a minute, or add captions manually.")
            if response.status_code != 200:
                status = 429 if response.status_code == 429 else 502
                raise HTTPException(status, "The speech service is busy or its account needs attention. Try again later or add captions manually.")
            try:
                payload = response.json()
                cues, previous = [], 0.0
                for segment in payload.get("segments", []):
                    start = max(previous, float(segment["start"]))
                    end = min(duration, MAX_SECONDS, float(segment["end"]))
                    text = str(segment["text"]).strip()
                    if text and end > start:
                        cues.append({"start": start, "end": end, "text": text})
                        previous = end
                if not cues:
                    raise HTTPException(422, "No speech was detected. You can add captions manually.")
                return {"language": payload.get("language", language), "duration": duration, "cues": validate_cues(cues)}
            except (ValueError, KeyError, TypeError):
                raise HTTPException(502, "The speech service returned an unreadable result. Please retry.")

    @router.post("/creator/subtitle-video")
    async def subtitle_video(request: Request, file: UploadFile = File(...), cues: str = Form(...)):
        limit_request(request)
        captions = validate_cues(cues)
        if not subtitle_filter_ready():
            raise HTTPException(503, "Video captions need FFmpeg with the subtitles filter. SRT export is available on your device.")
        temp = tempfile.TemporaryDirectory(prefix="waverise-")
        try:
            src = await save_upload(file, temp.name)
            binary = ffmpeg_path()
            duration = await bounded_work(probe, binary, src)
            if captions[-1]["end"] > duration + .1:
                raise HTTPException(422, "A caption ends after this video. Adjust its end time first.")
            subtitles = Path(temp.name) / "captions.srt"
            subtitles.write_text(srt(captions), encoding="utf-8")
            out = str(Path(temp.name) / "captioned.mp4")
            args = LOCAL_INPUT + ["-i", src, "-vf", f"subtitles={subtitles}:force_style='FontSize=20,Outline=2,MarginV=24'",
                    "-map", "0:v:0", "-map", "0:a:0?", "-t", str(MAX_SECONDS), "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", "-map_metadata", "-1", "-threads", "2", out]
            await bounded_work(run_ffmpeg, binary, args)
            return FileResponse(out, media_type="video/mp4", filename="waverise-captioned.mp4",
                                background=BackgroundTask(temp.cleanup))
        except BaseException:
            temp.cleanup()
            raise
