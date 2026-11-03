"""커뮤니티에 전달할 funder 정보 수정 요청 목록(엑셀).

본문 사사가 유발 근거로 우선하므로 Funder가 빠져도 유발 판정은 바뀌지 않지만,
출판사 funder 메타데이터에 KISTI가 빠지거나 잘못 등록된 논문은 커뮤니티(컬래버레이션)를
통해 출판사에 고쳐 달라고 해야 한다. 판정 대상 커뮤니티의 출판 논문만 대상으로,
요약 탭 + 커뮤니티별 탭으로 낸다.
"""
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from . import config
from .xlsx_report import FONT, _HEADER_FILL, sheet_name

KISTI_ID = "10.13039/501100003708"
KIST_ID = "10.13039/501100003693"

# (요청 유형, 요청 내용) — 위에서부터 판정
REQUEST_TYPES = {
    "KIST오태깅": ("KIST ID 오태깅", f"KIST ID({KIST_ID})로 잘못 등록 → KISTI ID({KISTI_ID})로 수정"),
    "이름만": ("ID 누락(이름만)", f"KISTI 이름만 있고 funder ID 없음 → KISTI ID({KISTI_ID}) 추가"),
    "KIST확인": ("KIST ID 확인 필요", f"KIST ID({KIST_ID})가 등록됨 — KISTI 오태깅인지 확인 후 수정"),
    "누락": ("KISTI 누락", f"본문 사사엔 KISTI가 있으나 funder 목록에 없음 → KISTI ID({KISTI_ID}) 추가"),
    "미등록": ("funder 정보 미등록", f"출판사가 funder 정보를 등록하지 않음 → KISTI ID({KISTI_ID}) 포함 등록"),
}
TYPE_ORDER = ["KIST오태깅", "이름만", "KIST확인", "누락", "미등록"]

COLUMNS = ["No.", "요청 유형", "요청 내용", "title", "journal", "Vol", "Page", "date", "DOI",
           "현재 funder 등록 상태", "본문 사사 KISTI 문구"]
_WIDTHS = {"No.": 5, "요청 유형": 16, "요청 내용": 48, "title": 60, "journal": 18, "Vol": 6,
           "Page": 9, "date": 11, "DOI": 32, "현재 funder 등록 상태": 48, "본문 사사 KISTI 문구": 70}


def request_type(c):
    """candidate 하나의 funder 수정 요청 유형 키(REQUEST_TYPES), 요청할 게 없으면 None."""
    status = c.get("ack_funder_status")
    if status in ("KIST오태깅", "이름만", "KIST확인"):
        return status
    if status == "Y" or c.get("ack_text_status") != "Y":
        return None
    return "누락" if status == "N" else "미등록"


def collect_requests(candidates):
    """판정 대상 커뮤니티 출판 논문 중 funder 수정 요청 대상. [(candidate, 유형 키), ...]"""
    out = []
    for c in candidates:
        if c.get("publication_status") != "published" or c.get("reference_only"):
            continue
        key = request_type(c)
        if key:
            out.append((c, key))
    out.sort(key=lambda x: (TYPE_ORDER.index(x[1]), x[0].get("date") or ""))
    return out


def _row(c, key, number):
    label, action = REQUEST_TYPES[key]
    evidence = c.get("ack_text_evidence") or []
    return {
        "No.": number,
        "요청 유형": label,
        "요청 내용": action,
        "title": c.get("inspire_title") or c.get("raw_title") or "",
        "journal": c.get("journal") or "",
        "Vol": c.get("volume") or "",
        "Page": c.get("page") or "",
        "date": c.get("date") or "",
        "DOI": c.get("doi") or "",
        "현재 funder 등록 상태": c.get("ack_funder_note") or "",
        "본문 사사 KISTI 문구": evidence[0] if evidence else "",
    }


def _write_sheet(ws, rows):
    bold, normal = Font(name=FONT, bold=True), Font(name=FONT)
    for i, col in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=i, value=col)
        cell.font, cell.fill = bold, _HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = _WIDTHS.get(col, 14)
    for r, row in enumerate(rows, 2):
        for i, col in enumerate(COLUMNS, 1):
            cell = ws.cell(row=r, column=i, value=row.get(col, ""))
            cell.font = normal
            cell.alignment = Alignment(vertical="top", wrap_text=col in ("요청 내용", "본문 사사 KISTI 문구"))
            if col == "DOI" and row.get("DOI"):
                cell.hyperlink = f"https://doi.org/{row['DOI']}"
    ws.freeze_panes = "D2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{len(rows) + 1}"


def build_workbook(candidates, communities, year):
    requests = collect_requests(candidates)
    wb = Workbook()
    summary = wb.active
    summary.title = "요약"
    summary["A1"] = f"{year}년 KISTI funder 정보 수정 요청 (커뮤니티 전달용)"
    summary["A1"].font = Font(name=FONT, bold=True, size=13)
    summary["A2"] = (
        "출판사 funder 메타데이터에 KISTI가 빠지거나 잘못 등록된 논문. "
        f"KISTI funder ID: {KISTI_ID} (KIST {KIST_ID}는 별개 기관)"
    )
    summary["A2"].font = Font(name=FONT)
    header = ["커뮤니티"] + [REQUEST_TYPES[k][0] for k in TYPE_ORDER] + ["합계"]
    for i, h in enumerate(header, 1):
        cell = summary.cell(row=4, column=i, value=h)
        cell.font, cell.fill = Font(name=FONT, bold=True), _HEADER_FILL
        summary.column_dimensions[get_column_letter(i)].width = 18 if i > 1 else 14
    r = 5
    for cfg in communities:
        if cfg.get("reference_only"):
            continue
        mine = [(c, k) for c, k in requests if c["community"] == cfg["name"]]
        counts = [sum(1 for _, k in mine if k == t) for t in TYPE_ORDER]
        for i, v in enumerate([cfg["name"]] + counts + [len(mine)], 1):
            summary.cell(row=r, column=i, value=v).font = Font(name=FONT)
        r += 1
        if mine:
            _write_sheet(wb.create_sheet(sheet_name(cfg)), [_row(c, k, n) for n, (c, k) in enumerate(mine, 1)])
    return wb, len(requests)


def write_funder_requests(year, candidates, settings=None):
    settings = settings or config.load_settings()
    wb, n = build_workbook(candidates, config.load_communities(), year)
    path = settings["output"].get("funder_requests_path", "output/funder_requests_{year}.xlsx").format(year=year)
    wb.save(path)
    return path, n
