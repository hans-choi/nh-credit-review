"""End-to-end 여신 심사 워크플로우 orchestrator (7 steps).

Phases:
  1. 접수 · 데이터 추출      — aggregate IE extractions from selected docs
  2. 서류 분류 · 완비도      — compare doc_types against required-for-loan-type template
  3. 리스크 평가             — financial ratios (real) + CB/industry risk (mock)
  4. 여신 규정 체크 (RAG)    — Upstage embeddings search + Solar judgment per rule
  5. 심사 의견서 생성        — Solar LLM structured report
  6. 최종 승인 판단          — rule-based decision
  7. 외부 조회 (Mock)        — DART / 국세청 / 대법원 placeholder responses

All real steps are marked "real". Demo-only items carry a "demo" flag in output.
"""

import json
import re
import time
import uuid
from datetime import datetime
from typing import Any

# Required documents per loan type (derived from 여신업무 분석 docx + heuristics)
REQUIRED_DOCS = {
    "기업 운전자금": {
        "label": "기업 운전자금 대출",
        "required": [
            "credit_agreement", "financial_statement", "business_registration",
            "board_resolution", "local_tax_certificate",
        ],
        "optional": ["real_estate_title", "mortgage", "bank_account_copy"],
    },
    "기업 시설자금": {
        "label": "기업 시설자금 대출",
        "required": [
            "credit_agreement", "financial_statement", "business_registration",
            "board_resolution", "real_estate_title", "mortgage",
        ],
        "optional": ["local_tax_certificate", "bank_account_copy"],
    },
    "개인 주택담보": {
        "label": "개인 주택담보대출",
        "required": [
            "loan_agreement", "payroll", "income_cert",
            "real_estate_title", "mortgage",
        ],
        "optional": ["bank_account_copy", "retirement_income_tax"],
    },
    "개인 신용대출": {
        "label": "개인 신용대출",
        "required": ["loan_agreement", "payroll", "income_cert", "bank_account_copy"],
        "optional": ["retirement_income_tax", "local_tax_certificate"],
    },
}


def parse_amount(s: Any) -> float | None:
    """Parse 금액 string to float won. Handles comma, '원', Korean units."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    # Remove Korean units markers and normalize
    s = s.replace("원", "").replace("₩", "").replace("금", "").strip()
    # Handle Korean number units (억, 천만, 백만, 만)
    total = 0.0
    # Try pattern: N억 M천만 원 etc.
    m_ok = False
    for unit, mult in [("조", 1e12), ("억", 1e8), ("천만", 1e7), ("백만", 1e6), ("만", 1e4)]:
        m = re.search(rf"(-?[\d,\.]+)\s*{unit}", s)
        if m:
            try:
                total += float(m.group(1).replace(",", "")) * mult
                s = s.replace(m.group(0), "", 1)
                m_ok = True
            except ValueError:
                pass
    if m_ok:
        # Remaining digits
        rem = re.search(r"-?[\d,\.]+", s)
        if rem:
            try:
                total += float(rem.group(0).replace(",", ""))
            except ValueError:
                pass
        return total
    # Pure numeric
    cleaned = re.sub(r"[^\d\-\.]", "", s)
    if not cleaned or cleaned in ("-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def fmt_won(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1e8:
        return f"{v/1e8:,.2f} 억원"
    if abs(v) >= 1e4:
        return f"{v/1e4:,.0f} 만원"
    return f"{v:,.0f} 원"


class WorkflowOrchestrator:
    """Runs the 7-step pipeline for a single 심사 case."""

    def __init__(self, doc_store, rag_store, upstage_client):
        self.store = doc_store
        self.rag = rag_store
        self.upstage = upstage_client

    STEP_PLAN = [
        ("1_intake",      "접수 · 데이터 추출",       "_step1_intake"),
        ("2_classify",    "서류 분류 · 완비도",       "_step2_classify"),
        ("3_risk",        "리스크 평가",              "_step3_risk"),
        ("4_regulations", "여신 규정 체크 (RAG)",     "_step4_rag"),
        ("5_report",      "심사 의견서 생성",         "_step5_report"),
        ("6_decision",    "최종 승인 판단",           "_step6_decision"),
        ("7_external",    "외부 조회 (Demo)",         "_step7_external"),
    ]

    async def run(self, case: dict, loan_type: str | None = None) -> dict:
        """Execute pipeline. Mutates `case`, returns updated case dict."""
        case.setdefault("steps", [])
        case["status"] = "running"
        case["started_at"] = datetime.now().isoformat()
        case["evidence"] = []

        for step_id, title, method_name in self.STEP_PLAN:
            if step_id == "2_classify":
                await self._run_step(case, step_id, title,
                                     lambda c: self._step2_classify(c, loan_type))
            else:
                await self._run_step(case, step_id, title, getattr(self, method_name))

        case["status"] = "done"
        case["finished_at"] = datetime.now().isoformat()
        return case

    async def run_stream(self, case: dict, loan_type: str | None = None):
        """Async generator that yields SSE-shaped events for each step boundary.

        Event shapes (all JSON-serializable):
          {"type":"case_start", "case":{id,title,status}}
          {"type":"step_start", "step_id":..., "step_index":1..7, "title":...}
          {"type":"step_done",  "step_id":..., "status":..., "output":..., "duration_ms":..., "evidence_delta":[...]}
          {"type":"case_done",  "case": <full case>}
          {"type":"error",      "message":...}
        """
        case.setdefault("steps", [])
        case["status"] = "running"
        case["started_at"] = datetime.now().isoformat()
        case["evidence"] = []

        yield {"type": "case_start", "case": {
            "id": case["id"], "title": case.get("title", ""), "status": "running",
            "total_steps": len(self.STEP_PLAN),
        }}

        for idx, (step_id, title, method_name) in enumerate(self.STEP_PLAN, start=1):
            yield {"type": "step_start", "step_id": step_id,
                   "step_index": idx, "title": title}
            evidence_before = len(case["evidence"])
            if step_id == "2_classify":
                await self._run_step(case, step_id, title,
                                     lambda c: self._step2_classify(c, loan_type))
            else:
                await self._run_step(case, step_id, title, getattr(self, method_name))
            step_entry = case["steps"][-1]
            yield {
                "type": "step_done",
                "step_id": step_id,
                "step_index": idx,
                "status": step_entry.get("status"),
                "title": title,
                "output": step_entry.get("output"),
                "duration_ms": step_entry.get("duration_ms"),
                "error": step_entry.get("error"),
                "evidence_delta": case["evidence"][evidence_before:],
            }

        case["status"] = "done"
        case["finished_at"] = datetime.now().isoformat()
        yield {"type": "case_done", "case": case}

    async def _run_step(self, case: dict, step_id: str, title: str, fn):
        step_entry = {
            "id": step_id,
            "title": title,
            "status": "running",
            "started_at": datetime.now().isoformat(),
        }
        case["steps"].append(step_entry)
        t0 = time.time()
        try:
            output = await fn(case) if callable(fn) else fn
            step_entry["status"] = output.get("_status", "done") if isinstance(output, dict) else "done"
            step_entry["output"] = output
        except Exception as e:
            step_entry["status"] = "error"
            step_entry["error"] = str(e)
            print(f"[Workflow {step_id}] error: {e}")
        step_entry["duration_ms"] = int((time.time() - t0) * 1000)
        step_entry["finished_at"] = datetime.now().isoformat()

    # ─────────────────────────────────────────────
    # STEP 1 — Intake: collect extractions
    # ─────────────────────────────────────────────
    async def _step1_intake(self, case: dict) -> dict:
        docs_data = []
        field_index: dict[str, list] = {}  # field_name → [{value, source_doc_id, source_doc_filename}]

        for doc_id in case["doc_ids"]:
            doc = self.store.get_document(doc_id)
            if not doc:
                continue
            ext_record = self.store.get_extraction(doc_id)
            extracted = (ext_record or {}).get("extracted", {}) or {}
            docs_data.append({
                "doc_id": doc_id,
                "filename": doc["filename"],
                "doc_type": doc["doc_type"],
                "extracted_field_count": len([v for v in extracted.values() if v not in [None, "", [], False]]),
                "has_extraction": bool(extracted),
            })
            # Index key fields
            for k, v in extracted.items():
                if v in [None, "", [], False]:
                    continue
                field_index.setdefault(k, []).append({
                    "value": v if not isinstance(v, (list, dict)) else json.dumps(v, ensure_ascii=False)[:200],
                    "source_doc_id": doc_id,
                    "source_doc_filename": doc["filename"],
                    "source_doc_type": doc["doc_type"],
                })

        # Pick headline values for the case (first hit for commonly needed fields)
        def pick(keys):
            for k in keys:
                if k in field_index and field_index[k]:
                    return field_index[k][0]
            return None

        headline = {
            "회사명": pick(["채무자", "채무자_상호_성명", "회사명", "기업_상호", "본인_기업_상호", "상호"]),
            "대출금액": pick(["대출한도금액", "대출금액", "여신한도", "금액"]),
            "담보_채권최고액": pick(["채권최고액", "근저당설정금액"]),
            "매출액": pick(["매출액"]),
            "영업이익": pick(["영업이익"]),
            "자산총계": pick(["자산총계"]),
            "부채총계": pick(["부채총계"]),
            "자본총계": pick(["자본총계"]),
            "당기순이익": pick(["당기순이익"]),
        }

        # Build evidence entries for headline items (for Evidence Pack)
        for k, v in headline.items():
            if v:
                case["evidence"].append({
                    "step": "1_intake",
                    "category": "데이터 추출",
                    "title": k,
                    "value": v["value"],
                    "source_doc_id": v["source_doc_id"],
                    "source_doc_filename": v["source_doc_filename"],
                })

        return {
            "docs": docs_data,
            "field_index": {k: v[0] if v else None for k, v in field_index.items()},  # 1 sample per field for display
            "headline": headline,
            "total_docs": len(docs_data),
            "total_fields_across_docs": sum(d["extracted_field_count"] for d in docs_data),
        }

    # ─────────────────────────────────────────────
    # STEP 2 — Document classification & completeness
    # ─────────────────────────────────────────────
    async def _step2_classify(self, case: dict, loan_type: str | None) -> dict:
        doc_types_present = set()
        for doc_id in case["doc_ids"]:
            doc = self.store.get_document(doc_id)
            if doc:
                doc_types_present.add(doc["doc_type"])

        # Auto-detect loan type if not specified: look for signals
        if not loan_type:
            if "credit_agreement" in doc_types_present or "business_registration" in doc_types_present:
                if "real_estate_title" in doc_types_present or "mortgage" in doc_types_present:
                    loan_type = "기업 시설자금"
                else:
                    loan_type = "기업 운전자금"
            elif "loan_agreement" in doc_types_present:
                if "mortgage" in doc_types_present or "real_estate_title" in doc_types_present:
                    loan_type = "개인 주택담보"
                else:
                    loan_type = "개인 신용대출"
            else:
                loan_type = "기업 운전자금"  # default

        template = REQUIRED_DOCS.get(loan_type, REQUIRED_DOCS["기업 운전자금"])
        required = template["required"]
        optional = template["optional"]

        present = [rt for rt in required if rt in doc_types_present]
        missing = [rt for rt in required if rt not in doc_types_present]
        opt_present = [rt for rt in optional if rt in doc_types_present]

        coverage = len(present) / max(len(required), 1)

        case["evidence"].append({
            "step": "2_classify",
            "category": "서류 완비도",
            "title": f"{template['label']} · 필수서류 {len(present)}/{len(required)}",
            "value": f"누락: {', '.join(missing) if missing else '없음'}",
        })

        return {
            "detected_loan_type": loan_type,
            "loan_type_label": template["label"],
            "required": required,
            "optional": optional,
            "present": present,
            "missing": missing,
            "optional_present": opt_present,
            "coverage": round(coverage, 3),
            "coverage_pct": round(coverage * 100),
            "completeness_verdict": "완비" if coverage >= 1.0 else ("부분 완비" if coverage >= 0.7 else "미흡"),
        }

    # ─────────────────────────────────────────────
    # STEP 3 — Risk assessment
    # ─────────────────────────────────────────────
    async def _step3_risk(self, case: dict) -> dict:
        step1 = case["steps"][0]["output"]
        headline = step1["headline"]

        def val(name):
            item = headline.get(name)
            return parse_amount(item["value"]) if item else None

        매출액 = val("매출액")
        영업이익 = val("영업이익")
        자산 = val("자산총계")
        부채 = val("부채총계")
        자본 = val("자본총계")
        순이익 = val("당기순이익")

        ratios: dict[str, Any] = {}
        if 부채 is not None and 자본 and 자본 != 0:
            ratios["부채비율"] = round(부채 / 자본 * 100, 1)  # %
        if 자산 is not None and 자본:
            # Equity ratio
            ratios["자기자본비율"] = round(자본 / 자산 * 100, 1) if 자산 else None
        if 영업이익 is not None and 매출액 and 매출액 != 0:
            ratios["영업이익률"] = round(영업이익 / 매출액 * 100, 2)
        if 순이익 is not None and 매출액 and 매출액 != 0:
            ratios["순이익률"] = round(순이익 / 매출액 * 100, 2)

        # Risk signals (real)
        signals = []
        if "부채비율" in ratios and ratios["부채비율"] > 400:
            signals.append({"level": "high", "msg": f"부채비율 {ratios['부채비율']}% — 400% 초과"})
        elif "부채비율" in ratios and ratios["부채비율"] > 200:
            signals.append({"level": "mid", "msg": f"부채비율 {ratios['부채비율']}% — 주의 수준"})
        if "영업이익률" in ratios and ratios["영업이익률"] < 0:
            signals.append({"level": "high", "msg": f"영업적자 {ratios['영업이익률']}%"})
        elif "영업이익률" in ratios and ratios["영업이익률"] < 3:
            signals.append({"level": "mid", "msg": f"영업이익률 {ratios['영업이익률']}% 낮음"})
        if 자본 is not None and 자본 < 0:
            signals.append({"level": "high", "msg": "자본잠식 — 자본총계 < 0"})

        # Mock external signals
        import random
        random.seed(hash(case["id"]) & 0xFFFFFFFF)
        cb_score = random.randint(72, 94)
        cb_grade = "AA" if cb_score >= 88 else ("A+" if cb_score >= 82 else ("A" if cb_score >= 76 else "BBB"))
        industry_risk = random.choice(["저위험", "중위험", "중위험", "고위험"])

        # Evidence entries
        for k, v in ratios.items():
            case["evidence"].append({
                "step": "3_risk",
                "category": "재무 비율",
                "title": k,
                "value": f"{v}%",
            })
        case["evidence"].append({
            "step": "3_risk", "category": "외부 신용조회 (Demo)",
            "title": "NICE CB 등급", "value": f"{cb_grade} ({cb_score}점)",
            "demo": True,
        })
        case["evidence"].append({
            "step": "3_risk", "category": "업종 리스크 (Demo)",
            "title": "업종 등급", "value": industry_risk, "demo": True,
        })

        return {
            "ratios": ratios,
            "signals": signals,
            "cb_score_demo": cb_score,
            "cb_grade_demo": cb_grade,
            "industry_risk_demo": industry_risk,
            "summary": {
                "매출액": fmt_won(매출액),
                "영업이익": fmt_won(영업이익),
                "자산총계": fmt_won(자산),
                "부채총계": fmt_won(부채),
                "자본총계": fmt_won(자본),
                "당기순이익": fmt_won(순이익),
            },
        }

    # ─────────────────────────────────────────────
    # STEP 4 — Regulation RAG check
    # ─────────────────────────────────────────────
    async def _step4_rag(self, case: dict) -> dict:
        regulations = self.rag.list_regulations()
        if not regulations:
            return {
                "_status": "warning",
                "warning": "등록된 규정 문서가 없습니다. 규정 관리 메뉴에서 먼저 업로드 하세요.",
                "queries": [],
                "rule_judgments": [],
            }

        step1 = case["steps"][0]["output"]
        step3 = case["steps"][2]["output"]
        headline = step1["headline"]
        ratios = step3["ratios"]

        # Generate queries based on extracted context
        queries = []
        if headline.get("대출금액"):
            queries.append({"q": "여신 한도 기준 및 승인 절차", "context": "여신 금액"})
        if headline.get("담보_채권최고액"):
            queries.append({"q": "담보 LTV 및 채권최고액 설정 기준", "context": "담보"})
        if headline.get("매출액"):
            queries.append({"q": "기업 여신 재무 비율 기준", "context": "재무"})
        queries.append({"q": "이사회 결의 필요 여부 기준", "context": "절차"})
        queries.append({"q": "신용등급 및 리스크 관리 규정", "context": "리스크"})

        # Run RAG + Solar judgment
        rule_judgments = []
        for q in queries:
            hits = await self.rag.search(q["q"], top_k=3, upstage_client=self.upstage)
            if not hits:
                continue
            # Solar judge each top hit against the case context
            top_hit = hits[0]
            judgment = await self._judge_rule(top_hit["text"], headline, ratios)
            entry = {
                "query": q["q"],
                "context": q["context"],
                "top_rule": {
                    "text": top_hit["text"],
                    "score": round(top_hit["score"], 3),
                    "source_filename": top_hit["reg_filename"],
                    "chunk_id": top_hit["chunk_id"],
                    "page": top_hit["page"],
                },
                "other_hits": [
                    {"text": h["text"][:150], "score": round(h["score"], 3), "filename": h["reg_filename"]}
                    for h in hits[1:]
                ],
                "judgment": judgment,
            }
            rule_judgments.append(entry)
            case["evidence"].append({
                "step": "4_regulations",
                "category": "규정 인용",
                "title": q["q"],
                "value": f"[{judgment['verdict']}] {judgment['reason'][:120]}",
                "source_regulation": top_hit["reg_filename"],
                "rule_text": top_hit["text"][:300],
            })

        return {
            "regulation_count": len(regulations),
            "queries": queries,
            "rule_judgments": rule_judgments,
            "pass_count": sum(1 for r in rule_judgments if r["judgment"]["verdict"] == "통과"),
            "warn_count": sum(1 for r in rule_judgments if r["judgment"]["verdict"] == "주의"),
            "fail_count": sum(1 for r in rule_judgments if r["judgment"]["verdict"] == "위반"),
        }

    async def _judge_rule(self, rule_text: str, headline: dict, ratios: dict) -> dict:
        """Ask Solar LLM to judge whether a regulation passage is satisfied by the case."""
        hl_brief = {k: (v["value"] if v else None) for k, v in headline.items() if v}
        msg = [
            {"role": "system", "content": (
                "너는 은행 여신 심사관이다. 주어진 '여신 규정' 조항 하나와 '심사 건 데이터'를 비교하여, "
                "이 심사 건이 해당 규정 조항을 만족하는지 판정한다.\n\n"
                "반드시 아래 JSON 포맷으로만 답한다 (다른 텍스트 금지):\n"
                '{"verdict":"통과|주의|위반|미판정","reason":"한 문장 근거 설명"}\n\n'
                "판정 기준:\n"
                "- 통과: 심사 데이터가 규정 기준을 명확히 충족.\n"
                "- 주의: 기준에 근접하거나 추가 확인 필요.\n"
                "- 위반: 기준을 명백히 벗어남.\n"
                "- 미판정: 규정이 심사 데이터와 직접 관련 없거나 판단 근거 부족."
            )},
            {"role": "user", "content": (
                f"【여신 규정 조항】\n{rule_text}\n\n"
                f"【심사 건 핵심 데이터】\n{json.dumps(hl_brief, ensure_ascii=False, indent=2)}\n"
                f"\n【재무 비율】\n{json.dumps(ratios, ensure_ascii=False)}"
            )},
        ]
        try:
            raw = await self.upstage.chat_completion(msg, temperature=0.2, max_tokens=300,
                                                    detail="regulation judgment")
            # Parse JSON
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1]
                if raw.lower().startswith("json"):
                    raw = raw[4:]
                raw = raw.rsplit("```", 1)[0].strip()
            parsed = json.loads(raw)
            if parsed.get("verdict") not in ["통과", "주의", "위반", "미판정"]:
                parsed["verdict"] = "미판정"
            return parsed
        except Exception as e:
            return {"verdict": "미판정", "reason": f"LLM 응답 파싱 실패: {str(e)[:80]}"}

    # ─────────────────────────────────────────────
    # STEP 5 — Review opinion draft (Solar LLM)
    # ─────────────────────────────────────────────
    async def _step5_report(self, case: dict) -> dict:
        step1 = case["steps"][0]["output"]
        step2 = case["steps"][1]["output"]
        step3 = case["steps"][2]["output"]
        step4 = case["steps"][3]["output"]

        context = {
            "채무자": step1["headline"].get("회사명", {}).get("value", "N/A") if step1["headline"].get("회사명") else "N/A",
            "여신_유형": step2.get("loan_type_label", "미상"),
            "대출금액": step1["headline"].get("대출금액", {}).get("value", "") if step1["headline"].get("대출금액") else "",
            "담보채권최고액": step1["headline"].get("담보_채권최고액", {}).get("value", "") if step1["headline"].get("담보_채권최고액") else "",
            "재무요약": step3["summary"],
            "재무비율": step3["ratios"],
            "재무리스크신호": step3["signals"],
            "cb등급_demo": f"{step3['cb_grade_demo']} ({step3['cb_score_demo']}점)",
            "업종리스크_demo": step3["industry_risk_demo"],
            "서류완비도": f"{step2['coverage_pct']}% ({step2['completeness_verdict']})",
            "누락서류": step2["missing"],
            "규정_통과": step4.get("pass_count", 0),
            "규정_주의": step4.get("warn_count", 0),
            "규정_위반": step4.get("fail_count", 0),
        }

        msg = [
            {"role": "system", "content": (
                "너는 은행 여신 심사관이다. 주어진 심사 건 데이터를 바탕으로 여신 심사의견서를 작성한다. "
                "마크다운 포맷으로 아래 섹션을 순서대로 작성:\n"
                "## 1. 채무자 및 여신 개요\n"
                "## 2. 재무 현황 분석\n"
                "## 3. 담보 및 보증 현황\n"
                "## 4. 내부 규정 준수 여부\n"
                "## 5. 종합 리스크 평가\n"
                "## 6. 심사 의견\n\n"
                "규칙:\n"
                "- 추출된 필드 값만 사용. 없는 데이터는 '미기재' 로 표기.\n"
                "- 숫자는 원본 포맷 유지.\n"
                "- '[Demo]' 로 표시된 외부 조회 데이터는 그대로 '[Demo]' 표기 유지.\n"
                "- 각 섹션은 3-5문장 내 간결하게.\n"
                "- 마지막 '6. 심사 의견'은 '승인 / 조건부 승인 / 불승인' 중 하나를 명시하고 근거 제시."
            )},
            {"role": "user", "content": (
                "【심사 건 컨텍스트】\n" + json.dumps(context, ensure_ascii=False, indent=2)
            )},
        ]
        report_md = await self.upstage.chat_completion(msg, temperature=0.3, max_tokens=1800,
                                                      detail="review opinion")

        case["evidence"].append({
            "step": "5_report",
            "category": "자동 생성 의견서",
            "title": "여신 심사의견서",
            "value": f"{len(report_md)} chars, Solar-pro generated",
        })
        return {"report_markdown": report_md, "context": context}

    # ─────────────────────────────────────────────
    # STEP 6 — Rule-based final decision
    # ─────────────────────────────────────────────
    async def _step6_decision(self, case: dict) -> dict:
        step2 = case["steps"][1]["output"]
        step3 = case["steps"][2]["output"]
        step4 = case["steps"][3]["output"]

        scores = {
            "서류_완비도": step2["coverage"] * 100,  # 0-100
            "재무_건전성": 100,
            "규정_준수": 100,
            "신용등급": min(100, step3["cb_score_demo"]),
        }

        reasons = []
        conditions = []

        # Document completeness
        if step2["missing"]:
            reasons.append(f"필수서류 {len(step2['missing'])}종 누락: {', '.join(step2['missing'])}")
            conditions.append(f"누락 서류({', '.join(step2['missing'])}) 보완 제출")

        # Financial signals
        for sig in step3["signals"]:
            if sig["level"] == "high":
                scores["재무_건전성"] -= 30
                reasons.append(f"재무 경고: {sig['msg']}")
            elif sig["level"] == "mid":
                scores["재무_건전성"] -= 12

        # Regulation violations
        violations = step4.get("fail_count", 0)
        warnings = step4.get("warn_count", 0)
        if violations:
            scores["규정_준수"] -= 25 * violations
            reasons.append(f"규정 위반 {violations}건")
        if warnings:
            scores["규정_준수"] -= 8 * warnings

        # Hard-fail conditions: 자본잠식, 감사의견 부적정, 서류 심각 부족, 다수 규정 위반
        has_bankruptcy = any("자본잠식" in s["msg"] for s in step3["signals"])
        severe_missing = step2["coverage"] < 0.7   # 70% 미만
        many_violations = violations >= 2

        # Clamp sub-scores
        for k in scores:
            scores[k] = max(0, min(100, scores[k]))
        total_score = round(sum(scores.values()) / 4, 1)

        # Decision
        if has_bankruptcy or severe_missing or many_violations:
            decision = "승인 거절"
            reasons.insert(0, (
                "자본잠식 상태" if has_bankruptcy else
                ("필수서류 완비도 70% 미만" if severe_missing else
                 f"규정 위반 {violations}건")
            ))
        elif total_score >= 80 and not step2["missing"] and violations == 0:
            decision = "승인"
        elif total_score >= 60:
            decision = "조건부 승인"
            if not conditions:
                conditions.append("내부 심사위원회 추가 검토 후 조건부 승인")
        else:
            decision = "승인 거절"

        case["evidence"].append({
            "step": "6_decision",
            "category": "최종 판정",
            "title": decision,
            "value": f"종합 점수 {total_score}/100 · {', '.join(reasons) if reasons else '주요 리스크 시그널 없음'}",
        })

        return {
            "decision": decision,
            "total_score": total_score,
            "sub_scores": scores,
            "reasons": reasons,
            "conditions": conditions,
            "demo_note": "실제 운영 시 결재선·승인 권한 체크 필요",
        }

    # ─────────────────────────────────────────────
    # STEP 7 — External lookups (mock, backed by uploaded mock PDFs when present)
    # ─────────────────────────────────────────────
    async def _step7_external(self, case: dict) -> dict:
        step1 = case["steps"][0]["output"]
        company_item = step1["headline"].get("회사명")
        company = company_item["value"] if company_item else ""

        # Try to find pre-uploaded external mock documents matching this company
        # (filenames begin with "ext_01_DART공시_", "ext_02_NICE신용평가_", etc.)
        ext_docs = self._find_external_mock_docs(company)

        mocks = {}

        def build_mock(label: str, default_data: dict, ext_key: str):
            doc = ext_docs.get(ext_key)
            data = dict(default_data)
            data["status"] = "조회 완료 (Demo)"
            if doc:
                data["source_filename"] = doc["filename"]
                data["source_doc_id"] = doc["id"]
            mocks[label] = data
            ev = {
                "step": "7_external",
                "category": "외부 시스템 (Demo)",
                "title": label,
                "value": data.get("summary", data.get("status", "")),
                "demo": True,
            }
            if doc:
                ev["source_doc_id"] = doc["id"]
                ev["source_doc_filename"] = doc["filename"]
            case["evidence"].append(ev)

        build_mock("금감원 전자공시 (DART)", {
            "법인명": company or "(주)데모기업",
            "summary": f"{company or '대상 법인'} · 분기보고서 및 최근 공시 이력 확인",
            "최근_공시": "분기보고서 (2026.Q1)",
            "감사의견": "조회 결과 참조",
        }, "DART")

        build_mock("NICE · KCB 신용조회", {
            "summary": "기업 신용 등급 · 연체 이력 · 타행 여신 현황 조회",
            "평가기관": "NICE 평가정보 (주)",
        }, "NICE")

        build_mock("국세청 홈택스 납세증명", {
            "summary": "국세 체납 여부 확인 (Mock)",
        }, "TAX")

        build_mock("대법원 등기정보광장", {
            "summary": "법인등기 유효성 · 임원 현황 · 회생/파산 여부 조회",
        }, "COURT")

        demo_note = "실제 연동 시 DART API, 홈택스 API, 대법원 등기정보광장 API, CB사 API 계약 필요"
        if not ext_docs:
            demo_note += " · 외부 Mock PDF 미업로드 (sample_data/demo_*/ext_*.pdf 업로드 시 클릭 가능)"
        return {"mocks": mocks, "external_docs": list(ext_docs.keys()), "demo_note": demo_note}

    def _find_external_mock_docs(self, company: str) -> dict:
        """Scan uploaded documents for pre-generated external mock PDFs matching
        the current company. Returns dict: {key: doc} where key in
        {'DART','NICE','TAX','COURT'}. Best-effort."""
        if not company:
            return {}
        company_core = company.replace("(주)", "").replace(" ", "")
        out = {}
        for doc in self.store.documents.values():
            fname = doc.get("filename", "")
            if not fname.startswith("ext_"):
                continue
            # Filter by company name in filename
            if company_core and company_core not in fname.replace(" ", ""):
                continue
            if "DART공시" in fname or "dart" in fname.lower():
                out.setdefault("DART", doc)
            elif "NICE" in fname or "신용평가" in fname:
                out.setdefault("NICE", doc)
            elif "국세청" in fname or "홈택스" in fname:
                out.setdefault("TAX", doc)
            elif "대법원" in fname or "등기" in fname:
                out.setdefault("COURT", doc)
        return out
