"""WaveRise backend: public Instagram, X (Twitter), Threads and YouTube posts, MP3, clips, ringtones, GIFs, smaller videos and captions. No login is used."""
import html as _html, os, re, shutil, subprocess, uuid
from urllib.parse import urlparse
from pathlib import Path

import httpx
import yt_dlp
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

IG_URL = re.compile(r"^https?://(www\.)?instagram\.com/(p|reel|reels|tv)/", re.I)
YT_ID = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})", re.I)
TH_URL = re.compile(r"^https?://(?:www\.)?threads\.(?:net|com)/(?:(@[A-Za-z0-9._]{1,30})/post|t)/([A-Za-z0-9_-]{5,24})", re.I)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CDN = ("cdninstagram.com", "fbcdn.net")
MAX_HD_SECONDS = 20 * 60  # longest video the free server will join into 720p/1080p
X_URL = re.compile(r"^https?://(www\.|mobile\.)?(x|twitter)\.com/(i/(web/)?|[A-Za-z0-9_]{1,15}/)status/\d+", re.I)
BLOCKED = [x.strip().lower() for x in os.getenv("BLOCKED_LINKS", "").split(",") if x.strip()]  # link parts to refuse after a valid copyright notice
app = FastAPI(title="WaveRise")
PUBLIC = Path(__file__).parent / "public"
r_ = APIRouter()  # mounted at /api and at / so it works however Vercel passes the path


class Req(BaseModel):
    url: str


def site_of(url):
    return "youtube" if YT_ID.match(url) else "threads" if TH_URL.match(url) else "x" if X_URL.match(url) else "instagram"


def clean(url):
    url = url.strip()
    if any(b in url.lower() for b in BLOCKED):
        raise HTTPException(451, "This link is not available for legal reasons.")
    m = YT_ID.match(url)
    if m:
        return "https://www.youtube.com/watch?v=" + m.group(1)   # one video only, playlist parts dropped
    url = url.split("?")[0].split("#")[0]
    m = TH_URL.match(url)
    if m:
        return f"https://www.threads.com/{m.group(1)}/post/{m.group(2)}" if m.group(1) else f"https://www.threads.com/t/{m.group(2)}"
    if not (IG_URL.match(url) or X_URL.match(url)):
        raise HTTPException(400, "Paste a public Instagram, X, Threads or YouTube link.")
    return url


def explain(err, url):
    """Plain-language reason for visitors (no yt-dlp text)."""
    m = str(err).lower()
    site = {"youtube": "YouTube", "threads": "Threads", "x": "X", "instagram": "Instagram"}[site_of(url)]
    if "not a bot" in m or "confirm you" in m and "bot" in m:
        return "YouTube is blocking this server for now (it often does this to cloud servers). Try again later."
    if "age" in m and ("confirm" in m or "restricted" in m) or "inappropriate for some users" in m:
        return "This video is age-restricted, which needs a YouTube login. WaveRise doesn't use logins."
    if "members-only" in m or "join this channel" in m:
        return "This is a members-only video, so it can't be downloaded."
    if "premiere" in m or "live event will begin" in m or "is live" in m:
        return "Live streams and premieres can't be downloaded until they have finished."
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
    site = site_of(url)
    if site == "threads":
        return _threads(url)
    o = {"quiet": True, "no_warnings": True, "cachedir": False, "ignore_no_formats_error": True, "noplaylist": True,
         "format": "18/best[ext=mp4][acodec!=none][vcodec!=none]/best[acodec!=none][vcodec!=none]/best" if site == "youtube" else "best[ext=mp4]/best"}
    try:
        with yt_dlp.YoutubeDL(o) as y:
            d = y.extract_info(url, download=False)
    except Exception as e:
        print(f"[waverise] could not read {url}: {e}")  # full detail stays in the server log
        raise HTTPException(422, explain(e, url))
    if site == "youtube" and (d.get("is_live") or d.get("live_status") in ("is_live", "is_upcoming")):
        raise HTTPException(422, "Live streams and premieres can't be downloaded until they have finished.")
    return d


def _yt_streams(d):
    """720p/1080p MP4 video streams plus the best MP4 audio, for joining on the fly."""
    fm = d.get("formats") or []
    aud = [f for f in fm if f.get("vcodec") == "none" and str(f.get("acodec", "")).startswith("mp4a") and f.get("url") and f.get("protocol", "https").startswith("http")]
    vid = {}
    for f in fm:
        h = f.get("height") or 0
        if (f.get("acodec") == "none" and str(f.get("vcodec", "")).startswith("avc1") and h in (480, 720, 1080)
                and f.get("url") and f.get("protocol", "https").startswith("http")):
            if h not in vid or (f.get("tbr") or 0) > (vid[h].get("tbr") or 0):
                vid[h] = f
    best_a = max(aud, key=lambda f: f.get("abr") or f.get("tbr") or 0) if aud else None
    return vid, best_a


def _attr(tag, name):
    m = re.search(name + r'\s*=\s*"([^"]*)"', tag) or re.search(name + r"\s*=\s*'([^']*)'", tag)
    return _html.unescape(m.group(1)) if m else ""


def _cdn(u):
    p = urlparse(u)
    return p.scheme == "https" and (p.hostname or "").endswith(CDN)


def _threads(url):
    """Public Threads post -> videos and photos (profile pictures are skipped on purpose)."""
    m = TH_URL.match(url)
    user = (m.group(1) or "").lstrip("@")
    hd = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.8"}
    try:
        r = httpx.get(url.rstrip("/") + "/embed", headers=hd, timeout=20, follow_redirects=True)
    except httpx.HTTPError:
        raise HTTPException(502, "Couldn't reach Threads. Try again in a moment.")
    if r.status_code == 404:
        raise HTTPException(422, "This post is private, deleted or no longer available.")
    if r.status_code == 429:
        raise HTTPException(422, "Threads is limiting requests right now. Wait a few minutes and try again.")
    page = r.text if r.status_code == 200 else ""
    entries, seen, posters = [], set(), set()
    for tag in re.finditer(r"<video\b[^>]*>.*?</video>|<img\b[^>]*>", page, re.S | re.I):
        t = tag.group(0)
        if t[:6].lower() == "<video":
            src = _attr(t.split(">", 1)[0], "src") or next((_attr(x, "src") for x in re.findall(r"<source\b[^>]*>", t, re.I) if _attr(x, "src")), "")
            poster = _attr(t.split(">", 1)[0], "poster")
            if _cdn(src) and src not in seen:
                seen.add(src); posters.add(poster)
                entries.append({"url": src, "formats": [{"url": src, "ext": "mp4"}], "thumbnail": poster if _cdn(poster) else None,
                                "http_headers": {"User-Agent": UA, "Referer": "https://www.threads.com/"}})
            continue
        src, alt, cls = _attr(t, "src"), _attr(t, "alt").lower(), _attr(t, "class").lower()
        w, h = _attr(t, "width"), _attr(t, "height")
        small = any(x.isdigit() and int(x) <= 160 for x in (w, h))
        if (not _cdn(src) or src in seen or src in posters or small or "profile" in alt or "avatar" in cls or "profile" in cls):
            continue
        seen.add(src)
        entries.append({"url": src, "thumbnail": src, "http_headers": {"User-Agent": UA, "Referer": "https://www.threads.com/"}})
    cap = ""
    mm = re.search(r'class="[^"]*BodyText[^"]*"[^>]*>(.*?)</(?:span|div)>', page, re.S)
    if mm:
        cap = _html.unescape(re.sub(r"<[^>]+>", " ", mm.group(1)))
    if not entries or not cap:   # fall back to the post page's public preview tags
        try:
            pg = httpx.get(url, headers=hd, timeout=20, follow_redirects=True).text
        except httpx.HTTPError:
            pg = ""
        meta = {k.lower(): _html.unescape(v) for k, v in re.findall(r'<meta[^>]+property="(og:[a-z:_]+)"[^>]+content="([^"]*)"', pg)}
        cap = cap or meta.get("og:description", "")
        if not entries:
            v = meta.get("og:video:secure_url") or meta.get("og:video")
            if v and _cdn(v):
                entries.append({"url": v, "formats": [{"url": v, "ext": "mp4"}], "thumbnail": meta.get("og:image"), "http_headers": {"User-Agent": UA}})
            elif meta.get("og:image") and _cdn(meta["og:image"]):
                entries.append({"url": meta["og:image"], "thumbnail": meta["og:image"], "http_headers": {"User-Agent": UA}})
    if not entries:
        raise HTTPException(422, "Couldn't find a video or photo in this Threads post. It may be private, deleted or text only.")
    return {"uploader": user or None, "description": re.sub(r"\s+", " ", cap).strip(), "thumbnail": entries[0].get("thumbnail"), "entries": entries}


def items_of(d):
    return list(d.get("entries") or [d])


def is_video(e):
    return bool(e.get("formats"))


def media(url, index=0, audio=False):
    d = fetch_info(url)
    if audio and site_of(url) == "youtube":
        _, a = _yt_streams(d)
        if a:
            return a["url"], a.get("http_headers") or d.get("http_headers") or {}, True
    its = items_of(d)
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
    site = site_of(b.url.strip())
    q = []
    if site == "youtube":
        vids, aud = _yt_streams(d)
        base_h = d.get("height") or 360
        q = sorted({base_h} | ({h for h in vids if h > base_h} if aud else set()))
    return {
        "site": site, "title": d.get("title") if site == "youtube" else None, "qualities": q,
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
    src, hdrs, vid = media(clean(url), index, audio=True)
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
    src, hdrs, vid = media(clean(url), index, audio=True)
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
def download(request: Request, url: str, index: int = 0, inline: int = 0, q: int = 0):
    u = clean(url)
    if q and site_of(u) == "youtube" and not inline:
        d = fetch_info(u)
        vids, aud = _yt_streams(d)
        if q in vids and aud and q != (d.get("height") or 360):
            return _yt_join(d, vids[q], aud, q)
    src, hdrs, vid = media(u, index)
    name = None if inline else f"waverise.{'mp4' if vid else 'jpg'}"
    return stream(src, hdrs, name, request.headers.get("range"))


def _yt_join(d, v, a, q):
    if (d.get("duration") or 0) > MAX_HD_SECONDS:
        raise HTTPException(413, f"HD downloads are limited to videos under {MAX_HD_SECONDS // 60} minutes on this free server. Choose 360p instead.")
    cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error"]
    for f in (v, a):
        hd = f.get("http_headers") or d.get("http_headers") or {}
        if hd:
            cmd += ["-headers", "".join(f"{k}: {x}\r\n" for k, x in hd.items())]
        cmd += ["-i", f["url"]]
    cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    first = p.stdout.read(65536)
    if not first:
        err = p.stderr.read().decode(errors="ignore")[-200:]
        p.wait()
        print("[waverise] HD join failed:", err)
        raise HTTPException(422, "Couldn't prepare the HD version. Try 360p instead.")

    def gen():
        try:
            yield first
            while True:
                c = p.stdout.read(65536)
                if not c:
                    break
                yield c
        finally:
            p.kill(); p.wait()
    return StreamingResponse(gen(), media_type="video/mp4", headers={"Content-Disposition": f'attachment; filename="waverise-{q}p.mp4"'})


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
