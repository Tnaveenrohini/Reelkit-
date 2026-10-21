"""ReelKit backend sized for Vercel (needs only fastapi, yt-dlp, httpx).
Heavy AI runs on hosted APIs: Groq (Whisper) and Replicate (Demucs)."""
import os, re
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

IG_URL = re.compile(r"^https?://(www\.)?instagram\.com/(p|reel|reels|tv|stories)/", re.I)
R = "https://api.replicate.com/v1"
COOKIES = None
if os.getenv("IG_COOKIES_TEXT"):  # paste a Netscape cookies.txt into this env var
    COOKIES = "/tmp/ig_cookies.txt"
    Path(COOKIES).write_text(os.environ["IG_COOKIES_TEXT"])

app = FastAPI(title="ReelKit")


class Req(BaseModel):
    url: str


def need(name):
    if not os.getenv(name):
        raise HTTPException(503, f"{name} is not set on the server. Add it in your hosting environment variables.")
    return os.environ[name]


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


def stream(src, headers=None, filename=None):
    c = httpx.Client(timeout=60, follow_redirects=True)
    r = c.send(c.build_request("GET", src, headers=headers or {}), stream=True)

    def gen():
        try:
            yield from r.iter_bytes(65536)
        finally:
            r.close(); c.close()
    h = {"Content-Disposition": f'attachment; filename="{filename}"'} if filename else {}
    return StreamingResponse(gen(), media_type=r.headers.get("content-type", "application/octet-stream"), headers=h)


# ---------- downloader + caption ----------
@app.post("/api/info")
def info(b: Req):
    d = fetch_info(clean(b.url))
    its = items_of(d)
    caption = d.get("description") or (its[0].get("description") if its else "") or ""
    return {
        "uploader": d.get("uploader") or d.get("channel"),
        "thumb": d.get("thumbnail") or (its[0].get("thumbnail") if its else None),
        "caption": caption,
        "hashtags": list(dict.fromkeys(re.findall(r"#\w+", caption))),
        "mentions": list(dict.fromkeys(re.findall(r"@[\w.]+", caption))),
        "items": [{"type": "video" if is_video(e) else "photo"} for e in its],
    }


@app.get("/api/download")
def download(url: str, index: int = 0):
    src, hdrs, vid = media(clean(url), index)
    return stream(src, hdrs, f"reelkit.{'mp4' if vid else 'jpg'}")


# ---------- Video-to-Script (Groq Whisper) ----------
@app.post("/api/script")
def script(b: Req):
    key = need("GROQ_API_KEY")
    src, hdrs, vid = media(clean(b.url))
    if not vid:
        raise HTTPException(400, "This post has no video to transcribe.")
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        data = c.get(src, headers=hdrs).content
        if len(data) > 24 * 1024 * 1024:
            raise HTTPException(413, "This video is over 24 MB, the free transcription limit.")
        r = c.post("https://api.groq.com/openai/v1/audio/transcriptions",
                   headers={"Authorization": f"Bearer {key}"}, files={"file": ("reel.mp4", data, "video/mp4")},
                   data={"model": os.getenv("GROQ_MODEL", "whisper-large-v3-turbo"), "response_format": "verbose_json"})
    if r.status_code >= 400:
        raise HTTPException(502, "Transcription failed: " + r.text[:200])
    j = r.json()
    segs = [{"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
            for s in j.get("segments", [])]
    return {"language": j.get("language", "unknown"), "segments": segs, "text": (j.get("text") or "").strip()}


# ---------- AI BGM & Vocal Splitter (Replicate Demucs) ----------
@app.post("/api/split/start")
def split_start(b: Req):
    h = {"Authorization": f"Bearer {need('REPLICATE_API_TOKEN')}"}
    src, _, vid = media(clean(b.url))
    if not vid:
        raise HTTPException(400, "This post has no video to split.")
    with httpx.Client(timeout=30) as c:
        m = c.get(f"{R}/models/ryan5453/demucs", headers=h)
        if m.status_code >= 400:
            raise HTTPException(502, "Replicate error: " + m.text[:200])
        v = m.json()["latest_version"]
        props = v["openapi_schema"]["components"]["schemas"]["Input"]["properties"]
        inp = {"audio": src}
        for k, val in (("stem", "vocals"), ("isolate_stem", "vocals"), ("output_format", "mp3"), ("format", "mp3")):
            if k in props:
                inp[k] = val
        r = c.post(f"{R}/predictions", headers=h, json={"version": v["id"], "input": inp})
    if r.status_code >= 400:
        raise HTTPException(502, "Replicate error: " + r.text[:200])
    return {"id": r.json()["id"]}


@app.get("/api/split/{pid}")
def split_poll(pid: str):
    if not re.fullmatch(r"[a-z0-9]+", pid):
        raise HTTPException(404)
    h = {"Authorization": f"Bearer {need('REPLICATE_API_TOKEN')}"}
    r = httpx.get(f"{R}/predictions/{pid}", headers=h, timeout=30).json()
    st = r.get("status")
    if st == "succeeded":
        out = r.get("output") or {}
        if isinstance(out, dict) and "stems" in out:
            out = {s["name"]: s["audio"] for s in out["stems"]}
        voc, bgm = out.get("vocals"), out.get("no_vocals") or out.get("other")
        if not voc or not bgm:
            raise HTTPException(502, "Unexpected output from the separation model.")
        f = lambda u: "/api/file?u=" + quote(u, safe="")
        return {"status": "done", "result": {"vocals": f(voc), "bgm": f(bgm)}}
    if st in ("failed", "canceled"):
        return {"status": "error", "error": str(r.get("error") or st)[:200]}
    return {"status": "running", "step": "Separating vocals from music"}


@app.get("/api/file")
def file(u: str, dl: int = 0):
    p = urlparse(u)
    if p.scheme != "https" or not (p.hostname or "").endswith("replicate.delivery"):
        raise HTTPException(400, "Not allowed.")
    return stream(u, filename="reelkit-" + Path(p.path).name if dl else None)


if not os.getenv("VERCEL"):  # local testing only; on Vercel the public/ folder is served by the CDN
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "public", html=True))
