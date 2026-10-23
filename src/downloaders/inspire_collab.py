import requests

from ..models import RawPublication
from ..reconcile import collab_query, min_authors
from .base import BaseDownloader


class InspireCollabDownloader(BaseDownloader):
    """공식 논문 목록 페이지를 직접 읽을 수 없는 커뮤니티용: INSPIRE-HEP에서
    컬래버레이션 이름으로 검색한 결과를 논문 목록으로 쓴다.
    (예: DUNE 공식 페이지는 목록을 JavaScript로 그려서 HTML에 내용이 없다.)

    url은 INSPIRE literature API 주소, 검색식은 inspire_collaboration(들) 또는
    inspire_query 설정으로 만든다(reconcile.collab_query). 다른 커뮤니티 페이지가 컬래버레이션 명의
    논문만 싣는 것과 맞추기 위해 document_type이 article인 레코드만 남긴다
    (개인 명의 학회 발표집·학위논문 제외).
    """

    PAGE_SIZE = 250
    FIELDS = "titles,dois,arxiv_eprints,imprints,preprint_date,document_type,author_count"

    def fetch(self):
        query = collab_query(self.community_config)
        results = []
        page = 1
        while True:
            resp = requests.get(
                self.url,
                params={"q": query, "size": self.PAGE_SIZE, "page": page, "fields": self.FIELDS},
                timeout=60,
            )
            resp.raise_for_status()
            hits = resp.json()["hits"]["hits"]
            for hit in hits:
                self._row()
                metadata = hit.get("metadata", {})
                raw = self._to_raw(metadata)
                doc_types = metadata.get("document_type") or []
                if "article" not in doc_types:
                    self._drop(f"article 아님({','.join(doc_types)})", raw.title, expected=True)
                    continue
                # DOI도 arXiv ID도 없는 레코드(기관 내부 보고서 등)는 출판 여부를
                # 확인할 단서가 없어 제외한다.
                if not (raw.doi or raw.arxiv_id):
                    self._drop("DOI·arXiv 모두 없음(내부 보고서 등)", raw.title, expected=True)
                    continue
                # 저자 수가 적으면 컬래버레이션 표기가 붙은 개인 명의 논문("on behalf of")
                if (metadata.get("author_count") or 0) < min_authors(self.community_config):
                    self._drop("저자 수가 커뮤니티 기준 미만(개인 명의 논문)", raw.title, expected=True)
                    continue
                results.append(raw)
            if len(hits) < self.PAGE_SIZE:
                return results
            page += 1

    def _to_raw(self, metadata):
        titles = metadata.get("titles") or []
        dois = metadata.get("dois") or []
        eprints = metadata.get("arxiv_eprints") or []
        imprints = metadata.get("imprints") or []
        doi = dois[0]["value"] if dois else None
        return RawPublication(
            community=self.community_name,
            title=titles[0].get("title", "") if titles else "",
            arxiv_id=eprints[0].get("value") if eprints else None,
            doi=doi,
            # 게재일은 출판된 경우의 imprint만 쓴다(preprint_date는 투고일 취급).
            pub_date_raw=(imprints[0].get("date") if imprints and doi else None),
            submitted_date_raw=metadata.get("preprint_date"),
            status="published" if doi else "submitted",
            source_url=self.url,
        )
