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


def write_report_csv(rows, path, encoding="utf-8-sig"):
    """rows: COLUMNS의 일부/전체 키를 갖는 dict의 리스트.
    'No.', '유발근거', 'Funder 수정요청'은 항상 여기서 다시 계산하고, '비고'에는 ISSN 없음 표시를 덧붙인다."""
    with open(path, "w", newline="", encoding=encoding) as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for i, row in enumerate(rows, 1):
            out = {col: row.get(col, "") for col in COLUMNS}
            out["No."] = i
            out["유발근거"] = evidence_summary(out)
            out["Funder 수정요청"] = funder_fix(out)
            out["SCIE"] = _scie(out) or out.get("SCIE", "")
            out["비고"] = _notes(out)
            writer.writerow(out)


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
