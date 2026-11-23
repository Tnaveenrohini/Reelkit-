"""Language pages, hreflang, one main heading, slimmer pages and IndexNow."""
import json
import re

import pytest
from fastapi.testclient import TestClient

import app as backend
import wr_local as LC
import wr_seo
from wr_store import STORE, _Memory


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(STORE, "r", _Memory())
    monkeypatch.setattr(STORE, "kind", "memory")
    monkeypatch.setattr(STORE, "_cache", {})


@pytest.fixture
def c():
    return TestClient(backend.app)


def h1s(t):
    return re.findall(r"<h1[\s>]", t)


def test_every_language_page_opens_in_its_language(c):
    for lang in LC.LANGS:
        for key in LC.LOC[lang]:
            r = c.get(wr_seo.path_of(key, lang))
            assert r.status_code == 200, (lang, key)
            t = r.text
            row = LC.get(lang, key)
            assert f'<html lang="{lang}"' in t
            assert f"<title>{wr_seo.esc(row['t'])}</title>" in t
            assert f'rel="canonical" href="http://testserver{wr_seo.path_of(key, lang)}"' in t
            assert len(h1s(t)) == 1, (lang, key)


def test_hreflang_points_both_ways(c):
    en = c.get("/instagram-reels-downloader").text
    te = c.get("/te/instagram-reels-downloader").text
    for t in (en, te):
        for l in ("en", "te", "hi", "ta", "x-default"):
            assert f'hreflang="{l}"' in t
    assert 'hreflang="x-default" href="http://testserver/instagram-reels-downloader"' in te


def test_unknown_language_page_is_404(c):
    assert c.get("/te/about").status_code == 404
    assert c.get("/te/instagram-reels-downloader/", follow_redirects=False).status_code == 301


def test_sitemap_lists_language_pages_with_alternates(c):
    sm = c.get("/sitemap.xml").text
    assert "xmlns:xhtml" in sm
    assert "<loc>http://testserver/te/instagram-reels-downloader</loc>" in sm
    assert "<loc>http://testserver/hi</loc>" in sm
    assert sm.count("<loc>") == len(set(re.findall(r"<loc>([^<]+)</loc>", sm)))


def test_pages_carry_only_their_own_info_text(c):
    tool = c.get("/photo-mb-to-kb").text
    assert 'data-page="privacy"' not in tool and 'data-page="about"' not in tool
    about = c.get("/about").text
    assert 'data-page="about"' in about and 'data-page="privacy"' not in about
    assert len(h1s(about)) == 1


def test_one_main_heading_everywhere(c):
    for p in ("/", "/chat", "/instagram-reels-downloader", "/guides", "/faq"):
        assert len(h1s(c.get(p).text)) == 1, p


def test_tool_pages_have_breadcrumbs(c):
    t = c.get("/ta/pdf-to-jpg").text
    ld = json.loads(re.search(r'id="wr-ld">(.*?)</script>', t, re.S).group(1))
    types = [x["@type"] for x in ld["@graph"]]
    assert "BreadcrumbList" in types
    assert ld["@graph"][0]["inLanguage"] == "ta"


def test_indexnow_key_file(c):
    r = c.get(f"/{wr_seo.INDEXNOW_KEY}.txt")
    assert r.status_code == 200 and r.text == wr_seo.INDEXNOW_KEY


def test_indexnow_sends_once_per_deploy_and_only_in_production(monkeypatch):
    sent = []

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: sent.append(json.loads(req.data)) or Resp())
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("SITE_URL", raising=False)
    assert wr_seo.indexnow_once("https://example.in") is None and not sent
    monkeypatch.setenv("VERCEL_ENV", "production")
    assert wr_seo.indexnow_once("https://example.in") == 200
    assert wr_seo.indexnow_once("https://example.in") is None
    assert len(sent) == 1 and sent[0]["host"] == "example.in" and "https://example.in/te" in sent[0]["urlList"]
