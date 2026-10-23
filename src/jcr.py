"""JCR(Journal Citation Reports) 저널 표 읽기 — SCIE 여부, JIF, JIF 기준 카테고리 상위%.

JCR은 Clarivate 유료 데이터라 자동으로 받지 않고, 사람이 정리한
config/journals_<연도>.tsv(탭 구분)를 읽는다. 필요한 헤더:
  ISSN, IF, Top %   (선택: eISSN, SCIE(Y/N), 저널명, JCR 정식명, 조사일자)
SCIE 칸은 헤더가 "SCIE"로 시작하면 된다("SCIE", "SCIE(Y/N)" 등).
SCIE 칸이 없으면 표에 있는 저널은 SCIE로 본다. JCR에는 ESCI 저널도 JIF와 함께
실리므로, 표에 ESCI 저널이 섞일 수 있으면 SCIE 칸(Y/N)을 넣어 구분한다.
"""
import csv
import os


def jcr_path(year):
    return f"config/journals_{year}.tsv"


def load_jcr_table(year):
    """{ISSN: entry}. 파일이 없으면 빈 dict."""
    path = jcr_path(year)
    if not os.path.exists(path):
        return {}
    table = {}
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader((line for line in f if not line.startswith("#")), delimiter="\t")
        for row in reader:
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            scie_col = next((k for k in row if k.upper().startswith("SCIE")), None)
            entry = {
                "issn": row.get("ISSN", ""),
                "eissn": row.get("eISSN", ""),
                "scie": ((row.get(scie_col) if scie_col else "") or "Y").upper(),
                "jif": row.get("IF", ""),
                "top_pct": row.get("Top %", ""),
                "jcr_title": row.get("JCR 정식명", ""),
            }
            for key in (entry["issn"], entry["eissn"]):
                if key:
                    table[key] = entry
    return table


def lookup(table, issn, eissn, issn_pairs):
    """논문의 (인쇄판 우선 ISSN, 전자판 ISSN)으로 표를 찾는다. 표에 한쪽 ISSN만
    적혀 있어도 찾을 수 있게 journal_issn_fallback 쌍도 함께 본다."""
    keys = [issn, eissn]
    for k in (issn, eissn):
        keys += list(issn_pairs.get(k, ()))
    for k in keys:
        if k and k in table:
            return table[k]
    return None


def scie_fields(table, issn, eissn, issn_pairs):
    """반환: (scie, jif, top_pct). scie는 "Y"/"N"(ISSN 없음 또는 표에서 N)/"미확인"(표에 없음)."""
    if not issn:
        return "N", "", ""
    entry = lookup(table, issn, eissn, issn_pairs)
    if entry is None:
        return "미확인", "", ""
    return entry["scie"], entry["jif"], entry["top_pct"]
