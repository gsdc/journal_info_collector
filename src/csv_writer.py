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
    "JCI percentile",
    "DOI",
    "Auth",
    "Ack",
]


def write_report_csv(rows, path, encoding="utf-8-sig"):
    """rows: COLUMNS의 일부/전체 키를 갖는 dict의 리스트.
    'No.'는 항상 여기서 순서대로 다시 매긴다(입력값은 무시)."""
    with open(path, "w", newline="", encoding=encoding) as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for i, row in enumerate(rows, 1):
            out = {col: row.get(col, "") for col in COLUMNS}
            out["No."] = i
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
