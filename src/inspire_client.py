import time
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
            if cached is not None:
                return _record_from_dict(cached)

        url = f"{self.base_url}/doi/{doi}"
        resp = self._get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        metadata = data.get("metadata", data)
        record = self._parse_record(metadata)
        if self._cache is not None:
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
            if cached is not None:
                return _record_from_dict(cached)

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
        # 바뀌므로 캐시하지 않고, DOI가 이미 붙은(=게재 확정) 경우만 캐시한다.
        if self._cache is not None and record.doi is not None:
            self._cache.set("inspire_arxiv", arxiv_id, asdict(record))
        return record

    def _parse_record(self, metadata):
        pub_info_list = metadata.get("publication_info") or [{}]
        pub_info = next((p for p in pub_info_list if p.get("journal_title")), pub_info_list[0])

        journal_title = pub_info.get("journal_title")
        volume = pub_info.get("journal_volume")
        page = pub_info.get("artid") or pub_info.get("page_start")

        journal_ref = pub_info.get("journal_record", {}).get("$ref")
        issn = self._get_journal_issn(journal_ref) if journal_ref else None

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

        pub_date = None
        imprints = metadata.get("imprints") or []
        if imprints:
            pub_date = imprints[0].get("date")
        if not pub_date:
            pub_date = metadata.get("preprint_date")

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
            issn=issn,
            pub_date=pub_date,
            authors=authors,
            collaborations=collaborations,
        )

    def _get_journal_issn(self, journal_ref_url):
        if journal_ref_url in _journal_cache:
            return _journal_cache[journal_ref_url]

        if self._cache is not None:
            cached = self._cache.get("inspire_journal_issn", journal_ref_url)
            if cached is not None:
                issn = cached.get("issn")
                _journal_cache[journal_ref_url] = issn
                return issn

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
        pub_date=d.get("pub_date"),
        authors=authors,
        collaborations=d.get("collaborations", []),
    )
