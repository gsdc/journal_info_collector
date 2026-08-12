import re
import sys
from datetime import date, timedelta

from . import config
from .ack_check import check_ack_via_funder
from .arxiv_fetcher import ArxivFetcher
from .crossref_client import CrossrefClient
from .date_filter import in_range
from .downloaders.registry import get_downloader

# "We gratefully acknowledge the computing centres/centers" 류의 일반적인
# WLCG 감사 문구는 있는데, 우리 기관(KISTI/GSDC)은 이름으로도 funder DOI로도
# 전혀 언급되지 않는 논문을 찾기 위한 패턴.
COMPUTING_CENTRES_RE = re.compile(
    r"gratefully\s+acknowledge[sd]?\s+the\s+computing\s+cent(?:er|re)s?",
    re.IGNORECASE,
)
KISTI_MENTION_RE = re.compile(
    r"kisti|korea institute of science and technology information|gsdc|global science",
    re.IGNORECASE,
)


def scan_computing_ack_gap(
    community_names=None, year=None, since_days=None, limit_per_community=None
):
    """"computing centres를 감사한다"는 문구는 있는데 KISTI/GSDC는 (이름으로도,
    CrossRef funder DOI/이름으로도) 전혀 안 나오는 논문을 찾는다.

    year가 주어지면 해당 연도(pub_date_raw 기준)만, 아니면 since_days(기본 2년)
    이내만 본다. 둘 다 None이면 전체 이력을 본다.

    Ack 판정은 check_ack_via_funder()(funder DOI 또는 - 출판사가 KISTI를 다른
    funder DOI에 잘못 태깅해둔 경우를 대비해 - funder 이름 텍스트까지 확인)를
    먼저 쓰고, "Y"가 아닐 때만 arXiv 원문을 추가로 확인한다.
    """
    settings = config.load_settings()
    communities = config.load_communities()
    if community_names:
        communities = [c for c in communities if c["name"] in community_names]

    since = None
    if year is None and since_days:
        since = date.today() - timedelta(days=since_days)

    kisti_funder_doi = settings["crossref"]["kisti_funder_doi"]

    crossref_client = CrossrefClient(settings)
    arxiv_fetcher = ArxivFetcher(settings)

    flagged = []
    checked = 0
    skipped_no_text = []

    for community_cfg in communities:
        name = community_cfg["name"]
        print(f"[scan] {name}: downloading list", file=sys.stderr)
        downloader = get_downloader(community_cfg)
        raw_pubs = downloader.fetch()

        # 사사(Ack) 분석은 실제로 출판된 논문만 대상으로 한다 — 다운로더가 이제
        # 투고/게재확정 단계(DOI 없음, 날짜도 없음)까지 포함시키므로 여기서 걸러낸다.
        published = [raw for raw in raw_pubs if raw.doi]
        filtered = [
            raw for raw in published if in_range(raw.pub_date_raw, year=year, since=since)
        ]

        print(
            f"[scan] {name}: {len(filtered)}/{len(published)} published in scope "
            f"({len(raw_pubs)} total incl. unpublished)",
            file=sys.stderr,
        )

        if limit_per_community:
            filtered = filtered[:limit_per_community]

        for i, raw in enumerate(filtered, 1):
            checked += 1
            print(f"[scan] {name} {i}/{len(filtered)}: {raw.doi}", file=sys.stderr)

            crossref_record = None
            try:
                crossref_record = crossref_client.get_by_doi(raw.doi)
            except Exception as e:  # noqa: BLE001
                print(f"[scan]   CrossRef 조회 오류: {e}", file=sys.stderr)

            ack_funder_status, ack_funder_note = check_ack_via_funder(
                crossref_record, kisti_funder_doi
            )
            if ack_funder_status == "Y":
                continue  # funder DOI/이름으로 이미 credited 확인됨

            excerpt, excerpt_note = arxiv_fetcher.fetch_ack_excerpt(raw.arxiv_id)
            if not excerpt:
                skipped_no_text.append(
                    {
                        "community": name,
                        "doi": raw.doi,
                        "비고": f"{ack_funder_note} / arXiv 원문도 확인 불가({excerpt_note})",
                    }
                )
                continue

            has_computing_centres = bool(COMPUTING_CENTRES_RE.search(excerpt))
            has_kisti_mention = bool(KISTI_MENTION_RE.search(excerpt))

            if has_computing_centres and not has_kisti_mention:
                flagged.append(
                    {
                        "community": name,
                        "doi": raw.doi,
                        "arxiv_id": raw.arxiv_id,
                        "title": raw.title,
                        "pub_date_raw": raw.pub_date_raw,
                        "비고": f"{ack_funder_note} / arXiv 원문에도 KISTI/GSDC 언급 없음(computing centres 감사 문구는 있음)",
                    }
                )

    print(
        f"[scan] checked {checked}, flagged {len(flagged)}, "
        f"no-text {len(skipped_no_text)}",
        file=sys.stderr,
    )
    return flagged, skipped_no_text, checked
