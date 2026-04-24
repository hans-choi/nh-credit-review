"""Upstage API client - Document Parse (Enhanced) + Universal Information Extraction.

Optimized for NH credit-review documents with handwritten content:
- Document Parse: ocr="force" + coordinates/base64 output for bbox overlay
- Information Extract: schema-driven structured field extraction (handwriting-friendly VLM)
"""

import base64
import httpx
import json
from datetime import datetime
from typing import Optional, Callable

from config import UPSTAGE_API_KEY, UPSTAGE_BASE_URL


class UpstageClient:
    def __init__(self):
        self.api_key = UPSTAGE_API_KEY
        self.base_url = UPSTAGE_BASE_URL
        self.headers = {"Authorization": f"Bearer {self.api_key}"}
        self._usage_callback: Optional[Callable] = None

    def set_usage_callback(self, callback: Callable):
        self._usage_callback = callback

    def _log_usage(self, api_type: str, model: str, usage: dict, doc_id: str = "", detail: str = ""):
        if self._usage_callback:
            self._usage_callback({
                "timestamp": datetime.now().isoformat(),
                "api_type": api_type,
                "model": model,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "doc_id": doc_id,
                "detail": detail,
            })

    async def parse_document(self, file_path: str, doc_id: str = "") -> dict:
        """Document Parse with Enhanced OCR (ocr=force) for handwriting + coordinates."""
        async with httpx.AsyncClient(timeout=180.0) as client:
            with open(file_path, "rb") as f:
                response = await client.post(
                    f"{self.base_url}/document-ai/document-parse",
                    headers=self.headers,
                    files={"document": f},
                    data={
                        "model": "document-parse",
                        "ocr": "force",
                        "output_formats": '["html", "text", "markdown"]',
                        "coordinates": "true",
                        "base64_encoding": '["table", "figure"]',
                    },
                )
            if response.status_code != 200:
                print(f"[DP Error] status={response.status_code}, body={response.text[:500]}")
            response.raise_for_status()
            result = response.json()
            usage = result.get("usage", {})
            num_pages = result.get("num_pages", usage.get("pages", 0))
            self._log_usage("document-parse", "document-parse", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": num_pages or 1,
            }, doc_id=doc_id, detail=f"{num_pages or 1} page(s), ocr=force")
            return result

    async def extract_information(
        self,
        file_path: str,
        schema: dict,
        doc_id: str = "",
        detail: str = "",
    ) -> dict:
        """Universal Information Extraction using a JSON Schema.

        Uses Upstage's VLM-based /information-extraction endpoint which handles
        handwritten content naturally by understanding the visual layout.
        The schema constrains the response to structured key-value + table data.
        """
        # Upstage Universal IE expects base64-encoded file in the `messages` payload
        with open(file_path, "rb") as f:
            file_b64 = base64.b64encode(f.read()).decode("utf-8")

        ext = file_path.lower().rsplit(".", 1)[-1]
        mime_map = {
            "pdf": "application/pdf",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "tif": "image/tiff",
            "tiff": "image/tiff",
            "bmp": "image/bmp",
        }
        mime = mime_map.get(ext, "application/octet-stream")

        payload = {
            "model": "information-extract",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{file_b64}"},
                        }
                    ],
                }
            ],
            "response_format": schema,
        }

        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{self.base_url}/information-extraction",
                headers={**self.headers, "Content-Type": "application/json"},
                json=payload,
            )
            if response.status_code != 200:
                print(f"[IE Error] status={response.status_code}, body={response.text[:600]}")
            response.raise_for_status()
            data = response.json()
            usage = data.get("usage", {})
            self._log_usage("information-extract", "information-extract", usage,
                            doc_id=doc_id, detail=detail or "schema extract")
            content = data["choices"][0]["message"]["content"]
            # content is a JSON string per schema — parse it
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                # Attempt recovery
                trimmed = content.strip()
                if trimmed.startswith("```"):
                    trimmed = trimmed.split("```", 2)[1]
                    if trimmed.lower().startswith("json"):
                        trimmed = trimmed[4:]
                    trimmed = trimmed.rsplit("```", 1)[0].strip()
                try:
                    parsed = json.loads(trimmed)
                except Exception:
                    parsed = {"_raw": content}
            return {"extracted": parsed, "usage": usage, "raw_response": data}

    async def chat_completion(
        self,
        messages: list[dict],
        model: str = "solar-pro",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        doc_id: str = "",
        detail: str = "",
    ) -> str:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={**self.headers, "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            response.raise_for_status()
            data = response.json()
            usage = data.get("usage", {})
            self._log_usage("chat-completion", model, usage, doc_id=doc_id, detail=detail)
            return data["choices"][0]["message"]["content"]

    async def embed(
        self,
        inputs: list[str] | str,
        model: str = "embedding-passage",
        detail: str = "",
    ) -> list[list[float]]:
        """Upstage embeddings — model='embedding-passage' for documents, 'embedding-query' for queries.
        Returns list of embedding vectors (always list of list). dim = 4096.
        """
        single = isinstance(inputs, str)
        payload_inputs = [inputs] if single else list(inputs)
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Batch call (Upstage embeddings accepts array input)
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={**self.headers, "Content-Type": "application/json"},
                json={"model": model, "input": payload_inputs},
            )
            if response.status_code != 200:
                print(f"[Embed Error] status={response.status_code}, body={response.text[:400]}")
            response.raise_for_status()
            data = response.json()
            usage = data.get("usage", {})
            self._log_usage("embedding", model, usage, detail=detail or f"embed {len(payload_inputs)} passage(s)")
            vecs = [item["embedding"] for item in data.get("data", [])]
            return vecs


upstage = UpstageClient()
