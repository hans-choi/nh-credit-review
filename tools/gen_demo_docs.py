"""Generate demo PDF documents for the NH credit review workflow.

Two scenarios:
  - best: healthy company, all required docs present → 승인 expected
  - fail: struggling company, regulation violations → 조건부/불승인 expected

Produces 5 PDFs per scenario under sample_data/demo_best/ and demo_fail/
"""

import os
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)

# ─── Font setup ──────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "backend" / "fonts"
pdfmetrics.registerFont(TTFont("Pretendard",     str(FONT_DIR / "Pretendard-Regular.ttf")))
pdfmetrics.registerFont(TTFont("Pretendard-B",   str(FONT_DIR / "Pretendard-Bold.ttf")))
pdfmetrics.registerFont(TTFont("Pretendard-SB",  str(FONT_DIR / "Pretendard-SemiBold.ttf")))

# ─── Output dirs ─────────────────────────────────────────────
OUT_BEST   = ROOT / "sample_data" / "demo_best"
OUT_FAIL   = ROOT / "sample_data" / "demo_fail"
OUT_REJECT = ROOT / "sample_data" / "demo_reject"
OUT_BEST.mkdir(parents=True, exist_ok=True)
OUT_FAIL.mkdir(parents=True, exist_ok=True)
OUT_REJECT.mkdir(parents=True, exist_ok=True)

# ─── Common styles ───────────────────────────────────────────
def make_styles():
    ss = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("T", fontName="Pretendard-B", fontSize=18, leading=24,
                                alignment=1, spaceAfter=6*mm, textColor=colors.HexColor("#0f0f0f")),
        "subtitle": ParagraphStyle("ST", fontName="Pretendard", fontSize=10, leading=13,
                                   alignment=1, textColor=colors.HexColor("#555"), spaceAfter=4*mm),
        "h2": ParagraphStyle("H2", fontName="Pretendard-SB", fontSize=12.5, leading=16,
                             textColor=colors.HexColor("#1a1a1a"), spaceBefore=5*mm, spaceAfter=2*mm),
        "body": ParagraphStyle("B", fontName="Pretendard", fontSize=10, leading=14.5,
                               textColor=colors.HexColor("#222")),
        "small": ParagraphStyle("S", fontName="Pretendard", fontSize=9, leading=12,
                                textColor=colors.HexColor("#555")),
        "note": ParagraphStyle("N", fontName="Pretendard", fontSize=8.5, leading=11,
                               textColor=colors.HexColor("#777"), spaceBefore=3*mm),
        "cell_bold": ParagraphStyle("CB", fontName="Pretendard-SB", fontSize=10, leading=13),
        "cell": ParagraphStyle("C", fontName="Pretendard", fontSize=10, leading=13),
    }
    return styles


def kv_table(rows, col_widths=(40*mm, 120*mm)):
    """Key-value 2-column table with soft borders."""
    tbl = Table(rows, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ("FONT",       (0, 0), (-1, -1), "Pretendard", 10),
        ("FONT",       (0, 0), (0, -1),  "Pretendard-SB", 10),
        ("BACKGROUND", (0, 0), (0, -1),  colors.HexColor("#f4f4f4")),
        ("TEXTCOLOR",  (0, 0), (0, -1),  colors.HexColor("#333")),
        ("TEXTCOLOR",  (1, 0), (1, -1),  colors.HexColor("#111")),
        ("GRID",       (0, 0), (-1, -1), 0.4, colors.HexColor("#bbb")),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
    ]))
    return tbl


def data_table(header_row, data_rows, col_widths=None, highlight_row=None):
    """Styled data table with header."""
    all_rows = [header_row] + data_rows
    tbl = Table(all_rows, colWidths=col_widths)
    styles = [
        ("FONT",       (0, 0), (-1, -1),  "Pretendard", 9),
        ("FONT",       (0, 0), (-1, 0),   "Pretendard-SB", 9.5),
        ("BACKGROUND", (0, 0), (-1, 0),   colors.HexColor("#e8e8e8")),
        ("TEXTCOLOR",  (0, 0), (-1, 0),   colors.HexColor("#111")),
        ("ALIGN",      (1, 0), (-1, -1),  "RIGHT"),
        ("ALIGN",      (0, 0), (0, -1),   "LEFT"),
        ("GRID",       (0, 0), (-1, -1),  0.35, colors.HexColor("#bbb")),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
    ]
    if highlight_row is not None:
        styles.append(("BACKGROUND", (0, highlight_row+1), (-1, highlight_row+1), colors.HexColor("#fff7d6")))
        styles.append(("FONT", (0, highlight_row+1), (-1, highlight_row+1), "Pretendard-SB", 9))
    tbl.setStyle(TableStyle(styles))
    return tbl


# ─── Scenario data ───────────────────────────────────────────
BEST = {
    "상호":       "(주)녹색테크",
    "대표자":     "김선우",
    "사업자번호": "214-86-52719",
    "법인번호":   "110111-8812453",
    "소재지":     "서울특별시 강남구 테헤란로 427 (삼성동)",
    "업태":       "제조업",
    "종목":       "전자부품 제조",
    "개업일":     "2015년 03월 12일",
    "결산일":     "2024년 12월 31일",

    "여신_종류": "일반자금대출",
    "여신_금액": "금 오억원정 (₩ 500,000,000)",
    "여신_기간": "2025.05.01 ~ 2026.04.30 (1년)",
    "자금용도":   "운전자금",
    "담보":       "대표이사 개인 연대보증 + 무담보",
    "근저당설정금액": "해당없음",

    "재무_당기": {"매출액": 8250000000, "매출원가": 5480000000,
                 "매출총이익": 2770000000, "판관비": 1950000000, "영업이익": 820000000,
                 "당기순이익": 610000000,
                 "자산총계": 5120000000, "유동자산": 2840000000, "비유동자산": 2280000000,
                 "부채총계": 1540000000, "유동부채": 1010000000, "비유동부채": 530000000,
                 "자본총계": 3580000000},
    "재무_전기": {"매출액": 7380000000, "매출원가": 4910000000,
                 "매출총이익": 2470000000, "판관비": 1810000000, "영업이익": 660000000,
                 "당기순이익": 490000000,
                 "자산총계": 4610000000, "유동자산": 2450000000, "비유동자산": 2160000000,
                 "부채총계": 1340000000, "유동부채": 890000000, "비유동부채": 450000000,
                 "자본총계": 3270000000},
    "감사의견": "적정",
    "감사인":   "(주)한빛회계법인",

    "이사회_일시": "2025년 4월 15일 오전 10시",
    "이사회_장소": "본사 회의실",
    "이사회_재적": "5", "이사회_출석": "5",
    "이사회_결의": "원안대로 만장일치 가결",

    "지방세_체납": "체납없음",
}

FAIL = {
    "상호":       "(주)퍼스트레버리지",
    "대표자":     "박민호",
    "사업자번호": "108-81-33290",
    "법인번호":   "110111-6721983",
    "소재지":     "서울특별시 영등포구 여의대로 38 (여의도동)",
    "업태":       "건설업",
    "종목":       "토목건축공사업",
    "개업일":     "2019년 07월 08일",
    "결산일":     "2024년 12월 31일",

    "여신_종류": "일반자금대출",
    "여신_금액": "금 삼십억원정 (₩ 3,000,000,000)",
    "여신_기간": "2025.05.01 ~ 2025.10.31 (6개월)",
    "자금용도":   "운전자금 · 기한연장",
    "담보":       "후순위 근저당 (선순위 채권최고액 합계 55억원)",
    "근저당설정금액": "금 삼십육억원정 (₩ 3,600,000,000)",

    # 부채비율 700%, 영업적자, 자본잠식 근접
    "재무_당기": {"매출액": 2180000000, "매출원가": 2050000000,
                 "매출총이익": 130000000, "판관비": 430000000, "영업이익": -300000000,
                 "당기순이익": -450000000,
                 "자산총계": 4020000000, "유동자산": 890000000, "비유동자산": 3130000000,
                 "부채총계": 3500000000, "유동부채": 2640000000, "비유동부채": 860000000,
                 "자본총계": 520000000},
    "재무_전기": {"매출액": 3110000000, "매출원가": 2730000000,
                 "매출총이익": 380000000, "판관비": 450000000, "영업이익": -70000000,
                 "당기순이익": -120000000,
                 "자산총계": 4350000000, "유동자산": 1120000000, "비유동자산": 3230000000,
                 "부채총계": 3380000000, "유동부채": 2490000000, "비유동부채": 890000000,
                 "자본총계": 970000000},
    "감사의견": "한정",
    "감사인":   "(주)선진회계법인",

    "이사회_일시": "2025년 4월 20일 오후 2시",
    "이사회_장소": "본사 회의실",
    "이사회_재적": "4", "이사회_출석": "3",
    "이사회_결의": "찬성 2, 반대 1 — 조건부 가결 (선순위 채권자 동의 조건)",

    "지방세_체납": "체납있음 (총 42,180,000원)",
    "체납_상세": [
        ("지방소득세", "2023년 11월", "18,320,000"),
        ("주민세",     "2024년 03월", "8,560,000"),
        ("재산세",     "2024년 09월", "15,300,000"),
    ],
}


# 거절 예상: 자본잠식 + 대형 여신 신청 + 서류 불완전
REJECT = {
    "상호":       "(주)침체테크",
    "대표자":     "최지훈",
    "사업자번호": "317-86-81204",
    "법인번호":   "110111-4492815",
    "소재지":     "경기도 성남시 분당구 정자일로 95 (정자동)",
    "업태":       "도매 및 소매업",
    "종목":       "전자제품 도매",
    "개업일":     "2012년 05월 22일",
    "결산일":     "2024년 12월 31일",

    "여신_종류": "일반자금대출",
    "여신_금액": "금 백억원정 (₩ 10,000,000,000)",
    "여신_기간": "2025.05.01 ~ 2025.10.31 (6개월)",
    "자금용도":   "기존 단기차입금 상환 · 운영자금",
    "담보":       "후순위 근저당 (선순위 합계 88억원)",
    "근저당설정금액": "금 백이십억원정 (₩ 12,000,000,000)",

    # 완전 자본잠식 + 심각한 영업적자
    "재무_당기": {"매출액": 1340000000, "매출원가": 1480000000,
                 "매출총이익": -140000000, "판관비": 610000000, "영업이익": -750000000,
                 "당기순이익": -980000000,
                 "자산총계": 5200000000, "유동자산": 620000000, "비유동자산": 4580000000,
                 "부채총계": 5880000000, "유동부채": 4950000000, "비유동부채": 930000000,
                 "자본총계": -680000000},
    "재무_전기": {"매출액": 2100000000, "매출원가": 2080000000,
                 "매출총이익": 20000000, "판관비": 580000000, "영업이익": -560000000,
                 "당기순이익": -720000000,
                 "자산총계": 5850000000, "유동자산": 1030000000, "비유동자산": 4820000000,
                 "부채총계": 5510000000, "유동부채": 4320000000, "비유동부채": 1190000000,
                 "자본총계": 340000000},
    "감사의견": "부적정",
    "감사인":   "(주)정직회계법인",

    "이사회_일시": "2025년 4월 28일 오전 10시",
    "이사회_장소": "본사 회의실",
    "이사회_재적": "5", "이사회_출석": "4",
    "이사회_결의": "찬성 2, 반대 2 — 부결 후 재심의 보류",

    "지방세_체납": "체납있음 (총 125,640,000원)",
}


# ─── Document generators ─────────────────────────────────────
S = make_styles()


def _header(title, subtitle):
    return [
        Paragraph(title, S["title"]),
        Paragraph(subtitle, S["subtitle"]),
    ]


def gen_business_registration(d, out_path):
    """사업자등록증 — 국세청 양식 유사"""
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=22*mm, rightMargin=22*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    story = _header("사 업 자 등 록 증", "(법인사업자)")
    story.append(Paragraph(f"등 록 번 호 : <b>{d['사업자번호']}</b>", S["body"]))
    story.append(Spacer(1, 4*mm))
    rows = [
        ["법인명 (상호)",   d["상호"]],
        ["대 표 자",         d["대표자"]],
        ["법인등록번호",    d["법인번호"]],
        ["개업연월일",       d["개업일"]],
        ["사업장 소재지",   d["소재지"]],
        ["본점 소재지",      d["소재지"]],
        ["사업의 종류 - 업태",  d["업태"]],
        ["사업의 종류 - 종목",  d["종목"]],
        ["발 급 사 유",      "신규 발급"],
        ["사업자 단위과세 적용사업자 여부", "부"],
    ]
    story.append(kv_table(rows, col_widths=(48*mm, 112*mm)))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        "「부가가치세법」 제8조 및 같은 법 시행령 제11조 제1항·제2항, "
        "같은 법 시행규칙 제9조 제2항에 따라 위와 같이 등록하였음을 증명합니다.",
        S["small"]))
    story.append(Spacer(1, 12*mm))
    story.append(Paragraph("2025년 03월 11일", S["body"]))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph("<b>강 남 세 무 서 장</b>", S["h2"]))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph("(직인)", S["small"]))
    doc.build(story)


def gen_financial_statement(d, out_path):
    """재무제표 — 재무상태표 + 손익계산서 요약"""
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=18*mm, bottomMargin=18*mm)
    story = _header("재 무 제 표 ( 3 개 년 )", f"{d['상호']} · 결산기준일: {d['결산일']} · 감사의견: {d['감사의견']} ({d['감사인']})")
    story.append(Paragraph(f"(단위: 원)", S["small"]))
    story.append(Spacer(1, 3*mm))

    cur = d["재무_당기"]; prv = d["재무_전기"]
    # 재무상태표
    story.append(Paragraph("Ⅰ. 재 무 상 태 표", S["h2"]))
    rs_rows = [
        ["유동자산",    f"{int(cur['유동자산']):,}",    f"{int(prv['유동자산']):,}"],
        ["비유동자산",  f"{int(cur['비유동자산']):,}",  f"{int(prv['비유동자산']):,}"],
        ["  자 산  총 계",  f"{int(cur['자산총계']):,}",    f"{int(prv['자산총계']):,}"],
        ["유동부채",    f"{int(cur['유동부채']):,}",    f"{int(prv['유동부채']):,}"],
        ["비유동부채",  f"{int(cur['비유동부채']):,}",  f"{int(prv['비유동부채']):,}"],
        ["  부 채  총 계",  f"{int(cur['부채총계']):,}",    f"{int(prv['부채총계']):,}"],
        ["  자 본  총 계",  f"{int(cur['자본총계']):,}",    f"{int(prv['자본총계']):,}"],
    ]
    story.append(data_table(
        ["계 정 과 목", "당기 (제25기)", "전기 (제24기)"],
        rs_rows, col_widths=(70*mm, 50*mm, 50*mm), highlight_row=2))
    story.append(Spacer(1, 5*mm))

    # 손익계산서
    story.append(Paragraph("Ⅱ. 손 익 계 산 서", S["h2"]))
    is_rows = [
        ["매 출 액",          f"{int(cur['매출액']):,}",      f"{int(prv['매출액']):,}"],
        ["매 출 원 가",       f"{int(cur['매출원가']):,}",    f"{int(prv['매출원가']):,}"],
        ["매 출 총 이 익",   f"{int(cur['매출총이익']):,}",  f"{int(prv['매출총이익']):,}"],
        ["판매비와 관리비",  f"{int(cur['판관비']):,}",      f"{int(prv['판관비']):,}"],
        ["영 업 이 익",       f"{int(cur['영업이익']):,}",    f"{int(prv['영업이익']):,}"],
        ["당 기 순 이 익",   f"{int(cur['당기순이익']):,}",  f"{int(prv['당기순이익']):,}"],
    ]
    story.append(data_table(
        ["계 정 과 목", "당기 (제25기)", "전기 (제24기)"],
        is_rows, col_widths=(70*mm, 50*mm, 50*mm), highlight_row=4))
    story.append(Spacer(1, 5*mm))

    # 주요 재무비율
    부채 = int(cur["부채총계"]); 자본 = int(cur["자본총계"])
    매출 = int(cur["매출액"]); 영익 = int(cur["영업이익"])
    ratios = [
        ["부채비율 (부채총계 / 자본총계)",     f"{부채/자본*100:.1f}%"],
        ["영업이익률 (영업이익 / 매출액)",     f"{영익/매출*100:.2f}%" if 매출 else "—"],
        ["자기자본비율 (자본 / 자산)",         f"{자본/int(cur['자산총계'])*100:.1f}%"],
    ]
    story.append(Paragraph("Ⅲ. 주 요 재 무 비 율", S["h2"]))
    story.append(kv_table(ratios, col_widths=(80*mm, 80*mm)))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(f"본 재무제표는 일반적으로 인정되는 회계처리기준에 따라 작성되었으며, {d['감사인']}의 "
                           f"외부감사 결과 '<b>{d['감사의견']}</b>' 의견을 받았음을 증명합니다.", S["small"]))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("2025년 03월 30일", S["body"]))
    story.append(Paragraph(f"<b>{d['상호']}</b>", S["h2"]))
    story.append(Paragraph(f"대표이사 {d['대표자']} (인)", S["body"]))
    doc.build(story)


def gen_credit_agreement(d, out_path):
    """여신거래약정서(기업용) — 간략 양식"""
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=18*mm, bottomMargin=18*mm)
    story = _header("여 신 거 래 약 정 서", "( Ⅰ형 · 기업용 )")
    story.append(Paragraph("○○은행 주식회사 앞", S["body"]))
    story.append(Spacer(1, 4*mm))
    story.append(kv_table([
        ["접 수 번 호", "2025-0501-" + d["사업자번호"].replace("-", "")[:6]],
        ["작 성 일",    "2025년 5월 1일"],
    ], col_widths=(40*mm, 120*mm)))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph("1. 본인(기업) 정보", S["h2"]))
    story.append(kv_table([
        ["상      호", d["상호"]],
        ["대  표  자", d["대표자"]],
        ["사업자등록번호", d["사업자번호"]],
        ["주      소", d["소재지"]],
    ], col_widths=(40*mm, 120*mm)))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph("2. 거래조건", S["h2"]))
    story.append(kv_table([
        ["여 신 과 목",   d["여신_종류"]],
        ["거 래 구 분",   "☑ 개별거래  ☐ 한도거래  ☐ 종합한도"],
        ["여 신 한 도",   d["여신_금액"]],
        ["여 신 기 한",   d["여신_기간"]],
        ["자 금 용 도",   d["자금용도"]],
        ["담      보",    d["담보"]],
        ["이 자 율 구 분","☐ 여신선취수수료  ☑ COFIX 기준금리  ☐ MOR  ☐ 기타"],
        ["기 준 금 리",   "4.02% (COFIX 신규)"],
        ["가 산 금 리",   "1.35%"],
        ["최종적용금리",  "연 5.37%"],
        ["연 체 이 율",   "최종적용금리 + 3% (최대 15%)"],
        ["이자지급방법",  "매월 말일 후취"],
        ["상 환 방 법",   "☑ 만기일시상환  ☐ 분할상환"],
    ], col_widths=(40*mm, 120*mm)))
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("본 약정서 기재 사항 및 은행여신거래기본약관(기업용) 내용을 숙지하였으며, "
                           "이에 동의하고 본인의 인감으로 날인합니다.", S["small"]))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph(f"{d['상호']}", S["h2"]))
    story.append(Paragraph(f"대표이사 {d['대표자']} &nbsp;&nbsp;&nbsp;&nbsp; (법인인감)", S["body"]))
    doc.build(story)


def gen_board_resolution(d, out_path):
    """이사회결의서"""
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=22*mm, rightMargin=22*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    story = _header("이 사 회 결 의 서", f"{d['상호']}")
    story.append(kv_table([
        ["일     시", d["이사회_일시"]],
        ["장     소", d["이사회_장소"]],
        ["재적 이사 수", d["이사회_재적"] + "명"],
        ["출석 이사 수", d["이사회_출석"] + "명"],
    ], col_widths=(40*mm, 120*mm)))
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph("[의 안]", S["h2"]))
    story.append(Paragraph(
        f"{d['상호']}의 ○○은행 ○○지점으로부터의 <b>{d['여신_종류']}</b> 신규 여신 및 "
        f"이에 따른 담보제공의 건", S["body"]))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph("[의안 세부사항]", S["h2"]))
    story.append(kv_table([
        ["채 무 자",       d["상호"] + f" ({d['사업자번호']})"],
        ["여 신 과 목",    d["여신_종류"]],
        ["금      액",     d["여신_금액"]],
        ["차 입 기 간",    d["여신_기간"]],
        ["자 금 용 도",    d["자금용도"]],
        ["담  보  내  역", d["담보"]],
        ["근저당설정금액", d["근저당설정금액"]],
        ["기 타 사 항",    "본 안건에 명시되지 않은 사항은 대표이사가 차입은행과 협의하여 결정한다."],
    ], col_widths=(42*mm, 118*mm)))
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph("[결 의 결 과]", S["h2"]))
    story.append(Paragraph(d["이사회_결의"], S["body"]))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("2025년 " + d["이사회_일시"].split()[1] + " " + d["이사회_일시"].split()[2], S["body"]))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(f"<b>{d['상호']}</b>", S["h2"]))
    story.append(Spacer(1, 3*mm))
    # Director signature lines
    story.append(Paragraph(f"대 표 이 사  {d['대표자']}  &nbsp;&nbsp;&nbsp;&nbsp;(인)", S["body"]))
    story.append(Paragraph("사 내 이 사  홍 길 동  &nbsp;&nbsp;&nbsp;&nbsp;(인)", S["body"]))
    story.append(Paragraph("사 내 이 사  이 영 희  &nbsp;&nbsp;&nbsp;&nbsp;(인)", S["body"]))
    doc.build(story)


def gen_tax_certificate(d, out_path):
    """지방세 납세증명서"""
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=22*mm, rightMargin=22*mm,
                            topMargin=22*mm, bottomMargin=20*mm)
    story = _header("지 방 세 납 세 증 명 서", "(법인·개인사업자용)")
    story.append(kv_table([
        ["성명 / 상호",          d["상호"]],
        ["주민(법인)등록번호",   d["법인번호"]],
        ["주      소",            d["소재지"]],
        ["증 명 세 목",          "지방세 전 세목 (지방소득세·주민세·재산세 등)"],
        ["과 세 기 간",          "전 기간"],
    ], col_widths=(42*mm, 118*mm)))
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph("[증 명 내 용]", S["h2"]))

    if d["지방세_체납"] == "체납없음":
        story.append(Paragraph("「지방세징수법」제5조에 따른 <b>체납액이 없음</b>을 증명합니다.", S["body"]))
        story.append(Spacer(1, 4*mm))
        story.append(kv_table([
            ["체 납 여 부", "체납없음"],
            ["체 납 액",    "0 원"],
        ], col_widths=(42*mm, 118*mm)))
    else:
        story.append(Paragraph(f"「지방세징수법」제5조에 따라 아래와 같이 <b>체납액이 있음</b>을 증명합니다.", S["body"]))
        story.append(Spacer(1, 3*mm))
        rows = [[세목, 기간, f"{int(액.replace(',','')):,}"] for 세목, 기간, 액 in d["체납_상세"]]
        total = sum(int(액.replace(',', '')) for _, _, 액 in d["체납_상세"])
        rows.append(["합 계", "", f"{total:,}"])
        story.append(data_table(
            ["세  목", "과세연월", "체납액 (원)"],
            rows, col_widths=(60*mm, 50*mm, 50*mm), highlight_row=len(rows)-1))

    story.append(Spacer(1, 6*mm))
    story.append(kv_table([
        ["사 용 용 도", "여신 신청용"],
        ["유 효 기 간", "발급일로부터 30일"],
        ["발 급 일 자", "2025년 4월 25일"],
    ], col_widths=(42*mm, 118*mm)))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("<b>서 울 특 별 시 강 남 구 청 장</b>", S["h2"]))
    story.append(Paragraph("(직인)", S["small"]))
    doc.build(story)


# ─── Run ─────────────────────────────────────────────────────
def build_scenario(data, outdir, label, include_docs=None):
    """include_docs: subset of {'biz','fin','cred','board','tax'} to generate.
       None = all 5 (default). Omitting some simulates 필수서류 누락."""
    print(f"\n=== Building {label} scenario → {outdir} ===")
    sel = include_docs or {'biz', 'fin', 'cred', 'board', 'tax'}
    if 'biz'   in sel: gen_business_registration(data, outdir / f"01_사업자등록증_{data['상호']}.pdf")
    if 'fin'   in sel: gen_financial_statement  (data, outdir / f"02_재무제표_{data['상호']}.pdf")
    if 'cred'  in sel: gen_credit_agreement     (data, outdir / f"03_여신거래약정서_{data['상호']}.pdf")
    if 'board' in sel: gen_board_resolution     (data, outdir / f"04_이사회결의서_{data['상호']}.pdf")
    if 'tax'   in sel: gen_tax_certificate      (data, outdir / f"05_지방세납세증명_{data['상호']}.pdf")
    for p in sorted(outdir.glob("*.pdf")):
        print(f"  • {p.name}  ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    build_scenario(BEST,   OUT_BEST,   "BEST (승인 예상)")
    build_scenario(FAIL,   OUT_FAIL,   "FAIL (조건부 승인 예상)")
    # REJECT: 이사회결의서·지방세납세증명 누락 (60% 완비도 → 조건부 or 거절 유도)
    build_scenario(REJECT, OUT_REJECT, "REJECT (승인 거절 예상)",
                   include_docs={'biz', 'fin', 'cred'})
    print("\n✓ All demo PDFs generated.")
