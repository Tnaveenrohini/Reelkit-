# WaveRise (Vercel edition)

Public-post downloads and photo tools, built with FastAPI and plain JavaScript.

Run locally:

```sh
pip install -r requirements.txt uvicorn
uvicorn app:app --port 8000
```

Open `http://localhost:8000`. Health: `/api/health`.

Batch downloads are available at `/batch-downloads`, from the home page, Tools menu and mobile tools sheet. Paste up to 10 unique public Instagram, Facebook or X post links, one per line. Repeated links are removed. Downloads run sequentially with progress and stop controls. Retry failed or stopped posts without downloading completed posts or carousel files again.

Keep the batch page open and allow multiple downloads when your browser asks. Manual save links remain available if automatic saving is blocked. Downloads depend on the source platform allowing the existing extractor to read public posts. Batch downloads use the existing `/api/info` and `/api/download` endpoints and need no extra API key.

Upstream HTML/JSON error pages are rejected instead of being saved as video files. Failed media requests clear cached metadata so a retry can fetch a fresh media link.

Verification:

```sh
pip install pytest
python -m pytest -q tests/test_creator.py
npm --prefix tests install
npm --prefix tests test
```

The JavaScript suite uses a simulated DOM to check navigation, link validation and batch retries. Playback and mobile file-saving behavior still need testing in a real browser.
