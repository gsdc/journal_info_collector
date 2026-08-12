import re

import requests
from bs4 import BeautifulSoup

from ..models import RawPublication
from .base import BaseDownloader

_DOI_TEXT_RE = re.compile(r"doi:(\S+)", re.IGNORECASE)


class LigoDownloader(BaseDownloader):
    """pnp.ligo.org/ppcomm/Papers.html 형식 (단일 페이지, 전체 이력 포함).

    테이블 컬럼: Release Date, Title, Keywords, Science Summary,
                Journal citation, arXiv Preprint, Public Report.
    'Journal citation' 셀에 "<citation text> doi:10.xxx" 형태로 DOI가 텍스트로 포함됨.
    표시명은 'LIGO'로 통일한다(LSC/Virgo/KAGRA 공동 논문 포함).
    """

    def fetch(self):
        resp = requests.get(self.url, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        tbody = soup.find("tbody", id="Table")
        if tbody is None:
            return []

        results = []
        for row in tbody.find_all("tr", recursive=False):
            tds = row.find_all("td", recursive=False)
            if len(tds) < 6:
                continue

            title_b = tds[1].find("b")
            title = title_b.get_text(strip=True) if title_b else tds[1].get_text(strip=True)

            journal_cell_text = tds[4].get_text(" ", strip=True)
            doi_match = _DOI_TEXT_RE.search(journal_cell_text)
            if doi_match:
                doi = doi_match.group(1)
                journal_citation_raw = _DOI_TEXT_RE.sub("", journal_cell_text).strip()
                status = "published"
            else:
                doi = None
                journal_citation_raw = journal_cell_text if journal_cell_text != "-" else None
                if "submitted" in journal_cell_text.lower():
                    status = "submitted"
                elif journal_cell_text == "-":
                    status = "preprint"  # 아직 저널에 투고 정보조차 없음(내부 초안/최근 프리프린트)
                else:
                    status = "unknown"

            arxiv_link = tds[5].find("a")
            arxiv_id = arxiv_link.get_text(strip=True) if arxiv_link else None

            pub_date_raw = tds[0].get_text(" ", strip=True).replace("*Recent*", "").strip()

            results.append(
                RawPublication(
                    community=self.community_name,
                    title=title,
                    arxiv_id=arxiv_id,
                    doi=doi,
                    journal_citation_raw=journal_citation_raw or None,
                    pub_date_raw=pub_date_raw or None,
                    status=status,
                    source_url=self.url,
                )
            )
        return results
