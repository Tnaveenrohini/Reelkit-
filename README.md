# WaveRise (Vercel edition)

Instagram downloader + caption/hashtag copier.
Repository root: app.py, requirements.txt, public/. No environment variables are required.
Check the backend at https://YOUR-SITE/api/health
Local: pip install -r requirements.txt uvicorn && uvicorn app:app --port 8000


## AI Bug Fixer

The site records real errors and a separate always-on worker fixes them with Claude: analysis, a patch on an `autofix/` branch, CI tests, a staging check on the Vercel preview, a production deploy (owner approval by default), live verification and automatic rollback. Setup and operation: [docs/BUGFIXER.md](docs/BUGFIXER.md). Without `DATABASE_URL` the site works exactly as before.
