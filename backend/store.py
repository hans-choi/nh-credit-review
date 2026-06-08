"""Lightweight document store for NH credit-review POC."""

import json
import os
import uuid
from datetime import datetime
from typing import Optional

from config import DATA_DIR


class DocumentStore:
    def __init__(self):
        self.documents: dict[str, dict] = {}
        self.extractions: dict[str, dict] = {}   # doc_id -> extracted fields
        self.usage_logs: list[dict] = []
        self.review_cases: dict[str, dict] = {}  # case_id -> case record
        self._load()

    def _path(self, name: str) -> str:
        return os.path.join(DATA_DIR, f"{name}.json")

    def _load(self):
        for name in ("documents", "extractions", "usage_logs", "review_cases"):
            p = self._path(name)
            if not os.path.exists(p):
                continue
            if name == "documents":
                self.documents = self._load_documents_safe(p)
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    setattr(self, name, json.load(f))
            except Exception:
                pass

    # ── page_images(페이지 렌더 base64)는 영구 저장하지 않는다 ──────────────
    # 과거 동작은 render-pages가 재생성한 page_images를 documents.json에 되저장해,
    # 파일과 인스턴스 메모리가 계속 커지다 Starter(512MB)에서 OOM을 냈다.
    # 이제 page_images는 요청 시 재생성하고 디스크/메모리에는 두지 않는다.
    _DOC_RESET_BYTES = 35 * 1024 * 1024  # 슬림 시드(~23MB)보다 크게 부풀면 누적으로 간주

    def _seed_documents_path(self) -> Optional[str]:
        for cand in (
            "/app/seed/data/documents.json",
            os.path.join(os.path.dirname(__file__), "seed", "data", "documents.json"),
        ):
            if os.path.exists(cand):
                return cand
        return None

    def _strip_page_images(self, docs) -> bool:
        changed = False
        if isinstance(docs, dict):
            for d in docs.values():
                md = d.get("metadata") if isinstance(d, dict) else None
                if isinstance(md, dict) and md.get("page_images"):
                    md["page_images"] = {}
                    changed = True
        return changed

    def _load_documents_safe(self, p: str) -> dict:
        # 디스크 파일이 슬림 시드보다 크게 부풀었으면(page_images 누적) 512MB에서
        # json.load 자체가 OOM날 수 있다 → 베이크된 슬림 시드로 베이스라인을 복원한다.
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        if size > self._DOC_RESET_BYTES:
            seed = self._seed_documents_path()
            if seed and os.path.abspath(seed) != os.path.abspath(p):
                try:
                    import shutil
                    shutil.copyfile(seed, p)
                except Exception:
                    pass
        try:
            with open(p, "r", encoding="utf-8") as f:
                docs = json.load(f)
        except Exception:
            return {}
        if not isinstance(docs, dict):
            return {}
        if self._strip_page_images(docs):
            try:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(docs, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        return docs

    def _save(self, name: str):
        data = getattr(self, name)
        if name == "documents":
            # page_images는 디스크/메모리에 남기지 않는다(부풀림·OOM 방지)
            self._strip_page_images(data)
        with open(self._path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_document(
        self,
        filename: str,
        file_path: str,
        doc_type: str,
        parsed_text: str,
        parsed_html: str,
        metadata: Optional[dict] = None,
    ) -> str:
        doc_id = uuid.uuid4().hex[:10]
        self.documents[doc_id] = {
            "id": doc_id,
            "filename": filename,
            "file_path": file_path,
            "doc_type": doc_type,
            "parsed_text": parsed_text,
            "parsed_html": parsed_html,
            "metadata": metadata or {},
            "uploaded_at": datetime.now().isoformat(),
        }
        self._save("documents")
        return doc_id

    def get_document(self, doc_id: str) -> Optional[dict]:
        return self.documents.get(doc_id)

    def list_documents(self, doc_type: Optional[str] = None) -> list[dict]:
        docs = list(self.documents.values())
        if doc_type:
            docs = [d for d in docs if d["doc_type"] == doc_type]
        docs.sort(key=lambda d: d.get("uploaded_at", ""), reverse=True)
        return [
            {
                "id": d["id"],
                "filename": d["filename"],
                "doc_type": d["doc_type"],
                "uploaded_at": d["uploaded_at"],
                "has_extraction": d["id"] in self.extractions,
            }
            for d in docs
        ]

    def delete_document(self, doc_id: str) -> bool:
        doc = self.documents.pop(doc_id, None)
        if not doc:
            return False
        self.extractions.pop(doc_id, None)
        self._save("documents")
        self._save("extractions")
        fp = doc.get("file_path", "")
        if fp and os.path.exists(fp):
            try:
                os.remove(fp)
            except Exception:
                pass
        return True

    def save_extraction(self, doc_id: str, extraction: dict):
        self.extractions[doc_id] = {
            **extraction,
            "extracted_at": datetime.now().isoformat(),
        }
        self._save("extractions")

    def get_extraction(self, doc_id: str) -> Optional[dict]:
        return self.extractions.get(doc_id)

    def add_usage_log(self, entry: dict):
        self.usage_logs.append(entry)
        self._save("usage_logs")

    def get_usage_logs(self) -> list[dict]:
        return self.usage_logs

    def get_usage_stats(self) -> dict:
        # ── Upstage pricing (approximate USD) ────────────────────────────
        PRICE_DP_PER_PAGE      = 0.01
        PRICE_IE_INPUT_PER_1M  = 0.50
        PRICE_IE_OUTPUT_PER_1M = 0.50
        PRICE_LLM_INPUT_PER_1M = 0.25
        PRICE_LLM_OUTPUT_PER_1M= 0.25

        dp  = [e for e in self.usage_logs if e.get("api_type") == "document-parse"]
        ie  = [e for e in self.usage_logs if e.get("api_type") == "information-extract"]
        llm = [e for e in self.usage_logs if e.get("api_type") == "chat-completion"]

        dp_pages       = sum(e.get("total_tokens", 0) for e in dp)
        ie_prompt      = sum(e.get("prompt_tokens", 0) for e in ie)
        ie_completion  = sum(e.get("completion_tokens", 0) for e in ie)
        llm_prompt     = sum(e.get("prompt_tokens", 0) for e in llm)
        llm_completion = sum(e.get("completion_tokens", 0) for e in llm)

        cost_dp         = dp_pages * PRICE_DP_PER_PAGE
        cost_ie_input   = ie_prompt      / 1_000_000 * PRICE_IE_INPUT_PER_1M
        cost_ie_output  = ie_completion  / 1_000_000 * PRICE_IE_OUTPUT_PER_1M
        cost_llm_input  = llm_prompt     / 1_000_000 * PRICE_LLM_INPUT_PER_1M
        cost_llm_output = llm_completion / 1_000_000 * PRICE_LLM_OUTPUT_PER_1M
        cost_total      = cost_dp + cost_ie_input + cost_ie_output + cost_llm_input + cost_llm_output

        # Aggregate by function (detail)
        by_function: dict[str, dict] = {}
        for e in self.usage_logs:
            key = e.get("detail") or e.get("api_type") or "unknown"
            rec = by_function.setdefault(key, {"calls": 0, "total_tokens": 0, "api_type": e.get("api_type", "")})
            rec["calls"] += 1
            rec["total_tokens"] += e.get("total_tokens", 0)

        # Aggregate by document
        by_doc: dict[str, dict] = {}
        for e in self.usage_logs:
            d = e.get("doc_id") or ""
            if not d:
                continue
            rec = by_doc.setdefault(d, {"calls": 0, "total_tokens": 0, "actions": set()})
            rec["calls"] += 1
            rec["total_tokens"] += e.get("total_tokens", 0)
            action = e.get("detail") or e.get("api_type") or "unknown"
            rec["actions"].add(action)
        for doc_id, rec in by_doc.items():
            rec["actions"] = sorted(rec["actions"])
            doc = self.documents.get(doc_id, {})
            rec["filename"] = doc.get("filename", doc_id)
            rec["doc_type"] = doc.get("doc_type", "")

        return {
            "total_calls": len(self.usage_logs),
            "dp_calls": len(dp),
            "ie_calls": len(ie),
            "llm_calls": len(llm),
            "dp_pages": dp_pages,
            "ie_total_tokens": ie_prompt + ie_completion,
            "llm_total_tokens": llm_prompt + llm_completion,
            "ie_prompt_tokens": ie_prompt,
            "ie_completion_tokens": ie_completion,
            "llm_prompt_tokens": llm_prompt,
            "llm_completion_tokens": llm_completion,
            "costs": {
                "dp": round(cost_dp, 6),
                "ie_input": round(cost_ie_input, 6),
                "ie_output": round(cost_ie_output, 6),
                "llm_input": round(cost_llm_input, 6),
                "llm_output": round(cost_llm_output, 6),
                "total": round(cost_total, 6),
            },
            "pricing": {
                "dp_per_page": PRICE_DP_PER_PAGE,
                "ie_input_per_1m": PRICE_IE_INPUT_PER_1M,
                "ie_output_per_1m": PRICE_IE_OUTPUT_PER_1M,
                "llm_input_per_1m": PRICE_LLM_INPUT_PER_1M,
                "llm_output_per_1m": PRICE_LLM_OUTPUT_PER_1M,
            },
            "by_function": by_function,
            "by_doc": by_doc,
        }


store = DocumentStore()
