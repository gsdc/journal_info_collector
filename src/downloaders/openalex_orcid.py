import os
from datetime import date

import requests

from ..models import RawPublication
from .base import BaseDownloader

# 논문으로 볼 OpenAlex work type(프리프린트·학회 초록·정오표 등은 제외)
_PAPER_TYPES = {"article", "review", "letter"}


def _bare_orcid(value):
    return (value or "").replace("https://orcid.org/", "").strip()


class OpenAlexOrcidDownloader(BaseDownloader):
    """사용자 논문을 모아 둔 공식 목록이 없는 장비·시설 커뮤니티(TEM, PAL 등)용:
    communities.yaml의 professors(주요 사용자 교수 이름·ORCID)로 OpenAlex에서
    그 교수들의 저널 논문을 모아 목록으로 쓴다.

    교수 논문 전체가 시설을 쓴 논문은 아니므로, 목록은 '후보'이고 유발 여부는
    파이프라인의 SCIE·Ack(사사 원문)·Funder 판정이 가린다.

    게재일은 OpenAlex 값을 투고일 자리에만 두고(pub_date_raw 비움) CrossRef로
    다시 확인한다 — OpenAlex 연도와 실제 온라인 게재일이 다른 경우가 있어서다.
    """

    PER_PAGE = 200
    MAX_PAGES = 5

    def fetch(self):
        cfg = self.community_config
        professors = [p for p in cfg.get("professors") or [] if _bare_orcid(p.get("orcid"))]
        if not professors:
            raise ValueError("communities.yaml에 professors(교수 ORCID)가 없음 - 입력 후 다시 실행")
        names = {_bare_orcid(p["orcid"]): p.get("name") or _bare_orcid(p["orcid"]) for p in professors}

        # 요청 연도 앞뒤 1년까지 받아 두고 정확한 연도는 CrossRef 게재일로 거른다.
        year = cfg.get("_year")
        if year:
            year_filter = f"publication_year:{year - 1}-{year + 1}"
        else:
            year_filter = f"publication_year:>{cfg.get('since_year', date.today().year - 1) - 1}"

        api_key = os.environ.get(cfg.get("api_key_env", "OPENALEX_API_KEY"))
        params = {
            "filter": f"authorships.author.orcid:{'|'.join(names)},{year_filter}",
            "per_page": self.PER_PAGE,
            "select": "id,doi,title,type,publication_date,primary_location,ids,authorships",
        }
        if api_key:
            params["api_key"] = api_key

        works = []
        for page in range(1, self.MAX_PAGES + 1):
            resp = requests.get(self.url, params={**params, "page": page}, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            works += data["results"]
            if page * self.PER_PAGE >= data["meta"]["count"]:
                break
        else:
            self.audit["truncated"] = True

        results, seen = [], set()
        for w in works:
            self._row()
            title = w.get("title") or ""
            source_type = ((w.get("primary_location") or {}).get("source") or {}).get("type")
            if w.get("type") not in _PAPER_TYPES or source_type != "journal":
                self._drop(f"저널 논문 아님({w.get('type')}/{source_type})", title, expected=True)
                continue
            doi = (w.get("doi") or "").replace("https://doi.org/", "")
            if not doi:
                self._drop("DOI 없음", title)
                continue
            if doi.lower() in seen:
                # OpenAlex에 같은 DOI 레코드가 둘 있는 경우
                self._drop("DOI 중복 레코드", title, expected=True)
                continue
            seen.add(doi.lower())

            authors = {_bare_orcid((a.get("author") or {}).get("orcid")) for a in w.get("authorships") or []}
            matched = [names[o] for o in names if o in authors]
            pmcid = ((w.get("ids") or {}).get("pmcid") or "").rstrip("/").rsplit("/", 1)[-1]
            if pmcid and not pmcid.upper().startswith("PMC"):
                pmcid = f"PMC{pmcid}"
            results.append(
                RawPublication(
                    community=self.community_name,
                    title=title,
                    doi=doi,
                    submitted_date_raw=w.get("publication_date"),
                    status="published",
                    source_url=w["id"],
                    pmcid=pmcid or None,
                    source_note=f"대상 교수: {', '.join(matched)}" if matched else "",
                )
            )
        return results
