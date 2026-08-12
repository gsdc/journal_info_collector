import re

from dateutil import parser as dateparser

_ARXIV_YYMM_RE = re.compile(r"^(\d{2})(\d{2})\.\d+")


def parse_date(raw):
    if not raw:
        return None
    try:
        return dateparser.parse(raw, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def arxiv_id_to_date(arxiv_id):
    """arXiv ID(YYMM.NNNNN)에서 연-월을 뽑아 그 달 1일로 근사한다.
    명시적인 투고/출판일이 없는 항목의 연도 필터링용 대체값으로만 쓴다."""
    if not arxiv_id:
        return None
    m = _ARXIV_YYMM_RE.match(arxiv_id)
    if not m:
        return None
    yy, mm = m.groups()
    year = 2000 + int(yy)
    return f"{year:04d}-{mm}-01"


def in_range(raw_date_str, year=None, since=None, until=None):
    """year/since/until 중 지정된 조건만 적용한다.
    날짜를 못 읽으면(형식 이상 등) 필터링하지 않고 포함시킨다(안전 기본값)."""
    d = parse_date(raw_date_str)
    if d is None:
        return True
    if year is not None and d.year != year:
        return False
    if since is not None and d < since:
        return False
    if until is not None and d > until:
        return False
    return True
