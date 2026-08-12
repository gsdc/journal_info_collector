import re

# "Korea Institute of Science and Technology Information"(KISTI)만 매칭한다.
# 뒤에 "Information"이 없는 "Korea Institute of Science and Technology"(KIST)는
# 이름이 비슷한 별개 기관이라 제외해야 하는데, 실제로 일부 출판사(APS 확인됨)가
# KISTI 이름을 KIST의 funder DOI(10.13039/501100003693)에 잘못 태깅해 둔 사례를
# 발견했다 — 그래서 DOI 매칭이 실패해도 이름 텍스트로 다시 확인한다.
KISTI_NAME_RE = re.compile(
    r"korea institute of science and technology information", re.IGNORECASE
)


def check_ack_via_funder(crossref_record, kisti_funder_doi):
    """CrossRef의 FundRef(funder) 메타데이터로 KISTI 사사 여부를 1차 판정한다.

    APS(Phys.Rev. 시리즈) 등 일부 출판사는 사사 문구에 없는 기관도
    구조화된 funder 목록에는 포함시켜 두는 경우가 있어(예: CMS 논문 사사 원문에는
    "MSIT and NRF (Republic of Korea)"만 보여도 CrossRef funder 목록에는
    KISTI DOI가 들어있음), "Y"는 arXiv 원문 텍스트 검색보다 더 정확할 수 있다.

    다만 funder DOI만 보면 안 된다 — 실제로 확인한 사례: 어떤 APS 논문은
    funder name이 "Korea Institute of Science and Technology Information"인데
    DOI는 10.13039/501100003693로 태깅돼 있었다. 이 DOI의 Crossref Funder
    Registry 정식 명칭은 "Korea Institute of Science and Technology"(KIST,
    이름만 비슷한 다른 기관)라서, DOI만 대조하면 이런 출판사 쪽 오태깅 건을
    거짓 음성으로 놓친다. 그래서 DOI가 안 맞아도 funder name 텍스트로 한 번 더
    확인한다.

    반대로 "N"은 곧이곧대로 믿으면 안 된다 — Elsevier/Springer 등은 사사 문단을
    자동 텍스트마이닝해서 funder 목록을 채우는 것으로 보이는데, 실제로 원문에
    분명히 있는 기관(NRF 등)이 통째로 빠진 채 funder 목록이 채워진 사례를
    확인했다. 즉 funder 목록이 "있다"는 것이 "완전하다"를 보장하지 않는다.
    그래서 이 함수의 "N"은 참고용 1차 신호일 뿐이고, 호출하는 쪽(pipeline.py)은
    "Y"가 아닌 모든 경우(N/None)에 대해 arXiv 원문도 반드시 확인한다.

    반환: (status, note)
      status: "Y" (funder DOI 또는 funder 이름으로 확인, 신뢰 가능) /
               "N" (funder 데이터는 있으나 KISTI DOI/이름 모두 없음 — 목록이
               불완전할 수 있어 원문 재확인 필요) /
               None (이 출판사는 funder 메타데이터 자체를 등록 안 함 -> 원문 텍스트로 확인 필요)
    """
    if crossref_record is None or not crossref_record.funder_dois:
        return None, "CrossRef funder 메타데이터 없음(출판사가 FundRef 등록 안 함)"

    if kisti_funder_doi in crossref_record.funder_dois:
        return "Y", f"CrossRef funder DOI 일치 ({kisti_funder_doi})"

    name_hits = [n for n in crossref_record.funder_names if KISTI_NAME_RE.search(n)]
    if name_hits:
        return "Y", (
            "funder DOI는 다르지만(출판사 오태깅으로 추정) funder 이름에 KISTI가 있음: "
            + ", ".join(name_hits)
        )

    return "N", (
        "CrossRef funder 목록에 KISTI DOI/이름 모두 없음(단, 이 목록이 불완전할 수 있어 "
        "원문 재확인 필요): " + ", ".join(crossref_record.funder_names)
    )
