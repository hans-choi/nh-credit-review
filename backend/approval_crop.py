"""Per-cell approval stamp/signature detection for 근저당권설정계약서.

Strategy:
  1. Find the approval table element in DP output (contains text "본인자필확인" etc.).
  2. Use its bbox + known 6-column structure (결재 | 본인자필확인 | 팀원 | 팀장 | 팀장 | 영업점장)
     to compute 5 cell bboxes.
  3. Extend vertically below the label row to include the stamp area.
  4. Crop each cell into a standalone image for targeted IE inspection.
"""

import os
import numpy as np
from PIL import Image

# Approval table is 6 columns: [결재] [본인자필확인] [팀원] [팀장] [팀장] [영업점장]
# The 결재 header column is narrower. Approximate width proportions.
HEADER_COL_WIDTH_RATIO = 0.12   # 결재 column ≈ 12% of total table width
APPROVAL_CELLS = 5               # number of approval cells (excluding 결재 column)

# Stamp area extends below the detected label bbox by this multiple of bbox height.
# Empirically: DP bbox typically covers the full approval-row height (label + stamp).
# Small positive margin is enough to safely include any overflow.
STAMP_AREA_HEIGHT_MULT = 0.25


def find_approval_table_bbox(elements: list[dict]) -> dict | None:
    """Return normalized bbox {x1,y1,x2,y2} of the approval table header, or None."""
    for el in elements:
        text = (el.get("text") or "").strip()
        if not text:
            continue
        # Match table element with 본인자필확인 in header text
        if "본인자필확인" in text and ("영업점장" in text or "팀원" in text or "팀장" in text):
            coords = el.get("coordinates")
            if not coords or not isinstance(coords, list):
                continue
            xs = [p.get("x", 0) for p in coords]
            ys = [p.get("y", 0) for p in coords]
            return {
                "x1": min(xs),
                "y1": min(ys),
                "x2": max(xs),
                "y2": max(ys),
            }
    return None


def compute_cell_bboxes(table_bbox: dict, image_size: tuple[int, int]) -> list[dict]:
    """Divide approval table header bbox into 5 approval-cell pixel bboxes
    (excluding leftmost 결재 header column) with stamp area extended below."""
    W, H = image_size
    nx1, ny1 = table_bbox["x1"], table_bbox["y1"]
    nx2, ny2 = table_bbox["x2"], table_bbox["y2"]

    table_width = nx2 - nx1
    label_height = ny2 - ny1

    # Skip the leftmost 결재 column
    approval_x_start = nx1 + table_width * HEADER_COL_WIDTH_RATIO
    approval_x_end = nx2
    cell_width = (approval_x_end - approval_x_start) / APPROVAL_CELLS

    # Stamp area: include label row + extended area below
    # y_top = slightly above label top (for some margin)
    # y_bottom = label bottom + multiplier × label height
    y_top = ny1
    y_bottom = ny2 + label_height * STAMP_AREA_HEIGHT_MULT
    # Clamp
    y_top = max(0.0, y_top)
    y_bottom = min(1.0, y_bottom)

    cells = []
    for i in range(APPROVAL_CELLS):
        nx_left = approval_x_start + i * cell_width
        nx_right = approval_x_start + (i + 1) * cell_width
        cells.append({
            "nx1": nx_left, "ny1": y_top,
            "nx2": nx_right, "ny2": y_bottom,
            "x1": int(nx_left * W),
            "y1": int(y_top * H),
            "x2": int(nx_right * W),
            "y2": int(y_bottom * H),
        })
    return cells


def crop_cells(image_path: str, cells: list[dict], out_dir: str, doc_id: str) -> list[str]:
    """Crop the source image per cell and save to disk. Returns list of crop paths."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    with Image.open(image_path) as im:
        for i, cell in enumerate(cells):
            box = (cell["x1"], cell["y1"], cell["x2"], cell["y2"])
            crop = im.crop(box)
            # Upscale small crops so VLM sees them clearly
            w, h = crop.size
            if max(w, h) < 400:
                scale = 400 / max(w, h)
                crop = crop.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            out_path = os.path.join(out_dir, f"_approval_{doc_id}_cell{i}.png")
            crop.save(out_path, "PNG")
            paths.append(out_path)
    return paths


def crop_approval_strip(image_path: str, cells: list[dict], out_dir: str, doc_id: str) -> str:
    """Crop the full approval strip (all 5 cells combined into one wide image)."""
    os.makedirs(out_dir, exist_ok=True)
    # Combine bbox spanning all cells horizontally
    x1 = min(c["x1"] for c in cells)
    x2 = max(c["x2"] for c in cells)
    y1 = min(c["y1"] for c in cells)
    y2 = max(c["y2"] for c in cells)
    with Image.open(image_path) as im:
        crop = im.crop((x1, y1, x2, y2))
        # Upscale for better VLM detail
        w, h = crop.size
        target_w = 1200
        if w < target_w:
            scale = target_w / w
            crop = crop.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        out_path = os.path.join(out_dir, f"_approval_strip_{doc_id}.png")
        crop.save(out_path, "PNG")
    return out_path


# ─── IE schema for strip-based stamp detection ───
STAMP_STRIP_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "stamp_strip_detection",
        "schema": {
            "type": "object",
            "properties": {
                "has_stamp_per_cell": {
                    "type": "array",
                    "description": (
                        "이 이미지는 문서의 결재표에서 5개의 결재 셀(본인자필확인/팀원/팀장/팀장/영업점장)만 "
                        "잘라낸 가로 strip 이미지이다. 왼쪽에서 오른쪽 순서로 각 셀을 확인하여, "
                        "셀 내부에 도장(붉은/검은 인주·인장) 또는 서명(필기체 글자)이 있으면 true, "
                        "셀 라벨만 있고 아래 공란이 완전히 비어있으면 false. "
                        "반드시 정확히 5개 원소의 boolean 배열. "
                        "순서: [0]=본인자필확인(왼쪽), [1]=팀원, [2]=팀장(3번째), "
                        "[3]=팀장(4번째), [4]=영업점장(오른쪽). "
                        "매우 관대하게 판단 — 옅거나 일부만 남은 흔적도 true. "
                        "5개 셀 중 비어있는 셀 수를 먼저 세어본 후 배열 채움. "
                        "일반적으로 4~5개 셀에 날인됨 — false가 2개 이상이면 재검토."
                    ),
                    "items": {"type": "boolean"},
                },
            },
        },
    },
}


def cleanup_crops(paths: list[str]):
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def find_borrower_signature_region(elements: list[dict], image_size: tuple[int, int]) -> dict | None:
    """Find the '본 인' (borrower) signature region in 대출거래약정서.

    The 본 인 table typically has 2 rows:
        | 본 인 | 성 명 : [name]     서명 또는 (인) |
        |        | 주 소 : [address]                     |
    Signature/stamp appears on the 성명 row, right portion.

    Returns {x1, y1, x2, y2} in pixel coords, or None.
    """
    W, H = image_size
    for el in elements:
        text = el.get("text") or ""
        coords = el.get("coordinates")
        if not text or not coords or not isinstance(coords, list):
            continue
        # Must contain "본 인" label + 성명/주소 structure
        is_table_like = (el.get("category") or "").lower() == "table" or "|" in text
        has_label = "본 인" in text or "본인" in text
        has_name_row = "성 명" in text or "성명" in text
        if not (has_label and has_name_row and is_table_like):
            continue
        # Must be in the upper half (borrower table is near top)
        xs = [p.get("x", 0) for p in coords]
        ys = [p.get("y", 0) for p in coords]
        tx1, tx2 = min(xs), max(xs)
        ty1, ty2 = min(ys), max(ys)
        if ty1 > 0.40:
            continue  # too low on page — probably not the borrower table
        tw = tx2 - tx1
        th = ty2 - ty1
        # Stamp area: right ~28% of the 성명 row (top half of table)
        stamp_x1 = tx1 + tw * 0.72
        stamp_x2 = tx2
        name_y1 = ty1
        name_y2 = ty1 + th * 0.55
        return {
            "x1": int(stamp_x1 * W),
            "y1": int(name_y1 * H),
            "x2": int(stamp_x2 * W),
            "y2": int(name_y2 * H),
        }
    return None


def find_party_signature_regions(elements: list[dict], image_size: tuple[int, int]) -> list[dict]:
    """Find signature regions for the 3 party tables in 근저당권설정계약서.

    DP typically splits the parties into 3 separate table elements, each containing
    one party's 성명 and 주소 rows:
        | 채권자겸근저당권자 | 성 명 : … |
        | 채권자겸근저당권자 | 주 소 : … |
    For each found party-table, the stamp area = right portion of the 성명 row.

    Returns list of {role, role_label, x1, y1, x2, y2} in pixel coords.
    """
    W, H = image_size
    # Party label → canonical role_label
    party_patterns = [
        (["채권자겸근저당권자", "채권자 겸 근저당권자"], "채권자"),
        (["채 무 자", "채무자"], "채무자"),
        (["근저당권설정자"], "근저당권설정자"),
    ]

    found_by_role: dict[str, dict] = {}
    for el in elements:
        text = el.get("text") or ""
        coords = el.get("coordinates")
        if not text or not coords or not isinstance(coords, list):
            continue
        # Require table element or markdown-table-like text
        is_table_like = (el.get("category") or "").lower() == "table" or "|" in text

        for patterns, role_label in party_patterns:
            if any(p in text for p in patterns):
                # Skip if this element doesn't look like a party row (avoid narrative matches)
                if not is_table_like and "성 명" not in text and "성명" not in text and "주 소" not in text and "주소" not in text:
                    continue
                if role_label in found_by_role:
                    continue  # keep the first match per role
                xs = [p.get("x", 0) for p in coords]
                ys = [p.get("y", 0) for p in coords]
                tx1, tx2 = min(xs), max(xs)
                ty1, ty2 = min(ys), max(ys)
                found_by_role[role_label] = {
                    "role_label": role_label,
                    "tx1": tx1, "tx2": tx2, "ty1": ty1, "ty2": ty2,
                }
                break

    if not found_by_role:
        return []

    regions = []
    for role_label in ["채권자", "채무자", "근저당권설정자"]:
        info = found_by_role.get(role_label)
        if not info:
            continue
        tx1, tx2 = info["tx1"], info["tx2"]
        ty1, ty2 = info["ty1"], info["ty2"]
        tw = tx2 - tx1
        th = ty2 - ty1
        # Table has 2 rows: 성명 (top half) and 주소 (bottom half).
        # Stamp/signature is on 성명 row, right portion.
        # First column is the label column (~20% width). Then data column (80%).
        # Within data column, name text is left ~55%, stamp area is right ~45%.
        label_col_end = tx1 + tw * 0.20
        stamp_x1 = tx1 + tw * 0.72   # start well into data column, past the name text
        stamp_x2 = tx2
        # 성명 row (top half of table)
        name_y1 = ty1
        name_y2 = ty1 + th * 0.52
        regions.append({
            "role": role_label,
            "role_label": role_label,
            "x1": int(stamp_x1 * W), "y1": int(name_y1 * H),
            "x2": int(stamp_x2 * W), "y2": int(name_y2 * H),
        })
    return regions


def find_director_signature_regions(elements: list[dict], image_size: tuple[int, int]) -> list[dict]:
    """Find signature regions for 대표이사 / 사내이사 in 이사회결의서.

    The signatory block is typically in the lower-right area of the page as a
    multi-line element like:
        대표이사 신
        사내이사 김
        사내이사 김
    We find the best-matching element (lower-right, multi-line with director labels),
    split its bbox vertically into N sub-lines, and compute the stamp area to the
    right of the name on each line.

    Returns list of dicts in order: 대표이사 first, then 사내이사1, 사내이사2, ...
    """
    import re
    W, H = image_size
    # Accept role label followed by anything (space, paren, or immediate name).
    # Reject lines where the role appears mid-sentence (e.g., "의장인 대표이사 XX는").
    DIRECTOR_LINE_RE = re.compile(r"^\s*(대표이사|사내이사|사외이사|감\s*사)\b")
    # Reject if line contains narrative verbs/sentence patterns (not a signatory line).
    NARRATIVE_RE = re.compile(r"(선언|심의|안건|의장|상정|협의|결정|승인|회의|부의|기타)")

    candidates = []
    for el in elements:
        text = (el.get("text") or "").strip()
        coords = el.get("coordinates")
        if not text or not coords or not isinstance(coords, list):
            continue
        ys = [p.get("y", 0) for p in coords]
        xs = [p.get("x", 0) for p in coords]
        ny1, ny2 = min(ys), max(ys)
        nx1, nx2 = min(xs), max(xs)

        # Split into individual lines; for each, check if it's a signatory line
        lines = [ln for ln in text.split("\n") if ln.strip()]
        role_lines = []
        for i, ln in enumerate(lines):
            m = DIRECTOR_LINE_RE.match(ln)
            if not m:
                continue
            if NARRATIVE_RE.search(ln):
                continue
            role_lines.append((i, ln, m))
        if not role_lines:
            continue
        candidates.append({
            "lines": lines,
            "role_lines": role_lines,
            "nx1": nx1, "nx2": nx2, "ny1": ny1, "ny2": ny2,
        })

    if not candidates:
        return []

    # Prefer the candidate with the most director-role lines; tiebreak by most lines.
    candidates.sort(key=lambda c: (-len(c["role_lines"]), -len(c["lines"]), c["ny1"]))
    best = candidates[0]

    total_lines = len(best["lines"])
    line_h_norm = (best["ny2"] - best["ny1"]) / max(1, total_lines)

    regions = []
    sanae_counter = 0
    for (idx, ln, m) in best["role_lines"]:
        role_raw = m.group(1).replace(" ", "")
        if role_raw == "대표이사":
            role = "대표이사"
            label = "대표이사"
        elif role_raw == "사내이사":
            role = "사내이사"
            sanae_counter += 1
            label = f"사내이사{sanae_counter}"
        else:
            continue  # skip 감사/사외이사 for now (not in our schema)

        # Compute pixel bbox for this specific line within the element
        ly1 = best["ny1"] + idx * line_h_norm
        ly2 = best["ny1"] + (idx + 1) * line_h_norm
        # The DP element bbox covers only the "role_label name" text. The stamp/signature
        # is located to the RIGHT of the text (sometimes extending well past element edge).
        # Start just past the name text; extend toward the right edge of the page.
        text_w = best["nx2"] - best["nx1"]
        sig_x1 = best["nx2"]
        sig_x2 = min(0.97, best["nx2"] + text_w * 2.5)
        # Ensure minimum width of 10% of page
        if sig_x2 - sig_x1 < 0.10:
            sig_x2 = min(0.97, sig_x1 + 0.10)
        # Small vertical padding
        pad = line_h_norm * 0.15
        sig_y1 = max(0.0, ly1 - pad)
        sig_y2 = min(1.0, ly2 + pad)

        regions.append({
            "role": role,
            "role_label": label,
            "text": ln.strip(),
            "x1": int(sig_x1 * W), "y1": int(sig_y1 * H),
            "x2": int(sig_x2 * W), "y2": int(sig_y2 * H),
        })
    return regions


def find_real_estate_party_regions(
    elements: list[dict], image_size: tuple[int, int]
) -> list[dict]:
    """Find signature/stamp regions for the 3 parties in 부동산 매매·임대차 계약서.

    Real-estate contracts have a signatory block at the BOTTOM of the page,
    structured as a vertically stacked table with these roles (any subset):
        매 도 인 / 매도인   — seller (sales contract)
        임 대 인 / 임대인   — lessor (lease contract)
        매 수 인 / 매수인   — buyer
        임 차 인 / 임차인   — lessee
        개업공인중개사      — real estate agent

    Each role row has fields 주소·주민번호·전화·성명 + a rightmost signature column
    where 도장(직인) or 서명 appears.

    Returns list of {role, role_label, x1,y1,x2,y2} in pixel coords.
    """
    import re
    W, H = image_size

    # Canonical roles → list of patterns (literal substrings, spaces-tolerant)
    role_patterns = [
        ("매도인_또는_임대인", [
            r"매\s*도\s*인", r"임\s*대\s*인", r"임대사업자",
        ]),
        ("매수인_또는_임차인", [
            r"매\s*수\s*인", r"임\s*차\s*인",
        ]),
        ("개업공인중개사", [
            r"개\s*업\s*공\s*인\s*중\s*개\s*사", r"공인중개사사무소",
            r"개업공인중개자", r"개\s*업", r"중\s*개\s*업\s*자",
        ]),
    ]

    found: dict[str, dict] = {}
    for el in elements:
        text = (el.get("text") or "").strip()
        coords = el.get("coordinates")
        if not text or not coords or not isinstance(coords, list):
            continue
        # Only consider table-like elements at the bottom half of the page.
        xs = [p.get("x", 0) for p in coords]
        ys = [p.get("y", 0) for p in coords]
        tx1, tx2 = min(xs), max(xs)
        ty1, ty2 = min(ys), max(ys)
        if ty1 < 0.35:  # must be in lower 65% of page
            continue
        is_table_like = (el.get("category") or "").lower() == "table" or "|" in text

        for role, patterns in role_patterns:
            if role in found:
                continue
            if not any(re.search(p, text) for p in patterns):
                continue
            # For 개업공인중개사, accept either table-like blocks or short standalone lines
            if not is_table_like and ("성 명" not in text and "성명" not in text
                                      and "사무소" not in text and "중개" not in text):
                continue
            found[role] = {
                "role_label": role,
                "tx1": tx1, "tx2": tx2, "ty1": ty1, "ty2": ty2,
                "text": text,
            }
            break

    if not found:
        return []

    regions = []
    role_order = ["매도인_또는_임대인", "매수인_또는_임차인", "개업공인중개사"]
    for role in role_order:
        info = found.get(role)
        if not info:
            continue
        tx1, tx2 = info["tx1"], info["tx2"]
        ty1, ty2 = info["ty1"], info["ty2"]
        tw = tx2 - tx1
        th = ty2 - ty1
        # Signature column: rightmost ~20-28% of the party table, vertically centered.
        # Accounts for photo tilt — widen ×1.15.
        sig_x1 = tx1 + tw * 0.74
        sig_x2 = min(0.99, tx2 + tw * 0.04)
        # Vertically, restrict to middle 90% (avoid row borders).
        pad_y = th * 0.05
        sig_y1 = ty1 + pad_y
        sig_y2 = ty2 - pad_y
        regions.append({
            "role": role,
            "role_label": role,
            "text": info["text"][:80],
            "x1": int(sig_x1 * W), "y1": int(sig_y1 * H),
            "x2": int(sig_x2 * W), "y2": int(sig_y2 * H),
        })
    return regions


def detect_ink_in_regions(
    image_path: str,
    regions: list[dict],
    threshold_ratio: float = 0.04,
    dark_cutoff: int = 180,
) -> list[tuple[bool, float]]:
    """Generic ink detection for horizontally-oriented signature regions
    (e.g., director name + stamp line in 이사회결의서).

    Unlike `detect_ink_in_cells`, this does NOT skip the top 30% vertically —
    these are single-line strips where ink can appear anywhere.
    """
    results: list[tuple[bool, float]] = []
    with Image.open(image_path) as im:
        im_gray = im.convert("L")
        for reg in regions:
            x1, y1, x2, y2 = reg["x1"], reg["y1"], reg["x2"], reg["y2"]
            inset = max(2, int((x2 - x1) * 0.03))
            box = (x1 + inset, y1, x2 - inset, y2)
            crop = im_gray.crop(box)
            arr = np.asarray(crop, dtype=np.uint8)
            if arr.size == 0:
                results.append((False, 0.0)); continue
            dark_pixels = int(np.sum(arr < dark_cutoff))
            ratio = dark_pixels / arr.size
            results.append((ratio > threshold_ratio, ratio))
    return results


def detect_ink_in_cells(
    image_path: str,
    cells: list[dict],
    threshold_ratio: float = 0.07,
    dark_cutoff: int = 180,
) -> list[tuple[bool, float]]:
    """Heuristic ink detection: count dark pixels in the lower portion (stamp area)
    of each cell. Returns list of (has_ink, ratio) per cell.

    IE/VLM fails on non-textual visual elements (stamps, signatures). Pixel density
    is a deterministic signal: a stamp or signature has significantly more dark
    pixels than a blank white cell.

    Args:
        image_path: source document image
        cells: list of pixel bboxes from compute_cell_bboxes
        threshold_ratio: min fraction of dark pixels to count as "has content"
        dark_cutoff: grayscale value below which a pixel is considered ink (0-255)
    """
    results: list[tuple[bool, float]] = []
    with Image.open(image_path) as im:
        im_gray = im.convert("L")
        for cell in cells:
            # Focus on bottom ~70% of the cell (skip the label area at top)
            x1, y1, x2, y2 = cell["x1"], cell["y1"], cell["x2"], cell["y2"]
            height = y2 - y1
            stamp_y1 = y1 + int(height * 0.30)    # skip top 30% (label text)
            # Shrink inward a bit to avoid table borders being counted as ink
            inset = max(2, int((x2 - x1) * 0.05))
            box = (x1 + inset, stamp_y1, x2 - inset, y2 - inset)
            crop = im_gray.crop(box)
            arr = np.asarray(crop, dtype=np.uint8)
            if arr.size == 0:
                results.append((False, 0.0))
                continue
            dark_pixels = int(np.sum(arr < dark_cutoff))
            ratio = dark_pixels / arr.size
            results.append((ratio > threshold_ratio, ratio))
    return results


# ─── IE schema for per-cell stamp detection ───
STAMP_CHECK_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "stamp_detection",
        "schema": {
            "type": "object",
            "properties": {
                "has_stamp_or_signature": {
                    "type": "boolean",
                    "description": (
                        "이 이미지는 문서의 결재표 중 한 개 셀만 잘라낸 crop 이미지이다. "
                        "셀의 라벨(본인자필확인/팀원/팀장/영업점장) 아래의 사각 공란 영역에 "
                        "도장(인주 자국, 붉은/주황/분홍/검은 원형·타원형 흔적) 또는 "
                        "서명(필기체 글자/획)이 있는지 시각적으로 판단. "
                        "있으면 true, 완전히 비어있으면 false. "
                        "매우 관대하게 판단 — 옅거나 일부만 남은 흔적도 true. "
                        "라벨(글자)만 있고 아래 공란이 완전히 백지면 false."
                    ),
                },
            },
        },
    },
}
