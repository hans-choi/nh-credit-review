FROM python:3.11-slim

WORKDIR /app

# System deps: PyMuPDF needs build-essential; Pillow ships wheels. Fonts help PDF render.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY tools/ ./tools/

# Seeded baseline data (stripped — page thumbnails regen on-demand)
COPY backend/seed/ /app/seed/

# Default filesystem layout (Render disk mounts at /data and overrides)
RUN mkdir -p /app/uploads /app/data /data/uploads /data/store

ENV UPLOAD_DIR=/data/uploads \
    DATA_DIR=/data/store \
    PYTHONUNBUFFERED=1 \
    MALLOC_ARENA_MAX=2 \
    PORT=8000

# Entrypoint seeds the persistent disk on first boot (no-op if already populated)
COPY tools/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
WORKDIR /app/backend

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
