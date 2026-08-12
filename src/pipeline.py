import json
import sys

from . import config
from .ack_check import check_ack_via_funder
from .auth_check import check_auth
from .arxiv_fetcher import ArxivFetcher
from .crossref_client import CrossrefClient
from .date_filter import in_range
from .downloaders.registry import get_downloader
from .inspire_client import InspireClient


def _resolve_issn(inspire_record, crossref_record, settings):
    if crossref_record and crossref_record.issn_list:
        return crossref_record.issn_list[0]
    if inspire_record and inspire_record.issn:
        return inspire_record.issn
    fallback_map = settings.get("journal_issn_fallback", {})
    for journal in (
        inspire_record.journal_title if inspire_record else None,
        crossref_record.journal_title if crossref_record else None,
    ):
        if journal and journal in fallback_map:
            # 값 형식은 "ISSN,EISSN" (EISSN이 없는 저널은 "ISSN"만) — ISSN을 우선 사용.
            issn, _, eissn = fallback_map[journal].partition(",")
            return issn.strip() or eissn.strip()
    return ""


def _resolve_inspire_record(inspire_client, doi, arxiv_id):
    """DOI가 있으면 DOI로, 없거나 못 찾으면 arXiv ID로 INSPIRE 레코드를 찾는다.
    (source 페이지가 아직 'submitted'/'accepted'로 보여도 그 사이 실제로는
    출판돼 INSPIRE에 DOI가 잡혀 있을 수 있어, 이 경로로 '숨은 출판'을 잡아낸다.)

    반환: (inspire_record, match_status, note)
    """
    if doi:
        try:
            record = inspire_client.get_by_doi(doi)
        except Exception as e:  # noqa: BLE001
            return None, "error", f"INSPIRE 조회 오류: {e}"
        if record is not None:
            return record, "ok", ""

    if arxiv_id:
        try:
            record = inspire_client.get_by_arxiv(arxiv_id)
        except Exception as e:  # noqa: BLE001
            return None, "error", f"INSPIRE(arXiv) 조회 오류: {e}"
        if record is not None:
            if doi:
                return record, "matched_via_arxiv_doi_pending", "INSPIRE에 DOI가 아직 반영 안 돼 arXiv ID로 레코드를 찾음"
            return record, "matched_via_arxiv_no_source_doi", "원본 페이지엔 DOI 없음 - arXiv ID로 INSPIRE 레코드를 찾음"

    if doi:
        return None, "inspire_not_found", "INSPIRE-HEP에서 이 DOI를 찾지 못함"
    return None, "no_doi_no_arxiv_match", "DOI도 없고 arXiv ID로도 INSPIRE에서 못 찾음(아직 출판 전으로 보임)"


def build_candidates(community_names=None, year=None, limit_per_community=None):
    """모든(또는 지정된) 커뮤니티를 다운로드하고, INSPIRE-HEP / CrossRef 조회 +
    Auth 판정 + Ack 발췌까지 마친 candidate 목록을 만든다.

    DOI가 아직 없는(투고/게재확정 단계) 논문도 일단 리스트에 포함시키되,
    arXiv ID로 INSPIRE를 다시 조회해서 그 사이 출판됐는지 확인한다. 최종적으로
    이걸 리포트에 넣을지는 candidate의 `publication_status`를 보고 다음
    단계(/build-report)에서 판단한다 — 여기서는 임의로 빼지 않는다.

    Ack의 Y/N 최종 판정과 커뮤니티-INSPIRE 정보 불일치 검토는 여기서 하지
    않고 다음 단계(LLM)에 넘긴다.
    """
    settings = config.load_settings()
    communities = config.load_communities()
    responsible_authors = config.load_responsible_authors()

    if community_names:
        communities = [c for c in communities if c["name"] in community_names]

    inspire_client = InspireClient(settings)
    crossref_client = CrossrefClient(settings)
    arxiv_fetcher = ArxivFetcher(settings)
    kisti_funder_doi = settings["crossref"]["kisti_funder_doi"]

    candidates = []
    for community_cfg in communities:
        name = community_cfg["name"]
        print(f"[fetch] {name}: downloading list from {community_cfg['url']}", file=sys.stderr)
        downloader = get_downloader(community_cfg)
        raw_pubs = downloader.fetch()

        raw_pubs = [
            raw
            for raw in raw_pubs
            if in_range(raw.pub_date_raw or raw.submitted_date_raw, year=year)
        ]

        if limit_per_community:
            raw_pubs = raw_pubs[:limit_per_community]
        print(f"[fetch] {name}: {len(raw_pubs)} publications in scope", file=sys.stderr)

        responsible_list = responsible_authors.get(name, [])

        for i, raw in enumerate(raw_pubs, 1):
            print(f"[fetch] {name} {i}/{len(raw_pubs)}: {raw.doi or raw.arxiv_id} ({raw.status})", file=sys.stderr)

            inspire_record, match_status, note = _resolve_inspire_record(
                inspire_client, raw.doi, raw.arxiv_id
            )

            # source 페이지엔 DOI가 없어도 INSPIRE가 이미 알고 있으면 그 DOI를 쓴다.
            effective_doi = raw.doi or (inspire_record.doi if inspire_record else None)
            publication_status = "published" if effective_doi else raw.status

            crossref_record = None
            if effective_doi:
                try:
                    crossref_record = crossref_client.get_by_doi(effective_doi)
                except Exception as e:  # noqa: BLE001
                    note = (note + f" / CrossRef 조회 오류: {e}").strip(" /")

            inspire_authors = inspire_record.authors if inspire_record else []
            crossref_authors = crossref_record.authors if crossref_record else []
            if inspire_record is None:
                auth_status, auth_note = "확인불가", "INSPIRE 레코드를 찾지 못해 저자 대조 불가"
            else:
                auth_status, auth_note = check_auth(responsible_list, inspire_authors, crossref_authors)

            ack_funder_status, ack_funder_note = check_ack_via_funder(crossref_record, kisti_funder_doi)

            ack_excerpt, ack_note = "", ""
            if ack_funder_status != "Y":
                # CrossRef funder 목록은 (출판사가 자동 텍스트마이닝으로 채우는 경우가
                # 많아) 있어도 불완전할 수 있다 — 실제로 원문에 있는 기관이 통째로
                # 빠진 사례를 확인함. 따라서 "N"(목록은 있지만 KISTI 없음)도 거짓
                # 음성일 수 있어, funder DOI로 확실히 "Y"가 나온 경우만 신뢰하고
                # 그 외에는(N 포함, 아직 미출판이라 crossref 자체가 없는 경우 포함)
                # 항상 arXiv 원문으로 다시 확인한다.
                ack_excerpt, ack_note = arxiv_fetcher.fetch_ack_excerpt(raw.arxiv_id)

            candidates.append(
                {
                    "community": name,
                    "doi": effective_doi,
                    "source_doi": raw.doi,  # 원본 페이지에 실제로 있던 DOI(없으면 None)
                    "arxiv_id": raw.arxiv_id,
                    "raw_title": raw.title,
                    "raw_journal_citation": raw.journal_citation_raw,
                    "raw_pub_date": raw.pub_date_raw,
                    "raw_status": raw.status,  # 원본 페이지 기준 상태(published/accepted/submitted/...)
                    "publication_status": publication_status,  # INSPIRE 재확인까지 반영한 최종 상태
                    "inspire_title": inspire_record.title if inspire_record else None,
                    "journal": (
                        (inspire_record.journal_title if inspire_record else None)
                        or (crossref_record.journal_title if crossref_record else None)
                    ),
                    "issn": _resolve_issn(inspire_record, crossref_record, settings),
                    "volume": (
                        (inspire_record.journal_volume if inspire_record else None)
                        or (crossref_record.volume if crossref_record else None)
                        or ""
                    ),
                    "page": (
                        (inspire_record.page if inspire_record else None)
                        or (crossref_record.page if crossref_record else None)
                        or ""
                    ),
                    "date": (
                        (inspire_record.pub_date if inspire_record else None)
                        or (crossref_record.pub_date if crossref_record else None)
                        or raw.pub_date_raw
                        or ""
                    ),
                    "collaborations": inspire_record.collaborations if inspire_record else [],
                    "auth_status": auth_status,
                    "auth_note": auth_note,
                    "ack_funder_status": ack_funder_status,
                    "ack_funder_note": ack_funder_note,
                    "ack_excerpt": ack_excerpt,
                    "ack_note": ack_note,
                    "match_status": match_status,
                    "note": note,
                }
            )

    return candidates, settings


def save_candidates(candidates, settings):
    path = settings["output"]["candidates_path"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    print(f"[fetch] wrote {len(candidates)} candidates to {path}", file=sys.stderr)
    return path
