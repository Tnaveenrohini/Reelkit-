# WaveRise (Vercel edition)

Public-post downloads, photo tools and creator tools, built with FastAPI and plain JavaScript.

Run locally:

```sh
pip install -r requirements.txt uvicorn
uvicorn app:app --port 8000
```

Open `http://localhost:8000`. Health: `/api/health`.

The new tools appear in the home page, Tools menu and mobile tools sheet:

| Tool | Address | Behavior |
| --- | --- | --- |
| Batch downloads | `/batch-downloads` | Up to 10 unique public Instagram, Facebook or X links. Sequential downloads, per-post progress, stop, and retry failed/stopped posts. Completed carousel files survive retries. Manual save links remain available if the browser blocks automatic saving. |
| Auto subtitles | `/auto-subtitles` | Upload a saved video; transcribe English, Telugu, Hindi or Tamil (or detect the spoken language). Edit text and non-overlapping timing, preview subtitles, export SRT/VTT, or render a captioned MP4. This transcribes speech in its original language; it does not translate it. |
| Reel size converter | `/reel-size-converter` | Upload a video; choose 9:16, 1:1, 4:5 or 16:9. Preview crop positioning, black bars or blurred background. Export H.264/AAC MP4 at 720p. |
| Brand kit | `/brand-kit` | Save a name, PNG/JPG/WebP logo, two colors, font and position in this browser. Enable branding for Festival Frames and Daily Status previews and image exports. The kit is not synced between devices. |
| Download diagnostics | `/download-diagnostics` | Check a link, classify common failures and show useful next steps. Existing download errors also offer diagnostics and retry controls. |

Batch downloads depend on the source platform allowing the existing extractor to read public posts. Keep the page open and allow multiple downloads when prompted. Resizing and subtitles use uploaded files, so first save a Reel if starting from a post link.

Automatic transcription requires `OPENAI_API_KEY` **on the server**. Add it in your host's environment settings and restart/redeploy. WaveRise sends the extracted audio to OpenAI's `whisper-1` transcription API when the user clicks Generate; usage is billed to that API account. Manual captions, SRT/VTT exports, batch downloads, resizing and the brand kit work without this key. `/api/creator/capabilities` reports availability without returning credentials.

MP4 subtitle rendering requires FFmpeg built with the `subtitles` filter (libass). The bundled FFmpeg may not include it; the UI disables this export when unavailable. Install system FFmpeg with libass and Noto fonts for Telugu, Tamil and Devanagari text on a full server. SRT/VTT exports retain Unicode regardless of server fonts.

Video processing accepts clips up to 3 minutes and 4096 × 2160 pixels. Uploads are limited to **4 MB on Vercel**, allowing room for multipart overhead under its request limit, and **50 MB locally**. Your hosting plan's response size, CPU and execution-time limits still apply. A full Python server is recommended for larger or longer processing jobs. Inputs and outputs are temporary and removed after processing/response delivery. Edited captions are held in page memory; leaving or reloading clears them.

Creator processing is limited to 6 requests per IP per 10 minutes. Speech generation also has a 3-per-IP limit and a default daily counter of 100 requests (`SUBTITLE_DAILY_LIMIT`). The existing Upstash Redis configuration shares these limits between instances; without it, limits fall back to process memory and reset with that process. Use persistent Redis when relying on a deployment-wide speech budget.

Verification:

```sh
pip install pytest
python -m pytest -q tests/test_creator.py
# Real media tests require ffmpeg and ffprobe.
npm --prefix tests install
npm --prefix tests test
```

The JavaScript suite uses a simulated DOM to check navigation, batch retry behavior, manual subtitle export and brand rendering. It does not replace testing mobile file downloads or playback in a real browser. Speech API tests use a stub provider and do not spend API credits.

