"""커뮤니티 페이지·INSPIRE-HEP·OpenAlex 대조 보고서.

세 출처는 서로 조금씩 다르다:
- 커뮤니티 페이지: 공식 목록이지만 갱신이 늦거나 출판 후에도 상태가 안 바뀐다.
- INSPIRE: 컬래버레이션 표기가 정확하지만 출판사 레코드와 arXiv 레코드를 늦게
  합치고, 개인 명의 발표집도 같은 컬래버레이션으로 잡힌다.
- OpenAlex: 넓게 모으지만 컬래버레이션 정보가 없어 KISTI funder·소속·담당자
  ORCID로만 찾을 수 있다.

그래서 fetch 결과(candidates.json, 커뮤니티 페이지 기준)를 나머지 두 출처와
대조해 "어느 한쪽에만 있는 논문"을 사람이 볼 수 있게 목록으로 만든다.
"""
import json
import os
import re
import sys
import time

import requests

from . import config
from .inspire_client import normalize_title

# 컬래버레이션 명의 논문은 저자가 수백~수천 명이다. 이보다 저자가 적으면
# 컬래버레이션 표기가 붙어 있어도 개인/소수 명의 논문(검출기 R&D, "on behalf of")으로 본다.
_COLLAB_MIN_AUTHORS = 100

_ERRATUM_WORDS = ("erratum", "corrigendum", "publisher's note", "publisher correction")


def _our_keys(candidates):
    dois, arxivs, titles = set(), set(), set()
    for c in candidates:
        for d in (c.get("doi"), c.get("source_doi")):
            if d:
                dois.add(d.lower())
        if c.get("arxiv_id"):
            arxivs.add(c["arxiv_id"])
        for t in (c.get("raw_title"), c.get("inspire_title")):
            key = normalize_title(t)
            if key:
                titles.add(key)
    return dois, arxivs, titles


def _is_ours(keys, dois=(), arxiv=None, title=None):
    our_dois, our_arxivs, our_titles = keys
    if any(d.lower() in our_dois for d in dois if d):
        return True
    if arxiv and arxiv in our_arxivs:
        return True
    return bool(title) and normalize_title(title) in our_titles


def collab_names(community_cfg):
    """커뮤니티의 INSPIRE 컬래버레이션 표기 목록. 한 실험이 여러 표기로 등록돼
    있거나(JSNS2/JSNS^2) 여러 실험을 묶은 커뮤니티(HK/SK)는 목록으로 적는다."""
    names = community_cfg.get("inspire_collaborations") or []
    if community_cfg.get("inspire_collaboration"):
        names = [community_cfg["inspire_collaboration"], *names]
    return names


def collab_query(community_cfg):
    """커뮤니티 논문을 찾는 INSPIRE 검색식. 컬래버레이션 표기가 없는 소규모 실험
    (예: RENE)은 inspire_query로 직접 적는다. 없으면 None."""
    if community_cfg.get("inspire_query"):
        return community_cfg["inspire_query"]
    names = collab_names(community_cfg)
    return " or ".join(f'collaboration:"{n}"' for n in names) or None


def min_authors(community_cfg):
    """컬래버레이션 명의 논문으로 볼 최소 저자 수. 소규모 실험은 낮춰 적는다."""
    return community_cfg.get("collab_min_authors", _COLLAB_MIN_AUTHORS)


def _collab_matches(inspire_collab, collaborations):
    """communities.yaml의 컬래버레이션 표기와 INSPIRE 레코드의 컬래버레이션
    표기 비교. 표기가 흔들린다(Belle-II/Belle II, LIGO/LIGO Scientific)."""
    want = inspire_collab.lower().replace("-", " ")
    return any(want in (c or "").lower().replace("-", " ") for c in collaborations)


def _inspire_search(base_url, query, fields, size=250):
    hits, page = [], 1
    while True:
        resp = requests.get(
            f"{base_url}/literature",
            params={"q": query, "size": size, "page": page, "fields": fields},
            timeout=120,
        )
        resp.raise_for_status()
        batch = resp.json()["hits"]["hits"]
        hits.extend(h.get("metadata", {}) for h in batch)
        if len(batch) < size:
            return hits
        page += 1
        time.sleep(1)


def _record_year(metadata):
    imprints = metadata.get("imprints") or []
    if imprints and imprints[0].get("date"):
        return imprints[0]["date"][:4]
    for p in metadata.get("publication_info") or []:
        if p.get("year"):
            return str(p["year"])
    return None


def _brief(metadata):
    titles = metadata.get("titles") or [{}]
    dois = metadata.get("dois") or []
    eprints = metadata.get("arxiv_eprints") or []
    pub = (metadata.get("publication_info") or [{}])[0]
    return {
        "doi": dois[0]["value"] if dois else None,
        "arxiv": eprints[0].get("value") if eprints else None,
        "title": titles[0].get("title", ""),
        "journal": pub.get("journal_title"),
        "date": ((metadata.get("imprints") or [{}])[0].get("date")) or _record_year(metadata),
        "document_type": ",".join(metadata.get("document_type") or []),
        "collaborations": [c.get("value") for c in metadata.get("collaborations") or []],
        "author_count": metadata.get("author_count"),
    }


_FIELDS = "titles,dois,arxiv_eprints,imprints,publication_info,document_type,collaborations,author_count"


def inspire_published_records(settings, base_query, year):
    """검색식(collab_query)에 해당하는 해당 연도(저널 연도 기준, 이듬해 권호 포함) 출판 레코드."""
    base_url = settings["inspire"]["base_url"].rstrip("/")
    query = f"({base_query}) and tc:p and (jy:{year} or jy:{year + 1})"
    return _inspire_search(base_url, query, _FIELDS)


def inspire_records_by_doi(settings, dois):
    """DOI 목록을 INSPIRE에서 40개씩 묶어 조회한다. 반환: {doi(소문자): metadata}"""
    base_url = settings["inspire"]["base_url"].rstrip("/")
    found = {}
    dois = list(dois)
    for i in range(0, len(dois), 40):
        query = " or ".join(f'doi:"{d}"' for d in dois[i:i + 40])
        for md in _inspire_search(base_url, query, _FIELDS, size=100):
            for d in md.get("dois") or []:
                found[d["value"].lower()] = md
        time.sleep(1)
    return found


def is_erratum(metadata):
    titles = metadata.get("titles") or [{}]
    return any(w in (titles[0].get("title") or "").lower() for w in _ERRATUM_WORDS)


def is_collab_paper(metadata, year, min_author_count=_COLLAB_MIN_AUTHORS):
    """해당 연도에 출판된 컬래버레이션 명의 논문인지(발표집·개인 명의 논문·정오표 제외)."""
    return (
        _record_year(metadata) == str(year)
        and "article" in (metadata.get("document_type") or [])
        and (metadata.get("author_count") or 0) >= min_author_count
        and not is_erratum(metadata)
    )


def matching_community(communities, metadata):
    collabs = [c.get("value") for c in metadata.get("collaborations") or []]
    return next(
        (c["name"] for c in communities
         if any(_collab_matches(n, collabs) for n in collab_names(c))),
        None,
    )


def inspire_not_in_ours(settings, communities, keys, year):
    """커뮤니티별로 INSPIRE에 그 해 출판 논문으로 있는데 우리 결과에 없는 것."""
    result = {}
    for c in communities:
        query = collab_query(c)
        if not query:
            continue
        missing, small_author, other_types, errata = [], [], [], []
        for md in inspire_published_records(settings, query, year):
            if _record_year(md) != str(year):
                continue
            dois = [d["value"] for d in md.get("dois") or []]
            arxiv = ((md.get("arxiv_eprints") or [{}])[0]).get("value")
            titles = [t.get("title") for t in md.get("titles") or []]
            if _is_ours(keys, dois, arxiv) or any(_is_ours(keys, title=t) for t in titles):
                continue
            item = _brief(md)
            if is_erratum(md):
                errata.append(item)
            elif "article" not in (md.get("document_type") or []):
                other_types.append(item)
            elif (item["author_count"] or 0) < min_authors(c):
                small_author.append(item)
            else:
                missing.append(item)
        result[c["name"]] = {
            "missing": missing,
            "small_author": small_author,
            "other_types": other_types,
            "errata": errata,
        }
        time.sleep(1)
    return result


def _openalex_get(base_url, params, api_key):
    if api_key:
        params = {**params, "api_key": api_key}
    resp = requests.get(f"{base_url}/works", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def openalex_existing_dois(settings, dois):
    """DOI 목록 중 OpenAlex에 있는 것(소문자). 50개씩 묶어 조회한다(요청 수 최소화)."""
    cfg = settings.get("openalex", {})
    base_url = cfg.get("base_url", "https://api.openalex.org").rstrip("/")
    api_key = os.environ.get(cfg.get("api_key_env", "OPENALEX_API_KEY"))
    dois = sorted({d.lower() for d in dois if d})
    found = set()
    for i in range(0, len(dois), 50):
        chunk = dois[i:i + 50]
        data = _openalex_get(
            base_url,
            {"filter": "doi:" + "|".join(chunk), "per_page": 50, "select": "doi"},
            api_key,
        )
        found |= {(w.get("doi") or "").replace("https://doi.org/", "").lower() for w in data["results"]}
    return found


def openalex_kisti_works(settings, orcids, year):
    """OpenAlex에서 KISTI funder·KISTI 소속·담당자 ORCID 중 하나라도 걸리는
    해당 연도 논문. 반환: {doi(소문자): {"work": ..., "via": set}}"""
    cfg = settings.get("openalex", {})
    base_url = cfg.get("base_url", "https://api.openalex.org").rstrip("/")
    api_key = os.environ.get(cfg.get("api_key_env", "OPENALEX_API_KEY"))
    max_pages = cfg.get("max_pages", 5)
    filters = {
        "funder": f"funders.id:{cfg['kisti_funder_id']}",
        "institution": f"authorships.institutions.id:{cfg['kisti_institution_id']}",
    }
    if orcids:
        filters["orcid"] = "authorships.author.orcid:" + "|".join(orcids)

    works = {}
    for via, flt in filters.items():
        for page in range(1, max_pages + 1):
            data = _openalex_get(
                base_url,
                {
                    "filter": f"{flt},publication_year:{year}",
                    "per_page": 200,
                    "page": page,
                    "select": "id,doi,title,publication_date,type",
                },
                api_key,
            )
            for w in data["results"]:
                doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
                if doi:
                    works.setdefault(doi, {"work": w, "via": set()})["via"].add(via)
            if page * 200 >= data["meta"]["count"]:
                break
        else:
            print(f"[reconcile] WARN OpenAlex {via}: {max_pages}페이지 상한에 걸림", file=sys.stderr)
    return works


class OpenAlexTitleLookup:
    """INSPIRE에서 출판이 확인되지 않은 논문을 OpenAlex에서 제목으로 찾아 출판본
    DOI를 얻는다.

    OpenAlex는 arXiv 프리프린트(10.48550/arXiv.*)와 저널 출판본을 별개 레코드로
    두는 경우가 대부분이라(2026-10-23 확인: 미출판 50건 중 합쳐진 것 0건) arXiv
    DOI 조회로는 출판본을 못 찾는다. 그래서 논문마다 제목 검색이 필요하고(요청 1건/편),
    무료 공유 한도로는 감당이 안 돼 API 키가 있을 때만 쓴다.
    정규화한 제목이 완전히 같은 저널 논문만 인정한다(비슷한 제목 오매칭 방지).
    """

    def __init__(self, settings):
        cfg = settings.get("openalex", {})
        self.base_url = cfg.get("base_url", "https://api.openalex.org").rstrip("/")
        self.api_key = os.environ.get(cfg.get("api_key_env", "OPENALEX_API_KEY"))
        self.enabled = bool(self.api_key)

    def find_published_doi(self, title, since_year):
        if not self.enabled or not title:
            return None
        key = normalize_title(title)
        # LaTeX 명령·기호를 지운 단어만 검색어로 쓴다(OpenAlex 검색 문법과 충돌 방지)
        words = re.sub(r"\\[a-zA-Z]+|<[^>]+>|[^A-Za-z0-9 ]", " ", title).split()
        if not words:
            return None
        data = _openalex_get(
            self.base_url,
            {
                "filter": f"title.search:{' '.join(words)},type:article,publication_year:>{since_year - 1}",
                "per_page": 10,
                "select": "doi,title,primary_location",
            },
            self.api_key,
        )
        matches = set()
        for w in data["results"]:
            doi = (w.get("doi") or "").replace("https://doi.org/", "")
            source_type = ((w.get("primary_location") or {}).get("source") or {}).get("type")
            if (
                doi
                and not doi.lower().startswith("10.48550/")
                and source_type == "journal"
                and normalize_title(w.get("title")) == key
            ):
                matches.add(doi.lower())
        # 같은 제목의 저널 논문이 둘 이상이면 어느 쪽인지 모르므로 쓰지 않는다.
        return matches.pop() if len(matches) == 1 else None


def classify_openalex(settings, communities, keys, works, year):
    """우리 결과에 없는 OpenAlex 논문을 INSPIRE로 컬래버레이션을 확인해 분류한다."""
    rest = [d for d in works if not _is_ours(keys, [d], title=works[d]["work"].get("title"))]
    inspire = inspire_records_by_doi(settings, rest)

    out = {"missing": [], "other_collab": [], "no_collab": [], "other_year": [], "not_in_inspire": 0}
    for d in rest:
        md = inspire.get(d)
        if md is None:
            out["not_in_inspire"] += 1
            continue
        item = _brief(md)
        item["doi"] = d
        item["via"] = sorted(works[d]["via"])
        item["openalex_date"] = works[d]["work"].get("publication_date")
        if _record_year(md) and _record_year(md) != str(year):
            out["other_year"].append(item)  # OpenAlex 연도 오류(정오표 등)
            continue
        collabs = item["collaborations"]
        community = matching_community(communities, md)
        if community:
            item["community"] = community
            out["missing"].append(item)
        elif collabs:
            out["other_collab"].append(item)
        else:
            out["no_collab"].append(item)
    return out


def _md_items(items, extra=()):
    lines = []
    for it in items:
        bits = [f"`{it['doi']}`" if it.get("doi") else "(DOI 없음)"]
        if it.get("arxiv"):
            bits.append(f"arXiv:{it['arxiv']}")
        bits.append(str(it.get("date") or ""))
        if it.get("journal"):
            bits.append(it["journal"])
        for key in extra:
            if it.get(key):
                val = it[key]
                bits.append(", ".join(val) if isinstance(val, list) else str(val))
        title = (it.get("title") or "").replace("\n", " ")[:110]
        lines.append(f"- {' · '.join(bits)} — {title}")
    return lines or ["- 없음"]


def write_report(path, year, unresolved, supplemented, inspire_result, openalex_result):
    lines = [f"# {year}년 논문 대조 보고서", ""]
    lines += [
        "커뮤니티 페이지(fetch 결과)를 기준으로 INSPIRE-HEP·OpenAlex와 대조한 결과.",
        "**누락 의심** 항목은 커뮤니티 페이지 갱신 지연, 다운로더 문제, 게재일 판정 차이 중",
        "하나일 수 있으니 사람이 확인해야 한다.",
        "",
        "## 1. 커뮤니티 페이지에 있지만 출판 확인이 안 된 논문",
        "",
    ]
    for name, items in unresolved.items():
        lines.append(f"### {name} ({len(items)}건)")
        lines += [
            f"- {it['raw_status']} · arXiv:{it.get('arxiv_id') or '-'} — {(it.get('raw_title') or '')[:110]}"
            for it in items
        ] or ["- 없음"]
        lines.append("")

    lines += ["## 2. 커뮤니티 페이지에 없어 INSPIRE·OpenAlex로 보충한 논문", ""]
    lines += [
        f"- {it['community']} · `{it.get('doi')}` · {it.get('date')} · {it.get('source_label')} — {(it.get('inspire_title') or it.get('raw_title') or '')[:100]}"
        for it in supplemented
    ] or ["- 없음"]
    lines.append("")

    lines += ["## 3. INSPIRE에는 그 해 출판 논문인데 우리 결과에 없는 논문", ""]
    for name, r in inspire_result.items():
        lines.append(f"### {name}: 누락 의심 {len(r['missing'])}건")
        lines += _md_items(r["missing"], ("author_count",))
        if r["small_author"]:
            lines.append(
                "- 참고: 저자 수가 커뮤니티 기준 미만(개인/소수 명의 검출기 논문 등) "
                f"{len(r['small_author'])}건 — 아래"
            )
            lines += ["  " + l for l in _md_items(r["small_author"], ("author_count",))]
        if r["other_types"]:
            lines.append(f"- 참고: 컬래버레이션 논문이 아닌 것(발표집 등) {len(r['other_types'])}건 — 아래")
            lines += ["  " + l for l in _md_items(r["other_types"], ("document_type",))]
        if r["errata"]:
            lines.append(f"- 참고: 정오표 {len(r['errata'])}건 (대상 아님)")
        lines.append("")

    lines += ["## 4. OpenAlex의 KISTI 관련 논문 중 우리 결과에 없는 논문", ""]
    if openalex_result is None:
        lines += ["- OpenAlex 조회 실패 — 로그 참고", ""]
    else:
        lines.append(f"### 등록 커뮤니티 소속 — 누락 의심 {len(openalex_result['missing'])}건")
        lines += _md_items(openalex_result["missing"], ("community", "via"))
        lines.append("")
        lines.append(f"### 등록되지 않은 컬래버레이션 {len(openalex_result['other_collab'])}건 (참고)")
        lines += _md_items(openalex_result["other_collab"], ("collaborations", "via"))
        lines.append("")
        lines.append(f"### 컬래버레이션 표기 없는 INSPIRE 논문 {len(openalex_result['no_collab'])}건 (참고: GSDC 자체 논문일 수 있음)")
        lines += _md_items(openalex_result["no_collab"], ("via",))
        lines.append("")
        if openalex_result["other_year"]:
            lines.append(f"### INSPIRE 기준 다른 연도 {len(openalex_result['other_year'])}건 (OpenAlex 날짜 오류, 참고)")
            lines += _md_items(openalex_result["other_year"], ("via",))
            lines.append("")
        lines.append(f"- INSPIRE에 없는 KISTI 논문(타 분야로 추정) {openalex_result['not_in_inspire']}건은 생략")
        lines.append("")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _load_saved_openalex(settings, year):
    """fetch가 저장한 OpenAlex 결과가 같은 연도면 재사용한다(무료 요청 한도 절약)."""
    path = settings["output"].get("openalex_path", "data/openalex_kisti.json")
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return None
    if saved.get("year") != year:
        return None
    return {d: {"work": w, "via": set(w.get("via", []))} for d, w in saved["works"].items()}


def run_reconcile(year, candidates=None, settings=None):
    settings = settings or config.load_settings()
    communities = config.load_communities()
    if candidates is None:
        with open(settings["output"]["candidates_path"], encoding="utf-8") as f:
            candidates = json.load(f)
    keys = _our_keys(candidates)

    unresolved = {}
    for c in candidates:
        if c.get("publication_status") != "published":
            unresolved.setdefault(c["community"], []).append(c)

    inspire_result = inspire_not_in_ours(settings, communities, keys, year)

    orcids = [
        p["orcid"]
        for people in config.load_responsible_authors().values()
        for p in people
        if p.get("orcid")
    ]
    try:
        works = _load_saved_openalex(settings, year) or openalex_kisti_works(settings, orcids, year)
        openalex_result = classify_openalex(settings, communities, keys, works, year)
    except requests.RequestException as e:
        print(f"[reconcile] WARN OpenAlex 조회 실패: {e}", file=sys.stderr)
        openalex_result = None

    path = settings["output"].get("reconcile_path", "output/reconcile_report.md")
    supplemented = [c for c in candidates if c.get("origin", "community") != "community"]
    write_report(path, year, unresolved, supplemented, inspire_result, openalex_result)

    n_inspire = sum(len(r["missing"]) for r in inspire_result.values())
    n_openalex = len(openalex_result["missing"]) if openalex_result else "?"
    print(
        f"[reconcile] 출판 미확인 {sum(map(len, unresolved.values()))}건, "
        f"INSPIRE 누락 의심 {n_inspire}건, OpenAlex 누락 의심 {n_openalex}건 -> {path}",
        file=sys.stderr,
    )
    return path
