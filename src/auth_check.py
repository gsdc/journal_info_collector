import re


def _name_tokens(name):
    name = name.replace(",", " ")
    return set(re.findall(r"[a-zA-Z]+", name.lower()))


def check_auth(responsible_list, inspire_authors, crossref_authors=None):
    """커뮤니티 담당자 명단(여러 명, OR 조건) vs 논문 저자 목록 대조.

    responsible_list: [{"name": ..., "orcid": ...}, ...] (config/responsible_authors.yaml)
    inspire_authors:  list[InspireAuthor] (INSPIRE-HEP 레코드의 전체 저자 목록)
    crossref_authors: list[CrossrefAuthor] or None (ORCID 보강용, CrossRef)

    반환: (status, note)
      status: "Y"(ORCID로 확인) / "확인필요"(이름만 일치, 표기 차이로 오탐 가능) / "N"
    """
    crossref_authors = crossref_authors or []

    orcid_pool = {a.orcid for a in inspire_authors if a.orcid}
    orcid_pool |= {a.orcid for a in crossref_authors if a.orcid}

    name_token_pool = [_name_tokens(a.full_name) for a in inspire_authors if a.full_name]
    name_token_pool += [
        _name_tokens(f"{a.given} {a.family}") for a in crossref_authors if a.family
    ]

    matched_by_orcid = []
    matched_by_name = []

    for person in responsible_list:
        name = (person.get("name") or "").strip()
        orcid = (person.get("orcid") or "").strip()
        if not name and not orcid:
            continue

        if orcid:
            if orcid in orcid_pool:
                matched_by_orcid.append(name or orcid)
            continue  # orcid가 지정돼 있으면 이름 매칭은 생략(오탐 방지)

        tokens = _name_tokens(name)
        if tokens and any(tokens <= pool for pool in name_token_pool):
            matched_by_name.append(name)

    if matched_by_orcid:
        return "Y", "ORCID 일치: " + ", ".join(matched_by_orcid)
    if matched_by_name:
        return "확인필요", "이름만 일치(표기 차이로 오탐 가능): " + ", ".join(matched_by_name)
    return "N", ""
