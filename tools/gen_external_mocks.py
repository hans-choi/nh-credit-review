"""Generate mock external verification documents per scenario.

Creates 4 PDFs per company simulating what external lookups (DART, NICE CB,
국세청, 대법원) would return. These are used as citable source docs in the
workflow's Step 7 evidence, so every "외부 조회 (Demo)" click can open a
document-like preview with bbox highlights.
"""

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "backend" / "fonts"
pdfmetrics.registerFont(TTFont("Pretendard",    str(FONT_DIR / "Pretendard-Regular.ttf")))
pdfmetrics.registerFont(TTFont("Pretendard-B",  str(FONT_DIR / "Pretendard-Bold.ttf")))
pdfmetrics.registerFont(TTFont("Pretendard-SB", str(FONT_DIR / "Pretendard-SemiBold.ttf")))

# Import scenario data
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_demo_docs import BEST, FAIL, REJECT, make_styles, kv_table, data_table  # noqa: E402

S = make_styles()

OUT_BEST   = ROOT / "sample_data" / "demo_best"
OUT_FAIL   = ROOT / "sample_data" / "demo_fail"
OUT_REJECT = ROOT / "sample_data" / "demo_reject"


def _hdr(title, subtitle):
    return [
        Paragraph(title, S["title"]),
        Paragraph(subtitle, S["subtitle"]),
    ]


def gen_dart_disclosure(d, out_path, profile="healthy"):
    """금감원 전자공시(DART) 분기보고서 요약 — 기업 개요·재무 요약·주요 공시."""
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=22*mm, rightMargin=22*mm,
                            topMargin=20*mm, bottomMargin=18*mm)
    corp_code = "0" + d["사업자번호"].replace("-", "")[:7]
    story = _hdr("금융감독원 전자공시시스템 (DART)", "분기보고서 · 기업 개요 조회 결과")

    story.append(Paragraph("Ⅰ. 기업 개요", S["h2"]))
    story.append(kv_table([
        ["회  사  명",       d["상호"]],
        ["법인등록번호",    d["법인번호"]],
        ["사업자등록번호",  d["사업자번호"]],
        ["대  표  자",       d["대표자"]],
        ["본점 소재지",      d["소재지"]],
        ["업      종",       f"{d['업태']} · {d['종목']}"],
        ["설립일",           d["개업일"]],
        ["고유번호",         corp_code],
    ], col_widths=(44*mm, 116*mm)))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph("Ⅱ. 재무 요약 (당기)", S["h2"]))
    cur = d["재무_당기"]
    story.append(data_table(
        ["계 정 과 목", "금액 (원)", "전기 대비"],
        [
            ["자산총계", f"{int(cur['자산총계']):,}", "—"],
            ["부채총계", f"{int(cur['부채총계']):,}", "—"],
            ["자본총계", f"{int(cur['자본총계']):,}", ""],
            ["매 출 액", f"{int(cur['매출액']):,}", ""],
            ["영업이익", f"{int(cur['영업이익']):,}", ""],
            ["당기순이익", f"{int(cur['당기순이익']):,}", ""],
        ],
        col_widths=(60*mm, 58*mm, 42*mm)))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph("Ⅲ. 최근 3개월 주요 공시", S["h2"]))
    if profile == "healthy":
        rows = [
            ["2026-03-15", "정기보고서 — 분기보고서 (1분기)",            "제출"],
            ["2026-03-10", "기타경영사항 — 주요 계약 체결 공시",         "제출"],
            ["2026-02-28", "주주총회결과 — 정기주주총회 소집 결과",     "제출"],
            ["2025-12-10", "주요사항보고서 — 유상증자 결의",             "제출"],
        ]
    elif profile == "moderate":
        rows = [
            ["2026-03-18", "정기보고서 — 분기보고서 (1분기)",            "제출"],
            ["2026-02-05", "주요사항보고서 — 타법인주식취득 결의",      "제출"],
            ["2025-12-22", "기타경영사항 — 대표이사 변경",               "제출"],
            ["2025-11-30", "정기보고서 — 사업보고서 (2024)",             "제출"],
        ]
    else:  # distressed
        rows = [
            ["2026-04-05", "주요사항보고서 — 감사의견 부적정",           "경보"],
            ["2026-03-20", "공정공시 — 영업실적 잠정치 (적자 확대)",    "제출"],
            ["2026-02-18", "주요사항보고서 — 유동성 위기 관련 공시",    "경보"],
            ["2026-01-12", "투자자 유의 — 관리종목 지정 사유 발생",     "경보"],
        ]
    story.append(data_table(["공시 일자", "공시 제목", "상태"], rows,
                            col_widths=(30*mm, 110*mm, 20*mm)))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph("Ⅳ. 감사 및 지배구조", S["h2"]))
    감사의견 = d.get("감사의견", "적정")
    story.append(kv_table([
        ["외부감사 수행",     "O"],
        ["감  사  인",         d.get("감사인", "—")],
        ["감사 의견",         감사의견],
        ["관리종목 지정 여부", "아니오" if profile != "distressed" else "예 (실적 악화)"],
    ], col_widths=(44*mm, 116*mm)))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        "본 조회 결과는 금융감독원 전자공시시스템(DART, https://dart.fss.or.kr) 에 공시된 "
        "기업 공개 정보를 발췌 요약한 것이며, 데모 시연을 위한 Mock 응답임을 명시합니다.",
        S["note"]))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("조회일: 2026년 04월 22일", S["body"]))
    story.append(Paragraph("<b>금융감독원 전자공시시스템</b>", S["h2"]))
    doc.build(story)


def gen_nice_cb_report(d, out_path, profile="healthy"):
    """NICE 신용평가정보 기업 신용리포트."""
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=22*mm, rightMargin=22*mm,
                            topMargin=20*mm, bottomMargin=18*mm)
    if profile == "healthy":
        grade, score = "AA-", 88
        risk_level = "저위험"
        연체 = "없음"
    elif profile == "moderate":
        grade, score = "BBB+", 78
        risk_level = "중위험"
        연체 = "최근 2년 이내 단기 연체 1건 (완납)"
    else:
        grade, score = "CCC", 52
        risk_level = "고위험"
        연체 = "30일 이상 연체 2건 · 기한이익상실 이력 1건"

    story = _hdr("NICE 기업 신용 정보 리포트", "KCB · NICE 신용조회 결과")
    story.append(Paragraph("Ⅰ. 기업 식별 정보", S["h2"]))
    story.append(kv_table([
        ["회  사  명",         d["상호"]],
        ["사업자등록번호",    d["사업자번호"]],
        ["법인등록번호",       d["법인번호"]],
        ["대  표  자",         d["대표자"]],
        ["업      종",         f"{d['업태']} · {d['종목']}"],
    ], col_widths=(44*mm, 116*mm)))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph("Ⅱ. 신용 등급 요약", S["h2"]))
    story.append(kv_table([
        ["NICE 기업 등급",    grade],
        ["신용 점수",          f"{score} / 100"],
        ["리스크 레벨",        risk_level],
        ["평가 기준일",        "2026년 04월 15일"],
        ["평가 모델",          "NICE CB-Corp v5.2"],
    ], col_widths=(44*mm, 116*mm)))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph("Ⅲ. 연체·부도 이력", S["h2"]))
    story.append(kv_table([
        ["연체 이력",          연체],
        ["최근 2년 부도/회생", "없음" if profile != "distressed" else "파산 신청 이력 있음 (기각)"],
        ["법적 분쟁",          "없음" if profile == "healthy" else "소액 계약 분쟁 1건" if profile == "moderate" else "채권자 대위변제 분쟁 3건"],
    ], col_widths=(44*mm, 116*mm)))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph("Ⅳ. 금융 거래 현황 (타행 포함)", S["h2"]))
    if profile == "healthy":
        rows = [
            ["일반 대출",  "NH농협은행",   "4.2억원",  "정상"],
            ["무역금융",   "KB국민은행",   "2.0억원",  "정상"],
            ["할부금융",   "현대캐피탈",   "0.3억원",  "정상"],
        ]
        total = "6.5억원"
    elif profile == "moderate":
        rows = [
            ["일반 대출",  "신한은행",     "18.0억원", "정상"],
            ["기업 여신",  "우리은행",     "12.5억원", "정상"],
            ["리스",       "하나캐피탈",   "3.2억원",  "정상"],
            ["할부금융",   "현대캐피탈",   "1.8억원",  "1회 연체"],
        ]
        total = "35.5억원"
    else:
        rows = [
            ["일반 대출",  "광주은행",     "45.0억원", "연체"],
            ["기업 여신",  "부산은행",     "28.0억원", "연체"],
            ["팩토링",     "BNK캐피탈",   "15.5억원", "기한이익상실"],
            ["리스",       "AJ캐피탈",     "8.2억원",  "회수진행"],
        ]
        total = "96.7억원"
    story.append(data_table(
        ["여신 과목", "금융기관", "잔액", "상태"],
        rows, col_widths=(40*mm, 50*mm, 34*mm, 36*mm)))
    story.append(Paragraph(f"타행 총 여신 잔액: {total}", S["body"]))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        "본 리포트는 NICE 평가정보(주)의 Mock 응답이며 실제 데이터와 무관합니다. "
        "운영 환경에서는 NICE CB / KCB API 계약을 통한 실시간 조회가 필요합니다.",
        S["note"]))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("조회일: 2026년 04월 22일", S["body"]))
    story.append(Paragraph("<b>NICE 평가정보 주식회사</b>", S["h2"]))
    doc.build(story)


def gen_tax_verification(d, out_path, profile="healthy"):
    """국세청 납세증명·체납확인 조회."""
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=22*mm, rightMargin=22*mm,
                            topMargin=20*mm, bottomMargin=18*mm)
    story = _hdr("국세 납세증명 · 체납확인 조회", "국세청 홈택스 Open API")
    story.append(Paragraph("Ⅰ. 신청인 (조회 대상)", S["h2"]))
    story.append(kv_table([
        ["성명 / 상호",          d["상호"]],
        ["사업자등록번호",       d["사업자번호"]],
        ["법인등록번호",          d["법인번호"]],
        ["주        소",          d["소재지"]],
    ], col_widths=(44*mm, 116*mm)))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph("Ⅱ. 국세 체납 현황", S["h2"]))
    if profile == "healthy":
        story.append(Paragraph("「국세징수법」 제108조에 따라 확인한 결과 <b>체납 국세가 없음</b>을 증명합니다.", S["body"]))
        story.append(Spacer(1, 3*mm))
        story.append(kv_table([
            ["체납 여부",      "체납없음"],
            ["체납 세액 합계", "0 원"],
            ["유효 기간",      "발급일로부터 30일"],
        ], col_widths=(44*mm, 116*mm)))
    elif profile == "moderate":
        story.append(Paragraph("국세 체납 이력이 일부 확인되었습니다 (전액 완납 처리).", S["body"]))
        story.append(Spacer(1, 3*mm))
        story.append(data_table(
            ["세목", "과세연월", "체납세액 (원)", "상태"],
            [["부가가치세", "2024-01", "5,820,000", "완납"],
             ["법인세",     "2023-04", "2,300,000", "완납"]],
            col_widths=(40*mm, 30*mm, 40*mm, 50*mm)))
    else:
        story.append(Paragraph("아래와 같이 다수의 <b>국세 체납이 확인</b>됩니다.", S["body"]))
        story.append(Spacer(1, 3*mm))
        rows = [
            ["부가가치세", "2024-01", "48,200,000", "체납"],
            ["법인세",     "2023-03", "92,500,000", "체납 · 가산금 누적"],
            ["원천징수소득세", "2024-12", "38,900,000", "체납"],
        ]
        total = 48200000 + 92500000 + 38900000
        rows.append(["합 계", "", f"{total:,}", ""])
        story.append(data_table(
            ["세목", "과세연월", "체납세액 (원)", "상태"],
            rows, col_widths=(40*mm, 30*mm, 40*mm, 50*mm), highlight_row=len(rows)-1))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        "본 증명 결과는 국세청 홈택스 Open API 의 Mock 응답이며, 실제 발급 증명서와 법적 효력이 다릅니다.",
        S["note"]))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("조회일: 2026년 04월 22일", S["body"]))
    story.append(Paragraph("<b>국세청 홈택스</b>", S["h2"]))
    doc.build(story)


def gen_court_registry(d, out_path, profile="healthy"):
    """대법원 등기정보광장 · 법인등기 확인."""
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=22*mm, rightMargin=22*mm,
                            topMargin=20*mm, bottomMargin=18*mm)
    story = _hdr("법인 등기사항 확인서", "대법원 등기정보광장 (Open API)")
    story.append(Paragraph("Ⅰ. 법인 기본 정보", S["h2"]))
    story.append(kv_table([
        ["법인명 (상호)",     d["상호"]],
        ["법인등록번호",      d["법인번호"]],
        ["사업자등록번호",    d["사업자번호"]],
        ["본점 소재지",       d["소재지"]],
        ["대  표  이  사",     d["대표자"]],
    ], col_widths=(44*mm, 116*mm)))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph("Ⅱ. 등기 상태", S["h2"]))
    if profile == "healthy":
        상태 = "등기부 존재 · 유효"
        최근변경 = "2025-11-22 · 사내이사 신규 선임"
        말소 = "없음"
    elif profile == "moderate":
        상태 = "등기부 존재 · 유효"
        최근변경 = "2024-08-14 · 본점 이전"
        말소 = "없음"
    else:
        상태 = "등기부 존재 · 주의사항 있음"
        최근변경 = "2025-10-03 · 가압류 및 가처분 다수 기입"
        말소 = "부분 말소 (영업양도 이력)"

    story.append(kv_table([
        ["법인 등기 상태",     상태],
        ["최근 등기 변경일자", 최근변경],
        ["말소 여부",           말소],
        ["경매·회생 절차",    "없음" if profile != "distressed" else "회생절차 신청 (진행 중)"],
    ], col_widths=(44*mm, 116*mm)))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph("Ⅲ. 임원 명단", S["h2"]))
    if profile != "distressed":
        rows = [
            ["대표이사", d["대표자"],      "재임"],
            ["사내이사", "홍길동",          "재임"],
            ["사내이사", "이영희",          "재임"],
            ["감    사", "김철수",          "재임"],
        ]
    else:
        rows = [
            ["대표이사", d["대표자"],      "재임"],
            ["사내이사", "박도준",          "사임 예정"],
            ["사내이사", "(공석)",          "선임 필요"],
            ["감    사", "최지은",          "재임"],
        ]
    story.append(data_table(
        ["직   위", "성명", "상태"],
        rows, col_widths=(40*mm, 70*mm, 50*mm)))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        "본 문서는 대법원 등기정보광장 Open API 의 Mock 응답이며 실제 법인등기부등본과 효력이 다릅니다.",
        S["note"]))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("조회일: 2026년 04월 22일", S["body"]))
    story.append(Paragraph("<b>대법원 등기정보광장</b>", S["h2"]))
    doc.build(story)


def build_externals(data, outdir, profile, label):
    print(f"\n=== Externals for {label} → {outdir} ===")
    gen_dart_disclosure (data, outdir / f"ext_01_DART공시_{data['상호']}.pdf", profile=profile)
    gen_nice_cb_report  (data, outdir / f"ext_02_NICE신용평가_{data['상호']}.pdf", profile=profile)
    gen_tax_verification(data, outdir / f"ext_03_국세청조회_{data['상호']}.pdf", profile=profile)
    gen_court_registry  (data, outdir / f"ext_04_대법원등기_{data['상호']}.pdf", profile=profile)
    for p in sorted(outdir.glob("ext_*.pdf")):
        print(f"  • {p.name}  ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    build_externals(BEST,   OUT_BEST,   "healthy",    "BEST")
    build_externals(FAIL,   OUT_FAIL,   "moderate",   "FAIL")
    build_externals(REJECT, OUT_REJECT, "distressed", "REJECT")
    print("\n✓ External mock PDFs generated.")
