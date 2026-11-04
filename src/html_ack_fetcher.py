import re
import time

import requests
from bs4 import BeautifulSoup

from .ack_check import find_kisti_ack_mentions, mentions_kist

_HEADING_RE = re.compile(r"^\s*(acknowledge?ments?|funding(\s+information|\s+sources?)?)\s*:?\s*$", re.IGNORECASE)
_HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_MAX_SECTION = 20000  # 이보다 긴 부모 요소는 본문 전체일 수 있어 쓰지 않는다
_MAX_EXCERPT = 8000
# 출판사 페이지가 자동 요청을 막지 않도록 일반적인 클라이언트 이름을 쓴다.
_USER_AGENT = "journal_info_collector/1.0 (+https://github.com/gsdc/journal_info_collector)"


class HtmlAckFetcher:
    """arXiv·Europe PMC에서 사사를 못 구한 논문의 원문 HTML 페이지에서 사사(Acknowledgements)
    ·Funding 절을 찾는다. 주소는 CrossRef가 알려 주는 원문(text-mining) 링크를 먼저 쓰고,
    없으면 DOI 주소(https://doi.org/...)를 쓴다.

    오픈액세스 원문(Nature 계열 등)은 페이지에 사사가 그대로 있다. 구독 논문이나 자동 요청을
    막는 출판사(Elsevier·ACS·Wiley 등)는 사사 절이 없거나 접근이 거부돼 미확인으로 남는다.
    사사 절을 못 찾으면 N이 아니라 미확인(None)으로 돌려준다 — 페이지에 본문이 없는 것일 수 있다.

    반환 형식은 ArxivFetcher.fetch_ack와 같다: (excerpt, note, kisti_mentions, kist_mentioned).
    """

    def __init__(self, settings):
        cfg = settings.get("html_ack", {})
        self.delay = cfg.get("request_delay_sec", 1.0)
        self.timeout = cfg.get("timeout_sec", 30)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _USER_AGENT, "Accept": "text/html"})

    def fetch_ack(self, doi, links=()):
        urls = list(dict.fromkeys([*links, f"https://doi.org/{doi}"]))
        failures = []
        for url in urls:
            try:
                resp = self._session.get(url, timeout=self.timeout, allow_redirects=True)
            except requests.RequestException as e:
                failures.append(f"{type(e).__name__}")
                continue
            finally:
                time.sleep(self.delay)
            if resp.status_code != 200:
                failures.append(f"HTTP {resp.status_code}")
                continue
            if "html" not in resp.headers.get("Content-Type", "html"):
                failures.append("HTML 아님")
                continue
            region = self._ack_region(resp.text)
            if region:
                escaped = region.replace("%", r"\%")  # LaTeX 주석 제거 규칙에 안 걸리게
                return (
                    region[:_MAX_EXCERPT],
                    f"원문 HTML({resp.url})에서 사사 확인",
                    find_kisti_ack_mentions(escaped),
                    mentions_kist(escaped),
                )
            failures.append("사사 절 없음")
        return "", f"원문 HTML에서 사사 못 찾음({', '.join(failures)})", None, False

    @staticmethod
    def _ack_region(html):
        """제목이 Acknowledgements·Funding인 절의 텍스트. 제목의 부모 요소에 다른 제목이
        없으면(절 하나) 그 전체를, 아니면 다음 제목 전까지의 형제 요소를 모은다."""
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        parts = []
        for h in soup.find_all(_HEADING_TAGS):
            if not _HEADING_RE.match(h.get_text(" ", strip=True)):
                continue
            # 부모 요소에 다른 제목이 있으면 사사 절만이 아니다(소속 목록 등이 섞여 오탐 위험).
            parent = h.parent
            text = ""
            if parent is not None and len(parent.find_all(_HEADING_TAGS)) == 1:
                text = parent.get_text(" ", strip=True)
            if not text or len(text) > _MAX_SECTION:
                chunks = [h.get_text(" ", strip=True)]
                for sib in h.find_next_siblings():
                    if sib.name in _HEADING_TAGS:
                        break
                    chunks.append(sib.get_text(" ", strip=True))
                text = " ".join(chunks)
            parts.append(text)
        seen, unique = set(), []
        for p in parts:
            if p and p not in seen:
                seen.add(p)
                unique.append(p)
        return "\n\n".join(unique)
