"""WaveRise backend: public Instagram and X (Twitter) posts, MP3, clips, ringtones, GIFs, smaller videos and captions. No login is used."""
import os, re, shutil, subprocess, uuid
from pathlib import Path

import httpx
import yt_dlp
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

IG_URL = re.compile(r"^https?://(www\.)?instagram\.com/(p|reel|reels|tv)/", re.I)
X_URL = re.compile(r"^https?://(www\.|mobile\.)?(x|twitter)\.com/(i/(web/)?|[A-Za-z0-9_]{1,15}/)status/\d+", re.I)
BLOCKED = [x.strip().lower() for x in os.getenv("BLOCKED_LINKS", "").split(",") if x.strip()]  # link parts to refuse after a valid copyright notice
app = FastAPI(title="WaveRise")
PUBLIC = Path(__file__).parent / "public"
r_ = APIRouter()  # mounted at /api and at / so it works however Vercel passes the path


class Req(BaseModel):
    url: str


def clean(url):
    url = url.strip().split("?")[0]
    if any(b in url.lower() for b in BLOCKED):
        raise HTTPException(451, "This link is not available for legal reasons.")
    if not (IG_URL.match(url) or X_URL.match(url)):
        raise HTTPException(400, "Paste a valid Instagram post, Reel or IGTV link, or an X (Twitter) post link.")
    return url


def explain(err, url):
    """Plain-language reason for visitors (no yt-dlp text)."""
    m, site = str(err).lower(), ("X" if X_URL.match(url) else "Instagram")
    if "no video could be found" in m or "no video" in m:
        return "This post has no video or GIF. For X, WaveRise saves videos and GIFs from public posts."
    if any(k in m for k in ("login", "log in", "authentication", "cookies", "empty media response", "accessible in your browser", "nsfw", "age-restricted", "sensitive")):
        return f"{site} is asking for a login for this link, which WaveRise doesn't use. Try a public post, or try again in a few minutes."
    if "429" in m or "too many" in m or "rate" in m:
        return f"{site} is limiting requests right now. Wait a few minutes and try again."
    if any(k in m for k in ("private", "protected", "not available", "unavailable", "404", "removed", "does not exist", "suspended", "deleted")):
        return "This post is private, deleted or no longer available."
    return "Couldn't read this link. Check that it opens in a private browser tab, then try again."


def fetch_info(url):
    o = {"quiet": True, "no_warnings": True, "cachedir": False, "format": "best[ext=mp4]/best",
         "ignore_no_formats_error": True}
    try:
        with yt_dlp.YoutubeDL(o) as y:
            return y.extract_info(url, download=False)
    except Exception as e:
        print(f"[waverise] could not read {url}: {e}")  # full detail stays in the server log
        raise HTTPException(422, explain(e, url))


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
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
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
                             headers={"Content-Disposition": 'attachment; filename="waverise.mp3"'})


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
    return FileResponse(out, media_type="audio/mp4" if m4r else "audio/mpeg", filename=f"waverise-ringtone.{ext}",
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
    return FileResponse(out, media_type="video/mp4", filename="waverise-clip.mp4",
                        content_disposition_type="attachment" if dl else "inline",
                        background=BackgroundTask(os.remove, out))


def _ff(src, hdrs, ss, args, out, timeout=140):
    head = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y"]
    if hdrs:
        head += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())]
    sk = ["-ss", f"{ss:.2f}"] if ss else []
    try:
        r = subprocess.run(head + sk + ["-i", src] + args + [out], capture_output=True, timeout=timeout)
        if r.returncode and ss:  # server can't seek: read from the start instead
            r = subprocess.run(head + ["-i", src] + sk + args + [out], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.path.exists(out):
            os.remove(out)
        raise HTTPException(504, "This took too long for the free server. Try a shorter clip.")
    return r


def _send(r, out, mime, name, dl, what):
    if r.returncode or not os.path.exists(out) or os.path.getsize(out) < 500:
        if os.path.exists(out):
            os.remove(out)
        raise HTTPException(422, f"Couldn't make the {what}. " + r.stderr.decode(errors="ignore")[-150:])
    return FileResponse(out, media_type=mime, filename=name, content_disposition_type="attachment" if dl else "inline",
                        background=BackgroundTask(os.remove, out))


@r_.get("/gif")
def gif(url: str, index: int = 0, start: float = 0, length: float = 4, width: int = 480, fmt: str = "gif", dl: int = 0):
    src, hdrs, vid = media(clean(url), index)
    if not vid:
        raise HTTPException(400, "This item is a photo, so there is nothing to animate.")
    start, length = max(0.0, min(start, 21600.0)), max(1.0, min(length, 6.0))
    width = width if width in (240, 320, 480) else 320
    webp = fmt == "webp"
    out = f"/tmp/g-{uuid.uuid4().hex}.{'webp' if webp else 'gif'}"
    if webp:
        args = ["-t", f"{length:.2f}", "-an", "-vf", f"fps=15,scale={width}:-2:flags=lanczos", "-c:v", "libwebp", "-loop", "0", "-q:v", "65"]
    else:
        args = ["-t", f"{length:.2f}", "-an", "-loop", "0", "-vf",
                f"fps=12,scale={width}:-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"]
    r = _ff(src, hdrs, start, args, out)
    return _send(r, out, "image/webp" if webp else "image/gif", f"waverise.{'webp' if webp else 'gif'}", dl, "animation")


@r_.get("/shrink")
def shrink(url: str, index: int = 0, target: float = 16, dur: float = 30, dl: int = 0):
    src, hdrs, vid = media(clean(url), index)
    if not vid:
        raise HTTPException(400, "This item is a photo, so there is nothing to shrink.")
    if dur > 180:
        raise HTTPException(413, "This video is longer than 3 minutes. The free server can only shrink shorter videos.")
    target, dur = max(2.0, min(target, 64.0)), max(1.0, dur)
    out = f"/tmp/s-{uuid.uuid4().hex}.mp4"
    v = int(target * 8192 * 0.92 / dur) - 96  # video kbit/s that fits the size limit
    for _ in range(2):
        v = max(v, 150)
        args = ["-vf", "scale='min(720,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{v}k", "-maxrate", f"{int(v * 1.15)}k",
                "-bufsize", f"{v * 2}k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart"]
        r = _ff(src, hdrs, 0, args, out, 150)
        if r.returncode or not os.path.exists(out) or os.path.getsize(out) <= target * 1048576 * 1.03:
            break
        v = int(v * 0.8)  # still too big: try once more at a lower bitrate
    return _send(r, out, "video/mp4", "waverise-small.mp4", dl, "smaller video")


@r_.get("/download")
def download(request: Request, url: str, index: int = 0, inline: int = 0):
    src, hdrs, vid = media(clean(url), index)
    name = None if inline else f"waverise.{'mp4' if vid else 'jpg'}"
    return stream(src, hdrs, name, request.headers.get("range"))


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(PUBLIC / "index.html")


@r_.get("/health")
def health():
    return {"ok": True, "version": 5}


app.include_router(r_, prefix="/api")
app.include_router(r_)

if not os.getenv("VERCEL"):  # local testing only; on Vercel the public/ folder is served by the CDN
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=PUBLIC))
