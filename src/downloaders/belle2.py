import re

import requests
from bs4 import BeautifulSoup

from ..models import RawPublication
from ..date_filter import arxiv_id_to_date
from .base import BaseDownloader

_DOI_RE = re.compile(r"10\.\S+$")


def _clean_doi_href(href):
    """belle2 페이지의 DOI 링크가 'doi.org/https://doi.org/10.xxx' 처럼
    중복돼 있는 경우가 있어 10.으로 시작하는 부분만 추출한다."""
    m = _DOI_RE.search(href)
    return m.group(0) if m else None


class Belle2Downloader(BaseDownloader):
    """docs.belle2.org/pub_data/publications/ 형식 (페이지네이션 있는 HTML 테이블).

    테이블 컬럼(왼쪽부터): title(+tags), memo번호, 저자(orcid 링크 포함),
    상태("submitted to X" / "published in X" / "<journal citation> [doi 링크]"), arXiv.
    """

    MAX_PAGES = 20

    def fetch(self):
        results = []
        for page in range(1, self.MAX_PAGES + 1):
            resp = requests.get(
                self.url, params={"max_results": 200, "page": page}, timeout=30
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table", class_="table-hover")
            if table is None:
                break
            rows = table.find("tbody").find_all("tr", recursive=False)
            if not rows:
                break

            for row in rows:
                tds = row.find_all("td", recursive=False)
                if len(tds) < 5:
                    continue

                title_link = tds[0].find("a")
                title = title_link.get_text(strip=True) if title_link else tds[0].get_text(strip=True)

                status_cell = tds[3]
                status_link = status_cell.find("a")
                if status_link is not None:
                    journal_citation_raw = status_link.get_text(strip=True)
                    doi = _clean_doi_href(status_link.get("href", ""))
                    status = "published"
                else:
                    journal_citation_raw = status_cell.get_text(strip=True)
                    doi = None
                    text = journal_citation_raw.lower()
                    if text.startswith("published"):
                        status = "accepted"  # 저널 게재확정, 이 페이지엔 DOI 링크 미표시
                    elif text.startswith("submitted"):
                        status = "submitted"
                    else:
                        status = "unknown"

                arxiv_link = tds[4].find("a")
                arxiv_id = None
                if arxiv_link is not None:
                    arxiv_id = arxiv_link.get_text(strip=True).split()[0]

                results.append(
                    RawPublication(
                        community=self.community_name,
                        title=title,
                        arxiv_id=arxiv_id,
                        doi=doi,
                        journal_citation_raw=journal_citation_raw,
                        pub_date_raw=None,
                        # belle2 목록엔 날짜 컬럼이 없어 arXiv ID로 대략적인 연-월을 추정한다.
                        submitted_date_raw=arxiv_id_to_date(arxiv_id),
                        status=status,
                        source_url=self.url,
                    )
                )

            if len(rows) < 200:
                break  # 마지막 페이지
        return results
