import re

# "Korea Institute of Science and Technology Information"(KISTI)만 매칭한다.
# 뒤에 "Information"이 없는 "Korea Institute of Science and Technology"(KIST)는
# 이름이 비슷한 별개 기관이라 제외해야 하는데, 실제로 일부 출판사(APS 확인됨)가
# KISTI 이름을 KIST의 funder DOI(10.13039/501100003693)에 잘못 태깅해 둔 사례를
# 발견했다 — 그래서 DOI 매칭이 실패해도 이름 텍스트로 다시 확인한다.
KISTI_NAME_RE = re.compile(
    r"korea institute of science and technology information", re.IGNORECASE
)
KIST_FUNDER_DOI = "10.13039/501100003693"  # KIST(별개 기관). KISTI 오태깅 판별용

# 사사(감사의 글) 원문에서 KISTI 또는 GSDC 언급을 찾는 패턴. 약칭(KISTI, GSDC)과
# 전체 명칭을 모두 본다. GSDC 전체 명칭은 "Global Science experimental Data hub
# Center"가 정식이지만 "experimental"이 빠지거나 Centre로 쓰는 등 표기가 흔들린다.
# KIST(뒤에 Information 없음)는 별개 기관이라 매칭되지 않게 한다.
KISTI_ACK_RE = re.compile(
    r"\bKISTI\b"
    r"|korea\s+institute\s+of\s+science\s+and\s+technology\s+information"
    r"|\bGSDC\b"
    r"|global\s+science\s+(?:experimental\s+)?data[\s-]*hub\s+cent(?:er|re)",
    re.IGNORECASE,
)
# funder 이름에서 KISTI를 찾는 패턴(약칭·GSDC 표기 포함).
_KISTI_FUNDER_NAME_RE = re.compile(
    KISTI_NAME_RE.pattern + r"|\bKISTI\b|\bGSDC\b|data\s+hub\s+cent(?:er|re)",
    re.IGNORECASE,
)
# 원문 사사에 KIST(뒤에 Information 없음)가 따로 나오는지
_KIST_ONLY_RE = re.compile(
    r"korea\s+institute\s+of\s+science\s+and\s+technology(?!\s+information)|\bKIST\b",
    re.IGNORECASE,
)
_TEX_COMMENT_RE = re.compile(r"(?<!\\)%.*")
_EVIDENCE_WINDOW = 80


def mentions_kist(text):
    """사사 원문에 KIST(KISTI가 아닌 별개 기관)가 나오는지."""
    text = re.sub(r"\s+", " ", _TEX_COMMENT_RE.sub("", text).replace("~", " "))
    return bool(_KIST_ONLY_RE.search(text))


def find_kisti_ack_mentions(text, max_hits=3):
    """사사 원문(LaTeX)에서 KISTI/GSDC 언급 부분을 앞뒤 문맥과 함께 반환한다.
    주석(%...)은 실제 논문에 안 나오는 문구라 제외하고, ~·줄바꿈은 공백으로 본다."""
    text = _TEX_COMMENT_RE.sub("", text).replace("~", " ")
    text = re.sub(r"\s+", " ", text)
    hits = []
    for m in KISTI_ACK_RE.finditer(text):
        start = max(0, m.start() - _EVIDENCE_WINDOW)
        end = min(len(text), m.end() + _EVIDENCE_WINDOW)
        snippet = text[start:end].strip()
        if snippet not in hits:
            hits.append(snippet)
        if len(hits) >= max_hits:
            break
    return hits


def check_ack_via_funder(crossref_record, kisti_funder_doi, ack_mentions=None, ack_mentions_kist=False):
    """CrossRef의 FundRef(funder) 메타데이터로 KISTI funder ID 등록 여부를 판정한다.

    출판사(APS 확인됨)가 KISTI를 KIST(이름만 비슷한 별개 기관)의 funder DOI
    (10.13039/501100003693)로 잘못 등록한 사례가 실제로 있어서, KISTI DOI가
    정확히 있는 경우와 오태깅을 구분해서 돌려준다:
      - CMS PRL 등: 이름은 "Korea Institute of Science and Technology Information"
        인데 DOI는 KIST 것
      - ALICE PRC 10.1103/5bdl-cry6: 이름까지 "Korea Institute of Science and
        Technology"(KIST)로 바뀌어 있었지만 원문 사사에는 KISTI만 있고 KIST는 없음

    "N"은 곧이곧대로 믿으면 안 된다 — Elsevier/Springer 등은 사사 문단을
    자동 텍스트마이닝해서 funder 목록을 채우는 것으로 보이는데, 원문에 분명히
    있는 기관이 통째로 빠진 사례를 확인했다. 그래서 사사 원문은 별도(Ack)로
    판정하고, 이 함수는 funder 메타데이터만 본다.

    ack_mentions: 사사 원문에서 찾은 KISTI/GSDC 언급 목록(find_kisti_ack_mentions).
    ack_mentions_kist: 사사 원문에 KIST(Information 없음)가 따로 나오는지.
      KIST DOI+KIST 이름인 경우 이 둘로 오태깅인지(원문엔 KISTI만 있음) 추정한다.

    반환: (status, note)
      "Y"          KISTI funder DOI가 정확히 등록됨
      "KIST오태깅"  KISTI DOI는 없고 KIST DOI가 있는데, 그 funder 이름이 KISTI이거나
                    (이름도 KIST면) 원문 사사에 KISTI가 있고 KIST는 없음
      "이름만"      KISTI 이름은 있으나 funder DOI가 비어 있음
      "KIST확인"    KIST DOI가 있지만 오태깅인지 판단할 근거가 없음(사람 확인 필요)
      "N"          funder 데이터는 있으나 KISTI 관련 항목 없음
      None         출판사가 funder 메타데이터 자체를 등록 안 함
    """
    if crossref_record is None or not (crossref_record.funders or crossref_record.funder_names):
        return None, "CrossRef funder 메타데이터 없음(출판사가 FundRef 등록 안 함)"

    if kisti_funder_doi in crossref_record.funder_dois:
        return "Y", f"CrossRef funder DOI 일치 ({kisti_funder_doi})"

    funders = crossref_record.funders
    kist_tagged = [f for f in funders if f.get("doi") == KIST_FUNDER_DOI]
    kisti_named_kist = [f for f in kist_tagged if KISTI_NAME_RE.search(f.get("name") or "")]
    if kisti_named_kist:
        return "KIST오태깅", (
            f"funder 이름은 KISTI인데 DOI는 KIST({KIST_FUNDER_DOI})로 등록됨: "
            + ", ".join(f["name"] for f in kisti_named_kist)
        )
    if kist_tagged:
        if ack_mentions and not ack_mentions_kist:
            return "KIST오태깅", (
                f"funder는 KIST({KIST_FUNDER_DOI}, 이름 '{kist_tagged[0].get('name')}')로 "
                "등록됐지만 원문 사사에는 KISTI만 있고 KIST는 없어 오태깅으로 추정"
            )

    name_only = [
        f["name"] for f in funders
        if not f.get("doi") and _KISTI_FUNDER_NAME_RE.search(f.get("name") or "")
    ]
    if name_only:
        return "이름만", "funder DOI 없이 이름만 등록됨: " + ", ".join(name_only)

    if kist_tagged:
        return "KIST확인", (
            f"KIST DOI({KIST_FUNDER_DOI}, 이름 '{kist_tagged[0].get('name')}')가 등록돼 있으나 "
            "원문으로 KISTI 오태깅인지 판단할 수 없음 — 사람 확인 필요"
        )
    return "N", "CrossRef funder 목록에 KISTI DOI/이름 없음"


