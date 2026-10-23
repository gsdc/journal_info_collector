import re


def _name_tokens(name):
    name = name.replace(",", " ")
    return set(re.findall(r"[a-zA-Z]+", name.lower()))


# KISTI 소속 표기(INSPIRE 정규화 값 "KISTI, Daejeon" 또는 출판사 원문 전체 명칭)
_KISTI_AFF_RE = re.compile(r"\bKISTI\b|korea institute of science and technology information", re.IGNORECASE)


def _family_and_initial(full_name):
    """"Bae, S." / "Bae, Sangwook" / "Sangwook Bae" -> ("bae", "s")."""
    if "," in full_name:
        family, given = full_name.split(",", 1)
    else:
        parts = full_name.split()
        family, given = (parts[-1], " ".join(parts[:-1])) if parts else ("", "")
    given_letters = re.findall(r"[a-zA-Z]", given)
    return family.strip().lower(), (given_letters[0].lower() if given_letters else "")


def check_auth(responsible_list, inspire_authors, crossref_authors=None):
    """커뮤니티 담당자 명단(여러 명, OR 조건) vs 논문 저자 목록 대조.

    responsible_list: [{"name": ..., "orcid": ...}, ...] (config/responsible_authors.yaml)
    inspire_authors:  list[InspireAuthor] (INSPIRE-HEP 레코드의 전체 저자 목록)
    crossref_authors: list[CrossrefAuthor] or None (ORCID 보강용, CrossRef)

    반환: (status, note)
      status: "Y"(ORCID로 확인, 또는 성·이니셜이 같고 그 저자 소속이 KISTI) /
              "확인필요"(이름만 일치, 표기 차이로 오탐 가능) / "N"

    컬래버레이션 저자 목록에는 "Bae, S."처럼 이니셜만 있고 ORCID가 없는 경우가 있어
    (실제 사례: LIGO 논문 3편), ORCID로 못 찾으면 성·이니셜 + KISTI 소속으로 다시 본다.
    """
    crossref_authors = crossref_authors or []

    orcid_pool = {a.orcid for a in inspire_authors if a.orcid}
    orcid_pool |= {a.orcid for a in crossref_authors if a.orcid}

    name_token_pool = [_name_tokens(a.full_name) for a in inspire_authors if a.full_name]
    name_token_pool += [
        _name_tokens(f"{a.given} {a.family}") for a in crossref_authors if a.family
    ]

    matched_by_orcid = []
    matched_by_affil = []
    matched_by_name = []
    initial_pool = [
        (_family_and_initial(a.full_name), any(_KISTI_AFF_RE.search(x) for x in a.affiliations))
        for a in inspire_authors
        if a.full_name
    ]

    for person in responsible_list:
        name = (person.get("name") or "").strip()
        orcid = (person.get("orcid") or "").strip()
        if not name and not orcid:
            continue

        if orcid and orcid in orcid_pool:
            matched_by_orcid.append(name or orcid)
            continue

        # 성·이니셜이 같고 그 저자의 소속이 KISTI면 담당자로 본다(ORCID 없는 이니셜 표기 대응)
        if name:
            key = _family_and_initial(name)
            if any(k == key and kisti for k, kisti in initial_pool):
                matched_by_affil.append(name)
                continue
        if orcid:
            continue  # orcid가 지정돼 있으면 이름만으로 하는 매칭은 생략(오탐 방지)

        tokens = _name_tokens(name)
        if tokens and any(tokens <= pool for pool in name_token_pool):
            matched_by_name.append(name)

    if matched_by_orcid:
        return "Y", "ORCID 일치: " + ", ".join(matched_by_orcid)
    if matched_by_affil:
        return "Y", "성·이니셜 일치 + KISTI 소속: " + ", ".join(matched_by_affil)
    if matched_by_name:
        return "확인필요", "이름만 일치(표기 차이로 오탐 가능): " + ", ".join(matched_by_name)
    return "N", ""
