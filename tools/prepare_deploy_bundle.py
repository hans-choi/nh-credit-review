"""Prepare a deploy-ready bundle of uploaded files + stripped data JSONs.

Strips huge base64 `page_images` blobs from documents.json (443MB → a few MB).
Those thumbnails regenerate on-demand via /api/documents/{id}/render-pages
(PyMuPDF + PIL), so no information is lost.

Output layout (into ./backend/seed/):
    seed/
      data/
        documents.json        # slim — no page_images
        extractions.json
        regulations.json
        review_cases.json
        usage_logs.json
      uploads/
        *.pdf *.jpg ...       # all 125 real user uploads

On first container boot the entrypoint copies seed/* → /data/* if empty.
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
DATA_SRC = BACKEND / "data"
UPLOADS_SRC = BACKEND / "uploads"
SEED_DIR = BACKEND / "seed"


def strip_page_images(src: Path, dst: Path) -> tuple[int, int, int]:
    """Strip large base64 blobs. Returns (doc_count, page_img_stripped, elem_b64_stripped)."""
    with src.open("r", encoding="utf-8") as f:
        documents = json.load(f)
    stripped_pages = 0
    stripped_elem_b64 = 0
    for doc_id, doc in documents.items():
        meta = doc.get("metadata") or {}
        page_images = meta.get("page_images") or {}
        if page_images:
            stripped_pages += len(page_images)
            meta["page_images"] = {}  # keep key so shape is stable
        # Strip element-level base64 (table/figure crops — not essential for demo)
        for el in (meta.get("elements") or []):
            if el.get("base64"):
                stripped_elem_b64 += 1
                el["base64"] = ""
        doc["metadata"] = meta
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8") as f:
        json.dump(documents, f, ensure_ascii=False)
    return len(documents), stripped_pages, stripped_elem_b64


def main():
    if SEED_DIR.exists():
        shutil.rmtree(SEED_DIR)
    seed_data = SEED_DIR / "data"
    seed_uploads = SEED_DIR / "uploads"
    seed_data.mkdir(parents=True)
    seed_uploads.mkdir(parents=True)

    # Strip documents.json
    docs_src = DATA_SRC / "documents.json"
    if docs_src.exists():
        n_docs, n_pages, n_elem = strip_page_images(docs_src, seed_data / "documents.json")
        slim_size = (seed_data / "documents.json").stat().st_size / (1024 * 1024)
        orig_size = docs_src.stat().st_size / (1024 * 1024)
        print(f"  documents.json: {orig_size:.1f}MB → {slim_size:.2f}MB "
              f"({n_docs} docs, stripped {n_pages} page thumbs + {n_elem} element crops)")

    # Copy the rest of data/ verbatim (small files)
    for name in ["extractions.json", "regulations.json",
                 "review_cases.json", "usage_logs.json"]:
        src = DATA_SRC / name
        if src.exists():
            shutil.copy2(src, seed_data / name)
            sz = src.stat().st_size / 1024
            print(f"  {name}: {sz:.1f} KB copied")

    # Copy uploads/ (skip _crops/ — regenerated on demand)
    copied = 0
    for f in UPLOADS_SRC.iterdir():
        if f.name.startswith("_") or f.name == ".DS_Store":
            continue
        if f.is_file():
            shutil.copy2(f, seed_uploads / f.name)
            copied += 1
    total_mb = sum(f.stat().st_size for f in seed_uploads.iterdir()) / (1024 * 1024)
    print(f"  uploads/: {copied} files, {total_mb:.1f} MB")

    # Final tally
    bundle_mb = sum(
        f.stat().st_size for f in SEED_DIR.rglob("*") if f.is_file()
    ) / (1024 * 1024)
    print(f"\nSeed bundle ready at {SEED_DIR.relative_to(ROOT)}/ → {bundle_mb:.1f} MB")
    if bundle_mb > 100:
        print("⚠  WARNING: bundle >100MB — GitHub file limit may apply per-file")


if __name__ == "__main__":
    sys.exit(main() or 0)
