import difflib
import hashlib
import json
import sys
from datetime import date

from . import config
from .ack_check import check_ack_via_funder
from .auth_check import check_auth
from .arxiv_fetcher import ArxivFetcher
from .cache import SqliteCache
from .crossref_client import CrossrefClient
from .crossref_client import is_complete as crossref_is_complete
from .date_filter import in_range, parse_date
from .downloaders.registry import get_downloader
from .inspire_client import InspireClient, normalize_title
from .jcr import load_jcr_table, scie_fields
from .models import RawPublication
from .reconcile import (
    inspire_published_records,
    inspire_records_by_doi,
    OpenAlexTitleLookup,
    collab_query,
    is_collab_paper,
    min_authors,
    matching_community,
    openalex_existing_dois,
    openalex_kisti_works,
)


def _issn_pairs(settings):
    """settings의 journal_issn_fallback을 ISSN으로 찾을 수 있게 바꾼다.
    값 형식 "인쇄판,전자판" — 전자판 전용 저널은 값 하나(인쇄판 없음).
    반환: {저널명 또는 ISSN: (인쇄판, 전자판)}"""
    pairs = {}
    for journal, value in (settings.get("journal_issn_fallback") or {}).items():
        parts = [p.strip() for p in str(value).split(",")]
        pair = (parts[0], parts[1]) if len(parts) > 1 else ("", parts[0])
        pairs[journal] = pair
        for issn in pair:
            if issn:
                pairs[issn] = pair
    return pairs


def _resolve_issn(inspire_record, crossref_record, settings):
    """(ISSN, 전자판 ISSN). ISSN은 전자판으로 출판됐더라도 인쇄판을 우선 기입하고,
    인쇄판이 없는 전자판 전용 저널(JHEP 등)만 전자판을 쓴다.

    CrossRef·INSPIRE는 인쇄판이 있는 저널도 전자판만 알려주는 경우가 있어서
    (예: Eur.Phys.J.C — 둘 다 1434-6052만 줌, 인쇄판 1434-6044), 검증해 둔
    journal_issn_fallback 쌍을 ISSN(또는 저널명)으로 먼저 찾는다.
    """
    found = []
    if crossref_record:
        found += [crossref_record.issn_print, crossref_record.issn_electronic, *crossref_record.issn_list]
    if inspire_record:
        found += [inspire_record.issn, inspire_record.issn_online]
    found = [i for i in found if i]

    pairs = _issn_pairs(settings)
    names = [
        inspire_record.journal_title if inspire_record else None,
        crossref_record.journal_title if crossref_record else None,
    ]
    for key in found + [n for n in names if n]:
        if key in pairs:
            printed, electronic = pairs[key]
            return printed or electronic, electronic

    printed = (crossref_record.issn_print if crossref_record else None) or (
        inspire_record.issn if inspire_record and inspire_record.issn != inspire_record.issn_online else None
    )
    electronic = (crossref_record.issn_electronic if crossref_record else None) or (
        inspire_record.issn_online if inspire_record else None
    )
    if printed or electronic:
        return printed or electronic, electronic or ""
    return (found[0], "") if found else ("", "")


def _resolve_pub_date(inspire_record, crossref_record, raw):
    """게재일은 CrossRef와 INSPIRE 중 더 이른 날짜(=온라인 게재일)를 쓴다.

    출판사에 따라 CrossRef에 온라인 게재일 없이 권호 발행일만 등록돼 있어
    실제보다 늦은 날짜가 나오는 경우가 있다(예: CMS Phys.Rept.
    10.1016/j.physrep.2026.07.007 — CrossRef는 2027년 권호, INSPIRE는
    2026-09-17 온라인 게재). 둘 다 없으면 커뮤니티 원본 페이지 값을 쓴다.
    """
    found = [
        (parse_date(d), d)
        for d in (
            crossref_record.pub_date if crossref_record else None,
            inspire_record.pub_date if inspire_record else None,
        )
        if d and parse_date(d)
    ]
    if found:
        return min(found)[1]
    return raw.pub_date_raw or ""


def _title_similarity(a, b):
    return difflib.SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def _fix_shared_ids(raw_pubs, name, inspire_client):
    """같은 커뮤니티 안에서 서로 다른 행이 같은 arXiv ID(또는 DOI)를 갖고 있으면
    원본 입력 오류다(실제 사례: CMS CSV의 HIN-25-010과 SUS-21-005가 모두
    arXiv:2606.09567 — 실제로는 SUS 논문 번호, HIN은 2606.02285).
    INSPIRE 레코드 제목과 가장 비슷한 행만 그 ID를 쓰고, 나머지는 정규화 제목이
    정확히 같은 INSPIRE 레코드로 ID를 고친다. 못 찾으면 ID를 비우고 메모를 남긴다.
    (제목만 다르다고 고치지는 않는다 — Belle II 등은 원본 제목이 내부 메모 제목이라
    저널 제목과 원래 많이 다르다.)"""
    for attr in ("arxiv_id", "doi"):
        groups = {}
        for raw in raw_pubs:
            value = getattr(raw, attr)
            if value:
                groups.setdefault(value.lower(), []).append(raw)
        for value, rows in groups.items():
            if len(rows) < 2:
                continue
            try:
                record = (
                    inspire_client.get_by_arxiv(rows[0].arxiv_id) if attr == "arxiv_id"
                    else inspire_client.get_by_doi(rows[0].doi)
                )
            except Exception:  # noqa: BLE001
                record = None
            ref_title = record.title if record else None
            rows.sort(key=lambda r: _title_similarity(r.title, ref_title) if ref_title else 0, reverse=True)
            for raw in rows[1:]:
                found = None
                try:
                    found = inspire_client.find_by_exact_title(raw.title)
                except Exception:  # noqa: BLE001
                    pass
                new_value = (found or {}).get(attr)
                if new_value and new_value.lower() != value:
                    setattr(raw, attr, new_value)
                    if attr == "arxiv_id" and found.get("doi") and not raw.doi:
                        raw.doi = found["doi"]
                    raw.source_note = (
                        f"원본 {attr}({value})가 다른 논문과 겹쳐 제목으로 찾은 {new_value}로 고침"
                    )
                else:
                    setattr(raw, attr, None)
                    raw.source_note = f"원본 {attr}({value})가 다른 논문과 겹침(입력 오류 의심) - 무시"
                print(f"[fetch] WARN {name}: {raw.source_note}: {raw.title[:70]}", file=sys.stderr)


def _drop_impossible_pub_date(raw):
    """원본 페이지의 게재일이 투고일보다 이르면 입력 오류로 보고 버린다
    (실제 사례: CMS CSV의 EXO-25-007이 투고 2026-04-07인데 게재 2016-06-24).
    게재일을 버리면 연도 판단은 CrossRef/INSPIRE 조회 결과로 하게 된다."""
    pub, sub = parse_date(raw.pub_date_raw), parse_date(raw.submitted_date_raw)
    if pub and sub and pub < sub:
        print(
            f"[fetch] WARN {raw.community}: 원본 게재일({raw.pub_date_raw})이 투고일"
            f"({raw.submitted_date_raw})보다 이름 - 무시하고 조회로 판단: {raw.title[:80]}",
            file=sys.stderr,
        )
        raw.pub_date_raw = None


def _needs_deferred_date_filter(raw):
    """커뮤니티 페이지에 실제 게재일이 없는 경우(pub_date_raw 없음) — DOI가
    이미 있는 경우(예: Belle II는 게재일 컬럼 자체가 없어 항상 이렇다)뿐 아니라,
    DOI가 아직 없어도(원본 페이지엔 "accepted"로만 보이는 경우) arXiv ID로
    INSPIRE를 재조회하면 이미 게재 확정(DOI 발급)된 걸 발견하는 경우가 흔하다
    (`_resolve_inspire_record`의 '숨은 출판' 탐지와 같은 이유). 제출일과 실제
    게재일 사이에 연도가 바뀌는 일이 잦아서, 조회할 단서(DOI 또는 arXiv ID)가
    있으면 지금 연도로 걸러내지 않고 CrossRef/INSPIRE 조회 결과로 다시
    판단한다. 조회할 단서가 전혀 없으면(둘 다 없음) 거를 수밖에 없다."""
    return not raw.pub_date_raw and bool(raw.doi or raw.arxiv_id)


# 게재일을 조회로 확인하는(deferred) 논문 중, 투고/공개일이 요청 연도보다 이만큼
# 이상 이전이면 대상에서 뺀다. LIGO처럼 전체 이력이 한 페이지에 있는 경우 수백 건을
# 매번 조회하지 않기 위함(투고 후 2년 넘어 게재되는 경우는 거의 없다).
_MAX_SUBMIT_TO_PUBLISH_YEARS = 2


def _submitted_recently(raw, year):
    if year is None:
        return True
    return in_range(raw.submitted_date_raw, since=date(year - _MAX_SUBMIT_TO_PUBLISH_YEARS, 1, 1))


# 판정 로직이 바뀌면 올린다 — 저장된 확정 결과 중 버전이 다른 것은 다시 판정한다.
EVIDENCE_VERSION = 7


def _evidence_keys(doi, arxiv_id):
    """확정된 유발논문 근거를 찾고 저장할 캐시 키. 원본 페이지엔 DOI가 없고
    arXiv ID만 있다가 나중에 DOI가 붙는 경우가 있어 두 키 모두 쓴다."""
    keys = []
    if doi:
        keys.append(f"doi:{doi.lower()}")
    if arxiv_id:
        keys.append(f"arxiv:{arxiv_id}")
    return keys


def _responsible_fingerprint(responsible_list):
    """담당자 명단이 바뀌면 저장해 둔 Auth 판정이 무효가 되므로 지문으로 구분한다."""
    blob = json.dumps(responsible_list, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _is_evidence_final(candidate, crossref_record):
    """유발논문 근거(저자·사사·Funder)가 출판 확정 상태에서 모두 판정됐는지.

    - 출판 확정: DOI가 있고, CrossRef에 그 DOI가 등록돼 있으며 ISSN·권·페이지·
      게재일이 다 채워져 있음(출판사가 실제 게재 정보를 등록했다는 뜻).
    - Auth: INSPIRE 저자 목록과 대조를 마침("확인불가"가 아님).
    - Ack: arXiv 원문으로 Y/N을 판정함("미확인"이 아님).
    Funder는 위 CrossRef 레코드에서 바로 판정되므로 따로 조건이 없다.
    이 조건을 만족하면 이후 조사에서 다시 조회하지 않는다.
    """
    return (
        candidate["publication_status"] == "published"
        and crossref_record is not None
        and crossref_is_complete(crossref_record)
        and candidate["auth_status"] != "확인불가"
        and candidate["ack_text_status"] in ("Y", "N")
    )


def _outside_year(date_str, year, published=True):
    """조회로 게재일을 판단하는 논문이 요청 연도 밖인지.

    DOI가 있는데(출판된 것으로 보이는데) 게재일을 끝내 알 수 없으면(예: CERN
    보고서처럼 CrossRef·INSPIRE에 날짜가 없는 DOI) 연도를 확인할 수 없으므로 밖으로
    본다 — in_range()는 날짜를 못 읽으면 포함시키기 때문에 따로 처리한다. 아직
    미출판인 논문은 '출판 미확인' 목록에 남도록 기존처럼 포함한다."""
    if parse_date(date_str) is None:
        return published
    return not in_range(date_str, year=year)


def _summarize_audit(name, downloader, kept):
    """다운로더 기록을 요약하고, 사람이 봐야 하는 누락이 있으면 경고를 출력한다."""
    audit = downloader.audit
    expected, unexpected = {}, []
    for d in audit["dropped"]:
        if d["expected"]:
            expected[d["reason"]] = expected.get(d["reason"], 0) + 1
        else:
            unexpected.append(d)
    summary = {
        "community": name,
        "rows": audit["rows"],
        "kept": kept,
        "dropped_expected": expected,
        "dropped_unexpected": unexpected,
        "truncated": audit["truncated"],
    }
    print(
        f"[audit] {name}: 페이지 {audit['rows']}행 -> 수집 {kept}, "
        f"의도된 제외 {sum(expected.values())}, 확인필요 제외 {len(unexpected)}",
        file=sys.stderr,
    )
    if audit["rows"] == 0:
        print(f"[audit] WARN {name}: 페이지에서 한 행도 읽지 못함 - 사이트 구조 변경 의심", file=sys.stderr)
    if audit["truncated"]:
        print(f"[audit] WARN {name}: 페이지 수 상한에 걸려 끝까지 읽지 못함", file=sys.stderr)
    for d in unexpected:
        print(f"[audit] WARN {name}: 제외됨 - {d['reason']}: {d['title']}", file=sys.stderr)
    return summary


def _raw_from_inspire(metadata, community, origin):
    """INSPIRE 레코드로 RawPublication을 만든다(커뮤니티 페이지에 없는 논문 보충용)."""
    titles = metadata.get("titles") or [{}]
    dois = metadata.get("dois") or []
    eprints = metadata.get("arxiv_eprints") or []
    imprints = metadata.get("imprints") or []
    return RawPublication(
        community=community,
        title=titles[0].get("title", ""),
        arxiv_id=eprints[0].get("value") if eprints else None,
        doi=dois[0]["value"] if dois else None,
        pub_date_raw=imprints[0].get("date") if imprints else None,
        status="published",
        source_url="https://inspirehep.net",
        origin=origin,
    )


def _page_keys(raw_pubs):
    dois = {r.doi.lower() for r in raw_pubs if r.doi}
    arxivs = {r.arxiv_id for r in raw_pubs if r.arxiv_id}
    titles = {normalize_title(r.title) for r in raw_pubs if r.title}
    return dois, arxivs, titles


def _in_keys(keys, metadata):
    dois, arxivs, titles = keys
    if any(d["value"].lower() in dois for d in metadata.get("dois") or []):
        return True
    if any(e.get("value") in arxivs for e in metadata.get("arxiv_eprints") or []):
        return True
    return any(normalize_title(t.get("title")) in titles for t in metadata.get("titles") or [])


def _supplement(raw_pubs, name, year, collab_records, openalex_records, min_author_count):
    """커뮤니티 페이지에 없는 그 해 컬래버레이션 논문을 INSPIRE 출판 목록과
    OpenAlex(KISTI 검색) 결과로 보충한다. 페이지 갱신 지연 등으로 빠진 논문을
    결과에 포함시키고, 출처 컬럼에 어디서 찾았는지 남긴다."""
    keys = _page_keys(raw_pubs)
    added = []
    for records, origin in ((collab_records, "inspire"), (openalex_records, "openalex")):
        for md in records:
            if not is_collab_paper(md, year, min_author_count) or _in_keys(keys, md):
                continue
            raw = _raw_from_inspire(md, name, origin)
            added.append(raw)
            keys = _page_keys(raw_pubs + added)
    if added:
        print(
            f"[fetch] {name}: 커뮤니티 페이지에 없는 논문 보충 - "
            f"INSPIRE {sum(r.origin == 'inspire' for r in added)}건, "
            f"OpenAlex {sum(r.origin == 'openalex' for r in added)}건",
            file=sys.stderr,
        )
    return raw_pubs + added


_PAGE_STATUS_LABEL = {
    "published": "출판",
    "accepted": "게재확정 표기",
    "submitted": "투고 표기",
    "preprint": "프리프린트 표기",
}


def _source_label(c, community_cfg, openalex_dois, openalex_existing=None):
    """출처 컬럼: 커뮤니티 공식 목록 / INSPIRE / OpenAlex 각각에서 이 논문이
    확인되는지. 판정의 근거를 사람이 볼 수 있게 남긴다.
    OpenAlex: "O" 논문이 있음, "O(KISTI)" KISTI funder·소속·담당자 검색에도 잡힘,
    "O(출판 확인)" INSPIRE로 확인 못 한 출판을 OpenAlex 제목 검색으로 확인, "X" 없음,
    "-" 조회 실패."""
    origin = c.get("origin", "community")
    if origin != "community":
        page = "없음"
    elif community_cfg.get("downloader") == "inspire_collab":
        page = "공식목록 없음"
    else:
        page = _PAGE_STATUS_LABEL.get(c.get("raw_status"), c.get("raw_status") or "?")

    if c.get("inspire_title") is None and not c.get("collaborations"):
        inspire = "X"
    elif c.get("match_status") == "matched_via_title":
        inspire = "O(제목 대조)"
    else:
        inspire = "O"

    doi = (c.get("doi") or "").lower()
    if c.get("match_status") == "matched_via_openalex":
        openalex = "O(출판 확인)"
    elif openalex_dois is not None and doi in openalex_dois:
        openalex = "O(KISTI)"
    elif openalex_existing is not None and doi in openalex_existing:
        openalex = "O"
    elif openalex_existing is None and openalex_dois is None:
        openalex = "-"
    else:
        openalex = "X"

    label = f"커뮤니티:{page} / INSPIRE:{inspire} / OpenAlex:{openalex}"
    if origin == "inspire":
        label += " (INSPIRE 목록에서 보충)"
    elif origin == "openalex":
        label += " (OpenAlex에서만 발견)"
    return label


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

    cache = SqliteCache.from_settings(settings)
    inspire_client = InspireClient(settings, cache=cache)
    crossref_client = CrossrefClient(settings, cache=cache)
    arxiv_fetcher = ArxivFetcher(settings)
    kisti_funder_doi = settings["crossref"]["kisti_funder_doi"]

    openalex_dois, openalex_md = None, {}
    if year is not None and not limit_per_community:
        orcids = [p["orcid"] for people in responsible_authors.values() for p in people if p.get("orcid")]
        try:
            works = openalex_kisti_works(settings, orcids, year)
            openalex_dois = set(works)
            openalex_md = inspire_records_by_doi(settings, works)
            path = settings["output"].get("openalex_path", "data/openalex_kisti.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {"year": year, "works": {d: {**w["work"], "via": sorted(w["via"])} for d, w in works.items()}},
                    f, ensure_ascii=False, indent=1,
                )
            print(f"[fetch] OpenAlex KISTI 관련 {year}년 논문 {len(works)}건 -> {path}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[fetch] WARN OpenAlex 조회 실패({e}) - OpenAlex 보충/출처 표기 생략", file=sys.stderr)

    openalex_titles = OpenAlexTitleLookup(settings)
    if year is not None and not openalex_titles.enabled:
        print(
            "[fetch] OpenAlex API 키(OPENALEX_API_KEY) 없음 - 출판 미확인 논문의 OpenAlex 제목 검색 생략",
            file=sys.stderr,
        )

    candidates = []
    audits = []
    for community_cfg in communities:
        name = community_cfg["name"]
        print(f"[fetch] {name}: downloading list from {community_cfg['url']}", file=sys.stderr)
        downloader = get_downloader(community_cfg)
        raw_pubs = downloader.fetch()
        audits.append(_summarize_audit(name, downloader, len(raw_pubs)))
        for raw in raw_pubs:
            _drop_impossible_pub_date(raw)
        _fix_shared_ids(raw_pubs, name, inspire_client)

        query = collab_query(community_cfg)
        collab_records = []
        if query and year is not None:
            try:
                collab_records = inspire_published_records(settings, query, year)
            except Exception as e:  # noqa: BLE001
                print(f"[fetch] {name}: INSPIRE 출판 목록 조회 실패({e}) - 보충/제목 대조 생략", file=sys.stderr)
        if year is not None and not limit_per_community:
            community_openalex = [md for md in openalex_md.values() if matching_community(communities, md) == name]
            raw_pubs = _supplement(
                raw_pubs, name, year, collab_records, community_openalex, min_authors(community_cfg)
            )

        # arXiv 레코드에 DOI가 없을 때 제목으로 출판본을 찾기 위한 {정규화 제목: DOI}
        # 정규화 제목이 같은 출판 논문이 둘 이상이면 어느 쪽인지 모르므로 쓰지 않는다.
        title_dois = {}
        for md in collab_records:
            dois = md.get("dois") or []
            if dois:
                for t in md.get("titles") or []:
                    key = normalize_title(t.get("title"))
                    if key:
                        title_dois.setdefault(key, set()).add(dois[0]["value"].lower())
        published_by_title = {k: next(iter(v)) for k, v in title_dois.items() if len(v) == 1}

        deferred = [
            raw for raw in raw_pubs if _needs_deferred_date_filter(raw) and _submitted_recently(raw, year)
        ]
        deferred_ids = {id(raw) for raw in deferred}
        immediate = [
            raw
            for raw in raw_pubs
            if id(raw) not in deferred_ids
            and not _needs_deferred_date_filter(raw)
            and in_range(raw.pub_date_raw or raw.submitted_date_raw, year=year)
        ]
        raw_pubs = immediate + deferred

        if limit_per_community:
            raw_pubs = raw_pubs[:limit_per_community]
        print(f"[fetch] {name}: {len(raw_pubs)} publications in scope", file=sys.stderr)

        responsible_list = responsible_authors.get(name, [])
        responsible_fp = _responsible_fingerprint(responsible_list)

        for i, raw in enumerate(raw_pubs, 1):
            print(f"[fetch] {name} {i}/{len(raw_pubs)}: {raw.doi or raw.arxiv_id} ({raw.status})", file=sys.stderr)

            final = None
            for key in _evidence_keys(raw.doi, raw.arxiv_id):
                stored = cache.get("evidence", key)
                if (
                    stored is not None
                    and stored.get("version") == EVIDENCE_VERSION
                    and stored.get("responsible_fp") == responsible_fp
                ):
                    final = stored["candidate"]
                    break
            if final is not None:
                if id(raw) in deferred_ids and year is not None and _outside_year(final["date"], year):
                    continue
                print("[fetch]   -> 유발논문 근거 확정됨(재조사 생략)", file=sys.stderr)
                final["origin"] = raw.origin
                candidates.append(final)
                continue

            inspire_record, match_status, note = _resolve_inspire_record(
                inspire_client, raw.doi, raw.arxiv_id
            )

            # source 페이지엔 DOI가 없어도 INSPIRE가 이미 알고 있으면 그 DOI를 쓴다.
            effective_doi = raw.doi or (inspire_record.doi if inspire_record else None)

            # 그래도 DOI가 없으면 INSPIRE 출판 목록에서 같은 제목을 찾는다(INSPIRE가
            # 출판사 레코드와 arXiv 레코드를 아직 합치지 않은 경우).
            if not effective_doi and published_by_title:
                titles = [raw.title, inspire_record.title if inspire_record else None]
                title_doi = next(
                    (published_by_title[k] for k in map(normalize_title, titles) if k in published_by_title),
                    None,
                )
                if title_doi:
                    effective_doi = title_doi
                    try:
                        inspire_record = inspire_client.get_by_doi(title_doi) or inspire_record
                    except Exception as e:  # noqa: BLE001
                        note = (note + f" / INSPIRE 조회 오류: {e}").strip(" /")
                    match_status = "matched_via_title"
                    note = (note + " / arXiv 레코드엔 DOI가 없어 INSPIRE 출판 목록에서 제목으로 찾음").strip(" /")

            # INSPIRE에서도 출판이 확인 안 되면 OpenAlex에서 제목으로 출판본을 찾는다(API 키 있을 때).
            if not effective_doi and openalex_titles.enabled and year is not None:
                try:
                    oa_doi = openalex_titles.find_published_doi(raw.title, year - 1)
                except Exception as e:  # noqa: BLE001
                    oa_doi = None
                    note = (note + f" / OpenAlex 제목 검색 오류: {e}").strip(" /")
                if oa_doi:
                    effective_doi = oa_doi
                    try:
                        inspire_record = inspire_client.get_by_doi(oa_doi) or inspire_record
                    except Exception as e:  # noqa: BLE001
                        note = (note + f" / INSPIRE 조회 오류: {e}").strip(" /")
                    match_status = "matched_via_openalex"
                    note = (note + " / INSPIRE에서 출판 확인이 안 돼 OpenAlex에서 제목으로 출판본을 찾음").strip(" /")
            publication_status = "published" if effective_doi else raw.status

            crossref_record = None
            if effective_doi:
                try:
                    crossref_record = crossref_client.get_by_doi(effective_doi)
                except Exception as e:  # noqa: BLE001
                    note = (note + f" / CrossRef 조회 오류: {e}").strip(" /")

            resolved_date = _resolve_pub_date(inspire_record, crossref_record, raw)
            if id(raw) in deferred_ids and year is not None and _outside_year(
                resolved_date, year, published=publication_status == "published"
            ):
                continue  # 실제(CrossRef 기준) 게재일로 보면 요청한 연도가 아님

            issn, eissn = _resolve_issn(inspire_record, crossref_record, settings)

            inspire_authors = inspire_record.authors if inspire_record else []
            crossref_authors = crossref_record.authors if crossref_record else []
            if inspire_record is None and crossref_authors:
                # INSPIRE에 없는 분야(예: ENIGMA 지구물리)는 CrossRef 저자 목록으로 대조한다.
                auth_status, auth_note = check_auth(responsible_list, [], crossref_authors)
                auth_note = (auth_note + " (INSPIRE 없음 - CrossRef 저자 기준)").strip()
            elif inspire_record is None:
                auth_status, auth_note = "확인불가", "INSPIRE 레코드를 찾지 못해 저자 대조 불가"
            else:
                auth_status, auth_note = check_auth(responsible_list, inspire_authors, crossref_authors)

            # KISTI 유발 논문의 근거는 저자(Auth)·사사 원문(Ack)·funder ID(Funder)
            # 세 가지를 각각 따로 표기해야 하므로, funder ID로 이미 확인된 논문도
            # 사사 원문을 별도로 확인한다. 미출판 논문은 최종 보고서에서 빠지므로
            # arXiv 다운로드(건당 수 초)를 생략한다.
            ack_excerpt, ack_note, ack_mentions, ack_mentions_kist = "", "", None, False
            if publication_status == "published":
                ack_excerpt, ack_note, ack_mentions, ack_mentions_kist = arxiv_fetcher.fetch_ack(raw.arxiv_id)
            else:
                ack_note = "미출판이라 원문 확인 생략"
            if ack_mentions:
                ack_text_status = "Y"
            elif ack_mentions is not None or ack_excerpt:
                ack_text_status = "N"
            else:
                ack_text_status = "미확인"

            # 사사 원문 결과를 함께 넘겨 KIST DOI 오태깅 여부를 추정한다.
            ack_funder_status, ack_funder_note = check_ack_via_funder(
                crossref_record, kisti_funder_doi, ack_mentions, ack_mentions_kist
            )

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
                    "issn": issn,
                    "eissn": eissn,
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
                    "date": resolved_date,
                    "collaborations": inspire_record.collaborations if inspire_record else [],
                    "auth_status": auth_status,
                    "auth_note": auth_note,
                    "ack_funder_status": ack_funder_status,
                    "ack_funder_note": ack_funder_note,
                    "ack_text_status": ack_text_status,  # 사사 원문 KISTI/GSDC 언급: Y/N/미확인
                    "ack_text_evidence": ack_mentions or [],
                    "ack_excerpt": ack_excerpt,
                    "ack_note": ack_note,
                    "match_status": match_status,
                    "note": " / ".join(n for n in (raw.source_note, note) if n),
                    "origin": raw.origin,
                }
            )
            if _is_evidence_final(candidates[-1], crossref_record):
                stored = {
                    "version": EVIDENCE_VERSION,
                    "responsible_fp": responsible_fp,
                    "candidate": candidates[-1],
                }
                for key in _evidence_keys(effective_doi, raw.arxiv_id):
                    cache.set("evidence", key, stored)

    cache.close()

    # 게재일이 오늘 이후(권호 발행 예정 등)인 논문은 원래 조사 대상이 아니지만
    # 빼지 않고 표시만 한다. 실행 날짜에 따라 달라지므로 확정 판정과 별도로 매번 계산.
    # 같은 커뮤니티 안에서 같은 DOI가 두 번 나오면(원본 중복 행 등) 하나만 남긴다.
    seen, deduped = set(), []
    for c in candidates:
        key = (c["community"], (c.get("doi") or "").lower())
        if c.get("doi") and key in seen:
            print(f"[fetch] WARN {c['community']}: DOI 중복 행 제외 - {c['doi']}", file=sys.stderr)
            continue
        seen.add(key)
        deduped.append(c)
    candidates = deduped

    # 출처 표기용: 출판 논문이 OpenAlex에 있는지(KISTI 검색과 별개로 존재 여부만) 확인
    openalex_existing = None
    if year is not None and not limit_per_community:
        try:
            openalex_existing = openalex_existing_dois(
                settings, [c.get("doi") for c in candidates if c.get("publication_status") == "published"]
            )
        except Exception as e:  # noqa: BLE001
            print(f"[fetch] WARN OpenAlex 존재 확인 실패({e}) - 출처의 OpenAlex 칸은 KISTI 검색 기준", file=sys.stderr)

    today = date.today()
    cfg_by_name = {c["name"]: c for c in communities}
    # SCIE 여부·JIF·상위%는 사람이 갱신하는 JCR 표에서 매번 다시 채운다(확정 판정과 별개).
    jcr_table = load_jcr_table(year) if year is not None else {}
    pairs = _issn_pairs(settings)
    for c in candidates:
        if c.get("publication_status") == "published":
            c["scie"], c["jif"], c["jif_top_pct"] = scie_fields(
                jcr_table, c.get("issn"), c.get("eissn"), pairs
            )
        d = parse_date(c.get("date"))
        c["pub_date_future"] = bool(d and d > today)
        c["source_label"] = _source_label(
            c, cfg_by_name.get(c["community"], {}), openalex_dois, openalex_existing
        )
        # 참고용 커뮤니티(유발논문 판정 대상 아님)는 최종 보고서에서 따로 낸다.
        c["reference_only"] = bool(cfg_by_name.get(c["community"], {}).get("reference_only"))

    audit_path = settings["output"].get("audit_path", "data/download_audit.json")
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(audits, f, ensure_ascii=False, indent=2)
    print(f"[audit] 다운로드 기록 -> {audit_path}", file=sys.stderr)
    return candidates, settings


def save_candidates(candidates, settings):
    path = settings["output"]["candidates_path"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    print(f"[fetch] wrote {len(candidates)} candidates to {path}", file=sys.stderr)
    return path
