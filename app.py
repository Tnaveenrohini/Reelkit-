"""WaveRise backend: public Instagram and X (Twitter) posts, MP3, clips, ringtones, GIFs, smaller videos, stickers, Reels and captions. No login is used.
Threads and YouTube code stays in place but is switched off (SOON_SITES) until it works reliably from a cloud server."""
import html as _html, json, math, os, re, shutil, subprocess, time, unicodedata, uuid
from urllib.parse import quote
from urllib.parse import urlparse
from pathlib import Path

import httpx
import yt_dlp
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

IG_URL = re.compile(r"^https?://(www\.)?instagram\.com/(p|reel|reels|tv)/", re.I)
YT_ID = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})", re.I)
TH_URL = re.compile(r"^https?://(?:www\.)?threads\.(?:net|com)/(?:(@[A-Za-z0-9._]{1,30})/post|t)/([A-Za-z0-9_-]{5,24})", re.I)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CDN = ("cdninstagram.com", "fbcdn.net")
SOON = {x.strip().lower() for x in os.getenv("SOON_SITES", "youtube,threads").split(",") if x.strip()}  # sites shown as "coming soon"; set SOON_SITES to empty to switch them on
MAX_HD_SECONDS = 20 * 60  # longest video the free server will join into 720p/1080p
X_URL = re.compile(r"^https?://(www\.|mobile\.)?(x|twitter)\.com/(i/(web/)?|[A-Za-z0-9_]{1,15}/)status/\d+", re.I)
FB_URL = re.compile(r"^https?://(?:(?:www|m|web|mbasic|touch)\.)?facebook\.com/(?:watch/?\?|watch/live/?\?|reel/\d|share/(?:v|r|p)/|[^/?#]+/videos/|video\.php\?|story\.php\?|permalink\.php\?|[^/?#]+/posts/|groups/[^/]+/(?:posts|permalink)/)|^https?://fb\.watch/[\w-]+", re.I)
PLAN_SITES = ("youtube", "facebook")   # sites whose videos come as several qualities (ready-made or picture + sound joined on the fly)
BLOCKED = [x.strip().lower() for x in os.getenv("BLOCKED_LINKS", "").split(",") if x.strip()]  # link parts to refuse after a valid copyright notice
app = FastAPI(title="WaveRise")
PUBLIC = Path(__file__).parent / "public"
r_ = APIRouter()  # mounted at /api and at / so it works however Vercel passes the path


class Req(BaseModel):
    url: str


def site_of(url):
    return ("youtube" if YT_ID.match(url) else "threads" if TH_URL.match(url) else "x" if X_URL.match(url)
            else "facebook" if FB_URL.match(url) else "instagram")


_fb_resolved = {}


def _fb_clean(url):
    """Facebook links: keep the parts that name the video, follow share/fb.watch links to the real video address."""
    u = re.sub(r"^https?://(?:m|web|mbasic|touch)\.facebook\.com/", "https://www.facebook.com/", url, flags=re.I).replace("http://", "https://", 1)
    u = re.sub(r"^https://facebook\.com/", "https://www.facebook.com/", u, flags=re.I)
    m = re.search(r"facebook\.com/watch(?:/live)?/?\?(?:.*&)?v=(\d+)", u) or re.search(r"facebook\.com/video\.php\?(?:.*&)?v=(\d+)", u)
    if m:
        return f"https://www.facebook.com/watch/?v={m.group(1)}"
    if re.search(r"facebook\.com/(?:story|permalink)\.php\?", u):
        q = dict(x.split("=", 1) for x in u.split("?", 1)[1].split("#")[0].split("&") if "=" in x)
        return u.split("?")[0] + "?" + "&".join(f"{k}={q[k]}" for k in ("story_fbid", "id") if k in q)
    u = u.split("?")[0].split("#")[0]
    if re.match(r"^https://fb\.watch/|^https://www\.facebook\.com/share/", u, re.I):
        if u in _fb_resolved:
            return _fb_resolved[u]
        final = u
        try:
            r = httpx.get(u, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"}, timeout=12, follow_redirects=True)
            to = str(r.url)
            if "/login" in to:                       # Facebook sends logged-out visitors to log in, with the video in ?next=
                from urllib.parse import parse_qs, unquote
                nxt = (parse_qs(urlparse(to).query).get("next") or [""])[0]
                to = unquote(nxt) if nxt else ""
            if to and FB_URL.match(to) and not re.search(r"/share/|fb\.watch", to):
                final = _fb_clean(to)
            else:
                m = re.search(r'<link rel="canonical" href="([^"]+)"', r.text) or re.search(r'property="og:url" content="([^"]+)"', r.text)
                if m and FB_URL.match(_html.unescape(m.group(1))) and not re.search(r"/share/|fb\.watch", m.group(1)):
                    final = _fb_clean(_html.unescape(m.group(1)))
        except httpx.HTTPError as e:
            print("[waverise] facebook share link not followed:", e)
        if len(_fb_resolved) > 500:
            _fb_resolved.clear()
        _fb_resolved[u] = final
        return final
    return u


def clean(url):
    url = url.strip()
    if any(b in url.lower() for b in BLOCKED):
        raise HTTPException(451, "This link is not available for legal reasons.")
    for site, rx in (("youtube", YT_ID), ("threads", TH_URL)):
        if site in SOON and rx.match(url):
            raise HTTPException(503, f"{'YouTube' if site == 'youtube' else 'Threads'} downloads are coming soon to WaveRise.")
    m = YT_ID.match(url)
    if m:
        return "https://www.youtube.com/watch?v=" + m.group(1)   # one video only, playlist parts dropped
    if FB_URL.match(url):
        return _fb_clean(url)
    url = url.split("?")[0].split("#")[0]
    m = TH_URL.match(url)
    if m:
        return f"https://www.threads.com/{m.group(1)}/post/{m.group(2)}" if m.group(1) else f"https://www.threads.com/t/{m.group(2)}"
    if not (IG_URL.match(url) or X_URL.match(url)):
        raise HTTPException(400, "Paste a public Instagram, Facebook or X link.")
    return url


def explain(err, url):
    """Plain-language reason for visitors (no yt-dlp text)."""
    m = str(err).lower()
    site = {"youtube": "YouTube", "threads": "Threads", "x": "X", "facebook": "Facebook", "instagram": "Instagram"}[site_of(url)]
    if "only images" in m:
        return "YouTube only shared the thumbnail with this server, not the video. It does this when it limits cloud servers. Try again later."
    if "requested format is not available" in m:
        return "YouTube didn't offer a downloadable version of this video to this server. Try again later, or try another video."
    if "javascript runtime" in m or "n challenge" in m or "signature" in m and "solv" in m:
        return "YouTube changed how its videos are unlocked. The site owner needs to update yt-dlp and redeploy."
    if "po token" in m or ("http error 403" in m and site == "YouTube"):
        return "YouTube is blocking this server for now (it often does this to cloud servers). Try again later."
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
        if site == "Instagram":
            return ("Instagram didn't share this post with WaveRise just now. It sometimes holds back posts from cloud servers for a few minutes. "
                    "Wait a minute and try again. Posts from private accounts, and posts marked sensitive, can't be downloaded.")
        if site == "Facebook":
            return ("Facebook wants a login to show this video, which WaveRise doesn't use. That happens with private videos, videos shared only with friends, "
                    "private groups and age-restricted videos. Public Page videos and public Reels work. If it is public, try again in a few minutes.")
        return f"{site} is asking for a login for this link, which WaveRise doesn't use. Try a public post, or try again in a few minutes."
    if "429" in m or "too many" in m or "rate" in m:
        return f"{site} is limiting requests right now. Wait a few minutes and try again."
    if any(k in m for k in ("private", "protected", "not available", "unavailable", "404", "removed", "does not exist", "suspended", "deleted")):
        return "This post is private, deleted or no longer available."
    why = re.sub(r"https?://\S+", "", str(err)).replace("ERROR:", "").strip()
    why = re.sub(r"\s+", " ", why)[:140]
    return "Couldn't read this link. Check that it opens in a private browser tab, then try again." + (f" Details: {why}" if why else "")


YT_FORMAT = "18/b/bv*+ba/all"   # WaveRise picks the streams itself (see _yt_plans); this only has to never fail
YT_CLIENT_FALLBACKS = (None, ["tv_simply", "web_safari"], ["mweb", "android_vr"])


def _retryable(e):
    m = str(e).lower()
    return any(k in m for k in ("only images", "not a bot", "requested format", "player response", "403", "po token", "unable to extract", "signature", "n challenge", "javascript"))


# ---------- Instagram without login: the post page's own data, then the web app's public query ----------
IG_CODE = re.compile(r"instagram\.com/(?:p|reels?|tv)/([A-Za-z0-9_-]{5,})", re.I)
IG_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
IG_APP_ID = "936619743392459"
IG_DOC_ID = os.getenv("IG_DOC_ID", "27128499623469141")      # the logged-out web app's post query (updated July 2026); override without a code change
IG_MEDIA_HDR = {"User-Agent": IG_UA, "Referer": "https://www.instagram.com/"}
IG_NAV = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9",
          "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1", "Upgrade-Insecure-Requests": "1"}


def _ig_walk(o, code, depth=0):
    """Find the media object for this shortcode anywhere in Instagram's JSON (new v1 shape or older GraphQL shape)."""
    if depth > 60:
        return None
    if isinstance(o, dict):
        c = o.get("code") or o.get("shortcode")
        if c == code and any(k in o for k in ("video_versions", "image_versions2", "carousel_media", "display_url", "video_url", "edge_sidecar_to_children")):
            return o
        for v in o.values():
            r = _ig_walk(v, code, depth + 1)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _ig_walk(v, code, depth + 1)
            if r is not None:
                return r
    return None


def _ig_entry(m):
    if "video_versions" in m or "image_versions2" in m:          # v1 shape
        vids = m.get("video_versions") or []
        imgs = (m.get("image_versions2") or {}).get("candidates") or []
        img = max(imgs, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0)) if imgs else {}
        e = {"thumbnail": img.get("url"), "width": img.get("width"), "height": img.get("height"), "http_headers": dict(IG_MEDIA_HDR)}
        if vids:
            v = max(vids, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0))
            e.update(url=v["url"], ext="mp4", width=v.get("width") or e["width"], height=v.get("height") or e["height"], duration=m.get("video_duration"),
                     formats=[{"url": v["url"], "ext": "mp4", "width": v.get("width"), "height": v.get("height"), "http_headers": dict(IG_MEDIA_HDR)}])
        else:
            e["url"] = img.get("url")
        return e
    dims_ = m.get("dimensions") or {}
    e = {"thumbnail": m.get("display_url") or m.get("thumbnail_src"), "width": dims_.get("width"), "height": dims_.get("height"), "http_headers": dict(IG_MEDIA_HDR)}
    if m.get("is_video") and m.get("video_url"):
        e.update(url=m["video_url"], ext="mp4", duration=m.get("video_duration"),
                 formats=[{"url": m["video_url"], "ext": "mp4", "http_headers": dict(IG_MEDIA_HDR)}])
    else:
        e["url"] = e["thumbnail"]
    return e


def _ig_info(m):
    if m.get("carousel_media"):
        kids = m["carousel_media"]
    elif m.get("edge_sidecar_to_children"):
        kids = [x.get("node") or {} for x in m["edge_sidecar_to_children"].get("edges") or []]
    else:
        kids = [m]
    entries = [e for e in (_ig_entry(k) for k in kids) if e.get("url")]
    if not entries:
        return None
    cap = m.get("caption")
    cap = cap.get("text") if isinstance(cap, dict) else (cap if isinstance(cap, str) else "")
    if not cap:
        edges = (m.get("edge_media_to_caption") or {}).get("edges") or []
        cap = (edges[0].get("node") or {}).get("text", "") if edges else ""
    user = (m.get("user") or m.get("owner") or {}).get("username")
    ts = m.get("taken_at") or m.get("taken_at_timestamp")
    d = {"uploader": user, "channel": user, "description": cap or "", "title": None, "thumbnail": entries[0].get("thumbnail"),
         "like_count": m.get("like_count") or (m.get("edge_media_preview_like") or {}).get("count"), "comment_count": m.get("comment_count"),
         "upload_date": time.strftime("%Y%m%d", time.gmtime(int(ts))) if ts else None, "entries": entries}
    if len(entries) == 1:
        d["duration"] = entries[0].get("duration")
    return d


def _ig_page_json(html):
    for s in re.finditer(r'<script type="application/json"[^>]*>(.*?)</script>', html or "", re.S):
        try:
            yield json.loads(s.group(1))
        except ValueError:
            continue


def _ig_embed(html, code):
    """Last resort: the public embed page. Video address and picture are written into its script data."""
    m = None
    for blob in re.findall(r'"gql_data"\s*:\s*(\{.*?\})\s*,\s*"', html or "", re.S) + re.findall(r'"contextJSON"\s*:\s*"((?:[^"\\]|\\.)*)"', html or ""):
        try:
            j = json.loads(blob) if blob.startswith("{") else json.loads(json.loads(f'"{blob}"'))
        except ValueError:
            continue
        m = _ig_walk(j, code)
        if m:
            return _ig_info(m)
    vu = re.search(r'"video_url"\s*:\s*"([^"]+)"', html or "")
    img = re.search(r'class="EmbeddedMediaImage"[^>]*src="([^"]+)"', html or "")
    user = re.search(r'class="UsernameText"[^>]*>([^<]+)<', html or "")
    if not (vu or img):
        return None
    un = lambda s: json.loads(f'"{s}"') if s else None
    pic = _html.unescape(img.group(1)) if img else None
    e = {"thumbnail": pic, "http_headers": dict(IG_MEDIA_HDR)}
    if vu:
        v = un(vu.group(1)); e.update(url=v, ext="mp4", formats=[{"url": v, "ext": "mp4", "http_headers": dict(IG_MEDIA_HDR)}])
    else:
        e["url"] = pic
    return {"uploader": user.group(1).strip() if user else None, "description": "", "title": None, "thumbnail": pic, "entries": [e]}


def _ig_direct(url, notes):
    m = IG_CODE.search(url)
    if not m:
        return None
    code = m.group(1)
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": IG_UA, "Accept-Language": "en-US,en;q=0.9"}) as c:
        html = ""
        try:                                                       # 1) the post page embeds the post's data for logged-out visitors
            r = c.get(f"https://www.instagram.com/p/{code}/", headers=IG_NAV)
            final = str(r.url)
            if "accounts/login" in final or "/challenge" in final:
                notes.append("post page sent to login")
            elif r.status_code == 404:
                notes.append("post page 404"); raise HTTPException(422, "This post is private, deleted or no longer available.")
            elif r.status_code == 200:
                html = r.text
                for j in _ig_page_json(html):
                    found = _ig_walk(j, code)
                    if found and (d := _ig_info(found)):
                        notes.append("post page ok"); return d
                notes.append("post page had no post data")
            else:
                notes.append(f"post page {r.status_code}")
        except httpx.HTTPError as e:
            notes.append(f"post page unreachable ({type(e).__name__})")
        try:                                                       # 2) the query the logged-out web app makes
            lsd = re.search(r'"LSD",\[\],\{[^{}]*"token":"([^"]+)"', html)
            if not lsd or not c.cookies.get("csrftoken"):
                home = c.get("https://www.instagram.com/", headers=IG_NAV)
                lsd = lsd or re.search(r'"LSD",\[\],\{[^{}]*"token":"([^"]+)"', home.text)
            tok = lsd.group(1) if lsd else ""
            data = {"variables": json.dumps({"shortcode": code, "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False}, separators=(",", ":")),
                    "doc_id": IG_DOC_ID, "server_timestamps": "true"}
            if tok:
                data["lsd"] = tok
            h = {"x-ig-app-id": IG_APP_ID, "x-csrftoken": c.cookies.get("csrftoken") or "", "Referer": f"https://www.instagram.com/p/{code}/", "Origin": "https://www.instagram.com",
                 "Accept": "*/*", "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest",
                 "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
            if tok:
                h["x-fb-lsd"] = tok
            r = c.post("https://www.instagram.com/api/graphql", data=data, headers=h)
            if r.status_code == 200:
                try:
                    j = r.json()
                except ValueError:
                    j = None
                found = _ig_walk(j, code) if j else None
                if found and (d := _ig_info(found)):
                    notes.append("web query ok"); return d
                notes.append("web query had no post data")
            else:
                notes.append(f"web query {r.status_code}")
        except httpx.HTTPError as e:
            notes.append(f"web query unreachable ({type(e).__name__})")
        try:                                                       # 3) the public embed page
            r = c.get(f"https://www.instagram.com/p/{code}/embed/captioned/", headers=IG_NAV)
            if r.status_code == 200 and (d := _ig_embed(r.text, code)):
                notes.append("embed ok"); return d
            notes.append(f"embed {r.status_code}, no media")
        except httpx.HTTPError as e:
            notes.append(f"embed unreachable ({type(e).__name__})")
    return None


_info_cache = {}
INFO_TTL = 600     # seconds: one Instagram read serves the info screen and the download that follows


def fetch_info(url):
    hit = _info_cache.get(url)
    if hit and time.time() - hit[0] < INFO_TTL:
        return json.loads(hit[1])
    d = _fetch_info(url)
    if len(_info_cache) > 300:
        _info_cache.clear()
    try:
        _info_cache[url] = (time.time(), json.dumps(d, default=str))
    except (TypeError, ValueError):
        pass
    return d


def _fetch_info(url):
    site = site_of(url)
    if site == "threads":
        return _threads(url)
    ig_notes = []
    if site == "instagram":
        d = _ig_direct(url, ig_notes)
        print(f"[waverise] instagram direct for {url}: {'; '.join(ig_notes)}")
        if d:
            return d
    o = {"quiet": True, "no_warnings": True, "cachedir": False, "ignore_no_formats_error": True, "noplaylist": True,
         "format": YT_FORMAT if site == "youtube" else "b/bv*+ba/all" if site == "facebook" else "best[ext=mp4]/best"}
    if site == "youtube" and js_runtime():
        o["js_runtimes"] = js_runtime()
    d, last = None, None
    for clients in (YT_CLIENT_FALLBACKS if site == "youtube" else (None,)):
        opts = dict(o)
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": clients}}
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                d = y.extract_info(url, download=False)
            if site == "youtube" and not _yt_plans(d)[0]:
                raise yt_dlp.utils.DownloadError("Only images are available (YouTube shared no video or audio streams)")
            break
        except Exception as e:
            last = e
            print(f"[waverise] could not read {url} (clients {clients or 'default'}): {e}")  # full detail stays in the server log
            d = None
            if not (site == "youtube" and _retryable(e)):
                break
    if d is None:
        raise HTTPException(422, explain(last, url))
    if site in PLAN_SITES and (d.get("is_live") or d.get("live_status") in ("is_live", "is_upcoming")):
        raise HTTPException(422, "Live streams and premieres can't be downloaded until they have finished.")
    if site == "facebook":
        d = _fb_fix(d)
        if not _yt_plans(d)[0]:
            raise HTTPException(422, "This Facebook post has no video WaveRise can save. It saves public Facebook videos and Reels.")
    return d


def _fb_fix(d):
    """Facebook posts: use the first video of a post, and give Facebook's ready-made 'sd'/'hd' files a height when it is missing."""
    if d.get("entries"):
        vids = [e for e in d["entries"] if e and e.get("formats")]
        if vids:
            e = dict(vids[0]); e.setdefault("uploader", d.get("uploader")); e.setdefault("description", d.get("description")); d = e
    for f in d.get("formats") or []:
        fid = str(f.get("format_id", "")).lower()
        if fid in ("hd", "sd") and f.get("vcodec") is None and f.get("acodec") is None:
            f["vcodec"], f["acodec"] = "avc1", "mp4a"      # Facebook's ready-made files carry picture and sound
        if not f.get("height") and _hv(f):
            f["height"] = 720 if "hd" in fid else 360 if "sd" in fid else None
    t = str(d.get("title") or "")
    if not t or t.lower().startswith(("facebook", "video")) or t.isdigit():
        d["title"] = (d.get("description") or "").split("\n")[0][:80] or d.get("uploader") or "Facebook video"
    return d


def _proto(f):
    return str(f.get("protocol") or "https")


def _hv(f):
    return f.get("vcodec") not in (None, "none")


def _ha(f):
    return f.get("acodec") not in (None, "none")


def _yt_plans(d):
    """height -> how to deliver it: ("one", file) | ("join", video, audio) | ("hls", playlist). Plus the best audio stream."""
    fm = [f for f in (d.get("formats") or []) if f.get("url") and f.get("ext") != "mhtml" and "storyboard" not in str(f.get("format_note", "")).lower()
          and (_hv(f) or _ha(f))]
    http = [f for f in fm if _proto(f).startswith("http")]
    plans = {}
    for f in sorted((f for f in http if _hv(f) and _ha(f)), key=lambda f: (f.get("ext") == "mp4", f.get("tbr") or 0)):
        h = f.get("height") or 0
        if 0 < h <= 1080:
            plans[h] = ("one", f)                      # ready-made file with picture and sound
    auds = [f for f in http if _ha(f) and not _hv(f)]
    m4a = [f for f in auds if f.get("ext") in ("m4a", "mp4") or str(f.get("acodec", "")).startswith("mp4a")]
    a = max(m4a or auds, key=lambda f: f.get("abr") or f.get("tbr") or 0) if auds else None
    if m4a:
        best = {}
        for f in http:
            h = f.get("height") or 0
            if _hv(f) and not _ha(f) and str(f.get("vcodec", "")).startswith("avc1") and 0 < h <= 1080:
                if h not in best or (f.get("tbr") or 0) > (best[h].get("tbr") or 0):
                    best[h] = f
        top = max(m4a, key=lambda f: f.get("abr") or f.get("tbr") or 0)
        for h, f in best.items():
            plans.setdefault(h, ("join", f, top))      # separate picture + sound, joined on the fly
    for f in sorted((f for f in fm if "m3u8" in _proto(f) and _hv(f) and f.get("acodec") != "none"),
                    key=lambda f: (str(f.get("vcodec", "")).startswith("avc1"), -(f.get("tbr") or 0)), reverse=True):
        h = f.get("height") or 0
        if 0 < h <= 1080:
            plans.setdefault(h, ("hls", f))            # streaming playlist with sound
    return plans, a


def _yt_choices(plans):
    nice = [h for h in sorted(plans) if h in (360, 480, 720, 1080)]
    return nice or sorted(plans)[-3:]


def _yt_default(plans):
    one = sorted(h for h, p in plans.items() if p[0] == "one")
    pick = [h for h in one if h >= 360] or one or [h for h in sorted(plans) if h >= 360] or sorted(plans)
    return pick[0]


def _hd(f, d):
    return f.get("http_headers") or d.get("http_headers") or {}


def _attr(tag, name):
    m = re.search(name + r'\s*=\s*"([^"]*)"', tag) or re.search(name + r"\s*=\s*'([^']*)'", tag)
    return _html.unescape(m.group(1)) if m else ""


def _cdn(u):
    p = urlparse(u)
    return p.scheme == "https" and (p.hostname or "").endswith(CDN)


TH_HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9",
              "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Upgrade-Insecure-Requests": "1"}
TH_MEDIA_HD = {"User-Agent": UA, "Referer": "https://www.threads.com/"}


def _th_get(u):
    try:
        r = httpx.get(u, headers=TH_HEADERS, timeout=20, follow_redirects=True)
        return r.status_code, r.text, str(r.url)
    except httpx.HTTPError as e:
        return 0, "", str(e)[:60]


def _th_find(o, code, out):
    """Walk Threads' embedded page data for the post whose shortcode matches."""
    if isinstance(o, dict):
        if o.get("code") == code and any(k in o for k in ("image_versions2", "video_versions", "carousel_media")):
            out.append(o)
        for v in o.values():
            _th_find(v, code, out)
    elif isinstance(o, list):
        for v in o:
            _th_find(v, code, out)


def _th_from_post(post):
    entries = []
    for n in post.get("carousel_media") or [post]:
        vv = [v for v in (n.get("video_versions") or []) if _cdn(v.get("url", ""))]
        cand = [c for c in ((n.get("image_versions2") or {}).get("candidates") or []) if _cdn(c.get("url", ""))]
        img = max(cand, key=lambda c: c.get("width") or 0)["url"] if cand else None
        if vv:
            v = max(vv, key=lambda v: (v.get("width") or 0, -(v.get("type") or 0)))["url"]
            entries.append({"url": v, "formats": [{"url": v, "ext": "mp4"}], "thumbnail": img, "http_headers": TH_MEDIA_HD,
                            "width": n.get("original_width"), "height": n.get("original_height")})
        elif img:
            entries.append({"url": img, "thumbnail": img, "http_headers": TH_MEDIA_HD, "width": n.get("original_width"), "height": n.get("original_height")})
    cap = ((post.get("caption") or {}).get("text") or "") if isinstance(post.get("caption"), dict) else ""
    return entries, cap, (post.get("user") or {}).get("username")


def _th_from_embed(page):
    entries, seen, posters = [], set(), set()
    for tag in re.finditer(r"<video\b[^>]*>.*?</video>|<img\b[^>]*>", page, re.S | re.I):
        t = tag.group(0)
        if t[:6].lower() == "<video":
            src = _attr(t.split(">", 1)[0], "src") or next((_attr(x, "src") for x in re.findall(r"<source\b[^>]*>", t, re.I) if _attr(x, "src")), "")
            poster = _attr(t.split(">", 1)[0], "poster")
            if _cdn(src) and src not in seen:
                seen.add(src); posters.add(poster)
                entries.append({"url": src, "formats": [{"url": src, "ext": "mp4"}], "thumbnail": poster if _cdn(poster) else None, "http_headers": TH_MEDIA_HD})
            continue
        src, alt, cls = _attr(t, "src"), _attr(t, "alt").lower(), _attr(t, "class").lower()
        w, h = _attr(t, "width"), _attr(t, "height")
        small = any(x.isdigit() and int(x) <= 160 for x in (w, h))
        if not _cdn(src) or src in seen or src in posters or small or "profile" in alt or "avatar" in cls or "profile" in cls:
            continue
        seen.add(src)
        entries.append({"url": src, "thumbnail": src, "http_headers": TH_MEDIA_HD})
    cap = ""
    mm = re.search(r'class="[^"]*BodyText[^"]*"[^>]*>(.*?)</(?:span|div)>', page, re.S)
    if mm:
        cap = _html.unescape(re.sub(r"<[^>]+>", " ", mm.group(1)))
    return entries, cap


def _threads(url):
    """Public Threads post -> videos and photos. Profile pictures are never collected.
    1) the post data Threads embeds in its page, 2) the embed page, 3) the public preview tags."""
    m = TH_URL.match(url)
    user, code = (m.group(1) or "").lstrip("@"), m.group(2)
    notes, entries, cap = [], [], ""
    st, page, final = _th_get(url)
    if st == 429:
        raise HTTPException(422, "Threads is limiting requests right now. Wait a few minutes and try again.")
    if st == 404:
        raise HTTPException(422, "This post is private, deleted or no longer available.")
    blocks = re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', page, re.S)
    found = []
    for b in blocks:
        if code in b:
            try:
                _th_find(json.loads(b), code, found)
            except ValueError:
                pass
    if found:
        best = max(found, key=lambda o: len(json.dumps(o)))
        entries, cap, user2 = _th_from_post(best)
        user = user or user2 or ""
    login = "/login" in final or "accounts/login" in final
    notes.append(f"post page {st or final}" + (" (sent to login)" if login else f", {len(blocks)} data blocks, post {'found' if found else 'not found'}"))
    if not entries:
        st2, emb, _ = _th_get(url.rstrip("/") + "/embed")
        entries, cap2 = _th_from_embed(emb if st2 == 200 else "")
        cap = cap or cap2
        notes.append(f"embed {st2 or 'unreachable'}, {len(entries)} media")
    if not entries or not cap:
        meta = {k.lower(): _html.unescape(v) for k, v in re.findall(r'<meta[^>]+property="(og:[a-z:_]+)"[^>]+content="([^"]*)"', page)}
        cap = cap or meta.get("og:description", "")
        if not entries:
            v = meta.get("og:video:secure_url") or meta.get("og:video")
            if v and _cdn(v):
                entries.append({"url": v, "formats": [{"url": v, "ext": "mp4"}], "thumbnail": meta.get("og:image"), "http_headers": TH_MEDIA_HD})
            elif meta.get("og:image") and _cdn(meta["og:image"]):
                entries.append({"url": meta["og:image"], "thumbnail": meta["og:image"], "http_headers": TH_MEDIA_HD})
            notes.append("preview tags " + ("used" if entries else "none"))
    if not entries:
        print("[waverise] threads failed:", url, "|", "; ".join(notes))
        raise HTTPException(422, "Couldn't find a video or photo in this Threads post. It may be private, deleted or text only. Details: " + "; ".join(notes))
    return {"uploader": user or None, "description": re.sub(r"\s+", " ", cap).strip(), "thumbnail": entries[0].get("thumbnail"), "entries": entries}


def items_of(d):
    return list(d.get("entries") or [d])


def is_video(e):
    return bool(e.get("formats"))


def media(url, index=0, audio=False, info=False):
    d = fetch_info(url)
    r = _media(d, url, index, audio)
    return (*r, d) if info else r


def _media(d, url, index=0, audio=False):
    if site_of(url) in PLAN_SITES:
        plans, a = _yt_plans(d)
        if audio and a:
            return a["url"], _hd(a, d), True
        p = plans[_yt_default(plans)]
        return p[1]["url"], _hd(p[1], d), True     # "one"/"hls" include sound; for "join" the picture stream is used
    its = items_of(d)
    e = its[min(max(index, 0), len(its) - 1)]
    vid = is_video(e)
    src = e.get("url") or (e["formats"][-1]["url"] if vid else e.get("thumbnail"))
    if not src:
        raise HTTPException(404, "No downloadable file found for this item.")
    return src, e.get("http_headers") or {}, vid


def _words(t):
    """Split into words, keeping letters, digits and combining marks (Hindi/Tamil/Telugu vowel signs) together."""
    out, cur = [], ""
    for ch in unicodedata.normalize("NFC", str(t or "")):
        if unicodedata.category(ch)[0] in "LMN":
            cur += ch
        elif cur:
            out.append(cur); cur = ""
    return out + ([cur] if cur else [])


def nice_name(d, ext, tag="", index=None):
    """Readable file name from the account and the first words of the caption, e.g. nasa-sunrise-over-the-ocean.mp4"""
    who = (d.get("uploader") or d.get("channel") or "").strip().lstrip("@")
    text = d.get("title") if d.get("title") and not str(d.get("title")).lower().startswith(("video by", "post by")) else d.get("description") or ""
    text = re.sub(r"https?://\S+|[#@][\w.]+", " ", str(text))
    words = _words(text)[:5]
    base = "-".join(x for x in _words(who) + words if x)
    slug = base.lower()[:60].rstrip("-") or "waverise"
    for extra in ([f"{index + 1}"] if index else []) + ([tag] if tag else []):
        slug += "-" + extra
    return f"{slug}.{ext}"


def disposition(name, inline=False):
    stem, _, ext = name.rpartition(".")
    asc = re.sub(r"[^A-Za-z0-9._-]+", "-", unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()).strip("-.") or "waverise"
    return f'{"inline" if inline else "attachment"}; filename="{asc}.{ext}"; filename*=UTF-8\'\'{quote(name)}'


CLEAN = ["-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact"]
LOUD = "loudnorm=I=-14:TP=-1.5:LRA=11"


def jpeg_clean(b):
    """Remove hidden details (EXIF location/device/dates, XMP, comments) from a JPEG without re-compressing it."""
    if b[:2] != b"\xff\xd8":
        return b
    out, i = bytearray(b[:2]), 2
    while i + 4 <= len(b) and b[i] == 0xFF:
        m = b[i + 1]
        if m == 0xDA:                                   # image data starts: copy the rest as is
            out += b[i:]
            return bytes(out)
        ln = int.from_bytes(b[i + 2:i + 4], "big")
        if not (0xE1 <= m <= 0xEF or m == 0xFE):        # keep everything except APP1-APP15 and comments
            out += b[i:i + 2 + ln]
        i += 2 + ln
    return b


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
    h = {"Content-Disposition": disposition(filename)} if filename else {}
    h["Accept-Ranges"] = "bytes"
    if r.status_code == 206 and r.headers.get("content-range"):
        h["Content-Range"] = r.headers["content-range"]
    if r.headers.get("content-length") and not r.headers.get("content-encoding"):
        h["Content-Length"] = r.headers["content-length"]
    return StreamingResponse(gen(), status_code=206 if r.status_code == 206 else 200,
                             media_type=r.headers.get("content-type", "application/octet-stream"), headers=h)


_JS = None


def js_runtime():
    """yt-dlp needs a JavaScript engine to unlock YouTube. Use the Deno that ships in requirements.txt."""
    global _JS
    if _JS is not None:
        return _JS
    _JS = {}
    os.environ.setdefault("DENO_DIR", "/tmp/deno-cache")
    os.environ.setdefault("DENO_NO_UPDATE_CHECK", "1")
    try:
        try:
            import deno
            src = deno.find_deno_bin()
        except Exception:
            src = shutil.which("deno")
        if src:
            if not os.access(src, os.X_OK):      # some hosts drop the "run" permission: use a copy in /tmp
                dst = "/tmp/deno"
                if not os.path.exists(dst):
                    shutil.copy(src, dst)
                    os.chmod(dst, 0o755)
                src = dst
            _JS = {"deno": {"path": src}}
        elif shutil.which("node"):
            _JS = {"node": {"path": shutil.which("node")}}
    except Exception as e:
        print("[waverise] no JavaScript engine for YouTube:", e)
    return _JS


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
    if site in PLAN_SITES:
        q = _yt_choices(_yt_plans(d)[0])
    return {
        "site": site, "title": d.get("title") if site in PLAN_SITES else None, "qualities": q,
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
def mp3(url: str, index: int = 0, loud: int = 0):
    src, hdrs, vid, d = media(clean(url), index, audio=True, info=True)
    if not vid:
        raise HTTPException(400, "This item is a photo, so there is no audio to extract.")
    cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error"]
    if hdrs:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())]
    cmd += ["-i", src, "-vn", "-map", "0:a:0"] + (["-af", LOUD] if loud else []) + CLEAN + ["-c:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "0", "-f", "mp3", "pipe:1"]
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
                             headers={"Content-Disposition": disposition(nice_name(d, "mp3", "", index))})


@r_.get("/ringtone")
def ringtone(url: str, index: int = 0, start: float = 0, length: float = 30, fade: int = 1,
             fmt: str = "mp3", dl: int = 0):
    src, hdrs, vid, d = media(clean(url), index, audio=True, info=True)
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
    tail = ["-t", f"{length:.2f}", "-vn", "-map", "0:a:0", "-af", af] + enc + CLEAN + [out]
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
    return FileResponse(out, media_type="audio/mp4" if m4r else "audio/mpeg", filename=nice_name(d, ext, "ringtone"),
                        content_disposition_type="attachment" if dl else "inline",
                        background=BackgroundTask(os.remove, out))


@r_.get("/clip")
def clip(url: str, index: int = 0, start: float = 0, length: float = 15, dl: int = 0):
    src, hdrs, vid, d = media(clean(url), index, info=True)
    if not vid:
        raise HTTPException(400, "This item is a photo, so there is nothing to trim.")
    start, length = max(0.0, min(start, 21600.0)), max(2.0, min(length, 30.0))
    out = f"/tmp/clip-{uuid.uuid4().hex}.mp4"
    head = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y"]
    if hdrs:
        head += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())]
    ss = ["-ss", f"{start:.2f}"]
    tail = ["-t", f"{length:.2f}", "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"] + CLEAN + [out]
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
    return FileResponse(out, media_type="video/mp4", filename=nice_name(d, "mp4", "clip"),
                        content_disposition_type="attachment" if dl else "inline",
                        background=BackgroundTask(os.remove, out))


def _ff(src, hdrs, ss, args, out, timeout=140):
    if "-map_metadata" not in args:
        args = list(args) + CLEAN          # every file WaveRise makes is cleaned of hidden details
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
    src, hdrs, vid, d = media(clean(url), index, info=True)
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
    return _send(r, out, "image/webp" if webp else "image/gif", nice_name(d, "webp" if webp else "gif"), dl, "animation")


@r_.get("/shrink")
def shrink(url: str, index: int = 0, target: float = 16, dur: float = 30, dl: int = 0):
    src, hdrs, vid, d = media(clean(url), index, info=True)
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
    return _send(r, out, "video/mp4", nice_name(d, "mp4", "small"), dl, "smaller video")


def _video_only(vid):
    if not vid:
        raise HTTPException(400, "This item is a photo. Choose a video post.")


@r_.get("/frame")
def frame(url: str, index: int = 0, at: float = -1, dl: int = 0):
    """HD photo from a video: a clear, typical frame picked automatically (at=-1) or the moment you choose."""
    src, hdrs, vid, d = media(clean(url), index, info=True)
    _video_only(vid)
    out = f"/tmp/f-{uuid.uuid4().hex}.jpg"
    if at < 0:
        r = _ff(src, hdrs, 0, ["-t", "20", "-an", "-vf", "thumbnail=90", "-frames:v", "1", "-q:v", "2"] + CLEAN, out)
    else:
        r = _ff(src, hdrs, max(0.0, min(at, 21600.0)), ["-an", "-frames:v", "1", "-q:v", "2"] + CLEAN, out)
    return _send(r, out, "image/jpeg", nice_name(d, "jpg", "photo"), dl, "photo")


MAX_SPLIT_SECONDS = 10 * 60


@r_.get("/status")
def status_part(url: str, index: int = 0, part: int = 1, seg: int = 30, dl: int = 1):
    """One part of a video cut into Status/Story-sized pieces (each part is its own short request)."""
    src, hdrs, vid, d = media(clean(url), index, info=True)
    _video_only(vid)
    seg = seg if seg in (15, 30, 60) else 30
    its = items_of(d)
    dur = d.get("duration") or (its[min(index, len(its) - 1)].get("duration") if its else None) or 0
    if dur > MAX_SPLIT_SECONDS:
        raise HTTPException(413, f"Videos up to {MAX_SPLIT_SECONDS // 60} minutes can be split on this free server.")
    n = max(1, math.ceil(dur / seg)) if dur else 1
    part = max(1, min(part, n))
    out = f"/tmp/p-{uuid.uuid4().hex}.mp4"
    args = ["-t", str(seg), "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"] + CLEAN
    r = _ff(src, hdrs, (part - 1) * seg, args, out)
    return _send(r, out, "video/mp4", nice_name(d, "mp4", f"part-{part}-of-{n}"), dl, f"part {part}")


# ---------- WhatsApp sticker ----------
STICKER_VF = "scale=512:512:force_original_aspect_ratio=decrease:flags=lanczos,pad=512:512:(ow-iw)/2:(oh-ih)/2:color=0x00000000,format=rgba"


@r_.get("/sticker")
def sticker(url: str, index: int = 0, kind: str = "still", at: float = -1, start: float = 0, length: float = 3, dl: int = 0):
    """512x512 WebP sticker with a transparent background: still (under 100 KB) or animated (under 500 KB)."""
    src, hdrs, vid, d = media(clean(url), index, info=True)
    anim = kind == "animated"
    if anim and not vid:
        raise HTTPException(400, "Animated stickers need a video. For photos, choose Still.")
    out = f"/tmp/k-{uuid.uuid4().hex}.webp"
    limit = 500_000 if anim else 100_000
    tries = [(12, 50, 6.0), (10, 38, 5.0), (10, 26, 4.0), (8, 18, 3.0)] if anim else [(0, 80, 0), (0, 62, 0), (0, 45, 0), (0, 30, 0)]
    r = None
    for fps, qv, maxlen in tries:
        if anim:
            ln = max(1.0, min(length, maxlen))
            # compression level 2: level 6 takes minutes on the cloud ffmpeg build, level 2 under a second, still within WhatsApp's 500 KB
            args = ["-t", f"{ln:.2f}", "-an", "-vf", f"fps={fps}," + STICKER_VF, "-c:v", "libwebp_anim", "-loop", "0", "-q:v", str(qv), "-compression_level", "2"]
            r = _ff(src, hdrs, max(0.0, start), args, out)
        else:
            ss = 0 if (not vid or at < 0) else max(0.0, at)
            pick = ["-vf", ("thumbnail=60," if vid and at < 0 else "") + STICKER_VF] + (["-t", "15"] if vid and at < 0 else [])
            r = _ff(src, hdrs, ss, ["-an", "-frames:v", "1"] + pick + ["-c:v", "libwebp", "-q:v", str(qv), "-compression_level", "6"], out)
        if r.returncode or not os.path.exists(out):
            break
        if os.path.getsize(out) <= limit:
            break
    return _send(r, out, "image/webp", nice_name(d, "webp", "sticker"), dl, "sticker")


# ---------- reshape for any app ----------
SHAPES = {"9x16": (720, 1280), "1x1": (720, 720), "4x5": (720, 900), "16x9": (1280, 720)}


@r_.get("/reshape")
def reshape(url: str, index: int = 0, ratio: str = "9x16", fill: str = "blur", dl: int = 0):
    src, hdrs, vid, d = media(clean(url), index, info=True)
    _video_only(vid)
    if ratio not in SHAPES:
        raise HTTPException(400, "Choose 9:16, 1:1, 4:5 or 16:9.")
    its = items_of(d)
    dur = d.get("duration") or (its[min(index, len(its) - 1)].get("duration") if its else None) or 0
    if dur > 180:
        raise HTTPException(413, "Videos up to 3 minutes can be reshaped on this free server.")
    W, H = SHAPES[ratio]
    fg = f"scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1"
    if fill == "crop":
        vf = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[v]"
    elif fill == "black":
        vf = f"[0:v]{fg},pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black[v]"
    else:
        vf = (f"[0:v]split[a][b];[a]scale={W//4}:{H//4}:force_original_aspect_ratio=increase,crop={W//4}:{H//4},boxblur=6:2,scale={W}:{H},eq=brightness=-0.06[bg];"
              f"[b]{fg}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
    out = f"/tmp/r-{uuid.uuid4().hex}.mp4"
    args = ["-filter_complex", vf, "-map", "[v]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
    r = _ff(src, hdrs, 0, args, out, 170)
    return _send(r, out, "video/mp4", nice_name(d, "mp4", ratio.replace("x", "-")), dl, "reshaped video")


# ---------- carousel (or your photos) to Reel ----------
REEL_SHAPES = {"9x16": (720, 1280), "4x5": (720, 900), "1x1": (720, 720)}
FX = {"fade": "fade", "slide": "slideleft", "zoom": "zoomin", "none": None}
GOLD = "0xF2C14E"


def _run(cmd, timeout=170):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "This took too long for the free server. Try fewer or shorter videos.")


def _hdr(h):
    return ["-headers", "".join(f"{k}: {v}\r\n" for k, v in h.items())] if h else []


def _cell(i, W, H, fill, D, tag):
    """Filter that turns input i into a WxH picture lasting D seconds (crop to fill, or fit on a blurred copy)."""
    if fill == "crop":
        f = f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1"
    else:
        w4, h4 = max(W // 4 // 2 * 2, 2), max(H // 4 // 2 * 2, 2)
        f = (f"[{i}:v]split[p{tag}][q{tag}];[p{tag}]scale={w4}:{h4}:force_original_aspect_ratio=increase,crop={w4}:{h4},boxblur=6:2,scale={W}:{H},eq=brightness=-0.06[bg{tag}];"
             f"[q{tag}]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg{tag}];[bg{tag}][fg{tag}]overlay=(W-w)/2:(H-h)/2,setsar=1")
    return f + f",fps=30,tpad=stop_mode=clone:stop_duration={D:.2f},trim=duration={D:.2f},setpts=PTS-STARTPTS"


def _reel(sources, sec, ratio, fill, fx, zoom, name, dl):
    """sources: list of (path_or_url, headers, is_video). Makes an MP4 slideshow with gentle zoom and transitions."""
    if ratio not in REEL_SHAPES:
        raise HTTPException(400, "Choose 9:16, 4:5 or 1:1.")
    n = len(sources)
    if not n:
        raise HTTPException(422, "No photos found to make a Reel.")
    sec = max(1.5, min(float(sec), 6.0))
    if n * sec > 61:
        raise HTTPException(400, f"That would be {round(n * sec)} seconds. Reels made here can be up to 60 seconds: choose fewer seconds per slide.")
    W, H = REEL_SHAPES[ratio]
    tr = FX.get(fx, "fade")
    T = 0.5 if tr and n > 1 else 0.0
    D = sec + T
    frames = int(D * 30)
    cmd, parts = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y"], []
    for i, (src, hdrs, vid) in enumerate(sources):
        cmd += _hdr(hdrs) + ((["-t", f"{D:.2f}"]) if vid else ["-loop", "1", "-framerate", "30", "-t", f"{D:.2f}"]) + ["-i", src]
        f = _cell(i, W, H, fill, D, i)
        if zoom and not vid:   # slow zoom in or out (alternating), worked on a 2x copy so it glides instead of wobbling
            z = f"1+0.07*on/{frames}" if i % 2 == 0 else f"1.07-0.07*on/{frames}"
            f += f",scale={W * 2}:{H * 2}:flags=bicubic,zoompan=z='{z}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={W}x{H}:fps=30,trim=duration={D:.2f},setpts=PTS-STARTPTS"
        parts.append(f + f",fps=30,settb=AVTB,setsar=1,format=yuv420p[v{i}]")
    total = n * sec + T
    if n == 1:
        last = "[v0]"
    elif tr:
        for k in range(1, n):
            prev = "[v0]" if k == 1 else f"[x{k - 1}]"
            parts.append(f"{prev}[v{k}]xfade=transition={tr}:duration={T:.2f}:offset={k * sec:.3f}[x{k}]")
        last = f"[x{n - 1}]"
    else:
        parts.append("".join(f"[v{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0[x]")
        last, total = "[x]", n * D
    cmd += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
    out = f"/tmp/w-{uuid.uuid4().hex}.mp4"
    cmd += ["-filter_complex", ";".join(parts), "-map", last, "-map", f"{n}:a", "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart"] + CLEAN + [out]
    return _send(_run(cmd, 280), out, "video/mp4", name, dl, "Reel")


def _fetch_files(items):
    """Download carousel photos to /tmp (videos are read straight from the link)."""
    srcs, tmp = [], []
    with httpx.Client(timeout=40, follow_redirects=True) as c:
        for e in items:
            h = e.get("http_headers") or {}
            if is_video(e):
                u = e.get("url") or e["formats"][-1]["url"]
                srcs.append((u, h, True)); continue
            u = e.get("url") or e.get("thumbnail")
            if not u:
                continue
            try:
                r = c.get(u, headers=h)
            except httpx.HTTPError:
                continue
            if r.status_code == 200 and len(r.content) > 100:
                p = f"/tmp/i-{uuid.uuid4().hex}"
                open(p, "wb").write(r.content); tmp.append(p); srcs.append((p, {}, False))
    return srcs, tmp


def _rm(paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


@r_.get("/slideshow")
def slideshow(url: str, sec: float = 3, ratio: str = "9x16", fill: str = "blur", fx: str = "fade", zoom: int = 1, dl: int = 0):
    """Carousel to Reel: every slide becomes part of one MP4 (videos play their first seconds, muted)."""
    d = fetch_info(clean(url))
    srcs, tmp = _fetch_files(items_of(d)[:20])
    try:
        return _reel(srcs, sec, ratio, fill, fx, zoom, nice_name(d, "mp4", "reel"), dl)
    finally:
        _rm(tmp)


@r_.post("/slideshow")
async def slideshow_upload(files: list[UploadFile] = File(...), sec: float = Form(3), ratio: str = Form("9x16"), fill: str = Form("blur"),
                           fx: str = Form("fade"), zoom: int = Form(1)):
    """Your own photos to a Reel. The photos are deleted as soon as the video is made."""
    tmp = []
    try:
        for f in files[:20]:
            b = await f.read()
            if len(b) > 100:
                p = f"/tmp/u-{uuid.uuid4().hex}"
                open(p, "wb").write(b); tmp.append(p)
        return _reel([(p, {}, False) for p in tmp], sec, ratio, fill, fx, zoom, "my-photos-reel.mp4", 1)
    finally:
        _rm(tmp)


@r_.get("/download")
def download(request: Request, url: str, index: int = 0, inline: int = 0, q: int = 0, loud: int = 0, privacy: int = Query(0, alias="clean")):
    u = clean(url)
    if site_of(u) in PLAN_SITES:
        d = fetch_info(u)
        plans, _ = _yt_plans(d)
        dflt = _yt_default(plans)
        h = q if (q in plans and not inline) else dflt
        p = plans[h]
        if p[0] == "one":
            if not (loud or privacy) or inline:
                return stream(p[1]["url"], _hd(p[1], d), None if inline else nice_name(d, "mp4", f"{h}p"), request.headers.get("range"))
            return _ff_stream(d, [p[1]], h, 0, loud, privacy, nice_name(d, "mp4", f"{h}p"))
        if h != dflt and (d.get("duration") or 0) > MAX_HD_SECONDS:
            raise HTTPException(413, f"HD downloads are limited to videos under {MAX_HD_SECONDS // 60} minutes on this free server. Choose {dflt}p instead.")
        return _ff_stream(d, list(p[1:]), h, inline, loud, privacy, nice_name(d, "mp4", f"{h}p"))
    src, hdrs, vid, d = media(u, index, info=True)
    if inline:
        return stream(src, hdrs, None, request.headers.get("range"))
    name = nice_name(d, "mp4" if vid else "jpg", "", index)
    if vid and (loud or privacy):
        return _ff_stream(d, [{"url": src, "http_headers": hdrs}], 0, 0, loud, privacy, name)
    if not vid and privacy:
        try:
            with httpx.Client(timeout=60, follow_redirects=True) as c:
                r = c.get(src, headers=hdrs)
        except httpx.HTTPError:
            raise HTTPException(502, "Couldn't fetch the photo. Try again.")
        if len(r.content) > 40 * 1048576:
            raise HTTPException(413, "This photo is too large to clean here.")
        return Response(jpeg_clean(r.content), media_type=r.headers.get("content-type", "image/jpeg"), headers={"Content-Disposition": disposition(name)})
    return stream(src, hdrs, name, request.headers.get("range"))


def _ff_stream(d, inputs, q, inline=0, loud=0, privacy=0, name=None):
    """Stream one MP4 made from separate picture+sound, or from a streaming playlist, without temp files."""
    cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error"]
    for f in inputs:
        hd = _hd(f, d)
        if hd:
            cmd += ["-headers", "".join(f"{k}: {x}\r\n" for k, x in hd.items())]
        cmd += ["-i", f["url"]]
    hls = len(inputs) == 1 and "m3u8" in (str(inputs[0].get("protocol", "")) + inputs[0]["url"])
    cmd += ["-map", "0:v:0", "-map", "1:a:0"] if len(inputs) == 2 else ["-map", "0:v:0", "-map", "0:a:0?"]
    cmd += ["-c:v", "copy"] + (["-af", LOUD, "-c:a", "aac", "-b:a", "160k"] if loud else ["-c:a", "copy"] + (["-bsf:a", "aac_adtstoasc"] if hls else []))
    cmd += (CLEAN if privacy else []) + ["-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    first = p.stdout.read(65536)
    if not first:
        err = p.stderr.read().decode(errors="ignore")[-200:]
        p.wait()
        print("[waverise] video stream failed:", err)
        raise HTTPException(422, "Couldn't prepare this video. Try another quality.")

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
    return StreamingResponse(gen(), media_type="video/mp4", headers={} if inline else {"Content-Disposition": disposition(name or nice_name(d, "mp4", f"{q}p" if q else ""))})


# ---------- Share → WaveRise (installed app). The service worker normally handles this on the phone; this is the fallback
# when it hasn't started yet: links go to the downloader, files go to Status Saver with a "share again" note. ----------
@app.post("/share-target", include_in_schema=False)
async def share_target(request: Request):
    from urllib.parse import urlencode
    from fastapi.responses import RedirectResponse
    try:
        form = await request.form()
    except Exception:
        return RedirectResponse("/whatsapp-status-saver?share_failed=1", status_code=303)
    if any(getattr(v, "filename", None) for _, v in form.multi_items()):
        return RedirectResponse("/whatsapp-status-saver?share_failed=1", status_code=303)
    q = {k: str(form.get(k))[:2000] for k in ("title", "text", "url") if form.get(k)}
    return RedirectResponse("/" + ("?" + urlencode(q) if q else ""), status_code=303)


# ---------- Public SEO pages, sitemap.xml, robots.txt, redirects, 404 log (wr_seo) and the private admin area (wr_admin) ----------
import wr_seo, wr_admin
SEO = wr_seo.SEO
wr_admin.install(app)
wr_seo.install(app, {getattr(r, "path", "") for r in r_.routes})


# ---------- Caption & hashtag writer (Groq AI when GROQ_API_KEY is set, built-in writer otherwise) ----------
import random
GROQ_MODELS = [m for m in [os.getenv("GROQ_MODEL", "").strip(), "openai/gpt-oss-120b", "openai/gpt-oss-20b"] if m]
AI_PER_HOUR = int(os.getenv("AI_PER_HOUR", "30"))     # AI requests per visitor per hour; after that the built-in writer answers
_ai_hits = {}
LANGS = {"en": "English", "hi": "Hindi written in Devanagari script", "ta": "Tamil written in Tamil script", "te": "Telugu written in Telugu script",
         "hinglish": "Hinglish (Hindi written in English letters, the way people text in India)"}
TONES = {"fun": "fun, witty and playful", "heart": "warm, heartfelt and emotional", "classy": "classy, minimal and elegant",
         "short": "very short and punchy (under 8 words)", "promo": "a friendly sales post for a small business, with a clear call to action"}
EMO = {"fun": ["😂", "✨", "🤪"], "heart": ["❤️", "🥹", "🌸"], "classy": ["✨", "🤍", "🌙"], "short": ["🔥", "😎", "⚡"], "promo": ["🛍️", "✨", "📦"]}
TPL = {
 "en": {"fun": ["Plot twist: {t} was the best part of my week {e}", "Me, pretending to be normal about {t} {e}", "Not me posting {t} again… okay, yes me {e}", "{t}? Say less {e}"],
        "heart": ["Some moments stay with you forever. {t} is one of them {e}", "Grateful for {t} and the people who make it special {e}", "Little moments, big memories: {t} {e}"],
        "classy": ["{t}. Simple, quiet, beautiful.", "A little bit of {t}, a lot of happiness {e}", "Collecting moments, not things: {t}"],
        "short": ["{t} {e}", "Mood: {t}", "{t} mode: on {e}"],
        "promo": ["Now available: {t}! DM us to order {e}", "{t} is here. Limited stock, grab yours today {e}", "Looking for {t}? We've got you covered. Message us now {e}"]},
 "hi": {"fun": ["{t} देख के दिल बोला — बस यही चाहिए था {e}", "दुनिया एक तरफ़, {t} एक तरफ़ {e}", "नॉर्मल रहने की कोशिश की, पर {t} ने सब बदल दिया {e}"],
        "heart": ["कुछ पल हमेशा दिल में रह जाते हैं, {t} उनमें से एक है {e}", "{t} और अपनों का साथ — इससे बढ़कर क्या {e}", "छोटे पल, बड़ी यादें: {t} {e}"],
        "classy": ["{t}. सादगी में ही ख़ूबसूरती है।", "थोड़ा सा {t}, ढेर सारी ख़ुशी {e}", "पल इकट्ठा करने का शौक़: {t}"],
        "short": ["{t} {e}", "आज का मूड: {t}", "{t} वाला दिन {e}"],
        "promo": ["अब उपलब्ध: {t}! ऑर्डर के लिए DM करें {e}", "{t} आ गया है। स्टॉक सीमित है, आज ही लें {e}", "{t} चाहिए? हमसे संपर्क करें {e}"]},
 "ta": {"fun": ["{t} பார்த்ததும் மனசு சொன்னது — இதுதான் வேணும் {e}", "உலகம் ஒரு பக்கம், {t} ஒரு பக்கம் {e}", "சாதாரணமா இருக்கப் பார்த்தேன், {t} எல்லாத்தையும் மாத்திடுச்சு {e}"],
        "heart": ["சில தருணங்கள் என்றும் மனதில் நிற்கும், {t} அதில் ஒன்று {e}", "{t}, கூடவே நம்ம மக்கள் — இதைவிட வேற என்ன வேணும் {e}", "சின்ன தருணங்கள், பெரிய நினைவுகள்: {t} {e}"],
        "classy": ["{t}. எளிமையே அழகு.", "கொஞ்சம் {t}, நிறைய சந்தோஷம் {e}", "பொருட்கள் அல்ல, தருணங்களைச் சேர்க்கிறேன்: {t}"],
        "short": ["{t} {e}", "இன்றைய மூட்: {t}", "{t} டே {e}"],
        "promo": ["இப்போது கிடைக்கிறது: {t}! ஆர்டர் செய்ய DM செய்யுங்கள் {e}", "{t} வந்துவிட்டது. குறைந்த அளவே உள்ளது, இன்றே வாங்குங்கள் {e}", "{t} வேண்டுமா? உடனே எங்களைத் தொடர்பு கொள்ளுங்கள் {e}"]},
 "te": {"fun": ["{t} చూడగానే మనసు చెప్పింది — ఇదే కావాలి {e}", "ప్రపంచం ఒక వైపు, {t} ఒక వైపు {e}", "నార్మల్‌గా ఉండాలనుకున్నా, కానీ {t} అంతా మార్చేసింది {e}"],
        "heart": ["కొన్ని క్షణాలు ఎప్పటికీ గుండెలో ఉంటాయి, {t} అందులో ఒకటి {e}", "{t}, మనవాళ్ల తోడు — ఇంతకంటే ఏం కావాలి {e}", "చిన్న క్షణాలు, పెద్ద జ్ఞాపకాలు: {t} {e}"],
        "classy": ["{t}. సింప్లిసిటీలోనే అందం.", "కొంచెం {t}, బోలెడంత ఆనందం {e}", "వస్తువులు కాదు, క్షణాలు సేకరిస్తున్నా: {t}"],
        "short": ["{t} {e}", "ఈరోజు మూడ్: {t}", "{t} డే {e}"],
        "promo": ["ఇప్పుడు అందుబాటులో: {t}! ఆర్డర్ కోసం DM చేయండి {e}", "{t} వచ్చేసింది. స్టాక్ పరిమితం, ఈరోజే తీసుకోండి {e}", "{t} కావాలా? ఇప్పుడే మమ్మల్ని సంప్రదించండి {e}"]},
 "hinglish": {"fun": ["{t} dekh ke dil bola — bas yahi chahiye tha {e}", "Duniya ek taraf, {t} ek taraf {e}", "Normal rehne ki koshish ki, par {t} ne sab badal diya {e}"],
        "heart": ["Kuch pal hamesha dil mein reh jaate hain, {t} unmein se ek hai {e}", "{t} aur apno ka saath — isse badhkar kya {e}", "Chhote pal, badi yaadein: {t} {e}"],
        "classy": ["{t}. Saadgi mein hi khoobsurti hai.", "Thoda sa {t}, dher saari khushi {e}", "Cheezein nahi, pal collect karte hain: {t}"],
        "short": ["{t} {e}", "Aaj ka mood: {t}", "{t} wala din {e}"],
        "promo": ["Ab available: {t}! Order ke liye DM karein {e}", "{t} aa gaya hai. Limited stock, aaj hi lein {e}", "{t} chahiye? Abhi message karein {e}"]},
}
TAGS = [
 (("food", "biryani", "dosa", "idli", "chai", "coffee", "cake", "recipe", "cooking", "restaurant", "lunch", "dinner", "breakfast", "sweets", "street food", "pizza", "thali"),
  ["#foodie", "#foodlover", "#indianfood", "#instafood", "#foodphotography", "#homemade", "#streetfood"]),
 (("travel", "trip", "beach", "mountain", "hills", "goa", "kerala", "manali", "ooty", "temple", "sunset", "sunrise", "road trip", "vacation", "holiday", "marina"),
  ["#travel", "#travelgram", "#incredibleindia", "#wanderlust", "#travelphotography", "#explore", "#naturelovers"]),
 (("wedding", "marriage", "shaadi", "bride", "groom", "engagement", "haldi", "mehendi", "sangeet", "reception"),
  ["#wedding", "#indianwedding", "#shaadi", "#bride", "#weddingphotography", "#mehendi", "#couplegoals"]),
 (("gym", "workout", "fitness", "yoga", "running", "cricket", "football", "sports", "badminton"),
  ["#fitness", "#gym", "#workout", "#fitindia", "#yoga", "#healthylifestyle", "#motivation"]),
 (("dance", "song", "music", "singing", "cover", "guitar", "lyrics"), ["#dance", "#dancevideo", "#reelsindia", "#music", "#singing", "#cover"]),
 (("saree", "outfit", "fashion", "dress", "kurta", "style", "makeup", "jewellery", "lehenga"), ["#fashion", "#saree", "#ootd", "#style", "#indianwear", "#ethnicwear", "#makeup"]),
 (("family", "friends", "birthday", "mom", "dad", "amma", "appa", "baby", "kids", "anniversary", "love"), ["#family", "#friends", "#love", "#memories", "#happymoments", "#togetherness"]),
 (("rain", "flower", "garden", "nature", "sky", "monsoon", "river", "waterfall"), ["#nature", "#naturephotography", "#monsoon", "#skylovers", "#naturelovers"]),
 (("shop", "sale", "offer", "order", "store", "business", "new arrival", "handmade", "discount", "collection", "available"),
  ["#smallbusiness", "#shoplocal", "#onlineshopping", "#handmade", "#sale", "#supportsmallbusiness", "#madeinindia"]),
 (("dog", "cat", "puppy", "kitten", "pet"), ["#pets", "#doglover", "#catlover", "#petsofinstagram"]),
 (("funny", "comedy", "meme", "prank", "joke"), ["#funny", "#comedy", "#memes", "#reelsindia"]),
]
FEST_TAGS = {"diwali": "#happydiwali", "deepavali": "#happydeepavali", "pongal": "#happypongal", "sankranti": "#makarsankranti", "holi": "#happyholi", "eid": "#eidmubarak",
             "christmas": "#merrychristmas", "navratri": "#navratri", "dussehra": "#dussehra", "ganesh": "#ganeshchaturthi", "onam": "#happyonam", "ugadi": "#happyugadi", "rakhi": "#rakshabandhan"}
LANG_TAGS = {"ta": ["#tamil", "#tamilreels"], "te": ["#telugu", "#telugureels"], "hi": ["#hindi", "#hindireels"], "hinglish": ["#desi", "#hindireels"], "en": ["#india"]}
STOP = {"with", "from", "this", "that", "have", "were", "into", "your", "about", "after", "before", "today", "first", "some", "very", "just", "post", "video", "photo", "reel"}


def _short_topic(t):
    t = re.sub(r"https?://\S+|[#@][\w.]+", " ", t or "")
    t = re.sub(r"\s+", " ", t).strip(" .,!-–—")
    t = re.split(r"(?<=[.!?।])\s|\n", t)[0].strip(" .,!")
    if len(t) > 70:
        t = t[:70].rsplit(" ", 1)[0]
    return t


def quick_tags(text, lang):
    low = (text or "").lower()
    out = []
    for f, tag in FEST_TAGS.items():
        if f in low:
            out += [tag, "#festival"]
    cats = [tags for keys, tags in TAGS if any(re.search(r"(?<![a-z])" + re.escape(k), low) for k in keys)]
    per = 6 if len(cats) <= 1 else 4 if len(cats) == 2 else 3      # several topics: a few tags from each
    for tags in cats:
        out += tags[:per]
    words = [w for w in re.findall(r"[a-z]{4,}", low) if w not in STOP][:3]
    out += ["#" + w for w in words]
    if len(words) >= 2:
        out.append("#" + words[0] + words[1])
    out = out[:14] + LANG_TAGS.get(lang, []) + ["#reels", "#reelsinstagram", "#instagood", "#explore", "#trending"]
    seen, res = set(), []
    for t in out:
        if t not in seen:
            seen.add(t); res.append(t)
    return res[:20]


def quick_write(topic, lang, tone, emoji, seed=None):
    lang = lang if lang in TPL else "en"
    tone = tone if tone in TPL[lang] else "fun"
    t = _short_topic(topic) or "this moment"
    rnd = random.Random(seed)
    pool = list(TPL[lang][tone])
    rnd.shuffle(pool)
    em = EMO[tone]
    caps = []
    for i, p in enumerate(pool[:3]):
        e = em[i % len(em)] if emoji else ""
        c = p.replace("{t}", t).replace("{e}", e).strip()
        caps.append(c[:1].upper() + c[1:] if lang in ("en", "hinglish") else c)
    return {"captions": caps, "hashtags": quick_tags(topic, lang), "ai": False}


def _groq(topic, ctx, lang, tone, emoji):
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        return None
    sys_p = ("You write Instagram and WhatsApp captions for Indian creators and small businesses. "
             "Reply with JSON only, exactly: {\"captions\": [three different captions], \"hashtags\": [15 hashtags]}. "
             "Each caption under 220 characters. Hashtags start with #, have no spaces, mostly in English letters, mixing popular and specific ones; "
             "add 1 or 2 in the caption language when it fits. Never include links, phone numbers or made-up facts, prices or dates.")
    user_p = (f"Post: {topic or '(not given)'}\n" + (f"Existing caption of the post for context: {ctx}\n" if ctx else "") +
              f"Language: {LANGS.get(lang, 'English')}\nTone: {TONES.get(tone, TONES['fun'])}\nEmojis: {'yes, a few' if emoji else 'no emojis'}")
    body = {"messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}], "temperature": 0.9,
            "max_completion_tokens": 1600, "response_format": {"type": "json_object"}, "reasoning_effort": "low"}
    with httpx.Client(timeout=25) as c:
        for model in GROQ_MODELS:
            for extra in (True, False):
                b = dict(body, model=model)
                if not extra:
                    b.pop("reasoning_effort", None); b.pop("response_format", None)
                try:
                    r = c.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=b)
                except httpx.HTTPError as e:
                    print("[waverise] Groq unreachable:", e); return None
                if r.status_code == 200:
                    try:
                        txt = r.json()["choices"][0]["message"]["content"] or ""
                        m = re.search(r"\{.*\}", txt, re.S)
                        j = json.loads(m.group(0) if m else txt)
                        caps = [str(x).strip() for x in j.get("captions", []) if str(x).strip()][:3]
                        tags = []
                        for t in j.get("hashtags", []):
                            t = "#" + re.sub(r"[\s#]+", "", str(t))
                            if len(t) > 1 and t not in tags:
                                tags.append(t)
                        if caps:
                            return {"captions": caps, "hashtags": tags[:20] or quick_tags(topic + " " + ctx, lang), "ai": True}
                    except (ValueError, KeyError, IndexError, AttributeError):
                        pass
                    break                                  # answered but unusable: try the next model
                if r.status_code in (401, 403, 429):     # bad key or out of free quota: use the built-in writer
                    print("[waverise] Groq said", r.status_code, r.text[:200]); return None
                if r.status_code == 400 and extra:
                    continue                              # maybe this model doesn't take one of the options: try plain
                break                                      # model missing or retired: try the next one
    return None


class WriteReq(BaseModel):
    topic: str = ""
    url: str = ""
    lang: str = "en"
    tone: str = "fun"
    emoji: bool = True


@r_.post("/write")
def write(b: WriteReq, request: Request):
    topic, ctx = (b.topic or "").strip()[:400], ""
    if (b.url or "").strip():
        d = fetch_info(clean(b.url.strip()))
        ctx = re.sub(r"\s+", " ", str(d.get("description") or d.get("title") or "")).strip()[:600]
    if not topic and not ctx:
        raise HTTPException(400, "Tell us what your post is about, or paste a post link.")
    lang = b.lang if b.lang in LANGS else "en"
    tone = b.tone if b.tone in TONES else "fun"
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "") or "?").split(",")[0].strip()
    now = time.time()
    hits = [t for t in _ai_hits.get(ip, []) if now - t < 3600]
    res = None
    if len(hits) < AI_PER_HOUR:
        res = _groq(topic, ctx, lang, tone, b.emoji)
        if res:
            hits.append(now)
    _ai_hits[ip] = hits
    if len(_ai_hits) > 5000:
        _ai_hits.clear()
    return res or quick_write(topic or ctx, lang, tone, b.emoji)


@r_.get("/health")
def health():
    return {"ok": True, "version": 13, "coming_soon": sorted(SOON), "youtube_engine": next(iter(js_runtime()), None),
            "ai_writer": bool(os.getenv("GROQ_API_KEY", "").strip())}


app.include_router(r_, prefix="/api")
app.include_router(r_)

if not os.getenv("VERCEL"):  # local testing only; on Vercel the public/ folder is served by the CDN
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=PUBLIC))
