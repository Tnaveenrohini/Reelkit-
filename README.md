# ReelKit (Vercel edition)

Vercel's zero-config FastAPI support: `app.py` (backend) + `public/` (page, served by the CDN). No vercel.json needed.

## Environment variables (Vercel > Settings > Environment Variables)
- GROQ_API_KEY        Video-to-Script (free key at console.groq.com)
- REPLICATE_API_TOKEN Vocal Splitter (replicate.com, pay per run)
- IG_COOKIES_TEXT     optional cookies.txt text; Instagram often blocks Vercel IPs without it

## Deploy
Repository root must contain ONLY: app.py, requirements.txt, public/, README.md.
Delete any old static/, server.py, api/, vercel.json, .vercelignore. Vercel > Settings > General > Root Directory: empty.
Open https://YOUR-SITE/api/health to confirm the backend is running.

## Run locally
pip install -r requirements.txt uvicorn
uvicorn app:app --port 8000
