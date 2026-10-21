# ReelKit (Vercel edition)

Everything deploys to Vercel: `public/` is the page, `api/index.py` is the backend (bundle is only a few MB).

## Environment variables (Vercel > Settings > Environment Variables)
- `GROQ_API_KEY`        required for Video-to-Script (free key at console.groq.com)
- `REPLICATE_API_TOKEN` required for Vocal Splitter (replicate.com, pay per run)
- `IG_COOKIES_TEXT`     optional: paste a Netscape cookies.txt from a logged-in browser. Instagram often blocks Vercel's IPs without it.

## Deploy
Push this folder to GitHub, import it in Vercel (Framework: Other, Root Directory: project root), add the variables, deploy.

## Run locally
pip install -r requirements.txt uvicorn
uvicorn api.index:app --port 8000   # open http://localhost:8000
