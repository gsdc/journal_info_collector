"""SCIE 여부·JIF·카테고리 상위%를 조사해야 하는 저널 목록(txt, 탭 구분) 생성.

JCR(Journal Citation Reports)은 Clarivate 유료 데이터라 자동으로 받을 수 없다.
그래서 출판 논문들의 저널을 ISSN 기준으로 묶어 목록을 만들고, 사람이 JCR에서
SCIE 여부·JIF·JIF 기준 첫 번째 카테고리와 그 카테고리 상위%를 채운다.
같은 저널이 표기만 다른 경우(Phys. Rev. Lett. / Phys.Rev.Lett.)는 한 줄로 합친다.
"""
import json
import os
from collections import Counter, defaultdict

from . import config

COLUMNS = [
    "저널명",
    "ISSN(인쇄판 우선)",
    "eISSN",
    "논문수",
    "커뮤니티",
    "SCIE(Y/N)",
    "JIF",
    "JIF 첫번째 카테고리",
    "카테고리 상위%",
]


def _issn_pairs(settings):
    from .pipeline import _issn_pairs as pairs  # 같은 규칙 재사용(인쇄판 우선)

    return pairs(settings)


def build_journal_rows(candidates, settings, year=None):
    from .jcr import load_jcr_table, lookup

    pairs = _issn_pairs(settings)
    jcr_table = load_jcr_table(year) if year is not None else {}
    groups = defaultdict(lambda: {"names": Counter(), "communities": Counter(), "eissn": ""})
    for c in candidates:
        if c.get("publication_status") != "published":
            continue
        issn = c.get("issn") or ""
        eissn = c.get("eissn") or ""
        # eissn 필드가 없는 예전 결과도 인쇄판 우선 규칙으로 맞춘다
        pair = pairs.get(issn) or pairs.get(eissn) or pairs.get(c.get("journal") or "")
        if pair:
            issn, eissn = pair[0] or pair[1], pair[1]
        key = issn or f"(ISSN 없음) {c.get('journal') or '?'}"
        g = groups[key]
        g["names"][c.get("journal") or "?"] += 1
        g["communities"][c["community"]] += 1
        g["eissn"] = g["eissn"] or eissn
    rows = []
    for key, g in groups.items():
        no_issn = key.startswith("(ISSN 없음)")
        known = None if no_issn else lookup(jcr_table, key, g["eissn"], pairs)
        rows.append(
            {
                "저널명": g["names"].most_common(1)[0][0],
                "ISSN(인쇄판 우선)": "" if no_issn else key,
                "eISSN": "" if (no_issn or g["eissn"] == key) else g["eissn"],
                "논문수": sum(g["names"].values()),
                "커뮤니티": ", ".join(f"{k} {v}" for k, v in g["communities"].most_common()),
                # ISSN이 없으면 저널 논문이 아니므로 SCIE 아님. 이미 JCR 표에 있는 저널은 값을 미리 채운다.
                "SCIE(Y/N)": "N" if no_issn else (known["scie"] if known else ""),
                "JIF": known["jif"] if known else "",
                "JIF 첫번째 카테고리": "",
                "카테고리 상위%": known["top_pct"] if known else "",
            }
        )
    rows.sort(key=lambda r: (-r["논문수"], r["저널명"]))
    return rows


def write_journal_list(year, path=None, candidates=None, settings=None):
    settings = settings or config.load_settings()
    if candidates is None:
        with open(settings["output"]["candidates_path"], encoding="utf-8") as f:
            candidates = json.load(f)
    rows = build_journal_rows(candidates, settings, year)
    path = path or f"output/journals_{year}.txt"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            f"# {year}년 출판 논문의 저널 목록 ({len(rows)}개, 논문 {sum(r['논문수'] for r in rows)}편)\n"
            "# 탭 구분. 뒤의 4개 칸(SCIE·JIF·JIF 첫번째 카테고리·카테고리 상위%)을 JCR에서 채운다.\n"
            "# ISSN은 전자판 출판이어도 인쇄판 우선(인쇄판 없는 저널만 전자판). ISSN 없음 = SCIE 아님.\n"
        )
        f.write("\t".join(COLUMNS) + "\n")
        for r in rows:
            f.write("\t".join(str(r[c]) for c in COLUMNS) + "\n")
    return path, rows
