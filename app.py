"""WaveRise backend for Vercel: downloader + caption copier (fastapi, yt-dlp, httpx)."""
import os, re, shutil, subprocess, time, uuid
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
import yt_dlp
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

IG_URL = re.compile(r"^https?://(www\.)?instagram\.com/(p|reel|reels|tv|stories)/", re.I)
def _norm_cookies(t):
    """Accept cookies pasted into an env var even if tabs became spaces or newlines were escaped."""
    t = t.replace("\\n", "\n").replace("\r", "")
    rows = ["# Netscape HTTP Cookie File"]
    for ln in t.split("\n"):
        ln = ln.strip()
        if not ln or (ln.startswith("#") and not ln.startswith("#HttpOnly_")):
            continue
        parts = ln.split("\t") if ln.count("\t") >= 6 else ln.split(None, 6)
        if len(parts) == 7:
            rows.append("\t".join(x.strip() for x in parts))
    return "\n".join(rows) + "\n"


def _session_cookies():
    """Easiest option: only the sessionid value (IG_SESSIONID), optionally IG_DS_USER_ID and IG_CSRFTOKEN."""
    pairs = [("sessionid", os.environ["IG_SESSIONID"].strip())]
    for name, var in (("ds_user_id", "IG_DS_USER_ID"), ("csrftoken", "IG_CSRFTOKEN")):
        if os.getenv(var):
            pairs.append((name, os.environ[var].strip()))
    return "# Netscape HTTP Cookie File\n" + "".join(f".instagram.com\tTRUE\t/\tTRUE\t2000000000\t{k}\t{v}\n" for k, v in pairs)


COOKIES = None
try:
    _raw = None
    if os.getenv("IG_COOKIES_B64"):
        import base64
        _raw = _norm_cookies(base64.b64decode(os.environ["IG_COOKIES_B64"]).decode("utf-8", "ignore"))
    elif os.getenv("IG_COOKIES_TEXT"):
        _raw = _norm_cookies(os.environ["IG_COOKIES_TEXT"])
    elif os.getenv("IG_SESSIONID"):
        _raw = _session_cookies()
    if _raw and _raw.count("\t") >= 6:
        COOKIES = "/tmp/ig_cookies.txt"
        Path(COOKIES).write_text(_raw)
except Exception as _e:  # a bad cookie value must never stop the site from starting
    print("[waverise] could not load Instagram cookies:", _e)

BLOCKED = [x.strip().lower() for x in os.getenv("BLOCKED_LINKS", "").split(",") if x.strip()]  # e.g. a reel code or URL part
BROWSER = (os.getenv("IG_BROWSER") or "").strip().lower() or None  # e.g. "firefox": reuse the Instagram login of a browser on this computer
_ck_cache = (0.0, {})
app = FastAPI(title="WaveRise")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","), allow_methods=["*"], allow_headers=["*"])
PUBLIC = Path(__file__).parent / "public"
r_ = APIRouter()  # mounted at /api and at / so it works however Vercel passes the path


class Req(BaseModel):
    url: str


def clean(url):
    url = url.strip().split("?")[0]
    if any(b in url.lower() for b in BLOCKED):
        raise HTTPException(451, "This link is not available for legal reasons.")
    if not IG_URL.match(url):
        raise HTTPException(400, "Paste a valid Instagram post, Reel, IGTV or Story link.")
    return url


def explain(err, url):
    """Plain-language reason for visitors (no yt-dlp text, no environment variable names)."""
    m, story = str(err).lower(), "/stories/" in url.lower()
    if any(k in m for k in ("login", "log in", "authentication", "cookies", "empty media response", "accessible in your browser")):
        if _has_login():
            return "Instagram did not accept the saved login for this request. The site owner may need to refresh it, or the link may be private."
        if story:
            return "Stories need an Instagram login, and this server doesn't have one yet. Reels, posts and carousels work without it."
        return "Instagram is asking for a login for this link. Try a public Reel or post, or try again in a few minutes."
    if "429" in m or "too many" in m or "rate" in m:
        return "Instagram is limiting requests right now. Wait a few minutes and try again."
    if any(k in m for k in ("private", "not available", "unavailable", "404", "removed", "does not exist")):
        return "This post is private, deleted or no longer available."
    return "Couldn't read this link. Check that it opens in a private browser tab, then try again."


def fetch_info(url):
    o = {"quiet": True, "no_warnings": True, "cachedir": False, "format": "best[ext=mp4]/best",
         "ignore_no_formats_error": True}
    if COOKIES:
        o["cookiefile"] = COOKIES
    if BROWSER:
        o["cookiesfrombrowser"] = (BROWSER,)
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


# ---------- profile picture, Stories and Highlights by username ----------
IG_APP = "936619743392459"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CDN = ("cdninstagram.com", "fbcdn.net")


def _cdict():
    """Instagram cookies as a dict: from a browser on this computer (IG_BROWSER) or from the saved cookie file."""
    global _ck_cache
    d = {}
    if BROWSER:
        if time.time() - _ck_cache[0] < 60:
            return _ck_cache[1]
        try:
            from yt_dlp.cookies import extract_cookies_from_browser
            d = {c.name: c.value for c in extract_cookies_from_browser(BROWSER) if "instagram.com" in (c.domain or "")}
        except Exception as e:
            print(f"[waverise] could not read cookies from {BROWSER}: {e}")
    if not d.get("sessionid") and COOKIES:
        for ln in Path(COOKIES).read_text().splitlines():
            p = ln.replace("#HttpOnly_", "", 1).split("\t")
            if len(p) == 7 and "instagram.com" in p[0]:
                d[p[5]] = p[6]
    if BROWSER:
        _ck_cache = (time.time(), d)
    return d


def _has_login():
    return bool(_cdict().get("sessionid"))


def _ig(path, params=None, auth=False):
    h = {"User-Agent": UA, "X-IG-App-ID": IG_APP, "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.instagram.com/", "Accept": "*/*"}
    ck = _cdict() if auth else {}
    if ck.get("csrftoken"):
        h["X-CSRFToken"] = ck["csrftoken"]
    try:
        return httpx.get("https://www.instagram.com" + path, params=params, headers=h, cookies=ck, timeout=20, follow_redirects=True)
    except httpx.HTTPError:
        raise HTTPException(502, "Couldn't reach Instagram. Try again in a moment.")


def _profile(u):
    u = u.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", u):
        raise HTTPException(400, "That doesn't look like an Instagram username.")
    r = _ig("/api/v1/users/web_profile_info/", {"username": u})
    if r.status_code in (401, 403, 429) and _has_login():
        r = _ig("/api/v1/users/web_profile_info/", {"username": u}, auth=True)
    if r.status_code == 404:
        raise HTTPException(404, "No Instagram account with that username.")
    if r.status_code == 429:
        raise HTTPException(429, "Instagram is limiting this server right now. Try again in a few minutes.")
    try:
        user = r.json()["data"]["user"]
    except Exception:
        raise HTTPException(422, "Instagram didn't return this profile right now. It may be limiting this server, so try again later.")
    if not user:
        raise HTTPException(404, "No Instagram account with that username.")
    return user


def _public(u):
    user = _profile(u)
    if user.get("is_private"):
        raise HTTPException(403, "This account is private. Stories and highlights of private accounts can't be downloaded.")
    return user


def _px(url):
    return "/api/img?u=" + quote(url, safe="")


def _items(js, rid):
    reel = (js.get("reels") or {}).get(rid) or ((js.get("reels_media") or [None])[0]) or {}
    out = []
    for it in reel.get("items") or []:
        vv, cand = it.get("video_versions") or [], (it.get("image_versions2") or {}).get("candidates") or []
        src = (vv[0] if vv else cand[0] if cand else {}).get("url")
        if src:
            out.append({"type": "video" if vv else "photo", "thumb": _px(cand[-1]["url"] if cand else src), "src": _px(src), "taken": it.get("taken_at")})
    return out


def _gate(r):
    """Why Instagram refused: limited | rejected (our login was not accepted) | no_session. None means fine."""
    if r.status_code == 429:
        return "limited"
    if r.status_code in (400, 401, 403) or "login" in r.text[:300].lower():
        return "rejected" if _has_login() else "no_session"
    return None


@r_.get("/profile")
def profile(u: str):
    user = _profile(u)
    return {"username": user.get("username"), "name": user.get("full_name"), "verified": bool(user.get("is_verified")), "private": bool(user.get("is_private")),
            "followers": (user.get("edge_followed_by") or {}).get("count"), "dp": _px(user.get("profile_pic_url_hd") or user.get("profile_pic_url") or ""),
            "session": _has_login()}


@r_.get("/stories")
def stories(u: str):
    user = _public(u)
    if not _has_login():
        return {"needs_login": True, "reason": "no_session", "items": []}
    r = _ig("/api/v1/feed/reels_media/", {"reel_ids": user["id"]}, auth=True)
    g = _gate(r)
    if g:
        return {"needs_login": True, "reason": g, "items": []}
    return {"items": _items(r.json(), str(user["id"]))}


def _tray(user):
    r = _ig(f"/api/v1/highlights/{user['id']}/highlights_tray/", auth=True)
    g = _gate(r)
    if g:
        return g
    return [{"id": str(t["id"]).split(":")[-1], "title": t.get("title") or "Highlight",
             "cover": _px(((t.get("cover_media") or {}).get("cropped_image_version") or {}).get("url", ""))} for t in r.json().get("tray") or []]


@r_.get("/highlights")
def highlights(u: str):
    user = _public(u)
    if not _has_login():
        return {"needs_login": True, "reason": "no_session", "items": []}
    t = _tray(user)
    return {"needs_login": True, "reason": t, "items": []} if isinstance(t, str) else {"items": t}


@r_.get("/highlight")
def highlight(u: str, id: str):
    user = _public(u)
    if not _has_login():
        raise HTTPException(503, "Highlights need the site's Instagram login, which isn't set up yet.")
    if not re.fullmatch(r"\d{5,25}", id) or id not in {x["id"] for x in (lambda t: t if isinstance(t, list) else [])(_tray(user))}:  # only highlights that belong to this public profile
        raise HTTPException(404, "That highlight wasn't found on this profile.")
    r = _ig("/api/v1/feed/reels_media/", {"reel_ids": "highlight:" + id}, auth=True)
    return {"items": _items(r.json(), "highlight:" + id)}


@r_.get("/diag")
def diag(u: str = "instagram"):
    """Plain-language checks for the site owner. Returns no secrets."""
    ck = _cdict()
    checks, st = [], {}

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
    r = _ig("/")
    add("This server can reach Instagram", r.status_code == 200, f"Instagram answered {r.status_code}.")
    r = _ig("/api/v1/users/web_profile_info/", {"username": u})
    uid = None
    try:
        uid = r.json()["data"]["user"]["id"]
    except Exception:
        pass
    st["profile"] = r.status_code
    add("Profile lookup without a login", bool(uid), "Works." if uid else f"Instagram answered {r.status_code}. " + ("It is limiting this server." if r.status_code == 429 else "It is blocking this lookup from the server (common for data-centre addresses)."))
    add("Site Instagram login is saved", "sessionid" in ck, "Saved." if "sessionid" in ck else "Not set. Add IG_SESSIONID in Vercel, then redeploy.")
    if "sessionid" in ck:
        r = _ig("/api/v1/accounts/current_user/", {"edit": "true"}, auth=True)
        good = r.status_code == 200 and '"user"' in r.text
        st["login"] = r.status_code
        add("Instagram accepts the saved login", good, "Accepted." if good else f"Instagram answered {r.status_code}. The login is expired, copied wrongly, or blocked from this server.")
        if good and uid:
            r = _ig("/api/v1/feed/reels_media/", {"reel_ids": uid}, auth=True)
            add("Stories feed answers with the login", r.status_code == 200, "Works." if r.status_code == 200 else f"Instagram answered {r.status_code}.")
    allok = all(c["ok"] for c in checks)
    if allok:
        v = "Everything works. Stories and Highlights should load."
    elif "sessionid" not in ck and BROWSER:
        v = f"No Instagram login was found in {BROWSER}. Open instagram.com in {BROWSER}, log in (Firefox is the most reliable), close the browser, then restart this program."
    elif "sessionid" not in ck:
        v = "Add the Instagram login (IG_SESSIONID) in Vercel and redeploy. Profile pictures may work without it, but Stories and Highlights cannot."
    elif st.get("login") in (400, 401, 403):
        v = "Instagram rejected the saved login. Log in to Instagram again in a browser, copy a fresh sessionid value, update IG_SESSIONID in Vercel and redeploy. Use a spare account."
    elif st.get("profile") in (401, 403, 429):
        v = "Instagram is blocking or limiting requests from this server. Wait and retry, or host the backend somewhere Instagram does not block (a home PC with a tunnel, or a small VPS)."
    else:
        v = "Some checks failed. See the details above."
    return {"checks": checks, "verdict": v, "ok": allok}


@r_.get("/img")
def img(u: str, dl: int = 0, name: str = "waverise"):
    p = urlparse(u)
    if p.scheme != "https" or not (p.hostname or "").endswith(CDN):
        raise HTTPException(400, "Not allowed.")
    return stream(u, {"User-Agent": UA, "Referer": "https://www.instagram.com/"}, re.sub(r"[^A-Za-z0-9._-]", "_", name)[:60] if dl else None)


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
    has_session = False
    try:
        has_session = _has_login()
    except Exception:
        pass
    return {"ok": True, "version": 4, "ig_cookies": bool(COOKIES or BROWSER), "ig_session": has_session, "browser": BROWSER}


app.include_router(r_, prefix="/api")
app.include_router(r_)

if not os.getenv("VERCEL"):  # local testing only; on Vercel the public/ folder is served by the CDN
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=PUBLIC))
