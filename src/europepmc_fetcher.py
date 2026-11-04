import re
import time

import requests
from bs4 import BeautifulSoup

from .ack_check import find_kisti_ack_mentions, mentions_kist

_SECTION_TITLE_RE = re.compile(r"acknowledg|funding", re.IGNORECASE)
_MAX_EXCERPT = 8000


class EuropePmcFetcher:
    """arXiv 원문이 없는 논문(생명과학·재료 분야 등)의 사사를 Europe PMC 전문 XML에서
    찾는다. PMC 오픈액세스 논문은 JATS XML에 사사(<ack>)와 funding 영역이 따로
    표시돼 있어, 저자 소속(<aff>)과 섞이지 않고 사사만 볼 수 있다.

    반환 형식은 ArxivFetcher.fetch_ack와 같다: (excerpt, note, kisti_mentions, kist_mentioned).
    전문을 못 구하면 kisti_mentions는 None(=미확인).
    """

    def __init__(self, settings):
        cfg = settings.get("europepmc", {})
        self.base_url = cfg.get("base_url", "https://www.ebi.ac.uk/europepmc/webservices/rest").rstrip("/")
        self.delay = cfg.get("request_delay_sec", 0.5)
        self.timeout = cfg.get("timeout_sec", 60)

    def fetch_ack(self, doi, pmcid=None):
        try:
            pmcid = pmcid or self._find_pmcid(doi)
        except requests.RequestException as e:
            return "", f"Europe PMC 검색 오류: {e}", None, False
        if not pmcid:
            return "", "Europe PMC에 전문 없음(PMC 미등재)", None, False

        try:
            resp = self._get(f"{self.base_url}/{pmcid}/fullTextXML")
        except requests.RequestException as e:
            return "", f"Europe PMC 전문 다운로드 오류: {e}", None, False
        if resp.status_code != 200 or not resp.content:
            return "", f"Europe PMC 전문 없음({pmcid}, HTTP {resp.status_code})", None, False

        region = self._ack_region(resp.content)
        if not region:
            return "", f"Europe PMC 전문({pmcid})에 사사·funding 영역 없음", None, False
        # find_kisti_ack_mentions는 LaTeX 주석(%…)을 지우므로 일반 텍스트의 %는 이스케이프한다.
        escaped = region.replace("%", r"\%")
        return (
            region[:_MAX_EXCERPT],
            f"Europe PMC 전문({pmcid})에서 사사 확인",
            find_kisti_ack_mentions(escaped),
            mentions_kist(escaped),
        )

    def _find_pmcid(self, doi):
        resp = self._get(
            f"{self.base_url}/search",
            params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "lite"},
        )
        resp.raise_for_status()
        for r in resp.json().get("resultList", {}).get("result", []):
            if r.get("pmcid") and r.get("inEPMC") == "Y":
                return r["pmcid"]
        return None

    def _get(self, url, params=None):
        try:
            return requests.get(url, params=params, timeout=self.timeout)
        finally:
            time.sleep(self.delay)

    @staticmethod
    def _ack_region(xml_bytes):
        """<ack>, <funding-group>, 제목이 Acknowledg·Funding인 절/주석 텍스트."""
        soup = BeautifulSoup(xml_bytes, "lxml-xml")
        parts = [el.get_text(" ", strip=True) for el in soup.find_all(["ack", "funding-group"])]
        for el in soup.find_all(["sec", "notes", "fn-group", "fn"]):
            title = el.find("title", recursive=False)
            if title and _SECTION_TITLE_RE.search(title.get_text()):
                parts.append(el.get_text(" ", strip=True))
        seen, unique = set(), []
        for p in parts:
            if p and p not in seen:
                seen.add(p)
                unique.append(p)
        return "\n\n".join(unique)
