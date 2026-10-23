"""최종 결과를 엑셀 파일 하나로 낸다: 요약 탭 + 커뮤니티별 탭 + 출판 미확인 탭.

- 판정 대상 커뮤니티는 이름 그대로, 참고용(reference_only) 커뮤니티는 "(참고) "를 붙인 탭.
- 행 구성은 CSV와 같은 규칙(csv_writer.candidate_to_row / finalize_row)을 쓴다.
- 요약 탭의 숫자는 각 탭을 세는 수식이라, 탭의 행을 고치면 요약도 따라 바뀐다.
"""
import json
import re
from datetime import date
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import config
from .csv_writer import COLUMNS, candidate_to_row, finalize_row
from .reconcile import collab_query

FONT = "Arial"
_HEADER_FILL = PatternFill("solid", start_color="D9D9D9")
_REF_TAB_COLOR = "BFBFBF"
# 행 색(위에서부터 우선):
#   증빙 논문(유발 논문 Y) / 증빙 불가·공식 페이지에 출판으로 입력 /
#   증빙 불가·INSPIRE나 OpenAlex로 출판 확인 / 나머지
_FILL_EVIDENCE = PatternFill("solid", start_color="E2EFDA")   # 옅은 녹색
_FILL_OFFICIAL = PatternFill("solid", start_color="DDEBF7")   # 옅은 파란색
_FILL_IO = PatternFill("solid", start_color="FFF2CC")         # 노란색
_FILL_OTHER = PatternFill("solid", start_color="EDEDED")      # 옅은 회색
# 결격(SCIE 아님·ISSN 없음)은 행 색이 아니라 SCIE 칸에 표시
_FILL_DISQ = PatternFill("solid", start_color="F8CBAD")
_FONT_DISQ_COLOR = "C00000"
_HEADER_ROW = 3  # 1행: 출판 정보 주소, 2행: 색 범례, 3행: 표 머리글
_WIDTHS = {
    "No.": 5, "title": 60, "journal": 18, "ISSN": 11, "Vol": 6, "Page": 9, "coll.": 9,
    "date": 11, "IF": 6, "JIF 상위%": 9, "SCIE": 6, "DOI": 32, "Auth": 8, "Ack": 7,
    "Funder": 10, "유발논문": 8, "유발근거": 22, "Funder 수정요청": 30, "출처": 46, "비고": 36,
}
_UNPUBLISHED_COLUMNS = ["No.", "coll.", "title", "arXiv", "커뮤니티 페이지 상태", "출처", "비고"]


def sheet_name(community_cfg):
    """엑셀 탭 이름: 탭 이름에 못 쓰는 문자(/ \\ ? * [ ] :)는 '-'로 바꾸고 31자로 자른다."""
    name = re.sub(r"[/\\?*\[\]:]", "-", community_cfg["name"])
    if community_cfg.get("reference_only"):
        name = f"(참고) {name}"
    return name[:31]


def source_urls(community_cfg):
    """탭 맨 위에 넣을 커뮤니티 출판 정보 주소. 공식 페이지가 있으면 그 주소,
    없으면 실제로 쓰는 INSPIRE·OpenAlex 검색을 웹에서 볼 수 있는 링크로."""
    downloader = community_cfg.get("downloader")
    if downloader == "inspire_collab":
        return [("INSPIRE 검색", "https://inspirehep.net/literature?q=" + quote(collab_query(community_cfg)))]
    if downloader == "openalex_search":
        return [
            ("OpenAlex 검색", "https://api.openalex.org/works?search=" + quote(q["search"]))
            for q in community_cfg.get("openalex_queries", [])
        ]
    url = community_cfg["url"]
    if downloader == "alice_html":
        base = url.rstrip("/").rsplit("/", 1)[0]
        return [("출판", url), ("게재확정", f"{base}/accepted"), ("투고", f"{base}/submitted")]
    return [("공식 목록", url)]


def _row_fill(candidate, row):
    """행 색 4가지(위에서부터 우선).
    녹색: 증빙 논문(유발 논문 Y — 출판은 3곳 중 어디서든 확인된 상태)
    파란색: 증빙 불가지만 커뮤니티 공식 페이지에 출판으로 입력
    노란색: 증빙 불가지만 INSPIRE나 OpenAlex로 출판 확인
    회색: 나머지
    결격(SCIE 아님·ISSN 없음)은 SCIE 칸 색으로 따로 표시한다."""
    label = candidate.get("source_label") or ""
    if row.get("유발논문") == "Y":
        return _FILL_EVIDENCE
    if "커뮤니티:출판" in label:
        return _FILL_OFFICIAL
    if "INSPIRE:O" in label or "OpenAlex:O" in label:
        return _FILL_IO
    return _FILL_OTHER


def _write_header_block(ws, community_cfg):
    ws["A1"] = "출판 정보"
    ws["A1"].font = Font(name=FONT, bold=True)
    col = 2
    for label, url in source_urls(community_cfg):
        cell = ws.cell(row=1, column=col, value=f"{label}: {url}")
        cell.hyperlink = url
        cell.font = Font(name=FONT, color="0563C1", underline="single")
        col += 1
    legend = [
        ("범례", None),
        ("증빙 논문(유발 논문 Y)", _FILL_EVIDENCE),
        ("증빙 불가 · 공식 페이지에 출판으로 입력", _FILL_OFFICIAL),
        ("증빙 불가 · INSPIRE/OpenAlex로 출판 확인", _FILL_IO),
        ("나머지", _FILL_OTHER),
        ("SCIE 칸 붉은색 = 결격(SCIE 아님·ISSN 없음)", _FILL_DISQ),
    ]
    for i, (text, fill) in enumerate(legend, 1):
        cell = ws.cell(row=2, column=i, value=text)
        cell.font = Font(name=FONT, bold=fill is None)
        if fill:
            cell.fill = fill


def _write_table(ws, columns, rows, fills=None):
    bold = Font(name=FONT, bold=True)
    normal = Font(name=FONT)
    for i, col in enumerate(columns, 1):
        ws.cell(row=_HEADER_ROW, column=i, value=col)
    for r, row in enumerate(rows, _HEADER_ROW + 1):
        for i, col in enumerate(columns, 1):
            ws.cell(row=r, column=i, value=row.get(col, ""))
    for cell in ws[_HEADER_ROW]:
        cell.font = bold
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for offset, r in enumerate(ws.iter_rows(min_row=_HEADER_ROW + 1, max_col=len(columns))):
        fill = fills[offset] if fills and offset < len(fills) else None
        for cell in r:
            cell.font = normal
            if fill:
                cell.fill = fill
    for i, col in enumerate(columns, 1):
        ws.column_dimensions[get_column_letter(i)].width = _WIDTHS.get(col, 14)
    ws.freeze_panes = f"C{_HEADER_ROW + 1}"
    if rows:
        ws.auto_filter.ref = f"A{_HEADER_ROW}:{get_column_letter(len(columns))}{len(rows) + _HEADER_ROW}"


def _to_number(value):
    """IF·상위%는 숫자로 넣어야 엑셀에서 정렬·계산이 된다."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def build_workbook(candidates, communities, year):
    wb = Workbook()
    summary = wb.active
    summary.title = "요약"

    tabs = []  # (탭 이름, 판정 대상 여부, 행 수)
    for cfg in communities:
        pubs = [c for c in candidates if c["community"] == cfg["name"] and c.get("publication_status") == "published"]
        pubs.sort(key=lambda c: c.get("date") or "", reverse=True)
        rows, fills = [], []
        for i, c in enumerate(pubs, 1):
            row = finalize_row(candidate_to_row(c), i)
            fills.append(_row_fill(c, row))
            row["IF"] = _to_number(row["IF"])
            row["JIF 상위%"] = _to_number(row["JIF 상위%"])
            rows.append(row)
        name = sheet_name(cfg)
        ws = wb.create_sheet(name)
        if cfg.get("reference_only"):
            ws.sheet_properties.tabColor = _REF_TAB_COLOR
        _write_header_block(ws, cfg)
        _write_table(ws, COLUMNS, rows, fills)
        scie_col = COLUMNS.index("SCIE") + 1
        for r in range(_HEADER_ROW + 1, len(rows) + _HEADER_ROW + 1):
            cell = ws.cell(row=r, column=scie_col)
            if cell.value == "N":
                cell.fill = _FILL_DISQ
                cell.font = Font(name=FONT, bold=True, color=_FONT_DISQ_COLOR)
        doi_col = COLUMNS.index("DOI") + 1
        for r in range(_HEADER_ROW + 1, len(rows) + _HEADER_ROW + 1):
            cell = ws.cell(row=r, column=doi_col)
            if cell.value:
                cell.hyperlink = f"https://doi.org/{cell.value}"
                cell.font = Font(name=FONT, color="0563C1", underline="single")
        has_official = cfg.get("downloader") not in ("inspire_collab", "openalex_search")
        tabs.append((name, not cfg.get("reference_only"), has_official))

    unpublished = [c for c in candidates if c.get("publication_status") != "published"]
    unpublished.sort(key=lambda c: (c["community"], c.get("raw_title") or ""))
    ws = wb.create_sheet("출판 미확인")
    _write_table(
        ws,
        _UNPUBLISHED_COLUMNS,
        [
            {
                "No.": i,
                "coll.": c["community"],
                "title": c.get("raw_title") or "",
                "arXiv": c.get("arxiv_id") or "",
                "커뮤니티 페이지 상태": c.get("raw_status") or "",
                "출처": c.get("source_label") or "",
                "비고": c.get("note") or "",
            }
            for i, c in enumerate(unpublished, 1)
        ],
    )

    _write_summary(summary, tabs, year)
    # 요약 탭은 수식이라, 파일을 열 때 엑셀이 전부 다시 계산하게 한다(미리 계산된 값이 없음).
    wb.calculation.fullCalcOnLoad = True
    return wb


def _write_summary(ws, tabs, year):
    """커뮤니티별 집계. 숫자는 각 탭을 세는 수식이다."""
    bold = Font(name=FONT, bold=True)
    normal = Font(name=FONT)
    col = {c: get_column_letter(COLUMNS.index(c) + 1) for c in COLUMNS}

    ws["A1"] = f"{year}년 KISTI 유발 논문 조사 결과"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws["A2"] = f"작성일 {date.today().isoformat()} · journal_info_collector (python main.py report)"
    ws["A2"].font = normal

    headers = ["커뮤니티", "구분", "출판 논문", "공식 페이지 출판", "기타 출처 확인 출판", "유발 논문", "증빙 불가",
               "검증", "SCIE 논문", "저자 Y", "사사 Y", "Funder ID Y", "KIST 오태깅", "Funder 이름만"]
    ws.append([])
    ws.append(headers)
    header_row = ws.max_row
    for cell in ws[header_row]:
        cell.font = bold
        cell.fill = _HEADER_FILL

    def rng(sheet, c):
        return f"'{sheet}'!${col[c]}${_HEADER_ROW + 1}:${col[c]}$2000"

    first = header_row + 1
    for name, judged, has_official in tabs:
        r = ws.max_row + 1  # 이 행 번호(검증 수식에서 같은 행 칸을 참조)
        ws.append([
            name,
            "판정 대상" if judged else "참고용",
            f"=COUNTA({rng(name, 'DOI')})",
            # 커뮤니티 공식 출판 목록에 '출판'으로 표시된 논문(출처 칸 "커뮤니티:출판"). 공식
            # 페이지가 없는 커뮤니티(INSPIRE·OpenAlex 검색이 목록)는 "-"
            f'=COUNTIF({rng(name, "출처")},"커뮤니티:출판*")' if has_official else "-",
            # 공식 페이지에서는 출판이 아닌데 INSPIRE·OpenAlex로 출판 확인된 논문(공식 페이지가
            # 없으면 전부 여기). 출판 논문에서 빼지 않고 따로 세서 아래 검증이 의미 있게 한다.
            (f'=COUNTA({rng(name, "DOI")})-COUNTIF({rng(name, "출처")},"커뮤니티:출판*")'
             if has_official else f"=COUNTA({rng(name, 'DOI')})"),
            f'=COUNTIF({rng(name, "유발논문")},"Y")',
            f'=COUNTIF({rng(name, "유발논문")},"N")',
            # (공식 페이지 출판 + 기타 출처 확인) = (유발 논문 + 증빙 불가) 인지
            f'=IF(N(D{r})+E{r}=F{r}+G{r},"일치","불일치")',
            f'=COUNTIF({rng(name, "SCIE")},"Y")',
            f'=COUNTIF({rng(name, "Auth")},"Y")',
            f'=COUNTIF({rng(name, "Ack")},"Y")',
            f'=COUNTIF({rng(name, "Funder")},"Y")',
            f'=COUNTIF({rng(name, "Funder")},"KIST오태깅")',
            f'=COUNTIF({rng(name, "Funder")},"이름만")',
        ])
    last = ws.max_row
    ws.append([
        "판정 대상 합계", "",
        *[
            (f'=IF(N(D{last + 1})+E{last + 1}=F{last + 1}+G{last + 1},"일치","불일치")' if headers[i - 1] == "검증"
             else f'=SUMIF($B${first}:$B${last},"판정 대상",{get_column_letter(i)}{first}:{get_column_letter(i)}{last})')
            for i in range(3, len(headers) + 1)
        ],
    ])
    total_row = ws.max_row
    for r in ws.iter_rows(min_row=first, max_row=total_row):
        for cell in r:
            cell.font = normal
    for cell in ws[total_row]:
        cell.font = bold

    notes = [
        "판정 기준",
        "· 유발 논문: SCIE 저널 논문 중 저자(GSDC 담당자)·사사 원문(KISTI/GSDC)·Funder(KISTI ID, KIST 오태깅, ID 없이 기관명만 등록 포함) 중 하나라도 해당.",
        "· SCIE·IF·JIF 상위%: config/journals_<연도>.tsv(JCR에서 정리)를 ISSN으로 매칭. ISSN 없음·SCIE 아님은 유발 논문 N, 표에 없는 저널은 보류.",
        "· ISSN은 전자판 출판이어도 인쇄판 우선. 게재일은 CrossRef·INSPIRE 중 이른 날짜(온라인 게재일).",
        "· 출처: 커뮤니티 공식 목록 / INSPIRE / OpenAlex(KISTI 검색)에서 확인 여부.",
        "· 출판 논문 = 공식 페이지 출판 + 기타 출처 확인 출판(공식 페이지엔 출판 아님, INSPIRE·OpenAlex로 확인) = 유발 논문 + 증빙 불가. '검증' 열은 두 합이 같은지 확인한다.",
        "· (참고) 탭: 논문 수 파악용 커뮤니티 — 유발 논문 판정 대상이 아니며 합계에서 제외. 공식 페이지가 없어 '공식 페이지 출판'은 '-'.",
        "· 출판 미확인 탭: 커뮤니티 목록에는 있으나 아직 출판이 확인되지 않은 논문(다음 조사에서 재확인).",
    ]
    ws.append([])
    for text in notes:
        ws.append([text])
        ws.cell(row=ws.max_row, column=1).font = bold if text == "판정 기준" else normal

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 10
    for i in range(3, len(headers) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 12


def write_xlsx_report(year, candidates=None, path=None, settings=None):
    settings = settings or config.load_settings()
    if candidates is None:
        with open(settings["output"]["candidates_path"], encoding="utf-8") as f:
            candidates = json.load(f)
    communities = config.load_communities()
    wb = build_workbook(candidates, communities, year)
    path = path or settings["output"].get("xlsx_path", "output/journal_report_{year}.xlsx").format(year=year)
    wb.save(path)
    return path
