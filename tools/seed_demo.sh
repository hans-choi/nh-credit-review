#!/bin/bash
# Seed all demo documents (BEST / FAIL / REJECT × 5 core + 4 external mocks)
# and run workflows to produce 3 showcase cases visible in 지난 심사 기록.
#
# Requires: backend running at http://127.0.0.1:8010 and a regulation uploaded.
set -e
API=http://127.0.0.1:8010
BASE="$(cd "$(dirname "$0")/.." && pwd)/sample_data"

upload_and_extract() {
  local dt="$1" f="$2"
  local up=$(curl -sS -X POST "${API}/api/documents/upload" -F "file=@${f}" -F "doc_type=${dt}")
  local id=$(echo "$up" | python3 -c "import sys,json;print(json.load(sys.stdin).get('doc_id',''))" 2>/dev/null)
  if [ -z "$id" ]; then
    echo "  UPLOAD FAIL: ${f}: $up" >&2
    return 1
  fi
  curl -sS -X POST --max-time 180 "${API}/api/documents/${id}/extract" >/dev/null
  echo "$id"
}

seed_scenario() {
  local label="$1" dir="$2" shift_or_skip="$3"
  echo "=== Seeding $label from $dir ==="
  local ids=()

  # Core documents
  [ -f "$dir/01_사업자등록증_"*.pdf ] && ids+=($(upload_and_extract business_registration "$dir/01_사업자등록증_"*.pdf))
  [ -f "$dir/02_재무제표_"*.pdf ] && ids+=($(upload_and_extract financial_statement "$dir/02_재무제표_"*.pdf))
  [ -f "$dir/03_여신거래약정서_"*.pdf ] && ids+=($(upload_and_extract credit_agreement "$dir/03_여신거래약정서_"*.pdf))
  [ -f "$dir/04_이사회결의서_"*.pdf ] && ids+=($(upload_and_extract board_resolution "$dir/04_이사회결의서_"*.pdf))
  [ -f "$dir/05_지방세납세증명_"*.pdf ] && ids+=($(upload_and_extract local_tax_certificate "$dir/05_지방세납세증명_"*.pdf))

  # External mock docs (stored under business_registration for convenience — just needs to be findable)
  for extf in "$dir/ext_01_DART공시_"*.pdf "$dir/ext_02_NICE신용평가_"*.pdf "$dir/ext_03_국세청조회_"*.pdf "$dir/ext_04_대법원등기_"*.pdf; do
    [ -f "$extf" ] && upload_and_extract business_registration "$extf" >/dev/null 2>&1 || true
  done

  echo "  core_ids: ${ids[@]}"
  local ids_json=$(printf '%s\n' "${ids[@]}" | python3 -c "import sys,json;print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))")
  local case=$(curl -sS -X POST "${API}/api/workflow/cases" -H "Content-Type: application/json" -d "{\"title\":\"${label}\",\"doc_ids\":${ids_json}}")
  local cid=$(echo "$case" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
  echo "  case_id: $cid · running pipeline..."
  curl -sS -X POST --max-time 300 "${API}/api/workflow/cases/${cid}/run" | python3 -c "
import sys, json
d = json.load(sys.stdin)
s6 = next((s for s in d['steps'] if s['id']=='6_decision'), None)
if s6:
  out = s6['output']
  print(f'  ✓ {out[\"decision\"]}  ·  score {out[\"total_score\"]}/100')
"
}

seed_scenario "(주)녹색테크 · 운전자금 5억 (BEST)" "$BASE/demo_best"
seed_scenario "(주)퍼스트레버리지 · 운전자금 30억 (FAIL)" "$BASE/demo_fail"
seed_scenario "(주)침체테크 · 운전자금 100억 (REJECT)" "$BASE/demo_reject"

echo ""
echo "✓ All demo cases seeded. Open 자동 심사 워크플로우 메뉴 → 지난 심사 기록."
