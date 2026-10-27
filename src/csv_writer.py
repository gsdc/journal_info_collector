import csv
import json
import sys

COLUMNS = [
    "No.",
    "title",
    "journal",
    "ISSN",
    "Vol",
    "Page",
    "coll.",
    "date",
    "IF",
    "JIF 상위%",
    "SCIE",
    "DOI",
    "Auth",
    "Ack",
    "Funder",
    "유발논문",
    "유발근거",
    "Funder 수정요청",
    "출처",
    "비고",
]

# 유발근거 컬럼에 넣을 (컬럼, 값) -> 이름. 여기 없는 값(Auth "확인필요" 등)은
# 근거로 세지 않고 해당 컬럼에서 그대로 보이게 둔다.
_EVIDENCE_LABELS = [
    ("Auth", "Y", "저자"),
    ("Ack", "Y", "사사"),
    ("Funder", "Y", "Funder ID"),
    # 출판사가 KISTI를 KIST DOI로 잘못 등록한 경우 — KISTI를 funder로 밝힌 것은
    # 맞으므로 근거로 세되, 수정 요청 대상이라 구분해서 표기한다.
    ("Funder", "KIST오태깅", "Funder ID(KIST 오태깅)"),
    # CrossRef funder에 KISTI 기관명은 있는데 ID가 빠진 경우 — KISTI 지원은 맞으므로
    # 근거로 세되, ID 추가 요청 대상이라 구분해서 표기한다.
    ("Funder", "이름만", "Funder 기관명(ID 누락)"),
]

# Funder 값별로 출판사/컬래버레이션에 수정 요청이 필요할 수 있는 경우
_FUNDER_FIX = {
    "KIST오태깅": "KIST ID(10.13039/501100003693)로 잘못 등록 → KISTI ID로 수정 요청",
    "KIST확인": "KIST ID 등록됨 — KISTI 오태깅인지 확인 필요",
    "이름만": "KISTI funder ID 누락(이름만 등록) → ID 추가 요청",
}


NO_ISSN_NOTE = "ISSN 없음 — SCIE 아님(유발논문 N)"
NOT_SCIE_NOTE = "SCIE 아님(유발논문 N)"
SCIE_UNKNOWN_NOTE = "JCR 표에 없는 저널 — SCIE 확인 필요(유발논문 보류)"


def _scie(row):
    """ISSN이 없으면 SCIE 아님. SCIE 값이 비어 있으면(예전 형식 행) 판단하지 않는다."""
    if not (row.get("ISSN") or "").strip():
        return "N"
    return (row.get("SCIE") or "").strip()


def evidence_summary(row):
    # SCIE 저널 논문만 유발논문으로 센다. SCIE가 아니거나(ISSN 없음 포함) 아직 확인이
    # 안 된 저널이면 근거 컬럼과 무관하게 유발근거를 비운다.
    if _scie(row) in ("N", "미확인"):
        return ""
    return ", ".join(label for col, val, label in _EVIDENCE_LABELS if row.get(col) == val)


def _notes(row):
    notes = [n for n in (row.get("비고") or "").split(" / ") if n]
    scie = _scie(row)
    if not (row.get("ISSN") or "").strip():
        extra = NO_ISSN_NOTE
    elif scie == "N":
        extra = NOT_SCIE_NOTE
    elif scie == "미확인":
        extra = SCIE_UNKNOWN_NOTE
    else:
        extra = None
    if extra and extra not in notes:
        notes.append(extra)
    return " / ".join(notes)


def funder_fix(row):
    return _FUNDER_FIX.get(row.get("Funder"), "")


def finalize_row(row, number=None):
    """COLUMNS 순서의 행을 만들고 자동 계산 컬럼(유발근거·Funder 수정요청·SCIE·비고)을 채운다.
    CSV와 엑셀 출력이 같은 규칙을 쓰도록 한곳에 둔다."""
    out = {col: row.get(col, "") for col in COLUMNS}
    if number is not None:
        out["No."] = number
    out["유발근거"] = evidence_summary(out)
    # 판정 결과: SCIE 논문이면서 근거가 하나라도 있으면 유발 논문
    out["유발논문"] = "Y" if out["유발근거"] else "N"
    out["Funder 수정요청"] = funder_fix(out)
    out["SCIE"] = _scie(out) or out.get("SCIE", "")
    out["비고"] = _notes(out)
    return out


_FUNDER_VALUES = ("Y", "KIST오태깅", "KIST확인", "이름만")


_paper_notes = None


def _manual_note(doi):
    """config/paper_notes.yaml의 논문별 수동 메모."""
    global _paper_notes
    if _paper_notes is None:
        from . import config

        _paper_notes = config.load_paper_notes()
    return _paper_notes.get((doi or "").lower(), "")


_review_overrides = None


def _ack_override(doi):
    """config/review_overrides.yaml에서 /build-report 재확인으로 고친 Ack 판정."""
    global _review_overrides
    if _review_overrides is None:
        from . import config

        _review_overrides = config.load_review_overrides()
    return _review_overrides.get((doi or "").lower())


def candidate_to_row(c):
    """candidates.json의 출판 논문 하나를 보고서 행(dict)으로 옮긴다(자동 계산 컬럼 제외).
    Ack는 사사 원문 판정(재확인으로 고친 값이 있으면 그 값), Funder는 funder ID 판정."""
    override = _ack_override(c.get("doi"))
    ack = c.get("ack_text_status") or ""
    override_note = ""
    if override and override.get("ack"):
        override_note = f"Ack 재확인 {ack or '-'}→{override['ack']}: {override.get('reason', '')}".rstrip(": ")
        ack = override["ack"]
    return {
        "title": c.get("inspire_title") or c.get("raw_title") or "",
        "journal": c.get("journal") or "",
        "ISSN": c.get("issn") or "",
        "Vol": c.get("volume") or "",
        "Page": c.get("page") or "",
        "coll.": c.get("community") or "",
        "date": c.get("date") or "",
        "IF": c.get("jif") or "",
        "JIF 상위%": c.get("jif_top_pct") or "",
        "SCIE": c.get("scie") or "",
        "DOI": c.get("doi") or "",
        "Auth": c.get("auth_status") or "",
        "Ack": ack,
        "Funder": c.get("ack_funder_status") if c.get("ack_funder_status") in _FUNDER_VALUES else "N",
        "출처": c.get("source_label") or "",
        "비고": " / ".join(
            n for n in (
                f"게재 예정({c.get('date')})" if c.get("pub_date_future") else "",
                _manual_note(c.get("doi")),
                override_note,
            ) if n
        ),
    }


def write_csv_reports(candidates, settings):
    """candidates.json의 출판 논문으로 최종 CSV 2개를 쓴다: 판정 대상 커뮤니티
    (output.csv_path)와 참고용 커뮤니티(output.reference_csv_path). 최신 논문 먼저.
    반환: [(경로, 행 수), ...]"""
    published = [c for c in candidates if c.get("publication_status") == "published"]
    published.sort(key=lambda c: c.get("date") or "", reverse=True)
    out = []
    for key, reference in (("csv_path", False), ("reference_csv_path", True)):
        rows = [candidate_to_row(c) for c in published if bool(c.get("reference_only")) == reference]
        path = settings["output"][key]
        write_report_csv(rows, path)
        out.append((path, len(rows)))
    return out


def write_report_csv(rows, path, encoding="utf-8-sig"):
    """rows: COLUMNS의 일부/전체 키를 갖는 dict의 리스트.
    'No.', '유발논문', '유발근거', 'Funder 수정요청'은 항상 여기서 다시 계산하고, '비고'에는 ISSN 없음 표시를 덧붙인다."""
    with open(path, "w", newline="", encoding=encoding) as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for i, row in enumerate(rows, 1):
            writer.writerow(finalize_row(row, i))


def _main():
    if len(sys.argv) != 3:
        print("usage: python -m src.csv_writer <rows.json> <output.csv>", file=sys.stderr)
        sys.exit(1)
    rows_path, out_path = sys.argv[1], sys.argv[2]
    with open(rows_path, encoding="utf-8") as f:
        rows = json.load(f)
    write_report_csv(rows, out_path)
    print(f"wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    _main()
