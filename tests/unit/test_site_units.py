"""Unit tests for the website's own helpers (no network)."""
import app
import wr_guides


def test_site_detection_and_link_cleaning():
    assert app.site_of("https://www.instagram.com/reel/ABC/") == "instagram"
    assert app.site_of("https://x.com/a/status/1") == "x"
    assert app.site_of("https://fb.watch/abc/") == "facebook"


def test_nice_name_is_safe_and_readable():
    n = app.nice_name({"uploader": "travel.daily", "description": "Sunset ride #goa"}, "mp4", "720p")
    assert n.endswith("-720p.mp4") and "/" not in n and " " not in n


def test_guides_render_and_link_to_tools():
    html = wr_guides.article_html(wr_guides.GUIDES[0])
    assert "<h1>" in html and 'href="/photo-mb-to-kb"' in html and 'class="adanchor"' in html
    assert wr_guides.index_html().count('class="gcard"') == len(wr_guides.GUIDES)
