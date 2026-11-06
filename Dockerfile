# WaveRise on Google Cloud Run: the FastAPI app plus the public/ folder, with ffmpeg for video tools.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt "uvicorn[standard]==0.32.1"
COPY . .

# Cloud Run sends the visitor's address in X-Forwarded-For; trust it so rate limits and https checks work.
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*" --timeout-keep-alive 75
