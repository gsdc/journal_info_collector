import re
import time
import unicodedata
from dataclasses import asdict

import requests

from .models import InspireAuthor, InspireRecord

_journal_cache = {}


class InspireClient:
    def __init__(self, settings, cache=None):
        cfg = settings["inspire"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.delay = cfg.get("request_delay_sec", 1.0)
        self.timeout = cfg.get("timeout_sec", 30)
        self._session = requests.Session()
        self._cache = cache  # SqliteCache 또는 None (DOI/arXiv ID로 찾은 레코드 재사용용)

    def _get(self, url, **kwargs):
        resp = self._session.get(url, timeout=self.timeout, **kwargs)
        time.sleep(self.delay)
        return resp

    def get_by_doi(self, doi):
        """DOI로 INSPIRE-HEP literature 레코드를 조회한다.
        찾지 못하면 None을 반환한다."""
        if self._cache is not None:
            cached = self._cache.get("inspire_doi", doi)
            # preprint_date 키가 없는 옛 캐시는 게재일에 투고일이 섞였을 수 있어 다시 조회한다.
            if cached is not None and "preprint_date" in cached and "issn_online" in cached:
                record = _record_from_dict(cached)
                if is_complete(record):
                    return record

        url = f"{self.base_url}/doi/{doi}"
        resp = self._get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        metadata = data.get("metadata", data)
        record = self._parse_record(metadata)
        if self._cache is not None and is_complete(record):
            self._cache.set("inspire_doi", doi, asdict(record))
        return record

    def get_by_arxiv(self, arxiv_id):
        """arXiv ID로 조회. 최근 논문은 출판사에서 DOI가 막 발급돼도
        INSPIRE의 dois 필드 갱신이 며칠 지연되는 경우가 있어, DOI 조회가
        404일 때 저자/저널/사사 정보라도 미리 확인하기 위한 대체 경로다.
        (매칭 기준 자체는 여전히 다운로드 원본의 DOI다.)"""
        if not arxiv_id:
            return None

        if self._cache is not None:
            cached = self._cache.get("inspire_arxiv", arxiv_id)
            # preprint_date 키가 없는 옛 캐시는 게재일에 투고일이 섞였을 수 있어 다시 조회한다.
            if cached is not None and "preprint_date" in cached and "issn_online" in cached:
                record = _record_from_dict(cached)
                if is_complete(record):
                    return record

        url = f"{self.base_url}/arxiv/{arxiv_id}"
        resp = self._get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        metadata = data.get("metadata", data)
        record = self._parse_record(metadata)
        # arXiv ID로는 preprint 단계에서도(DOI 없이) 레코드가 잡히는 경우가
        # 흔하다 — DOI가 아직 없는 레코드는 다음에 실제로 출판되면 내용이
        # 바뀌므로 캐시하지 않고, 게재 정보까지 다 붙은 경우만 캐시한다.
        if self._cache is not None and is_complete(record):
            self._cache.set("inspire_arxiv", arxiv_id, asdict(record))
        return record

    def _parse_record(self, metadata):
        pub_info_list = metadata.get("publication_info") or [{}]
        pub_info = next((p for p in pub_info_list if p.get("journal_title")), pub_info_list[0])

        journal_title = pub_info.get("journal_title")
        volume = pub_info.get("journal_volume")
        page = pub_info.get("artid") or pub_info.get("page_start")

        journal_ref = pub_info.get("journal_record", {}).get("$ref")
        issn_print, issn_online = self._get_journal_issn(journal_ref) if journal_ref else (None, None)

        dois = metadata.get("dois") or []
        doi = dois[0].get("value") if dois else None

        titles = metadata.get("titles") or []
        # 출판사(APS 등) 제목에는 MathML 태그가 그대로 섞여 있는 경우가 많아
        # CSV에서 읽기 어려우므로, LaTeX 표기인 arXiv 소스 제목을 우선한다.
        title = titles[0].get("title") if titles else None
        for t in titles:
            if t.get("source") == "arXiv":
                title = t.get("title")
                break

        # 게재일은 실제 게재 정보(imprint)만 쓴다. 예전에는 없으면 preprint_date로
        # 채웠는데, 그러면 게재일이 arXiv 투고일로 잡혀 연도가 틀릴 수 있다.
        imprints = metadata.get("imprints") or []
        pub_date = imprints[0].get("date") if imprints else None

        authors = []
        for a in metadata.get("authors", []):
            orcid = None
            for ident in a.get("ids", []):
                if ident.get("schema") == "ORCID":
                    orcid = ident.get("value")
                    break
            authors.append(InspireAuthor(full_name=a.get("full_name", ""), orcid=orcid))

        collaborations = [c.get("value") for c in metadata.get("collaborations", []) if c.get("value")]

        return InspireRecord(
            control_number=metadata.get("control_number"),
            title=title,
            doi=doi,
            journal_title=journal_title,
            journal_volume=volume,
            page=page,
            issn=issn_print or issn_online,
            issn_online=issn_online,
            pub_date=pub_date,
            authors=authors,
            collaborations=collaborations,
            preprint_date=metadata.get("preprint_date"),
        )

    def _get_journal_issn(self, journal_ref_url):
        """INSPIRE 저널 레코드의 (인쇄판 ISSN, 전자판 ISSN). 없으면 None."""
        if journal_ref_url in _journal_cache:
            return _journal_cache[journal_ref_url]

        if self._cache is not None:
            cached = self._cache.get("inspire_journal_issns", journal_ref_url)
            if cached is not None:
                pair = (cached.get("print"), cached.get("online"))
                _journal_cache[journal_ref_url] = pair
                return pair

        resp = self._get(journal_ref_url)
        printed = online = None
        if resp.status_code == 200:
            for item in resp.json().get("metadata", {}).get("issns") or []:
                if item.get("medium") == "online":
                    online = online or item.get("value")
                else:  # "print" 또는 medium 미기재
                    printed = printed or item.get("value")
        pair = (printed, online)
        _journal_cache[journal_ref_url] = pair
        if self._cache is not None and (printed or online):
            self._cache.set("inspire_journal_issns", journal_ref_url, {"print": printed, "online": online})
        return pair

        resp = self._get(journal_ref_url)
        issn = None
        if resp.status_code == 200:
            md = resp.json().get("metadata", {})
            issns = md.get("issns") or []
            if issns:
                issn = issns[0].get("value")
        _journal_cache[journal_ref_url] = issn
        if self._cache is not None and issn is not None:
            self._cache.set("inspire_journal_issn", journal_ref_url, {"issn": issn})
        return issn


# 글꼴·장식용 LaTeX 명령: 의미가 없으므로 지운다(\mathrm{B} -> B).
_LATEX_STYLE_RE = re.compile(
    r"\\(?:mathrm|mathbf|mathit|mathcal|mathsf|boldsymbol|text|textrm|textit|textbf"
    r"|rm|bf|it|bar|overline|hat|tilde|vec|sqrt|left|right|big|Big)(?![a-zA-Z])"
)
_LATEX_ARROW_RE = re.compile(r"\\(?:to|rightarrow|longrightarrow)(?![a-zA-Z])")


def normalize_title(title):
    """제목 비교용 정규화. 출판사(MathML·유니코드)와 arXiv(LaTeX) 표기가 달라도
    같은 제목이면 같은 값이 되게 한다.

    그리스 문자·prime·화살표·+는 이름으로 바꿔 남긴다. 지워 버리면 붕괴 채널만
    다른 논문끼리 같은 값이 된다(실제 사례: Belle II "Observation of the decays
    B -> Sigma_c ..." 두 논문). LaTeX \Sigma와 유니코드 Σ는 모두 "sigma"가 된다.
    """
    t = re.sub(r"<[^>]+>", " ", title or "")
    t = _LATEX_STYLE_RE.sub(" ", t)
    t = _LATEX_ARROW_RE.sub(" to ", t)
    t = re.sub(r"\\([a-zA-Z]+)", r" \1 ", t)  # \Sigma -> Sigma, \prime -> prime
    out = []
    for ch in t:
        if ch in "'′’":
            out.append(" prime ")
        elif ch in "→⟶":
            out.append(" to ")
        elif ch == "+":
            out.append(" plus ")
        elif ord(ch) > 127:
            name = unicodedata.name(ch, "")
            if name.startswith("GREEK") and "LETTER" in name:
                out.append(" " + name.split()[-1].lower() + " ")
            else:
                out.append(unicodedata.normalize("NFKD", ch))
        else:
            out.append(ch)
    return re.sub(r"[^a-z0-9]", "", "".join(out).lower())


def is_complete(record):
    """DOI·저널·권·페이지·게재일이 모두 채워졌는지. ISSN은 INSPIRE 저널
    레코드에 원래 비어 있는 경우가 많고 settings의 fallback으로 보완하므로
    기준에서 뺀다. 하나라도 비어 있으면 캐시하지 않고(이미 캐시된 것도
    무시하고) 다음 실행에서 다시 조회한다."""
    return bool(
        record.doi and record.journal_title and record.journal_volume
        and record.page and record.pub_date
    )


def _record_from_dict(d):
    authors = [InspireAuthor(**a) for a in d.get("authors", [])]
    return InspireRecord(
        control_number=d.get("control_number"),
        title=d.get("title"),
        doi=d.get("doi"),
        journal_title=d.get("journal_title"),
        journal_volume=d.get("journal_volume"),
        page=d.get("page"),
        issn=d.get("issn"),
        issn_online=d.get("issn_online"),
        pub_date=d.get("pub_date"),
        authors=authors,
        collaborations=d.get("collaborations", []),
        preprint_date=d.get("preprint_date"),
    )
