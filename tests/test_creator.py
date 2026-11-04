"""Verify batch access, removed routes, and upstream media error handling."""
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as backend


@pytest.fixture
def client():
    return TestClient(backend.app)


def test_batch_page_and_sitemap(client):
    response = client.get("/batch-downloads")
    assert response.status_code == 200
    assert '<script src="/creator.js?v=2">' in response.text
    assert "http://testserver/batch-downloads" in response.text
    assert "batch-downloads" in client.get("/sitemap.xml").text


@pytest.mark.parametrize("slug", ["auto-subtitles", "reel-size-converter", "brand-kit", "download-diagnostics"])
def test_removed_tools_are_not_routed_or_listed(client, slug):
    assert client.get("/" + slug).status_code == 404
    assert slug not in client.get("/sitemap.xml").text
    assert '"s":"' + slug + '"' not in client.get("/").text


def test_removed_api_routes_are_not_registered():
    paths = {getattr(route, "path", "") for route in backend.app.routes}
    assert not any("/creator/" in path or path in ("/diagnose", "/api/diagnose") for path in paths)


@pytest.mark.parametrize("path", ["creator.js", "creator-core.js", "creator.css"])
def test_batch_assets_served(client, path):
    assert client.get("/" + path).status_code == 200


@pytest.mark.parametrize("status,mime,expected", [(403, "text/html", 502), (200, "text/html", 502),
                                                  (404, "text/html", 404), (429, "application/json", 429)])
def test_media_errors_are_not_saved_as_videos(monkeypatch, status, mime, expected):
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
    assert error.value.status_code == expected
    assert closed
    assert post not in backend._info_cache
