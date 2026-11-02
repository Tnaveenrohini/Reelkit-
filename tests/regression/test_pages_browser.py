"""Regression tests in a real browser (Chromium): the main pages load on a phone and a desktop without JavaScript errors or
sideways scrolling, the chat box is not covered by the footer, and the page's error reporter sends real errors to /api/errors."""
import os
import socket
import threading
import time

import pytest

playwright = pytest.importorskip("playwright.sync_api")
PAGES = ["/", "/instagram-video-downloader", "/photo-mb-to-kb", "/guides"]


@pytest.fixture(scope="module")
def site():
    os.environ.pop("DATABASE_URL", None)
    import uvicorn

    import app as site_app
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(site_app.app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(300):
        if server.started:
            break
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(5)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_page(browser, site, path, width):
    ctx = browser.new_context(viewport={"width": width, "height": 780 if width < 600 else 900})
    page = ctx.new_page()
    errors, reports = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # only our own server: third-party scripts (fonts, ads) are blocked so the test is offline and repeatable
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(site) else route.abort())
    page.on("request", lambda r: reports.append(r.post_data) if r.url.endswith("/api/errors") else None)
    page.goto(site + path, wait_until="load")
    page.wait_for_timeout(400)
    return ctx, page, errors, reports


@pytest.mark.parametrize("width", [360, 1280])
@pytest.mark.parametrize("path", PAGES)
def test_page_loads_without_errors_or_sideways_scroll(browser, site, path, width):
    ctx, page, errors, reports = open_page(browser, site, path, width)
    try:
        assert errors == [], errors
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, f"{path} scrolls sideways by {overflow}px at {width}px"
        assert reports == [], "a clean page load must not report errors"
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [360, 390, 1280])
def test_link_box_is_not_covered_by_the_footer(browser, site, width):
    ctx, page, errors, _ = open_page(browser, site, "/", width)
    try:
        phone = width < 600
        if phone:      # on phones the footer is pinned above the bottom link box
            page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(300)
        r = page.evaluate("""(() => { const f = document.querySelector('#f'), ft = document.querySelector('footer .fwrap');
            const fr = f.getBoundingClientRect(), tr = ft.getBoundingClientRect();
            if (getComputedStyle(document.querySelector('footer')).position !== 'fixed' && tr.top >= fr.bottom) return {gap: 999, onTop: true, flow: true};
            const pts = [[fr.left + 30, fr.top + 6], [fr.left + fr.width / 2, fr.top + fr.height / 2], [fr.right - 30, fr.top + 6]];
            return {gap: Math.round(fr.top - tr.bottom), onTop: pts.every(([x, y]) => { const e = document.elementFromPoint(x, y); return e && !!e.closest('#f'); })}; })()""")
        assert r["onTop"] and r["gap"] >= 8, r
    finally:
        ctx.close()


def test_page_reports_a_real_javascript_error(browser, site):
    ctx, page, errors, reports = open_page(browser, site, "/", 1280)
    try:
        page.evaluate("setTimeout(() => { null.value }, 0)")
        page.wait_for_timeout(600)
        sent = [r for r in reports if r]
        assert sent and "Cannot read properties of null" in sent[0] and '"kind":"frontend"' in sent[0].replace(" ", "")
    finally:
        ctx.close()


@pytest.mark.parametrize("path,width,height", [("/guides", 980, 2050), ("/about", 980, 2050), ("/", 960, 2600)])
def test_footer_bar_sits_at_the_bottom_of_tall_screens(browser, site, path, width, height):
    """A short page on a tall screen (phones in desktop mode, tablets) must not leave an empty band under the footer."""
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(site) else route.abort())
    try:
        page.goto(site + path, wait_until="load")
        page.wait_for_timeout(400)
        gap = page.evaluate("innerHeight - document.querySelector('footer').getBoundingClientRect().bottom")
        link_box = page.evaluate("(() => { const f = document.querySelector('#f'); return f && getComputedStyle(f).position === 'fixed' ? f.offsetHeight : 0 })()")
        assert gap <= link_box + 40, f"{gap}px empty under the footer on {path}"
        assert page.evaluate("document.documentElement.scrollHeight - innerHeight") <= 1, "a short page must not scroll"
    finally:
        ctx.close()
