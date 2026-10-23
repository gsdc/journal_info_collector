from abc import ABC, abstractmethod


class BaseDownloader(ABC):
    """커뮤니티별 다운로더의 공통 인터페이스.

    새 커뮤니티를 추가하려면 이 클래스를 상속해 fetch()만 구현하고,
    registry.py의 DOWNLOADERS 딕셔너리에 등록하면 된다.
    """

    def __init__(self, community_config):
        self.community_config = community_config
        self.community_name = community_config["name"]
        self.url = community_config["url"]
        # 페이지에 있는 논문을 조용히 놓치지 않았는지 확인하기 위한 기록.
        # rows: 페이지에서 읽은 행 수, dropped: 버린 행과 이유,
        # truncated: 페이지 수 상한에 걸려 끝까지 못 읽었는지.
        self.audit = {"rows": 0, "dropped": [], "truncated": False}
        # 매번 경고할 필요가 없는 알려진 예외(최근 연도와 무관한 옛 문서 등).
        # communities.yaml의 audit_ignore_titles에 제목을 적는다(대소문자·공백 무시).
        self._ignore_titles = {
            " ".join(t.lower().split()) for t in community_config.get("audit_ignore_titles", [])
        }

    def _row(self):
        self.audit["rows"] += 1

    def _drop(self, reason, title="", expected=False):
        """행을 결과에서 뺄 때 반드시 호출한다. expected=True는 의도된 제외
        (철회된 논문, 중복, 학회 발표집 등), False는 파싱 실패처럼 사람이 봐야 하는 경우."""
        if not expected and " ".join((title or "").lower().split()) in self._ignore_titles:
            reason, expected = f"{reason} [예외 등록]", True
        self.audit["dropped"].append(
            {"reason": reason, "title": (title or "")[:150], "expected": expected}
        )

    @abstractmethod
    def fetch(self):
        """List[RawPublication] 반환."""
        raise NotImplementedError
