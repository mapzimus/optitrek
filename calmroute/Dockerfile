# Calm Route API — Railway deploy image.
# OSRM is NOT in this image; it stays on BRONTOSAURUS behind a Cloudflare
# Tunnel and is reached via CALMROUTE_OSRM_URL.
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY static ./static

EXPOSE 8000
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
