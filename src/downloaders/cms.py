import csv
import io

import requests

from ..models import RawPublication
from .base import BaseDownloader


def _status_from_reference(doi, reference):
    if doi:
        return "published"
    ref = (reference or "").strip().lower()
    if ref.startswith("submitted"):
        return "submitted"
    if ref.startswith("accepted"):
        return "accepted"
    if ref.startswith("superseded"):
        return "superseded"  # 철회/대체된 분석노트 - 리포트 대상 아님
    return "unknown"


class CmsDownloader(BaseDownloader):
    """CMS_publications.csv 형식.

    컬럼: Number, CADI, Other, CERN-EP, arXiv, Categ, Inspire, CDS,
          Reference, DOI, Err/Corr/Add, Date Sub, Date Pub, Title
    DOI가 없어도(투고/게재확정 단계) 일단 포함한다 — 최종 출판 여부는
    파이프라인에서 INSPIRE/CrossRef로 다시 확인해서 판단한다.
    """

    def fetch(self):
        resp = requests.get(self.url, timeout=30)
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"

        reader = csv.DictReader(io.StringIO(resp.text))
        results = []
        for row in reader:
            self._row()
            doi = (row.get("DOI") or "").strip() or None
            reference = (row.get("Reference") or "").strip() or None
            status = _status_from_reference(doi, reference)
            if status == "superseded":
                self._drop("superseded(철회/대체)", row.get("Title"), expected=True)
                continue  # 철회/다른 분석노트로 대체됨 - 애초에 논문이 아님

            arxiv_id = (row.get("arXiv") or "").strip() or None
            results.append(
                RawPublication(
                    community=self.community_name,
                    title=(row.get("Title") or "").strip(),
                    arxiv_id=arxiv_id,
                    doi=doi,
                    journal_citation_raw=reference,
                    pub_date_raw=(row.get("Date Pub") or "").strip() or None,
                    submitted_date_raw=(row.get("Date Sub") or "").strip() or None,
                    status=status,
                    source_url=self.url,
                )
            )
        return results
