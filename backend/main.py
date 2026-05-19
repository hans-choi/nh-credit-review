"""여신심사 AI 판독 POC — FastAPI backend.

Document Parse (Enhanced/ocr=force) + Universal Information Extraction
for handwritten credit-review forms used by banks and financial institutions.
"""

import json
import os
import traceback
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from config import UPLOAD_DIR
from store import store
from upstage_client import upstage
from schemas import DOC_TYPES, get_schema, list_doc_types
from approval_crop import (
    find_approval_table_bbox,
    compute_cell_bboxes,
    crop_cells,
    crop_approval_strip,
    cleanup_crops,
    detect_ink_in_cells,
    detect_ink_in_regions,
    find_director_signature_regions,
    find_party_signature_regions,
    find_borrower_signature_region,
    find_real_estate_party_regions,
    STAMP_CHECK_SCHEMA,
    STAMP_STRIP_SCHEMA,
)


def _resolve_doc_file(doc: dict) -> str | None:
    """Resolve a document's on-disk path across all the places it might live.

    Tries in order:
      1. The stored file_path as-is (handles absolute paths from new uploads
         where UPLOAD_DIR is /data/uploads on Render).
      2. file_path joined with backend/ dir (legacy relative './uploads/...').
      3. UPLOAD_DIR + same basename (handles seeded data where file_path
         was './uploads/...' locally but actual files now live at /data/uploads/).
      4. UPLOAD_DIR + '{doc_type}__{filename}' (the canonical safe_name pattern).
    Returns the first path that exists, or None.
    """
    fp = doc.get("file_path") or ""
    candidates = []
    if fp:
        candidates.append(fp)
        if not os.path.isabs(fp):
            candidates.append(os.path.join(os.path.dirname(__file__), fp))
        candidates.append(os.path.join(UPLOAD_DIR, os.path.basename(fp)))
    fname = doc.get("filename", "")
    dt = doc.get("doc_type", "")
    if fname and dt:
        candidates.append(os.path.join(UPLOAD_DIR, f"{dt}__{fname}"))
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def _render_pdf_pages_to_base64(file_path: str, zoom: float = 2.0) -> dict[str, str]:
    """Render pages of a PDF or multi-page TIF to PNG base64 so the browser
    (which cannot display PDF/TIF in <img>) has something to show.
    Returns { "1": "...base64...", "2": "..." }. Unsupported input returns {}."""
    import base64
    import io
    ext = file_path.lower().rsplit(".", 1)[-1]
    out: dict[str, str] = {}

    if ext == "pdf":
        try:
            import fitz  # PyMuPDF
            with fitz.open(file_path) as pdf:
                mat = fitz.Matrix(zoom, zoom)
                for i, page in enumerate(pdf, start=1):
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    out[str(i)] = base64.b64encode(pix.tobytes("png")).decode("ascii")
        except Exception as e:
            print(f"[PDF render] failed for {file_path}: {e}")
        return out

    if ext in ("tif", "tiff"):
        try:
            from PIL import Image
            im = Image.open(file_path)
            # Multi-page TIF: iterate frames via seek
            for i in range(getattr(im, "n_frames", 1)):
                im.seek(i)
                frame = im.convert("RGB")
                # Upscale small scans for readability (don't upscale already-large images)
                w, h = frame.size
                if max(w, h) < 1600:
                    scale = 1600 / max(w, h)
                    frame = frame.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                buf = io.BytesIO()
                frame.save(buf, format="PNG")
                out[str(i + 1)] = base64.b64encode(buf.getvalue()).decode("ascii")
            im.close()
        except Exception as e:
            print(f"[TIF render] failed for {file_path}: {e}")
        return out

    return out

app = FastAPI(
    title="여신심사 AI 판독 POC",
    description="여신 심사 서류 자동 판독 (Document Parse Enhanced + Information Extract)",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

upstage.set_usage_callback(lambda entry: store.add_usage_log(entry))

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    idx = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(idx):
        with open(idx, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>여신심사 AI 판독 POC</h1>"


@app.get("/api/health")
async def health():
    """Liveness + minimal readiness probe for Render/K8s."""
    return {
        "status": "ok",
        "upload_dir": UPLOAD_DIR,
        "has_upstage_key": bool(os.getenv("UPSTAGE_API_KEY")),
    }


# ──────────────────────────────────────────────
# Doc type config
# ──────────────────────────────────────────────
@app.get("/api/doc-types")
async def get_doc_types():
    return {"doc_types": list_doc_types()}


# ──────────────────────────────────────────────
# Upload + parse (Document Parse Enhanced)
# ──────────────────────────────────────────────
@app.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    doc_type: str = Form(...),
):
    if doc_type not in DOC_TYPES:
        raise HTTPException(400, f"Unknown doc_type: {doc_type}")
    if not file.filename:
        raise HTTPException(400, "No filename")
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"):
        raise HTTPException(400, f"Unsupported file type: {ext}")

    # Save file (prefix with doc_type to keep uploads organized)
    safe_name = f"{doc_type}__{file.filename}"
    save_path = os.path.join(UPLOAD_DIR, safe_name)
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    try:
        result = await upstage.parse_document(save_path)

        parsed_text = ""
        parsed_html = ""
        elements = []
        page_images: dict[str, str] = {}

        content_data = result.get("content", {})
        if isinstance(content_data, dict):
            parsed_text = content_data.get("text", "") or ""
            parsed_html = content_data.get("html", content_data.get("markdown", parsed_text)) or ""
        elif isinstance(content_data, str):
            parsed_text = content_data
            parsed_html = content_data

        for pg in result.get("pages", []) or []:
            if not isinstance(pg, dict):
                continue
            pn = pg.get("page", pg.get("page_number", pg.get("id", 1)))
            img = pg.get("base64", pg.get("image", ""))
            if img:
                page_images[str(pn)] = img

        # Fallback: render PDF pages via PyMuPDF when DP didn't return page images.
        if not page_images:
            rendered = _render_pdf_pages_to_base64(save_path)
            if rendered:
                page_images.update(rendered)

        for el in result.get("elements", []) or []:
            t = el.get("text", el.get("content", ""))
            if isinstance(t, dict):
                t = t.get("text", t.get("content", json.dumps(t, ensure_ascii=False)))
            if not isinstance(t, str):
                t = str(t)
            elements.append({
                "id": el.get("id", len(elements)),
                "text": t,
                "category": el.get("category", el.get("type", "paragraph")),
                "page": el.get("page", el.get("page_number", 1)),
                "coordinates": el.get("coordinates", el.get("bounding_box", None)),
                "html": el.get("html", ""),
                "confidence": el.get("confidence", el.get("score", None)),
                "base64": el.get("base64_encoding", el.get("base64", "")),
            })

        doc_id = store.add_document(
            filename=file.filename,
            file_path=save_path,
            doc_type=doc_type,
            parsed_text=parsed_text,
            parsed_html=parsed_html,
            metadata={
                "pages": result.get("num_pages", len(page_images) or 1),
                "elements": elements,
                "page_images": page_images,
            },
        )

        return {
            "doc_id": doc_id,
            "filename": file.filename,
            "doc_type": doc_type,
            "pages": result.get("num_pages", len(page_images) or 1),
            "elements_count": len(elements),
            "text_length": len(parsed_text),
        }
    except Exception as e:
        raise HTTPException(500, f"Parse failed: {e}\n{traceback.format_exc()}")


# ──────────────────────────────────────────────
# Information Extract (schema-driven, handles handwriting)
# ──────────────────────────────────────────────
@app.post("/api/documents/{doc_id}/extract")
async def extract_document(doc_id: str):
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    schema = get_schema(doc["doc_type"])
    if not schema:
        raise HTTPException(400, f"No schema for doc_type: {doc['doc_type']}")

    file_path = _resolve_doc_file(doc)
    if not file_path:
        raise HTTPException(404, "Source file not found on disk")

    try:
        result = await upstage.extract_information(
            file_path, schema,
            doc_id=doc_id,
            detail=f"extract {doc['doc_type']}",
        )
        extracted = result["extracted"] or {}

        # ─── Mortgage: 결재란 통합 boolean + 3 당사자 날인 (픽셀 휴리스틱) ───
        if doc["doc_type"] == "mortgage":
            try:
                # 결재란: 5셀 스캔 후 any() → 단일 boolean
                approval_arr = await _detect_approval_cells_per_crop(doc_id, file_path, doc)
                if approval_arr is not None:
                    extracted["결재_날인유무"] = any(bool(x) for x in approval_arr)
            except Exception as e:
                print(f"[Mortgage 결재] skipped: {e}")
            try:
                # 3 당사자 날인유무
                party_results = _detect_party_signatures(doc, file_path)
                if party_results:
                    key_map = {
                        "채권자": "채권자_날인유무",
                        "채무자": "채무자_날인유무",
                        "근저당권설정자": "근저당권설정자_날인유무",
                    }
                    for k in key_map.values():
                        extracted[k] = False
                    for p in party_results:
                        k = key_map.get(p["role_label"])
                        if k:
                            extracted[k] = bool(p["has_ink"])
            except Exception as e:
                print(f"[Mortgage 당사자] skipped: {e}")

        # ─── Credit agreement: 결재란 통합 (픽셀 휴리스틱) ───
        if doc["doc_type"] == "credit_agreement":
            try:
                approval_arr = await _detect_approval_cells_per_crop(doc_id, file_path, doc)
                if approval_arr is not None:
                    extracted["결재_날인유무"] = any(bool(x) for x in approval_arr)
            except Exception as e:
                print(f"[Credit 결재] skipped: {e}")

        # ─── Loan agreement: 결재란 통합 + 본인 서명 (픽셀 휴리스틱) ───
        if doc["doc_type"] == "loan_agreement":
            try:
                approval_arr = await _detect_approval_cells_per_crop(doc_id, file_path, doc)
                if approval_arr is not None:
                    extracted["결재_날인유무"] = any(bool(x) for x in approval_arr)
            except Exception as e:
                print(f"[Loan 결재] skipped: {e}")
            try:
                borrower_sig = _detect_borrower_signature(doc, file_path)
                if borrower_sig is not None:
                    extracted["본인_서명_날인_유무"] = bool(borrower_sig["has_ink"])
            except Exception as e:
                print(f"[Loan 본인서명] skipped: {e}")

        # ─── Real estate contract: 3 당사자 날인 (픽셀 휴리스틱) ───
        if doc["doc_type"] == "real_estate_contract":
            try:
                party_results = _detect_real_estate_party_signatures(doc, file_path)
                if party_results:
                    key_map = {
                        "매도인_또는_임대인": "매도인_또는_임대인_날인유무",
                        "매수인_또는_임차인": "매수인_또는_임차인_날인유무",
                        "개업공인중개사": "개업공인중개사_날인유무",
                    }
                    for k in key_map.values():
                        extracted[k] = False
                    for p in party_results:
                        k = key_map.get(p["role_label"])
                        if k:
                            extracted[k] = bool(p["has_ink"])
            except Exception as e:
                print(f"[Real estate 당사자] skipped: {e}")

        # ─── Director signature detection (board_resolution only, pixel heuristic) ───
        if doc["doc_type"] == "board_resolution":
            try:
                signatures = _detect_board_signatures(doc, file_path)
                if signatures:
                    # Overwrite 날인유무 booleans in extracted with pixel-based results.
                    # Fields: 대표이사_날인유무, 사내이사1_날인유무, 사내이사2_날인유무, 사내이사3_날인유무
                    key_map = {
                        "대표이사": "대표이사_날인유무",
                        "사내이사1": "사내이사1_날인유무",
                        "사내이사2": "사내이사2_날인유무",
                        "사내이사3": "사내이사3_날인유무",
                    }
                    # Reset all to False; set True only where detected
                    for k in key_map.values():
                        extracted[k] = False
                    for sig in signatures:
                        k = key_map.get(sig["role_label"])
                        if k:
                            extracted[k] = bool(sig["has_ink"])
            except Exception as e:
                print(f"[Board signatures] skipped: {e}")

        store.save_extraction(doc_id, {
            "doc_type": doc["doc_type"],
            "extracted": extracted,
        })
        return {
            "doc_id": doc_id,
            "doc_type": doc["doc_type"],
            "extracted": extracted,
        }
    except Exception as e:
        raise HTTPException(500, f"Extract failed: {e}\n{traceback.format_exc()}")


def _detect_borrower_signature(doc: dict, file_path: str) -> dict | None:
    """Pixel-heuristic signature detection for 본인 (borrower) row in 대출거래약정서."""
    from PIL import Image

    elements = doc.get("metadata", {}).get("elements", []) or []
    if not elements:
        return None
    with Image.open(file_path) as im:
        img_size = im.size

    region = find_borrower_signature_region(elements, img_size)
    if not region:
        print("[Borrower sig] 본인 table not found in DP elements")
        return None
    ink_results = detect_ink_in_regions(file_path, [region], threshold_ratio=0.04)
    has, ratio = ink_results[0]
    print(f"[Borrower sig] ratio={round(ratio, 3)}  has_ink={has}")
    return {"has_ink": has, "ratio": round(ratio, 3)}


def _detect_real_estate_party_signatures(doc: dict, file_path: str) -> list[dict] | None:
    """Pixel-heuristic signature/stamp detection for 3 parties in 부동산 매매·임대차 계약서.

    Parties: 매도인/임대인, 매수인/임차인, 개업공인중개사.
    Photo-captured documents are common — detection tolerates mild tilt and
    compression noise. Threshold tuned looser than 근저당계약서 since real-estate
    seals are often lighter/smaller (rubber-stamp, personal signature).
    """
    from PIL import Image

    elements = doc.get("metadata", {}).get("elements", []) or []
    if not elements:
        return None
    with Image.open(file_path) as im:
        img_size = im.size

    regions = find_real_estate_party_regions(elements, img_size)
    if not regions:
        print("[RE party sig] no party tables found in DP elements")
        return None

    # Slightly more sensitive threshold for photo-captured seals (often partial/light)
    ink_results = detect_ink_in_regions(file_path, regions, threshold_ratio=0.035)
    ratios = [round(r, 3) for _, r in ink_results]
    print(f"[RE party sig] regions: {[r['role_label'] for r in regions]} ratios: {ratios}")
    return [
        {"role_label": reg["role_label"], "has_ink": has, "ratio": round(ratio, 3)}
        for reg, (has, ratio) in zip(regions, ink_results)
    ]


def _detect_party_signatures(doc: dict, file_path: str) -> list[dict] | None:
    """Pixel-heuristic signature detection for 3 parties in 근저당권설정계약서."""
    from PIL import Image

    elements = doc.get("metadata", {}).get("elements", []) or []
    if not elements:
        return None
    with Image.open(file_path) as im:
        img_size = im.size

    regions = find_party_signature_regions(elements, img_size)
    if not regions:
        print("[Party sig] party table not found in DP elements")
        return None

    ink_results = detect_ink_in_regions(file_path, regions, threshold_ratio=0.05)
    ratios = [round(r, 3) for _, r in ink_results]
    print(f"[Party sig] regions: {[r['role_label'] for r in regions]} ratios: {ratios}")
    return [
        {"role_label": reg["role_label"], "has_ink": has, "ratio": round(ratio, 3)}
        for reg, (has, ratio) in zip(regions, ink_results)
    ]


def _detect_board_signatures(doc: dict, file_path: str) -> list[dict] | None:
    """Detect presence of stamps/signatures next to each director name in 이사회결의서.
    Returns list of {role_label, has_ink, ratio, text} or None."""
    from PIL import Image

    elements = doc.get("metadata", {}).get("elements", []) or []
    if not elements:
        return None
    with Image.open(file_path) as im:
        img_size = im.size

    regions = find_director_signature_regions(elements, img_size)
    if not regions:
        print("[Board signatures] no director regions found in DP elements")
        return None

    ink_results = detect_ink_in_regions(file_path, regions)
    ratios = [round(r, 3) for _, r in ink_results]
    print(f"[Board signatures] regions: {[r['role_label'] for r in regions]} ratios: {ratios}")

    return [
        {
            "role_label": reg["role_label"],
            "has_ink": has,
            "ratio": round(ratio, 3),
            "text": reg.get("text", ""),
        }
        for reg, (has, ratio) in zip(regions, ink_results)
    ]


async def _detect_approval_cells_per_crop(doc_id: str, file_path: str, doc: dict) -> list[bool] | None:
    """For mortgage docs: detect stamp/signature in each approval cell.

    Strategy (POC high-quality path):
      1. Find approval table bbox from DP output.
      2. Compute 5 cell bboxes.
      3. Run deterministic pixel-based ink detection (fast, accurate for stamps).
      4. Fallback to VLM IE per-cell if heuristic is inconclusive.
    """
    from PIL import Image

    elements = doc.get("metadata", {}).get("elements", []) or []
    table_bbox = find_approval_table_bbox(elements)
    if not table_bbox:
        print("[Approval] approval table bbox not found in DP elements")
        return None

    with Image.open(file_path) as im:
        img_size = im.size

    cells = compute_cell_bboxes(table_bbox, img_size)

    # ── Strategy 1: pixel-based ink detection (primary) ──
    # IE/VLM failed to visually interpret stamps/signatures in isolated crops.
    # Pixel density is a more reliable signal for this binary classification.
    try:
        ink_results = detect_ink_in_cells(file_path, cells)
        print(f"[Approval] ink ratios: {[round(r, 3) for _, r in ink_results]}")
        return [has for has, _ in ink_results]
    except Exception as e:
        print(f"[Approval] pixel heuristic failed: {e}")

    # ── Strategy 2: fallback to VLM strip call ──
    crop_dir = os.path.join(UPLOAD_DIR, "_crops")
    strip_path = crop_approval_strip(file_path, cells, crop_dir, doc_id)
    try:
        r = await upstage.extract_information(
            strip_path, STAMP_STRIP_SCHEMA,
            doc_id=doc_id, detail="approval-strip",
        )
        arr = (r.get("extracted") or {}).get("has_stamp_per_cell")
        if isinstance(arr, list) and len(arr) == 5:
            return [bool(x) for x in arr]
    except Exception as e:
        print(f"[Approval strip] fallback failed: {e}")
    finally:
        cleanup_crops([strip_path])

    return None


# ──────────────────────────────────────────────
# Document list / detail / delete
# ──────────────────────────────────────────────
@app.get("/api/documents")
async def list_documents(doc_type: Optional[str] = None):
    return {"documents": store.list_documents(doc_type)}


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: str):
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    extraction = store.get_extraction(doc_id)
    return {
        "id": doc["id"],
        "filename": doc["filename"],
        "doc_type": doc["doc_type"],
        "parsed_text": doc["parsed_text"],
        "parsed_html": doc["parsed_html"],
        "metadata": doc["metadata"],
        "uploaded_at": doc["uploaded_at"],
        "extraction": extraction,
    }


@app.post("/api/documents/{doc_id}/render-pages")
async def render_pdf_pages(doc_id: str):
    """Lightweight: render PDF/TIF pages as PNG base64 (no DP recall)."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    fp = _resolve_doc_file(doc)
    if not fp:
        raise HTTPException(404, "Source file not found")
    ext = fp.lower().rsplit(".", 1)[-1]
    if ext not in ("pdf", "tif", "tiff"):
        return {"doc_id": doc_id, "skipped": True, "reason": f"ext={ext} not PDF/TIF"}
    rendered = _render_pdf_pages_to_base64(fp)
    if not rendered:
        return {"doc_id": doc_id, "rendered_pages": 0}
    meta = doc.get("metadata", {}) or {}
    meta["page_images"] = rendered
    meta["pages"] = len(rendered)
    doc["metadata"] = meta
    store._save("documents")
    return {"doc_id": doc_id, "rendered_pages": len(rendered)}


@app.post("/api/documents/{doc_id}/reparse")
async def reparse_document(doc_id: str):
    """Re-parse an existing document (useful after DP parameter changes)."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    file_path = _resolve_doc_file(doc)
    if not file_path:
        raise HTTPException(404, "Source file not found")
    try:
        result = await upstage.parse_document(file_path, doc_id=doc_id)
        parsed_text = ""
        parsed_html = ""
        elements = []
        page_images: dict[str, str] = {}
        cd = result.get("content", {})
        if isinstance(cd, dict):
            parsed_text = cd.get("text", "") or ""
            parsed_html = cd.get("html", cd.get("markdown", parsed_text)) or ""
        elif isinstance(cd, str):
            parsed_text = cd
            parsed_html = cd
        for pg in result.get("pages", []) or []:
            if not isinstance(pg, dict):
                continue
            pn = pg.get("page", pg.get("page_number", pg.get("id", 1)))
            img = pg.get("base64", pg.get("image", ""))
            if img:
                page_images[str(pn)] = img
        if not page_images:
            rendered = _render_pdf_pages_to_base64(file_path)
            if rendered:
                page_images.update(rendered)
        for el in result.get("elements", []) or []:
            t = el.get("text", el.get("content", ""))
            if isinstance(t, dict):
                t = t.get("text", t.get("content", json.dumps(t, ensure_ascii=False)))
            if not isinstance(t, str):
                t = str(t)
            elements.append({
                "id": el.get("id", len(elements)),
                "text": t,
                "category": el.get("category", el.get("type", "paragraph")),
                "page": el.get("page", el.get("page_number", 1)),
                "coordinates": el.get("coordinates", el.get("bounding_box", None)),
                "html": el.get("html", ""),
                "confidence": el.get("confidence", el.get("score", None)),
                "base64": el.get("base64_encoding", el.get("base64", "")),
            })
        doc["parsed_text"] = parsed_text or doc["parsed_text"]
        doc["parsed_html"] = parsed_html or doc["parsed_html"]
        doc["metadata"] = {
            "pages": result.get("num_pages", len(page_images) or 1),
            "elements": elements,
            "page_images": page_images,
        }
        store._save("documents")
        return {
            "doc_id": doc_id,
            "pages": doc["metadata"]["pages"],
            "elements_count": len(elements),
            "pages_with_images": list(page_images.keys()),
        }
    except Exception as e:
        raise HTTPException(500, f"Reparse failed: {e}\n{traceback.format_exc()}")


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    ok = store.delete_document(doc_id)
    if not ok:
        raise HTTPException(404, "Document not found")
    return {"deleted": doc_id}


@app.get("/api/documents/{doc_id}/file")
async def get_document_file(doc_id: str):
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    fp = _resolve_doc_file(doc)
    if not fp:
        raise HTTPException(404, "File missing")
    return FileResponse(fp, filename=doc["filename"])


# ──────────────────────────────────────────────
# Usage stats
# ──────────────────────────────────────────────
@app.get("/api/usage")
async def usage_stats():
    return {
        "stats": store.get_usage_stats(),
        "logs": store.get_usage_logs()[-50:],
    }


# ══════════════════════════════════════════════════════════════════
# RAG: 규정 관리 (Regulation corpus)
# ══════════════════════════════════════════════════════════════════
from rag_store import store_rag
from workflow import WorkflowOrchestrator


@app.get("/api/rag/regulations")
async def list_regulations():
    return {"regulations": store_rag.list_regulations()}


@app.post("/api/rag/regulations/upload")
async def upload_regulation(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "No filename")
    ext = os.path.splitext(file.filename)[1].lower()
    content = await file.read()

    # Extract text
    full_text: str
    if ext in (".txt", ".md"):
        try:
            full_text = content.decode("utf-8")
        except UnicodeDecodeError:
            full_text = content.decode("cp949", errors="replace")
    else:
        # PDF/image: use Document Parse to get plain text
        tmp_path = os.path.join(UPLOAD_DIR, f"_reg__{file.filename}")
        with open(tmp_path, "wb") as f:
            f.write(content)
        try:
            result = await upstage.parse_document(tmp_path, doc_id=f"reg-upload")
            cd = result.get("content", {})
            if isinstance(cd, dict):
                full_text = cd.get("text", "") or cd.get("markdown", "") or ""
            elif isinstance(cd, str):
                full_text = cd
            else:
                full_text = ""
        finally:
            try: os.remove(tmp_path)
            except Exception: pass
        if not full_text:
            raise HTTPException(500, "Document Parse returned empty text")

    info = await store_rag.add_regulation(file.filename, full_text, upstage)
    return info


@app.delete("/api/rag/regulations/{reg_id}")
async def delete_regulation(reg_id: str):
    ok = store_rag.delete_regulation(reg_id)
    if not ok:
        raise HTTPException(404, "Regulation not found")
    return {"deleted": reg_id}


@app.post("/api/rag/search")
async def rag_search(payload: dict):
    q = (payload or {}).get("query", "").strip()
    k = int((payload or {}).get("top_k", 5))
    if not q:
        raise HTTPException(400, "Missing 'query'")
    hits = await store_rag.search(q, top_k=k, upstage_client=upstage)
    return {"query": q, "hits": hits}


# ══════════════════════════════════════════════════════════════════
# Workflow: 7-step 여신 심사 파이프라인
# ══════════════════════════════════════════════════════════════════

@app.get("/api/workflow/cases")
async def list_cases():
    items = []
    for c in store.review_cases.values():
        items.append({
            "id": c["id"],
            "title": c.get("title", ""),
            "status": c.get("status", "draft"),
            "doc_ids": c.get("doc_ids", []),
            "created_at": c.get("created_at", ""),
            "finished_at": c.get("finished_at", ""),
            "decision": (c.get("steps", [])[-2]["output"].get("decision")
                         if c.get("steps") and len(c["steps"]) >= 6 and c["steps"][5].get("output") else None),
        })
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"cases": items}


@app.get("/api/workflow/cases/{case_id}")
async def get_case(case_id: str):
    c = store.review_cases.get(case_id)
    if not c:
        raise HTTPException(404, "Case not found")
    return c


@app.delete("/api/workflow/cases/{case_id}")
async def delete_case(case_id: str):
    if case_id not in store.review_cases:
        raise HTTPException(404, "Case not found")
    del store.review_cases[case_id]
    store._save("review_cases")
    return {"deleted": case_id}


@app.post("/api/workflow/cases")
async def create_case(payload: dict):
    doc_ids = (payload or {}).get("doc_ids", [])
    title = (payload or {}).get("title", "").strip()
    loan_type = (payload or {}).get("loan_type") or None
    if not doc_ids or not isinstance(doc_ids, list):
        raise HTTPException(400, "Missing 'doc_ids'")
    missing = [d for d in doc_ids if not store.get_document(d)]
    if missing:
        raise HTTPException(404, f"Documents not found: {missing}")

    import uuid as _uuid
    case_id = "case-" + _uuid.uuid4().hex[:10]
    # Derive default title if empty
    if not title:
        first_doc = store.get_document(doc_ids[0])
        title = f"심사 건 · {first_doc['filename'][:30]}…"
    case = {
        "id": case_id,
        "title": title,
        "doc_ids": doc_ids,
        "loan_type_hint": loan_type,
        "status": "draft",
        "created_at": datetime.now().isoformat(),
        "steps": [],
        "evidence": [],
    }
    store.review_cases[case_id] = case
    store._save("review_cases")
    return case


@app.post("/api/workflow/cases/{case_id}/run")
async def run_case(case_id: str):
    c = store.review_cases.get(case_id)
    if not c:
        raise HTTPException(404, "Case not found")
    orch = WorkflowOrchestrator(store, store_rag, upstage)
    result = await orch.run(c, loan_type=c.get("loan_type_hint"))
    store.review_cases[case_id] = result
    store._save("review_cases")
    return result


@app.get("/api/workflow/cases/{case_id}/run-stream")
async def run_case_stream(case_id: str):
    """SSE endpoint: yields JSON events for each step boundary so the UI can
    show progressive state updates without waiting for the entire pipeline."""
    c = store.review_cases.get(case_id)
    if not c:
        raise HTTPException(404, "Case not found")
    orch = WorkflowOrchestrator(store, store_rag, upstage)

    async def event_stream():
        try:
            async for ev in orch.run_stream(c, loan_type=c.get("loan_type_hint")):
                payload = json.dumps(ev, ensure_ascii=False, default=str)
                yield f"data: {payload}\n\n"
            store.review_cases[case_id] = c
            store._save("review_cases")
        except Exception as e:
            err = {"type": "error", "message": str(e), "trace": traceback.format_exc()[:500]}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            try:
                store.review_cases[case_id] = c
                store._save("review_cases")
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/workflow/cases/{case_id}/chat")
async def case_chat(case_id: str, payload: dict):
    """Ask a free-form question about a reviewed case. Solar-pro grounded on
    the full case context (headline, ratios, steps, evidence, report)."""
    c = store.review_cases.get(case_id)
    if not c:
        raise HTTPException(404, "Case not found")
    q = (payload or {}).get("question", "").strip()
    history = (payload or {}).get("history", []) or []
    if not q:
        raise HTTPException(400, "Missing 'question'")

    steps = c.get("steps", [])
    def _out(step_id):
        for s in steps:
            if s.get("id") == step_id:
                return s.get("output") or {}
        return {}
    s1 = _out("1_intake")
    s2 = _out("2_classify")
    s3 = _out("3_risk")
    s4 = _out("4_regulations")
    s5 = _out("5_report")
    s6 = _out("6_decision")

    compact_ctx = {
        "case_title": c.get("title", ""),
        "status": c.get("status"),
        "headline": {k: (v.get("value") if isinstance(v, dict) else v) for k, v in (s1.get("headline", {}) or {}).items() if v},
        "loan_type": s2.get("loan_type_label"),
        "coverage_pct": s2.get("coverage_pct"),
        "missing": s2.get("missing"),
        "ratios": s3.get("ratios"),
        "signals": s3.get("signals"),
        "cb_grade_demo": s3.get("cb_grade_demo"),
        "pass": s4.get("pass_count"), "warn": s4.get("warn_count"), "fail": s4.get("fail_count"),
        "rule_hits": [
            {"query": r.get("query"), "verdict": (r.get("judgment") or {}).get("verdict"),
             "reason": (r.get("judgment") or {}).get("reason"),
             "rule_excerpt": (r.get("top_rule") or {}).get("text", "")[:160]}
            for r in (s4.get("rule_judgments") or [])
        ],
        "decision": s6.get("decision"),
        "total_score": s6.get("total_score"),
        "reasons": s6.get("reasons"),
        "conditions": s6.get("conditions"),
        "report_md_head": (s5.get("report_markdown") or "")[:1400],
    }

    messages = [
        {"role": "system", "content": (
            "너는 은행 여신 심사관을 보조하는 AI 어시스턴트이다. "
            "주어진 심사 건 컨텍스트를 근거로만 질문에 답한다. 컨텍스트에 없는 내용은 "
            "'해당 정보는 심사 건에 기록되지 않았습니다' 라고 답한다. "
            "답변은 한국어, 2-5문장으로 간결하게, 숫자·근거를 인용한다."
        )},
        {"role": "user", "content": (
            "【심사 건 컨텍스트】\n" + json.dumps(compact_ctx, ensure_ascii=False, indent=2)
        )},
    ]
    for h in history[-6:]:
        role = h.get("role")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": q})

    try:
        answer = await upstage.chat_completion(
            messages, temperature=0.2, max_tokens=600, detail="case chat Q&A",
        )
    except Exception as e:
        raise HTTPException(500, f"Chat failed: {e}")
    return {"answer": answer, "case_id": case_id}


@app.post("/api/workflow/cases/{case_id}/report-pdf")
async def case_report_pdf(case_id: str):
    """Render Step 5 markdown report as a Korean-friendly PDF using Pretendard."""
    c = store.review_cases.get(case_id)
    if not c:
        raise HTTPException(404, "Case not found")
    steps = c.get("steps", [])
    step5 = next((s for s in steps if s.get("id") == "5_report"), None)
    step6 = next((s for s in steps if s.get("id") == "6_decision"), None)
    md = ((step5 or {}).get("output") or {}).get("report_markdown") or ""
    if not md.strip():
        raise HTTPException(400, "심사 의견서가 아직 생성되지 않았습니다.")
    decision = ((step6 or {}).get("output") or {}).get("decision") or ""
    total_score = ((step6 or {}).get("output") or {}).get("total_score")

    pdf_bytes = _build_review_pdf(c, md, decision, total_score)
    filename = f"review_{c['id']}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _build_review_pdf(case: dict, md: str, decision: str, total_score) -> bytes:
    """Render markdown report to PDF with Pretendard Korean font."""
    import io
    import re as _re
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    )

    fonts_dir = os.path.join(os.path.dirname(__file__), "fonts")
    for name, fname in [
        ("Pretendard", "Pretendard-Regular.ttf"),
        ("Pretendard-Bold", "Pretendard-Bold.ttf"),
        ("Pretendard-SemiBold", "Pretendard-SemiBold.ttf"),
    ]:
        if name not in pdfmetrics.getRegisteredFontNames():
            try:
                pdfmetrics.registerFont(TTFont(name, os.path.join(fonts_dir, fname)))
            except Exception as e:
                print(f"[PDF] font {fname}: {e}")

    base_font = "Pretendard" if "Pretendard" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    bold_font = "Pretendard-Bold" if "Pretendard-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"

    styles = {
        "title": ParagraphStyle("t", fontName=bold_font, fontSize=18, leading=24, textColor=HexColor("#0E1A2B")),
        "subtitle": ParagraphStyle("s", fontName=base_font, fontSize=10, leading=14, textColor=HexColor("#6B7280")),
        "h2": ParagraphStyle("h2", fontName=bold_font, fontSize=13, leading=18,
                             textColor=HexColor("#0E1A2B"), spaceBefore=10, spaceAfter=4),
        "h3": ParagraphStyle("h3", fontName=bold_font, fontSize=11, leading=16,
                             textColor=HexColor("#374151"), spaceBefore=6, spaceAfter=2),
        "body": ParagraphStyle("b", fontName=base_font, fontSize=10, leading=15.5,
                               textColor=HexColor("#111827"), spaceAfter=3),
        "bullet": ParagraphStyle("bl", fontName=base_font, fontSize=10, leading=15, leftIndent=12,
                                 bulletIndent=0, textColor=HexColor("#111827"), spaceAfter=2),
        "meta": ParagraphStyle("m", fontName=base_font, fontSize=9, leading=13, textColor=HexColor("#6B7280")),
    }

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=18*mm, bottomMargin=18*mm,
        title=f"여신 심사의견서 · {case.get('title','')}",
    )
    story = []

    # Header block
    dec_color_map = {
        "승인": "#2F7D5B",
        "조건부 승인": "#B47A1F",
        "승인 거절": "#B84545",
        "불승인": "#B84545",
    }
    dec_color = dec_color_map.get(decision, "#374151")
    story.append(Paragraph("여신 심사의견서", styles["title"]))
    story.append(Paragraph(
        f"심사 건 · {_pdf_escape(case.get('title',''))} &nbsp;·&nbsp; 생성일 "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        styles["subtitle"],
    ))

    header_tbl = Table(
        [[
            Paragraph(f"<b>판정</b>&nbsp; <font color='{dec_color}'><b>{_pdf_escape(decision or '—')}</b></font>", styles["body"]),
            Paragraph(f"<b>종합 점수</b>&nbsp; {total_score if total_score is not None else '—'} / 100", styles["body"]),
            Paragraph(f"<b>Case ID</b>&nbsp; {_pdf_escape(case.get('id',''))}", styles["body"]),
        ]],
        colWidths=[60*mm, 55*mm, 55*mm],
    )
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), HexColor("#F5F7FA")),
        ("BOX", (0,0), (-1,-1), 0.5, HexColor("#E5E7EB")),
        ("TOPPADDING", (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
    ]))
    story.append(Spacer(1, 6))
    story.append(header_tbl)
    story.append(Spacer(1, 10))

    # Render markdown block-by-block
    for block in _md_to_flowables(md, styles):
        story.append(block)

    # Footer meta
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "※ 본 의견서는 Upstage Document AI 파이프라인(Document Parse Enhanced + Universal IE + Solar-pro) "
        "을 사용하여 자동 생성되었습니다. POC 데모용.",
        styles["meta"],
    ))

    doc.build(story)
    return buf.getvalue()


def _pdf_escape(s: str) -> str:
    import html as _html
    return _html.escape(str(s or ""))


def _md_to_flowables(md: str, styles):
    """Minimal markdown → reportlab flowables (headings, bullets, bold, paragraphs)."""
    from reportlab.platypus import Paragraph, Spacer
    import re as _re
    out = []
    lines = md.replace("\r", "").split("\n")
    buf_para: list[str] = []

    def flush():
        if buf_para:
            text = " ".join(buf_para).strip()
            if text:
                out.append(Paragraph(_md_inline(text), styles["body"]))
            buf_para.clear()

    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            flush()
            out.append(Spacer(1, 4))
            continue
        if stripped.startswith("### "):
            flush()
            out.append(Paragraph(_md_inline(stripped[4:]), styles["h3"]))
            continue
        if stripped.startswith("## "):
            flush()
            out.append(Paragraph(_md_inline(stripped[3:]), styles["h2"]))
            continue
        if stripped.startswith("# "):
            flush()
            out.append(Paragraph(_md_inline(stripped[2:]), styles["h2"]))
            continue
        m_bul = _re.match(r"^[-*·]\s+(.*)", stripped)
        if m_bul:
            flush()
            out.append(Paragraph("• " + _md_inline(m_bul.group(1)), styles["bullet"]))
            continue
        m_num = _re.match(r"^\d+\.\s+(.*)", stripped)
        if m_num:
            flush()
            out.append(Paragraph(_md_inline(m_num.group(1)), styles["bullet"]))
            continue
        buf_para.append(stripped)
    flush()
    return out


def _md_inline(text: str) -> str:
    """Bold **x** → <b>x</b>, escape HTML, preserve spaces."""
    import html as _html
    import re as _re
    t = _html.escape(text)
    t = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = _re.sub(r"\*(.+?)\*", r"<i>\1</i>", t)
    t = _re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", t)
    return t


# ──────────────────────────────────────────────
# Regulation text preview (full text for modal)
# ──────────────────────────────────────────────
@app.get("/api/rag/regulations/{reg_id}")
async def get_regulation(reg_id: str):
    reg = store_rag.get_regulation(reg_id)
    if not reg:
        raise HTTPException(404, "Regulation not found")
    return {
        "id": reg["id"],
        "filename": reg["filename"],
        "uploaded_at": reg["uploaded_at"],
        "num_chunks": len(reg.get("chunks", [])),
        "full_text": reg.get("full_text", ""),
        "chunks": [
            {"id": c.get("id"), "page": c.get("page", 1), "text": c.get("text", "")}
            for c in reg.get("chunks", [])
        ],
    }


# ──────────────────────────────────────────────
# Usage CSV export
# ──────────────────────────────────────────────
@app.get("/api/usage/export")
async def usage_export_csv():
    import csv
    import io as _io
    logs = store.get_usage_logs()
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "timestamp", "api_type", "model",
        "prompt_tokens", "completion_tokens", "total_tokens",
        "doc_id", "detail",
    ])
    for l in logs:
        w.writerow([
            l.get("timestamp", ""), l.get("api_type", ""), l.get("model", ""),
            l.get("prompt_tokens", 0), l.get("completion_tokens", 0), l.get("total_tokens", 0),
            l.get("doc_id", ""), l.get("detail", ""),
        ])
    data = "\ufeff" + buf.getvalue()  # BOM for Excel Korean
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="usage_{datetime.now().strftime("%Y%m%d_%H%M")}.csv"',
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8010, reload=False)
