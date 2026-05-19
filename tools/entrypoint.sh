#!/bin/sh
# Additive seed: on every boot, copy any NEW seed files onto the persistent
# disk without overwriting existing files. User-uploaded data is always preserved.
# The bundle version-tag mechanism only logs progress — copies are idempotent.
set -e

SEED_DIR=/app/seed
UPLOAD_TARGET="${UPLOAD_DIR:-/data/uploads}"
DATA_TARGET="${DATA_DIR:-/data/store}"

mkdir -p "$UPLOAD_TARGET" "$DATA_TARGET"

echo "[seed] Additive merge from $SEED_DIR (preserves existing files)"

# ── Uploads: copy each seed file ONLY if missing on disk ─────────
copied=0
if [ -d "$SEED_DIR/uploads" ]; then
  for src in "$SEED_DIR/uploads"/*; do
    [ -e "$src" ] || continue
    name=$(basename "$src")
    if [ ! -e "$UPLOAD_TARGET/$name" ]; then
      cp -p "$src" "$UPLOAD_TARGET/$name"
      copied=$((copied + 1))
    fi
  done
  total=$(find "$UPLOAD_TARGET" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')
  echo "[seed] uploads: +$copied new (total on disk: $total)"
fi

# ── Data JSONs: merge keys for dict-shaped files, append for list-shaped ─
if [ -d "$SEED_DIR/data" ]; then
  for src in "$SEED_DIR/data"/*.json; do
    [ -e "$src" ] || continue
    name=$(basename "$src")
    dst="$DATA_TARGET/$name"
    if [ ! -e "$dst" ]; then
      cp "$src" "$dst"
      echo "[seed]   + $name (new file)"
      continue
    fi
    # Both exist → merge via Python (additive — existing keys win)
    python3 - "$src" "$dst" <<'PY'
import json, sys
src_p, dst_p = sys.argv[1], sys.argv[2]
try:
    with open(src_p) as f: src = json.load(f)
    with open(dst_p) as f: dst = json.load(f)
except Exception as e:
    print(f"[seed]   ! merge {dst_p}: {e}", file=sys.stderr)
    sys.exit(0)
import os
base = os.path.basename(dst_p)
if isinstance(src, dict) and isinstance(dst, dict):
    added = 0
    for k, v in src.items():
        if k not in dst:
            dst[k] = v
            added += 1
    with open(dst_p, "w") as f:
        json.dump(dst, f, ensure_ascii=False)
    print(f"[seed]   ~ {base}: +{added} new entries (kept {len(dst)-added} existing, total {len(dst)})")
elif isinstance(src, list) and isinstance(dst, list):
    # For append-only logs (usage_logs.json): dedupe via timestamp+detail signature
    sig = lambda e: (e.get("timestamp",""), e.get("api_type",""), e.get("detail",""))
    existing = {sig(e) for e in dst}
    new = [e for e in src if sig(e) not in existing]
    dst.extend(new)
    with open(dst_p, "w") as f:
        json.dump(dst, f, ensure_ascii=False)
    print(f"[seed]   ~ {base}: +{len(new)} appended (total {len(dst)})")
else:
    print(f"[seed]   ! {base}: shape mismatch — skip")
PY
  done
fi

# Render injects $PORT; expand it for uvicorn
if [ -n "$PORT" ] && [ "$1" = "uvicorn" ]; then
  exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
fi

exec "$@"
