#!/bin/sh
# First-boot seed: populate Render persistent disk from the baked-in /app/seed/
# Subsequent boots are no-ops (stamp file guards against re-seeding).
set -e

SEED_DIR=/app/seed
UPLOAD_TARGET="${UPLOAD_DIR:-/data/uploads}"
DATA_TARGET="${DATA_DIR:-/data/store}"
STAMP="${DATA_TARGET}/.seeded"

mkdir -p "$UPLOAD_TARGET" "$DATA_TARGET"

if [ ! -f "$STAMP" ]; then
  echo "[seed] First boot — populating persistent disk from $SEED_DIR"

  # Uploads: copy if target is empty (user-uploaded files)
  if [ -d "$SEED_DIR/uploads" ] && [ -z "$(ls -A "$UPLOAD_TARGET" 2>/dev/null)" ]; then
    cp -a "$SEED_DIR/uploads/." "$UPLOAD_TARGET/"
    echo "[seed] Copied uploads/ ($(ls "$UPLOAD_TARGET" | wc -l) files)"
  else
    echo "[seed] Uploads already present or seed dir missing — skip"
  fi

  # Data JSONs: copy any that don't yet exist (regulations, documents, etc.)
  if [ -d "$SEED_DIR/data" ]; then
    for f in "$SEED_DIR/data"/*.json; do
      [ -e "$f" ] || continue
      name=$(basename "$f")
      if [ ! -e "$DATA_TARGET/$name" ]; then
        cp "$f" "$DATA_TARGET/$name"
        echo "[seed]   + $name"
      fi
    done
  fi

  # Stamp so we never re-seed over existing writes
  date -u +%FT%TZ > "$STAMP"
  echo "[seed] Done."
else
  echo "[seed] Already seeded at $(cat "$STAMP") — skip."
fi

# Render injects $PORT; rewrite CMD args if needed
if [ -n "$PORT" ] && [ "$1" = "uvicorn" ]; then
  # Replace --port value in args
  set -- "$@"
  exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
fi

exec "$@"
