"""ReelKit backend for Vercel: downloader + caption copier (fastapi, yt-dlp, httpx)."""
import os, re, shutil, subprocess, uuid
from pathlib import Path

import httpx
import yt_dlp
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

IG_URL = re.compile(r"^https?://(www\.)?instagram\.com/(p|reel|reels|tv|stories)/", re.I)
COOKIES = None
if os.getenv("IG_COOKIES_TEXT"):  # paste a Netscape cookies.txt into this env var
    COOKIES = "/tmp/ig_cookies.txt"
    Path(COOKIES).write_text(os.environ["IG_COOKIES_TEXT"])

app = FastAPI(title="ReelKit")
PUBLIC = Path(__file__).parent / "public"
r_ = APIRouter()  # mounted at /api and at / so it works however Vercel passes the path


class Req(BaseModel):
    url: str


def clean(url):
    url = url.strip().split("?")[0]
    if not IG_URL.match(url):
        raise HTTPException(400, "Paste a valid Instagram post, Reel, IGTV or Story link.")
    return url


def fetch_info(url):
    o = {"quiet": True, "no_warnings": True, "cachedir": False, "format": "best[ext=mp4]/best",
         "ignore_no_formats_error": True}
    if COOKIES:
        o["cookiefile"] = COOKIES
    try:
        with yt_dlp.YoutubeDL(o) as y:
            return y.extract_info(url, download=False)
    except Exception as e:
        raise HTTPException(422, "Couldn't read this link. The post may be private or deleted, or Instagram is "
                                 "blocking this server (set IG_COOKIES_TEXT). " + str(e)[-160:])


def items_of(d):
    return list(d.get("entries") or [d])


def is_video(e):
    return bool(e.get("formats"))


def media(url, index=0):
    its = items_of(fetch_info(url))
    e = its[min(max(index, 0), len(its) - 1)]
    vid = is_video(e)
    src = e.get("url") or (e["formats"][-1]["url"] if vid else e.get("thumbnail"))
    if not src:
        raise HTTPException(404, "No downloadable file found for this item.")
    return src, e.get("http_headers") or {}, vid


def stream(src, headers=None, filename=None, range_=None):
    hd = dict(headers or {})
    if range_:
        hd["Range"] = range_
    c = httpx.Client(timeout=60, follow_redirects=True)
    r = c.send(c.build_request("GET", src, headers=hd), stream=True)

    def gen():
        try:
            yield from r.iter_bytes(65536)
        finally:
            r.close(); c.close()
    h = {"Content-Disposition": f'attachment; filename="{filename}"'} if filename else {}
    h["Accept-Ranges"] = "bytes"
    if r.status_code == 206 and r.headers.get("content-range"):
        h["Content-Range"] = r.headers["content-range"]
    if r.headers.get("content-length") and not r.headers.get("content-encoding"):
        h["Content-Length"] = r.headers["content-length"]
    return StreamingResponse(gen(), status_code=206 if r.status_code == 206 else 200,
                             media_type=r.headers.get("content-type", "application/octet-stream"), headers=h)


def ffmpeg_path():
    """imageio-ffmpeg ships a static ffmpeg binary; copy it to /tmp so it is executable on Vercel."""
    import imageio_ffmpeg
    dst = "/tmp/ffmpeg"
    if not os.path.exists(dst):
        shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), dst)
        os.chmod(dst, 0o755)
    return dst


# ---------- downloader + caption ----------
@r_.post("/info")
def info(b: Req):
    d = fetch_info(clean(b.url))
    its = items_of(d)
    caption = d.get("description") or (its[0].get("description") if its else "") or ""
    g = lambda k: d.get(k) if d.get(k) is not None else (its[0].get(k) if its else None)
    return {
        "uploader": d.get("uploader") or d.get("channel"),
        "thumb": d.get("thumbnail") or (its[0].get("thumbnail") if its else None),
        "caption": caption,
        "duration": d.get("duration") or (its[0].get("duration") if its else None),
        "hashtags": list(dict.fromkeys(re.findall(r"#\w+", caption))),
        "mentions": list(dict.fromkeys(re.findall(r"@[\w.]+", caption))),
        "date": g("upload_date"), "views": g("view_count"), "likes": g("like_count"), "comments": g("comment_count"),
        "music": " - ".join(x for x in (g("track"), g("artist")) if x) or None,
        "items": [{"type": "video" if is_video(e) else "photo", "width": e.get("width"), "height": e.get("height"),
                   "duration": e.get("duration"), "size": e.get("filesize") or e.get("filesize_approx")} for e in its],
    }


@r_.get("/mp3")
def mp3(url: str, index: int = 0):
    src, hdrs, vid = media(clean(url), index)
    if not vid:
        raise HTTPException(400, "This item is a photo, so there is no audio to extract.")
    cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error"]
    if hdrs:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())]
    cmd += ["-i", src, "-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    first = p.stdout.read(65536)
    if not first:
        err = p.stderr.read().decode(errors="ignore")[-200:]
        p.wait()
        raise HTTPException(422, "Couldn't extract audio. This video may have no sound. " + err)

    def gen():
        try:
            yield first
            while True:
                chunk = p.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            p.kill(); p.wait()
    return StreamingResponse(gen(), media_type="audio/mpeg",
                             headers={"Content-Disposition": 'attachment; filename="reelkit.mp3"'})


@r_.get("/ringtone")
def ringtone(url: str, index: int = 0, start: float = 0, length: float = 30, fade: int = 1,
             fmt: str = "mp3", dl: int = 0):
    src, hdrs, vid = media(clean(url), index)
    if not vid:
        raise HTTPException(400, "This item is a photo, so there is no audio to use.")
    start, length = max(0.0, min(start, 21600.0)), max(3.0, min(length, 30.0))
    m4r = fmt == "m4r"
    ext = "m4r" if m4r else "mp3"
    out = f"/tmp/rt-{uuid.uuid4().hex}.{ext}"
    af = "afade=t=in:st=0:d=0.3" + (f",afade=t=out:st={max(length - 2, 0):.2f}:d=2" if fade else "")
    enc = (["-c:a", "aac", "-b:a", "192k", "-f", "ipod"] if m4r else ["-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3"])
    head = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y"]
    if hdrs:
        head += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())]
    ss = ["-ss", f"{start:.2f}"]
    tail = ["-t", f"{length:.2f}", "-vn", "-map", "0:a:0", "-af", af] + enc + [out]
    try:
        # fast path: seek before opening; if the server can't seek, retry by reading from the start
        r = subprocess.run(head + ss + ["-i", src] + tail, capture_output=True, timeout=100)
        if r.returncode:
            r = subprocess.run(head + ["-i", src] + ss + tail, capture_output=True, timeout=100)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Making the ringtone took too long. Try a shorter clip.")
    if r.returncode or not os.path.exists(out) or os.path.getsize(out) < 1024:
        if os.path.exists(out):
            os.remove(out)
        raise HTTPException(422, "Couldn't make the ringtone. The clip may start after the video ends, or it has no sound. "
                                 + r.stderr.decode(errors="ignore")[-150:])
    return FileResponse(out, media_type="audio/mp4" if m4r else "audio/mpeg", filename=f"reelkit-ringtone.{ext}",
                        content_disposition_type="attachment" if dl else "inline",
                        background=BackgroundTask(os.remove, out))


@r_.get("/clip")
def clip(url: str, index: int = 0, start: float = 0, length: float = 15, dl: int = 0):
    src, hdrs, vid = media(clean(url), index)
    if not vid:
        raise HTTPException(400, "This item is a photo, so there is nothing to trim.")
    start, length = max(0.0, min(start, 21600.0)), max(2.0, min(length, 30.0))
    out = f"/tmp/clip-{uuid.uuid4().hex}.mp4"
    head = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y"]
    if hdrs:
        head += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())]
    ss = ["-ss", f"{start:.2f}"]
    tail = ["-t", f"{length:.2f}", "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out]
    try:
        r = subprocess.run(head + ss + ["-i", src] + tail, capture_output=True, timeout=130)
        if r.returncode:
            r = subprocess.run(head + ["-i", src] + ss + tail, capture_output=True, timeout=130)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Cutting the clip took too long. Try a shorter clip.")
    if r.returncode or not os.path.exists(out) or os.path.getsize(out) < 1024:
        if os.path.exists(out):
            os.remove(out)
        raise HTTPException(422, "Couldn't cut the clip. The start may be after the video ends. "
                                 + r.stderr.decode(errors="ignore")[-150:])
    return FileResponse(out, media_type="video/mp4", filename="reelkit-clip.mp4",
                        content_disposition_type="attachment" if dl else "inline",
                        background=BackgroundTask(os.remove, out))


@r_.get("/download")
def download(request: Request, url: str, index: int = 0, inline: int = 0):
    src, hdrs, vid = media(clean(url), index)
    name = None if inline else f"reelkit.{'mp4' if vid else 'jpg'}"
    return stream(src, hdrs, name, request.headers.get("range"))


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(PUBLIC / "index.html")


@r_.get("/health")
def health():
    return {"ok": True, "version": 3, "ig_cookies": bool(COOKIES)}


app.include_router(r_, prefix="/api")
app.include_router(r_)

if not os.getenv("VERCEL"):  # local testing only; on Vercel the public/ folder is served by the CDN
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=PUBLIC))
