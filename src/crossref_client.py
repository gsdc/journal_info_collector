import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import requests


@dataclass
class CrossrefAuthor:
    family: str
    given: str
    orcid: Optional[str] = None  # "0000-0002-XXXX-XXXX" (URL 접두어 제거된 값)


@dataclass
class CrossrefRecord:
    issn_list: list = field(default_factory=list)
    issn_print: Optional[str] = None       # CrossRef issn-type "print"
    issn_electronic: Optional[str] = None  # CrossRef issn-type "electronic"
    volume: Optional[str] = None
    page: Optional[str] = None
    pub_date: Optional[str] = None  # "YYYY-MM-DD"
    journal_title: Optional[str] = None  # short-container-title 우선, 없으면 container-title
    authors: list = field(default_factory=list)  # list[CrossrefAuthor]
    funder_dois: list = field(default_factory=list)   # FundRef DOI 목록 (있는 경우만)
    funder_names: list = field(default_factory=list)  # 대응하는 funder 이름
    # funder별 (DOI, 이름) 짝. 위 두 목록은 DOI 없는 funder 때문에 길이가 달라
    # 짝을 맞출 수 없으므로, "이름은 KISTI인데 DOI는 KIST" 같은 오태깅 판별에는 이걸 쓴다.
    funders: list = field(default_factory=list)  # list[{"doi": str|None, "name": str}]


class CrossrefClient:
    """DOI 기반으로 CrossRef에서 ORCID/ISSN/출판일을 보강 조회한다.

    사용 예: curl -s "https://api.crossref.org/works/<doi>" | jq '.message.published["date-parts"][0]'
    INSPIRE-HEP 저자 레코드에는 ORCID가 비어있는 경우가 있어, CrossRef 값과 합쳐서 쓴다.
    """

    def __init__(self, settings=None, cache=None):
        cfg = (settings or {}).get("crossref", {})
        self.base_url = cfg.get("base_url", "https://api.crossref.org/works").rstrip("/")
        self.delay = cfg.get("request_delay_sec", 1.0)
        self.timeout = cfg.get("timeout_sec", 30)
        self._session = requests.Session()
        self._cache = cache  # SqliteCache 또는 None (DOI로 찾은 레코드 재사용용)

    def get_by_doi(self, doi):
        if self._cache is not None:
            cached = self._cache.get("crossref_doi", doi)
            # funders 필드가 없는 옛 캐시는 funder 짝 정보가 없어 다시 조회한다.
            # issn_print 키가 없는 옛 캐시는 인쇄판/전자판 구분이 없어 다시 조회한다.
            if cached is not None and "funders" in cached and "issn_print" in cached:
                record = _record_from_dict(cached)
                if is_complete(record):
                    return record

        url = f"{self.base_url}/{doi}"
        resp = self._session.get(url, timeout=self.timeout)
        time.sleep(self.delay)
        if resp.status_code != 200:
            return None
        message = resp.json().get("message", {})
        record = self._parse(message)
        if self._cache is not None and is_complete(record):
            self._cache.set("crossref_doi", doi, asdict(record))
        return record

    def _parse(self, message):
        issn_list = message.get("ISSN") or []
        issn_types = {t.get("type"): t.get("value") for t in message.get("issn-type") or []}

        page = message.get("page") or message.get("article-number")
        volume = message.get("volume")

        pub_date = None
        date_parts = (message.get("published") or {}).get("date-parts")
        if date_parts and date_parts[0]:
            parts = date_parts[0]
            pub_date = "-".join(f"{p:02d}" if i > 0 else str(p) for i, p in enumerate(parts))

        authors = []
        for a in message.get("author", []):
            orcid = a.get("ORCID")
            if orcid:
                orcid = orcid.rstrip("/").rsplit("/", 1)[-1]
            authors.append(
                CrossrefAuthor(family=a.get("family", ""), given=a.get("given", ""), orcid=orcid)
            )

        short_title = message.get("short-container-title") or []
        full_title = message.get("container-title") or []
        journal_title = (short_title or full_title or [None])[0]

        funders = message.get("funder") or []
        funder_dois = [f["DOI"] for f in funders if f.get("DOI")]
        funder_names = [f["name"] for f in funders if f.get("name")]
        funder_pairs = [{"doi": f.get("DOI"), "name": f.get("name", "")} for f in funders]

        return CrossrefRecord(
            issn_list=issn_list,
            issn_print=issn_types.get("print"),
            issn_electronic=issn_types.get("electronic"),
            volume=volume,
            page=page,
            pub_date=pub_date,
            journal_title=journal_title,
            authors=authors,
            funder_dois=funder_dois,
            funder_names=funder_names,
            funders=funder_pairs,
        )


def is_complete(record):
    """보고서에 필요한 서지 정보(ISSN·권·페이지·게재일)가 모두 채워졌는지.
    하나라도 비어 있으면 아직 출판사 메타데이터가 덜 갱신된 것일 수 있어
    캐시하지 않고(이미 캐시된 것도 무시하고) 다음 실행에서 다시 조회한다."""
    return bool(record.issn_list and record.volume and record.page and record.pub_date)


def _record_from_dict(d):
    authors = [CrossrefAuthor(**a) for a in d.get("authors", [])]
    return CrossrefRecord(
        issn_list=d.get("issn_list", []),
        issn_print=d.get("issn_print"),
        issn_electronic=d.get("issn_electronic"),
        volume=d.get("volume"),
        page=d.get("page"),
        pub_date=d.get("pub_date"),
        journal_title=d.get("journal_title"),
        authors=authors,
        funder_dois=d.get("funder_dois", []),
        funder_names=d.get("funder_names", []),
        funders=d.get("funders", []),
    )
