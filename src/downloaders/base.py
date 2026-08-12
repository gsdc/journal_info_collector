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

    @abstractmethod
    def fetch(self):
        """List[RawPublication] 반환."""
        raise NotImplementedError
