from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawPublication:
    """커뮤니티 웹페이지/API에서 그대로 긁어온 논문 1건.

    DOI가 아직 없는(투고/게재확정 단계) 논문도 포함한다 — 연도 리스트에는
    일단 넣어두고, 그 사이 실제로 출판됐는지는 파이프라인에서 INSPIRE/CrossRef로
    다시 확인해서 최종 결과물 포함 여부를 판단한다.
    """

    community: str
    title: str
    arxiv_id: Optional[str] = None
    doi: Optional[str] = None
    journal_citation_raw: Optional[str] = None
    pub_date_raw: Optional[str] = None
    # pub_date_raw가 없을 때(아직 미출판) 연도 필터링용 대체 날짜.
    # 보통 투고일(Date Sub 등) 또는 arXiv ID에서 유추한 연-월.
    submitted_date_raw: Optional[str] = None
    # "published"(DOI 있음) / "accepted"(게재확정, DOI 미부여) /
    # "submitted"(투고/심사중) / "superseded"(철회·대체됨) / "unknown"
    status: str = "published"
    source_url: Optional[str] = None


@dataclass
class InspireAuthor:
    full_name: str
    orcid: Optional[str] = None


@dataclass
class InspireRecord:
    control_number: Optional[int]
    title: Optional[str]
    doi: Optional[str]
    journal_title: Optional[str]
    journal_volume: Optional[str]
    page: Optional[str]
    issn: Optional[str]
    pub_date: Optional[str]
    authors: list = field(default_factory=list)  # list[InspireAuthor]
    collaborations: list = field(default_factory=list)


@dataclass
class Candidate:
    """csv 한 행을 만들기 위한 중간 산출물. Ack 판정 등 LLM 보조 단계에 필요한
    원문 텍스트(ack_excerpt)를 함께 들고 다닌다."""

    raw: RawPublication
    inspire: Optional[InspireRecord]
    auth_status: str  # "Y" / "N" / "확인필요" / "" (DOI 없어 매칭 실패 등)
    auth_note: str = ""
    ack_excerpt: str = ""     # arXiv 원문에서 뽑아낸 Acknowledgements 후보 텍스트
    ack_source: str = ""      # "arxiv" / "inspire" / "" (원문을 못 구한 경우)
    match_status: str = "ok"  # "ok" / "no_doi" / "inspire_not_found"
    note: str = ""
