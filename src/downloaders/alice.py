import re

import requests
from bs4 import BeautifulSoup

from ..models import RawPublication
from .base import BaseDownloader

_DOI_IN_URL_RE = re.compile(r"(10\.\d{4,}/[^\s?#]+)")
_ARXIV_RE = re.compile(r"arxiv\.org/abs/([0-9.]+)")


def _doi_from_href(href):
    """출판사 링크에서 DOI를 뽑는다. doi.org 외에도 출판사 링크 경로에 DOI가
    그대로 들어 있는 경우가 많다(예: https://link.aps.org/doi/10.1103/...,
    https://link.springer.com/article/10.1007/JHEP03(2025)029,
    https://journals.aps.org/prl/abstract/10.1103/PhysRevLett.132.162302).
    ScienceDirect(pii)처럼 DOI가 없는 링크는 None — 이 경우 arXiv ID로 INSPIRE에서 찾는다."""
    if "arxiv.org" in href:
        return None
    m = _DOI_IN_URL_RE.search(href)
    return m.group(1) if m else None


class AliceDownloader(BaseDownloader):
    """alice-publications.web.cern.ch 의 세 목록 페이지를 모두 읽는다
    (모두 페이지네이션 있는 HTML 테이블, 한 행 = 논문 하나).

    - /publications: 출판된 논문. "Article reference: <citation> [Publication link]
      [arXiv] [HEPData]" <li>와 "Publication date: ..." <li>가 있다.
    - /accepted: 게재확정. arXiv 링크만 있고 DOI·날짜 없음.
    - /submitted: 투고 중. "Submission Date: ..."와 arXiv 링크가 있다.

    accepted/submitted 페이지는 갱신이 늦어 이미 출판된 논문이 남아 있는 경우가
    있으므로, 파이프라인이 arXiv ID로 INSPIRE를 다시 조회해 실제 출판 여부를
    확인한다('숨은 출판' 탐지). 같은 arXiv ID가 여러 페이지에 있으면
    publications > accepted > submitted 순으로 하나만 남긴다.
    """

    MAX_PAGES = 60

    def fetch(self):
        base = self.url.rstrip("/").rsplit("/", 1)[0]
        results = self._fetch_published(self.url)
        seen = {p.arxiv_id for p in results if p.arxiv_id}
        for path, status in (("accepted", "accepted"), ("submitted", "submitted")):
            for pub in self._fetch_unpublished(f"{base}/{path}", status):
                if pub.arxiv_id and pub.arxiv_id in seen:
                    self._drop(f"{path} 페이지 중복(이미 다른 목록에 있음)", pub.title, expected=True)
                    continue
                seen.add(pub.arxiv_id)
                results.append(pub)
        return results

    def _iter_cells(self, url):
        for page in range(self.MAX_PAGES):
            resp = requests.get(url, params={"page": page}, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table", class_="views-table")
            if table is None:
                break
            rows = table.find("tbody").find_all("tr", recursive=False)
            if not rows:
                break
            for row in rows:
                self._row()
                cell = row.find("td")
                if cell is None:
                    self._drop("셀(td) 없음", row.get_text(" ", strip=True))
                    continue
                yield cell
        else:
            self.audit["truncated"] = True

    @staticmethod
    def _arxiv_id(cell):
        link = cell.find("a", href=_ARXIV_RE)
        if link is None:
            return None
        m = _ARXIV_RE.search(link.get("href", ""))
        return m.group(1) if m else None

    @staticmethod
    def _title(cell):
        link = cell.find("a")
        return link.get_text(strip=True) if link else ""

    def _fetch_published(self, url):
        results = []
        for cell in self._iter_cells(url):
            ref_li = None
            pub_date_li = None
            for li in cell.find_all("li"):
                text = li.get_text(" ", strip=True)
                if text.startswith("Article reference"):
                    ref_li = li
                elif "Publication date" in text:
                    pub_date_li = li

            if ref_li is None:
                self._drop("출판 목록인데 'Article reference' 항목 없음", self._title(cell))
                continue

            doi = None
            for a in ref_li.find_all("a"):
                doi = _doi_from_href(a.get("href", ""))
                if doi:
                    break
            arxiv_id = self._arxiv_id(cell)
            if not doi and not arxiv_id:
                self._drop("DOI·arXiv 모두 없음(INSPIRE 대조 불가)", self._title(cell))
                continue

            ref_text = ref_li.get_text(" ", strip=True)
            journal_citation_raw = ref_text.split("Article reference:")[-1]
            journal_citation_raw = re.split(
                r"Publication link|arXiv|HEPData", journal_citation_raw
            )[0].strip()

            pub_date_raw = None
            if pub_date_li is not None:
                m = re.search(r"Publication date:\s*([^;]+)", pub_date_li.get_text(" ", strip=True))
                if m:
                    pub_date_raw = m.group(1).strip()

            results.append(
                RawPublication(
                    community=self.community_name,
                    title=self._title(cell),
                    arxiv_id=arxiv_id,
                    doi=doi,
                    journal_citation_raw=journal_citation_raw or None,
                    pub_date_raw=pub_date_raw,
                    # DOI를 링크에서 못 뽑았어도 출판 목록에 있는 논문이다. DOI는
                    # 파이프라인이 arXiv ID로 INSPIRE에서 찾아 채운다.
                    status="published",
                    source_url=url,
                )
            )
        return results

    def _fetch_unpublished(self, url, status):
        results = []
        for cell in self._iter_cells(url):
            arxiv_id = self._arxiv_id(cell)
            if not arxiv_id:
                self._drop(f"{status} 목록인데 arXiv 링크 없음", self._title(cell))
                continue
            text = cell.get_text(" ", strip=True)
            m = re.search(r"Submission Date:\s*(\d{1,2} \w+,? \d{4})", text)
            results.append(
                RawPublication(
                    community=self.community_name,
                    title=self._title(cell),
                    arxiv_id=arxiv_id,
                    submitted_date_raw=m.group(1) if m else None,
                    status=status,
                    source_url=url,
                )
            )
        return results
