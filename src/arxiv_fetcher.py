import gzip
import io
import re
import tarfile
import time

import requests

from .ack_check import find_kisti_ack_mentions, mentions_kist

_ACK_RE = re.compile(r"acknowledg", re.IGNORECASE)
# 지원 기관을 밝히는 문단의 표지. Belle II처럼 "…was supported by …" 지원 문단이
# "Acknowledgements" 단어보다 앞에 오는 논문이 있어(실제 사례: arXiv:2510.01331,
# GSDC·KISTI가 이 문단에만 있음) 이런 문단도 사사 영역에 넣는다.
_FUNDING_PARA_RE = re.compile(
    r"acknowledg|supported\s+(?:in\s+part\s+)?by|support\s+(?:of|from)|funded\s+by|funding"
    r"|we\s+(?:gratefully\s+)?thank|grants?\b",
    re.IGNORECASE,
)
# 저자 소속·매크로 정의 줄. 소속 목록에 KISTI가 있어도 사사가 아니므로, 지원 문구
# 주변을 볼 때는 이런 줄을 먼저 지운다(DUNE처럼 소속 목록이 빈 줄 없이 한 덩어리라
# 그 안의 "grant" 등에 걸려 소속 전체가 사사로 잡히는 오탐 방지).
_AFFILIATION_LINE_RE = re.compile(
    r"^.*\\(?:affiliation|altaffiliation|address|institute|author|affil|inst"
    r"|newcommand|renewcommand|providecommand|def)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_FUNDING_WINDOW = (300, 4000)  # 지원 문구 앞뒤로 볼 글자 수
_EXCERPT_WINDOW = 4000       # 키워드 앞뒤로 자를 글자 수
_MAX_TOTAL_EXCERPT = 8000    # 파일별 발췌를 모두 합친 뒤 자를 최대 길이


class ArxivFetcher:
    """arXiv e-print(LaTeX 소스)를 내려받아 Acknowledgements 절 후보 텍스트를 뽑아낸다.

    사사 영역에서 KISTI/GSDC 언급을 정규식으로 찾아 Ack Y/N을 정한다. 표기가
    커뮤니티/논문마다 달라("KISTI" 축약형, 전체 기관명 등) 정규식이 놓칠 수 있으므로
    발췌도 함께 남기고, N인 논문은 /build-report(LLM) 단계에서 발췌를 다시 본다.
    """

    def __init__(self, settings):
        cfg = settings["arxiv"]
        self.url_template = cfg["eprint_url"]
        self.delay = cfg.get("request_delay_sec", 3.0)
        self.timeout = cfg.get("timeout_sec", 60)
        self.filename_hints = [h.lower() for h in cfg.get("ack_filename_hints", [])]

    def fetch_ack_excerpt(self, arxiv_id):
        """반환: (excerpt: str, note: str). 실패 시 excerpt=''이고 note에 사유 기록."""
        excerpt, note, _mentions, _kist = self.fetch_ack(arxiv_id)
        return excerpt, note

    def fetch_ack(self, arxiv_id):
        """반환: (excerpt, note, kisti_mentions, kist_mentioned).

        kisti_mentions는 사사 영역 전체(발췌 길이 제한과 무관)에서 찾은
        KISTI/GSDC 언급 문맥 목록이다. 원문을 못 구하면 None — 빈 리스트(=원문은
        봤는데 언급 없음)와 구분하기 위함. CMS처럼 사사 문단이 길면 KISTI가
        발췌 범위 밖에 있을 수 있어서, 발췌와 별도로 검색한다.
        kist_mentioned는 사사에 KIST(별개 기관)가 나오는지 — funder의 KIST DOI가
        오태깅인지 추정하는 데 쓴다.
        """
        if not arxiv_id:
            return "", "arxiv_id 없음", None, False

        raw = self._download(arxiv_id)
        if raw is None:
            return "", "arXiv e-print 다운로드 실패", None, False

        tex_files = self._extract_tex_files(raw)
        if not tex_files:
            return "", "tex 소스 없음(단일 PDF 등)", None, False

        region = self._ack_region(tex_files)
        mentions = find_kisti_ack_mentions(region)
        kist = mentions_kist(region)

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
            if mentions:
                return "", "Acknowledgements 키워드는 없지만 사사 파일에서 KISTI/GSDC 언급 발견", mentions, kist
            return "", "Acknowledgements 관련 텍스트를 찾지 못함", None, False

        excerpt = "\n\n---\n\n".join(chunks)[:_MAX_TOTAL_EXCERPT]
        return excerpt, "", mentions, kist

    def _ack_region(self, tex_files):
        """사사 판정에 쓸 원문 범위:
        - 파일명이 사사 파일 힌트(예: ALICE fa_*)에 맞으면 파일 전체
        - 그 외 파일은 첫 "acknowledg" 이후 끝까지, 그리고 그보다 앞에 있더라도
          지원·감사 문구(supported by, funding, grant, we thank …) 주변
        저자 소속에 KISTI가 나오는 경우(사사 아님)를 피하려고 문서 전체는 보지 않고,
        지원 문구 주변을 볼 때도 소속·매크로 정의 줄은 지운다."""
        parts = []
        for name, text in tex_files:
            lname = name.lower()
            if any(h in lname for h in self.filename_hints):
                parts.append(text)
                continue
            m = _ACK_RE.search(text)
            head = _AFFILIATION_LINE_RE.sub("", text[: m.start()] if m else text)
            before, after = _FUNDING_WINDOW
            for fm in _FUNDING_PARA_RE.finditer(head):
                parts.append(head[max(0, fm.start() - before): fm.start() + after])
            if m:
                parts.append(text[m.start():])
        return "\n".join(parts)

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
