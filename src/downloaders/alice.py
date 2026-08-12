import re

import requests
from bs4 import BeautifulSoup

from ..models import RawPublication
from .base import BaseDownloader

_DOI_HREF_RE = re.compile(r"10\.\S+$")


class AliceDownloader(BaseDownloader):
    """alice-publications.web.cern.ch/publications 형식 (페이지네이션 있는 HTML 테이블).

    한 행 = 논문 하나. 제목 링크 하나, "Article reference: <citation> [Publication link] [arXiv] [HEPData]"
    형태의 <li>, "Publication date: ..." 가 포함된 <li>가 뒤따른다.
    """

    MAX_PAGES = 60

    def fetch(self):
        results = []
        for page in range(self.MAX_PAGES):
            resp = requests.get(self.url, params={"page": page}, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table", class_="views-table")
            if table is None:
                break
            rows = table.find("tbody").find_all("tr", recursive=False)
            if not rows:
                break

            for row in rows:
                cell = row.find("td")
                if cell is None:
                    continue

                title_link = cell.find("a")
                title = title_link.get_text(strip=True) if title_link else ""

                ref_li = None
                pub_date_li = None
                for li in cell.find_all("li"):
                    text = li.get_text(" ", strip=True)
                    if text.startswith("Article reference"):
                        ref_li = li
                    elif "Publication date" in text:
                        pub_date_li = li

                if ref_li is None:
                    continue

                doi = None
                for a in ref_li.find_all("a"):
                    href = a.get("href", "")
                    if "doi.org" in href:
                        m = _DOI_HREF_RE.search(href)
                        if m:
                            doi = m.group(0)
                            break
                if not doi:
                    continue  # DOI 없는(출판 전) 논문은 대상 제외

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

                arxiv_id = None
                arxiv_link = cell.find("a", href=re.compile(r"arxiv\.org/abs/"))
                if arxiv_link is not None:
                    m = re.search(r"arxiv\.org/abs/([0-9.]+)", arxiv_link.get("href", ""))
                    if m:
                        arxiv_id = m.group(1)

                results.append(
                    RawPublication(
                        community=self.community_name,
                        title=title,
                        arxiv_id=arxiv_id,
                        doi=doi,
                        journal_citation_raw=journal_citation_raw or None,
                        pub_date_raw=pub_date_raw,
                        source_url=self.url,
                    )
                )
        return results
