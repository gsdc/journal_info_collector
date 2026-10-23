import os

import requests

from ..models import RawPublication
from .base import BaseDownloader


class OpenAlexSearchDownloader(BaseDownloader):
    """공식 논문 목록도 INSPIRE 컬래버레이션 표기도 없는 커뮤니티용: OpenAlex
    검색 결과를 논문 목록으로 쓴다. (예: ENIGMA — East-Asian Network Initiative
    for Gravity Measurement Alliance. 고에너지물리가 아니라 INSPIRE에 없다.)

    communities.yaml의 openalex_queries 각 항목을 OR로 합친다:
      - search: OpenAlex 전문 검색어(제목·초록)
      - authors: OpenAlex 저자 ID 목록(있으면 이 저자들의 논문으로 한정)
    "ENIGMA"는 같은 이름의 다른 컨소시엄(뇌영상 유전학, BRCA 변이 해석)이 있어
    이름만으로 검색하지 말고 정식 명칭이나 핵심 저자 + 주제어로 한정해야 한다.

    저널 논문만 남긴다(학회 초록·프리프린트 등은 의도된 제외로 기록).
    """

    PER_PAGE = 200
    MAX_PAGES = 5

    def fetch(self):
        cfg = self.community_config
        since = cfg.get("since_year", 2000)
        api_key = os.environ.get(cfg.get("api_key_env", "OPENALEX_API_KEY"))
        works = {}
        for q in cfg.get("openalex_queries", []):
            filters = [f"publication_year:>{since - 1}"]
            if q.get("authors"):
                filters.append("authorships.author.id:" + "|".join(q["authors"]))
            params = {
                "filter": ",".join(filters),
                "search": q["search"],
                "per_page": self.PER_PAGE,
                "select": "id,doi,title,type,publication_date,primary_location,ids",
            }
            if api_key:
                params["api_key"] = api_key
            for page in range(1, self.MAX_PAGES + 1):
                resp = requests.get(self.url, params={**params, "page": page}, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                for w in data["results"]:
                    works.setdefault(w["id"], w)
                if page * self.PER_PAGE >= data["meta"]["count"]:
                    break
            else:
                self.audit["truncated"] = True

        results = []
        for w in works.values():
            self._row()
            title = w.get("title") or ""
            source_type = ((w.get("primary_location") or {}).get("source") or {}).get("type")
            if w.get("type") != "article" or source_type != "journal":
                self._drop(f"저널 논문 아님({w.get('type')})", title, expected=True)
                continue
            doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
            if not doi:
                self._drop("DOI 없음", title)
                continue
            results.append(
                RawPublication(
                    community=self.community_name,
                    title=title,
                    doi=doi,
                    pub_date_raw=w.get("publication_date"),
                    status="published",
                    source_url=w["id"],
                )
            )
        return results
