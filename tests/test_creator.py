"""Run with: python -m pytest -q. Real FFmpeg fixtures need ffmpeg + ffprobe."""
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as backend
import wr_creator as creator


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(creator, "limit_request", lambda *a, **kw: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return TestClient(backend.app)


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real video tests require ffmpeg and ffprobe")
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=12", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
                    "-t", "1", "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", "-threads", "1", str(path)], check=True)
    return path.read_bytes()


def upload(video):
    return {"file": ("sample.mp4", video, "video/mp4")}


def inspect_video(blob, tmp_path):
    path = tmp_path / "output.mp4"
    path.write_bytes(blob)
    result = subprocess.run(["ffprobe", "-v", "quiet", "-show_streams", "-of", "json", str(path)],
                            capture_output=True, check=True)
    return json.loads(result.stdout)["streams"]


@pytest.mark.parametrize("slug", ["batch-downloads", "auto-subtitles", "reel-size-converter", "brand-kit", "download-diagnostics"])
def test_creator_pages_and_assets(client, slug):
    r = client.get("/" + slug)
    assert r.status_code == 200
    assert '<script src="/creator.js?v=1">' in r.text
    assert f'http://testserver/{slug}' in r.text
    assert slug in client.get("/sitemap.xml").text


@pytest.mark.parametrize("path", ["creator.js", "creator-core.js", "creator.css"])
def test_assets_served(client, path):
    assert client.get("/" + path).status_code == 200


def test_capabilities_do_not_leak_key(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-private-key")
    r = client.get("/api/creator/capabilities")
    assert r.status_code == 200
    assert r.json()["transcription"] is True
    assert "test-private-key" not in r.text


def test_transcription_without_configuration(client):
    r = client.post("/api/creator/transcribe", files=upload(b"x"))
    assert r.status_code == 503
    assert "manually" in r.json()["detail"]


@pytest.mark.parametrize("shape,fit", [("vertical", "crop"), ("square", "pad"), ("landscape", "blur"), ("portrait", "crop")])
def test_resize_creates_correct_dimensions_and_audio(client, video, tmp_path, shape, fit):
    r = client.post("/api/creator/resize", files=upload(video), data={"shape": shape, "fit": fit, "x": .25, "y": .75})
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]
    streams = inspect_video(r.content, tmp_path)
    stream = next(s for s in streams if s["codec_type"] == "video")
    assert (stream["width"], stream["height"]) == creator.SIZES[shape]
    assert stream["codec_name"] == "h264"
    assert stream["sample_aspect_ratio"] == "1:1"
    assert any(s["codec_type"] == "audio" for s in streams)


def test_bad_inputs_and_upload_limit(client, monkeypatch):
    assert client.post("/api/creator/resize", files=upload(b"x"), data={"shape": "unbounded"}).status_code == 422
    assert client.post("/api/creator/resize", files=upload(b"not a video")).status_code == 422
    monkeypatch.setattr(creator, "upload_limit", lambda: 8)
    assert client.post("/api/creator/resize", files=upload(b"123456789")).status_code == 413


@pytest.mark.parametrize("cues", [[], [{"start": 2, "end": 1, "text": "wrong"}],
                                   [{"start": 0, "end": 1, "text": "a"}, {"start": .5, "end": 2, "text": "b"}],
                                   [{"start": 0, "end": float("nan"), "text": "bad"}],
                                   [{"start": 0, "end": 181, "text": "long"}], [None]])
def test_caption_validation(cues):
    with pytest.raises(HTTPException):
        creator.validate_cues(cues)


def test_unicode_srt_and_format_controls():
    cues = creator.validate_cues([{"start": 0, "end": 1.2, "text": "<b>తెలుగు</b> {\\pos(1,2)} నమస్తే"}])
    result = creator.srt(cues)
    assert "00:00:00,000 --> 00:00:01,200" in result
    assert "తెలుగు" in result and "నమస్తే" in result
    assert "<b>" not in result and "pos(" not in result


def test_captioned_mp4_and_temp_cleanup(client, video, tmp_path, monkeypatch):
    if not client.get("/api/creator/capabilities").json()["subtitle_video"]:
        pytest.skip("FFmpeg lacks libass")
    original = creator.tempfile.TemporaryDirectory
    directories = []
    def track(*args, **kwargs):
        temp = original(*args, **kwargs)
        directories.append(Path(temp.name))
        return temp
    monkeypatch.setattr(creator.tempfile, "TemporaryDirectory", track)
    cues = [{"start": .1, "end": .8, "text": "Hello WaveRise"}]
    r = client.post("/api/creator/subtitle-video", files=upload(video), data={"cues": json.dumps(cues)})
    assert r.status_code == 200, r.text
    assert any(s["codec_type"] == "video" for s in inspect_video(r.content, tmp_path))
    assert all(not directory.exists() for directory in directories)
    cues[0]["end"] = 2
    assert client.post("/api/creator/subtitle-video", files=upload(video), data={"cues": json.dumps(cues)}).status_code == 422
    assert all(not directory.exists() for directory in directories)


def test_transcription_provider_request_and_normalized_cues(client, monkeypatch, video):
    monkeypatch.setenv("OPENAI_API_KEY", "test-private-key")
    calls = []
    class SpeechClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            assert kwargs["files"]["file"][1].read(3) in (b"ID3", b"\xff\xf3\x88", b"\xff\xfb\x88")
            return httpx.Response(200, json={"language": "telugu", "segments": [
                {"start": 0, "end": .6, "text": "నమస్తే"},
                {"start": .5, "end": 1.1, "text": "WaveRise"}]})
    monkeypatch.setattr(creator.httpx, "AsyncClient", SpeechClient)
    r = client.post("/api/creator/transcribe", files=upload(video), data={"language": "te"})
    assert r.status_code == 200, r.text
    assert calls[0][1]["data"]["language"] == "te"
    assert calls[0][0] == "https://api.openai.com/v1/audio/transcriptions"
    assert r.json()["cues"][1]["start"] == .6
    assert r.json()["cues"][1]["end"] <= 1.1
    assert "test-private-key" not in r.text


@pytest.mark.parametrize("status,message,code", [
    (429, "limited", "rate_limited"), (504, "slow", "timeout"), (400, "bad URL", "invalid_link"),
    (503, "coming soon", "unsupported_platform"), (403, "login required", "access_limited"), (451, "legal", "restricted")])
def test_download_error_diagnostics(client, monkeypatch, status, message, code):
    def fail(url): raise HTTPException(status, message)
    monkeypatch.setattr(backend, "fetch_info", fail)
    r = client.post("/api/info", json={"url": "https://www.instagram.com/reel/abc123/"})
    assert r.status_code == status
    assert r.json()["diagnostic"]["code"] == code
    assert r.headers["cache-control"] == "no-store"
    result = client.post("/api/diagnose", json={"url": "https://www.instagram.com/reel/abc123/"})
    assert result.json()["ok"] is False
    assert result.json()["diagnostic"]["code"] == code


def test_diagnostics_success(client, monkeypatch):
    monkeypatch.setattr(backend, "info", lambda b: {"site": "instagram", "items": [{"type": "video"}]})
    r = client.post("/api/diagnose", json={"url": "https://www.instagram.com/reel/abc123/"})
    assert r.json()["ok"] is True
    assert r.json()["items"] == 1


@pytest.mark.parametrize("status,mime,expected", [(403, "text/html", "media_expired"),
                                                  (200, "text/html", "media_expired"),
                                                  (404, "text/html", "not_found"),
                                                  (429, "application/json", "rate_limited")])
def test_media_errors_are_not_saved_as_videos(client, monkeypatch, status, mime, expected):
    closed = []
    class MediaClient:
        def __init__(self, **kwargs): pass
        def build_request(self, *args, **kwargs): return None
        def send(self, *args, **kwargs): return httpx.Response(status, headers={"content-type": mime}, content=b"upstream error")
        def close(self): closed.append(True)
    monkeypatch.setattr(backend.httpx, "Client", MediaClient)
    post = "https://www.instagram.com/reel/expired/"
    backend._info_cache[post] = (0, "{}")
    with pytest.raises(HTTPException) as error:
        backend.stream("https://media.example/video.mp4", filename="test.mp4", cache_key=post)
    assert creator.diagnose(error.value.status_code, error.value.detail)["code"] == expected
    assert closed
    assert post not in backend._info_cache
