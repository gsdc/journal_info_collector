import gzip
import io
import re
import tarfile
import time

import requests

_ACK_RE = re.compile(r"acknowledg", re.IGNORECASE)
_EXCERPT_WINDOW = 4000       # 키워드 앞뒤로 자를 글자 수
_MAX_TOTAL_EXCERPT = 8000    # 파일별 발췌를 모두 합친 뒤 자를 최대 길이


class ArxivFetcher:
    """arXiv e-print(LaTeX 소스)를 내려받아 Acknowledgements 절 후보 텍스트를 뽑아낸다.

    KISTI/GSDC 언급 여부의 최종 판정은 이 모듈이 하지 않는다(표기가
    "KISTI" 축약형일 수도, "Korea Institute of Science and Technology
    Information" 전체 명칭일 수도 있어 문구가 커뮤니티/논문마다 다르다).
    여기서는 판정에 필요한 원문 발췌만 만들고, 실제 Y/N 판단은
    /build-report 같은 LLM 단계에서 사람이 검토하듯 판단한다.
    """

    def __init__(self, settings):
        cfg = settings["arxiv"]
        self.url_template = cfg["eprint_url"]
        self.delay = cfg.get("request_delay_sec", 3.0)
        self.timeout = cfg.get("timeout_sec", 60)
        self.filename_hints = [h.lower() for h in cfg.get("ack_filename_hints", [])]

    def fetch_ack_excerpt(self, arxiv_id):
        """반환: (excerpt: str, note: str). 실패 시 excerpt=''이고 note에 사유 기록."""
        if not arxiv_id:
            return "", "arxiv_id 없음"

        raw = self._download(arxiv_id)
        if raw is None:
            return "", "arXiv e-print 다운로드 실패"

        tex_files = self._extract_tex_files(raw)
        if not tex_files:
            return "", "tex 소스 없음(단일 PDF 등)"

        ordered = self._order_by_hint(tex_files)

        chunks = []
        total_len = 0
        for _name, text in ordered:
            for m in _ACK_RE.finditer(text):
                start = max(0, m.start() - 500)
                end = min(len(text), m.start() + _EXCERPT_WINDOW)
                chunk = text[start:end]
                chunks.append(chunk)
                total_len += len(chunk)
                break  # 파일당 첫 매치만 사용
            if total_len >= _MAX_TOTAL_EXCERPT:
                break

        if not chunks:
            return "", "Acknowledgements 관련 텍스트를 찾지 못함"

        excerpt = "\n\n---\n\n".join(chunks)[:_MAX_TOTAL_EXCERPT]
        return excerpt, ""

    def _download(self, arxiv_id):
        url = self.url_template.format(arxiv_id=arxiv_id)
        try:
            resp = requests.get(url, timeout=self.timeout)
        finally:
            time.sleep(self.delay)
        if resp.status_code != 200:
            return None
        return resp.content

    def _extract_tex_files(self, raw_bytes):
        """gzip으로 감싼 tar 아카이브 또는 단일 gzip 파일 두 경우를 모두 처리한다."""
        try:
            decompressed = gzip.decompress(raw_bytes)
        except OSError:
            decompressed = raw_bytes  # 이미 압축 안 된 경우(드묾)

        # tar 아카이브인지 먼저 시도
        try:
            with tarfile.open(fileobj=io.BytesIO(decompressed)) as tar:
                files = []
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    if not member.name.lower().endswith((".tex", ".txt")):
                        continue
                    f = tar.extractfile(member)
                    if f is None:
                        continue
                    text = f.read().decode("utf-8", errors="ignore")
                    files.append((member.name, text))
                return files
        except tarfile.TarError:
            pass

        # 단일 파일(보통 main.tex 하나만 gzip된 경우)
        text = decompressed.decode("utf-8", errors="ignore")
        if "\\documentclass" in text or "\\begin{document}" in text:
            return [("main.tex", text)]
        return []

    def _order_by_hint(self, tex_files):
        if not self.filename_hints:
            return tex_files

        def score(item):
            name = item[0].lower()
            for i, hint in enumerate(self.filename_hints):
                if hint in name:
                    return i
            return len(self.filename_hints)

        return sorted(tex_files, key=score)
