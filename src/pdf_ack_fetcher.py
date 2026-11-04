import io
import logging
import os
import re
import time

import requests
from pypdf import PdfReader

from .ack_check import find_kisti_ack_mentions, mentions_kist

# 글꼴 인코딩 경고(fontTools 없음 등)가 페이지마다 쏟아져 로그를 덮으므로 끈다.
logging.getLogger("pypdf").setLevel(logging.ERROR)

# 사사·funding 절 제목(줄 첫머리). 저자 소속이 있는 첫 쪽에는 이런 제목이 없다.
_HEADING_RE = re.compile(
    r"^\s*(?:acknowledge?ments?|funding(?:\s+information|\s+sources?)?)\b",
    re.IGNORECASE | re.MULTILINE,
)
# 사사 절 뒤에 오는 절 제목 — 여기서 자른다.
_END_RE = re.compile(
    r"^\s*(?:references|bibliography|author\s+contributions?|competing\s+interests?"
    r"|conflicts?\s+of\s+interest|data\s+availability|additional\s+information)\b",
    re.IGNORECASE | re.MULTILINE,
)
_SECTION_MAX = 4000
_MAX_EXCERPT = 8000
_MAX_PDF_BYTES = 40 * 1024 * 1024
_USER_AGENT = "journal_info_collector/1.0 (+https://github.com/gsdc/journal_info_collector)"


class PdfAckFetcher:
    """arXiv·Europe PMC·원문 HTML에서 사사를 못 구한 논문의 오픈액세스 PDF에서
    사사(Acknowledgements)·Funding 절을 찾는다. PDF 주소는 CrossRef에 출판사가 등록한
    원문 PDF 링크와 OpenAlex가 아는 공개 사본(출판사 OA PDF, 기관 저장소 등)만 쓴다 —
    접근 제한이나 봇 확인 페이지(PMC PDF 등)를 우회하지 않는다. 구독 논문의 PDF는
    받아지지 않으므로(HTTP 오류 또는 PDF 아님) 미확인으로 남는다.

    PDF 텍스트는 줄바꿈·하이픈이 흐트러져 있어, 절 제목 뒤 일정 길이만 사사로 보고
    다음 절 제목(References 등)에서 자른다. 사사 절을 못 찾으면 미확인(None)이다.

    반환 형식은 ArxivFetcher.fetch_ack와 같다: (excerpt, note, kisti_mentions, kist_mentioned).
    """

    def __init__(self, settings):
        oa = settings.get("openalex", {})
        cfg = settings.get("pdf_ack", {})
        self.base_url = oa.get("base_url", "https://api.openalex.org").rstrip("/")
        self.api_key = os.environ.get(oa.get("api_key_env", "OPENALEX_API_KEY"))
        self.delay = cfg.get("request_delay_sec", 1.0)
        self.timeout = cfg.get("timeout_sec", 60)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _USER_AGENT})

    def fetch_ack(self, doi, links=()):
        urls = list(links)
        try:
            urls += self._pdf_urls(doi)
        except requests.RequestException as e:
            if not urls:
                return "", f"OA PDF 주소 조회 오류: {type(e).__name__}", None, False
        urls = list(dict.fromkeys(urls))
        if not urls:
            return "", "OA PDF 없음(OpenAlex 기준)", None, False

        failures = []
        for url in urls:
            text, why = self._pdf_text(url)
            if text is None:
                failures.append(why)
                continue
            region = self._ack_region(text)
            if region:
                escaped = region.replace("%", r"\%")  # LaTeX 주석 제거 규칙에 안 걸리게
                return (
                    region[:_MAX_EXCERPT],
                    f"OA PDF({url})에서 사사 확인",
                    find_kisti_ack_mentions(escaped),
                    mentions_kist(escaped),
                )
            failures.append("사사 절 없음")
        return "", f"OA PDF에서 사사 못 찾음({', '.join(failures)})", None, False

    def _pdf_urls(self, doi):
        params = {"select": "locations"}
        if self.api_key:
            params["api_key"] = self.api_key
        resp = self._session.get(f"{self.base_url}/works/doi:{doi}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        urls = [
            loc["pdf_url"] for loc in resp.json().get("locations") or []
            if loc.get("pdf_url") and not loc["pdf_url"].endswith("_reference.pdf")  # Nature 참고문헌 PDF
        ]
        return list(dict.fromkeys(urls))

    def _pdf_text(self, url):
        try:
            resp = self._session.get(url, timeout=self.timeout, stream=True)
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}"
            data = resp.raw.read(_MAX_PDF_BYTES + 1, decode_content=True)
        except requests.RequestException as e:
            return None, type(e).__name__
        finally:
            time.sleep(self.delay)
        if len(data) > _MAX_PDF_BYTES:
            return None, "PDF 너무 큼"
        if not data.startswith(b"%PDF"):
            return None, "PDF 아님"
        try:
            reader = PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages), ""
        except Exception as e:  # noqa: BLE001 - 깨진 PDF 등
            return None, f"PDF 읽기 실패({type(e).__name__})"

    @staticmethod
    def _ack_region(text):
        parts = []
        for m in _HEADING_RE.finditer(text):
            chunk = text[m.start(): m.start() + _SECTION_MAX]
            end = _END_RE.search(chunk, 20)
            parts.append(chunk[: end.start()] if end else chunk)
        # 줄 끝 하이픈 연결("Informa-\ntion")과 줄바꿈을 정리한다.
        return "\n\n".join(re.sub(r"\s+", " ", re.sub(r"-\n(?=[a-z])", "", p)).strip() for p in parts)
